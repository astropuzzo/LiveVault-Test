from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("DATA_DIR", "/data"))
    db_path: Path = Path(os.getenv("DB_PATH", "/data/livevault.db"))
    app_password: str = os.getenv("APP_PASSWORD", "")
    app_password_hash: str = os.getenv("APP_PASSWORD_HASH", "")
    app_secret: str = os.getenv("APP_SECRET", "")
    poll_seconds: int = _int("POLL_SECONDS", 60)
    max_probe_concurrency: int = _int("MAX_PROBE_CONCURRENCY", 4)
    segment_minutes: int = _int("SEGMENT_MINUTES", 15)
    container_format: str = os.getenv("CONTAINER_FORMAT", "mp4").lower()
    integrity_mode: str = os.getenv("INTEGRITY_MODE", "packet").lower()
    generate_thumbnails: bool = _bool("GENERATE_THUMBNAILS", True)
    buffer_max_gb: float = _float("BUFFER_MAX_GB", 12.0)
    buffer_hard_stop: bool = _bool("BUFFER_HARD_STOP", True)
    delete_after_upload: bool = _bool("DELETE_AFTER_UPLOAD", True)
    primary_uploader: str = os.getenv("PRIMARY_UPLOADER", "gofile").lower()
    fallback_uploader: str = os.getenv("FALLBACK_UPLOADER", "pixeldrain").lower()
    min_free_gb: float = _float("MIN_FREE_GB", 3.0)
    critical_free_gb: float = _float("CRITICAL_FREE_GB", 1.5)
    emergency_free_gb: float = _float("EMERGENCY_FREE_GB", 0.75)
    upload_retry_seconds: int = _int("UPLOAD_RETRY_SECONDS", 180)
    max_upload_attempts: int = _int("MAX_UPLOAD_ATTEMPTS", 12)
    gofile_token: str = os.getenv("GOFILE_TOKEN", "")
    allow_gofile_guest: bool = _bool("ALLOW_GOFILE_GUEST", False)
    gofile_folder_id: str = os.getenv("GOFILE_FOLDER_ID", "")
    gofile_region: str = os.getenv("GOFILE_REGION", "auto").lower()
    gofile_upload_endpoint: str = os.getenv("GOFILE_UPLOAD_ENDPOINT", "https://upload.gofile.io/uploadfile")
    pixeldrain_api_key: str = os.getenv("PIXELDRAIN_API_KEY", "")
    cookie_secure: bool = _bool("COOKIE_SECURE", False)
    timezone: str = os.getenv("TZ", "Europe/Brussels")

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    def validate(self) -> None:
        if not self.app_password and not self.app_password_hash:
            raise RuntimeError("APP_PASSWORD_HASH or APP_PASSWORD is required")
        if len(self.app_secret) < 32:
            raise RuntimeError("APP_SECRET must be at least 32 characters")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
