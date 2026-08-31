from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")


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


def media_duration(path: Path) -> float | None:
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if p.returncode == 0 and p.stdout.strip():
            return float(p.stdout.strip())
    except Exception:
        pass
    return None


def human_bytes(n: int | float) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
