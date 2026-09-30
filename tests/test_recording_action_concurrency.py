import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main, workers
from app.db import Base, Recording


@pytest.fixture()
def recording(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'actions.db'}")
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def scope():
        with maker() as session:
            yield session
            session.commit()

    monkeypatch.setattr(main, "db_session", scope)
    monkeypatch.setattr(workers, "db_session", scope)
    monkeypatch.setattr(main, "require_auth", lambda _request: None)
    cfg = SimpleNamespace(integrity_mode="quick", generate_thumbnails=True, delete_after_upload=True,
                          upload_paused=False, max_upload_attempts=3)
    monkeypatch.setattr(main, "runtime", lambda: cfg)
    monkeypatch.setattr(workers, "runtime", lambda: cfg)
    monkeypatch.setattr(workers.storage_handoff, "media_online", lambda: True)
    monkeypatch.setattr(main, "settings", SimpleNamespace(recordings_dir=tmp_path, data_dir=tmp_path))
    monkeypatch.setattr(main, "set_values", lambda *_args: None)
    monkeypatch.setattr(main.manager, "clear_retry_backoff", lambda: None)
    monkeypatch.setattr(main.manager, "wake", lambda: None)
    monkeypatch.setattr(main.manager, "_repairing_recordings", set())
    monkeypatch.setattr(main.manager, "nsfw_hold_blocks_delete", lambda _rec: False)
    monkeypatch.setattr(main, "verify_media", lambda *_args: SimpleNamespace(
        ok=True, duration=1.0, has_video=True, has_audio=True, error="", codec=lambda _kind: "h264"))
    monkeypatch.setattr(main, "sha256_file", lambda *_args: "a" * 64)
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"media")
    with maker.begin() as session:
        rec = Recording(source_id=1, source_name="Demo", session_id="s1", local_path=str(path), filename=path.name,
                        started_at=datetime(2026, 9, 29, tzinfo=timezone.utc), upload_status="failed",
                        integrity_status="passed", sha256="a" * 64, upload_attempts=2)
        session.add(rec)
        session.flush()
        recording_id = rec.id
    yield maker, recording_id
    engine.dispose()


@pytest.mark.parametrize("action", ["recover_recording", "recheck_integrity", "delete_local_recording"])
def test_active_scan_keeps_original_bytes(recording, action):
    factory, recording_id = recording
    with factory.begin() as session:
        session.get(Recording, recording_id).nsfw_status = "scanning"
    with pytest.raises(HTTPException) as caught:
        if action == "delete_local_recording":
            main.delete_local_recording(recording_id, None, force=True)
        else:
            asyncio.run(getattr(main, action)(recording_id, None))
    assert caught.value.status_code == 409
    with factory() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == "failed" and rec.nsfw_status == "scanning"
        from pathlib import Path
        assert Path(rec.local_path).read_bytes() == b"media"


@pytest.mark.parametrize("status", ["uploading", "converting", "deleting"])
@pytest.mark.parametrize("action", ["upload_now", "retry_recording"])
def test_queue_actions_cannot_reopen_busy_media(recording, status, action):
    maker, recording_id = recording
    with maker.begin() as session:
        session.get(Recording, recording_id).upload_status = status
    with pytest.raises(HTTPException) as failure:
        getattr(main, action)(recording_id, None)
    assert failure.value.status_code == 409
    assert failure.value.detail["code"] == "recording_busy"
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == status and rec.upload_attempts == 2


def test_failed_queue_action_keeps_existing_retry_behavior(recording):
    maker, recording_id = recording
    assert main.upload_now(recording_id, None)["ok"]
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == "pending" and rec.upload_attempts == 0 and rec.upload_priority == 100


