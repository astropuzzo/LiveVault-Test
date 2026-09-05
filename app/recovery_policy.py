from __future__ import annotations

from pathlib import Path

_UNRECOVERABLE_FINALIZING_MARKERS = (
    "moov atom not found",
    "invalid data found when processing input",
    "error reading header",
)


def finalizing_error_is_unrecoverable(detail: str) -> bool:
    lowered = str(detail or "").lower()
    return any(marker in lowered for marker in _UNRECOVERABLE_FINALIZING_MARKERS)


def recovery_quarantine_path(temporary: Path) -> Path:
    suffix = ".finalizing.mp4"
    name = temporary.name
    if name.startswith(".") and name.endswith(suffix):
        stem = name[1:-len(suffix)]
    else:
        stem = temporary.stem.lstrip(".") or "recovery"
    base = temporary.with_name(f".{stem}.recovery-failed.mp4")
    if not base.exists():
        return base
    for index in range(2, 1000):
        candidate = temporary.with_name(f".{stem}.recovery-failed-{index}.mp4")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Troppi file di recovery falliti per {temporary.name}")
