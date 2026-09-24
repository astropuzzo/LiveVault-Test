from pathlib import Path

STATIC = Path(__file__).parents[1] / "app" / "static"


def test_live_nsfw_ui_hooks_and_icons_are_wired():
    icons = (STATIC / "icons.svg").read_text(encoding="utf-8")
    for name in ("nsfw-breast", "nsfw-butt", "nsfw-vulva", "nsfw-phallus"):
        assert f'id="{name}"' in icons
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "pulseNsfwLayer(profileSessions, xFor)" in app
    assert "data-dynamic-left" in app  # positions set by JS, CSP forbids inline styles
    nsfw = (STATIC / "nsfw.js").read_text(encoding="utf-8")
    assert "window.pulseNsfwLayer" in nsfw and "data-nsfw-pulse" in nsfw
    assert "nsfw-tl-pin" in nsfw and "liveMarkup(info.live)" in nsfw
    assert 'style=' not in nsfw
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for field in ("setNsfwLive", "setNsfwLiveFps", "setNsfwLiveLoad"):
        assert f'id="{field}"' in html
