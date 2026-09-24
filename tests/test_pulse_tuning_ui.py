from pathlib import Path

from tests.css_contract import has_css, stylesheet

ROOT = Path(__file__).resolve().parents[1]


def test_pulse_has_patterns_real_time_scale_and_selectable_window():
    js = (ROOT / 'app/static/pulse-tuning.js').read_text(encoding='utf-8')
    css = stylesheet()
    facade = (ROOT / 'app/main/__init__.py').read_text(encoding='utf-8')

    assert "{hours: 6, label: '6h'}" in js
    assert "{hours: 24, label: '24h'}" in js
    assert "{hours: 72, label: '3 gg'}" in js
    assert "{hours: 168, label: '7 gg'}" in js
    assert 'storedHours : 6' in js
    assert '/api/control-room/pulse?hours=${selectedHours}' in js
    assert "livevault-pulse-hours" in js
    assert 'data-pulse-hours' in js

    main = (ROOT / 'app/main.py').read_text(encoding='utf-8')
    assert 'hours = max(1, min(int(hours), 168))' in main
    assert '"sessions": sessions[:1000]' in main
    assert '__code__.replace' not in facade

    assert 'function nextWholeHour' in js
    assert 'function rangeDensity' in js
    assert 'halfHours: true' in js
    assert 'cr-pulse-hour-line minor' in js
    assert 'cr-pulse-day-line' in js
    assert has_css(css, '.cr-pulse-day-line')
    assert has_css(css, '.cr-pulse-half-hour-line')

    # Pattern tiles must paint their semantic hue with explicit numeric geometry.
    # Percentage-sized rects inside SVG paint servers rendered inconsistently and
    # could expose the blue ONLINE layer beneath the texture.
    assert 'function patternRect(pattern, fill, width, height)' in js
    assert "patternRect(privatePattern, '#a88bfa', 11, 11)" in js
    assert "patternRect(tipjarPattern, '#f0943f', 12, 12)" in js
    assert "patternRect(cloudPattern, '#3ecf8e', 18, 18)" in js
    assert "patternRect(processingPattern, '#6aa6ff', 12, 12)" in js
    assert "patternRect(missedPattern, '#ec6fb3', 14, 14)" in js
    assert "width: '100%'" not in js
    assert "height: '100%'" not in js

    for pattern in ['private', 'tipjar', 'cloud', 'processing', 'missed', 'restricted', 'unrecorded']:
        assert f"id: 'lv-pulse-{pattern}'" in js
        assert has_css(css, f'url(#lv-pulse-{pattern})') or f'url(#lv-pulse-{pattern})' in js
    assert "svgNode('circle'" in js
    assert 'M9 3.2L10 8L14.8 9' in js

    assert "['remote', 'CLOUD', 'url(#lv-pulse-cloud)']" in js
    assert 'cr-pulse-legend-swatch' in js
    # The legend spans its own row under the title and window selector.
    assert has_css(css, '.cr-pulse-legend {grid-column: 1 / -1; grid-row: 2;')


def test_pulse_legend_names_every_supported_state_without_vague_recovery_copy():
    js = (ROOT / 'app/static/pulse-tuning.js').read_text(encoding='utf-8')
    app = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')
    assert "['processing', 'IN ELABORAZIONE', 'url(#lv-pulse-processing)']" in js
    assert "['restricted', 'LIMITATA', 'url(#lv-pulse-restricted)']" in js
    assert 'RECUPERO' not in js
    assert '<i class="restricted"></i>LIMITATA' in app
