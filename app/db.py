from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), index=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    focus: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(40), default="chaturbate")
    slug: Mapped[str] = mapped_column(Text, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quality: Mapped[str] = mapped_column(String(20), default="best")
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_status: Mapped[str] = mapped_column(String(40), default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_live_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_live_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_status: Mapped[str] = mapped_column(String(40), default="unknown")
    metadata_error: Mapped[str] = mapped_column(Text, default="")
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    organize_cloud: Mapped[bool] = mapped_column(Boolean, default=True)
    gofile_folder_id: Mapped[str] = mapped_column(String(200), default="")
    gofile_folder_url: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    color: Mapped[str] = mapped_column(String(7), default="#7aa5ff")


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(7), default="#8c78ff")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ProfileCategory(Base):
    __tablename__ = "profile_categories"
    __table_args__ = (Index("ix_profile_categories_category_id", "category_id"),)

    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True)


class CollectionProfile(Base):
    __tablename__ = "collection_profiles"
    __table_args__ = (Index("ix_collection_profiles_profile_id", "profile_id"),)

    collection_id: Mapped[int] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)


class CloudDay(Base):
    __tablename__ = "cloud_days"
    __table_args__ = (
        Index("ux_cloud_days_profile_day_provider", "profile_id", "day_key", "provider", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    day_key: Mapped[str] = mapped_column(String(10), index=True)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    remote_id: Mapped[str] = mapped_column(String(255), default="")
    remote_url: Mapped[str] = mapped_column(Text, default="")
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    source_name: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    local_path: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(String(255), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    upload_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    upload_provider: Mapped[str] = mapped_column(String(30), default="")
    remote_id: Mapped[str] = mapped_column(String(255), default="")
    remote_url: Mapped[str] = mapped_column(Text, default="")
    cloud_day_key: Mapped[str] = mapped_column(String(10), default="", index=True)
    remote_parent_id: Mapped[str] = mapped_column(String(255), default="")
    remote_parent_url: Mapped[str] = mapped_column(Text, default="")
    upload_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    local_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    thumbnail_path: Mapped[str] = mapped_column(Text, default="")
    integrity_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    integrity_error: Mapped[str] = mapped_column(Text, default="")
    integrity_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    container_format: Mapped[str] = mapped_column(String(16), default="")
    upload_priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_video: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_audio: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    video_codec: Mapped[str] = mapped_column(String(40), default="")
    audio_codec: Mapped[str] = mapped_column(String(40), default="")


class LiveSession(Base):
    __tablename__ = "live_sessions"
    __table_args__ = (
        Index("ix_live_sessions_source_started", "source_id", "started_at"),
        Index("ix_live_sessions_source_open", "source_id", "ended_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    source_name: Mapped[str] = mapped_column(String(120), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(String(32), default="probe", index=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _columns(table: str) -> set[str]:
    with engine.connect() as conn:
        return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}


def _migrate_recordings() -> None:
    existing = _columns("recordings")
    additions = {
        "thumbnail_path": "TEXT NOT NULL DEFAULT ''",
        "integrity_status": "VARCHAR(30) NOT NULL DEFAULT 'passed'",
        "integrity_error": "TEXT NOT NULL DEFAULT ''",
        "integrity_checked_at": "DATETIME",
        "container_format": "VARCHAR(16) NOT NULL DEFAULT ''",
        "upload_priority": "INTEGER NOT NULL DEFAULT 0",
        "uploaded_at": "DATETIME",
        "has_video": "BOOLEAN",
        "has_audio": "BOOLEAN",
        "video_codec": "VARCHAR(40) NOT NULL DEFAULT ''",
        "audio_codec": "VARCHAR(40) NOT NULL DEFAULT ''",
        "cloud_day_key": "VARCHAR(10) NOT NULL DEFAULT ''",
        "remote_parent_id": "VARCHAR(255) NOT NULL DEFAULT ''",
        "remote_parent_url": "TEXT NOT NULL DEFAULT ''",
    }
    with engine.begin() as conn:
        for name, ddl in additions.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE recordings ADD COLUMN {name} {ddl}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recordings_integrity_status ON recordings (integrity_status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recordings_upload_priority ON recordings (upload_priority)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recordings_cloud_day_key ON recordings (cloud_day_key)"))
        # Older releases mixed local wall-clock values with UTC in started_at.
        # The segment mtime and probed duration provide an unambiguous UTC start.
        conn.execute(text("""
            UPDATE recordings
            SET started_at = datetime(finalized_at, '-' || duration_seconds || ' seconds')
            WHERE duration_seconds IS NOT NULL
              AND duration_seconds > 0
              AND (
                julianday(started_at) > julianday(finalized_at)
                OR abs((julianday(finalized_at) - julianday(started_at)) * 86400 - duration_seconds) > 300
              )
        """))


def _migrate_sources() -> None:
    existing = _columns("sources")
    additions = {
        "status_changed_at": "DATETIME",
        "last_error": "TEXT NOT NULL DEFAULT ''",
        "organize_cloud": "BOOLEAN NOT NULL DEFAULT 1",
        "gofile_folder_id": "VARCHAR(200) NOT NULL DEFAULT ''",
        "gofile_folder_url": "TEXT NOT NULL DEFAULT ''",
        "last_seen_live_at": "DATETIME",
        "metadata_status": "VARCHAR(40) NOT NULL DEFAULT 'unknown'",
        "metadata_error": "TEXT NOT NULL DEFAULT ''",
        "profile_id": "INTEGER REFERENCES profiles(id)",
        "archived": "BOOLEAN NOT NULL DEFAULT 0",
    }
    with engine.begin() as conn:
        for name, ddl in additions.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE sources ADD COLUMN {name} {ddl}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sources_profile_id ON sources (profile_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sources_archived ON sources (archived)"))


def _migrate_library() -> None:
    """Additive, idempotent profile backfill plus library indexes."""
    profile_columns = _columns("profiles")
    with engine.begin() as conn:
        if "focus" not in profile_columns:
            conn.execute(text("ALTER TABLE profiles ADD COLUMN focus BOOLEAN NOT NULL DEFAULT 0"))
        orphaned_sources = conn.execute(text("""
            SELECT s.id, s.name
            FROM sources AS s
            LEFT JOIN profiles AS p ON p.id = s.profile_id
            WHERE s.profile_id IS NULL OR p.id IS NULL
            ORDER BY s.id
        """)).fetchall()
        for source_id, source_name in orphaned_sources:
            result = conn.execute(
                text("""
                    INSERT INTO profiles (display_name, favorite, focus, notes, created_at)
                    VALUES (:display_name, 0, 0, '', CURRENT_TIMESTAMP)
                """),
                {"display_name": source_name},
            )
            conn.execute(
                text("UPDATE sources SET profile_id = :profile_id WHERE id = :source_id"),
                {"profile_id": int(result.lastrowid), "source_id": int(source_id)},
            )
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_categories_name_nocase ON categories (name COLLATE NOCASE)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_collections_name_nocase ON collections (name COLLATE NOCASE)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_profiles_display_name ON profiles (display_name)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_profiles_favorite ON profiles (favorite)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_profiles_focus ON profiles (focus)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_categories_color ON categories (color)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_collections_pinned_name ON collections (pinned, name)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_profile_categories_category_id ON profile_categories (category_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_collection_profiles_profile_id ON collection_profiles (profile_id)"))


def _migrate_live_sessions() -> None:
    """Backfill a minimum historical online timeline from existing recordings once.

    From v2.6.0 onward workers write probe-derived sessions, including periods where
    recording is paused. Older history can only be estimated from captured sessions.
    """
    with engine.begin() as conn:
        count = int(conn.execute(text("SELECT COUNT(*) FROM live_sessions")).scalar_one() or 0)
        if count:
            return
        conn.execute(text("""
            INSERT INTO live_sessions (source_id, source_name, started_at, ended_at, last_seen_at, origin)
            SELECT
                source_id,
                MAX(source_name),
                MIN(started_at),
                MAX(finalized_at),
                MAX(finalized_at),
                'recording_backfill'
            FROM recordings
            WHERE started_at IS NOT NULL AND finalized_at IS NOT NULL
            GROUP BY source_id, session_id
        """))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_recordings()
    _migrate_sources()
    _migrate_library()
    _migrate_live_sessions()


@contextmanager
def db_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
