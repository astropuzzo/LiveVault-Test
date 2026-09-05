from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def test_product_ui_is_dark_icon_driven_and_preserves_primary_hooks():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    ui = (STATIC / "ui.js").read_text(encoding="utf-8")
    icons = (STATIC / "icons.svg").read_text(encoding="utf-8")

    assert '<meta name="color-scheme" content="dark">' in html
    assert "color-scheme:dark" in css
    assert "gradient" not in css.lower()
    assert "glass" not in css.lower()
    assert "/static/icons.svg#monitor" in html
    assert '<symbol id="search"' in icons
    assert '<symbol id="settings"' in icons
    assert '<symbol id="cloud"' in icons

    for hook in (
        'id="dashboardView"', 'id="libraryView"', 'id="archiveView"',
        'id="statisticsView"', 'id="sources"', 'id="recordings"',
        'id="settingsForm"', 'id="videoPlayer"', 'id="commandDialog"',
    ):
        assert hook in html

    assert "renderSourcesProduct" in ui
    assert "renderLibraryProduct" in ui
    assert "renderRecordingsProduct" in ui
    assert "chart-y-label" in ui


def test_new_ui_does_not_reintroduce_marketing_dashboard_patterns():
    html = (STATIC / "index.html").read_text(encoding="utf-8").lower()
    for banned in ("hero", "eyebrow", "command center", "everything at a glance", "intelligent monitoring"):
        assert banned not in html
