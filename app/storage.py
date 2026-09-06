from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import settings


@dataclass
class DiskState:
    total: int
    used: int
    free: int
    free_gb: float
    pressure: str


def disk_state(path: Path | None = None) -> DiskState:
    target = path or settings.recordings_dir
    usage = shutil.disk_usage(target)
    free_gb = usage.free / (1024 ** 3)
    # Runtime values may be changed from the authenticated Settings UI.
    from .settings_store import runtime
    cfg = runtime()
    from .storage_handoff import state, BUFFER_RESERVE
    if state()["mode"] == "buffer":
        pressure = "critical" if usage.free <= BUFFER_RESERVE else "ok"
        return DiskState(usage.total, usage.used, usage.free, free_gb, pressure)
    pressure = "ok"
    if free_gb <= cfg.critical_free_gb:
        pressure = "critical"
    elif free_gb <= cfg.min_free_gb:
        pressure = "warning"
    return DiskState(usage.total, usage.used, usage.free, free_gb, pressure)
