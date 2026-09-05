from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pulse_has_real_hour_scale_centered_legend_and_selectable_window():
    js = (ROOT / 'app/static/pulse-tuning.js').read_text(encoding='utf-8')
    css = (ROOT / 'app/static/pulse-axis.css').read_text(encoding='utf-8')

    # The product defaults to a six-hour operational window but preserves
    # explicit shorter/longer choices and sends the selected value to the API.
    assert 'const ALLOWED_HOURS = [4, 6, 8, 12]' in js
    assert 'storedHours : 6' in js
    assert '/api/control-room/pulse?hours=${selectedHours}' in js
    assert "livevault-pulse-hours" in js
    assert 'data-pulse-hours' in js

    # Whole clock hours are computed, labeled and rendered as vertical guides;
    # half-hour guides provide a quieter secondary cadence.
    assert 'function nextWholeHour' in js
    assert 'setMinutes(0, 0, 0)' in js
    assert 'cr-pulse-hour-grid' in js
    assert 'cr-pulse-hour-axis' in js
    assert 'cr-pulse-hour-line' in js
    assert 'cr-pulse-half-hour-line' in js
    assert '.cr-pulse-hour-line' in css
    assert '.cr-pulse-half-hour-line' in css

    # Legend is rebuilt as a centered semantic key and includes the green
    # remote/cloud recording state that previously had no explanation.
    assert "['remote', 'CLOUD']" in js
    assert 'cr-pulse-legend-item' in js
    assert 'justify-self:center' in css
    assert '.cr-pulse-legend i.remote' in css
    assert '#32d583' in css
