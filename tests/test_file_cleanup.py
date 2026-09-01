from pathlib import Path

import pytest

from app.file_cleanup import cleanup_orphan_videos, safe_unlink


def test_safe_unlink_removes_file_and_reports_bytes(tmp_path: Path):
    root = tmp_path / "recordings"
    root.mkdir()
    target = root / "clip.mp4"
    target.write_bytes(b"123456")
    freed, removed = safe_unlink(target, root)
    assert removed is True
    assert freed == 6
    assert not target.exists()


def test_safe_unlink_rejects_path_outside_livevault_root(tmp_path: Path):
    root = tmp_path / "recordings"
    root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    with pytest.raises(ValueError):
        safe_unlink(outside, root)
    assert outside.exists()


def test_orphan_cleanup_keeps_tracked_and_active_files(tmp_path: Path):
    root = tmp_path / "recordings"
    tracked = root / "camera" / "old" / "tracked.mp4"
    orphan = root / "camera" / "old" / "orphan.mkv"
    active_dir = root / "camera" / "live"
    active = active_dir / "current.mp4"
    for path in (tracked, orphan, active):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"abcd")

    result = cleanup_orphan_videos(root, [tracked], [active_dir])

    assert tracked.exists()
    assert active.exists()
    assert not orphan.exists()
    assert result["removed"] == 1
    assert result["freed"] == 4
    assert result["skipped_active"] == 1
