from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import shutil
import sys
import time
import types
from pathlib import Path
from typing import Any

from app.settings_store import runtime


# Compatibility facade over the existing worker implementation.  Keeping the
# mature recorder/upload loops in one place lets this package add processing
# controls and progress reporting without forking the whole worker module.
_LEGACY_PATH = Path(__file__).resolve().parents[1] / "workers.py"
_LEGACY_NAME = "app._workers_legacy"
_spec = importlib.util.spec_from_file_location(_LEGACY_NAME, _LEGACY_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise RuntimeError("Cannot load worker implementation")
_legacy = importlib.util.module_from_spec(_spec)
sys.modules[_LEGACY_NAME] = _legacy
_spec.loader.exec_module(_legacy)


def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_legacy)))


class _WorkerFacade(types.ModuleType):
    """Forward compatibility monkeypatches to legacy worker globals."""

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if hasattr(_legacy, name):
            setattr(_legacy, name, value)


sys.modules[__name__].__class__ = _WorkerFacade


def session_stitch_gap_seconds() -> int:
    """Current reconnect/join window, persisted in Settings."""
    try:
        minutes = int(runtime().session_stitch_gap_minutes)
    except (AttributeError, TypeError, ValueError):
        minutes = 20
    return max(60, min(120 * 60, minutes * 60))


def stitch_gap_open(last_at, now, gap_seconds: int | None = None) -> bool:
    """Dynamic version of the legacy helper; Settings changes apply immediately."""
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=_legacy.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_legacy.timezone.utc)
    delta = (now.astimezone(_legacy.timezone.utc) - last_at.astimezone(_legacy.timezone.utc)).total_seconds()
    seconds = session_stitch_gap_seconds() if gap_seconds is None else max(0, int(gap_seconds))
    return 0 <= delta <= seconds


def refresh_runtime_constants() -> None:
    """Refresh legacy direct constant users such as the recorder continuation TTL."""
    _legacy.SESSION_STITCH_GAP_SECONDS = session_stitch_gap_seconds()
    _legacy.stitch_gap_open = stitch_gap_open


# Legacy methods resolve these names from their defining module at runtime.
refresh_runtime_constants()


