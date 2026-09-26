import asyncio
import json
import shutil
import subprocess
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import nsfw_live_worker, nsfw_worker, storage_handoff
from app.db import Base, NsfwCoverage, NsfwMark, Recording
from app.nsfw_live_worker import cluster_marks
from app.settings_store import RuntimeSettings
from app.workers import WorkerManager


def _mark(seconds, state="confirmed", cls="FEMALE_BREAST_EXPOSED", image=""):
    base = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)
    return SimpleNamespace(wall_at=base + timedelta(seconds=seconds), state=state, cls=cls, verified_cls="",
                           image=image, recording_id=None, file_time=None, id=seconds)


def test_cluster_marks_groups_by_wall_clock_and_keeps_strongest_label():
    moments = cluster_marks([_mark(0, "pending", image="a.jpg"), _mark(10), _mark(20, "inherited"),
                             _mark(200, "review", cls="BUTTOCKS_EXPOSED"), _mark(400, "rejected")], step=5)
    assert [(m["label"], m["count"], m["class"]) for m in moments] == [
        ("nsfw", 3, "FEMALE_BREAST_EXPOSED"), ("review", 1, "BUTTOCKS_EXPOSED")]
    assert moments[0]["image_url"] == "/api/nsfw/images/a.jpg"
    assert moments[0]["ended_at"] == "2026-09-24T20:00:25+00:00"


