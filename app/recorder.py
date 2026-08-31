from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import settings
from .db import Source
from .source_providers import ResolvedInput, resolve_inputs
from .utils import safe_name


@dataclass
class RecorderSession:
    source_id: int
    source_name: str
    session_id: str
    directory: Path
    process: asyncio.subprocess.Process
    started_at: datetime


def _ffmpeg_headers(headers: dict[str, str]) -> str:
    allowed = {"user-agent", "referer", "origin", "cookie", "authorization"}
    lines = []
    for key, value in headers.items():
        if key.lower() in allowed and "\n" not in value and "\r" not in value:
            lines.append(f"{key}: {value}")
    return "\r\n".join(lines) + ("\r\n" if lines else "")


def build_ffmpeg_command(inputs: list[ResolvedInput], output_pattern: Path) -> list[str]:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin"]
    for item in inputs:
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        headers = _ffmpeg_headers(item.http_headers)
        if headers:
            cmd += ["-headers", headers]
        cmd += ["-i", item.url]

    video_idx = next((i for i, item in enumerate(inputs) if item.kind == "video"), None)
    audio_idx = next((i for i, item in enumerate(inputs) if item.kind == "audio"), None)
    if len(inputs) == 1:
        cmd += ["-map", "0:v:0?", "-map", "0:a:0?"]
    else:
        if video_idx is not None:
            cmd += ["-map", f"{video_idx}:v:0?"]
        else:
            cmd += ["-map", "0:v:0?"]
        if audio_idx is not None:
            cmd += ["-map", f"{audio_idx}:a:0?"]
        else:
            cmd += ["-map", "0:a:0?"]

    cmd += [
        "-c", "copy",
        "-max_interleave_delta", "0",
        "-f", "segment",
        "-segment_time", str(max(60, settings.segment_minutes * 60)),
        "-reset_timestamps", "1",
        "-segment_format", "matroska",
        str(output_pattern),
    ]
    return cmd


async def start_recorder(source: Source) -> RecorderSession:
    inputs = await resolve_inputs(source.platform, source.slug, source.quality)
    local_now = datetime.now(ZoneInfo(settings.timezone))
    source_name = safe_name(source.name)
    session_id = f"{source_name}_{local_now:%Y-%m-%d_%H-%M-%S}"
    directory = settings.recordings_dir / source_name / session_id
    directory.mkdir(parents=True, exist_ok=True)
    output_pattern = directory / f"{session_id}_part%03d.mkv"
    cmd = build_ffmpeg_command(inputs, output_pattern)
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    return RecorderSession(source.id, source.name, session_id, directory, process, local_now)


async def stop_recorder(session: RecorderSession) -> None:
    if session.process.returncode is not None:
        return
    try:
        os.killpg(session.process.pid, signal.SIGINT)
        await asyncio.wait_for(session.process.wait(), timeout=20)
    except Exception:
        try:
            os.killpg(session.process.pid, signal.SIGTERM)
        except Exception:
            pass
        try:
            await asyncio.wait_for(session.process.wait(), timeout=10)
        except Exception:
            try:
                os.killpg(session.process.pid, signal.SIGKILL)
            except Exception:
                pass


async def remux_to_mp4(path: Path) -> Path:
    if path.suffix.lower() != ".mkv":
        return path
    # Remux temporarily needs roughly another file-sized allocation. Under disk
    # pressure keep the resilient MKV rather than risking a full filesystem.
    free = shutil.disk_usage(path.parent).free
    if free < path.stat().st_size + 512 * 1024 * 1024:
        return path
    output = path.with_suffix(".mp4")
    tmp = path.with_suffix(".tmp.mp4")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
        "-map", "0", "-dn", "-ignore_unknown", "-c", "copy", "-movflags", "+faststart", str(tmp),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    rc = await proc.wait()
    if rc == 0 and tmp.exists() and tmp.stat().st_size > 0:
        tmp.replace(output)
        path.unlink(missing_ok=True)
        return output
    tmp.unlink(missing_ok=True)
    return path
