from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from .config import settings
from .db import Recording, Source, db_session
from .recorder import RecorderSession, start_recorder, stop_recorder
from .settings_store import runtime
from .source_providers import probe
from .storage import disk_state
from .uploaders import create_gofile_folder, provider_available, upload
from .utils import generate_thumbnail, human_bytes, safe_name, sha256_file, utcnow, verify_media


class WorkerManager:
    def __init__(self) -> None:
        self.active: dict[int, RecorderSession] = {}
        self.watch_tasks: dict[int, asyncio.Task] = {}
        self.tasks: list[asyncio.Task] = []
        self._stopping = False
        self._wake_event = asyncio.Event()
        self.last_errors: dict[str, str] = {}
        self.started_at: datetime | None = None
        self.upload_current: dict | None = None
        self._retry_after: dict[int, float] = {}
        self.backfill_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._stopping = False
        self.started_at = utcnow()
        self._recover_interrupted_uploads()
        await self._recover_orphans()
        self.tasks = [
            asyncio.create_task(self._poll_loop(), name="source-poller"),
            asyncio.create_task(self._upload_loop(), name="uploader"),
            asyncio.create_task(self._cleanup_loop(), name="storage-guard"),
        ]
        self.backfill_task = asyncio.create_task(self._backfill_thumbnails(), name="thumbnail-backfill")

    async def stop(self) -> None:
        self._stopping = True
        self._wake_event.set()
        await self.stop_all_recordings()
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

    def wake(self) -> None:
        self._wake_event.set()

    def clear_retry_backoff(self) -> None:
        self._retry_after.clear()

    def health(self) -> dict:
        return {
            "started": self.started_at is not None,
            "tasks": {task.get_name(): not task.done() for task in self.tasks},
            "active_recorders": len(self.active),
            "thumbnail_backfill": "done" if self.backfill_task and self.backfill_task.done() else "running" if self.backfill_task else "idle",
        }

    def _recover_interrupted_uploads(self) -> None:
        with db_session() as db:
            rows = list(db.scalars(select(Recording).where(Recording.upload_status == "uploading")).all())
            for rec in rows:
                rec.upload_status = "pending"
                rec.last_error = "Upload interrotto da un riavvio; rimesso in coda"

    async def _sleep_or_wake(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            self._wake_event.clear()

    async def stop_source(self, source_id: int) -> None:
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
        with db_session() as db:
            total = int(db.scalar(
                select(func.coalesce(func.sum(Recording.size_bytes), 0)).where(Recording.local_deleted.is_(False))
            ) or 0)
        known_paths: set[str] = set()
        with db_session() as db:
            known_paths = {str(x) for x in db.scalars(select(Recording.local_path).where(Recording.local_deleted.is_(False))).all()}
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

    async def _backfill_thumbnails(self) -> None:
        """Generate persistent previews for local recordings created by older releases."""
        try:
            if not runtime().generate_thumbnails:
                return
            with db_session() as db:
                rows = list(db.scalars(
                    select(Recording)
                    .where(Recording.local_deleted.is_(False), Recording.thumbnail_path == "")
                    .order_by(Recording.finalized_at.desc())
                    .limit(1000)
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
                candidate = settings.data_dir / "thumbnails" / f"{digest[:24]}.jpg"
                ok = await asyncio.to_thread(generate_thumbnail, path, candidate, rec.duration_seconds)
                if ok:
                    with db_session() as db:
                        current = db.get(Recording, rec.id)
                        if current and not current.thumbnail_path:
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
            if not path.is_file() or path.stat().st_size <= 0:
                continue
            with db_session() as db:
                if db.scalar(select(Recording).where(Recording.local_path == str(path))):
                    continue
                sources = list(db.scalars(select(Source)).all())
            source_folder = path.parent.parent.name if path.parent.parent else "recovered"
            source = next((s for s in sources if safe_name(s.name) == source_folder), None)
            source_id = source.id if source else 0
            source_name = source.name if source else source_folder
            await self._index_file(
                source_id=source_id,
                source_name=source_name,
                session_id=path.parent.name,
                path=path,
                started_at=None,
            )

    async def _wait_until_stable(self, path: Path, timeout: float = 12.0) -> None:
        """Do not probe a segment while FFmpeg is still flushing its trailer."""
        deadline = time.monotonic() + timeout
        previous: tuple[int, int] | None = None
        while time.monotonic() < deadline:
            try:
                stat = path.stat()
            except FileNotFoundError:
                return
            current = (stat.st_size, stat.st_mtime_ns)
            if stat.st_size > 0 and current == previous and time.time() - stat.st_mtime >= 1.0:
                return
            previous = current
            await asyncio.sleep(1)

    async def _index_file(self, *, source_id: int, source_name: str, session_id: str, path: Path, started_at: datetime | None) -> None:
        cfg = runtime()
        await self._wait_until_stable(path)
        integrity = await asyncio.to_thread(verify_media, path, cfg.integrity_mode)
        digest = await asyncio.to_thread(sha256_file, path)
        finalized = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if integrity.duration:
            start = finalized - timedelta(seconds=integrity.duration)
        elif started_at:
            start = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
            start = start.astimezone(timezone.utc)
        else:
            start = finalized
        thumb_path = ""
        if cfg.generate_thumbnails and integrity.ok:
            candidate = settings.data_dir / "thumbnails" / f"{digest[:24]}.jpg"
            ok = await asyncio.to_thread(generate_thumbnail, path, candidate, integrity.duration)
            if ok:
                thumb_path = str(candidate)
        with db_session() as db:
            if db.scalar(select(Recording).where(Recording.local_path == str(path))):
                return
            db.add(Recording(
                source_id=source_id,
                source_name=source_name,
                session_id=session_id,
                local_path=str(path),
                filename=path.name,
                started_at=start,
                finalized_at=finalized,
                duration_seconds=integrity.duration,
                size_bytes=path.stat().st_size,
                sha256=digest,
                upload_status="pending" if integrity.ok else "integrity_failed",
                thumbnail_path=thumb_path,
                integrity_status="passed" if integrity.ok else "failed",
                integrity_error=integrity.error,
                integrity_checked_at=utcnow(),
                container_format=path.suffix.lower().lstrip("."),
                has_video=integrity.has_video,
                has_audio=integrity.has_audio,
                video_codec=integrity.codec("video"),
                audio_codec=integrity.codec("audio"),
            ))

    async def _check_source(self, source: Source, semaphore: asyncio.Semaphore) -> None:
        cfg = runtime()
        if self._stopping or cfg.recording_paused or source.id in self.active:
            return
        state = disk_state()
        if state.free_gb <= cfg.critical_free_gb:
            self.last_errors["storage"] = f"Spazio critico: {state.free_gb:.2f} GB; nuove registrazioni sospese"
            return
        if cfg.buffer_max_gb > 0 and self.local_buffer_bytes() >= cfg.buffer_max_gb * 1024**3:
            self.last_errors["buffer"] = f"Buffer locale al limite ({human_bytes(self.local_buffer_bytes())} / {cfg.buffer_max_gb:.1f} GB)"
            return
        async with semaphore:
            result = await probe(source.platform, source.slug, source.quality)
            checked_at = utcnow()
            with db_session() as db:
                current = db.get(Source, source.id)
                if current:
                    if current.last_status != result.status or current.status_changed_at is None:
                        current.status_changed_at = checked_at
                    current.last_status = result.status
                    current.last_checked_at = checked_at
                    current.last_error = result.error if result.status == "error" else ""
                    if result.last_broadcast is not None:
                        current.last_live_at = result.last_broadcast
            if not result.live or self._stopping or source.id in self.active or runtime().recording_paused:
                return
            try:
                session = await start_recorder(source)
                self.active[source.id] = session
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
                if cfg.recording_paused:
                    await self._sleep_or_wake(3)
                    continue
                with db_session() as db:
                    sources = list(db.scalars(select(Source).where(Source.enabled.is_(True), Source.consent_confirmed.is_(True))).all())
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
        finally:
            if tail and session.process.returncode not in (0, None):
                self.last_errors[f"ffmpeg:{session.source_id}"] = " | ".join(tail)[-1800:]

    async def _watch_session(self, session: RecorderSession) -> None:
        processed: set[Path] = set()
        stderr_task = asyncio.create_task(self._drain_stderr(session))
        try:
            while session.process.returncode is None:
                await asyncio.sleep(1)
                files = sorted(session.directory.glob(f"*{session.extension}"), key=lambda p: p.stat().st_mtime)
                if files and files[-1].stat().st_size >= session.safe_stop_bytes:
                    session.rollover_requested = True
                    await stop_recorder(session)
                    continue
                for path in files[:-1]:
                    if path not in processed and path.stat().st_size > 0:
                        await self._finalize_segment(session, path)
                        processed.add(path)
            await session.process.wait()
            files = sorted(session.directory.glob(f"*{session.extension}"), key=lambda p: p.stat().st_mtime)
            for path in files:
                if path not in processed and path.exists() and path.stat().st_size > 0:
                    await self._finalize_segment(session, path)
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
            total_session_bytes = 0
            with contextlib.suppress(Exception):
                total_session_bytes = sum(
                    path.stat().st_size for path in session.directory.glob(f"*{session.extension}") if path.is_file()
                )
            size_rollover = session.rollover_requested or total_session_bytes >= session.safe_stop_bytes
            with db_session() as db:
                source = db.get(Source, session.source_id)
                if source:
                    now = utcnow()
                    new_status = "live" if size_rollover else "offline"
                    if source.last_status != new_status or source.status_changed_at is None:
                        source.status_changed_at = now
                    source.last_status = new_status
                    source.last_checked_at = now
                    if size_rollover:
                        source.last_error = ""
            self.wake()

    async def _finalize_segment(self, session: RecorderSession, path: Path) -> None:
        try:
            part_index = int(path.stem.rsplit("part", 1)[1])
        except Exception:
            part_index = 0
        started = session.started_at + timedelta(seconds=part_index * runtime().segment_minutes * 60)
        await self._index_file(
            source_id=session.source_id,
            source_name=session.source_name,
            session_id=session.session_id,
            path=path,
            started_at=started,
        )
        self.wake()

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

    async def _gofile_folder_for(self, rec: Recording) -> tuple[str, str]:
        """Resolve or lazily create the stable Gofile folder for one source."""
        with db_session() as db:
            source = db.get(Source, rec.source_id)
            if not source or not source.organize_cloud:
                return "", ""
            existing_id = source.gofile_folder_id
            existing_url = source.gofile_folder_url
            source_name = source.name
        if existing_id:
            return existing_id, existing_url
        parent_id = runtime().gofile_folder_id
        folder_id, folder_url = await asyncio.to_thread(create_gofile_folder, source_name, parent_id)
        with db_session() as db:
            source = db.get(Source, rec.source_id)
            if source:
                if not source.gofile_folder_id:
                    source.gofile_folder_id = folder_id
                if folder_url and not source.gofile_folder_url:
                    source.gofile_folder_url = folder_url
                folder_id = source.gofile_folder_id
                folder_url = source.gofile_folder_url
        return folder_id, folder_url

    async def _verify_before_upload(self, rec: Recording, path: Path) -> bool:
        cfg = runtime()
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
        digest = await asyncio.to_thread(sha256_file, path)
        if rec.sha256 and digest != rec.sha256:
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
                            if self.upload_current and self.upload_current.get("recording_id") == rec.id:
                                self.upload_current["sent_bytes"] = sent
                                self.upload_current["percent"] = round((sent / total * 100) if total else 0, 1)

                        gofile_folder_id = ""
                        gofile_folder_url = ""
                        if provider == "gofile":
                            gofile_folder_id, gofile_folder_url = await self._gofile_folder_for(rec)
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
                            current.uploaded_at = utcnow()
                            current.last_error = ""
                    if result.provider == "gofile":
                        with db_session() as db:
                            source = db.get(Source, rec.source_id)
                            if source and source.organize_cloud and gofile_folder_url and not source.gofile_folder_url:
                                source.gofile_folder_url = gofile_folder_url
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
