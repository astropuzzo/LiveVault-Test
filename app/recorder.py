from __future__ import annotations

import asyncio
import os
import signal
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import settings
from .db import Source
from .settings_store import runtime
from .source_providers import ResolvedInput, audit_inputs, resolve_inputs
from .utils import probe_media, safe_name, utcnow

LIVE_PREVIEW_INTERVAL_SECONDS = 20
LIVE_PREVIEW_MAX_AGE_SECONDS = 90


def live_preview_path(source_id: int) -> Path:
    return settings.data_dir / "live_previews" / f"{int(source_id)}.jpg"


@dataclass
class RecorderSession:
    source_id: int
    source_name: str
    session_id: str
    directory: Path
    process: asyncio.subprocess.Process
    started_at: datetime
    extension: str
    max_file_bytes: int
    safe_stop_bytes: int
    preview_path: Path
    rollover_requested: bool = False


def _ffmpeg_headers(headers: dict[str, str]) -> str:
    allowed = {"user-agent", "referer", "origin", "cookie", "authorization"}
    lines = []
    for key, value in headers.items():
        if key.lower() in allowed and "\n" not in value and "\r" not in value:
            lines.append(f"{key}: {value}")
    return "\r\n".join(lines) + ("\r\n" if lines else "")


def max_output_bytes(segment_max_gb: float) -> int:
    return max(1, int(float(segment_max_gb) * 1024**3))


def safe_output_limit_bytes(segment_max_gb: float) -> int:
    """Leave enough headroom for buffered packets and the container trailer."""
    maximum = max_output_bytes(segment_max_gb)
    reserve = max(64 * 1024**2, int(maximum * 0.05))
    return max(1, maximum - reserve)


def build_ffmpeg_command(
    inputs: list[ResolvedInput],
    output_pattern: Path,
    *,
    segment_minutes: int | None = None,
    segment_max_gb: float | None = None,
    container_format: str | None = None,
    preview_path: Path | None = None,
    preview_interval_seconds: int = LIVE_PREVIEW_INTERVAL_SECONDS,
) -> list[str]:
    cfg = runtime()
    segment_minutes = int(segment_minutes or cfg.segment_minutes)
    segment_max_gb = float(segment_max_gb or cfg.segment_max_gb)
    container_format = (container_format or cfg.container_format).lower()
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"]
    for item in inputs:
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        headers = _ffmpeg_headers(item.http_headers)
        if headers:
            cmd += ["-headers", headers]
        cmd += ["-i", item.url]

    video_idx = next((i for i, item in enumerate(inputs) if item.kind == "video"), None)
    if video_idx is None:
        video_idx = next((i for i, item in enumerate(inputs) if item.kind == "media"), None)
    audio_idx = next((i for i, item in enumerate(inputs) if item.kind == "audio"), None)
    if audio_idx is None:
        audio_idx = next((i for i, item in enumerate(inputs) if item.kind == "media"), None)
    if len(inputs) == 1:
        video_map = "0:v:0"
        audio_map = "0:a:0"
    else:
        video_map = f"{video_idx}:v:0" if video_idx is not None else "0:v:0"
        audio_map = f"{audio_idx}:a:0" if audio_idx is not None else "0:a:0"
    cmd += ["-map", video_map, "-map", audio_map]

    cmd += [
        "-c", "copy",
        "-max_interleave_delta", "10000000",
        "-f", "segment",
        "-segment_time", str(max(60, segment_minutes * 60)),
        "-reset_timestamps", "1",
        "-fs", str(safe_output_limit_bytes(segment_max_gb)),
    ]
    if container_format == "mp4":
        cmd += [
            "-segment_format", "mp4",
            "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
        ]
    else:
        cmd += ["-segment_format", "matroska"]
    cmd += [str(output_pattern)]
    if preview_path is not None:
        interval = max(5, int(preview_interval_seconds))
        cmd += [
            "-map", video_map,
            "-an",
            "-vf", f"fps=1/{interval},scale=640:-2:force_original_aspect_ratio=decrease",
            "-c:v", "mjpeg",
            "-q:v", "6",
            "-f", "image2",
            "-update", "1",
            "-atomic_writing", "1",
            str(preview_path),
        ]
    return cmd


async def start_recorder(source: Source) -> RecorderSession:
    cfg = runtime()
    inputs = await resolve_inputs(source.platform, source.slug, source.quality)
    audit = await audit_inputs(inputs)
    if not audit.has_video or not audit.has_audio:
        raise RuntimeError(f"Audio Guard ha bloccato l'avvio: {audit.error}")
    inputs = [item for item in inputs if item.kind in {"media", "video", "audio"}]
    local_now = datetime.now(ZoneInfo(settings.timezone))
    source_name = safe_name(source.name)
    session_id = f"{source_name}_{local_now:%Y-%m-%d_%H-%M-%S}"
    directory = settings.recordings_dir / source_name / session_id
    directory.mkdir(parents=True, exist_ok=True)
    extension = ".mp4" if cfg.container_format == "mp4" else ".mkv"
    output_pattern = directory / f"{session_id}_part%03d{extension}"
    preview_path = live_preview_path(source.id)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.unlink(missing_ok=True)
    cmd = build_ffmpeg_command(
        inputs,
        output_pattern,
        segment_minutes=cfg.segment_minutes,
        segment_max_gb=cfg.segment_max_gb,
        container_format=cfg.container_format,
        preview_path=preview_path,
        preview_interval_seconds=LIVE_PREVIEW_INTERVAL_SECONDS,
    )
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    return RecorderSession(
        source.id,
        source.name,
        session_id,
        directory,
        process,
        utcnow(),
        extension,
        max_output_bytes(cfg.segment_max_gb),
        safe_output_limit_bytes(cfg.segment_max_gb),
        preview_path,
    )


