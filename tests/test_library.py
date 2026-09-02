from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import sessionmaker

from app import db as db_module
from app import main
from app.db import (
    Base,
    Category,
    Collection,
    CollectionProfile,
    Profile,
    ProfileCategory,
    Recording,
    Source,
)


@pytest.fixture
def library_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'library.db'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

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

    monkeypatch.setattr(main, "db_session", isolated_session)
    monkeypatch.setattr(main, "require_auth", lambda _request: None)
    yield factory
    engine.dispose()


def _seed_shared_profile(factory):
    with factory.begin() as session:
        profile = Profile(display_name="Performer", favorite=False, notes="")
        session.add(profile)
        session.flush()
        first = Source(
            profile_id=profile.id,
            name="performer_cb",
            platform="chaturbate",
            slug="performer_cb",
            consent_confirmed=True,
        )
        second = Source(
            profile_id=profile.id,
            name="performer_twitch",
            platform="twitch",
            slug="performer_twitch",
            consent_confirmed=True,
        )
        session.add_all([first, second])
        session.flush()
        return profile.id, first.id, second.id


def test_library_schema_separates_profiles_from_sources_and_collections():
    assert "profile_id" in Source.__table__.c
    targets = {key.target_fullname for key in Source.__table__.c.profile_id.foreign_keys}
    assert targets == {"profiles.id"}
    assert "profile_id" not in Collection.__table__.c


def test_legacy_source_migration_is_idempotent_and_preserves_rows(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sources (id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL UNIQUE)"))
        connection.execute(text("INSERT INTO sources (id, name) VALUES (1, 'alpha'), (2, 'beta')"))

    monkeypatch.setattr(db_module, "engine", engine)
    # create_all adds the v2.5 library tables while leaving the legacy sources table intact.
    Base.metadata.create_all(engine)
    db_module._migrate_sources()
    db_module._migrate_library()
    db_module._migrate_library()

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT s.id, s.name, s.profile_id, p.display_name "
            "FROM sources s JOIN profiles p ON p.id = s.profile_id ORDER BY s.id"
        )).all()
        profile_count = connection.scalar(text("SELECT count(*) FROM profiles"))

    assert rows == [(1, "alpha", rows[0].profile_id, "alpha"), (2, "beta", rows[1].profile_id, "beta")]
    assert rows[0].profile_id != rows[1].profile_id
    assert profile_count == 2
    engine.dispose()


def test_one_profile_can_link_multiple_providers(library_db):
    profile_id, _first_id, _second_id = _seed_shared_profile(library_db)
    with library_db() as session:
        linked = main._linked_sources_map(session, {profile_id})[profile_id]

    assert [(item["platform"], item["slug"]) for item in linked] == [
        ("chaturbate", "performer_cb"),
        ("twitch", "performer_twitch"),
    ]


def test_profile_edit_does_not_rename_technical_sources(library_db):
    profile_id, first_id, second_id = _seed_shared_profile(library_db)
    with library_db.begin() as session:
        category = Category(name="Featured", color="#123456")
        collection = Collection(name="Weekend", description="", color="#654321", pinned=True)
        session.add_all([category, collection])
        session.flush()
        category_id, collection_id = category.id, collection.id

    result = main.patch_source_library(
        first_id,
        main.SourceLibraryPatch(
            display_name="Editorial name",
            favorite=True,
            notes="Private note",
            category_ids=[category_id, category_id],
            collection_ids=[collection_id],
        ),
        object(),
    )

    with library_db() as session:
        profile = session.get(Profile, profile_id)
        source_names = list(session.scalars(
            select(Source.name).where(Source.id.in_([first_id, second_id])).order_by(Source.id)
        ))
        categories, collections = main._library_maps(session, {profile_id})

    assert result["profile"]["display_name"] == "Editorial name"
    assert profile.display_name == "Editorial name"
    assert profile.favorite is True
    assert source_names == ["performer_cb", "performer_twitch"]
    assert [item["id"] for item in categories[profile_id]] == [category_id]
    assert [item["id"] for item in collections[profile_id]] == [collection_id]


