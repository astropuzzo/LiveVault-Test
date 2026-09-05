from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pulse_has_patterns_real_time_scale_and_selectable_window():
    js = (ROOT / 'app/static/pulse-tuning.js').read_text(encoding='utf-8')
    css = (ROOT / 'app/static/pulse-axis.css').read_text(encoding='utf-8')
    facade = (ROOT / 'app/main/__init__.py').read_text(encoding='utf-8')

    # Default remains six hours, with progressively wider operational/history ranges.
    assert "{hours: 6, label: '6h'}" in js
    assert "{hours: 24, label: '24h'}" in js
    assert "{hours: 72, label: '3 gg'}" in js
    assert "{hours: 168, label: '7 gg'}" in js
    assert 'storedHours : 6' in js
    assert '/api/control-room/pulse?hours=${selectedHours}' in js
    assert "livevault-pulse-hours" in js
    assert 'data-pulse-hours' in js

    # The registered legacy endpoint is extended to serve seven days and a larger
    # session payload without duplicating the query implementation.
    assert 'replacements = {48: 168, 120: 1000}' in facade
    assert 'endpoint.__code__ = endpoint.__code__.replace' in facade

    # Clock cadence remains visible: each hour gets a guide, short ranges retain
    # half-hours, while long ranges strengthen 6h/day boundaries and reduce labels.
    assert 'function nextWholeHour' in js
    assert 'function rangeDensity' in js
    assert 'halfHours: true' in js
    assert 'cr-pulse-hour-line minor' in js
    assert 'cr-pulse-day-line' in js
    assert '.cr-pulse-day-line' in css
    assert '.cr-pulse-half-hour-line' in css

    # Pattern tiles must paint their semantic base color completely. Percentage
    # pattern backgrounds caused the previous transparent "everything blue" bug.
    assert 'function patternRect(pattern, fill, width, height)' in js
    assert "width: '100%'" not in js
    assert "patternRect(privatePattern, '#9a5cff', 12, 12)" in js
    assert "patternRect(tipjarPattern, '#f1a72a', 12, 12)" in js
    assert "patternRect(cloudPattern, '#32d583', 22, 22)" in js
    assert "patternRect(processingPattern, '#22c7ff', 14, 14)" in js
    assert "patternRect(missedPattern, '#ff4fc8', 20, 12)" in js

    # Textures are deliberately low contrast: hue carries state first, texture
    # is only a secondary accessibility/detail channel.
    assert 'opacity: .18' in js
    assert 'opacity: .17' in js
    assert 'opacity: .20' in js
    assert '.cr-pulse-svg .cr-pulse-live-span' in css
    assert 'filter:none' in css
    assert 'stroke-dasharray:none' in css

    # Legend uses the same SVG paint servers and remains centered.
    assert "['remote', 'CLOUD', 'url(#lv-pulse-cloud)']" in js
    assert 'cr-pulse-legend-swatch' in js
    assert 'justify-self:center' in css
