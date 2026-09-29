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


# ---- the same media indexed twice (raw .capture.mp4 + its remux) ----

def _index(manager, path):
    import asyncio
    return asyncio.run(manager._index_file(source_id=1, source_name="Demo", session_id="s1", path=path, started_at=None))


def _prepare_indexing(monkeypatch, manager, digest):
    from types import SimpleNamespace
    legacy = workers_pkg._legacy
    monkeypatch.setattr(legacy, "verify_media", lambda *_a: SimpleNamespace(
        ok=True, duration=75.0, has_video=True, has_audio=True, error="", codec=lambda _kind: "h264"))
    monkeypatch.setattr(legacy, "sha256_file", lambda _p: digest)
    monkeypatch.setattr(legacy, "build_validation_receipt", lambda *_a: "{}")

    async def stable(_path, timeout=12.0):
        return True

    async def prepared(_path):
        return True

    manager._wait_until_stable = stable
    manager._prepare_mp4 = prepared
    manager.nsfw_attach_parts = lambda *a, **k: None


def test_identical_content_is_indexed_once_and_the_copy_removed(factory, tmp_path, monkeypatch):
    manager = WorkerManager()
    _prepare_indexing(monkeypatch, manager, "a" * 64)
    first = tmp_path / "x_part001.mp4"
    first.write_bytes(b"video-bytes")
    second = tmp_path / "x_part001_copy.mp4"
    second.write_bytes(b"video-bytes")
    assert _index(manager, first) is True
    assert _index(manager, second) is True
    with factory() as session:
        rows = list(session.scalars(select(Recording)).all())
    assert [row.filename for row in rows] == ["x_part001.mp4"]
    assert first.exists() and not second.exists()


def test_duplicate_raw_capture_part_is_left_to_its_remux(factory, tmp_path, monkeypatch):
    manager = WorkerManager()
    _prepare_indexing(monkeypatch, manager, "b" * 64)
    done = tmp_path / "y_part001.mp4"
    done.write_bytes(b"same")
    raw = tmp_path / "y_part001.capture.mp4"
    raw.write_bytes(b"same")
    _index(manager, done)
    _index(manager, raw)
    with factory() as session:
        assert session.scalars(select(Recording)).all().__len__() == 1
    assert raw.exists()  # not ours to delete: the remux unlinks it


def test_different_content_of_the_same_size_is_kept(factory, tmp_path, monkeypatch):
    manager = WorkerManager()
    _prepare_indexing(monkeypatch, manager, "c" * 64)
    one = tmp_path / "z_part001.mp4"
    one.write_bytes(b"1111")
    _index(manager, one)
    _prepare_indexing(monkeypatch, manager, "d" * 64)
    two = tmp_path / "z_part002.mp4"
    two.write_bytes(b"2222")
    _index(manager, two)
    with factory() as session:
        assert session.scalars(select(Recording)).all().__len__() == 2


# ---- yt-dlp: default extractor list resolved once ----

def test_probe_ydl_registers_the_same_extractors_as_plain_yt_dlp():
    yt_dlp = pytest.importorskip("yt_dlp")
    from app import source_providers as providers

    cls = providers._legacy._probe_ydl_class() if hasattr(providers, "_legacy") else providers._probe_ydl_class()
    plain = yt_dlp.YoutubeDL({"quiet": True})
    first = cls({"quiet": True})
    second = cls({"quiet": True})  # served from the cached list
    assert list(first._ies) == list(plain._ies) == list(second._ies)
    # instances (the catch-all) are never shared between downloaders
    shared = [key for key, ie in first._ies.items() if not isinstance(ie, type) and second._ies[key] is ie]
    assert shared == []
    for key, ie in second._ies.items():
        if not isinstance(ie, type):
            assert ie._downloader is second
    # an explicit extractor filter bypasses the cache
    assert list(cls({"quiet": True, "allowed_extractors": []})._ies) == []
    first.close(); second.close(); plain.close()


# ---- TLS: ChaCha20 first (CM4 has no AES instructions) ----

def test_container_prefers_chacha20_for_tls():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    config = root / "app" / "openssl-chacha.cnf"
    assert "ENV OPENSSL_CONF=/app/app/openssl-chacha.cnf" in dockerfile
    text = config.read_text(encoding="utf-8")
    suites = next(line for line in text.splitlines() if line.startswith("Ciphersuites"))
    order = suites.split("=", 1)[1].strip().split(":")
    assert order[0] == "TLS_CHACHA20_POLY1305_SHA256"
    assert {"TLS_AES_256_GCM_SHA384", "TLS_AES_128_GCM_SHA256"} <= set(order)  # servers without ChaCha still work
    assert "MinProtocol = TLSv1.2" in text
