import asyncio
import json
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import nsfw_live_worker, nsfw_worker, storage_handoff
from app.db import Base, Recording
from app.settings_store import RuntimeSettings
from app.workers import WorkerManager


def _cfg(tmp_path: Path, **overrides) -> RuntimeSettings:
    cfg = RuntimeSettings(nsfw_enabled=True, nsfw_fast_model=str(tmp_path / "fast.onnx"),
                          nsfw_verify_model=str(tmp_path / "big.onnx"), nsfw_threshold=0.5)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'nsfw.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    # _run_nsfw_job first retries the live-mark match (nsfw_live_worker).
    for module in (nsfw_worker, nsfw_live_worker):
        monkeypatch.setattr(module, "db_session", session_scope)
    yield factory
    engine.dispose()


def _recording(factory, path: Path, **fields) -> int:
    with factory.begin() as session:
        rec = Recording(source_id=1, source_name="Demo", session_id="s1", local_path=str(path), filename=path.name,
                        started_at=datetime(2026, 9, 1, tzinfo=timezone.utc), integrity_status="passed",
                        upload_status="pending", **fields)
        session.add(rec)
        session.flush()
        return rec.id


def test_hold_keeps_uploaded_file_until_scanned_but_never_forever(tmp_path, monkeypatch):
    (tmp_path / "fast.onnx").write_bytes(b"x")
    (tmp_path / "big.onnx").write_bytes(b"x")
    pytest.importorskip("onnxruntime")
    manager = WorkerManager()
    cfg = _cfg(tmp_path, nsfw_max_hold_hours=24)
    monkeypatch.setattr(nsfw_worker, "runtime", lambda: cfg)
    monkeypatch.setattr("app.storage.disk_state", lambda *a: SimpleNamespace(pressure="ok"))
    rec = SimpleNamespace(nsfw_status="pending", uploaded_at=datetime.now(timezone.utc) - timedelta(hours=1))
    assert manager.nsfw_hold_blocks_delete(rec) is True
    rec.uploaded_at = datetime.now(timezone.utc) - timedelta(hours=30)
    assert manager.nsfw_hold_blocks_delete(rec) is False  # max hold elapsed
    rec.uploaded_at = datetime.now(timezone.utc)
    monkeypatch.setattr("app.storage.disk_state", lambda *a: SimpleNamespace(pressure="warning"))
    assert manager.nsfw_hold_blocks_delete(rec) is False  # disk pressure wins
    monkeypatch.setattr("app.storage.disk_state", lambda *a: SimpleNamespace(pressure="ok"))
    rec.nsfw_status = "safe"
    assert manager.nsfw_hold_blocks_delete(rec) is False
    rec.nsfw_status = "pending"
    cfg.nsfw_enabled = False
    assert manager.nsfw_hold_blocks_delete(rec) is False


