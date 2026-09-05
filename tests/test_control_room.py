from pathlib import Path


def test_control_room_ui_hooks_exist():
    js = Path("app/static/app.js").read_text(encoding="utf-8")
    css = Path("app/static/style.css").read_text(encoding="utf-8")
    assert "LiveVault Control Room v2.7.1" in js
    assert "controlRoomProfileRows" in js
    assert "data-live-wall" in js
    assert "data-focus-toggle" in js
    assert "preview_updated_at" in js
    assert ".cr-live-grid" in css
    assert ".cr-wall-grid" in css
    assert ".cr-compact-row" in css


def test_preview_and_focus_backend_hooks_exist():
    recorder = Path("app/recorder.py").read_text(encoding="utf-8")
    main = Path("app/main.py").read_text(encoding="utf-8")
    workers = Path("app/workers.py").read_text(encoding="utf-8")
    utils = Path("app/utils.py").read_text(encoding="utf-8")
    db = Path("app/db.py").read_text(encoding="utf-8")
    assert "LIVE_PREVIEW_INTERVAL_SECONDS = 20" in recorder
    assert "live_preview_path" in recorder
    assert "@app.get(\"/api/sources/{source_id}/preview\")" in main
    assert "async def source_live_preview" in main
    assert "await manager.live_preview_for(source_id)" in main
    assert "async def live_preview_for" in workers
    assert "generate_live_preview" in utils
    assert "preview_updated_at" in main
    assert "focus: bool | None = None" in main
    assert "focus: Mapped[bool]" in db
    assert "ALTER TABLE profiles ADD COLUMN focus" in db
