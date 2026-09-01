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
from .source_providers import ResolvedInput, resolve_inputs
from .utils import safe_name, utcnow


@dataclass
class RecorderSession:
    source_id: int
    source_name: str
    session_id: str
    directory: Path
    process: asyncio.subprocess.Process
    started_at: datetime
    extension: str


def _ffmpeg_headers(headers: dict[str, str]) -> str:
    allowed = {"user-agent", "referer", "origin", "cookie", "authorization"}
    lines = []
    for key, value in headers.items():
        if key.lower() in allowed and "\n" not in value and "\r" not in value:
            lines.append(f"{key}: {value}")
    return "\r\n".join(lines) + ("\r\n" if lines else "")


def build_ffmpeg_command(inputs: list[ResolvedInput], output_pattern: Path, *, segment_minutes: int | None = None, container_format: str | None = None) -> list[str]:
    cfg = runtime()
    segment_minutes = int(segment_minutes or cfg.segment_minutes)
    container_format = (container_format or cfg.container_format).lower()
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin"]
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
        cmd += ["-map", "0:v:0?", "-map", "0:a:0?"]
    else:
        cmd += ["-map", f"{video_idx}:v:0?" if video_idx is not None else "0:v:0?"]
        cmd += ["-map", f"{audio_idx}:a:0?" if audio_idx is not None else "0:a:0?"]

    cmd += [
        "-c", "copy",
        "-max_interleave_delta", "10000000",
        "-f", "segment",
        "-segment_time", str(max(60, segment_minutes * 60)),
        "-reset_timestamps", "1",
    ]
    if container_format == "mp4":
        cmd += [
            "-segment_format", "mp4",
            "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
        ]
    else:
        cmd += ["-segment_format", "matroska"]
    cmd += [str(output_pattern)]
    return cmd


async def start_recorder(source: Source) -> RecorderSession:
    cfg = runtime()
    inputs = await resolve_inputs(source.platform, source.slug, source.quality)
    local_now = datetime.now(ZoneInfo(settings.timezone))
    source_name = safe_name(source.name)
    session_id = f"{source_name}_{local_now:%Y-%m-%d_%H-%M-%S}"
    directory = settings.recordings_dir / source_name / session_id
    directory.mkdir(parents=True, exist_ok=True)
    extension = ".mp4" if cfg.container_format == "mp4" else ".mkv"
    output_pattern = directory / f"{session_id}_part%03d{extension}"
    cmd = build_ffmpeg_command(inputs, output_pattern, segment_minutes=cfg.segment_minutes, container_format=cfg.container_format)
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    return RecorderSession(source.id, source.name, session_id, directory, process, utcnow(), extension)


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
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
        "-map", "0", "-dn", "-ignore_unknown", "-c", "copy", "-movflags", "+faststart", str(tmp),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr = await proc.stderr.read() if proc.stderr else b""
    rc = await proc.wait()
    if rc == 0 and tmp.exists() and tmp.stat().st_size > 0:
        tmp.replace(output)
        path.unlink(missing_ok=True)
        return output
    tmp.unlink(missing_ok=True)
    raise RuntimeError((stderr.decode(errors="replace") or "FFmpeg remux failed")[-1500:])
