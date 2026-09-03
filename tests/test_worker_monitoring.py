import asyncio
import inspect
from contextlib import contextmanager
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import workers
from app.db import Base, LiveSession, Profile, Source
from app.workers import WorkerManager


def test_global_recording_pause_does_not_pause_source_monitoring():
    poll_loop = inspect.getsource(WorkerManager._poll_loop)
    source_check = inspect.getsource(WorkerManager._check_source_unlocked)

    assert "recording_paused" not in poll_loop
    assert "Source.enabled.is_(True)" not in poll_loop
    assert "cfg.recording_paused" in source_check
    assert "_observe_live_state" in source_check


def test_inflight_probe_cannot_start_after_source_is_paused(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker-race.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def isolated_session():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with factory.begin() as session:
        profile = Profile(display_name="Race", favorite=False, notes="")
        session.add(profile)
        session.flush()
        source = Source(
            profile_id=profile.id,
            name="race",
            slug="race",
            enabled=True,
            consent_confirmed=True,
        )
        session.add(source)
        session.flush()
        source_id = source.id

    started = []

    async def probe_then_pause(*_args):
        with isolated_session() as session:
            current = session.get(Source, source_id)
            current.enabled = False
        return SimpleNamespace(
            live=True,
            status="live",
            error="",
            metadata_status="ok",
            metadata_error="",
            last_broadcast=None,
        )

    async def forbidden_start(_source):
        started.append(True)
        raise AssertionError("recorder must not start after pause")

    monkeypatch.setattr(workers, "db_session", isolated_session)
    monkeypatch.setattr(workers, "probe", probe_then_pause)
    monkeypatch.setattr(workers, "start_recorder", forbidden_start)
    monkeypatch.setattr(workers, "runtime", lambda: SimpleNamespace(recording_paused=False))

    manager = WorkerManager()
    assert asyncio.run(manager.check_source_now(source_id)) is True
    assert started == []
    with factory() as session:
        assert session.get(Source, source_id).enabled is False
        live = session.query(LiveSession).filter(LiveSession.source_id == source_id, LiveSession.ended_at.is_(None)).one()
        assert live.origin == "probe"

    engine.dispose()


def test_legacy_gap_only_fragment_remains_stitchable(tmp_path):
    from types import SimpleNamespace
    from app.workers import fragment_usable_for_stitch

    path = tmp_path / "part.mp4"
    path.write_bytes(b"media")
    fragment = SimpleNamespace(
        local_path=str(path),
        integrity_status="failed",
        integrity_error="Gap video rilevato: 0.94s senza frame continui",
    )
    assert fragment_usable_for_stitch(fragment) is True

    fragment.integrity_error = "Packet scan failed"
    assert fragment_usable_for_stitch(fragment) is False


def test_capture_fragments_are_validated_without_rewriting_mp4():
    fragment_indexer = inspect.getsource(WorkerManager._index_fragment)
    recovery = inspect.getsource(WorkerManager._revalidate_retryable_fragments)

    assert "_prepare_mp4" not in fragment_indexer
    assert "verify_media" in fragment_indexer
    assert "finalizzazione frammento fallita" in recovery


def test_recorder_slot_is_released_before_large_fragment_validation():
    watcher = inspect.getsource(WorkerManager._watch_session)

    release = watcher.index("self.active.pop(session.source_id, None)")
    finalization = watcher.index("if await self._finalize_segment(session, path)", release)
    assert release < finalization
    assert "session.safe_stop_bytes * 0.98" in watcher
    assert "replacement is not None and replacement is not session" in watcher
