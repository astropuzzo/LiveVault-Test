from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pulse_function(js: str) -> str:
    start = js.index('function controlRoomPulseMarkup()')
    end = js.index('function controlRoomRecentEnded', start)
    return js[start:end]


def test_live_pulse_is_csp_safe_and_uses_real_geometry():
    js = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')
    css = (ROOT / 'app/static/enhancements.css').read_text(encoding='utf-8')
    main = (ROOT / 'app/main.py').read_text(encoding='utf-8')
    pulse = pulse_function(js)

    assert 'style=' not in pulse
    assert 'const labelRatios = [0, .25, .5, .75, 1]' in pulse
    assert '<svg class="cr-pulse-svg"' in pulse
    assert 'viewBox="0 0 1000 12"' in pulse
    assert '<rect class="cr-pulse-block' in pulse
    assert 'x="${x.toFixed(3)}"' in pulse
    assert 'width="${width.toFixed(3)}"' in pulse
    assert 'const maxProfiles = compact ? 5 : 8' in pulse

    assert 'Live Pulse CSP-safe hotfix v2.8.2' in css
    assert 'grid-template-columns:repeat(5,minmax(0,1fr))' in css
    assert 'grid-template-columns:repeat(3,minmax(0,1fr))' in css
    assert '.cr-pulse-tick-1,.cr-pulse-tick-3{display:none!important}' in css
    assert '.cr-pulse-svg{display:block;width:100%;height:12px' in css

    assert "style-src 'self'" in main
    assert "style-src 'self' 'unsafe-inline'" not in main
