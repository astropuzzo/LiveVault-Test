from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_live_pulse_mobile_has_compact_axis_and_spacing():
    js = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')
    css = (ROOT / 'app/static/enhancements.css').read_text(encoding='utf-8')
    assert "const compact = window.matchMedia('(max-width: 620px)').matches" in js
    assert "const labelRatios = compact ? [0, .5, 1] : [0, .25, .5, .75, 1]" in js
    assert "const maxProfiles = compact ? 5 : 8" in js
    assert "Live Pulse mobile hotfix v2.8.1" in css
    assert "grid-template-columns:96px minmax(0,1fr)" in css
    assert "min-width:6px" in css
