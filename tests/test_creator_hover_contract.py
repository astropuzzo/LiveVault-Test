import re
from pathlib import Path

from tests.css_contract import has_css, stylesheet

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / 'app/static/creator-hover.js').read_text(encoding='utf-8')
CSS = stylesheet()
HTML = (ROOT / 'app/static/index.html').read_text(encoding='utf-8')
SW = (ROOT / 'app/static/sw.js').read_text(encoding='utf-8')
MAIN = (ROOT / 'app/main.py').read_text(encoding='utf-8')


def test_creator_hover_is_lazy_cached_and_desktop_safe():
    assert 'const HOVER_DELAY_MS = 250;' in JS
    assert 'const CACHE_TTL_MS = 90_000;' in JS
    assert "(hover: hover) and (pointer: fine)" in JS
    assert "event.pointerType === 'touch'" in JS
    assert '/api/sources/${sourceId}/hover-preview' in JS
    assert '/api/sources/${sourceId}/profile' in JS
    assert 'previewFromProfile' in JS
    assert "pending.has(sourceId)" in JS
    assert "recent_recordings" in JS
    assert "slice(0, 3)" in JS
    assert "event.stopImmediatePropagation()" in JS
    assert "previewAlreadyOpen" in JS
    assert has_css(CSS, "@media(hover:none) and (pointer:coarse){.creator-hover-card{display:block")
    assert has_css(CSS, "pointer-events:auto")
    assert has_css(CSS, ".creator-hover-card{box-sizing:border-box;")
    assert not re.search(r"\.creator-hover-card[^{}]*\{[^}]*display:none!important", CSS)


def test_creator_hover_assets_and_endpoint_are_wired():
    assert '@app.get("/api/sources/{source_id}/hover-preview")' in MAIN
    assert '.limit(3)' in MAIN
    assert '/static/style.css' in HTML
    assert '/static/creator-hover.js' in HTML
    assert '/static/style.css' in SW
    assert '/static/creator-hover.js' in SW
    assert has_css(CSS, '.creator-hover-videos')
    assert has_css(CSS, 'grid-template-columns:repeat(3')
