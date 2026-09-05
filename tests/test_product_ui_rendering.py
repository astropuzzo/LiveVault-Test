from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_product_ui_rendering_regressions_are_pinned():
    css = (ROOT / 'app/static/ui-fixes.css').read_text(encoding='utf-8')
    workspace = (ROOT / 'app/static/workspace.js').read_text(encoding='utf-8')
    sw = (ROOT / 'app/static/sw.js').read_text(encoding='utf-8')
    utils = (ROOT / 'app/utils.py').read_text(encoding='utf-8')

    # icons.svg is referenced through external <use>; stroke must be inherited
    # from the host document rather than depending on styles inside the sprite.
    assert 'stroke: currentColor' in css
    assert '.icon use' in css
    assert '.button-icon use' in css

    # Persistent thumbnails are 3x3 storyboards. Compact UI surfaces must crop
    # the representative middle frame rather than squeeze the whole contact sheet.
    assert 'Create a 3x3 storyboard' in utils
    assert '.archive-thumb img' in css
    assert '.library-cover img' in css
    assert '.cr-preview-cover' in css
    assert 'transform: scale(3)' in css

    # Archive columns must retain explicit room for duration/cloud/actions.
    assert '.archive-table-head' in css
    assert 'grid-template-columns: minmax(260px,1.15fr) minmax(240px,.9fr) 120px 130px 96px' in css

    # The fix is same-origin/CSP safe and included in the versioned PWA shell.
    assert "/static/ui-fixes.css?v=3.0.0-redesign2" in workspace
    assert "livevault-shell-v3.0.0-redesign2" in sw
    assert "'/static/ui-fixes.css'" in sw
