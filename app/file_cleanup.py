from __future__ import annotations

from pathlib import Path

VIDEO_SUFFIXES = {".mp4", ".mkv"}


def confined_path(path: Path, root: Path) -> Path:
    """Resolve *path* and guarantee it stays inside *root*."""
    root_resolved = root.resolve(strict=False)
    target = path.resolve(strict=False)
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Percorso fuori dall'area LiveVault: {target}") from exc
    return target


def safe_unlink(path: Path, root: Path) -> tuple[int, bool]:
    """Delete one regular file confined to root and return (bytes_freed, removed)."""
    target = confined_path(path, root)
    if not target.exists():
        return 0, False
    if not target.is_file():
        raise ValueError(f"Il percorso non è un file: {target.name}")
    size = target.stat().st_size
    target.unlink()
    return size, True


def cleanup_empty_parents(start: Path, root: Path) -> None:
    root_resolved = root.resolve(strict=False)
    current = start.resolve(strict=False)
    while current != root_resolved:
        try:
            current.relative_to(root_resolved)
        except ValueError:
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def cleanup_orphan_videos(root: Path, tracked_paths: list[Path], active_dirs: list[Path]) -> dict:
    """Remove untracked MP4/MKV files while never touching active recorder directories."""
    root_resolved = root.resolve(strict=False)
    tracked: set[Path] = set()
    for path in tracked_paths:
        try:
            tracked.add(confined_path(path, root_resolved))
        except ValueError:
            continue

    active: list[Path] = []
    for directory in active_dirs:
        try:
            active.append(confined_path(directory, root_resolved))
        except ValueError:
            continue

    removed = 0
    freed = 0
    skipped_active = 0
    errors: list[str] = []

    if not root_resolved.exists():
        return {"removed": 0, "freed": 0, "skipped_active": 0, "errors": []}

    for candidate in root_resolved.rglob("*"):
        if candidate.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        try:
            resolved = confined_path(candidate, root_resolved)
            if not resolved.is_file() or resolved in tracked:
                continue
            if any(resolved.is_relative_to(directory) for directory in active):
                skipped_active += 1
                continue
            size, did_remove = safe_unlink(resolved, root_resolved)
            if did_remove:
                removed += 1
                freed += size
                cleanup_empty_parents(resolved.parent, root_resolved)
        except (OSError, ValueError) as exc:
            errors.append(f"{candidate.name}: {exc}")

    return {"removed": removed, "freed": freed, "skipped_active": skipped_active, "errors": errors}
