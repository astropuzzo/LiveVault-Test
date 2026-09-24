from pathlib import Path

from tests.css_contract import has_css, stylesheet

ROOT = Path(__file__).resolve().parents[1]


def test_product_ui_rendering_regressions_are_pinned():
    css = stylesheet()
    tuning = stylesheet()
    workspace = (ROOT / 'app/static/workspace.js').read_text(encoding='utf-8')
    sw = (ROOT / 'app/static/sw.js').read_text(encoding='utf-8')
    utils = (ROOT / 'app/utils.py').read_text(encoding='utf-8')

    assert has_css(css, 'stroke: currentColor')
    assert has_css(css, '.icon use')
    assert has_css(css, '.button-icon use')

    assert 'Create a 3x3 storyboard' in utils
    assert has_css(css, '.archive-thumb img')
    assert has_css(css, '.library-cover img')
    assert has_css(css, '.cr-preview-cover')
    assert has_css(css, 'transform: scale(3)')

    assert has_css(css, '.archive-table-head')
    assert has_css(css, 'grid-template-columns: minmax(260px,1.15fr) minmax(240px,.9fr) 120px 130px 96px')
    assert has_css(css, '.archive-surface .row-more[open]')
    assert has_css(css, '.archive-surface .row-more[open] .row-menu')
    assert has_css(css, '.archive-surface .icon-button.recovery')

    # A single live creator is promoted to a large preview + details card.
    assert has_css(tuning, ':has(> .cr-live-card:only-child)')
    assert has_css(tuning, 'grid-template-columns: minmax(0, 1.7fr) minmax(280px, 1fr)')
    assert has_css(tuning, 'min-height: 300px')

    # Timeline states: quiet online bar, one semantic fill per state.
    assert has_css(tuning, '.cr-pulse-live-span {fill: #56627a')
    assert has_css(tuning, '.cr-pulse-access-span.private {fill: url(#lv-pulse-private)')
    assert has_css(tuning, '.cr-pulse-access-span.tipjar {fill: url(#lv-pulse-tipjar)')
    assert has_css(tuning, '.cr-pulse-rec-span{fill:var(--recording)')
    assert has_css(tuning, '.cr-pulse-missed-span {fill: url(#lv-pulse-missed)')
    assert has_css(tuning, '--recording: #ff5c5c')

    html = (ROOT / 'app/static/index.html').read_text(encoding='utf-8')
    assert "createElement('link')" not in workspace
    assert "pulse-tuning.js" not in workspace
    assert '<script src="/static/pulse-tuning.js?v=' in html
    assert "'/static/style.css'" in sw
    assert "'/static/pulse-tuning.js'" in sw
    for retired in ('ui-fixes', 'dashboard-tuning', 'pulse-axis', 'mobile-fixes', 'creator-hover.css'):
        assert retired not in sw and retired not in html
