from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import tempfile
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

    @property
    def has_video(self) -> bool:
        return any(stream.get("codec_type") == "video" for stream in (self.streams or []))

    @property
    def has_audio(self) -> bool:
        return any(stream.get("codec_type") == "audio" for stream in (self.streams or []))

    def codec(self, stream_type: str) -> str:
        return next(
            (str(stream.get("codec_name") or "") for stream in (self.streams or []) if stream.get("codec_type") == stream_type),
            "",
        )


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


def _probe_duration(path: Path) -> float | None:
    """Duration is useful metadata, but it must never block file recovery."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        value = (result.stdout or "").strip()
        return float(value) if result.returncode == 0 and value not in {"", "N/A"} else None
    except (subprocess.TimeoutExpired, TypeError, ValueError):
        return None


def probe_media(path: Path, *, require_audio: bool = True) -> IntegrityResult:
    try:
        p = subprocess.run(
            [
                "ffprobe", "-v", "error", "-probesize", "32M", "-analyzeduration", "20M",
                "-read_intervals", "%+15", "-show_entries",
                "format=format_name:stream=index,codec_type,codec_name",
                "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if p.returncode != 0:
            return IntegrityResult(False, None, (p.stderr or "ffprobe failed")[-1200:])
        payload = json.loads(p.stdout or "{}")
        streams = payload.get("streams") or []
        duration = _probe_duration(path)
        has_video = any(x.get("codec_type") == "video" for x in streams)
        has_audio = any(x.get("codec_type") == "audio" for x in streams)
        if not has_video:
            return IntegrityResult(False, duration, "Nessuno stream audio/video valido trovato", streams)
        if require_audio and not has_audio:
            return IntegrityResult(False, duration, "Traccia audio assente: il file non verrà caricato come video muto", streams)
        if path.suffix.lower() == ".mp4" and (duration is None or not math.isfinite(duration) or duration <= 0):
            return IntegrityResult(False, duration, "Durata MP4 finale assente: file non pronto per lo streaming", streams)
        if path.stat().st_size <= 0:
            return IntegrityResult(False, duration, "File vuoto", streams)
        return IntegrityResult(True, duration, "", streams)
    except subprocess.TimeoutExpired:
        return IntegrityResult(False, None, "Analisi stream ffprobe scaduta dopo 60 secondi; riprova quando il file è stabile")
    except Exception as exc:
        return IntegrityResult(False, None, str(exc)[-1200:])


def verify_media(path: Path, mode: str = "packet", *, require_audio: bool = True) -> IntegrityResult:
    quick = probe_media(path, require_audio=require_audio)
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
    return probe_media(path, require_audio=False).duration


def generate_thumbnail(path: Path, output: Path, duration: float | None = None) -> bool:
    """Create a 3x3 storyboard from nine evenly spaced moments.

    Input-side seeks keep this inexpensive for long recordings. A single-frame
    extraction remains as a fallback for unusual media. Writing to a temporary
    file keeps regeneration atomic.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    if duration is None or not math.isfinite(duration) or duration <= 0:
        duration = _probe_duration(path)

    seek = 0.5
    seeks: list[float] = []
    if duration and math.isfinite(duration) and duration > 0:
        safe_end = max(0.0, duration - min(1.0, duration * 0.03))
        seeks = [safe_end * fraction for fraction in (0.05, 0.16, 0.27, 0.38, 0.50, 0.62, 0.73, 0.84, 0.95)]
        seek = min(30.0, max(0.05, duration * 0.2))

    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.stem}-",
        suffix=output.suffix or ".jpg",
        delete=False,
    ) as temporary:
        candidate = Path(temporary.name)

    try:
        if seeks:
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
            for position in seeks:
                command.extend(["-ss", f"{position:.3f}", "-i", str(path)])
            cells = [
                f"[{index}:v:0]scale=320:180:force_original_aspect_ratio=decrease,"
                f"pad=320:180:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[v{index}]"
                for index in range(9)
            ]
            filters = ";".join(cells + [
                "[v0][v1][v2]hstack=inputs=3[row0]",
                "[v3][v4][v5]hstack=inputs=3[row1]",
                "[v6][v7][v8]hstack=inputs=3[row2]",
                "[row0][row1][row2]vstack=inputs=3[sheet]",
            ])
            command.extend([
                "-filter_complex", filters, "-map", "[sheet]", "-an",
                "-frames:v", "1", "-update", "1", "-pix_fmt", "yuvj420p",
                "-q:v", "4", str(candidate),
            ])
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=120, check=False,
            )
            if result.returncode == 0 and candidate.exists() and candidate.stat().st_size > 0:
                candidate.replace(output)
                return True

        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{seek:.2f}", "-i", str(path), "-frames:v", "1",
                "-vf", "scale=960:540:force_original_aspect_ratio=decrease,"
                "pad=960:540:(ow-iw)/2:(oh-ih)/2:color=black",
                "-an", "-update", "1", "-pix_fmt", "yuvj420p",
                "-q:v", "4", str(candidate),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0 and candidate.exists() and candidate.stat().st_size > 0:
            candidate.replace(output)
            return True
    except Exception:
        pass
    candidate.unlink(missing_ok=True)
    return False


def human_bytes(n: int | float) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