def test_gate_reports_why_the_queue_waits(tmp_path, monkeypatch):
    manager = WorkerManager()
    cfg = _cfg(tmp_path, nsfw_enabled=False)
    monkeypatch.setattr(nsfw_worker, "runtime", lambda: cfg)
    monkeypatch.setattr(storage_handoff, "media_online", lambda: True)
    assert manager._nsfw_gate() == "disabled"
    cfg.nsfw_enabled = True
    assert manager._nsfw_gate() == "models_missing"
    assert "fast.onnx" in manager.nsfw_detail
    manager.active = {1: object()}
    assert manager._nsfw_gate() == "waiting_idle"
    monkeypatch.setattr(storage_handoff, "media_online", lambda: False)
    assert manager._nsfw_gate() == "waiting_storage"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_job_runs_scanner_saves_moments_and_pauses_on_storage_quiesce(tmp_path, monkeypatch, isolated_db):
    pytest.importorskip("onnxruntime")
    pytest.importorskip("onnx")
    import subprocess
    from tests.test_nsfw_scan import _fake_model
    _fake_model(tmp_path / "fast.onnx", 320)
    _fake_model(tmp_path / "big.onnx", 640)
    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:s=320x180:r=10:d=40", "-f", "lavfi",
                    "-i", "color=red:s=320x180:r=10:d=10", "-filter_complex", "[0][1]overlay=enable='between(t,10,19)'",
                    "-c:v", "libx264", "-g", "10", str(video)], check=True)
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(nsfw_worker, "runtime", lambda: cfg)
    monkeypatch.setattr(nsfw_worker, "settings", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(storage_handoff, "media_online", lambda: True)
    monkeypatch.setattr(storage_handoff, "state", lambda: {"mode": "nvme"})
    manager = WorkerManager()
    manager._delete_uploaded_local_if_ready = lambda *_a: False
    rec_id = _recording(isolated_db, video)

    job = manager._next_nsfw_job()
    assert job is not None and job.id == rec_id
    asyncio.run(manager._run_nsfw_job(job))
    with isolated_db() as session:
        rec = session.get(Recording, rec_id)
        assert rec.nsfw_status == "nsfw"
        moments = json.loads(rec.nsfw_moments)
        assert [(m["start"], m["label"]) for m in moments] == [(10.0, "nsfw")]
        assert moments[0]["image"] and (tmp_path / "nsfw" / moments[0]["image"]).is_file()
        assert rec.nsfw_progress == 1.0 and rec.nsfw_scanned_at is not None
    assert manager.nsfw_current is None

    # An NVMe eject while scanning: child stopped, position kept, job paused.
    second = _recording(isolated_db, video)
    monkeypatch.setattr(storage_handoff, "state", lambda: {"mode": "quiesce"})
    job = manager._next_nsfw_job()
    assert job.id == second
    asyncio.run(manager._run_nsfw_job(job))  # media_job swallows StorageQuiesced
    with isolated_db() as session:
        assert session.get(Recording, second).nsfw_status == "paused"
    assert manager._storage_jobs == 0


def test_modified_file_after_scan_is_rescanned(tmp_path, monkeypatch, isolated_db):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"one")
    sig = nsfw_worker.file_signature(video)
    rec_id = _recording(isolated_db, video, nsfw_status="safe", nsfw_file_sig=sig)
    manager = WorkerManager()
    assert manager._next_nsfw_job() is None
    video.write_bytes(b"remuxed-file")  # conversion / repair after the scan
    job = manager._next_nsfw_job()
    assert job is not None and job.id == rec_id
    with isolated_db() as session:
        assert session.get(Recording, rec_id).nsfw_status == "pending"


def test_gofile_gets_one_subfolder_per_video_with_fallback(monkeypatch):
    from app import workers
    calls = []

    def fake_create(name, parent):
        calls.append((name, parent))
        if name == "broken":
            raise RuntimeError("Gofile 500")
        return f"sub-{name}", f"https://gofile.io/d/{name}"

    cfg = RuntimeSettings(gofile_subfolder_per_video=True)
    monkeypatch.setattr(workers, "runtime", lambda: cfg)
    monkeypatch.setattr(workers, "create_gofile_folder", fake_create)
    manager = WorkerManager()
    rec = SimpleNamespace(id=7, filename="aurora_20260924_2100.mp4")
    first = asyncio.run(manager._gofile_file_folder(rec, "day-1", "011_Aurora_2026-09-24_21-00-00"))
    again = asyncio.run(manager._gofile_file_folder(rec, "day-1", "011_Aurora_2026-09-24_21-00-00"))  # retry reuses it
    assert first == again == ("sub-011_Aurora_2026-09-24_21-00-00", "https://gofile.io/d/011_Aurora_2026-09-24_21-00-00")
    assert calls == [("011_Aurora_2026-09-24_21-00-00", "day-1")]  # uploaded name, not the capture name
    # Gofile error → empty ids: the upload goes to the day folder as before.
    assert asyncio.run(manager._gofile_file_folder(SimpleNamespace(id=8, filename="broken.mp4"), "day-1")) == ("", "")
    cfg.gofile_subfolder_per_video = False
    assert asyncio.run(manager._gofile_file_folder(SimpleNamespace(id=9, filename="x.mp4"), "day-1")) == ("", "")
