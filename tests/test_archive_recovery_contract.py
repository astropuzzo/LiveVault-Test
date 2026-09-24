from pathlib import Path

from tests.css_contract import has_css, stylesheet

ROOT = Path(__file__).resolve().parents[1]


def test_archive_failed_media_has_one_click_recovery_contract():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    app_js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    ui_js = (ROOT / "app/static/ui.js").read_text(encoding="utf-8")
    css = stylesheet()

    assert '@app.post("/api/recordings/{recording_id}/recover")' in main
    assert "await manager._prepare_mp4(path)" in main
    assert "verify_media, path, runtime().integrity_mode" in main
    assert "data-rec-action=\"recover\"" in ui_js
    assert "Recupera file" in ui_js
    assert "/api/recordings/${id}/recover" in app_js
    assert has_css(css, ".archive-surface") and has_css(css, "overflow: visible")
