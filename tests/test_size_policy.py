from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.workers as workers
import app.workers.size_policy as policy


ROOT = Path(__file__).resolve().parents[1]


def _fragment(tmp_path: Path, fragment_id: int, size: int, seconds: int):
    path = tmp_path / f"capture_part{fragment_id:03d}.mp4"
    path.write_bytes(b"x" * size)
    return SimpleNamespace(
        id=fragment_id,
        local_path=str(path),
        size_bytes=size,
        started_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds),
        finalized_at=datetime(2026, 9, 4, 12, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds),
        duration_seconds=60.0,
        integrity_status="passed",
        integrity_error="",
    )


def test_configured_limit_and_target_follow_segment_max_gb(monkeypatch):
    monkeypatch.setattr(policy, "runtime", lambda: SimpleNamespace(segment_max_gb=2.0))
    maximum = policy.configured_max_bytes()
    target = policy.configured_stitch_target_bytes()

    assert maximum == 2 * 1024**3
    assert 0 < target < maximum


def test_bounded_batch_keeps_oldest_prefix_under_target(tmp_path):
    first = _fragment(tmp_path, 1, 600, 0)
    second = _fragment(tmp_path, 2, 300, 60)
    third = _fragment(tmp_path, 3, 500, 120)

    selected = policy.bounded_fragment_batch(
        [third, first, second],
        target_bytes=1000,
        maximum_bytes=1200,
    )

    assert [item.id for item in selected] == [1, 2]
    assert sum(Path(item.local_path).stat().st_size for item in selected) == 900


def test_bounded_batch_accepts_one_fragment_up_to_hard_limit(tmp_path):
    first = _fragment(tmp_path, 1, 1100, 0)
    second = _fragment(tmp_path, 2, 200, 60)

    selected = policy.bounded_fragment_batch(
        [first, second],
        target_bytes=1000,
        maximum_bytes=1200,
    )

    assert [item.id for item in selected] == [1]


def test_bounded_batch_rejects_single_oversized_fragment(tmp_path):
    fragment = _fragment(tmp_path, 1, 1300, 0)

    with pytest.raises(RuntimeError, match="Frammento singolo oltre il limite"):
        policy.bounded_fragment_batch(
            [fragment],
            target_bytes=1000,
            maximum_bytes=1200,
        )


def test_reconnect_stub_joins_next_capture_with_safe_headroom(tmp_path):
    # Proportions from live files 344/345: 38 MB stub + 2044 MB capture.
    first = _fragment(tmp_path, 1, 38, 0)
    first.duration_seconds = 72.35
    second = _fragment(tmp_path, 2, 2044, 83)
    second.duration_seconds = 3888.45
    selected = policy.bounded_fragment_batch([first, second], target_bytes=2040, maximum_bytes=2147)
    assert [row.id for row in selected] == [1, 2]
    conservative = policy.bounded_fragment_batch([first, second], target_bytes=2040, maximum_bytes=2147, join_short_prefix=False)
    assert [row.id for row in conservative] == [1]


def test_short_prefix_never_exceeds_headroom_or_hard_limit(tmp_path):
    first = _fragment(tmp_path, 1, 90, 0)
    second = _fragment(tmp_path, 2, 2044, 60)
    assert policy.bounded_fragment_batch([first, second], target_bytes=2040, maximum_bytes=2147) == [first]


def test_oversized_remux_rejects_only_temporary_output(tmp_path, monkeypatch):
    original = tmp_path / 'capture.mp4'
    original.write_bytes(b'original')
    output = tmp_path / '.finalizing.mp4'
    output.write_bytes(b'x' * 101)
    monkeypatch.setattr(policy, 'configured_max_bytes', lambda: 100)
    with pytest.raises(policy.StitchOutputTooLarge):
        policy.check_stitch_output_size(output)
    assert original.read_bytes() == b'original'
    assert not output.exists()


def test_join_overflow_retries_with_conservative_prefix(tmp_path, monkeypatch):
    import asyncio
    first = _fragment(tmp_path, 1, 38, 0)
    second = _fragment(tmp_path, 2, 2044, 60)
    calls = []
    async def stitch(batch, **kwargs):
        calls.append([row.id for row in batch])
        if len(batch) > 1:
            raise policy.StitchOutputTooLarge('trailer overhead')
    manager = SimpleNamespace(_stitch_fragment_group=stitch, _finalize_closed_stitch_sessions=None, _repair_local_mp4s=None)
    monkeypatch.setattr(policy, 'configured_max_bytes', lambda: 2147)
    monkeypatch.setattr(policy, 'configured_stitch_target_bytes', lambda: 2040)
    policy.install_size_policy(manager)
    asyncio.run(manager._stitch_fragment_group([first, second]))
    assert calls == [[1, 2], [1]]
    assert Path(first.local_path).is_file() and Path(second.local_path).is_file()


def test_size_policy_is_installed_before_dashboard_error_guard():
    source = (ROOT / "app/main/__init__.py").read_text(encoding="utf-8")
    install = source.index("_install_size_policy(_processing_manager)")
    guard = source.index("_processing_original_stitch = _processing_manager._stitch_fragment_group")

    assert install < guard


def test_oversized_recovery_is_lossless_and_does_not_fall_through_to_full_scan():
    source = (ROOT / "app/workers/size_policy.py").read_text(encoding="utf-8")

    assert '"-c", "copy"' in source
    assert '"-f", "segment"' in source
    assert "if not await _split_oversized_recording(self, candidate):" in source
    assert "return None" in source
    assert "configured_max_bytes()" in source
    assert "bounded_fragment_batch" in source


def test_policy_install_is_idempotent():
    manager = workers.WorkerManager()
    policy.install_size_policy(manager)
    first = manager._stitch_fragment_group
    policy.install_size_policy(manager)

    assert manager._size_policy_installed is True
    assert manager._stitch_fragment_group == first
