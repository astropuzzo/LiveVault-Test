from pathlib import Path

from tests.css_contract import has_css, stylesheet

ROOT = Path(__file__).resolve().parents[1]


def test_profile_workspace_is_readable_and_csp_safe():
    app = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    ui = (ROOT / "app/static/ui.js").read_text(encoding="utf-8")
    css = stylesheet()

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
    assert has_css(css, ".profile-workspace{display:grid;grid-template-columns:minmax(0,1fr) 360px")
    assert has_css(css, "@media(max-width:720px)")
    assert has_css(css, ".profile-save{position:sticky")


def test_profile_intelligence_has_large_stacked_charts_calendar_and_adaptive_forecast():
    app = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    css = stylesheet()

    assert 'class="stats-chart-stack profile"' in app
    assert 'function weekdayPreferencesMarkup' in app
    assert 'function activityCalendarMarkup' in app
    assert 'function forecastMarkup' in app
    assert 'activity_intervals' in app
    assert 'Previsione prossimi 7 giorni' in app
    assert 'Calendario attività' in app
    assert 'Giorni preferiti' in app
    assert has_css(css, '.stats-large-chart .activity-chart{display:block;width:100%;height:270px')
    assert has_css(css, '.activity-calendar-grid{display:grid;grid-template-columns:repeat(7')
    assert has_css(css, '.forecast-grid{display:grid')
