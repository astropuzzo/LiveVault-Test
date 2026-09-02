from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.statistics import build_activity_statistics


def test_activity_statistics_combines_exact_live_history_and_recording_backfill():
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    profile = SimpleNamespace(id=10, display_name="Creator")
    source = SimpleNamespace(id=1, profile_id=10, archived=False)
    sessions = [
        SimpleNamespace(source_id=1, started_at=now - timedelta(hours=4), ended_at=now - timedelta(hours=2), origin="probe"),
        SimpleNamespace(source_id=1, started_at=now - timedelta(days=1, hours=3), ended_at=now - timedelta(days=1, hours=2), origin="recording_backfill"),
    ]
    recordings = [
        SimpleNamespace(source_id=1, session_id="rec-1", started_at=now - timedelta(hours=3, minutes=30), finalized_at=now - timedelta(hours=2, minutes=30), duration_seconds=3600),
    ]
    data = build_activity_statistics(
        sources=[source], profiles=[profile], live_sessions=sessions, recordings=recordings, days=2, now=now
    )
    assert data["summary"]["online_seconds"] == 3 * 3600
    assert data["summary"]["exact_online_seconds"] == 2 * 3600
    assert data["summary"]["estimated_online_seconds"] == 3600
    assert data["summary"]["recorded_seconds"] == 3600
    assert data["summary"]["days_online"] == 2
    assert data["summary"]["live_sessions"] == 2
    assert data["summary"]["recording_sessions"] == 1
    assert data["top_creators"][0]["representative_source_id"] == 1
    assert data["top_creators"][0]["display_name"] == "Creator"


def test_linked_sources_do_not_double_count_overlapping_creator_time():
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    profile = SimpleNamespace(id=10, display_name="Creator")
    sources = [
        SimpleNamespace(id=1, profile_id=10, archived=False),
        SimpleNamespace(id=2, profile_id=10, archived=False),
    ]
    sessions = [
        SimpleNamespace(source_id=1, started_at=now - timedelta(hours=3), ended_at=now - timedelta(hours=1), origin="probe"),
        SimpleNamespace(source_id=2, started_at=now - timedelta(hours=2), ended_at=now, origin="probe"),
    ]
    data = build_activity_statistics(sources=sources, profiles=[profile], live_sessions=sessions, recordings=[], days=1, now=now)
    assert data["summary"]["online_seconds"] == 3 * 3600
    assert data["summary"]["live_sessions"] == 1
    assert data["top_creators"][0]["online_seconds"] == 3 * 3600


def test_v260_ui_contains_floating_live_pause_alert_and_statistics_navigation():
    root = Path(__file__).resolve().parents[1]
    html = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")
    js = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="livePauseAlert"' in html
    assert 'data-view="statistics"' in html
    assert 'id="statisticsView"' in html
    assert "recording_blocked_by_pause" in js
    assert "data-profile-link" in js
    assert "/api/statistics?days=" in js