def test_recover_excludes_other_media_actions_but_allows_abandoned_conversion(recording, monkeypatch):
    maker, recording_id = recording
    with maker.begin() as session:
        session.get(Recording, recording_id).upload_status = "converting"  # previous interrupted process

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        async def prepare(_path):
            started.set()
            await release.wait()
            return False
        monkeypatch.setattr(main.manager, "_prepare_mp4", prepare)
        first = asyncio.create_task(main.recover_recording(recording_id, None))
        await started.wait()
        for action in (main.recover_recording, main.convert_mp4, main.recheck_integrity):
            with pytest.raises(HTTPException) as failure:
                await action(recording_id, None)
            assert failure.value.status_code == 409
        for action in (main.upload_now, main.retry_recording):
            with pytest.raises(HTTPException) as failure:
                action(recording_id, None)
            assert failure.value.status_code == 409
        for action, kwargs in ((main.delete_local_recording, {"force": True}),
                               (main.delete_recording, {}), (main.delete_recording, {"delete_file": False})):
            with pytest.raises(HTTPException) as failure:
                action(recording_id, None, **kwargs)
            assert failure.value.status_code == 409
        release.set()
        assert (await first)["ok"]
    asyncio.run(run())
    with maker() as session:
        assert session.get(Recording, recording_id).upload_status == "pending"


def test_recover_does_not_overlap_active_worker_repair(recording):
    maker, recording_id = recording
    with maker.begin() as session:
        session.get(Recording, recording_id).upload_status = "converting"
    main.manager._repairing_recordings.add(recording_id)
    with pytest.raises(HTTPException) as failure:
        asyncio.run(main.recover_recording(recording_id, None))
    assert failure.value.status_code == 409


def test_compare_and_set_refuses_a_worker_state_change(recording):
    maker, recording_id = recording
    with maker() as stale:
        rec = stale.get(Recording, recording_id)
        with maker.begin() as current:
            current.get(Recording, recording_id).upload_status = "uploading"
        with pytest.raises(HTTPException) as failure:
            main._change_recording_state(stale, rec, upload_status="converting")
        assert failure.value.status_code == 409
    with maker() as session:
        assert session.get(Recording, recording_id).upload_status == "uploading"


def test_integrity_temporarily_claims_file_and_preserves_uploaded_state(recording, monkeypatch):
    maker, recording_id = recording
    with maker.begin() as session:
        session.get(Recording, recording_id).upload_status = "uploaded"
    states = []
    def verify(*_args):
        with maker() as session:
            states.append(session.get(Recording, recording_id).upload_status)
        return SimpleNamespace(ok=True, duration=1.0, has_video=True, has_audio=True, error="", codec=lambda _kind: "h264")
    monkeypatch.setattr(main, "verify_media", verify)
    assert asyncio.run(main.recheck_integrity(recording_id, None))["ok"]
    assert states == ["converting"]
    with maker() as session:
        assert session.get(Recording, recording_id).upload_status == "uploaded"


def test_integrity_failure_releases_claim_on_exception(recording, monkeypatch):
    maker, recording_id = recording
    def broken(*_args):
        raise OSError("read failed")
    monkeypatch.setattr(main, "verify_media", broken)
    with pytest.raises(OSError):
        asyncio.run(main.recheck_integrity(recording_id, None))
    with maker() as session:
        assert session.get(Recording, recording_id).upload_status == "failed"


@pytest.mark.parametrize("action, kwargs", [(main.delete_local_recording, {"force": True}),
                                            (main.delete_recording, {}),
                                            (main.delete_recording, {"delete_file": False})])
@pytest.mark.parametrize("status", ["uploading", "converting", "deleting"])
def test_deletion_keeps_busy_media_and_archive(recording, action, kwargs, status):
    maker, recording_id = recording
    with maker.begin() as session:
        rec = session.get(Recording, recording_id)
        rec.upload_status = status
        path = main.Path(rec.local_path)
    with pytest.raises(HTTPException) as failure:
        action(recording_id, None, **kwargs)
    assert failure.value.status_code == 409
    assert path.read_bytes() == b"media"
    with maker() as session:
        assert session.get(Recording, recording_id).upload_status == status


