from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(40), default="chaturbate")
    slug: Mapped[str] = mapped_column(Text, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quality: Mapped[str] = mapped_column(String(20), default="best")
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
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
    }
    with engine.begin() as conn:
        for name, ddl in additions.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE recordings ADD COLUMN {name} {ddl}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recordings_integrity_status ON recordings (integrity_status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recordings_upload_priority ON recordings (upload_priority)"))
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
    }
    with engine.begin() as conn:
        for name, ddl in additions.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE sources ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_recordings()
    _migrate_sources()


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
