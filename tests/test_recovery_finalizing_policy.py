from pathlib import Path

from app.recovery_policy import finalizing_error_is_unrecoverable, recovery_quarantine_path


def test_irrecoverable_finalizing_mp4_is_classified_and_quarantined(tmp_path: Path):
    assert finalizing_error_is_unrecoverable('[mov,mp4] moov atom not found')
    assert finalizing_error_is_unrecoverable('Invalid data found when processing input')
    assert finalizing_error_is_unrecoverable('error reading header')
    assert not finalizing_error_is_unrecoverable('temporaneo: timeout durante ffprobe')

    temporary = tmp_path / '.clip.finalizing.mp4'
    temporary.write_bytes(b'incomplete')
    first = recovery_quarantine_path(temporary)
    assert first.name == '.clip.recovery-failed.mp4'
    first.write_bytes(b'older copy')
    second = recovery_quarantine_path(temporary)
    assert second.name == '.clip.recovery-failed-2.mp4'


def test_main_installs_non_repeating_finalizing_recovery_wrapper():
    facade = (Path(__file__).resolve().parents[1] / 'app/main/__init__.py').read_text(encoding='utf-8')
    assert '_recover_stale_finalizing_files_safe' in facade
    assert 'temporary.replace(quarantine)' in facade
    assert 'self.last_errors.pop(key, None)' in facade
    assert 'recovery-failed.mp4' not in facade  # naming policy stays centralized


def _isolated_db(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import db as db_module
    from app.db import Base
    engine = create_engine(f"sqlite:///{tmp_path / 'r.db'}")
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
    monkeypatch.setattr(db_module, "db_session", scope)
    return factory


def test_interrupted_stripchat_remux_is_a_duplicate_of_the_raw_capture(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from app.db import Recording
    from app.recovery_policy import quarantine_stem, redundant_copy_reason
    factory = _isolated_db(tmp_path, monkeypatch)
    q = tmp_path / ".Alicia_20260916_034203_767445_part001.recovery-failed-2.mp4"
    assert quarantine_stem(q) == "Alicia_20260916_034203_767445_part001"
    stem = quarantine_stem(q)
    assert redundant_copy_reason(tmp_path, stem) == ""  # nothing else: keep it
    (tmp_path / f"{stem}.capture.mp4").write_bytes(b"raw capture bytes")
    assert redundant_copy_reason(tmp_path, stem).startswith("originale presente")
    (tmp_path / f"{stem}.capture.mp4").unlink()
    with factory.begin() as db:
        db.add(Recording(source_id=1, source_name="a", session_id="s", local_path="/gone", filename=f"{stem}.capture.mp4",
                         started_at=datetime.now(timezone.utc), upload_status="uploaded", local_deleted=True))
    assert "già caricato" in redundant_copy_reason(tmp_path, stem)


def test_redundant_quarantine_is_purged_and_unique_copies_are_kept(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import app.main as facade
    from app import storage_handoff
    _isolated_db(tmp_path, monkeypatch)
    folder = tmp_path / "rec" / "alicia" / "session"
    folder.mkdir(parents=True)
    duplicate = folder / ".a_part001.recovery-failed.mp4"
    duplicate.write_bytes(b"x" * 100)
    (folder / ".a_part001.recovery-failed.mp4.txt").write_text("reason")
    (folder / "a_part001.capture.mp4").write_bytes(b"raw")
    unique = folder / ".b_part001.recovery-failed.mp4"
    unique.write_bytes(b"only copy")
    monkeypatch.setattr(facade, "_recovery_settings", SimpleNamespace(recordings_dir=tmp_path / "rec"))
    monkeypatch.setattr(storage_handoff, "media_online", lambda: True)
    manager = SimpleNamespace(_stopping=False, last_errors={})
    facade._purge_redundant_quarantine(manager)
    assert not duplicate.exists() and not (folder / ".a_part001.recovery-failed.mp4.txt").exists()
    assert unique.exists() and (folder / "a_part001.capture.mp4").exists()
    assert manager.recovery_purged_bytes == 100


def test_orphan_raw_capture_parts_in_a_session_folder_are_recovered(tmp_path, monkeypatch):
    import asyncio
    import json
    from contextlib import contextmanager
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import storage_handoff, workers
    from app.db import Base
    engine = create_engine(f"sqlite:///{tmp_path / 'o.db'}")
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
    monkeypatch.setattr(workers, "db_session", scope)
    root = tmp_path / "rec"
    folder = root / "AliciaBrooks" / "AliciaBrooks_2026-09-16_03-42-03"
    folder.mkdir(parents=True)
    (folder / workers.STITCH_MARKER_NAME).write_text(json.dumps({"source_id": 3, "session_id": "s16"}))
    raw = folder / "AliciaBrooks_2026-09-16_03-42-03_20260916_034203_767445_part002.capture.mp4"
    raw.write_bytes(b"raw")
    leftover = folder / "B_20260916_000000_000000_part001.capture.mp4"
    leftover.write_bytes(b"raw")
    (folder / "B_20260916_000000_000000_part001.mp4").write_bytes(b"remuxed")  # raw is a duplicate here
    monkeypatch.setattr(workers, "settings", SimpleNamespace(recordings_dir=root, data_dir=tmp_path))
    monkeypatch.setattr(storage_handoff, "checkpoint", lambda: None)
    manager = workers.WorkerManager()
    indexed = []

    async def fake_index(**kwargs):
        indexed.append(kwargs["path"].name)
        return True
    manager._index_fragment = fake_index
    asyncio.run(manager._recover_orphans())
    assert raw.name in indexed
    assert leftover.name not in indexed
    engine.dispose()
