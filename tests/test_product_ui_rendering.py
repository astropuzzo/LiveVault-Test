from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_product_ui_rendering_regressions_are_pinned():
    css = (ROOT / 'app/static/ui-fixes.css').read_text(encoding='utf-8')
    tuning = (ROOT / 'app/static/dashboard-tuning.css').read_text(encoding='utf-8')
    workspace = (ROOT / 'app/static/workspace.js').read_text(encoding='utf-8')
    sw = (ROOT / 'app/static/sw.js').read_text(encoding='utf-8')
    utils = (ROOT / 'app/utils.py').read_text(encoding='utf-8')

    assert 'stroke: currentColor' in css
    assert '.icon use' in css
    assert '.button-icon use' in css

    assert 'Create a 3x3 storyboard' in utils
    assert '.archive-thumb img' in css
    assert '.library-cover img' in css
    assert '.cr-preview-cover' in css
    assert 'transform: scale(3)' in css

    assert '.archive-table-head' in css
    assert 'grid-template-columns: minmax(260px,1.15fr) minmax(240px,.9fr) 120px 130px 96px' in css

    assert ':has(> .cr-live-card:only-child)' in tuning
    assert 'grid-template-columns: minmax(0, 1200px)' in tuning
    assert 'min-height: 340px' in tuning

    assert '.cr-pulse-live-span' in tuning and '#2f7cff' in tuning
    assert '.cr-pulse-access-span.private' in tuning and '#9a5cff' in tuning
    assert '.cr-pulse-access-span.tipjar' in tuning and '#f1a72a' in tuning
    assert '.cr-pulse-rec-span' in tuning and '#ff4f62' in tuning
    assert '.cr-pulse-missed-span' in tuning and '#ff4fc8' in tuning

    assert "/static/ui-fixes.css?v=3.0.0-redesign2" in workspace
    assert "/static/dashboard-tuning.css?v=3.0.0-redesign3" in workspace
    assert "/static/pulse-axis.css?v=3.0.0-redesign6" in workspace
    assert "/static/pulse-tuning.js?v=3.0.0-redesign6" in workspace
    assert "livevault-shell-v3.0.0-redesign6" in sw
    assert "'/static/ui-fixes.css'" in sw
    assert "'/static/dashboard-tuning.css'" in sw
    assert "'/static/pulse-axis.css'" in sw
    assert "'/static/pulse-tuning.js'" in sw
