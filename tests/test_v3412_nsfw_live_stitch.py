"""3.4.12: live NSFW marks reach the stitched file; full scans yield to captures."""
import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.workers as workers
from app import nsfw_live_worker, nsfw_worker, storage_handoff
from app.db import Base, NsfwCoverage, NsfwMark, Recording, RecordingFragment
from app.settings_store import RuntimeSettings
from app.workers import size_policy

legacy = workers._legacy
BASE = datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'lv.db'}")
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

    for module in (legacy, nsfw_live_worker, nsfw_worker):
        monkeypatch.setattr(module, "db_session", scope)
    cfg = RuntimeSettings(nsfw_enabled=True, nsfw_step_seconds=4.0, nsfw_threads=3, nsfw_only_when_idle=False,
                          nsfw_fast_model=str(tmp_path / "fast.onnx"), nsfw_verify_model=str(tmp_path / "big.onnx"))
    for module in (nsfw_live_worker, nsfw_worker):
        monkeypatch.setattr(module, "runtime", lambda: cfg)
    monkeypatch.setattr(nsfw_worker, "settings", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(storage_handoff, "state", lambda: {"mode": "nvme"})
    monkeypatch.setattr(storage_handoff, "media_online", lambda: True)
    yield SimpleNamespace(factory=factory, scope=scope, cfg=cfg)
    engine.dispose()


def _fragment(db, path: Path, started: datetime, seconds: float) -> None:
    path.write_bytes(b"media")
    db.add(RecordingFragment(source_id=25, source_name="AliciaBrooks", session_id="AliciaBrooks_s1",
                             local_path=str(path), filename=path.name, started_at=started,
                             finalized_at=started + timedelta(seconds=seconds), duration_seconds=seconds,
                             size_bytes=5, container_format="mp4", integrity_status="passed"))


def test_production_stitch_moves_live_marks_onto_the_file_and_skips_the_full_scan(tmp_path, env, monkeypatch):
    """The facade stitch (the one running in production) used to drop the live marks."""
    first = tmp_path / "AliciaBrooks_s1_20260925_090000_1_part001.mp4"   # Stripchat: sampled as .capture.mp4
    second = tmp_path / "AliciaBrooks_s1_20260925_090000_1_part002.mp4"
    durations = {first: 120.0, second: 60.0}
    with env.scope() as db:
        _fragment(db, first, BASE, 120)
        _fragment(db, second, BASE + timedelta(seconds=121), 60)
        raw = first.with_name(f"{first.stem}.capture.mp4")
        db.add(NsfwMark(source_id=25, part_path=str(raw), part_time=30.0, wall_at=BASE + timedelta(seconds=30),
                        state="confirmed", cls="FEMALE_BREAST_EXPOSED", verified_score=0.9))
        db.add(NsfwMark(source_id=25, part_path=str(second), part_time=10.0, wall_at=BASE + timedelta(seconds=131),
                        state="rejected"))
        db.add(NsfwCoverage(part_path=str(raw), source_id=25, samples=30, covered_seconds=110.0))
        db.add(NsfwCoverage(part_path=str(second), source_id=25, samples=15, covered_seconds=55.0))

    async def fake_stitch(paths, output, **_kwargs):
        output.write_bytes(b"stitched")

    monkeypatch.setattr(legacy, "stitch_recording_parts", fake_stitch)
    monkeypatch.setattr(legacy, "_safe_duration", lambda path: durations[Path(path)])
    monkeypatch.setattr(legacy, "verify_media", lambda *_a: SimpleNamespace(
        ok=True, duration=180.0, has_video=True, has_audio=True, error="", codec=lambda _kind: "h264"))
    monkeypatch.setattr(legacy, "sha256_file", lambda _p: "0" * 64)
    monkeypatch.setattr(legacy, "build_validation_receipt", lambda *_a: "{}")
    monkeypatch.setattr(size_policy, "check_stitch_output_size", lambda _p: None)
    manager = workers.WorkerManager()

    async def prepared(_path):
        return True

    manager._prepare_mp4 = prepared
    manager._delete_uploaded_local_if_ready = lambda *_a: False
    with env.scope() as db:
        fragments = list(db.scalars(select(RecordingFragment)).all())
    asyncio.run(workers.WorkerManager._stitch_fragment_group(manager, fragments))

    with env.scope() as db:
        rec = db.scalar(select(Recording))
        assert rec.nsfw_source == "live" and rec.nsfw_status == "nsfw"
        assert rec.nsfw_live_coverage == pytest.approx(165 / 180, abs=0.01)
        assert [(m["start"], m["label"]) for m in json.loads(rec.nsfw_moments)] == [(30.0, "nsfw")]
        marks = {m.part_time: m for m in db.scalars(select(NsfwMark)).all()}
        assert marks[30.0].recording_id == rec.id and marks[30.0].file_time == 30.0
        assert marks[10.0].recording_id == rec.id and marks[10.0].file_time == 130.0  # second part, 120 s in
        assert db.scalar(select(NsfwCoverage)) is None
    assert manager._next_nsfw_job() is None  # nothing left for the full-scan queue


def test_full_scan_waits_for_captures_when_live_analysis_is_on(env, monkeypatch):
    monkeypatch.setattr(nsfw_worker, "models_ready", lambda _cfg=None: (True, ""))
    manager = workers.WorkerManager()
    assert manager._nsfw_gate() == ""
    assert manager._nsfw_threads(env.cfg) == 3  # idle: the configured cores
    manager.active = {25: object()}
    assert manager._nsfw_gate() == "waiting_idle"  # even with "only when idle" off
    assert manager._nsfw_should_stop(threads=1) == "recording"


def test_without_live_analysis_a_full_scan_beside_captures_uses_one_core(env, monkeypatch):
    monkeypatch.setattr(nsfw_worker, "models_ready", lambda _cfg=None: (True, ""))
    manager = workers.WorkerManager()
    env.cfg.nsfw_live_enabled = False
    manager.active = {25: object()}
    assert manager._nsfw_gate() == ""
    assert manager._nsfw_threads(env.cfg) == 1
    assert manager._nsfw_should_stop(threads=3) == "recording"  # restart the helper on one core
    assert manager._nsfw_should_stop(threads=1) == ""
    command = manager._nsfw_command(SimpleNamespace(local_path="/x.mp4", nsfw_resume_at=0, id=1))
    assert command[command.index("--threads") + 1] == "1"


def test_live_helpers_always_run_on_one_core(env, monkeypatch):
    env.cfg.nsfw_threads = 4
    seen = {}

    class FakeProc:
        returncode = None
        stdout = SimpleNamespace(readline=lambda: asyncio.sleep(0, result=b'{"ready": true}\n'))

    async def fake_exec(*args, **_kwargs):
        seen["args"] = args
        return FakeProc()

    monkeypatch.setattr(nsfw_live_worker.asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(nsfw_live_worker.HelperProcess("verify").ensure()) is True
    args = list(seen["args"])
    assert args[args.index("--threads") + 1] == "1"


def test_live_coverage_counts_samples_within_the_moment_gap_as_continuous(env):
    manager = workers.WorkerManager()
    track = nsfw_live_worker.LiveTrack(25, "AliciaBrooks", "s1", path=Path("/rec/part001.capture.mp4"))
    # Four captures sharing 0.5 fps: one sample every ~8 s per capture, step 4 s.
    for at in (0.0, 8.0, 16.0, 24.0, 60.0):
        manager._nsfw_live_cover(track, at, 4.0)
    with env.scope() as db:
        row = db.get(NsfwCoverage, str(track.path))
        # 4 (first) + 8 + 8 + 8 + 10 (36 s gap capped at 2.5 x step)
        assert row.covered_seconds == pytest.approx(38.0)


def test_capture_end_line_keeps_the_reason_and_hides_signed_urls():
    session = SimpleNamespace(
        source_name="Top Twins", started_at=BASE, process=SimpleNamespace(returncode=255),
        stderr_tail=["[hls @ 0x1] skipping 36 segments ahead, expired from playlists",
                     "Opening 'https://edge.example/v/chunk_9.ts?token=secret&exp=1' for reading"])
    line = workers.capture_end_line(session, "riavvio: segmenti video scaduti", 3 * 1024**2, BASE + timedelta(seconds=23))
    assert "Top Twins" in line and "riavvio: segmenti video scaduti" in line and "23 s" in line
    assert "exit 255" in line and "skipping 36 segments ahead" in line
    assert "secret" not in line and "https://edge.example/v/chunk_9.ts?…" in line


def test_sampler_ignores_the_load_but_the_verifier_waits(env, monkeypatch):
    """3.4.13: pausing the cheap sampler on load cost live coverage; only the large model waits."""
    monkeypatch.setattr(nsfw_live_worker, "load_average", lambda: 9.0)
    env.cfg.nsfw_fast_model = __file__
    env.cfg.nsfw_verify_model = __file__
    manager = workers.WorkerManager()
    manager.active = {25: object()}
    assert manager._nsfw_live_gate() == ""
    with env.scope() as db:
        db.add(NsfwMark(source_id=25, part_path="/rec/p.mp4", wall_at=BASE, state="pending", fast_score=0.9))

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(nsfw_live_worker.asyncio, "sleep", no_wait)
    asyncio.run(manager._nsfw_verify_once())
    assert manager.nsfw_verify_state == "busy"
    with env.scope() as db:
        assert db.scalar(select(NsfwMark)).state == "pending"  # verified later, not dropped


def test_queued_full_scan_keeps_the_live_coverage_measured_at_stitch(tmp_path, env):
    """3.4.14: the 3.4.10 re-match ran on the stitched name, found nothing and reset 0.732 to 0."""
    import sys
    video = tmp_path / "001_wasianbby_2026-09-25_11-24-51.mp4"
    video.write_bytes(b"media")
    with env.scope() as db:
        rec = Recording(source_id=32, source_name="wasianbby", session_id="s1", local_path=str(video),
                        filename=video.name, started_at=BASE, duration_seconds=2269.0, integrity_status="passed",
                        upload_status="uploaded", nsfw_status="pending", nsfw_live_coverage=0.732)
        db.add(rec)
        db.flush()
        db.expunge(rec)
    manager = workers.WorkerManager()
    manager.nsfw_attach_parts = lambda *_a: pytest.fail("coverage already measured at stitch")
    manager._nsfw_command = lambda _rec: [sys.executable, "-c", 'print(\'{"type": "error", "error": "stop"}\')']
    asyncio.run(manager._run_nsfw_job(rec))
    with env.scope() as db:
        assert db.get(Recording, rec.id).nsfw_live_coverage == pytest.approx(0.732)


def test_full_scan_waits_for_the_captures_to_resume_after_a_restart(env, monkeypatch):
    import time
    monkeypatch.setattr(nsfw_worker, "models_ready", lambda _cfg=None: (True, ""))
    manager = workers.WorkerManager()
    manager._nsfw_ready_at = time.monotonic() + 60  # set by _nsfw_loop at start
    assert manager._nsfw_gate() == "waiting_idle"  # no capture yet: the poller is still resuming them
    env.cfg.nsfw_live_enabled = False
    assert manager._nsfw_gate() == ""  # no live analysis and "only when idle" off: nothing to protect


def test_a_capture_stopped_by_the_user_is_logged_as_such(monkeypatch):
    manager = workers.WorkerManager()
    session = SimpleNamespace(stop_reason="", process=SimpleNamespace(returncode=None))
    manager.active = {32: session}

    async def fake_stop(target):
        target.process.returncode = 255

    monkeypatch.setattr(legacy, "stop_recorder", fake_stop)
    asyncio.run(manager.stop_source(32))
    assert session.stop_reason == "fermata manuale"
    session.stop_reason = ""
    asyncio.run(manager.stop_all_recordings("cambio storage"))
    assert session.stop_reason == "cambio storage"
