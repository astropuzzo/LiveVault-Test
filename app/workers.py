from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .db import Recording, Source, db_session
from .recorder import RecorderSession, remux_to_mp4, start_recorder, stop_recorder
from .source_providers import probe
from .storage import disk_state
from .uploaders import provider_available, upload
from .utils import media_duration, safe_name, sha256_file, utcnow


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

    async def start(self) -> None:
        self._stopping = False
        self.started_at = utcnow()
        self._recover_interrupted_uploads()
        await self._recover_orphans()
        self.tasks = [
            asyncio.create_task(self._poll_loop(), name="source-poller"),
            asyncio.create_task(self._upload_loop(), name="uploader"),
            asyncio.create_task(self._cleanup_loop(), name="storage-cleanup"),
        ]

    async def stop(self) -> None:
        self._stopping = True
        self._wake_event.set()
        for source_id in list(self.active):
            await self.stop_source(source_id)
        for task in self.tasks:
            task.cancel()
        for task in list(self.watch_tasks.values()):
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def wake(self) -> None:
        self._wake_event.set()

    def clear_retry_backoff(self) -> None:
        self._retry_after.clear()

    def health(self) -> dict:
        return {
            "started": self.started_at is not None,
            "tasks": {task.get_name(): not task.done() for task in self.tasks},
            "active_recorders": len(self.active),
        }

    def _recover_interrupted_uploads(self) -> None:
        with db_session() as db:
            rows = list(db.scalars(select(Recording).where(Recording.upload_status == "uploading")).all())
            for rec in rows:
                rec.upload_status = "pending"
                rec.last_error = "Upload interrupted by restart; queued again"

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
                await asyncio.wait_for(asyncio.shield(task), timeout=8)

    def snapshot(self) -> dict:
        now = utcnow()
        active = []
        for s in self.active.values():
            current_size = 0
            with contextlib.suppress(Exception):
                current_size = sum(p.stat().st_size for p in s.directory.glob("*.mkv") if p.is_file())
            started = s.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            active.append(
                {
                    "source_id": s.source_id,
                    "source_name": s.source_name,
                    "session_id": s.session_id,
                    "started_at": s.started_at.isoformat(),
                    "elapsed_seconds": max(0, (now - started.astimezone(timezone.utc)).total_seconds()),
                    "local_bytes": current_size,
                }
            )
        uptime = 0
        if self.started_at:
            uptime = max(0, (now - self.started_at).total_seconds())
        return {
            "active": active,
            "errors": self.last_errors,
            "upload_current": self.upload_current,
            "uptime_seconds": uptime,
            "health": self.health(),
        }

    async def _recover_orphans(self) -> None:
        """Recover completed/partial media left behind by a hard restart."""
        candidates = sorted(settings.recordings_dir.rglob("*.mkv")) + sorted(settings.recordings_dir.rglob("*.mp4"))
        for original in candidates:
            if not original.is_file() or original.stat().st_size <= 0:
                continue
            with db_session() as db:
                if db.scalar(select(Recording).where(Recording.local_path == str(original))):
                    continue
            path = original
            if path.suffix.lower() == ".mkv":
                try:
                    path = await remux_to_mp4(path)
                except Exception as exc:
                    self.last_errors["recovery-remux"] = str(exc)[-1000:]
            with db_session() as db:
                if db.scalar(select(Recording).where(Recording.local_path == str(path))):
                    continue
                sources = list(db.scalars(select(Source)).all())
            source_folder = path.parent.parent.name if path.parent.parent else "recovered"
            source = next((s for s in sources if safe_name(s.name) == source_folder), None)
            source_id = source.id if source else 0
            source_name = source.name if source else source_folder
            duration = await asyncio.to_thread(media_duration, path)
            digest = await asyncio.to_thread(sha256_file, path)
            finalized = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            started = finalized - timedelta(seconds=duration or 0)
            with db_session() as db:
                if db.scalar(select(Recording).where(Recording.local_path == str(path))):
                    continue
                db.add(
                    Recording(
                        source_id=source_id,
                        source_name=source_name,
                        session_id=path.parent.name,
                        local_path=str(path),
                        filename=path.name,
                        started_at=started,
                        finalized_at=finalized,
                        duration_seconds=duration,
                        size_bytes=path.stat().st_size,
                        sha256=digest,
                        upload_status="pending",
                    )
                )

    async def _check_source(self, source: Source, semaphore: asyncio.Semaphore) -> None:
        if self._stopping or source.id in self.active:
            return
        state = disk_state()
        if state.pressure == "critical":
            self.last_errors["storage"] = f"Critical free space: {state.free_gb:.2f} GB; new recordings paused"
            return
        async with semaphore:
            result = await probe(source.platform, source.slug, source.quality)
            with db_session() as db:
                current = db.get(Source, source.id)
                if current:
                    current.last_status = result.status
                    current.last_checked_at = utcnow()
                    if result.live:
                        current.last_live_at = utcnow()
            if not result.live or self._stopping or source.id in self.active:
                return
            try:
                session = await start_recorder(source)
                self.active[source.id] = session
                for key in (f"source:{source.id}", f"ffmpeg:{source.id}", f"watch:{source.id}"):
                    self.last_errors.pop(key, None)
                task = asyncio.create_task(self._watch_session(session), name=f"record-{source.id}")
                self.watch_tasks[source.id] = task
            except Exception as exc:
                self.last_errors[f"source:{source.id}"] = f"Recorder start failed: {exc}"

    async def _poll_loop(self) -> None:
        while not self._stopping:
            try:
                with db_session() as db:
                    sources = list(
                        db.scalars(select(Source).where(Source.enabled.is_(True), Source.consent_confirmed.is_(True))).all()
                    )
                if disk_state().pressure == "ok":
                    self.last_errors.pop("storage", None)
                candidates = [s for s in sources if s.id not in self.active]
                if candidates:
                    semaphore = asyncio.Semaphore(max(1, settings.max_probe_concurrency))
                    await asyncio.gather(*(self._check_source(source, semaphore) for source in candidates))
                await self._sleep_or_wake(max(15, settings.poll_seconds))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_errors["poller"] = str(exc)[-1000:]
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
                    tail = tail[-8:]
        finally:
            if tail and session.process.returncode not in (0, None):
                self.last_errors[f"ffmpeg:{session.source_id}"] = " | ".join(tail)[-1500:]

    async def _watch_session(self, session: RecorderSession) -> None:
        processed: set[Path] = set()
        stderr_task = asyncio.create_task(self._drain_stderr(session))
        try:
            while session.process.returncode is None:
                await asyncio.sleep(5)
                files = sorted(session.directory.glob("*.mkv"), key=lambda p: p.stat().st_mtime)
                # Newest file can still be written by FFmpeg. Previous files are safe to finalize/upload.
                for path in files[:-1]:
                    if path not in processed and path.stat().st_size > 0:
                        await self._finalize_segment(session, path)
                        processed.add(path)
            await session.process.wait()
            files = sorted(session.directory.glob("*.mkv"), key=lambda p: p.stat().st_mtime)
            for path in files:
                if path not in processed and path.exists() and path.stat().st_size > 0:
                    await self._finalize_segment(session, path)
                    processed.add(path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_errors[f"watch:{session.source_id}"] = str(exc)[-1000:]
        finally:
            if session.process.returncode is None:
                await stop_recorder(session)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(stderr_task, timeout=2)
            self.active.pop(session.source_id, None)
            self.watch_tasks.pop(session.source_id, None)
            with db_session() as db:
                source = db.get(Source, session.source_id)
                if source:
                    source.last_status = "offline"
                    source.last_checked_at = utcnow()
            self.wake()

    async def _finalize_segment(self, session: RecorderSession, path: Path) -> None:
        final_path = await remux_to_mp4(path)
        size = final_path.stat().st_size
        digest, duration = await asyncio.gather(
            asyncio.to_thread(sha256_file, final_path),
            asyncio.to_thread(media_duration, final_path),
        )
        try:
            part_index = int(final_path.stem.rsplit("part", 1)[1])
        except Exception:
            part_index = 0
        started = session.started_at + timedelta(seconds=part_index * settings.segment_minutes * 60)
        with db_session() as db:
            existing = db.scalar(select(Recording).where(Recording.local_path == str(final_path)))
            if existing:
                return
            db.add(
                Recording(
                    source_id=session.source_id,
                    source_name=session.source_name,
                    session_id=session.session_id,
                    local_path=str(final_path),
                    filename=final_path.name,
                    started_at=started,
                    finalized_at=utcnow(),
                    duration_seconds=duration,
                    size_bytes=size,
                    sha256=digest,
                    upload_status="pending",
                )
            )

    def _pending_recording(self) -> Recording | None:
        now = time.monotonic()
        with db_session() as db:
            # New segments always go first. A repeatedly failing old file must not block the whole queue.
            rec = db.scalar(
                select(Recording)
                .where(Recording.local_deleted.is_(False), Recording.upload_status == "pending")
                .order_by(Recording.finalized_at.asc())
                .limit(1)
            )
            if not rec:
                candidates = list(
                    db.scalars(
                        select(Recording)
                        .where(Recording.local_deleted.is_(False))
                        .where(Recording.upload_status.in_(["failed", "waiting_config"]))
                        .where(Recording.upload_attempts < settings.max_upload_attempts)
                        .order_by(Recording.finalized_at.asc())
                        .limit(100)
                    ).all()
                )
                rec = next((r for r in candidates if self._retry_after.get(r.id, 0) <= now), None)
            if rec:
                rec.upload_status = "uploading"
                rec.upload_attempts += 1
                db.flush()
                db.expunge(rec)
            return rec

    async def _upload_loop(self) -> None:
        while not self._stopping:
            rec: Recording | None = None
            try:
                rec = self._pending_recording()
                if not rec:
                    await asyncio.sleep(2)
                    continue
                path = Path(rec.local_path)
                self.upload_current = {
                    "recording_id": rec.id,
                    "filename": rec.filename,
                    "source_name": rec.source_name,
                    "size_bytes": rec.size_bytes,
                    "attempt": rec.upload_attempts,
                }
                if not path.exists():
                    with db_session() as db:
                        current = db.get(Recording, rec.id)
                        if current:
                            current.local_deleted = True
                            current.upload_status = "missing"
                            current.last_error = "Local file is missing before upload"
                    self._retry_after.pop(rec.id, None)
                    continue

                providers: list[str] = []
                for p in (settings.primary_uploader, settings.fallback_uploader):
                    if p not in providers and provider_available(p):
                        providers.append(p)
                if not providers:
                    with db_session() as db:
                        current = db.get(Recording, rec.id)
                        if current:
                            current.upload_status = "waiting_config"
                            current.upload_attempts = max(0, current.upload_attempts - 1)
                            current.last_error = "No usable uploader configured"
                    self._retry_after[rec.id] = time.monotonic() + 60
                    continue

                errors: list[str] = []
                result = None
                for provider in providers:
                    try:
                        self.upload_current["provider"] = provider
                        result = await asyncio.to_thread(upload, path, provider)
                        if result.verified:
                            break
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
                            current.last_error = ""
                    self._retry_after.pop(rec.id, None)
                    if settings.delete_after_upload:
                        path.unlink(missing_ok=True)
                        with db_session() as db:
                            current = db.get(Recording, rec.id)
                            if current:
                                current.local_deleted = True
                else:
                    delay = min(3600, max(30, settings.upload_retry_seconds) * (2 ** min(max(rec.upload_attempts - 1, 0), 4)))
                    self._retry_after[rec.id] = time.monotonic() + delay
                    with db_session() as db:
                        current = db.get(Recording, rec.id)
                        if current:
                            current.upload_status = "failed"
                            current.last_error = (" | ".join(errors)[-1400:] or "Upload verification failed") + f" · retry in ~{int(delay)}s"
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_errors["uploader"] = str(exc)[-1000:]
                if rec:
                    self._retry_after[rec.id] = time.monotonic() + 30
                    with contextlib.suppress(Exception):
                        with db_session() as db:
                            current = db.get(Recording, rec.id)
                            if current and current.upload_status == "uploading":
                                current.upload_status = "failed"
                                current.last_error = f"Unexpected uploader error: {exc}"[-1500:]
                await asyncio.sleep(5)
            finally:
                self.upload_current = None

    async def _cleanup_loop(self) -> None:
        while not self._stopping:
            try:
                state = disk_state()
                if state.pressure in {"warning", "critical"}:
                    with db_session() as db:
                        records = list(
                            db.scalars(
                                select(Recording)
                                .where(Recording.upload_status == "uploaded", Recording.local_deleted.is_(False))
                                .order_by(Recording.finalized_at.asc())
                            ).all()
                        )
                    for rec in records:
                        Path(rec.local_path).unlink(missing_ok=True)
                        with db_session() as db:
                            current = db.get(Recording, rec.id)
                            if current:
                                current.local_deleted = True
                        if disk_state().pressure == "ok":
                            break

                state = disk_state()
                if state.free_gb <= settings.emergency_free_gb and self.active:
                    self.last_errors["storage-emergency"] = (
                        f"Only {state.free_gb:.2f} GB free: active recorders are being stopped cleanly "
                        "to protect the filesystem; unuploaded files are preserved."
                    )
                    for source_id in list(self.active):
                        await self.stop_source(source_id)
                elif state.pressure == "ok":
                    self.last_errors.pop("storage", None)
                    self.last_errors.pop("storage-emergency", None)

                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_errors["cleanup"] = str(exc)[-1000:]
                await asyncio.sleep(30)


manager = WorkerManager()
