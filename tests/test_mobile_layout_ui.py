from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_phone_layout_has_explicit_overflow_containment_and_reflow():
    css = (ROOT / 'app/static/mobile-fixes.css').read_text(encoding='utf-8')
    workspace = (ROOT / 'app/static/workspace.js').read_text(encoding='utf-8')
    sw = (ROOT / 'app/static/sw.js').read_text(encoding='utf-8')

    assert '@media(max-width:560px)' in css
    assert 'overflow-x:clip' in css
    assert '.dashboard-filters' in css and 'grid-template-columns:minmax(0,1fr) 88px' in css
    assert '.cr-pulse-scale,.cr-pulse-row' in css and '78px minmax(0,1fr)' in css
    assert 'grid-template-areas:"thumb identity" "thumb upload" "actions actions"' in css
    assert '.archive-identity>*' in css and 'text-overflow:ellipsis' in css
    assert ':has(> .cr-live-card:only-child)' in css
    assert '/static/mobile-fixes.css?v=3.0.0-redesign8' in workspace
    assert "livevault-shell-v3.0.0-redesign8" in sw
    assert "'/static/mobile-fixes.css'" in sw
