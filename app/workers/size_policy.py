from __future__ import annotations

import asyncio
import contextlib
import math
import shutil
import time
import types
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

import app.workers as workers_module
from app.recorder import safe_output_limit_bytes
from app.settings_store import runtime


_legacy = workers_module._legacy
_ELIGIBLE_OVERSIZE_STATUSES = {"pending", "failed", "waiting_config", "integrity_failed", "converting"}


def configured_max_bytes() -> int:
    """Hard user-facing per-file limit from Settings."""
    try:
        value = float(runtime().segment_max_gb)
    except (AttributeError, TypeError, ValueError):
        value = 2.0
    value = max(0.25, min(2.0, value))
    return max(1, int(value * 1024**3))


def configured_stitch_target_bytes() -> int:
    """Aim below the hard limit so mux/keyframe overhead cannot cross it."""
    try:
        value = float(runtime().segment_max_gb)
    except (AttributeError, TypeError, ValueError):
        value = 2.0
    value = max(0.25, min(2.0, value))
    return min(configured_max_bytes(), safe_output_limit_bytes(value))


def _fragment_bytes(fragment: Any) -> int:
    try:
        return max(0, int(Path(fragment.local_path).stat().st_size))
    except (OSError, TypeError, ValueError):
        return max(0, int(getattr(fragment, "size_bytes", 0) or 0))


def bounded_fragment_batch(
    fragments: list[Any],
    *,
    target_bytes: int | None = None,
    maximum_bytes: int | None = None,
) -> list[Any]:
    """Return the oldest stitchable prefix that fits one physical recording.

    The reconnect window defines a logical session, not a single giant file.
    Fragments stay chronological and are consumed until the configured target
    is reached; the next fragment is left for the following output.
    """
    maximum = max(1, int(maximum_bytes or configured_max_bytes()))
    target = min(maximum, max(1, int(target_bytes or configured_stitch_target_bytes())))
    usable = [
        item
        for item in sorted(fragments, key=lambda row: (row.started_at, row.id))
        if _legacy.fragment_usable_for_stitch(item)
    ]
    selected: list[Any] = []
    total = 0
    for item in usable:
        size = _fragment_bytes(item)
        if size <= 0:
            continue
        if size > maximum:
            if not selected:
                raise RuntimeError(
                    f"Frammento singolo oltre il limite ({size / 1024**3:.2f} GB > "
                    f"{maximum / 1024**3:.2f} GB): {Path(item.local_path).name}"
                )
            break
        if selected and total + size > target:
            break
        selected.append(item)
        total += size
        if total >= target:
            break
    return selected


def _fragment_count(source_id: int | None = None) -> int:
    with _legacy.db_session() as db:
        query = _legacy.select(_legacy.func.count(_legacy.RecordingFragment.id))
        if source_id is not None:
            query = query.where(_legacy.RecordingFragment.source_id == int(source_id))
        return int(db.scalar(query) or 0)


async def _run_segment_copy(source: Path, staging: Path, segment_seconds: float) -> list[Path]:
    """Losslessly split one already-finalized recording on media keyframes."""
    staging.mkdir(parents=True, exist_ok=True)
    for child in staging.iterdir():
        if child.is_file():
            child.unlink(missing_ok=True)
    suffix = source.suffix.lower()
    pattern = staging / f".chunk%03d{suffix}"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-fflags", "+genpts+discardcorrupt",
        "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0", "-dn", "-ignore_unknown",
        "-c", "copy",
        "-max_interleave_delta", "1000000",
        "-avoid_negative_ts", "make_zero",
        "-f", "segment",
        "-segment_time", f"{max(2.0, float(segment_seconds)):.3f}",
        "-segment_start_number", "1",
        "-reset_timestamps", "1",
    ]
    if suffix == ".mp4":
        command += [
            "-segment_format", "mp4",
            "-segment_format_options", "movflags=+faststart",
        ]
    else:
        command += ["-segment_format", "matroska"]
    command.append(str(pattern))
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    timeout = max(300, min(3600, int(source.stat().st_size / (4 * 1024**2)) + 120))
    try:
        _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=3)
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        raise
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise RuntimeError("Suddivisione file grande scaduta") from exc
    if process.returncode != 0:
        detail = (stderr or b"").decode(errors="replace")[-1600:]
        raise RuntimeError(detail or "FFmpeg non ha suddiviso il file grande")
    outputs = sorted(path for path in staging.iterdir() if path.is_file() and path.stat().st_size > 0)
    if not outputs:
        raise RuntimeError("Suddivisione file grande non ha prodotto parti")
    return outputs


