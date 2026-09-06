from pathlib import Path
import re

ROOT = Path(__file__).parents[1]
HTML = (ROOT / 'control-panel/static/index.html').read_text(encoding='utf-8')
JS = (ROOT / 'control-panel/static/app.js').read_text(encoding='utf-8')
SW = (ROOT / 'control-panel/static/sw.js').read_text(encoding='utf-8')


def test_control_center_has_exact_primary_views():
    views = re.findall(r'data-view-panel="([a-z]+)"', HTML)
    assert views == ['dashboard', 'media', 'storage', 'system', 'advanced']


def test_desktop_and_mobile_navigation_match_views():
    routes = re.findall(r'data-route="([a-z]+)"', HTML)
    assert routes == ['dashboard','media','storage','system','advanced'] * 2


def test_html_ids_are_unique():
    ids = re.findall(r'\bid="([^"]+)"', HTML)
    assert len(ids) == len(set(ids))


def test_expensive_views_are_lazy_loaded():
    assert "currentView !== 'system'" in JS
    assert "currentView === 'media'" in JS
    assert "setInterval(() => { if (currentView === 'media')" in JS


def test_pwa_shell_contains_new_layout_css():
    assert '/shell.css' in SW
    assert 'openastro-control-v4' in SW
