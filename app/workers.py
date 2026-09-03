from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .config import settings
from .db import CloudDay, LiveSession, Profile, Recording, RecordingFragment, Source, db_session
from .recorder import (
    RecorderSession,
    STITCH_MARKER_NAME,
    finalize_mp4_for_streaming,
    mp4_is_streaming_ready,
    start_recorder,
    stitch_recording_parts,
    stop_recorder,
    stream_transport_fault,
)
from .settings_store import runtime
from .source_providers import probe
from .storage import disk_state
from .uploaders import UploadCancelled, create_gofile_folder, create_pixeldrain_list, provider_available, upload
from .utils import generate_thumbnail, human_bytes, safe_name, sha256_file, utcnow, verify_media

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - production containers are Linux
    _fcntl = None

RETRYABLE_MEDIA_ERRORS = ("scadut", "timeout", "timed out", "tempor")
CLOUD_TIME_ZONE = ZoneInfo("Europe/Berlin")
SESSION_STITCH_GAP_SECONDS = 20 * 60


def cloud_day_key(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CLOUD_TIME_ZONE).date().isoformat()



def stitch_gap_open(last_at: datetime, now: datetime, gap_seconds: int = SESSION_STITCH_GAP_SECONDS) -> bool:
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = (now.astimezone(timezone.utc) - last_at.astimezone(timezone.utc)).total_seconds()
    return 0 <= delta <= max(0, int(gap_seconds))


