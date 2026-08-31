from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(40), default="chaturbate")
    slug: Mapped[str] = mapped_column(String(160), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quality: Mapped[str] = mapped_column(String(20), default="best")
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_status: Mapped[str] = mapped_column(String(40), default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_live_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def init_db() -> None:
    Base.metadata.create_all(engine)


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
