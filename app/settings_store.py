from __future__ import annotations

import base64
import hashlib
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from .config import settings
from .db import AppSetting, db_session


SECRET_KEYS = {"gofile_token", "pixeldrain_api_key"}


@dataclass
class RuntimeSettings:
    poll_seconds: int = 60
    max_probe_concurrency: int = 4
    segment_minutes: int = 60
    segment_max_gb: float = 2.0
    session_stitch_gap_minutes: int = 20
    container_format: str = "mp4"
    integrity_mode: str = "packet"
    generate_thumbnails: bool = True
    buffer_max_gb: float = 12.0
    buffer_hard_stop: bool = True
    min_free_gb: float = 3.0
    critical_free_gb: float = 1.5
    emergency_free_gb: float = 0.75
    delete_after_upload: bool = True
    upload_retry_seconds: int = 180
    max_upload_attempts: int = 12
    primary_uploader: str = "gofile"
    fallback_uploader: str = "pixeldrain"
    gofile_token: str = ""
    gofile_folder_id: str = ""
    gofile_region: str = "auto"
    pixeldrain_api_key: str = ""
    recording_paused: bool = False
    upload_paused: bool = False


_state = RuntimeSettings(
    poll_seconds=settings.poll_seconds,
    max_probe_concurrency=settings.max_probe_concurrency,
    segment_minutes=min(settings.segment_minutes, 120),
    segment_max_gb=min(max(settings.segment_max_gb, 0.25), 2.0),
    container_format=settings.container_format if settings.container_format in {"mp4", "mkv"} else "mp4",
    integrity_mode=settings.integrity_mode if settings.integrity_mode in {"quick", "packet"} else "packet",
    generate_thumbnails=settings.generate_thumbnails,
    buffer_max_gb=settings.buffer_max_gb,
    buffer_hard_stop=settings.buffer_hard_stop,
    delete_after_upload=settings.delete_after_upload,
    primary_uploader=settings.primary_uploader,
    fallback_uploader=settings.fallback_uploader,
    min_free_gb=settings.min_free_gb,
    critical_free_gb=settings.critical_free_gb,
    emergency_free_gb=settings.emergency_free_gb,
    upload_retry_seconds=settings.upload_retry_seconds,
    max_upload_attempts=settings.max_upload_attempts,
    gofile_token=settings.gofile_token,
    gofile_folder_id=settings.gofile_folder_id,
    gofile_region=settings.gofile_region,
    pixeldrain_api_key=settings.pixeldrain_api_key,
)


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.app_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def _coerce(key: str, value: str):
    current = getattr(_state, key)
    if isinstance(current, bool):
        return value.lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(float(value))
    if isinstance(current, float):
        return float(value)
    return value


def reload_runtime() -> RuntimeSettings:
    with db_session() as db:
        rows = list(db.scalars(select(AppSetting)).all())
    for row in rows:
        if not hasattr(_state, row.key):
            continue
        raw = _decrypt(row.value) if row.is_secret else row.value
        try:
            setattr(_state, row.key, _coerce(row.key, raw))
        except (ValueError, TypeError):
            continue
    return _state


def runtime() -> RuntimeSettings:
    return _state


def _notify_runtime_change() -> None:
    """Refresh worker constants without importing workers during module bootstrap."""
    workers_module = sys.modules.get("app.workers")
    refresh = getattr(workers_module, "refresh_runtime_constants", None) if workers_module else None
    if callable(refresh):
        refresh()


def set_values(values: dict[str, object]) -> RuntimeSettings:
    allowed = set(asdict(_state))
    now = datetime.now(timezone.utc)
    with db_session() as db:
        for key, value in values.items():
            if key not in allowed:
                continue
            serialized = "true" if value is True else "false" if value is False else str(value)
            is_secret = key in SECRET_KEYS
            stored = _encrypt(serialized) if is_secret else serialized
            row = db.get(AppSetting, key)
            if row:
                row.value = stored
                row.is_secret = is_secret
                row.updated_at = now
            else:
                db.add(AppSetting(key=key, value=stored, is_secret=is_secret, updated_at=now))
            setattr(_state, key, value)
    _notify_runtime_change()
    return _state


def clear_secret(key: str) -> None:
    if key not in SECRET_KEYS:
        return
    set_values({key: ""})


def public_settings() -> dict:
    s = runtime()
    return {
        "poll_seconds": s.poll_seconds,
        "max_probe_concurrency": s.max_probe_concurrency,
        "segment_minutes": s.segment_minutes,
        "segment_max_gb": s.segment_max_gb,
        "session_stitch_gap_minutes": s.session_stitch_gap_minutes,
        "container_format": s.container_format,
        "integrity_mode": s.integrity_mode,
        "generate_thumbnails": s.generate_thumbnails,
        "buffer_max_gb": s.buffer_max_gb,
        "buffer_hard_stop": s.buffer_hard_stop,
        "min_free_gb": s.min_free_gb,
        "critical_free_gb": s.critical_free_gb,
        "emergency_free_gb": s.emergency_free_gb,
        "delete_after_upload": s.delete_after_upload,
        "upload_retry_seconds": s.upload_retry_seconds,
        "max_upload_attempts": s.max_upload_attempts,
        "primary_uploader": s.primary_uploader,
        "fallback_uploader": s.fallback_uploader,
        "gofile_folder_id": s.gofile_folder_id,
        "gofile_region": s.gofile_region,
        "gofile_configured": bool(s.gofile_token),
        "gofile_token_hint": ("••••" + s.gofile_token[-4:]) if s.gofile_token else "",
        "pixeldrain_configured": bool(s.pixeldrain_api_key),
        "pixeldrain_key_hint": ("••••" + s.pixeldrain_api_key[-4:]) if s.pixeldrain_api_key else "",
        "recording_paused": s.recording_paused,
        "upload_paused": s.upload_paused,
    }
