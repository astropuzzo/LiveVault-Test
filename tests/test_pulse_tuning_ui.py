from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pulse_has_patterns_real_time_scale_and_selectable_window():
    js = (ROOT / 'app/static/pulse-tuning.js').read_text(encoding='utf-8')
    css = (ROOT / 'app/static/pulse-axis.css').read_text(encoding='utf-8')
    facade = (ROOT / 'app/main/__init__.py').read_text(encoding='utf-8')

    assert "{hours: 6, label: '6h'}" in js
    assert "{hours: 24, label: '24h'}" in js
    assert "{hours: 72, label: '3 gg'}" in js
    assert "{hours: 168, label: '7 gg'}" in js
    assert 'storedHours : 6' in js
    assert '/api/control-room/pulse?hours=${selectedHours}' in js
    assert "livevault-pulse-hours" in js
    assert 'data-pulse-hours' in js

    assert 'replacements = {48: 168, 120: 1000}' in facade
    assert 'endpoint.__code__ = endpoint.__code__.replace' in facade

    assert 'function nextWholeHour' in js
    assert 'function rangeDensity' in js
    assert 'halfHours: true' in js
    assert 'cr-pulse-hour-line minor' in js
    assert 'cr-pulse-day-line' in js
    assert '.cr-pulse-day-line' in css
    assert '.cr-pulse-half-hour-line' in css

    # Pattern tiles must paint their semantic hue with explicit numeric geometry.
    # Percentage-sized rects inside SVG paint servers rendered inconsistently and
    # could expose the blue ONLINE layer beneath the texture.
    assert 'function patternRect(pattern, fill, width, height)' in js
    assert "patternRect(privatePattern, '#9a5cff', 11, 11)" in js
    assert "patternRect(tipjarPattern, '#f1a72a', 12, 12)" in js
    assert "patternRect(cloudPattern, '#32d583', 18, 18)" in js
    assert "patternRect(processingPattern, '#22c7ff', 12, 12)" in js
    assert "patternRect(missedPattern, '#ff4fc8', 14, 14)" in js
    assert "width: '100%'" not in js
    assert "height: '100%'" not in js

    for pattern in ['private', 'tipjar', 'cloud', 'processing', 'missed', 'restricted', 'unrecorded']:
        assert f"id: 'lv-pulse-{pattern}'" in js
        assert f'url(#lv-pulse-{pattern})' in css or f'url(#lv-pulse-{pattern})' in js
    assert "svgNode('circle'" in js
    assert 'M9 3.2L10 8L14.8 9' in js

    assert "['remote', 'CLOUD', 'url(#lv-pulse-cloud)']" in js
    assert 'cr-pulse-legend-swatch' in js
    assert 'justify-self:center' in css
