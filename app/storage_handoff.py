"""Host-controlled storage handoff; metadata stays on the internal drive."""
from __future__ import annotations

import json
from functools import wraps

from .config import settings

BUFFER_RESERVE = 128 * 1024**2


def state() -> dict:
    path = settings.data_dir / "storage-state.json"
    try:
        value = json.loads(path.read_text())
        if value.get("mode") not in {"nvme", "buffer", "quiesce"}:
            raise ValueError("Unknown storage mode")
        return value
    except FileNotFoundError:
        return {"mode": "legacy"}
    except (OSError, ValueError, TypeError):
        # Fail closed if a configured handoff cannot be read.
        return {"mode": "quiesce", "error": "Invalid storage state"}


def media_online() -> bool:
    return state()["mode"] in {"legacy", "nvme"}


def capture_allowed() -> bool:
    import shutil
    mode = state()["mode"]
    if mode == "quiesce":
        return False
    if mode == "buffer":
        return shutil.disk_usage(settings.recordings_dir).free > BUFFER_RESERVE
    return True


def media_job(function=None, *, buffering=False):
    """Drain started jobs; never cancel a thread while it still owns a file."""
    if function is None:
        return lambda f: media_job(f, buffering=buffering)
    @wraps(function)
    async def wrapped(self, *args, **kwargs):
        if not media_online() and not (buffering and state()["mode"] == "buffer"):
            return None
        self._storage_jobs += 1
        try:
            return await function(self, *args, **kwargs)
        finally:
            self._storage_jobs -= 1
    return wrapped


def acknowledge(token: str) -> None:
    path = settings.data_dir / "storage-ready.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"token": token}))
    temporary.replace(path)
