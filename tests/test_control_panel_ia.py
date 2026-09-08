from pathlib import Path
import json
import re
import re

ROOT = Path(__file__).parents[1]
HTML = (ROOT / 'control-panel/static/index.html').read_text(encoding='utf-8')
JS = (ROOT / 'control-panel/static/app.js').read_text(encoding='utf-8')
SW = (ROOT / 'control-panel/static/sw.js').read_text(encoding='utf-8')


def test_control_center_has_exact_primary_views():
    views = re.findall(r'data-view-panel="([a-z]+)"', HTML)
    assert views == ['dashboard', 'media', 'storage', 'system', 'nina', 'pihole', 'advanced']


def test_desktop_and_mobile_navigation_match_views():
    expected = ['dashboard','media','storage','system','nina','pihole','advanced']
    side = re.search(r'<nav class="side-nav".*?</nav>', HTML, re.S)
    mobile = re.search(r'<nav class="mobile-nav".*?</nav>', HTML, re.S)
    assert side and mobile
    assert re.findall(r'data-route="([a-z]+)"', side.group(0)) == expected
    assert re.findall(r'data-route="([a-z]+)"', mobile.group(0)) == expected


def test_html_ids_are_unique():
    ids = re.findall(r'\bid="([^"]+)"', HTML)
    assert len(ids) == len(set(ids))


def test_expensive_views_are_lazy_loaded():
    assert "currentView !== 'system'" in JS
    assert "currentView === 'media'" in JS
    assert "setInterval(() => { if (currentView === 'media')" in JS


def test_pwa_shell_contains_new_layout_css():
    assert '/shell.css' in SW
    assert 'openastro-control-v20.2-cache-repair' in SW
    assert '/magic.css' in SW


def test_nina_monitor_is_embedded_with_separate_runtime_and_local_public_entrypoints():
    assert 'data-view-panel="nina"' in HTML
    assert 'id="ninaMonitorFrame"' in HTML
    assert 'src="https://openastro.tailf2871c.ts.net:9091/"' in HTML
    assert 'https://openastro.tailf2871c.ts.net:9091/' in HTML
    assert 'http://192.168.1.27:9091/' in HTML
    assert 'Container Coolify indipendente' in HTML
    assert 'Il browser non contatta direttamente N.I.N.A.' in HTML
    assert 'il token QSM resta nel container NINA Monitor' in HTML
def test_service_worker_precache_contains_only_existing_assets():
    static = Path(__file__).resolve().parents[1] / 'control-panel' / 'static'
    worker = (static / 'sw.js').read_text()
    assets = json.loads(re.search(r'const SHELL = (\[.*?\]);', worker).group(1))
    assert all((static / asset.lstrip('/')).is_file() for asset in assets)