def test_uploaded_local_deletion_claims_before_unlink_and_restores_cloud_state(recording, monkeypatch):
    maker, recording_id = recording
    with maker.begin() as session:
        session.get(Recording, recording_id).upload_status = "uploaded"
    unlink = main.safe_unlink
    observed = []
    def inspect_unlink(path, root):
        with maker() as session:
            observed.append(session.get(Recording, recording_id).upload_status)
        for action in (main.upload_now, main.retry_recording):
            with pytest.raises(HTTPException) as failure:
                action(recording_id, None)
            assert failure.value.status_code == 409
        assert main.manager._pending_recording() is None
        assert main.manager._next_thumbnail_job() is None
        return unlink(path, root)
    monkeypatch.setattr(main, "safe_unlink", inspect_unlink)
    assert main.delete_local_recording(recording_id, None)["removed"]
    assert observed == ["deleting"]
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == "uploaded" and rec.local_deleted
        assert not main.Path(rec.local_path).exists()


def test_archive_only_deletion_refuses_a_worker_claim_after_initial_read(recording, monkeypatch):
    maker, recording_id = recording
    claim = main._claim_recording_deletion
    def worker_wins(db, stale):
        with maker.begin() as session:
            session.get(Recording, recording_id).upload_status = "uploading"
        return claim(db, stale)
    monkeypatch.setattr(main, "_claim_recording_deletion", worker_wins)
    with pytest.raises(HTTPException) as failure:
        main.delete_recording(recording_id, None, delete_file=False)
    assert failure.value.status_code == 409
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == "uploading" and main.Path(rec.local_path).exists()


def test_local_delete_compare_and_set_keeps_bytes_if_worker_claims_first(recording, monkeypatch):
    maker, recording_id = recording
    claim = main._claim_recording_deletion
    def worker_wins(db, stale):
        with maker.begin() as session:
            session.get(Recording, recording_id).upload_status = "converting"
        return claim(db, stale)
    monkeypatch.setattr(main, "_claim_recording_deletion", worker_wins)
    with pytest.raises(HTTPException) as failure:
        main.delete_local_recording(recording_id, None, force=True)
    assert failure.value.status_code == 409
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == "converting" and main.Path(rec.local_path).read_bytes() == b"media"


def test_batch_cleanup_obeys_active_manual_operation(recording):
    maker, recording_id = recording
    with main._recording_action(recording_id):
        with pytest.raises(HTTPException) as failure:
            main._remove_local_copy(recording_id, force=True)
    assert failure.value.status_code == 409
    with maker() as session:
        assert main.Path(session.get(Recording, recording_id).local_path).exists()


def test_auto_delete_claim_blocks_manual_recovery_until_bytes_are_removed(recording, monkeypatch):
    maker, recording_id = recording
    with maker.begin() as session:
        rec = session.get(Recording, recording_id)
        rec.upload_status = "uploaded"
        rec.thumbnail_status = "ready"
        path = main.Path(rec.local_path)
    unlink = main.Path.unlink
    def interleave(target, *args, **kwargs):
        if target == path:
            with maker() as session:
                assert session.get(Recording, recording_id).upload_status == "deleting"
            with pytest.raises(HTTPException) as failure:
                asyncio.run(main.recover_recording(recording_id, None))
            assert failure.value.status_code == 409
        return unlink(target, *args, **kwargs)
    monkeypatch.setattr(main.Path, "unlink", interleave)
    assert main.manager._delete_uploaded_local_if_ready(recording_id, path)
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == "uploaded" and rec.local_deleted


def test_thumbnail_cannot_claim_after_deletion_wins(recording, monkeypatch):
    maker, recording_id = recording
    with maker.begin() as session:
        session.get(Recording, recording_id).thumbnail_status = "pending"
    @contextmanager
    def interleaved_scope():
        with maker() as db:
            scalar = db.scalar
            def select_then_delete(statement):
                stale = scalar(statement)
                if stale:
                    with maker.begin() as other:
                        other.get(Recording, recording_id).upload_status = "deleting"
                return stale
            db.scalar = select_then_delete
            yield db
            db.commit()
    monkeypatch.setattr(workers, "db_session", interleaved_scope)
    assert main.manager._next_thumbnail_job() is None
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == "deleting" and rec.thumbnail_status == "pending"