@pytest.fixture()
def live_env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'live.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    for module in (nsfw_live_worker, nsfw_worker):
        monkeypatch.setattr(module, "db_session", scope)
    monkeypatch.setattr(nsfw_worker, "settings", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(nsfw_live_worker, "load_average", lambda: 0.0)
    yield factory
    engine.dispose()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_live_capture_is_marked_while_growing_then_mapped_onto_the_stitched_file(tmp_path, monkeypatch, live_env):
    pytest.importorskip("onnxruntime")
    pytest.importorskip("onnx")
    from tests.test_nsfw_scan import _fake_model
    _fake_model(tmp_path / "fast.onnx", 320)
    _fake_model(tmp_path / "big.onnx", 640)
    full = tmp_path / "full.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:s=320x180:r=10:d=40", "-f", "lavfi",
                    "-i", "color=red:s=320x180:r=10:d=10", "-filter_complex", "[0][1]overlay=enable='between(t,10,19)'",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "10",
                    "-movflags", "+frag_keyframe+empty_moov+default_base_moof", str(full)], check=True)
    data = full.read_bytes()
    capture = tmp_path / "rec" / "demo_part001.capture.mp4"
    capture.parent.mkdir()
    capture.write_bytes(data[: len(data) // 3])  # the recorder is still writing

    cfg = RuntimeSettings(nsfw_enabled=True, nsfw_live_enabled=True, nsfw_fast_model=str(tmp_path / "fast.onnx"),
                          nsfw_verify_model=str(tmp_path / "big.onnx"), nsfw_step_seconds=2.0, nsfw_candidate=0.3,
                          nsfw_threshold=0.5, nsfw_live_fps=4, nsfw_live_max_load=99)
    for module in (nsfw_live_worker, nsfw_worker):
        monkeypatch.setattr(module, "runtime", lambda: cfg)
    mode = {"mode": "nvme"}
    monkeypatch.setattr(storage_handoff, "state", lambda: dict(mode))
    manager = WorkerManager()
    manager.active = {7: SimpleNamespace(session_id="s1", source_name="demo")}
    manager.active_capture_path = lambda source_id: capture
    manager._delete_uploaded_local_if_ready = lambda *_a: False
    manager._nsfw_live_init()

    async def drain():
        manager._nsfw_live_sync_tracks()
        while (job := manager._nsfw_live_pick()) is not None:
            await manager._nsfw_live_sample(*job)

    async def scenario():
        await drain()
        first_pass = manager._nsfw_live_tracks[7].samples
        # NVMe being detached: sampling waits, nothing is read.
        mode["mode"] = "quiesce"
        assert manager._nsfw_live_gate() == "storage_switch"
        # Recording continues on the internal buffer: sampling continues too.
        mode["mode"] = "buffer"
        assert manager._nsfw_live_gate() == ""
        capture.write_bytes(data)
        await drain()
        # Verification works on saved frames only (runs even during a switch).
        mode["mode"] = "quiesce"
        while True:
            with live_env() as db:
                if not db.scalar(select(NsfwMark).where(NsfwMark.state == "pending")):
                    break
            await manager._nsfw_verify_once()
        await manager._nsfw_sampler.close()
        await manager._nsfw_verifier.close()
        return first_pass

    first_pass = asyncio.run(scenario())
    track = manager._nsfw_live_tracks[7]
    assert 0 < first_pass < track.samples
    with live_env() as db:
        every = db.scalars(select(NsfwMark).order_by(NsfwMark.part_time)).all()
        marks = [m for m in every if m.state != "clear"]
        times = [round(m.part_time) for m in marks]
        assert times and min(times) >= 9 and max(times) <= 20
        states = {m.state for m in marks}
        assert "confirmed" in states and states <= {"confirmed", "inherited"}
        # The first clean sample after the red stretch is kept as the band's end.
        clears = [m for m in every if m.state == "clear"]
        assert len(clears) == 1 and 19 <= clears[0].part_time <= 24
        assert all(m.verify_image == "" for m in marks)  # verification copies cleaned up
        assert (tmp_path / "nsfw" / marks[0].image).is_file()
        coverage = db.get(NsfwCoverage, str(capture))
        assert coverage.covered_seconds >= 30

        rec = Recording(source_id=7, source_name="demo", session_id="s1", local_path=str(tmp_path / "final.mp4"),
                        filename="final.mp4", started_at=datetime.now(timezone.utc), integrity_status="passed",
                        upload_status="pending")
        db.add(rec)
        db.commit()
        rec_id = rec.id
    # The capture was the second part of the stitched file, 100 s in.
    manager.nsfw_attach_parts(rec_id, [(str(tmp_path / "earlier.mp4"), 0.0, 100.0), (str(capture), 100.0, 40.0)])
    with live_env() as db:
        rec = db.get(Recording, rec_id)
        # Part 1 was never sampled: coverage 40/140 < 85% → a full scan is still queued.
        assert rec.nsfw_status == "pending" and rec.nsfw_live_coverage == pytest.approx(40 / 140, abs=0.05)
        assert all(m.recording_id == rec_id and 109 <= m.file_time <= 124 for m in db.scalars(select(NsfwMark)).all())
        rec.nsfw_status = "pending"
        other = Recording(source_id=7, source_name="demo", session_id="s1", local_path=str(tmp_path / "solo.mp4"),
                          filename="solo.mp4", started_at=datetime.now(timezone.utc), integrity_status="passed",
                          upload_status="pending")
        db.add(other)
        db.commit()
        solo_id = other.id
        for mark in db.scalars(select(NsfwMark)).all():
            mark.recording_id = None
        db.add(NsfwCoverage(part_path=str(capture), source_id=7, samples=20, covered_seconds=40.0))
        db.commit()
    manager.nsfw_attach_parts(solo_id, [(str(capture), 0.0, 40.0)])
    with live_env() as db:
        solo = db.get(Recording, solo_id)
        assert solo.nsfw_source == "live" and solo.nsfw_status == "nsfw"
        moments = json.loads(solo.nsfw_moments)
        assert len(moments) == 1 and 9 <= moments[0]["start"] <= 11 and moments[0]["image"]


def test_each_moment_keeps_a_preview_even_if_its_first_frame_is_rejected(tmp_path, live_env):
    (tmp_path / "nsfw").mkdir()
    base = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)
    with live_env() as db:
        for index, (state, offset) in enumerate([("rejected", 0), ("confirmed", 5), ("inherited", 10), ("inherited", 50)]):
            name = f"live-7-{index}.jpg"
            (tmp_path / "nsfw" / name).write_bytes(b"jpg")
            db.add(NsfwMark(source_id=7, part_path="p", part_time=offset, wall_at=base + timedelta(seconds=offset),
                            state=state, image="" if state == "rejected" else name))
        db.commit()
        ids = [m.id for m in db.scalars(select(NsfwMark).order_by(NsfwMark.id)).all()]
    manager = WorkerManager()
    for mark_id in ids[1:]:
        manager._prune_preview(mark_id)
    with live_env() as db:
        kept = [m.image for m in db.scalars(select(NsfwMark).order_by(NsfwMark.id)).all()]
    # The first surviving frame keeps its picture, the next one inside 30 s is
    # pruned, a frame 40 s later (long stretch) gets its own again.
    assert kept == ["", "live-7-1.jpg", "", "live-7-3.jpg"]
    assert not (tmp_path / "nsfw" / "live-7-2.jpg").exists()


def test_marks_on_the_raw_capture_follow_the_remuxed_part(tmp_path, live_env, monkeypatch):
    """Stripchat samples <stem>.capture.mp4 live, then indexes the remuxed <stem>.mp4."""
    cfg = RuntimeSettings(nsfw_enabled=True, nsfw_step_seconds=5.0)
    monkeypatch.setattr(nsfw_live_worker, "runtime", lambda: cfg)
    raw = tmp_path / "002_demo_part001.capture.mp4"
    remuxed = tmp_path / "002_demo_part001.mp4"
    remuxed.write_bytes(b"x")
    base = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)
    with live_env() as db:
        db.add(NsfwMark(source_id=7, part_path=str(raw), part_time=30.0, wall_at=base, state="confirmed",
                        cls="FEMALE_GENITALIA_EXPOSED"))
        db.add(NsfwCoverage(part_path=str(raw), source_id=7, samples=50, covered_seconds=118.0))
        rec = Recording(source_id=7, source_name="demo", session_id="s1", local_path=str(remuxed),
                        filename=remuxed.name, started_at=base, integrity_status="passed", upload_status="pending")
        db.add(rec)
        db.commit()
        rec_id = rec.id
    manager = WorkerManager()
    manager._delete_uploaded_local_if_ready = lambda *_a: False
    manager.nsfw_attach_parts(rec_id, [(str(remuxed), 0.0, 120.0)])
    with live_env() as db:
        rec = db.get(Recording, rec_id)
        assert rec.nsfw_source == "live" and rec.nsfw_status == "nsfw"  # no full re-scan
        assert rec.nsfw_live_coverage == pytest.approx(118 / 120, abs=0.01)
        assert json.loads(rec.nsfw_moments)[0]["start"] == 30.0
        assert db.scalar(select(NsfwCoverage)) is None