async def stop_recorder(session: RecorderSession) -> None:
    if session.process.returncode is not None:
        return
    try:
        os.killpg(session.process.pid, signal.SIGINT)
        await asyncio.wait_for(session.process.wait(), timeout=7)
    except Exception:
        try:
            os.killpg(session.process.pid, signal.SIGTERM)
        except Exception:
            pass
        try:
            await asyncio.wait_for(session.process.wait(), timeout=2)
        except Exception:
            try:
                os.killpg(session.process.pid, signal.SIGKILL)
            except Exception:
                pass


def mp4_is_streaming_ready(path: Path) -> bool:
    """Return True only for a non-fragmented MP4 with the moov index up front."""
    if path.suffix.lower() != ".mp4":
        return True
    try:
        file_size = path.stat().st_size
        offset = 0
        found_moov = False
        found_mdat = False
        boxes_seen = 0
        with path.open("rb") as handle:
            while offset + 8 <= file_size and boxes_seen < 100_000:
                handle.seek(offset)
                header = handle.read(16)
                if len(header) < 8:
                    return False
                box_size = int.from_bytes(header[:4], "big")
                box_type = header[4:8]
                header_size = 8
                if box_size == 1:
                    if len(header) < 16:
                        return False
                    box_size = int.from_bytes(header[8:16], "big")
                    header_size = 16
                elif box_size == 0:
                    box_size = file_size - offset
                if box_size < header_size or offset + box_size > file_size:
                    return False
                if box_type == b"moof":
                    return False
                if box_type == b"mdat":
                    found_mdat = True
                elif box_type == b"moov":
                    if found_mdat:
                        return False
                    found_moov = True
                offset += box_size
                boxes_seen += 1
        return found_moov
    except (OSError, ValueError):
        return False


async def _copy_remux(source: Path, output: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-map", "0", "-dn", "-ignore_unknown", "-c", "copy", "-movflags", "+faststart", str(output),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    timeout = max(120, min(900, int(source.stat().st_size / (8 * 1024**2))))
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.CancelledError:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        raise
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError("Finalizzazione MP4 scaduta") from exc
    if proc.returncode != 0 or not output.exists() or output.stat().st_size <= 0:
        detail = (stderr or b"").decode(errors="replace")[-1500:]
        raise RuntimeError(detail or "FFmpeg remux failed")


async def _validate_final_mp4(path: Path, source_size: int) -> None:
    if not mp4_is_streaming_ready(path):
        raise RuntimeError("Indice MP4 finale non valido")
    if path.stat().st_size < max(1, int(source_size * 0.8)):
        raise RuntimeError("MP4 finale troppo piccolo rispetto all'originale")
    media = await asyncio.to_thread(probe_media, path, require_audio=True)
    if not media.ok:
        raise RuntimeError(f"MP4 finale non valido: {media.error}")


async def finalize_mp4_for_streaming(path: Path, *, require_space: bool = True) -> bool:
    """Atomically turn a crash-resistant fragmented MP4 into a seekable final MP4."""
    if path.suffix.lower() != ".mp4" or mp4_is_streaming_ready(path):
        return False
    original = path.stat()
    free = shutil.disk_usage(path.parent).free
    if require_space and free < original.st_size + 256 * 1024 * 1024:
        raise RuntimeError("Spazio insufficiente per finalizzare l'MP4")
    tmp = path.with_name(f".{path.stem}.finalizing.mp4")
    tmp.unlink(missing_ok=True)
    try:
        await _copy_remux(path, tmp)
        await _validate_final_mp4(tmp, original.st_size)
        os.utime(tmp, ns=(original.st_atime_ns, original.st_mtime_ns))
        tmp.replace(path)
        return True
    except asyncio.CancelledError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


async def remux_to_mp4(path: Path, *, require_space: bool = True) -> Path:
    if path.suffix.lower() == ".mp4":
        return path
    if path.suffix.lower() != ".mkv":
        raise RuntimeError(f"Unsupported container for MP4 remux: {path.suffix}")
    free = shutil.disk_usage(path.parent).free
    if require_space and free < path.stat().st_size + 256 * 1024 * 1024:
        raise RuntimeError("Spazio insufficiente per il remux MP4")
    output = path.with_suffix(".mp4")
    tmp = path.with_suffix(".tmp.mp4")
    try:
        await _copy_remux(path, tmp)
        await _validate_final_mp4(tmp, path.stat().st_size)
        tmp.replace(output)
        path.unlink(missing_ok=True)
        return output
    except asyncio.CancelledError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
