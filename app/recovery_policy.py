from __future__ import annotations

import re
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


QUARANTINE_SUFFIX = re.compile(r"\.recovery-failed(?:-\d+)?\.mp4$")


def quarantine_stem(path: Path) -> str:
    """Stem of the capture a quarantined remux copy belongs to."""
    return QUARANTINE_SUFFIX.sub("", path.name).lstrip(".")


def redundant_copy_reason(folder: Path, stem: str) -> str:
    """Why an interrupted remux copy of ``stem`` carries nothing unique ('' = keep it).

    Stripchat parts are written raw as ``<stem>.capture.mp4`` and remuxed into
    ``<stem>.mp4`` through ``.<stem>.finalizing.mp4``. An interrupted remux
    leaves the raw capture intact; it is indexed and uploaded under its own
    name, so the half-written copy is a duplicate, never the only copy.
    """
    for name in (f"{stem}.capture.mp4", f"{stem}.mp4"):
        candidate = folder / name
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return f"originale presente: {name}"
        except OSError:
            continue
    from sqlalchemy import select

    from .db import Recording, db_session
    with db_session() as db:
        uploaded = db.scalar(select(Recording).where(
            Recording.filename.in_([f"{stem}.capture.mp4", f"{stem}.mp4"]),
            Recording.upload_status == "uploaded",
        ).limit(1))
        if uploaded is not None:
            return f"già caricato nel cloud: registrazione {uploaded.id}"
    return ""
