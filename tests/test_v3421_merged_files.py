"""3.4.21: the 15-minute capture parts of one recording become one cloud file
(size limit or 2 h), and the NSFW times follow the joined file."""
import asyncio
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.workers as workers
from app.db import NsfwCoverage, NsfwMark, Recording, RecordingFragment
from app.main import _fragment_waits_for_join
from app.workers import size_policy
from tests.test_v3412_nsfw_live_stitch import BASE, _fragment, env  # noqa: F401  (fixture)

legacy = workers._legacy
QUARTER = 900.0


def _part(tmp_path: Path, index: int, seconds: float = QUARTER, size: int = 100):
    path = tmp_path / f"creator_s1_part{index:03d}.mp4"
    path.write_bytes(b"x" * size)
    return SimpleNamespace(
        id=index, source_id=7, session_id="creator_s1", local_path=str(path), size_bytes=size,
        started_at=BASE + timedelta(seconds=index * QUARTER),
        finalized_at=BASE + timedelta(seconds=index * QUARTER + seconds),
        duration_seconds=seconds, integrity_status="passed", integrity_error="",
    )


def test_a_live_waits_for_two_hours_of_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(size_policy, "configured_stitch_target_bytes", lambda: 10**9)
    manager = workers.WorkerManager()
    manager.active[7] = SimpleNamespace(session_id="creator_s1")
    now = BASE + timedelta(hours=3)
    parts = [_part(tmp_path, i, 899.7) for i in range(9)]
    assert not manager._stitch_group_ready(parts[:1], now)   # one quarter: no more a file of its own
    assert not manager._stitch_group_ready(parts[:7], now)
    assert manager._stitch_group_ready(parts[:8], now)       # 2 h despite the ~1 s jitter per part
    # The file takes eight quarters; the ninth starts the next file.
    assert [p.id for p in size_policy.bounded_fragment_batch(parts)] == list(range(8))


def test_the_size_limit_closes_a_file_before_two_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(size_policy, "configured_stitch_target_bytes", lambda: 1000)
    monkeypatch.setattr(size_policy, "configured_max_bytes", lambda: 1100)
    manager = workers.WorkerManager()
    manager.active[7] = SimpleNamespace(session_id="creator_s1")
    parts = [_part(tmp_path, i, size=300) for i in range(4)]
    assert not manager._stitch_group_ready(parts[:3], BASE + timedelta(hours=1))
    assert manager._stitch_group_ready(parts, BASE + timedelta(hours=1))
    assert [p.id for p in size_policy.bounded_fragment_batch(parts)] == [0, 1, 2]


def test_a_quiet_recording_is_published_whatever_its_length(tmp_path):
    manager = workers.WorkerManager()
    parts = [_part(tmp_path, i) for i in range(3)]
    last = parts[-1].finalized_at
    assert not manager._stitch_group_ready(parts, last + timedelta(minutes=5))   # may still reconnect
    assert manager._stitch_group_ready(parts, last + timedelta(minutes=21))


def test_parts_waiting_for_the_join_show_as_recorded():
    fragment = SimpleNamespace(source_id=7, session_id="creator_s1")
    assert _fragment_waits_for_join(fragment, {7: "creator_s1"})
    assert not _fragment_waits_for_join(fragment, {7: "creator_s2"})
    assert not _fragment_waits_for_join(fragment, {})


def test_nsfw_times_are_minutes_of_the_joined_file(tmp_path, env, monkeypatch):  # noqa: F811
    """A moment 10 minutes into the fourth quarter is minute 55 of the 2 h file."""
    paths = [tmp_path / f"AliciaBrooks_s1_part{i:03d}.mp4" for i in range(8)]
    with env.scope() as db:
        for i, path in enumerate(paths):
            _fragment(db, path, BASE + timedelta(seconds=i * QUARTER), QUARTER)
            db.add(NsfwCoverage(part_path=str(path), source_id=25, samples=220, covered_seconds=880.0))
        db.add(NsfwMark(source_id=25, part_path=str(paths[3]), part_time=600.0,
                        wall_at=BASE + timedelta(seconds=3 * QUARTER + 600), state="confirmed",
                        cls="FEMALE_GENITALIA_EXPOSED", verified_score=0.9))

    async def fake_stitch(parts, output, **_kwargs):
        assert [Path(p) for p in parts] == paths  # one file, parts in order
        output.write_bytes(b"joined")

    monkeypatch.setattr(legacy, "stitch_recording_parts", fake_stitch)
    monkeypatch.setattr(legacy, "_safe_duration", lambda _path: QUARTER)
    monkeypatch.setattr(legacy, "verify_media", lambda *_a: SimpleNamespace(
        ok=True, duration=8 * QUARTER, has_video=True, has_audio=True, error="", codec=lambda _kind: "h264"))
    monkeypatch.setattr(legacy, "sha256_file", lambda _p: "0" * 64)
    monkeypatch.setattr(legacy, "build_validation_receipt", lambda *_a: "{}")
    monkeypatch.setattr(size_policy, "check_stitch_output_size", lambda _p: None)
    manager = workers.WorkerManager()

    async def prepared(_path):
        return True

    manager._prepare_mp4 = prepared
    with env.scope() as db:
        fragments = list(db.scalars(select(RecordingFragment)).all())
    asyncio.run(workers.WorkerManager._stitch_fragment_group(manager, fragments))

    with env.scope() as db:
        recordings = list(db.scalars(select(Recording)).all())
        assert len(recordings) == 1 and recordings[0].duration_seconds == 8 * QUARTER
        mark = db.scalar(select(NsfwMark))
        assert mark.recording_id == recordings[0].id and mark.file_time == pytest.approx(3 * QUARTER + 600)
        moments = json.loads(recordings[0].nsfw_moments)
        assert moments and moments[0]["start"] == pytest.approx(3300.0)  # 55:00 of 2:00:00
        assert db.scalar(select(RecordingFragment)) is None