async def _split_file_bounded(source: Path, maximum_bytes: int) -> tuple[Path, list[Path]]:
    quick = await asyncio.to_thread(_legacy.verify_media, source, "quick")
    if not quick.ok or not quick.duration or quick.duration <= 0:
        raise RuntimeError(f"File grande non segmentabile: {quick.error or 'durata non disponibile'}")
    size = source.stat().st_size
    free = shutil.disk_usage(source.parent).free
    if free < size + 256 * 1024 * 1024:
        raise RuntimeError("Spazio insufficiente per suddividere il file grande")

    # Aim at 90% of the hard cap because VBR/keyframe placement can make a
    # time-based segment larger than the average estimate. Retry shorter until
    # every produced chunk is physically below the configured maximum.
    target = max(64 * 1024**2, int(maximum_bytes * 0.90))
    estimated_parts = max(2, math.ceil(size / target))
    segment_seconds = max(2.0, float(quick.duration) / estimated_parts * 0.90)
    staging = source.parent / f".split-{source.stem}-{int(time.time() * 1000)}"
    try:
        for _attempt in range(7):
            outputs = await _run_segment_copy(source, staging, segment_seconds)
            if max(path.stat().st_size for path in outputs) <= maximum_bytes:
                return staging, outputs
            segment_seconds = max(2.0, segment_seconds * 0.62)
        raise RuntimeError("Impossibile rispettare il limite massimo per singolo file")
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _oversized_recording_candidate(manager: Any) -> Any | None:
    maximum = configured_max_bytes()
    with _legacy.db_session() as db:
        rows = list(db.scalars(
            _legacy.select(_legacy.Recording)
            .where(
                _legacy.Recording.local_deleted.is_(False),
                _legacy.Recording.upload_status.in_(tuple(_ELIGIBLE_OVERSIZE_STATUSES)),
            )
            .order_by(_legacy.Recording.started_at.asc(), _legacy.Recording.id.asc())
            .limit(200)
        ).all())
        for row in rows:
            db.expunge(row)
    for row in rows:
        if row.remote_url or row.uploaded_at is not None:
            continue
        path = Path(row.local_path)
        try:
            if path.is_file() and path.stat().st_size > maximum:
                return row
        except OSError:
            continue
    return None


def _apply_split_metadata(row: Any, info: dict[str, Any], started_at: Any, upload_priority: int) -> None:
    duration = float(info["integrity"].duration or 0)
    row.local_path = str(info["path"])
    row.filename = info["path"].name
    row.started_at = started_at
    row.finalized_at = started_at + timedelta(seconds=duration)
    row.duration_seconds = duration
    row.size_bytes = info["path"].stat().st_size
    row.sha256 = info["sha256"]
    row.upload_status = "pending"
    row.upload_provider = ""
    row.remote_id = ""
    row.remote_url = ""
    row.cloud_day_key = ""
    row.remote_parent_id = ""
    row.remote_parent_url = ""
    row.upload_attempts = 0
    row.last_error = ""
    row.local_deleted = False
    row.thumbnail_path = info["thumbnail"]
    row.integrity_status = "passed"
    row.integrity_error = ""
    row.integrity_checked_at = _legacy.utcnow()
    row.container_format = info["path"].suffix.lower().lstrip(".")
    row.upload_priority = int(upload_priority or 0)
    row.uploaded_at = None
    row.has_video = info["integrity"].has_video
    row.has_audio = info["integrity"].has_audio
    row.video_codec = info["integrity"].codec("video")
    row.audio_codec = info["integrity"].codec("audio")