def test_queued_full_scan_is_skipped_when_live_marks_cover_the_file(tmp_path, live_env, monkeypatch):
    cfg = RuntimeSettings(nsfw_enabled=True, nsfw_step_seconds=5.0)
    monkeypatch.setattr(nsfw_live_worker, "runtime", lambda: cfg)
    remuxed = tmp_path / "003_demo.mp4"
    remuxed.write_bytes(b"x")
    with live_env() as db:
        db.add(NsfwCoverage(part_path=str(tmp_path / "003_demo.capture.mp4"), source_id=7, samples=40, covered_seconds=60.0))
        rec = Recording(source_id=7, source_name="demo", session_id="s1", local_path=str(remuxed), filename=remuxed.name,
                        started_at=datetime.now(timezone.utc), duration_seconds=60.0, integrity_status="passed",
                        upload_status="pending", nsfw_status="pending")
        db.add(rec)
        db.commit()
        db.expunge(rec)
    manager = WorkerManager()
    manager._delete_uploaded_local_if_ready = lambda *_a: False
    manager._nsfw_command = lambda _rec: pytest.fail("full scan must not start")
    asyncio.run(manager._run_nsfw_job(rec))
    with live_env() as db:
        done = db.get(Recording, rec.id)
        assert done.nsfw_source == "live" and done.nsfw_status == "safe"


def test_band_lasts_until_a_clean_sample_not_a_fixed_gap():
    """3.4.16: every sample NSFW for 3 minutes = one band, ended by the first clean sample."""
    marks = [_mark(t) for t in range(0, 181, 8)] + [_mark(189, "clear"), _mark(300), _mark(305, "rejected")]
    bands = cluster_marks(marks, step=4)
    assert [(b["started_at"][11:19], b["ended_at"][11:19], b["count"]) for b in bands] == [
        ("20:00:00", "20:03:09", 23), ("20:05:00", "20:05:05", 1)]
