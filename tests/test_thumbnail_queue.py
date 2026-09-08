import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import workers
from app import storage_handoff
from app.utils import generate_thumbnail


@contextlib.contextmanager
def _db_returning(current):
    yield SimpleNamespace(get=lambda _model, _id: current)


def test_uploaded_file_is_retained_until_thumbnail_is_ready(tmp_path: Path, monkeypatch):
    media = tmp_path / "ready.mp4"
    media.write_bytes(b"video-bytes")
    current = SimpleNamespace(local_deleted=False, upload_status="uploaded", thumbnail_status="pending")
    manager = object.__new__(workers.WorkerManager)
    manager.last_errors = {}

    monkeypatch.setattr(workers, "runtime", lambda: SimpleNamespace(delete_after_upload=True, generate_thumbnails=True))
    monkeypatch.setattr(workers, "db_session", lambda: _db_returning(current))

    assert manager._delete_uploaded_local_if_ready(1, media) is False
    assert media.exists()
    assert current.local_deleted is False

    current.thumbnail_status = "ready"
    assert manager._delete_uploaded_local_if_ready(1, media) is True
    assert not media.exists()
    assert current.local_deleted is True


def test_thumbnail_processing_always_blocks_auto_delete_even_if_feature_was_disabled(tmp_path: Path, monkeypatch):
    media = tmp_path / "processing.mp4"
    media.write_bytes(b"video-bytes")
    current = SimpleNamespace(local_deleted=False, upload_status="uploaded", thumbnail_status="processing")
    manager = object.__new__(workers.WorkerManager)
    manager.last_errors = {}

    monkeypatch.setattr(workers, "runtime", lambda: SimpleNamespace(delete_after_upload=True, generate_thumbnails=False))
    monkeypatch.setattr(workers, "db_session", lambda: _db_returning(current))

    assert manager._delete_uploaded_local_if_ready(2, media) is False
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
