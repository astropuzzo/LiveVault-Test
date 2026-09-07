from pathlib import Path

ROOT = Path(__file__).parents[1] / 'control-panel' / 'static'


def test_product_visual_layers_are_explicit_and_ordered():
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    app = html.index('/app.css?v=')
    shell = html.index('/shell.css?v=4.0-control')
    theme = html.index('/theme.css?v=5.0-feel')
    premium = html.index('/premium.css?v=10.0-product')
    magic = html.index('/magic.css?v=13.0-ops')
    assert app < shell < theme < premium < magic


def test_dashboard_has_single_product_hero_and_mobile_route_title():
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    assert html.count('id="overview"') == 1
    assert 'class="dashboard-mosaic"' in html
    assert 'class="node-meters"' in html
    assert 'id="resourceToggle"' in html
    assert 'id="mediaTechToggle"' in html
    assert 'data-system-tab="power"' in html
    assert 'data-system-tab="telemetry"' in html
    assert 'id="mobileViewTitle"' in html
    assert 'id="quickNvmeAction"' in html
    assert 'data-action="restart_livevault"' in html
    assert 'data-action="restart_docker"' in html
    assert 'data-action="reboot"' in html
    assert 'https://openastro.tailf2871c.ts.net:10000/' in html


def test_premium_theme_keeps_route_specific_accent_and_mobile_dock():
    css = (ROOT / 'premium.css').read_text(encoding='utf-8')
    assert 'body[data-view="media"]' in css
    assert '.metric-card .ring' in css
    assert '.mobile-nav' in css
    assert 'conic-gradient' in css


def test_visual_qa_layer_uses_data_driven_surfaces_and_progressive_disclosure():
    css = (ROOT / 'magic.css').read_text(encoding='utf-8')
    js = (ROOT / 'app.js').read_text(encoding='utf-8')
    assert '.node-meters' in css
    assert '.app-tile' in css
    assert '.live-rings' in css
    assert '.power-gauge' in css
    assert '.media-feature-card' in css
    assert '.resource-details.open>.metrics-grid' in css
    assert '.media-tech-open' in css
    assert '.system-tab-telemetry' in css
    assert 'backdrop-filter:blur(28px)' in css
    assert "setHeroBar('#heroCpuBar'" in js
    assert "media.classList.toggle('media-tech-open')" in js
