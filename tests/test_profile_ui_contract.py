from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_profile_workspace_is_readable_and_csp_safe():
    app = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    ui = (ROOT / "app/static/ui.js").read_text(encoding="utf-8")
    css = (ROOT / "app/static/style.css").read_text(encoding="utf-8")

    # CSP deliberately forbids inline style attributes. Dynamic visual values
    # must be applied through the CSSOM helper after rendering instead.
    assert 'style="' not in app
    assert 'style="' not in ui
    assert "function applyDynamicStyles" in app
    assert "data-dynamic-height" in app
    assert "data-dynamic-width" in app
    assert "data-tag-color" in app

    # Profile content is split into operational and editing columns on desktop,
    # then collapses to a single readable column on narrow screens.
    assert 'class="profile-workspace"' in app
    assert 'class="profile-main-column"' in app
    assert 'class="profile-side-column"' in app
    assert ".profile-workspace{display:grid;grid-template-columns:minmax(0,1fr) 360px" in css
    assert "@media(max-width:720px)" in css
    assert ".profile-save{position:sticky" in css
