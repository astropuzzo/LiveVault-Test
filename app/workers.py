from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .config import settings
from .db import CloudDay, LiveSession, Profile, Recording, RecordingFragment, Source, db_session
from .recorder import (
    LIVE_PREVIEW_INTERVAL_SECONDS,
    LIVE_PREVIEW_MAX_AGE_SECONDS,
    RecorderSession,
    STITCH_MARKER_NAME,
    finalize_mp4_for_streaming,
    live_preview_path,
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
from .utils import generate_live_preview, generate_thumbnail, human_bytes, safe_name, sha256_file, utcnow, verify_media

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - production containers are Linux
    _fcntl = None

RETRYABLE_MEDIA_ERRORS = ("scadut", "timeout", "timed out", "tempor", "gap video")
CLOUD_TIME_ZONE = ZoneInfo("Europe/Berlin")
SESSION_STITCH_GAP_SECONDS = 20 * 60
SESSION_STITCH_READY_SECONDS = 15 * 60
NONFATAL_FFMPEG_NOISE = ("found duplicated moov atom. skipped it",)


def cloud_day_key(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CLOUD_TIME_ZONE).date().isoformat()


def public_recording_filename(source_name: str, started_at: datetime, sequence: int, suffix: str) -> str:
    """Build a stable filename whose lexical order matches capture chronology."""
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    local_started = started_at.astimezone(CLOUD_TIME_ZONE)
    clean_source = safe_name(source_name) or "recording"
    extension = suffix if suffix.startswith(".") else f".{suffix}"
    return f"{max(1, int(sequence)):03d}_{clean_source}_{local_started:%Y-%m-%d_%H-%M-%S}{extension}"


def stitch_gap_open(last_at: datetime, now: datetime, gap_seconds: int = SESSION_STITCH_GAP_SECONDS) -> bool:
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = (now.astimezone(timezone.utc) - last_at.astimezone(timezone.utc)).total_seconds()
    return 0 <= delta <= max(0, int(gap_seconds))


def fragment_usable_for_stitch(fragment: RecordingFragment) -> bool:
    path = Path(fragment.local_path)
    if not path.is_file():
        return False
    if fragment.integrity_status == "passed":
        return True
    # Rescue fragments indexed by pre-hotfix builds where the only failure was
    # a timestamp discontinuity. The final combined media is verified again.
    error = str(fragment.integrity_error or "").lower()
    return fragment.integrity_status == "failed" and error.startswith("gap video rilevato:")


def capture_output_files(session: RecorderSession) -> list[Path]:
    """Return FFmpeg parts only; consolidated outputs must never be re-indexed."""
    def capture_part(path: Path) -> bool:
        stem = path.stem
        suffix = stem.rsplit("_part", 1)[1] if "_part" in stem else (stem[4:] if stem.startswith("part") else "")
        return bool(suffix) and suffix.isdigit()

    return sorted(
        (
            path for path in session.directory.glob(f"*{session.extension}")
            if path.is_file() and not path.name.startswith(".") and capture_part(path)
        ),
        key=lambda path: path.stat().st_mtime,
    )


class WorkerManager:
    def __init__(self) -> None:
        self.active: dict[int, RecorderSession] = {}
        self.watch_tasks: dict[int, asyncio.Task] = {}
        self.finalizing_tasks: set[asyncio.Task] = set()
        self.tasks: list[asyncio.Task] = []
        self._leader_task: asyncio.Task | None = None
        self._leader_file = None
        self._stopping = False
        self._wake_event = asyncio.Event()
        self.last_errors: dict[str, str] = {}
        self.started_at: datetime | None = None
        self.upload_current: dict | None = None
        self._retry_after: dict[int, float] = {}
        self._stitch_retry_after: dict[tuple[int, str], float] = {}
        self._source_check_locks: dict[int, asyncio.Lock] = {}
        self._fragment_index_locks: dict[int, asyncio.Lock] = {}
        self._session_continuations: dict[int, tuple[str, float]] = {}
        self._preview_locks: dict[int, asyncio.Lock] = {}
        self._capture_playback_locks: dict[int, threading.Lock] = {}
        self._preview_semaphore = asyncio.Semaphore(1)
        self._mp4_finalize_lock = asyncio.Lock()
        self._recovery_lock = asyncio.Lock()
        self.recovery_task: asyncio.Task | None = None
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
                self.tasks = [
                    asyncio.create_task(self._poll_loop(), name="source-poller"),
                    asyncio.create_task(self._upload_loop(), name="uploader"),
                    asyncio.create_task(self._cleanup_loop(), name="storage-guard"),
                ]
                # Recording must resume immediately after a reboot. Multi-GB
                # orphan inspection runs beside the poller, never in front of it.
                self._start_recovery_task()
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
        for task in list(self.finalizing_tasks):
            task.cancel()
        if self.recovery_task and not self.recovery_task.done():
            self.recovery_task.cancel()
        if self.backfill_task and not self.backfill_task.done():
            self.backfill_task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.backfill_task:
            with contextlib.suppress(Exception):
                await asyncio.gather(self.backfill_task, return_exceptions=True)
        if self.finalizing_tasks:
            with contextlib.suppress(Exception):
                await asyncio.gather(*self.finalizing_tasks, return_exceptions=True)
        if self.recovery_task:
            with contextlib.suppress(Exception):
                await asyncio.gather(self.recovery_task, return_exceptions=True)
        if self._leader_task:
            with contextlib.suppress(Exception):
                await asyncio.gather(self._leader_task, return_exceptions=True)
        self.started_at = None

    def wake(self) -> None:
        self._wake_event.set()

    def clear_retry_backoff(self) -> None:
        self._retry_after.clear()

    def _start_recovery_task(self) -> bool:
        if self._stopping or (self.recovery_task and not self.recovery_task.done()):
            return False
        task = asyncio.create_task(self._run_recovery_pass(), name="media-recovery")
        self.recovery_task = task
        task.add_done_callback(self._recovery_done)
        return True

    def _recovery_done(self, task: asyncio.Task) -> None:
        if self.recovery_task is task:
            self.recovery_task = None
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.last_errors["recovery"] = str(error)[-1400:]

    def request_recovery(self) -> bool:
        """Schedule one recovery pass only in the process owning the worker lock."""
        if self._leader_file is None:
            return False
        self._stitch_retry_after.clear()
        return self._start_recovery_task()

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
            "recovery": "running" if self.recovery_task and not self.recovery_task.done() else "idle",
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
                current_size = sum(path.stat().st_size for path in capture_output_files(s))
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

    def active_capture_path(self, source_id: int) -> Path | None:
        session = self.active.get(int(source_id))
        if session is None:
            return None
        try:
            started_at = session.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            started_timestamp = started_at.timestamp() - 3
            candidates = [
                path for path in session.directory.iterdir()
                if path.is_file()
                and not path.name.startswith(".")
                and path.stat().st_size > 0
                and path.stat().st_mtime >= started_timestamp
                and (
                    path.suffix.lower() == session.extension.lower()
                    or path.name.lower().endswith((".capture.mp4", ".capture.webm"))
                )
            ]
        except OSError:
            return None
        if not candidates:
            return None
        # Stripchat writes the current MediaRecorder stream to a .capture file
        # before finalizing it. Prefer that live file over a completed part
        # from an earlier reconnect.
        return max(
            candidates,
            key=lambda path: (
                path.name.lower().endswith((".capture.mp4", ".capture.webm")),
                path.stat().st_mtime,
            ),
        )

    def playable_active_capture_path(self, source_id: int) -> Path | None:
        """Return a finalized browser preview without touching active capture."""
        source = self.active_capture_path(source_id)
        if source is None or ".capture." not in source.name.lower():
            return source
        target = source.with_name(f".{source.name}.browser.mp4")
        try:
            if target.is_file() and target.stat().st_size > 0:
                return target
        except OSError:
            pass
        lock = self._capture_playback_locks.setdefault(int(source_id), threading.Lock())
        with lock:
            try:
                if target.is_file() and target.stat().st_size > 0:
                    return target
            except OSError:
                pass
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp.mp4")
            temporary.unlink(missing_ok=True)
            common = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-t", "30", "-map", "0:v:0",
                "-map", "0:a:0?", "-avoid_negative_ts", "make_zero",
            ]
            attempts = (
                ["-c", "copy", "-movflags", "+faststart", "-f", "mp4"],
                [
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                    "-f", "mp4",
                ],
            )
            try:
                for codec_args in attempts:
                    temporary.unlink(missing_ok=True)
                    result = subprocess.run(
                        [*common, *codec_args, str(temporary)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=45,
                        check=False,
                    )
                    if result.returncode == 0 and temporary.is_file() and temporary.stat().st_size > 0:
                        os.replace(temporary, target)
                        return target
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                temporary.unlink(missing_ok=True)
        return source

    async def live_preview_for(self, source_id: int) -> Path | None:
        """Refresh a live JPEG only when an authenticated browser requests it."""
        session = self.active.get(int(source_id))
        if session is None:
            return None
        output = live_preview_path(source_id)

        def fresh(max_age: int) -> bool:
            try:
                stat = output.stat()
                return stat.st_size > 0 and time.time() - stat.st_mtime <= max_age
            except OSError:
                return False

        if fresh(LIVE_PREVIEW_INTERVAL_SECONDS):
            return output
        lock = self._preview_locks.setdefault(int(source_id), asyncio.Lock())
        async with lock:
            if fresh(LIVE_PREVIEW_INTERVAL_SECONDS):
                return output
            current = self.active.get(int(source_id))
            if current is None or current is not session:
                return None
            try:
                candidates = list(reversed(capture_output_files(current)))
            except OSError:
                candidates = []
            async with self._preview_semaphore:
                for candidate in candidates[:2]:
                    if await asyncio.to_thread(generate_live_preview, candidate, output):
                        return output
            return output if fresh(LIVE_PREVIEW_MAX_AGE_SECONDS) else None

    async def _maintenance_backfill(self) -> None:
        recovery = self.recovery_task
        if recovery and recovery is not asyncio.current_task():
            with contextlib.suppress(Exception):
                await asyncio.shield(recovery)
        while not self._stopping:
            async with self._recovery_lock:
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

    async def _run_recovery_pass(self) -> None:
        async with self._recovery_lock:
            await self._recover_stale_finalizing_files()
            await self._recover_orphans()
            await self._finalize_closed_stitch_sessions()
        self.last_errors.pop("recovery", None)

    async def _recover_stale_finalizing_files(self) -> None:
        """Resolve remux leftovers without ever deleting the original capture."""
        cutoff = time.time() - 30 * 60
        for temporary in sorted(settings.recordings_dir.rglob(".*.finalizing.mp4")):
            if self._stopping:
                return
            if not temporary.is_file():
                continue
            try:
                if temporary.stat().st_mtime > cutoff:
                    continue
            except OSError:
                continue
            stem = temporary.name[1:-len(".finalizing.mp4")]
            original = temporary.with_name(f"{stem}.mp4")
            if original.is_file() and original.stat().st_size > 0:
                temporary.unlink(missing_ok=True)
                continue
            integrity = await asyncio.to_thread(verify_media, temporary, "quick")
            if integrity.ok:
                temporary.replace(original)
                self.last_errors.pop(f"recovery-temp:{temporary}", None)
            else:
                self.last_errors[f"recovery-temp:{temporary}"] = (
                    f"Copia temporanea conservata: {integrity.error or temporary.name}"
                )[-1400:]

    async def _recover_orphans(self) -> None:
        candidates = sorted(settings.recordings_dir.rglob("*.mkv")) + sorted(settings.recordings_dir.rglob("*.mp4"))
        for path in candidates:
            active_directories = {session.directory.resolve() for session in self.active.values()}
            if path.name.startswith(".") or path.name.endswith(".tmp.mp4") or "_complete" in path.stem:
                continue
            if not path.is_file() or path.stat().st_size <= 0:
                continue
            with contextlib.suppress(OSError):
                if path.parent.resolve() in active_directories:
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
        lock = self._fragment_index_locks.setdefault(int(source_id), asyncio.Lock())
        async with lock:
            return await self._index_fragment_unlocked(
                source_id=source_id,
                source_name=source_name,
                session_id=session_id,
                path=path,
                started_at=started_at,
            )

    async def _index_fragment_unlocked(self, *, source_id: int, source_name: str, session_id: str, path: Path, started_at: datetime | None) -> bool:
        """Expose a stable local part immediately, then validate it in place."""
        if not await self._wait_until_stable(path):
            self.last_errors[f"fragment:{source_id}"] = f"Frammento ancora in scrittura: {path.name}"
            return False
        finalized = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if started_at:
            start = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
            start = start.astimezone(timezone.utc)
        else:
            start = finalized
        with db_session() as db:
            existing = db.scalar(select(RecordingFragment).where(RecordingFragment.local_path == str(path)))
            if existing and existing.integrity_status != "checking":
                return True
            if existing is None:
                existing = RecordingFragment(
                    source_id=source_id,
                    source_name=source_name,
                    session_id=session_id,
                    local_path=str(path),
                    filename=path.name,
                    started_at=start,
                    finalized_at=finalized,
                    duration_seconds=max(0.0, (finalized - start).total_seconds()),
                    size_bytes=path.stat().st_size,
                    container_format=path.suffix.lower().lstrip("."),
                    integrity_status="checking",
                    integrity_error="",
                )
                db.add(existing)
        self.wake()
        # A short head probe makes the part available in seconds. The complete
        # stitched output still receives the configured full integrity scan.
        integrity = await asyncio.to_thread(verify_media, path, "quick")
        start = finalized - timedelta(seconds=integrity.duration) if integrity.duration else start
        with db_session() as db:
            current = db.scalar(select(RecordingFragment).where(RecordingFragment.local_path == str(path)))
            if current is None:
                return False
            current.started_at = start
            current.finalized_at = finalized
            current.duration_seconds = integrity.duration
            current.size_bytes = path.stat().st_size
            current.has_video = integrity.has_video
            current.has_audio = integrity.has_audio
            current.video_codec = integrity.codec("video")
            current.audio_codec = integrity.codec("audio")
            current.integrity_status = "passed" if integrity.ok else "failed"
            current.integrity_error = integrity.error
        if integrity.ok:
            self.last_errors.pop(f"fragment:{source_id}", None)
        else:
            self.last_errors[f"fragment:{source_id}"] = integrity.error or "Frammento non valido"
        return True

    async def _revalidate_retryable_fragments(self, fragments: list[RecordingFragment]) -> None:
        """Recover parts rejected by older builds after a remux timeout."""
        for fragment in fragments:
            if fragment_usable_for_stitch(fragment):
                continue
            error = str(fragment.integrity_error or "").lower()
            retryable = fragment.integrity_status in {"checking", "failed"} or "finalizzazione frammento fallita" in error or any(
                marker in error for marker in RETRYABLE_MEDIA_ERRORS
            )
            path = Path(fragment.local_path)
            if not retryable or not path.is_file():
                continue
            integrity = await asyncio.to_thread(verify_media, path, "quick")
            if not integrity.ok and path.suffix.lower() == ".mp4":
                # Power loss can leave an incomplete final MP4 box. FFmpeg can
                # often rebuild the index while keeping the original untouched
                # unless the atomic replacement succeeds.
                with contextlib.suppress(Exception):
                    await self._prepare_mp4(path)
                    integrity = await asyncio.to_thread(verify_media, path, "quick")
            if not integrity.ok:
                continue
            fragment.duration_seconds = integrity.duration
            fragment.size_bytes = path.stat().st_size
            fragment.has_video = integrity.has_video
            fragment.has_audio = integrity.has_audio
            fragment.video_codec = integrity.codec("video")
            fragment.audio_codec = integrity.codec("audio")
            fragment.integrity_status = "passed"
            fragment.integrity_error = ""
            with db_session() as db:
                current = db.get(RecordingFragment, fragment.id)
                if current:
                    current.duration_seconds = fragment.duration_seconds
                    current.size_bytes = fragment.size_bytes
                    current.has_video = fragment.has_video
                    current.has_audio = fragment.has_audio
                    current.video_codec = fragment.video_codec
                    current.audio_codec = fragment.audio_codec
                    current.integrity_status = "passed"
                    current.integrity_error = ""
        if all(fragment_usable_for_stitch(item) for item in fragments):
            self.last_errors.pop(f"fragment:{fragments[0].source_id}", None)

    def _logical_session_id_for(self, source: Source, now: datetime) -> str:
        """Reuse the latest logical session only inside the same Frankfurt day and 20-minute gap."""
        continuation = self._session_continuations.get(source.id)
        if continuation:
            session_id, expires_at = continuation
            if time.monotonic() <= expires_at:
                return session_id
            self._session_continuations.pop(source.id, None)
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
            if self._stopping:
                continue
            retry_key = (source_id, session_id)
            if self._stitch_retry_after.get(retry_key, 0) > time.monotonic():
                continue
            await self._revalidate_retryable_fragments(items)
            latest = max(item.finalized_at for item in items)
            ready_seconds = sum(
                float(item.duration_seconds or 0)
                for item in items
                if fragment_usable_for_stitch(item)
            )
            if ready_seconds < SESSION_STITCH_READY_SECONDS and stitch_gap_open(latest, now):
                continue
            # Stitching may scan gigabytes. It must not own the source probe lock:
            # capture should resume immediately while an older immutable batch closes.
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
            ready_seconds = sum(
                float(item.duration_seconds or 0)
                for item in current
                if fragment_usable_for_stitch(item)
            )
            if ready_seconds < SESSION_STITCH_READY_SECONDS and stitch_gap_open(latest, utcnow()):
                continue
            try:
                active = self.active.get(source_id)
                await self._stitch_fragment_group(
                    current,
                    allow_transcode=not (active and active.session_id == session_id),
                )
                self._stitch_retry_after.pop(retry_key, None)
                self.last_errors.pop(f"stitch:{source_id}:{session_id}", None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._stitch_retry_after[retry_key] = time.monotonic() + 300
                self.last_errors[f"stitch:{source_id}:{session_id}"] = str(exc)[-1400:]

    def _recording_day_sequence(
        self, source_id: int, started_at: datetime, recording_id: int | None = None
    ) -> tuple[int, str]:
        """Return a creator/day ordinal and the public creator label."""
        day_key = cloud_day_key(started_at)
        with db_session() as db:
            source = db.get(Source, source_id)
            profile = db.get(Profile, source.profile_id) if source and source.profile_id is not None else None
            display_name = profile.display_name if profile else (source.name if source else "recording")
            if profile is not None:
                rows = db.execute(
                    select(Recording.id, Recording.started_at)
                    .join(Source, Source.id == Recording.source_id)
                    .where(Source.profile_id == profile.id)
                    .order_by(Recording.started_at.asc(), Recording.id.asc())
                ).all()
            else:
                rows = db.execute(
                    select(Recording.id, Recording.started_at)
                    .where(Recording.source_id == source_id)
                    .order_by(Recording.started_at.asc(), Recording.id.asc())
                ).all()
        same_day = [(int(row_id), value) for row_id, value in rows if cloud_day_key(value) == day_key]
        if recording_id is not None:
            for index, (row_id, _value) in enumerate(same_day, start=1):
                if row_id == int(recording_id):
                    return index, display_name
        return len(same_day) + 1, display_name

    def _normalize_generated_recording_filename(self, rec: Recording, path: Path) -> Path:
        """Upgrade pending legacy batch names before they become public cloud names."""
        if "_batch" not in rec.filename or "_complete" not in rec.filename or not path.is_file():
            return path
        sequence, display_name = self._recording_day_sequence(rec.source_id, rec.started_at, rec.id)
        expected = public_recording_filename(display_name, rec.started_at, sequence, path.suffix)
        target = path.with_name(expected)
        if target != path:
            if target.exists():
                raise RuntimeError(f"Impossibile normalizzare il nome: {target.name} esiste già")
            path.replace(target)
            with db_session() as db:
                current = db.get(Recording, rec.id)
                if current:
                    current.local_path = str(target)
                    current.filename = expected
            rec.local_path = str(target)
            rec.filename = expected
        return target

    async def _stitch_fragment_group(
        self,
        fragments: list[RecordingFragment],
        *,
        allow_transcode: bool = True,
    ) -> None:
        fragments = sorted(fragments, key=lambda item: (item.started_at, item.id))
        good = [item for item in fragments if fragment_usable_for_stitch(item)]
        if not good:
            raise RuntimeError("Nessun frammento integro nella sessione")
        first = good[0]
        started = min(item.started_at for item in good)
        paths = [Path(item.local_path) for item in good]
        total_bytes = sum(path.stat().st_size for path in paths)
        free = shutil.disk_usage(paths[0].parent).free
        if free < total_bytes + 256 * 1024 * 1024:
            raise RuntimeError("Spazio insufficiente per consolidare la sessione")
        suffixes = {path.suffix.lower() for path in paths}
        suffix = next(iter(suffixes)) if len(suffixes) == 1 else ".mp4"
        sequence, display_name = self._recording_day_sequence(first.source_id, started)
        output = paths[0].parent / public_recording_filename(display_name, started, sequence, suffix)
        # A stale file from a previously interrupted finalize may exist, but a
        # validated Recording row is what reserves an ordinal. Replace only the
        # unindexed stale path for the same chronological slot.
        output.unlink(missing_ok=True)
        await stitch_recording_parts(paths, output, allow_transcode=allow_transcode)
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
        finalized = max(item.finalized_at for item in good)
        with db_session() as db:
            existing = db.scalar(select(Recording).where(Recording.local_path == str(output)))
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
            fragment_ids = [int(fragment.id) for fragment in fragments]
            for fragment in db.scalars(select(RecordingFragment).where(
                RecordingFragment.id.in_(fragment_ids)
            )).all():
                db.delete(fragment)
        for path in paths:
            if path != output:
                path.unlink(missing_ok=True)
        for fragment in fragments:
            path = Path(fragment.local_path)
            if path not in paths:
                path.unlink(missing_ok=True)
        active = self.active.get(first.source_id)
        if not active or active.session_id != first.session_id:
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

    def _observe_live_state(
        self,
        db,
        source: Source,
        live: bool,
        observed_at: datetime,
        access_status: str = "live",
    ) -> None:
        open_session = db.scalar(
            select(LiveSession)
            .where(LiveSession.source_id == source.id, LiveSession.ended_at.is_(None))
            .order_by(LiveSession.started_at.desc(), LiveSession.id.desc())
        )
        if live:
            normalized_status = access_status if access_status in {"live", "private", "tipjar", "restricted"} else "live"
            if open_session and open_session.access_status != normalized_status:
                started_at = open_session.started_at
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                open_session.ended_at = max(observed_at, started_at.astimezone(timezone.utc))
                open_session.last_seen_at = observed_at
                open_session = None
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
                    access_status=normalized_status,
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
                    self._observe_live_state(db, current, bool(result.live), checked_at, result.status)
                    recording_allowed = bool(current.enabled and current.consent_confirmed and not current.archived)
            cfg = runtime()
            if not result.live or not getattr(result, "recordable", True) or self._stopping or source.id in self.active or cfg.recording_paused or not recording_allowed:
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
                continuation = self._session_continuations.get(source.id)
                if continuation and continuation[0] == session.session_id:
                    self._session_continuations.pop(source.id, None)
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
                    if session.transport_guard and not session.restart_requested:
                        reason = stream_transport_fault(text)
                        if reason:
                            session.restart_requested = True
                            session.restart_reason = reason
                    if not any(noise in text.lower() for noise in NONFATAL_FFMPEG_NOISE):
                        tail.append(text)
                        tail = tail[-10:]
        finally:
            if tail and session.process.returncode not in (0, None):
                self.last_errors[f"ffmpeg:{session.source_id}"] = " | ".join(tail)[-1800:]

    async def _watch_session(self, session: RecorderSession) -> None:
        processed: set[Path] = set()
        stderr_task = asyncio.create_task(self._drain_stderr(session))
        slot_released = False
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
                files = capture_output_files(session)
                if files and files[-1].stat().st_size >= session.safe_stop_bytes:
                    session.rollover_requested = True
                    await stop_recorder(session)
                    continue
                for path in files[:-1]:
                    if path not in processed and path.stat().st_size > 0:
                        if await self._finalize_segment(session, path):
                            processed.add(path)
            await session.process.wait()
            files = capture_output_files(session)
            # FFmpeg may satisfy -fs and exit between one-second size samples.
            # Treat a clean exit very close to the cap as a rollover as well.
            if session.process.returncode == 0 and any(
                path.stat().st_size >= int(session.safe_stop_bytes * 0.98)
                for path in files
            ):
                session.rollover_requested = True

            # Media validation can scan gigabytes. Release the recorder slot
            # first so a live source resumes while its immutable part is checked.
            if self.active.get(session.source_id) is session:
                self.active.pop(session.source_id, None)
                current_task = asyncio.current_task()
                if self.watch_tasks.get(session.source_id) is current_task:
                    self.watch_tasks.pop(session.source_id, None)
                if current_task is not None:
                    self.finalizing_tasks.add(current_task)
                    current_task.add_done_callback(self.finalizing_tasks.discard)
                if not self._stopping:
                    self._session_continuations[session.source_id] = (
                        session.session_id,
                        time.monotonic() + SESSION_STITCH_GAP_SECONDS,
                    )
                slot_released = True
                self.wake()
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
            if self.active.get(session.source_id) is session:
                self.active.pop(session.source_id, None)
            current_task = asyncio.current_task()
            if self.watch_tasks.get(session.source_id) is current_task:
                self.watch_tasks.pop(session.source_id, None)
            replacement = self.active.get(session.source_id)
            if replacement is None or replacement is session:
                with contextlib.suppress(OSError):
                    session.preview_path.unlink(missing_ok=True)
            if session.manifest_path is not None:
                with contextlib.suppress(OSError):
                    session.manifest_path.unlink(missing_ok=True)
            total_session_bytes = 0
            with contextlib.suppress(Exception):
                total_session_bytes = sum(
                    path.stat().st_size for path in capture_output_files(session)
                )
            size_rollover = session.rollover_requested or total_session_bytes >= session.safe_stop_bytes
            controlled_restart = session.restart_requested
            with db_session() as db:
                source = db.get(Source, session.source_id)
                if source:
                    now = utcnow()
                    replacement = self.active.get(session.source_id)
                    if replacement is not None and replacement is not session:
                        new_status = "recording"
                    elif source.archived:
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
                    self._observe_live_state(db, source, new_status in {"live", "recording"}, now)
                    if size_rollover or controlled_restart:
                        source.last_error = ""
            if not slot_released:
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
                .order_by(Recording.upload_priority.desc(), Recording.started_at.asc(), Recording.id.asc())
                .limit(1)
            )
            if not rec:
                candidates = list(db.scalars(
                    select(Recording)
                    .where(Recording.local_deleted.is_(False))
                    .where(Recording.integrity_status == "passed")
                    .where(Recording.upload_status.in_(["failed", "waiting_config"]))
                    .where(Recording.upload_attempts < cfg.max_upload_attempts)
                    .order_by(Recording.upload_priority.desc(), Recording.started_at.asc(), Recording.id.asc())
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
                path = self._normalize_generated_recording_filename(rec, path)
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
