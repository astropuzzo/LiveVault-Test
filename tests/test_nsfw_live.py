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
        marks = db.scalars(select(NsfwMark).order_by(NsfwMark.part_time)).all()
        times = [round(m.part_time) for m in marks]
        assert times and min(times) >= 9 and max(times) <= 20
        states = {m.state for m in marks}
        assert "confirmed" in states and states <= {"confirmed", "inherited"}
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
        assert all(m.recording_id == rec_id and 109 <= m.file_time <= 120 for m in db.scalars(select(NsfwMark)).all())
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
