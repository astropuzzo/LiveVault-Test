from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_live_pulse_endpoint_and_session_evolution_are_wired():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    assert '@app.get("/api/control-room/pulse")' in main
    assert 'controlRoomPulseMarkup' in js
    assert 'controlRoomRecentEnded' in js
    assert 'Appena terminate' in js
    assert '✓ SALVATA' in js
    assert 'recording_intervals' in main
    assert 'recording_started_at' in main
    assert "Europe/Berlin" in js


def test_live_dna_uses_existing_activity_statistics():
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    assert 'function liveDnaMarkup' in js
    assert 'class="profile-section live-dna"' in js
    assert 'dna-week' in js
    assert 'dna-hours' in js


def test_archive_is_grouped_and_filterable():
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "app/static/enhancements.css").read_text(encoding="utf-8")
    for token in ('archivePeriod', 'archiveCreator', 'archiveProvider', 'archiveStorage', 'archiveGroup', 'archiveSort'):
        assert token in js
    assert 'Per giorno' in js
    assert 'Per creator' in js
    assert 'Per sessione' in js
    assert 'archive-group' in css