def test_deleting_editorial_taxonomy_never_deletes_media_or_sources(library_db):
    profile_id, first_id, _second_id = _seed_shared_profile(library_db)
    with library_db.begin() as session:
        category = Category(name="Archive", color="#112233")
        collection = Collection(name="Reference", description="", color="#445566", pinned=False)
        session.add_all([category, collection])
        session.flush()
        recording = Recording(
            source_id=first_id,
            source_name="performer_cb",
            session_id="session-1",
            local_path="/data/recordings/example.mp4",
            filename="example.mp4",
            started_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        session.add_all([
            ProfileCategory(profile_id=profile_id, category_id=category.id),
            CollectionProfile(collection_id=collection.id, profile_id=profile_id),
            recording,
        ])
        session.flush()
        category_id, collection_id, recording_id = category.id, collection.id, recording.id

    assert main.delete_category(category_id, object()) == {"ok": True, "id": category_id}
    assert main.delete_collection(collection_id, object()) == {"ok": True, "id": collection_id}

    with library_db() as session:
        assert session.get(Profile, profile_id) is not None
        assert session.get(Source, first_id) is not None
        assert session.get(Recording, recording_id) is not None
        assert session.scalar(select(func.count()).select_from(ProfileCategory)) == 0
        assert session.scalar(select(func.count()).select_from(CollectionProfile)) == 0


def test_category_api_rejects_case_insensitive_duplicate(library_db):
    created = main.create_category(main.CategoryCreate(name="Sport", color="#aabbcc"), object())
    assert created["name"] == "Sport"
    assert created["color"] == "#aabbcc"

    with pytest.raises(HTTPException) as error:
        main.create_category(main.CategoryCreate(name=" sport ", color="#ffffff"), object())
    assert error.value.status_code == 409


def test_bulk_pause_applies_to_every_source_linked_to_selected_profile(library_db, monkeypatch):
    _profile_id, first_id, second_id = _seed_shared_profile(library_db)
    stopped = []

    async def stop_source(source_id):
        stopped.append(source_id)

    monkeypatch.setattr(main.manager, "stop_source", stop_source)
    result = asyncio.run(main.bulk_sources(
        main.SourceBulkAction(source_ids=[first_id], action="pause"),
        object(),
    ))

    with library_db() as session:
        enabled = dict(session.execute(select(Source.id, Source.enabled)).all())

    assert result["updated"] == 1
    assert result["source_ids"] == [first_id, second_id]
    assert stopped == [first_id, second_id]
    assert enabled == {first_id: False, second_id: False}


def test_source_with_history_is_archived_instead_of_orphaning_recordings(library_db, monkeypatch):
    profile_id, first_id, second_id = _seed_shared_profile(library_db)
    with library_db.begin() as session:
        recording = Recording(
            source_id=first_id,
            source_name="performer_cb",
            session_id="session-archive",
            local_path="/data/recordings/archive.mp4",
            filename="archive.mp4",
            started_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        session.add(recording)
        session.flush()
        recording_id = recording.id

    async def stop_source(_source_id):
        return None

    monkeypatch.setattr(main.manager, "stop_source", stop_source)
    monkeypatch.setattr(main.manager, "wake", lambda: None)

    archived = asyncio.run(main.remove_source(first_id, object()))
    removed = asyncio.run(main.remove_source(second_id, object()))

    with library_db() as session:
        historical_source = session.get(Source, first_id)
        assert historical_source is not None
        assert historical_source.archived is True
        assert historical_source.enabled is False
        secondary_source = session.get(Source, second_id)
        assert secondary_source is not None
        assert secondary_source.archived is True
        assert secondary_source.enabled is False
        assert session.get(Profile, profile_id) is not None
        assert session.get(Recording, recording_id).source_id == first_id

    assert archived == {"ok": True, "archived": True}
    assert removed == {"ok": True, "archived": True}


def test_library_validation_and_thumbnail_containment(tmp_path, monkeypatch):
    assert main._unique_positive_ids([3, 1, 3], "ids") == [1, 3]
    assert main._clean_color("#A0b1C2") == "#a0b1c2"
    with pytest.raises(HTTPException):
        main._unique_positive_ids([0], "ids")
    with pytest.raises(HTTPException):
        main._clean_library_name("bad\nname", "Nome")

    thumbnails = tmp_path / "thumbnails"
    thumbnails.mkdir()
    inside = thumbnails / "cover.jpg"
    inside.write_bytes(b"jpeg")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"jpeg")
    monkeypatch.setattr(main, "settings", SimpleNamespace(data_dir=tmp_path))

    assert main._safe_thumbnail_url(7, str(inside)) == "/api/recordings/7/thumbnail"
    assert main._safe_thumbnail_url(8, str(outside)) == ""
