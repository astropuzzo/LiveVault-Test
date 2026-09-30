from pathlib import Path

from tests.css_contract import has_css, stylesheet

ROOT = Path(__file__).resolve().parents[1]


def test_phone_layout_has_explicit_overflow_containment_and_reflow():
    css = stylesheet()
    workspace = (ROOT / 'app/static/workspace.js').read_text(encoding='utf-8')
    sw = (ROOT / 'app/static/sw.js').read_text(encoding='utf-8')

    assert has_css(css, '@media(max-width:560px)')
    # Contain wide timeline data locally while allowing card menus to extend.
    assert has_css(css, '.cr-pulse-legend {grid-column:1 / -1;grid-row:2;justify-self:stretch;min-width:0;max-width:100%')
    assert has_css(css, 'overflow-x:auto')
    assert has_css(css, '.dashboard-filters') and has_css(css, 'grid-template-columns:minmax(0,1fr) 112px')
    assert has_css(css, '.cr-pulse-scale,.cr-pulse-row') and has_css(css, '96px minmax(0,1fr)')
    assert has_css(css, 'grid-template-areas:"thumb identity identity" "thumb upload actions"')
    assert has_css(css, '.archive-identity>*') and has_css(css, 'text-overflow:ellipsis')
    assert has_css(css, ':has(> .cr-live-card:only-child)')
    # One stylesheet, loaded by index.html: no runtime-injected CSS layers.
    assert "createElement('link')" not in workspace
    assert "livevault-shell-v" in sw
    assert "'/static/style.css'" in sw
