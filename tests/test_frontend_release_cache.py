from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_service_worker_forces_fresh_release_assets():
    sw = (ROOT / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    assert "const RELEASE='2.8.12-r1'" in sw
    assert "cache:'no-store'" in sw
    assert "SHELL_PATHS" in sw
    assert "livevault-shell-${RELEASE}" in sw


def test_service_worker_covers_all_frontend_shell_assets():
    sw = (ROOT / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    for asset in (
        "/static/style.css",
        "/static/enhancements.css",
        "/static/app.js",
        "/static/icon.svg",
        "/manifest.webmanifest",
    ):
        assert asset in sw
