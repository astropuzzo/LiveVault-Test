from pathlib import Path

from app import main


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_consistent_across_runtime_docs_and_pwa():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    assert version == "2.8.16"
    assert main.VERSION == version
    assert f"# LiveVault v{version}" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"# LiveVault v{version} — START HERE" in (ROOT / "START_HERE.md").read_text(encoding="utf-8")
    assert f"## {version} —" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"livevault-shell-v{version}" in (ROOT / "app" / "static" / "sw.js").read_text(encoding="utf-8")
