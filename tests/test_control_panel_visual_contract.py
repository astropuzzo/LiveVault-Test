from pathlib import Path

ROOT = Path(__file__).parents[1] / 'control-panel' / 'static'


def test_one_authoritative_theme_follows_only_functional_components():
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    assert html.index('/diagnostics.css?v=') < html.index('/app.css?v=3.5.1')
    assert html.index('/media-upload.css?v=') < html.index('/app.css?v=3.5.1')
    for retired in ('shell.css', 'theme.css', 'premium.css', 'magic.css', 'glass.css'):
        assert retired not in html


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
    assert '<button id="quickNvmeAction" data-action="eject_nvme"' in html
    assert 'data-action="restart_livevault"' in html
    assert 'data-action="restart_docker"' in html
    assert 'data-action="reboot"' in html
    assert 'https://openastro.tailf2871c.ts.net:10000/' in html


def test_spatial_theme_keeps_operational_meters_and_mobile_dock():
    css = (ROOT / 'app.css').read_text(encoding='utf-8')
    assert '.node-meters' in css and '.live-rings' in css
    assert '.mobile-nav' in css and 'conic-gradient' in css
    assert 'Mona Sans' in css and 'prefers-reduced-motion' in css


def test_new_layout_keeps_progressive_disclosure_and_player_transport():
    css = (ROOT / 'app.css').read_text(encoding='utf-8')
    js = (ROOT / 'app.js').read_text(encoding='utf-8')
    for selector in ['.app-tile', '.power-gauge', '.media-feature-card', '.resource-details.open', '.media-tech-open', '.system-tab-telemetry', '.media-video-controls', '.media-subtitle-overlay']:
        assert selector in css
    assert "setHeroBar('#heroCpuBar'" in js
    assert "media.classList.toggle('media-tech-open')" in js


def test_shared_motion_and_local_font_are_available_offline():
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    sw = (ROOT / 'sw.js').read_text(encoding='utf-8')
    assert '/motion.js?v=3.5.1' in html
    assert '"/motion.js"' in sw and '"/fonts/mona-sans-latin-wght.woff2"' in sw
