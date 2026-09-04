from datetime import datetime, timezone
from pathlib import Path

from app.db import CloudDay, Recording
from app.workers import cloud_day_key

ROOT = Path(__file__).resolve().parents[1]


def test_cloud_day_uses_frankfurt_calendar_boundary():
    assert cloud_day_key(datetime(2026, 9, 3, 21, 59, tzinfo=timezone.utc)) == "2026-09-03"
    assert cloud_day_key(datetime(2026, 9, 3, 22, 1, tzinfo=timezone.utc)) == "2026-09-04"


def test_daily_cloud_models_keep_file_and_parent_links_separate():
    assert hasattr(Recording, "remote_url")
    assert hasattr(Recording, "remote_parent_url")
    assert hasattr(Recording, "cloud_day_key")
    assert hasattr(CloudDay, "day_key")
    assert hasattr(CloudDay, "provider")


def test_profile_days_and_thumbnail_remote_link_are_present():
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "profileData.recording_days" in js
    assert "profile-day-thumb" in js
    assert 'href="${esc(remote)}"' in js
    assert '"recording_days": recording_days' in main
    assert '"remote_parent_url": r.remote_parent_url' in main


def test_pixeldrain_closed_day_album_and_gofile_daily_folder_code_present():
    uploaders = (ROOT / "app/uploaders.py").read_text(encoding="utf-8")
    workers = (ROOT / "app/workers.py").read_text(encoding="utf-8")
    assert "def create_pixeldrain_list" in uploaders
    assert "https://pixeldrain.com/api/list" in uploaders
    assert "https://pixeldrain.com/l/{remote_id}" in uploaders
    assert "async def _finalize_closed_pixeldrain_days" in workers
    assert 'CloudDay.provider == "gofile"' in workers


def test_v286_version_and_cache():
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "2.8.22"
    assert 'VERSION = "2.8.22"' in (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "livevault-shell-v2.8.22" in (ROOT / "app/static/sw.js").read_text(encoding="utf-8")