class WorkerManager:
    def __init__(self) -> None:
        self.active: dict[int, RecorderSession] = {}
        self.watch_tasks: dict[int, asyncio.Task] = {}
        self.tasks: list[asyncio.Task] = []
        self._leader_task: asyncio.Task | None = None
        self._leader_file = None
        self._stopping = False
        self._wake_event = asyncio.Event()
        self.last_errors: dict[str, str] = {}
        self.started_at: datetime | None = None
        self.upload_current: dict | None = None
        self._retry_after: dict[int, float] = {}
        self._source_check_locks: dict[int, asyncio.Lock] = {}
        self._mp4_finalize_lock = asyncio.Lock()
        self.backfill_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._stopping = False
        self.started_at = utcnow()
        self.tasks = []
        self._leader_task = asyncio.create_task(self._leader_loop(), name="worker-leader")
        await asyncio.sleep(0)

    def _try_worker_leadership(self) -> bool:
        lock_path = settings.data_dir / ".livevault-worker.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+")
        if _fcntl is not None:
            try:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                return False
        self._leader_file = handle
        return True

    def _release_worker_leadership(self) -> None:
        handle = self._leader_file
        self._leader_file = None
        if not handle:
            return
        if _fcntl is not None:
            with contextlib.suppress(OSError):
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            handle.close()

    async def _leader_loop(self) -> None:
        try:
            while not self._stopping:
                if self._leader_file is None and not self._try_worker_leadership():
                    await asyncio.sleep(1)
                    continue
                self._recover_interrupted_uploads()
                await self._recover_orphans()
                if self._stopping:
                    return
                self.tasks = [
                    asyncio.create_task(self._poll_loop(), name="source-poller"),
                    asyncio.create_task(self._upload_loop(), name="uploader"),
                    asyncio.create_task(self._cleanup_loop(), name="storage-guard"),
                ]
                self.backfill_task = asyncio.create_task(self._maintenance_backfill(), name="maintenance-backfill")
                await asyncio.gather(*self.tasks)
                return
        finally:
            # During shutdown, blocking upload threads may still be winding down.
            # Keep the flock until process exit so a replacement cannot duplicate work.
            if not self._stopping:
                self._release_worker_leadership()

    async def stop(self) -> None:
        self._stopping = True
        self._wake_event.set()
        await self.stop_all_recordings()
        uploader_task = next((task for task in self.tasks if task.get_name() == "uploader"), None)
        if uploader_task and not uploader_task.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(uploader_task), timeout=5)
        if self._leader_task and not self._leader_task.done():
            self._leader_task.cancel()
        for task in self.tasks:
            task.cancel()
        for task in list(self.watch_tasks.values()):
            task.cancel()
        if self.backfill_task and not self.backfill_task.done():
            self.backfill_task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.backfill_task:
            with contextlib.suppress(Exception):
                await asyncio.gather(self.backfill_task, return_exceptions=True)
        if self._leader_task:
            with contextlib.suppress(Exception):
                await asyncio.gather(self._leader_task, return_exceptions=True)
        self.started_at = None

    def wake(self) -> None:
        self._wake_event.set()

    def clear_retry_backoff(self) -> None:
        self._retry_after.clear()

    async def check_source_now(self, source_id: int) -> bool:
        """Probe one source immediately, even while automatic recording is paused."""
        with db_session() as db:
            source = db.get(Source, source_id)
            if not source:
                return False
            db.expunge(source)
        if self._leader_task is not None and self._leader_file is None:
            return True
        await self._check_source(source, asyncio.Semaphore(1))
        return True

    def health(self) -> dict:
        live_tasks = ([self._leader_task] if self._leader_task else []) + self.tasks
        return {
            "started": self.started_at is not None,
            "tasks": {task.get_name(): not task.done() for task in live_tasks},
            "leader": self._leader_file is not None,
            "mode": "leader" if self._leader_file is not None else "standby",
            "active_recorders": len(self.active),
            "thumbnail_backfill": "done" if self.backfill_task and self.backfill_task.done() else "running" if self.backfill_task else "idle",
        }

    def _recover_interrupted_uploads(self) -> None:
        with db_session() as db:
            rows = list(db.scalars(
                select(Recording).where(Recording.upload_status.in_(["uploading", "converting"]))
            ).all())
            for rec in rows:
                rec.upload_status = "pending" if rec.integrity_status == "passed" else "integrity_failed"
                rec.last_error = "Elaborazione interrotta da un riavvio; rimessa in coda"

    async def _sleep_or_wake(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            self._wake_event.clear()

    async def stop_source(self, source_id: int) -> None:
        lock = self._source_check_locks.setdefault(source_id, asyncio.Lock())
        async with lock:
            session = self.active.get(source_id)
            if not session:
                return
            await stop_recorder(session)
            task = self.watch_tasks.get(source_id)
            if task and task is not asyncio.current_task():
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(task), timeout=10)

    async def stop_all_recordings(self) -> None:
        source_ids = list(self.active)
        if source_ids:
            await asyncio.gather(*(self.stop_source(source_id) for source_id in source_ids), return_exceptions=True)

    def local_buffer_bytes(self) -> int:
        total = 0
        known_paths: set[str] = set()
        with db_session() as db:
            total += int(db.scalar(
                select(func.coalesce(func.sum(Recording.size_bytes), 0)).where(Recording.local_deleted.is_(False))
            ) or 0)
            total += int(db.scalar(select(func.coalesce(func.sum(RecordingFragment.size_bytes), 0))) or 0)
            known_paths.update(str(x) for x in db.scalars(
                select(Recording.local_path).where(Recording.local_deleted.is_(False))
            ).all())
            known_paths.update(str(x) for x in db.scalars(select(RecordingFragment.local_path)).all())
        for session in self.active.values():
            with contextlib.suppress(Exception):
                for path in session.directory.glob(f"*{session.extension}"):
                    if str(path) not in known_paths and path.is_file():
                        total += path.stat().st_size
        return total

    def snapshot(self) -> dict:
        now = utcnow()
        active = []
        for s in self.active.values():
            current_size = 0
            with contextlib.suppress(Exception):
                current_size = sum(p.stat().st_size for p in s.directory.glob(f"*{s.extension}") if p.is_file())
            started = s.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            active.append({
                "source_id": s.source_id,
                "source_name": s.source_name,
                "session_id": s.session_id,
                "started_at": s.started_at.isoformat(),
                "elapsed_seconds": max(0, (now - started.astimezone(timezone.utc)).total_seconds()),
                "local_bytes": current_size,
                "container": s.extension.lstrip("."),
                "max_file_bytes": s.max_file_bytes,
                "max_file_human": human_bytes(s.max_file_bytes),
            })
        uptime = max(0, (now - self.started_at).total_seconds()) if self.started_at else 0
        cfg = runtime()
        return {
            "active": active,
            "errors": self.last_errors,
            "upload_current": self.upload_current,
            "uptime_seconds": uptime,
            "health": self.health(),
            "recording_paused": cfg.recording_paused,
            "upload_paused": cfg.upload_paused,
            "buffer_bytes": self.local_buffer_bytes(),
        }

    async def _maintenance_backfill(self) -> None:
        while not self._stopping:
            await self._finalize_closed_stitch_sessions()
            await self._repair_local_mp4s()
            await self._backfill_thumbnails()
            await self._finalize_closed_pixeldrain_days()
            await asyncio.sleep(60)

    async def _repair_local_mp4s(self) -> None:
        """Normalize older local fMP4 files that never reached a reliable upload."""
        with db_session() as db:
            rows = list(db.scalars(
                select(Recording)
                .where(
                    Recording.local_deleted.is_(False),
                    Recording.upload_status.in_(["pending", "failed", "waiting_config", "integrity_failed"]),
                )
                .order_by(Recording.finalized_at.asc())
            ).all())
            for rec in rows:
                db.expunge(rec)
        for rec in rows:
            if self._stopping:
                return
            path = Path(rec.local_path)
            if path.suffix.lower() != ".mp4" or not path.is_file():
                continue
            error_text = f"{rec.integrity_error} {rec.last_error}".lower()
            repairable_media = rec.upload_status == "integrity_failed" and (
                any(marker in error_text for marker in RETRYABLE_MEDIA_ERRORS)
                or "a/v fuori sync" in error_text
            )
            if mp4_is_streaming_ready(path) and not repairable_media:
                continue
            if self._retry_after.get(rec.id, 0) > time.monotonic():
                continue
            claimed = False
            with db_session() as db:
                current = db.get(Recording, rec.id)
                if current and current.upload_status in {"pending", "failed", "waiting_config", "integrity_failed"}:
                    current.upload_status = "converting"
                    claimed = True
            if not claimed:
                continue
            try:
                await self._prepare_mp4(path)
                integrity = await asyncio.to_thread(verify_media, path, runtime().integrity_mode)
                digest = await asyncio.to_thread(sha256_file, path) if integrity.ok else ""
                with db_session() as db:
                    current = db.get(Recording, rec.id)
                    if not current:
                        continue
                    current.duration_seconds = integrity.duration
                    current.size_bytes = path.stat().st_size
                    current.sha256 = digest
                    current.has_video = integrity.has_video
                    current.has_audio = integrity.has_audio
                    current.video_codec = integrity.codec("video")
                    current.audio_codec = integrity.codec("audio")
                    current.integrity_status = "passed" if integrity.ok else "failed"
                    current.integrity_error = integrity.error
                    current.integrity_checked_at = utcnow()
                    current.upload_status = "pending" if integrity.ok else "integrity_failed"
                    current.upload_attempts = 0
                    current.last_error = "" if integrity.ok else f"Integrità fallita: {integrity.error}"[-1600:]
                    if integrity.ok:
                        current.thumbnail_path = ""
                if integrity.ok:
                    self._retry_after.pop(rec.id, None)
                    self.last_errors.pop(f"mp4-repair:{rec.id}", None)
                elif any(marker in (integrity.error or "").lower() for marker in RETRYABLE_MEDIA_ERRORS):
                    self._retry_after[rec.id] = time.monotonic() + 300
                self.wake()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                detail = f"Finalizzazione MP4 fallita: {exc}"[-1200:]
                self.last_errors[f"mp4-repair:{rec.id}"] = detail
                self._retry_after[rec.id] = time.monotonic() + 300
                with db_session() as db:
                    current = db.get(Recording, rec.id)
                    if current and current.upload_status == "converting":
                        current.upload_status = "integrity_failed"
                        current.integrity_status = "failed"
                        current.integrity_error = detail
                        current.last_error = detail

    async def _backfill_thumbnails(self) -> None:
        """Generate persistent previews for local recordings created by older releases."""
        try:
            if not runtime().generate_thumbnails:
                return
            with db_session() as db:
                rows = list(db.scalars(
                    select(Recording)
                    .where(Recording.local_deleted.is_(False))
                    .order_by(Recording.finalized_at.desc())
                ).all())
                for rec in rows:
                    db.expunge(rec)
            for rec in rows:
                if self._stopping or not runtime().generate_thumbnails:
                    return
                path = Path(rec.local_path)
                if not path.exists() or not path.is_file():
                    continue
                digest = rec.sha256 or await asyncio.to_thread(sha256_file, path)
                candidate = settings.data_dir / "thumbnails" / f"{digest[:24]}-sheet-v2.jpg"
                if rec.thumbnail_path == str(candidate) and candidate.is_file():
                    continue
                ok = await asyncio.to_thread(generate_thumbnail, path, candidate, rec.duration_seconds)
                if ok:
                    with db_session() as db:
                        current = db.get(Recording, rec.id)
                        if current:
                            current.thumbnail_path = str(candidate)
                            if not current.sha256:
                                current.sha256 = digest
                await asyncio.sleep(0.03)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_errors["thumbnail-backfill"] = str(exc)[-1000:]

    async def _recover_orphans(self) -> None:
        candidates = sorted(settings.recordings_dir.rglob("*.mkv")) + sorted(settings.recordings_dir.rglob("*.mp4"))
        for path in candidates:
            if path.name.startswith(".") or path.name.endswith(".tmp.mp4") or "_complete" in path.stem:
                continue
            if not path.is_file() or path.stat().st_size <= 0:
                continue
            with db_session() as db:
                if db.scalar(select(Recording).where(Recording.local_path == str(path))):
                    continue
                if db.scalar(select(RecordingFragment).where(RecordingFragment.local_path == str(path))):
                    continue
                sources = list(db.scalars(select(Source)).all())
            marker = path.parent / STITCH_MARKER_NAME
            marker_data = {}
            if marker.is_file():
                with contextlib.suppress(Exception):
                    marker_data = json.loads(marker.read_text(encoding="utf-8"))
            source_folder = path.parent.parent.name if path.parent.parent else "recovered"
            source = next((s for s in sources if int(marker_data.get("source_id") or 0) == s.id), None)
            if source is None:
                source = next((s for s in sources if safe_name(s.name) == source_folder), None)
            source_id = source.id if source else int(marker_data.get("source_id") or 0)
            source_name = source.name if source else str(marker_data.get("source_name") or source_folder)
            session_id = str(marker_data.get("session_id") or path.parent.name)
            if marker.is_file():
                await self._index_fragment(
                    source_id=source_id,
                    source_name=source_name,
                    session_id=session_id,
                    path=path,
                    started_at=None,
                )
            else:
                await self._index_file(
                    source_id=source_id,
                    source_name=source_name,
                    session_id=session_id,
                    path=path,
                    started_at=None,
                )

    async def _wait_until_stable(self, path: Path, timeout: float = 12.0) -> bool:
        """Do not probe a segment while FFmpeg is still flushing its trailer."""
        deadline = time.monotonic() + timeout
        previous: tuple[int, int] | None = None
        stable_samples = 0
        while time.monotonic() < deadline:
            try:
                stat = path.stat()
            except FileNotFoundError:
                return False
            current = (stat.st_size, stat.st_mtime_ns)
            if stat.st_size > 0 and current == previous and time.time() - stat.st_mtime >= 1.0:
                stable_samples += 1
                if stable_samples >= 2:
                    return True
            else:
                stable_samples = 0
            previous = current
            await asyncio.sleep(1)
        return False

    async def _prepare_mp4(self, path: Path) -> bool:
        if path.suffix.lower() != ".mp4":
            return False
        async with self._mp4_finalize_lock:
            return await finalize_mp4_for_streaming(path)

    async def _index_fragment(self, *, source_id: int, source_name: str, session_id: str, path: Path, started_at: datetime | None) -> bool:
        """Validate a capture part but keep it out of Archive/upload until the 20-minute session closes."""
        cfg = runtime()
        if not await self._wait_until_stable(path):
            self.last_errors[f"fragment:{source_id}"] = f"Frammento ancora in scrittura: {path.name}"
            return False
        normalization_error = ""
        try:
            await self._prepare_mp4(path)
        except Exception as exc:
            normalization_error = f"Finalizzazione frammento fallita: {exc}"[-1500:]
        integrity = None if normalization_error else await asyncio.to_thread(verify_media, path, cfg.integrity_mode)
        finalized = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if integrity and integrity.duration:
            start = finalized - timedelta(seconds=integrity.duration)
        elif started_at:
            start = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
            start = start.astimezone(timezone.utc)
        else:
            start = finalized
        with db_session() as db:
            if db.scalar(select(RecordingFragment).where(RecordingFragment.local_path == str(path))):
                return True
            db.add(RecordingFragment(
                source_id=source_id,
                source_name=source_name,
                session_id=session_id,
                local_path=str(path),
                filename=path.name,
                started_at=start,
                finalized_at=finalized,
                duration_seconds=integrity.duration if integrity else None,
                size_bytes=path.stat().st_size,
                container_format=path.suffix.lower().lstrip("."),
                has_video=integrity.has_video if integrity else None,
                has_audio=integrity.has_audio if integrity else None,
                video_codec=integrity.codec("video") if integrity else "",
                audio_codec=integrity.codec("audio") if integrity else "",
                integrity_status="passed" if integrity and integrity.ok else "failed",
                integrity_error=normalization_error or (integrity.error if integrity else "Frammento non valido"),
            ))
        if integrity and integrity.ok:
            self.last_errors.pop(f"fragment:{source_id}", None)
        else:
            self.last_errors[f"fragment:{source_id}"] = normalization_error or (integrity.error if integrity else "Frammento non valido")
        return True

    def _logical_session_id_for(self, source: Source, now: datetime) -> str:
        """Reuse the latest logical session only inside the same Frankfurt day and 20-minute gap."""
        with db_session() as db:
            latest = db.scalar(
                select(RecordingFragment)
                .where(RecordingFragment.source_id == source.id)
                .order_by(RecordingFragment.finalized_at.desc(), RecordingFragment.id.desc())
                .limit(1)
            )
            if not latest:
                return ""
            if cloud_day_key(latest.started_at) != cloud_day_key(now):
                return ""
            return latest.session_id if stitch_gap_open(latest.finalized_at, now) else ""

    async def _finalize_closed_stitch_sessions(self) -> None:
        """Collapse all public-live parts into one user-visible recording after 20 minutes of silence."""
        now = utcnow()
        with db_session() as db:
            rows = list(db.scalars(
                select(RecordingFragment).order_by(
                    RecordingFragment.source_id,
                    RecordingFragment.session_id,
                    RecordingFragment.started_at,
                    RecordingFragment.id,
                )
            ).all())
            for row in rows:
                db.expunge(row)
        groups: dict[tuple[int, str], list[RecordingFragment]] = {}
        for row in rows:
            groups.setdefault((int(row.source_id), row.session_id), []).append(row)
        for (source_id, session_id), items in groups.items():
            if self._stopping or source_id in self.active:
                continue
            latest = max(item.finalized_at for item in items)
            if stitch_gap_open(latest, now):
                continue
            lock = self._source_check_locks.setdefault(source_id, asyncio.Lock())
            async with lock:
                if source_id in self.active:
                    continue
                with db_session() as db:
                    current = list(db.scalars(
                        select(RecordingFragment)
                        .where(
                            RecordingFragment.source_id == source_id,
                            RecordingFragment.session_id == session_id,
                        )
                        .order_by(RecordingFragment.started_at, RecordingFragment.id)
                    ).all())
                    for row in current:
                        db.expunge(row)
                if not current:
                    continue
                latest = max(item.finalized_at for item in current)
                if stitch_gap_open(latest, utcnow()):
                    continue
                try:
                    await self._stitch_fragment_group(current)
                    self.last_errors.pop(f"stitch:{source_id}:{session_id}", None)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_errors[f"stitch:{source_id}:{session_id}"] = str(exc)[-1400:]

    async def _stitch_fragment_group(self, fragments: list[RecordingFragment]) -> None:
        fragments = sorted(fragments, key=lambda item: (item.started_at, item.id))
        good = [item for item in fragments if item.integrity_status == "passed" and Path(item.local_path).is_file()]
        if not good:
            raise RuntimeError("Nessun frammento integro nella sessione")
        first = good[0]
        paths = [Path(item.local_path) for item in good]
        total_bytes = sum(path.stat().st_size for path in paths)
        free = shutil.disk_usage(paths[0].parent).free
        if free < total_bytes + 256 * 1024 * 1024:
            raise RuntimeError("Spazio insufficiente per consolidare la sessione")
        suffixes = {path.suffix.lower() for path in paths}
        suffix = next(iter(suffixes)) if len(suffixes) == 1 else ".mp4"
        output = paths[0].parent / f"{first.session_id}_complete{suffix}"
        output.unlink(missing_ok=True)
        await stitch_recording_parts(paths, output)
        if output.suffix.lower() == ".mp4":
            await self._prepare_mp4(output)
        integrity = await asyncio.to_thread(verify_media, output, runtime().integrity_mode)
        if not integrity.ok:
            output.unlink(missing_ok=True)
            raise RuntimeError(f"Sessione consolidata non valida: {integrity.error}")
        digest = await asyncio.to_thread(sha256_file, output)
        thumb_path = ""
        if runtime().generate_thumbnails:
            candidate = settings.data_dir / "thumbnails" / f"{digest[:24]}-sheet-v2.jpg"
            if await asyncio.to_thread(generate_thumbnail, output, candidate, integrity.duration):
                thumb_path = str(candidate)
        started = min(item.started_at for item in good)
        finalized = max(item.finalized_at for item in good)
        with db_session() as db:
            existing = db.scalar(select(Recording).where(
                Recording.source_id == first.source_id,
                Recording.session_id == first.session_id,
            ))
            if existing is None:
                db.add(Recording(
                    source_id=first.source_id,
                    source_name=first.source_name,
                    session_id=first.session_id,
                    local_path=str(output),
                    filename=output.name,
                    started_at=started,
                    finalized_at=finalized,
                    duration_seconds=integrity.duration,
                    size_bytes=output.stat().st_size,
                    sha256=digest,
                    upload_status="pending",
                    thumbnail_path=thumb_path,
                    integrity_status="passed",
                    integrity_error="",
                    integrity_checked_at=utcnow(),
                    container_format=output.suffix.lower().lstrip("."),
                    has_video=integrity.has_video,
                    has_audio=integrity.has_audio,
                    video_codec=integrity.codec("video"),
                    audio_codec=integrity.codec("audio"),
                ))
            for fragment in db.scalars(select(RecordingFragment).where(
                RecordingFragment.source_id == first.source_id,
                RecordingFragment.session_id == first.session_id,
            )).all():
                db.delete(fragment)
        for path in paths:
            if path != output:
                path.unlink(missing_ok=True)
        for fragment in fragments:
            path = Path(fragment.local_path)
            if path not in paths:
                path.unlink(missing_ok=True)
        (output.parent / STITCH_MARKER_NAME).unlink(missing_ok=True)
        for manifest in output.parent.glob(".livevault-synced-master-*.m3u8"):
            manifest.unlink(missing_ok=True)
        self.wake()

    async def _index_file(self, *, source_id: int, source_name: str, session_id: str, path: Path, started_at: datetime | None) -> bool:
        cfg = runtime()
        if not await self._wait_until_stable(path):
            self.last_errors[f"finalize:{source_id}"] = f"Segmento ancora in scrittura: {path.name}"
            return False
        normalization_error = ""
        try:
            await self._prepare_mp4(path)
        except Exception as exc:
            normalization_error = f"Finalizzazione MP4 fallita: {exc}"[-1500:]
        integrity = None if normalization_error else await asyncio.to_thread(verify_media, path, cfg.integrity_mode)
        digest = "" if normalization_error else await asyncio.to_thread(sha256_file, path)
        finalized = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if integrity and integrity.duration:
            start = finalized - timedelta(seconds=integrity.duration)
        elif started_at:
            start = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
            start = start.astimezone(timezone.utc)
        else:
            start = finalized
        thumb_path = ""
        if cfg.generate_thumbnails and integrity and integrity.ok:
            candidate = settings.data_dir / "thumbnails" / f"{digest[:24]}-sheet-v2.jpg"
            ok = await asyncio.to_thread(generate_thumbnail, path, candidate, integrity.duration)
            if ok:
                thumb_path = str(candidate)
        with db_session() as db:
            if db.scalar(select(Recording).where(Recording.local_path == str(path))):
                return True
            db.add(Recording(
                source_id=source_id,
                source_name=source_name,
                session_id=session_id,
                local_path=str(path),
                filename=path.name,
                started_at=start,
                finalized_at=finalized,
                duration_seconds=integrity.duration if integrity else None,
                size_bytes=path.stat().st_size,
                sha256=digest,
                upload_status="pending" if integrity and integrity.ok else "integrity_failed",
                thumbnail_path=thumb_path,
                integrity_status="passed" if integrity and integrity.ok else "failed",
                integrity_error=normalization_error or (integrity.error if integrity else "Finalizzazione MP4 fallita"),
                integrity_checked_at=utcnow(),
                container_format=path.suffix.lower().lstrip("."),
                has_video=integrity.has_video if integrity else None,
                has_audio=integrity.has_audio if integrity else None,
                video_codec=integrity.codec("video") if integrity else "",
                audio_codec=integrity.codec("audio") if integrity else "",
            ))
        self.last_errors.pop(f"finalize:{source_id}", None)
        return True

    def _observe_live_state(self, db, source: Source, live: bool, observed_at: datetime) -> None:
        open_session = db.scalar(
            select(LiveSession)
            .where(LiveSession.source_id == source.id, LiveSession.ended_at.is_(None))
            .order_by(LiveSession.started_at.desc(), LiveSession.id.desc())
        )
        if live:
            if open_session:
                open_session.last_seen_at = observed_at
            else:
                db.add(LiveSession(
                    source_id=source.id,
                    source_name=source.name,
                    started_at=observed_at,
                    ended_at=None,
                    last_seen_at=observed_at,
                    origin="probe",
                ))
            source.last_seen_live_at = observed_at
        elif open_session:
            started_at = open_session.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            open_session.ended_at = max(observed_at, started_at.astimezone(timezone.utc))
            open_session.last_seen_at = observed_at

    async def _check_source(self, source: Source, semaphore: asyncio.Semaphore) -> None:
        lock = self._source_check_locks.setdefault(source.id, asyncio.Lock())
        async with lock:
            await self._check_source_unlocked(source, semaphore)

    async def _check_source_unlocked(self, source: Source, semaphore: asyncio.Semaphore) -> None:
        if self._stopping or (self._leader_task is not None and self._leader_file is None) or source.id in self.active:
            return
        async with semaphore:
            result = await probe(source.platform, source.slug, source.quality)
            checked_at = utcnow()
            recording_allowed = False
            with db_session() as db:
                current = db.get(Source, source.id)
                if current:
                    if current.last_status != result.status or current.status_changed_at is None:
                        current.status_changed_at = checked_at
                    current.last_status = result.status
                    current.last_checked_at = checked_at
                    current.last_error = result.error
                    current.metadata_status = result.metadata_status
                    current.metadata_error = result.metadata_error
                    if result.last_broadcast is not None:
                        current.last_live_at = result.last_broadcast
                    self._observe_live_state(db, current, bool(result.live), checked_at)
                    recording_allowed = bool(current.enabled and current.consent_confirmed and not current.archived)
            cfg = runtime()
            if not result.live or self._stopping or source.id in self.active or cfg.recording_paused or not recording_allowed:
                return
            state = disk_state()
            if state.free_gb <= cfg.critical_free_gb:
                self.last_errors["storage"] = f"Spazio critico: {state.free_gb:.2f} GB; nuove registrazioni sospese"
                return
            local_buffer = self.local_buffer_bytes()
            if cfg.buffer_max_gb > 0 and local_buffer >= cfg.buffer_max_gb * 1024**3:
                self.last_errors["buffer"] = f"Buffer locale al limite ({human_bytes(local_buffer)} / {cfg.buffer_max_gb:.1f} GB)"
                return
            try:
                with db_session() as db:
                    current = db.get(Source, source.id)
                    if not current or not current.enabled or not current.consent_confirmed or current.archived:
                        return
                    db.expunge(current)
                logical_session_id = self._logical_session_id_for(current, checked_at)
                session = await start_recorder(current, session_id=logical_session_id or None)
                with db_session() as db:
                    latest = db.get(Source, source.id)
                    still_allowed = bool(
                        latest and latest.enabled and latest.consent_confirmed and not latest.archived
                        and not runtime().recording_paused
                    )
                if not still_allowed:
                    await stop_recorder(session)
                    return
                self.active[source.id] = session
                with db_session() as db:
                    current = db.get(Source, source.id)
                    if current:
                        current.last_status = "recording"
                        current.last_seen_live_at = utcnow()
                for key in (f"source:{source.id}", f"ffmpeg:{source.id}", f"watch:{source.id}"):
                    self.last_errors.pop(key, None)
                task = asyncio.create_task(self._watch_session(session), name=f"record-{source.id}")
                self.watch_tasks[source.id] = task
            except Exception as exc:
                self.last_errors[f"source:{source.id}"] = f"Avvio recorder fallito: {exc}"
                with db_session() as db:
                    current = db.get(Source, source.id)
                    if current:
                        current.last_status = "error"
                        current.status_changed_at = utcnow()
                        current.last_error = str(exc)[-1000:]

    async def _poll_loop(self) -> None:
        while not self._stopping:
            try:
                cfg = runtime()
                with db_session() as db:
                    # Paused creators are still probed so the UI can warn when they are
                    # live but intentionally not being recorded. Archived creators are ignored.
                    sources = list(db.scalars(select(Source).where(
                        Source.consent_confirmed.is_(True),
                        Source.archived.is_(False),
                    )).all())
                candidates = [s for s in sources if s.id not in self.active]
                semaphore = asyncio.Semaphore(max(1, cfg.max_probe_concurrency))
                if candidates:
                    await asyncio.gather(*(self._check_source(source, semaphore) for source in candidates))
                await self._sleep_or_wake(max(15, cfg.poll_seconds))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_errors["poller"] = str(exc)[-1200:]
                await self._sleep_or_wake(15)

    async def _drain_stderr(self, session: RecorderSession) -> None:
        if session.process.stderr is None:
            return
        tail: list[str] = []
        try:
            while True:
                line = await session.process.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if text:
                    tail.append(text)
                    tail = tail[-10:]
                    if session.transport_guard and not session.restart_requested:
                        reason = stream_transport_fault(text)
                        if reason:
                            session.restart_requested = True
                            session.restart_reason = reason
        finally:
            if tail and session.process.returncode not in (0, None):
                self.last_errors[f"ffmpeg:{session.source_id}"] = " | ".join(tail)[-1800:]

    async def _watch_session(self, session: RecorderSession) -> None:
        processed: set[Path] = set()
        stderr_task = asyncio.create_task(self._drain_stderr(session))
        try:
            while session.process.returncode is None:
                await asyncio.sleep(1)
                if session.restart_requested:
                    await stop_recorder(session)
                    continue
                if cloud_day_key(session.started_at) != cloud_day_key(utcnow()):
                    session.rollover_requested = True
                    await stop_recorder(session)
                    continue
                files = sorted(session.directory.glob(f"*{session.extension}"), key=lambda p: p.stat().st_mtime)
                if files and files[-1].stat().st_size >= session.safe_stop_bytes:
                    session.rollover_requested = True
                    await stop_recorder(session)
                    continue
                for path in files[:-1]:
                    if path not in processed and path.stat().st_size > 0:
                        if await self._finalize_segment(session, path):
                            processed.add(path)
            await session.process.wait()
            files = sorted(session.directory.glob(f"*{session.extension}"), key=lambda p: p.stat().st_mtime)
            for path in files:
                if path not in processed and path.exists() and path.stat().st_size > 0:
                    if await self._finalize_segment(session, path):
                        processed.add(path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_errors[f"watch:{session.source_id}"] = str(exc)[-1200:]
        finally:
            if session.process.returncode is None:
                await stop_recorder(session)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(stderr_task, timeout=2)
            self.active.pop(session.source_id, None)
            self.watch_tasks.pop(session.source_id, None)
            with contextlib.suppress(OSError):
                session.preview_path.unlink(missing_ok=True)
            if session.manifest_path is not None:
                with contextlib.suppress(OSError):
                    session.manifest_path.unlink(missing_ok=True)
            total_session_bytes = 0
            with contextlib.suppress(Exception):
                total_session_bytes = sum(
                    path.stat().st_size for path in session.directory.glob(f"*{session.extension}") if path.is_file()
                )
            size_rollover = session.rollover_requested or total_session_bytes >= session.safe_stop_bytes
            controlled_restart = session.restart_requested
            with db_session() as db:
                source = db.get(Source, session.source_id)
                if source:
                    now = utcnow()
                    if source.archived:
                        new_status = "archived"
                    elif not source.consent_confirmed:
                        new_status = "paused"
                    elif (
                        not source.enabled or self._stopping or runtime().recording_paused
                        or size_rollover or controlled_restart
                    ):
                        # A controlled stop (deploy/global pause/rollover/HLS restart) is not evidence
                        # that the creator went offline. The next probe closes the session
                        # if the stream actually ended.
                        new_status = "live"
                    else:
                        new_status = "offline"
                    if source.last_status != new_status or source.status_changed_at is None:
                        source.status_changed_at = now
                    source.last_status = new_status
                    source.last_checked_at = now
                    self._observe_live_state(db, source, new_status == "live", now)
                    if size_rollover or controlled_restart:
                        source.last_error = ""
            self.wake()

    async def _finalize_segment(self, session: RecorderSession, path: Path) -> bool:
        try:
            part_index = int(path.stem.rsplit("part", 1)[1])
        except Exception:
            part_index = 0
        started = session.started_at + timedelta(seconds=part_index * runtime().segment_minutes * 60)
        indexed = await self._index_fragment(
            source_id=session.source_id,
            source_name=session.source_name,
            session_id=session.session_id,
            path=path,
            started_at=started,
        )
        if indexed:
            self.wake()
        return indexed

    def _pending_recording(self) -> Recording | None:
        now = time.monotonic()
        cfg = runtime()
        if cfg.upload_paused:
            return None
        with db_session() as db:
            rec = db.scalar(
                select(Recording)
                .where(Recording.local_deleted.is_(False), Recording.upload_status == "pending", Recording.integrity_status == "passed")
                .order_by(Recording.upload_priority.desc(), Recording.finalized_at.asc())
                .limit(1)
            )
            if not rec:
                candidates = list(db.scalars(
                    select(Recording)
                    .where(Recording.local_deleted.is_(False))
                    .where(Recording.integrity_status == "passed")
                    .where(Recording.upload_status.in_(["failed", "waiting_config"]))
                    .where(Recording.upload_attempts < cfg.max_upload_attempts)
                    .order_by(Recording.upload_priority.desc(), Recording.finalized_at.asc())
                    .limit(100)
                ).all())
                rec = next((r for r in candidates if self._retry_after.get(r.id, 0) <= now), None)
            if rec:
                rec.upload_status = "uploading"
                rec.upload_attempts += 1
                rec.upload_priority = 0
                db.flush()
                db.expunge(rec)
            return rec

    def _cloud_day_spec(self, rec: Recording) -> tuple[int | None, str, str, bool]:
        day_key = cloud_day_key(rec.started_at)
        with db_session() as db:
            source = db.get(Source, rec.source_id)
            if not source:
                return None, day_key, f"{rec.source_name} - {day_key}", False
            profile = db.get(Profile, source.profile_id) if source.profile_id is not None else None
            display_name = profile.display_name if profile else source.name
            return source.profile_id, day_key, f"{display_name} - {day_key}"[:255], bool(source.organize_cloud)

    async def _gofile_folder_for(self, rec: Recording) -> tuple[str, str, str]:
        """Return the Gofile folder dedicated to this creator and Frankfurt calendar day."""
        profile_id, day_key, title, organize = self._cloud_day_spec(rec)
        if not organize or profile_id is None:
            return "", "", day_key
        with db_session() as db:
            existing = db.scalar(select(CloudDay).where(
                CloudDay.profile_id == profile_id,
                CloudDay.day_key == day_key,
                CloudDay.provider == "gofile",
            ))
            if existing and existing.remote_id:
                return existing.remote_id, existing.remote_url, day_key
        folder_id, folder_url = await asyncio.to_thread(
            create_gofile_folder, title, runtime().gofile_folder_id
        )
        with db_session() as db:
            row = db.scalar(select(CloudDay).where(
                CloudDay.profile_id == profile_id,
                CloudDay.day_key == day_key,
                CloudDay.provider == "gofile",
            ))
            if row is None:
                row = CloudDay(profile_id=profile_id, day_key=day_key, provider="gofile")
                db.add(row)
            row.title = title
            row.remote_id = folder_id
            row.remote_url = folder_url
            row.updated_at = utcnow()
        return folder_id, folder_url, day_key

    async def _finalize_closed_pixeldrain_days(self) -> None:
        """Create one Pixeldrain list after a Frankfurt day closes; never churn lists during the day."""
        today_key = datetime.now(CLOUD_TIME_ZONE).date().isoformat()
        with db_session() as db:
            rows = db.execute(
                select(Recording, Source.profile_id)
                .join(Source, Source.id == Recording.source_id)
                .where(
                    Recording.upload_status == "uploaded",
                    Recording.upload_provider == "pixeldrain",
                    Recording.remote_id != "",
                    Source.profile_id.is_not(None),
                )
                .order_by(Recording.started_at.asc(), Recording.id.asc())
            ).all()
            profile_ids = {int(profile_id) for _recording, profile_id in rows if profile_id is not None}
            profiles = {
                int(profile.id): profile.display_name
                for profile in db.scalars(select(Profile).where(Profile.id.in_(profile_ids))).all()
            } if profile_ids else {}
            existing = {
                (int(row.profile_id), row.day_key): (row.remote_id, int(row.file_count or 0))
                for row in db.scalars(select(CloudDay).where(CloudDay.provider == "pixeldrain")).all()
            }
        grouped: dict[tuple[int, str], list[tuple[int, str]]] = {}
        for recording, profile_id in rows:
            if profile_id is None:
                continue
            key = cloud_day_key(recording.started_at)
            if key >= today_key:
                continue
            grouped.setdefault((int(profile_id), key), []).append((int(recording.id), str(recording.remote_id)))
        for (profile_id, day_key), items in grouped.items():
            if self._stopping:
                return
            remote_ids = list(dict.fromkeys(remote_id for _recording_id, remote_id in items if remote_id))
            old_id, old_count = existing.get((profile_id, day_key), ("", 0))
            if old_id and old_count == len(remote_ids):
                continue
            title = f"{profiles.get(profile_id, f'Creator {profile_id}')} - {day_key}"[:300]
            try:
                list_id, list_url = await asyncio.to_thread(create_pixeldrain_list, title, remote_ids)
            except Exception as exc:
                self.last_errors[f"cloud-day:pixeldrain:{profile_id}:{day_key}"] = str(exc)[-900:]
                continue
            recording_ids = [recording_id for recording_id, _remote_id in items]
            with db_session() as db:
                row = db.scalar(select(CloudDay).where(
                    CloudDay.profile_id == profile_id,
                    CloudDay.day_key == day_key,
                    CloudDay.provider == "pixeldrain",
                ))
                if row is None:
                    row = CloudDay(profile_id=profile_id, day_key=day_key, provider="pixeldrain")
                    db.add(row)
                row.title = title
                row.remote_id = list_id
                row.remote_url = list_url
                row.file_count = len(remote_ids)
                row.updated_at = utcnow()
                for recording in db.scalars(select(Recording).where(Recording.id.in_(recording_ids))).all():
                    recording.cloud_day_key = day_key
                    recording.remote_parent_id = list_id
                    recording.remote_parent_url = list_url
            self.last_errors.pop(f"cloud-day:pixeldrain:{profile_id}:{day_key}", None)

    async def _verify_before_upload(self, rec: Recording, path: Path) -> bool:
        cfg = runtime()
        if not await self._wait_until_stable(path):
            with db_session() as db:
                current = db.get(Recording, rec.id)
                if current:
                    current.upload_status = "failed"
                    current.last_error = "Upload rinviato: il file è ancora in scrittura"
            self._retry_after[rec.id] = time.monotonic() + 30
            return False
        try:
            normalized = await self._prepare_mp4(path)
        except Exception as exc:
            detail = f"Finalizzazione MP4 fallita: {exc}"[-1500:]
            with db_session() as db:
                current = db.get(Recording, rec.id)
                if current:
                    current.integrity_status = "failed"
                    current.integrity_error = detail
                    current.integrity_checked_at = utcnow()
                    current.upload_status = "integrity_failed"
                    current.last_error = detail
            return False
        baseline = path.stat()
        integrity = await asyncio.to_thread(verify_media, path, cfg.integrity_mode)
        if not integrity.ok:
            with db_session() as db:
                current = db.get(Recording, rec.id)
                if current:
                    current.has_video = integrity.has_video
                    current.has_audio = integrity.has_audio
                    current.video_codec = integrity.codec("video")
                    current.audio_codec = integrity.codec("audio")
                    current.integrity_status = "failed"
                    current.integrity_error = integrity.error
                    current.integrity_checked_at = utcnow()
                    current.upload_status = "integrity_failed"
                    current.last_error = f"Controllo integrità fallito: {integrity.error}"[-1600:]
            return False
        current_stat = path.stat()
        if (current_stat.st_size, current_stat.st_mtime_ns) != (baseline.st_size, baseline.st_mtime_ns):
            with db_session() as db:
                current = db.get(Recording, rec.id)
                if current:
                    current.upload_status = "failed"
                    current.last_error = "Upload rinviato: il file è cambiato durante la verifica"
            self._retry_after[rec.id] = time.monotonic() + 30
            return False
        digest = await asyncio.to_thread(sha256_file, path)
        if rec.sha256 and digest != rec.sha256 and not normalized:
            with db_session() as db:
                current = db.get(Recording, rec.id)
                if current:
                    current.integrity_status = "failed"
                    current.integrity_error = "SHA-256 cambiato dopo la finalizzazione"
                    current.integrity_checked_at = utcnow()
                    current.upload_status = "integrity_failed"
                    current.last_error = "SHA-256 non coincide: file locale modificato o corrotto"
            return False
        with db_session() as db:
            current = db.get(Recording, rec.id)
            if current:
                current.has_video = integrity.has_video
                current.has_audio = integrity.has_audio
                current.video_codec = integrity.codec("video")
                current.audio_codec = integrity.codec("audio")
                current.duration_seconds = integrity.duration
                current.size_bytes = path.stat().st_size
                current.sha256 = digest
                current.integrity_status = "passed"
                current.integrity_error = ""
                current.integrity_checked_at = utcnow()
        return True

    async def _upload_loop(self) -> None:
        while not self._stopping:
            rec: Recording | None = None
            try:
                cfg = runtime()
                if cfg.upload_paused:
                    self.upload_current = None
                    await self._sleep_or_wake(2)
                    continue
                rec = self._pending_recording()
                if not rec:
                    self.upload_current = None
                    await self._sleep_or_wake(2)
                    continue
                path = Path(rec.local_path)
                self.upload_current = {
                    "recording_id": rec.id,
                    "filename": rec.filename,
                    "source_name": rec.source_name,
                    "size_bytes": rec.size_bytes,
                    "attempt": rec.upload_attempts,
                    "provider": "",
                    "sent_bytes": 0,
                    "percent": 0.0,
                }
                if not path.exists():
                    with db_session() as db:
                        current = db.get(Recording, rec.id)
                        if current:
                            current.local_deleted = True
                            current.upload_status = "missing"
                            current.last_error = "File locale mancante prima dell'upload"
                    self._retry_after.pop(rec.id, None)
                    continue
                if not await self._verify_before_upload(rec, path):
                    continue

                cfg = runtime()
                providers: list[str] = []
                for p in (cfg.primary_uploader, cfg.fallback_uploader):
                    if p not in providers and provider_available(p):
                        providers.append(p)
                if not providers:
                    with db_session() as db:
                        current = db.get(Recording, rec.id)
                        if current:
                            current.upload_status = "waiting_config"
                            current.upload_attempts = max(0, current.upload_attempts - 1)
                            current.last_error = "Nessun uploader configurato con credenziali valide"
                    self._retry_after[rec.id] = time.monotonic() + 30
                    continue

                errors: list[str] = []
                result = None
                for provider in providers:
                    try:
                        self.upload_current["provider"] = provider
                        self.upload_current["sent_bytes"] = 0
                        self.upload_current["percent"] = 0.0

                        def progress(sent: int, total: int) -> None:
                            if self._stopping:
                                raise UploadCancelled("Upload interrotto in sicurezza per riavvio")
                            if self.upload_current and self.upload_current.get("recording_id") == rec.id:
                                self.upload_current["sent_bytes"] = sent
                                self.upload_current["percent"] = round((sent / total * 100) if total else 0, 1)

                        gofile_folder_id = ""
                        gofile_folder_url = ""
                        recording_day_key = cloud_day_key(rec.started_at)
                        if provider == "gofile":
                            gofile_folder_id, gofile_folder_url, recording_day_key = await self._gofile_folder_for(rec)
                        result = await asyncio.to_thread(
                            upload,
                            path,
                            provider,
                            progress,
                            gofile_folder_id,
                        )
                        if result.verified:
                            break
                        errors.append(f"{provider}: verifica remota non riuscita")
                        result = None
                    except UploadCancelled:
                        raise
                    except Exception as exc:
                        errors.append(f"{provider}: {exc}")
                        result = None
                if result and result.verified:
                    with db_session() as db:
                        current = db.get(Recording, rec.id)
                        if current:
                            current.upload_status = "uploaded"
                            current.upload_provider = result.provider
                            current.remote_id = result.remote_id
                            current.remote_url = result.remote_url
                            current.cloud_day_key = recording_day_key
                            if result.provider == "gofile" and gofile_folder_id:
                                current.remote_parent_id = gofile_folder_id
                                current.remote_parent_url = gofile_folder_url
                            current.uploaded_at = utcnow()
                            current.last_error = ""
                    if result.provider == "gofile" and gofile_folder_id:
                        profile_id, day_key, _title, _organize = self._cloud_day_spec(rec)
                        if profile_id is not None:
                            with db_session() as db:
                                day = db.scalar(select(CloudDay).where(
                                    CloudDay.profile_id == profile_id,
                                    CloudDay.day_key == day_key,
                                    CloudDay.provider == "gofile",
                                ))
                                if day:
                                    day.file_count = int(day.file_count or 0) + 1
                                    day.updated_at = utcnow()
                    self._retry_after.pop(rec.id, None)
                    if runtime().delete_after_upload:
                        path.unlink(missing_ok=True)
                        with db_session() as db:
                            current = db.get(Recording, rec.id)
                            if current:
                                current.local_deleted = True
                else:
                    delay = min(3600, max(30, cfg.upload_retry_seconds) * (2 ** min(max(rec.upload_attempts - 1, 0), 4)))
                    self._retry_after[rec.id] = time.monotonic() + delay
                    with db_session() as db:
                        current = db.get(Recording, rec.id)
                        if current:
                            current.upload_status = "failed"
                            current.last_error = (" | ".join(errors)[-1600:] or "Upload verification failed") + f" · retry ~{int(delay)}s"
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except UploadCancelled:
                if rec:
                    with contextlib.suppress(Exception):
                        with db_session() as db:
                            current = db.get(Recording, rec.id)
                            if current and current.upload_status == "uploading":
                                current.upload_status = "pending"
                                current.last_error = "Upload interrotto in sicurezza per riavvio; rimesso in coda"
                continue
            except Exception as exc:
                self.last_errors["uploader"] = str(exc)[-1400:]
                if rec:
                    self._retry_after[rec.id] = time.monotonic() + 30
                    with contextlib.suppress(Exception):
                        with db_session() as db:
                            current = db.get(Recording, rec.id)
                            if current and current.upload_status == "uploading":
                                current.upload_status = "failed"
                                current.last_error = f"Errore uploader inatteso: {exc}"[-1600:]
                await asyncio.sleep(3)
            finally:
                if rec and self.upload_current and self.upload_current.get("recording_id") == rec.id:
                    self.upload_current = None

    async def _cleanup_loop(self) -> None:
        while not self._stopping:
            try:
                cfg = runtime()
                state = disk_state()
                buffer_bytes = self.local_buffer_bytes()
                over_buffer = cfg.buffer_max_gb > 0 and buffer_bytes > cfg.buffer_max_gb * 1024**3
                if state.free_gb <= cfg.emergency_free_gb:
                    self.last_errors["storage"] = f"EMERGENZA disco: {state.free_gb:.2f} GB liberi; arresto controllato recorder"
                    await self.stop_all_recordings()
                elif over_buffer:
                    self.last_errors["buffer"] = f"Buffer oltre limite: {human_bytes(buffer_bytes)} / {cfg.buffer_max_gb:.1f} GB"
                    if cfg.buffer_hard_stop:
                        await self.stop_all_recordings()
                else:
                    self.last_errors.pop("buffer", None)
                    if state.free_gb > cfg.min_free_gb:
                        self.last_errors.pop("storage", None)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_errors["storage-guard"] = str(exc)[-1000:]
                await asyncio.sleep(10)


manager = WorkerManager()
