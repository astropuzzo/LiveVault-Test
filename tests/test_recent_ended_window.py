from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_recent_ended_cards_follow_session_join_window():
    js = (ROOT / "app/static/operations.js").read_text(encoding="utf-8")
    assert "controlRoomRecentEndedConfigured" in js
    assert "session_stitch_gap_minutes" in js
    assert "configuredWindowMinutes() * 60 * 1000" in js
    assert "api('/api/settings')" in js
    assert "if (activeView === 'dashboard') renderSources();" in js
