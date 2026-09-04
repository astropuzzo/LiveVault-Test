from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from .utils import generate_live_preview


PREVIEW_INTERVAL_SECONDS = 20.0
PREVIEW_RETRY_SECONDS = 2.0
PREVIEW_INITIAL_DELAY_SECONDS = 1.5


def _argv_value(argv: Sequence[str], flag: str) -> str:
    try:
        index = list(argv).index(flag)
    except ValueError:
        return ""
    if index + 1 >= len(argv):
        return ""
    return str(argv[index + 1]).strip()


def preview_paths_from_argv(argv: Sequence[str]) -> tuple[str, str]:
    """Return (output_pattern, jpeg_preview_path) for the Stripchat child."""
    return _argv_value(argv, "--output-pattern"), _argv_value(argv, "--preview")


def _usable(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def newest_preview_source(output_pattern: str) -> Path | None:
    """Pick the currently growing Stripchat media file without opening the stream again.

    Mouflon capture writes ``*.capture.mp4`` and exposes ``.active-preview.mp4``
    as a symlink to it.  Flashphoner/FFmpeg writes the numbered output part
    directly.  Both are fragmented MP4 and can yield an in-progress keyframe.
    """
    if not output_pattern:
        return None
    pattern = Path(output_pattern)
    directory = pattern.parent
    if not directory.is_dir():
        return None

    candidates: list[Path] = []
    for name in (".active-preview.mp4", ".active-preview.webm"):
        path = directory / name
        if _usable(path):
            candidates.append(path)

    for glob_pattern in ("*.capture.mp4", "*.capture.webm"):
        candidates.extend(path for path in directory.glob(glob_pattern) if _usable(path))

    # FFmpeg segment output, e.g. creator_..._part%03d.mp4 -> part001.mp4.
    if "%03d" in pattern.name:
        direct_glob = pattern.name.replace("%03d", "*")
        candidates.extend(path for path in directory.glob(direct_glob) if _usable(path))

    if not candidates:
        return None

    def rank(path: Path) -> tuple[int, int, int]:
        try:
            stat = path.stat()
        except OSError:
            return (0, 0, 0)
        # Prefer a currently growing capture over a completed older part when
        # mtimes are close; then use freshest mtime and largest size.
        active = int(path.name.startswith(".active-preview") or ".capture." in path.name)
        return (stat.st_mtime_ns, active, stat.st_size)

    return max(candidates, key=rank)


def _preview_worker(output_pattern: str, preview_path: str, stop: threading.Event) -> None:
    output = Path(preview_path)
    delay = PREVIEW_INITIAL_DELAY_SECONDS
    last_signature: tuple[str, int, int] | None = None

    while not stop.wait(delay):
        source = newest_preview_source(output_pattern)
        if source is None:
            delay = PREVIEW_RETRY_SECONDS
            continue
        try:
            stat = source.stat()
            signature = (str(source), stat.st_size, stat.st_mtime_ns)
        except OSError:
            delay = PREVIEW_RETRY_SECONDS
            continue

        # If the producer has not advanced, keep the previous JPEG rather than
        # decoding the same frame repeatedly.
        if signature == last_signature and _usable(output):
            delay = PREVIEW_RETRY_SECONDS
            continue

        if generate_live_preview(source, output):
            last_signature = signature
            delay = PREVIEW_INTERVAL_SECONDS
        else:
            # Growing fMP4s can be unreadable for the first couple of fragments.
            # Retry quickly until the first decodable keyframe appears.
            delay = PREVIEW_RETRY_SECONDS


@contextmanager
def stripchat_preview_worker(argv: Sequence[str] | None = None) -> Iterator[None]:
    """Generate the dashboard JPEG from local capture data at very low duty cycle."""
    values = list(sys.argv[1:] if argv is None else argv)
    output_pattern, preview_path = preview_paths_from_argv(values)
    if not output_pattern or not preview_path:
        yield
        return

    stop = threading.Event()
    thread = threading.Thread(
        target=_preview_worker,
        args=(output_pattern, preview_path, stop),
        name="stripchat-preview",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2.5)
