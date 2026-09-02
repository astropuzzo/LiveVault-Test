from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_index_has_no_inline_script_or_event_handlers():
    html = (STATIC / "index.html").read_text(encoding="utf-8").lower()
    assert "<script>" not in html
    for handler in ("onclick=", "onchange=", "onsubmit=", "onload="):
        assert handler not in html


def test_generated_ui_uses_delegated_events():
    js = (STATIC / "app.js").read_text(encoding="utf-8").lower()
    assert "onclick=" not in js
    assert "data-action=" in js
    assert "data-rec-action=" in js
    assert "if(!value)return ''" in js


def test_pwa_service_worker_exists():
    assert (STATIC / "sw.js").is_file()
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "serviceWorker.register('/sw.js')" in js
    assert "livevault-shell-v" in (STATIC / "sw.js").read_text(encoding="utf-8")
