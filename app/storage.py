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
    target = path or settings.data_dir
    usage = shutil.disk_usage(target)
    free_gb = usage.free / (1024 ** 3)
    pressure = "ok"
    if free_gb <= settings.critical_free_gb:
        pressure = "critical"
    elif free_gb <= settings.min_free_gb:
        pressure = "warning"
    return DiskState(usage.total, usage.used, usage.free, free_gb, pressure)