async def _split_oversized_recording(manager: Any, recording: Any) -> bool:
    """Repair a legacy >limit Recording without ever destroying the original first."""
    path = Path(recording.local_path)
    maximum = configured_max_bytes()
    previous_status = str(recording.upload_status or "integrity_failed")
    if recording.source_id in manager.active:
        manager._retry_after[int(recording.id)] = time.monotonic() + 120
        return False

    with _legacy.db_session() as db:
        current = db.get(_legacy.Recording, int(recording.id))
        if current is None or current.upload_status not in _ELIGIBLE_OVERSIZE_STATUSES:
            return False
        if current.remote_url or current.uploaded_at is not None:
            return False
        current.upload_status = "converting"
        current.last_error = ""

    session_key = str(recording.session_id)
    manager.processing_current = {
        "source_id": int(recording.source_id),
        "source_name": str(recording.source_name),
        "session_id": session_key,
        "stage": "Suddivisione file grande",
        "percent": 5.0,
        "processed_bytes": 0,
        "total_bytes": int(path.stat().st_size),
        "parts": 0,
        "started_at": _legacy.utcnow().isoformat(),
        "error": "",
    }
    manager.wake()

    staging: Path | None = None
    finals: list[Path] = []
    generated_thumbnails: list[Path] = []
    backup = path.with_name(f".{path.name}.oversized-{recording.id}.bak")
    committed = False
    old_thumbnail = Path(recording.thumbnail_path) if str(recording.thumbnail_path or "").strip() else None
    try:
        if backup.exists():
            raise RuntimeError(f"Backup oversized già presente: {backup.name}")
        staging, chunks = await _split_file_bounded(path, maximum)
        manager._set_processing(stage="Verifica parti", percent=42.0, parts=len(chunks))
        manager.wake()

        verified: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks, start=1):
            if chunk.suffix.lower() == ".mp4" and not _legacy.mp4_is_streaming_ready(chunk):
                await manager._prepare_mp4(chunk)
            if chunk.stat().st_size > maximum:
                raise RuntimeError(
                    f"Parte {index} oltre il limite dopo finalizzazione: "
                    f"{chunk.stat().st_size / 1024**3:.2f} GB"
                )
            integrity = await asyncio.to_thread(_legacy.verify_media, chunk, runtime().integrity_mode)
            if not integrity.ok:
                raise RuntimeError(f"Parte {index} non valida: {integrity.error}")
            digest = await asyncio.to_thread(_legacy.sha256_file, chunk)
            thumbnail = ""
            if runtime().generate_thumbnails:
                candidate = _legacy.settings.data_dir / "thumbnails" / f"{digest[:24]}-sheet-v2.jpg"
                if await asyncio.to_thread(_legacy.generate_thumbnail, chunk, candidate, integrity.duration):
                    thumbnail = str(candidate)
                    generated_thumbnails.append(candidate)
            verified.append({
                "staging": chunk,
                "integrity": integrity,
                "sha256": digest,
                "thumbnail": thumbnail,
            })
            manager._set_processing(
                stage="Verifica parti",
                processed_bytes=sum(item["staging"].stat().st_size for item in verified),
                percent=round(42.0 + index / len(chunks) * 43.0, 1),
            )
            manager.wake()

        finals = [path.with_name(f"{path.stem}_part{index:02d}{path.suffix}") for index in range(1, len(verified) + 1)]
        if any(final.exists() for final in finals):
            finals = [
                path.with_name(f"{path.stem}_split{recording.id}_part{index:02d}{path.suffix}")
                for index in range(1, len(verified) + 1)
            ]
        if any(final.exists() for final in finals):
            raise RuntimeError("Esistono già file di destinazione per la suddivisione")

        path.replace(backup)
        for info, final in zip(verified, finals):
            info["staging"].replace(final)
            info["path"] = final

        manager._set_processing(stage="Indicizzazione parti", percent=92.0)
        manager.wake()
        started = recording.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        priority = int(recording.upload_priority or 0)
        cumulative = 0.0
        with _legacy.db_session() as db:
            current = db.get(_legacy.Recording, int(recording.id))
            if current is None or current.upload_status != "converting":
                raise RuntimeError("La registrazione oversized ha cambiato stato durante la conversione")
            for index, info in enumerate(verified):
                part_started = started + timedelta(seconds=cumulative)
                if index == 0:
                    row = current
                else:
                    row = _legacy.Recording(
                        source_id=recording.source_id,
                        source_name=recording.source_name,
                        session_id=recording.session_id,
                        local_path="",
                        filename="",
                        started_at=part_started,
                    )
                    db.add(row)
                _apply_split_metadata(row, info, part_started, priority)
                cumulative += float(info["integrity"].duration or 0)
        committed = True

        backup.unlink(missing_ok=True)
        if old_thumbnail and old_thumbnail.is_file() and str(old_thumbnail) not in {item["thumbnail"] for item in verified}:
            old_thumbnail.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        manager._retry_after.pop(int(recording.id), None)
        manager.last_errors.pop(f"oversize:{recording.id}", None)
        manager._set_processing(
            stage="Completato",
            percent=100.0,
            processed_bytes=sum(final.stat().st_size for final in finals),
            parts=len(finals),
            completed_at=_legacy.utcnow().isoformat(),
        )
        manager.wake()
        manager._schedule_processing_clear(session_key)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if not committed:
            for final in finals:
                final.unlink(missing_ok=True)
            if backup.is_file() and not path.exists():
                backup.replace(path)
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            for thumbnail in generated_thumbnails:
                if old_thumbnail is None or thumbnail != old_thumbnail:
                    thumbnail.unlink(missing_ok=True)
            with _legacy.db_session() as db:
                current = db.get(_legacy.Recording, int(recording.id))
                if current is not None and current.upload_status == "converting":
                    current.upload_status = previous_status if previous_status != "converting" else "integrity_failed"
                    current.last_error = f"Suddivisione file grande fallita: {exc}"[-1500:]
        detail = f"Suddivisione file grande fallita: {exc}"[-1400:]
        manager.last_errors[f"oversize:{recording.id}"] = detail
        manager._retry_after[int(recording.id)] = time.monotonic() + 300
        if manager.processing_current and str(manager.processing_current.get("session_id") or "") == session_key:
            manager._set_processing(stage="Errore", error=detail, completed_at=_legacy.utcnow().isoformat())
            manager.wake()
            manager._schedule_processing_clear(session_key, delay=8.0)
        return False


