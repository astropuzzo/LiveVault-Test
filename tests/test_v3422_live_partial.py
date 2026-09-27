"""3.4.22-3.4.23: moments seen live are shown on the file even when the sampler
covered only part of it, and what was analysed live is not analysed again."""
import json
from datetime import timedelta

from sqlalchemy import select

import app.workers as workers
from app.db import NsfwCoverage, NsfwMark, Recording
from tests.test_v3412_nsfw_live_stitch import BASE, env  # noqa: F401  (fixture)


def _recording(db, tmp_path, name: str, **values) -> int:
    video = tmp_path / name
    video.write_bytes(b"media")
    fields = dict(source_id=25, source_name="tinnydoll", session_id="s1", local_path=str(video), filename=name,
                  started_at=BASE, duration_seconds=3600.0, integrity_status="passed", upload_status="uploaded",
                  nsfw_status="pending")
    fields.update(values)
    rec = Recording(**fields)
    db.add(rec)
    db.flush()
    return int(rec.id)


def _mark(db, part: str, at: float, state: str = "confirmed", recording_id=None) -> None:
    db.add(NsfwMark(source_id=25, part_path=part, part_time=at, wall_at=BASE + timedelta(seconds=at), state=state,
                    cls="FEMALE_GENITALIA_EXPOSED" if state != "clear" else "", verified_score=0.9,
                    recording_id=recording_id, file_time=at if recording_id else None))


def test_a_partly_covered_file_shows_its_live_moments_and_is_not_analysed_again(tmp_path, env):  # noqa: F811
    """tinnydoll 2026-09-27: three joined files covered 64-83% stayed "Da analizzare", no moments."""
    part = str(tmp_path / "tinnydoll_part001.mp4")
    with env.scope() as db:
        rec_id = _recording(db, tmp_path, "001_tinnydoll.mp4")
        for at in (600.0, 604.0, 608.0):
            _mark(db, part, at)
        _mark(db, part, 612.0, "clear")
        db.add(NsfwCoverage(part_path=part, source_id=25, samples=200, covered_seconds=2466.0))  # 68.5%
    manager = workers.WorkerManager()
    manager._delete_uploaded_local_if_ready = lambda *_a: False
    manager.nsfw_attach_parts(rec_id, [(part, 0.0, 3600.0)])
    with env.scope() as db:
        rec = db.get(Recording, rec_id)
        assert rec.nsfw_source == "live" and rec.nsfw_status == "nsfw"
        assert json.loads(rec.nsfw_moments)[0]["start"] == 600.0
        # User decision 2026-09-27: what was analysed live is not analysed again,
        # and the local copy is not held for it.
        assert not manager.nsfw_hold_blocks_delete(rec)
    assert manager._next_nsfw_job() is None


def test_nothing_seen_is_safe_only_on_a_file_sampled_for_at_least_half(tmp_path, env):  # noqa: F811
    manager = workers.WorkerManager()
    manager._delete_uploaded_local_if_ready = lambda *_a: False
    ids = {}
    with env.scope() as db:
        for name, covered in (("001_seen.mp4", 2160.0), ("002_barely.mp4", 1080.0)):  # 60%, 30%
            part = str(tmp_path / f"{name}.part")
            ids[name] = (_recording(db, tmp_path, name), part)
            _mark(db, part, 100.0, "rejected")
            db.add(NsfwCoverage(part_path=part, source_id=25, samples=100, covered_seconds=covered))
    for rec_id, part in ids.values():
        manager.nsfw_attach_parts(rec_id, [(part, 0.0, 3600.0)])
    with env.scope() as db:
        seen = db.get(Recording, ids["001_seen.mp4"][0])
        assert seen.nsfw_source == "live" and seen.nsfw_status == "safe"
        barely = db.get(Recording, ids["002_barely.mp4"][0])
        assert barely.nsfw_status == "pending" and barely.nsfw_source in ("", None)  # "safe" would be a guess


def test_files_left_pending_before_the_fix_publish_their_live_moments(tmp_path, env):  # noqa: F811
    with env.scope() as db:
        pending = _recording(db, tmp_path, "001_a.mp4", nsfw_live_coverage=0.685)
        gone = _recording(db, tmp_path, "002_a.mp4", nsfw_status="skipped", local_deleted=True, nsfw_live_coverage=0.64)
        excluded = _recording(db, tmp_path, "003_a.mp4", nsfw_status="skipped", nsfw_error="Esclusa dall'analisi")
        for rec_id in (pending, gone, excluded):
            _mark(db, f"/rec/{rec_id}.mp4", 300.0, recording_id=rec_id)
    manager = workers.WorkerManager()
    manager._delete_uploaded_local_if_ready = lambda *_a: False
    assert manager.nsfw_publish_live_left_pending() == 2
    with env.scope() as db:
        for rec_id in (pending, gone):
            rec = db.get(Recording, rec_id)
            assert rec.nsfw_source == "live" and rec.nsfw_status == "nsfw"
            assert json.loads(rec.nsfw_moments)[0]["start"] == 300.0
        assert db.get(Recording, excluded).nsfw_status == "skipped"  # the user's choice stays
        assert len(db.scalars(select(Recording).where(Recording.nsfw_source == "live")).all()) == 2
