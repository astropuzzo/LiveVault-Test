from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pulse_function(js: str) -> str:
    start = js.index('function controlRoomPulseMarkup()')
    end = js.index('function controlRoomRecentEnded', start)
    return js[start:end]


def test_live_pulse_is_csp_safe_and_shows_live_and_recording_geometry():
    js = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')
    css = (ROOT / 'app/static/enhancements.css').read_text(encoding='utf-8')
    main = (ROOT / 'app/main.py').read_text(encoding='utf-8')
    pulse = pulse_function(js)

    assert 'style=' not in pulse
    assert 'const labelRatios = [0, .25, .5, .75, 1]' in pulse
    assert 'pulseRecordingFiles(session)' in pulse
    assert '<rect class="cr-pulse-live-span' in pulse
    assert "cr-pulse-rec-span ${remoteUrl ? 'remote' : ''}" in pulse
    assert 'cr-pulse-rec-media' in pulse
    assert 'data-preview-url' in pulse
    assert 'target="_blank"' in pulse
    assert 'cr-pulse-live-marker' in pulse
    assert 'cr-pulse-rec-marker' in pulse
    assert 'pulseSessionTimingMarkup(representative)' in pulse
    assert 'DISPLAY_TIME_ZONE_LABEL' in pulse
    assert 'viewBox="0 0 1000 16"' in pulse
    assert 'const maxProfiles = compact ? 5 : 8' in pulse

    assert "const DISPLAY_TIME_ZONE = 'Europe/Berlin'" in js
    assert "const DISPLAY_TIME_ZONE_LABEL = 'Frankfurt'" in js
    assert 'timeZone: DISPLAY_TIME_ZONE' in js
    assert 'recording_intervals' in main
    assert 'recording_started_at' in main
    assert 'recording_ended_at' in main
    assert 'LiveVault Pulse timing + Frankfurt timezone v2.8.3' in css
    assert '.cr-pulse-rec-span{fill:' in css

    assert "style-src 'self'" in main
    assert "style-src 'self' 'unsafe-inline'" not in main
