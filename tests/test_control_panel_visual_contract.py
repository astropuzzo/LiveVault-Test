from pathlib import Path

ROOT = Path(__file__).parents[1] / 'control-panel' / 'static'


def test_product_visual_layers_are_explicit_and_ordered():
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    app = html.index('/app.css?v=4.0-control')
    shell = html.index('/shell.css?v=4.0-control')
    theme = html.index('/theme.css?v=5.0-feel')
    premium = html.index('/premium.css?v=10.0-product')
    assert app < shell < theme < premium


def test_dashboard_has_single_product_hero_and_mobile_route_title():
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    assert html.count('id="overview"') == 1
    assert 'class="dashboard-stage"' in html
    assert 'class="hero-visual"' in html
    assert 'id="mobileViewTitle"' in html


def test_premium_theme_keeps_route_specific_accent_and_mobile_dock():
    css = (ROOT / 'premium.css').read_text(encoding='utf-8')
    assert 'body[data-view="media"]' in css
    assert '.metric-card .ring' in css
    assert '.mobile-nav' in css
    assert 'conic-gradient' in css
