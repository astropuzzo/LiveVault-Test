import contextlib
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import workers
from app import storage_handoff
from app.db import Base, Recording
from app.utils import generate_thumbnail


@pytest.fixture
def uploaded_recording(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'thumbnails.db'}")
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False)
    @contextlib.contextmanager
    def scope():
        with maker() as session:
            yield session
            session.commit()
    monkeypatch.setattr(workers, "db_session", scope)
    media = tmp_path / "ready.mp4"
    media.write_bytes(b"video-bytes")
    with maker.begin() as session:
        rec = Recording(source_id=1, source_name="Demo", session_id="s1", local_path=str(media), filename=media.name,
                        started_at=datetime.now(timezone.utc), upload_status="uploaded", thumbnail_status="pending")
        session.add(rec)
        session.flush()
        recording_id = rec.id
    manager = object.__new__(workers.WorkerManager)
    manager.last_errors = {}
    monkeypatch.setattr(manager, "nsfw_hold_blocks_delete", lambda _rec: False)
    yield maker, recording_id, media, manager
    engine.dispose()


def test_uploaded_file_is_retained_until_thumbnail_is_ready(uploaded_recording, monkeypatch):
    maker, recording_id, media, manager = uploaded_recording

    monkeypatch.setattr(workers, "runtime", lambda: SimpleNamespace(delete_after_upload=True, generate_thumbnails=True))
    assert manager._delete_uploaded_local_if_ready(recording_id, media) is False
    assert media.exists()
    with maker() as session:
        assert session.get(Recording, recording_id).local_deleted is False

    with maker.begin() as session:
        session.get(Recording, recording_id).thumbnail_status = "ready"
    assert manager._delete_uploaded_local_if_ready(recording_id, media) is True
    assert not media.exists()
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.local_deleted is True and rec.upload_status == "uploaded"


def test_thumbnail_processing_always_blocks_auto_delete_even_if_feature_was_disabled(uploaded_recording, monkeypatch):
    maker, recording_id, media, manager = uploaded_recording
    with maker.begin() as session:
        session.get(Recording, recording_id).thumbnail_status = "processing"

    monkeypatch.setattr(workers, "runtime", lambda: SimpleNamespace(delete_after_upload=True, generate_thumbnails=False))
    assert manager._delete_uploaded_local_if_ready(recording_id, media) is False
    assert media.exists()


def test_interrupted_thumbnail_job_is_requeued(monkeypatch):
    current = SimpleNamespace(thumbnail_status="processing", thumbnail_next_attempt_at=object(), thumbnail_error="")

    class FakeScalars:
        def all(self):
            return [current]

    @contextlib.contextmanager
    def fake_db():
        yield SimpleNamespace(scalars=lambda _query: FakeScalars())

    manager = object.__new__(workers.WorkerManager)
    monkeypatch.setattr(workers, "db_session", fake_db)
    manager._recover_interrupted_thumbnails()
    assert current.thumbnail_status == "pending"
    assert current.thumbnail_next_attempt_at is None
    assert "riavvio" in current.thumbnail_error.lower()


def test_thumbnail_ffmpeg_is_cancelled_by_storage_quiesce(tmp_path: Path, monkeypatch):
    media = tmp_path / "input.mp4"
    media.write_bytes(b"placeholder")
    output = tmp_path / "thumb.jpg"

    monkeypatch.setattr("app.utils._probe_duration", lambda *_a, **_k: 10.0)
    monkeypatch.setattr(
        "app.utils.storage_handoff.run_probe",
        lambda *_a, **_k: (_ for _ in ()).throw(storage_handoff.StorageQuiesced("quiesce")),
    )

    with pytest.raises(storage_handoff.StorageQuiesced):
        generate_thumbnail(media, output, 10.0)
    assert not output.exists()
    assert not list(tmp_path.glob(".thumb-*"))


def test_archive_ui_exposes_thumbnail_queue_states():
    script = (Path(__file__).parents[1] / "app" / "static" / "app.js").read_text()
    assert "Anteprima in coda" in script
    assert "Anteprima in corso" in script
    assert "Anteprima da riprovare" in script
