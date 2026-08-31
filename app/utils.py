from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass
class IntegrityResult:
    ok: bool
    duration: float | None
    error: str = ""
    streams: list[dict] | None = None


def safe_name(value: str) -> str:
    value = SAFE_RE.sub("_", value.strip())
    return value.strip("._-")[:120] or "source"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def probe_media(path: Path) -> IntegrityResult:
    try:
        p = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration,format_name:stream=index,codec_type,codec_name",
                "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if p.returncode != 0:
            return IntegrityResult(False, None, (p.stderr or "ffprobe failed")[-1200:])
        payload = json.loads(p.stdout or "{}")
        streams = payload.get("streams") or []
        has_media = any(x.get("codec_type") in {"video", "audio"} for x in streams)
        duration_raw = (payload.get("format") or {}).get("duration")
        duration = float(duration_raw) if duration_raw not in (None, "N/A", "") else None
        if not has_media:
            return IntegrityResult(False, duration, "Nessuno stream audio/video valido trovato", streams)
        if path.stat().st_size <= 0:
            return IntegrityResult(False, duration, "File vuoto", streams)
        return IntegrityResult(True, duration, "", streams)
    except Exception as exc:
        return IntegrityResult(False, None, str(exc)[-1200:])


def verify_media(path: Path, mode: str = "packet") -> IntegrityResult:
    quick = probe_media(path)
    if not quick.ok or mode == "quick":
        return quick
    try:
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-i", str(path), "-map", "0", "-c", "copy", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if p.returncode != 0:
            return IntegrityResult(False, quick.duration, (p.stderr or "Packet scan failed")[-1600:], quick.streams)
        return quick
    except Exception as exc:
        return IntegrityResult(False, quick.duration, str(exc)[-1200:], quick.streams)


def media_duration(path: Path) -> float | None:
    return probe_media(path).duration


def generate_thumbnail(path: Path, output: Path, duration: float | None = None) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    seek = 0.5
    if duration:
        # Stay well inside short clips instead of seeking exactly to EOF.
        seek = min(30.0, max(0.05, duration * 0.2))
    try:
        p = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{seek:.2f}", "-i", str(path), "-frames:v", "1",
                "-vf", "scale=480:-2:force_original_aspect_ratio=decrease",
                "-pix_fmt", "yuvj420p", "-strict", "unofficial",
                "-q:v", "4", str(output),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if p.returncode == 0 and output.exists() and output.stat().st_size > 0:
            return True
    except Exception:
        pass
    output.unlink(missing_ok=True)
    return False


def human_bytes(n: int | float) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