@pytest.mark.parametrize("status", ["failed", "uploaded"])
def test_local_delete_failure_releases_claim(recording, monkeypatch, status):
    maker, recording_id = recording
    with maker.begin() as session:
        session.get(Recording, recording_id).upload_status = status
    monkeypatch.setattr(main, "safe_unlink", lambda *_args: (_ for _ in ()).throw(OSError("device busy")))
    with pytest.raises(HTTPException) as failure:
        main.delete_local_recording(recording_id, None, force=True)
    assert failure.value.status_code == 500
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == status and not rec.local_deleted
        assert main.Path(rec.local_path).exists()


@pytest.mark.parametrize("delete_file", [True, False])
def test_archive_delete_removes_only_requested_bytes(recording, delete_file):
    maker, recording_id = recording
    with maker() as session:
        path = main.Path(session.get(Recording, recording_id).local_path)
    result = main.delete_recording(recording_id, None, delete_file=delete_file)
    assert result["entry_deleted"] and result["file_removed"] is delete_file
    assert path.exists() is not delete_file
    with maker() as session:
        assert session.get(Recording, recording_id) is None


@pytest.mark.parametrize("has_receipt", [True, False])
@pytest.mark.parametrize("exists", [True, False])
def test_interrupted_deletion_reconciles_presence_without_removing_bytes(recording, has_receipt, exists):
    maker, recording_id = recording
    with maker.begin() as session:
        rec = session.get(Recording, recording_id)
        rec.upload_status = "deleting"
        if has_receipt:
            rec.remote_url = "https://example.test/verified"
            rec.uploaded_at = datetime(2026, 9, 29, tzinfo=timezone.utc)
        path = main.Path(rec.local_path)
    if not exists:
        path.unlink()
    main.manager._recover_interrupted_uploads()
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == ("uploaded" if has_receipt else "discarded")
        assert rec.local_deleted is not exists
        assert path.exists() is exists


def test_interrupted_deletion_does_not_confuse_offline_storage_with_removed_bytes(recording, monkeypatch):
    maker, recording_id = recording
    with maker.begin() as session:
        session.get(Recording, recording_id).upload_status = "deleting"
    monkeypatch.setattr(workers.storage_handoff, "media_online", lambda: False)
    main.manager._recover_interrupted_uploads()
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == "deleting" and not rec.local_deleted


def test_auto_delete_loses_compare_and_set_to_manual_recovery(recording, monkeypatch):
    maker, recording_id = recording
    with maker.begin() as session:
        rec = session.get(Recording, recording_id)
        rec.upload_status = "uploaded"
        rec.thumbnail_status = "ready"
        path = main.Path(rec.local_path)
    @contextmanager
    def interleaved_scope():
        with maker() as db:
            get = db.get
            def read_then_recover(model, identity):
                stale = get(model, identity)
                with maker.begin() as other:
                    other.get(Recording, recording_id).upload_status = "converting"
                return stale
            db.get = read_then_recover
            yield db
            db.commit()
    monkeypatch.setattr(workers, "db_session", interleaved_scope)
    assert main.manager._delete_uploaded_local_if_ready(recording_id, path) is False
    assert path.read_bytes() == b"media"
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == "converting" and not rec.local_deleted


def test_auto_delete_failure_releases_claim_without_losing_cloud_state(recording, monkeypatch):
    maker, recording_id = recording
    with maker.begin() as session:
        rec = session.get(Recording, recording_id)
        rec.upload_status = "uploaded"
        rec.thumbnail_status = "ready"
        path = main.Path(rec.local_path)
    monkeypatch.setattr(main.Path, "unlink", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("device busy")))
    assert main.manager._delete_uploaded_local_if_ready(recording_id, path) is False
    with maker() as session:
        rec = session.get(Recording, recording_id)
        assert rec.upload_status == "uploaded" and not rec.local_deleted
        assert path.exists()