def install_size_policy(manager: Any) -> None:
    """Install hard per-file size enforcement on the live singleton manager."""
    if getattr(manager, "_size_policy_installed", False):
        return
    manager._size_policy_installed = True

    original_stitch = manager._stitch_fragment_group
    original_finalize = manager._finalize_closed_stitch_sessions
    original_repair = manager._repair_local_mp4s

    async def bounded_stitch(self, fragments, *, allow_transcode: bool = True):
        batch = bounded_fragment_batch(list(fragments))
        if not batch:
            raise RuntimeError("Nessun frammento utilizzabile nel blocco di stitching")
        clear_task = getattr(self, "_processing_clear_task", None)
        if clear_task is not None and not clear_task.done():
            clear_task.cancel()
        return await original_stitch(batch, allow_transcode=allow_transcode)

    async def bounded_finalize(self, force_source_id: int | None = None):
        for _ in range(128):
            before = _fragment_count(force_source_id)
            await original_finalize(force_source_id=force_source_id)
            after = _fragment_count(force_source_id)
            if after <= 0 or after >= before:
                break
            await asyncio.sleep(0)

    async def size_aware_repair(self):
        candidate = _oversized_recording_candidate(self)
        if candidate is not None:
            if candidate.source_id in self.active:
                self._retry_after[int(candidate.id)] = time.monotonic() + 120
                return None
            if not await _split_oversized_recording(self, candidate):
                return None
        return await original_repair()

    manager._stitch_fragment_group = types.MethodType(bounded_stitch, manager)
    manager._finalize_closed_stitch_sessions = types.MethodType(bounded_finalize, manager)
    manager._repair_local_mp4s = types.MethodType(size_aware_repair, manager)


__all__ = [
    "configured_max_bytes",
    "configured_stitch_target_bytes",
    "bounded_fragment_batch",
    "install_size_policy",
]
