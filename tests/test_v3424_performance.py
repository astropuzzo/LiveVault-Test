"""3.4.24: lighter hot paths (deferred nsfw_hits, idle uploader poll, pulse, thumbnails)."""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

import app.workers as workers_pkg
from app import main, nsfw_live_worker, nsfw_worker
from app.db import Base, Recording
from app.workers import WorkerManager


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'perf.db'}")
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def session_scope():
        session = maker()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    for module in (nsfw_worker, nsfw_live_worker, workers_pkg):
        monkeypatch.setattr(module, "db_session", session_scope)
    yield maker
    engine.dispose()


def _recording(maker, path: Path, **fields) -> int:
    with maker.begin() as session:
        rec = Recording(source_id=1, source_name="Demo", session_id="s1", local_path=str(path), filename=path.name,
                        started_at=datetime(2026, 9, 1, tzinfo=timezone.utc), integrity_status="passed",
                        upload_status="pending", **fields)
        session.add(rec)
        session.flush()
        return rec.id


def test_nsfw_hits_is_not_loaded_with_the_row_but_the_scan_job_has_it(factory, tmp_path):
    hits = json.dumps([{"t": 1.0, "score": 0.9}])
    rec_id = _recording(factory, tmp_path / "a.mp4", nsfw_hits=hits, nsfw_status="paused")
    with factory() as session:
        row = session.scalars(select(Recording)).one()
        assert "nsfw_hits" in inspect(row).unloaded  # not read by ordinary queries
    job = WorkerManager()._next_nsfw_job()
    assert job is not None and job.id == rec_id
    assert job.nsfw_hits == hits  # loaded before detaching: no DetachedInstanceError


def test_idle_uploader_poll_does_not_scan_fragments(factory, monkeypatch):
    manager = WorkerManager()
    monkeypatch.setattr(manager, "_oldest_eligible_fragment",
                        lambda: pytest.fail("idle poll must not load every fragment"))
    assert manager._pending_recording() is None


def test_uploader_still_yields_to_an_older_ready_fragment_batch(factory, tmp_path, monkeypatch):
    _recording(factory, tmp_path / "b.mp4")
    manager = WorkerManager()
    older = type("Fragment", (), {"started_at": datetime(2026, 8, 1)})()
    monkeypatch.setattr(manager, "_oldest_eligible_fragment", lambda: older)
    assert manager._pending_recording() is None


def test_thumbnail_url_check_accepts_inside_and_rejects_outside(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "settings", SimpleNamespace(data_dir=tmp_path))
    (tmp_path / "thumbnails").mkdir()
    inside = tmp_path / "thumbnails" / "1.jpg"
    inside.write_bytes(b"x")
    outside = tmp_path / "other.jpg"
    outside.write_bytes(b"x")
    assert main._safe_thumbnail_url(1, str(inside)) == "/api/recordings/1/thumbnail"
    assert main._safe_thumbnail_url(1, str(tmp_path / "thumbnails" / ".." / "other.jpg")) == ""
    assert main._safe_thumbnail_url(1, str(outside)) == ""
    assert main._safe_thumbnail_url(1, str(tmp_path / "thumbnails" / "missing.jpg")) == ""
    assert main._safe_thumbnail_url(1, "") == ""