class WorkerManager(_legacy.WorkerManager):
    def __init__(self) -> None:
        super().__init__()
        self.processing_current: dict | None = None
        self._processing_clear_task: asyncio.Task | None = None

    def snapshot(self) -> dict:
        payload = super().snapshot()
        payload["processing_current"] = dict(self.processing_current) if self.processing_current else None
        return payload

    def refresh_runtime_constants(self) -> None:
        refresh_runtime_constants()

    def _set_processing(self, **values: Any) -> None:
        if self.processing_current is None:
            self.processing_current = {}
        self.processing_current.update(values)

    def _schedule_processing_clear(self, current_session: str, delay: float = 3.0) -> None:
        if self._processing_clear_task and not self._processing_clear_task.done():
            self._processing_clear_task.cancel()

        async def clear_later() -> None:
            try:
                await asyncio.sleep(delay)
                if self.processing_current and self.processing_current.get("session_id") == current_session:
                    self.processing_current = None
            except asyncio.CancelledError:
                return

        self._processing_clear_task = asyncio.create_task(clear_later(), name="processing-progress-clear")

    async def _finalize_closed_stitch_sessions(self, force_source_id: int | None = None) -> None:
        """Finalize fragments after the configured quiet window, or immediately on demand.

        The old implementation could finalize long sessions before the nominal
        reconnect window had elapsed.  That made the 20-minute rule misleading
        and could split a creator that reconnected within the advertised window.
        The configured window is now authoritative; only an explicit process-now
        action bypasses it.
        """
        now = _legacy.utcnow()
        with _legacy.db_session() as db:
            query = _legacy.select(_legacy.RecordingFragment).order_by(
                _legacy.RecordingFragment.source_id,
                _legacy.RecordingFragment.session_id,
                _legacy.RecordingFragment.started_at,
                _legacy.RecordingFragment.id,
            )
            if force_source_id is not None:
                query = query.where(_legacy.RecordingFragment.source_id == int(force_source_id))
            rows = list(db.scalars(query).all())
            for row in rows:
                db.expunge(row)

        groups: dict[tuple[int, str], list[Any]] = {}
        for row in rows:
            groups.setdefault((int(row.source_id), row.session_id), []).append(row)

        for (source_id, session_id), items in groups.items():
            if self._stopping:
                continue
            forced = force_source_id is not None and source_id == int(force_source_id)
            retry_key = (source_id, session_id)
            if forced:
                self._stitch_retry_after.pop(retry_key, None)
            elif self._stitch_retry_after.get(retry_key, 0) > time.monotonic():
                continue

            await self._revalidate_retryable_fragments(items)
            latest = max(item.finalized_at for item in items)
            if not forced and stitch_gap_open(latest, now):
                continue

            # Never consolidate the exact session that is still being written.
            active = self.active.get(source_id)
            if active and active.session_id == session_id:
                continue

            with _legacy.db_session() as db:
                current = list(db.scalars(
                    _legacy.select(_legacy.RecordingFragment)
                    .where(
                        _legacy.RecordingFragment.source_id == source_id,
                        _legacy.RecordingFragment.session_id == session_id,
                    )
                    .order_by(_legacy.RecordingFragment.started_at, _legacy.RecordingFragment.id)
                ).all())
                for row in current:
                    db.expunge(row)
            if not current:
                continue
            latest = max(item.finalized_at for item in current)
            if not forced and stitch_gap_open(latest, _legacy.utcnow()):
                continue

            try:
                await self._stitch_fragment_group(current, allow_transcode=True)
                self._stitch_retry_after.pop(retry_key, None)
                self.last_errors.pop(f"stitch:{source_id}:{session_id}", None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._stitch_retry_after[retry_key] = time.monotonic() + 300
                self.last_errors[f"stitch:{source_id}:{session_id}"] = str(exc)[-1400:]

    async def _stitch_fragment_group(
        self,
        fragments: list[Any],
        *,
        allow_transcode: bool = True,
    ) -> None:
        """Legacy stitch pipeline with observable, file-backed progress."""
        fragments = sorted(fragments, key=lambda item: (item.started_at, item.id))
        good = [item for item in fragments if _legacy.fragment_usable_for_stitch(item)]
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
        output = paths[0].parent / _legacy.public_recording_filename(display_name, started, sequence, suffix)
        output.unlink(missing_ok=True)

        session_key = str(first.session_id)
        self.processing_current = {
            "source_id": int(first.source_id),
            "source_name": str(first.source_name),
            "session_id": session_key,
            "stage": "Preparazione",
            "percent": 5.0,
            "processed_bytes": 0,
            "total_bytes": int(total_bytes),
            "parts": len(good),
            "started_at": _legacy.utcnow().isoformat(),
            "error": "",
        }
        self.wake()

        stop_monitor = asyncio.Event()

        async def monitor_output() -> None:
            while not stop_monitor.is_set():
                try:
                    size = output.stat().st_size if output.is_file() else 0
                except OSError:
                    size = 0
                ratio = min(1.0, size / total_bytes) if total_bytes else 0.0
                self._set_processing(
                    stage="Unione frammenti",
                    processed_bytes=int(size),
                    percent=round(max(8.0, min(70.0, 8.0 + ratio * 62.0)), 1),
                )
                self.wake()
                try:
                    await asyncio.wait_for(stop_monitor.wait(), timeout=0.75)
                except asyncio.TimeoutError:
                    pass

        monitor_task = asyncio.create_task(monitor_output(), name=f"stitch-progress-{first.source_id}")
        try:
            await _legacy.stitch_recording_parts(paths, output, allow_transcode=allow_transcode)
        finally:
            stop_monitor.set()
            with contextlib.suppress(Exception):
                await monitor_task

        self._set_processing(stage="Finalizzazione MP4", percent=74.0, processed_bytes=int(output.stat().st_size))
        self.wake()
        if output.suffix.lower() == ".mp4":
            await self._prepare_mp4(output)

        self._set_processing(stage="Verifica audio/video", percent=84.0)
        self.wake()
        integrity = await asyncio.to_thread(_legacy.verify_media, output, runtime().integrity_mode)
        if not integrity.ok:
            output.unlink(missing_ok=True)
            raise RuntimeError(f"Sessione consolidata non valida: {integrity.error}")

        self._set_processing(stage="Checksum", percent=91.0)
        self.wake()
        digest = await asyncio.to_thread(_legacy.sha256_file, output)

        thumb_path = ""
        if runtime().generate_thumbnails:
            self._set_processing(stage="Anteprima", percent=95.0)
            self.wake()
            candidate = _legacy.settings.data_dir / "thumbnails" / f"{digest[:24]}-sheet-v2.jpg"
            if await asyncio.to_thread(_legacy.generate_thumbnail, output, candidate, integrity.duration):
                thumb_path = str(candidate)

        self._set_processing(stage="Indicizzazione", percent=98.0)
        self.wake()
        finalized = max(item.finalized_at for item in good)
        with _legacy.db_session() as db:
            existing = db.scalar(_legacy.select(_legacy.Recording).where(_legacy.Recording.local_path == str(output)))
            if existing is None:
                db.add(_legacy.Recording(
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
                    integrity_checked_at=_legacy.utcnow(),
                    container_format=output.suffix.lower().lstrip("."),
                    has_video=integrity.has_video,
                    has_audio=integrity.has_audio,
                    video_codec=integrity.codec("video"),
                    audio_codec=integrity.codec("audio"),
                ))
            fragment_ids = [int(fragment.id) for fragment in fragments]
            for fragment in db.scalars(_legacy.select(_legacy.RecordingFragment).where(
                _legacy.RecordingFragment.id.in_(fragment_ids)
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
            (output.parent / _legacy.STITCH_MARKER_NAME).unlink(missing_ok=True)
            for manifest in output.parent.glob(".livevault-synced-master-*.m3u8"):
                manifest.unlink(missing_ok=True)

        self._set_processing(
            stage="Completato",
            percent=100.0,
            processed_bytes=int(output.stat().st_size),
            completed_at=_legacy.utcnow().isoformat(),
        )
        self.wake()
        self._schedule_processing_clear(session_key)

    async def process_source_now(self, source_id: int) -> dict[str, Any]:
        """Finalize this source's immutable fragments now and prioritize its uploads."""
        source_id = int(source_id)
        if self._leader_file is None:
            return {"ok": False, "reason": "standby"}
        active = self.active.get(source_id)
        if active is not None:
            return {"ok": False, "reason": "recording", "message": "La registrazione è ancora attiva"}

        with _legacy.db_session() as db:
            fragments_before = int(db.scalar(
                _legacy.select(_legacy.func.count(_legacy.RecordingFragment.id)).where(
                    _legacy.RecordingFragment.source_id == source_id
                )
            ) or 0)

        self._stitch_retry_after = {
            key: value for key, value in self._stitch_retry_after.items() if key[0] != source_id
        }
        async with self._recovery_lock:
            await self._finalize_closed_stitch_sessions(force_source_id=source_id)

        prioritized_ids: list[int] = []
        with _legacy.db_session() as db:
            rows = list(db.scalars(
                _legacy.select(_legacy.Recording).where(
                    _legacy.Recording.source_id == source_id,
                    _legacy.Recording.local_deleted.is_(False),
                    _legacy.Recording.integrity_status == "passed",
                    _legacy.Recording.upload_status.in_(["pending", "failed", "waiting_config"]),
                )
            ).all())
            for rec in rows:
                if rec.upload_status in {"failed", "waiting_config"}:
                    rec.upload_status = "pending"
                rec.upload_priority = max(int(rec.upload_priority or 0), 10_000)
                rec.last_error = "" if rec.upload_status == "pending" else rec.last_error
                prioritized_ids.append(int(rec.id))
            fragments_after = int(db.scalar(
                _legacy.select(_legacy.func.count(_legacy.RecordingFragment.id)).where(
                    _legacy.RecordingFragment.source_id == source_id
                )
            ) or 0)

        for recording_id in prioritized_ids:
            self._retry_after.pop(recording_id, None)
        self.wake()
        return {
            "ok": True,
            "source_id": source_id,
            "fragments_before": fragments_before,
            "fragments_after": fragments_after,
            "finalized": max(0, fragments_before - fragments_after),
            "uploads_prioritized": len(prioritized_ids),
            "upload_paused": bool(runtime().upload_paused),
        }


manager = WorkerManager()


__all__ = [
    "WorkerManager",
    "manager",
    "stitch_gap_open",
    "session_stitch_gap_seconds",
    "refresh_runtime_constants",
]
