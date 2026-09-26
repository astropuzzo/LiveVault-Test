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


def test_phone_timeline_scrolls_and_groups_pins():
    tuning = (STATIC / "pulse-tuning.js").read_text(encoding="utf-8")
    assert "window.pulsePixelsPerHour" in tuning and "cr-pulse-scroll" in tuning and "cr-pulse-now" in tuning
    nsfw = (STATIC / "nsfw.js").read_text(encoding="utf-8")
    assert "data-nsfw-pulse-group" in nsfw and "openPulseRow" in nsfw


def test_cronologia_nsfw_strip_is_one_mark_per_fact():
    """3.4.19: a strip under each session bar, icons only on long stretches."""
    nsfw = (STATIC / "nsfw.js").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    # No floating pins or count badges in the Cronologia any more.
    assert "nsfw-pulse-mark" not in nsfw and "nsfw-pulse-band" not in nsfw
    assert "BADGE_MIN_PX = 44" in nsfw and "JOIN_PX = 6" in nsfw and "nsfw-strip-hit" in nsfw
    # Entrances are added after rendering, never in the markup (setMarkup skips identical
    # re-renders, so a refresh cannot cut an entrance short).
    assert "animateNewPulseItems(pulse)" in nsfw and " enter" not in nsfw.split("window.pulseNsfwLayer")[1].split("};")[0]
    # The legend follows the strip, not the old pins.
    assert "pulse?.querySelector('.nsfw-run')" in nsfw
    # Uniform 36px tracks: bars stay aligned across rows.
    assert ".cr-pulse-track {\n  position: relative;\n  isolation: isolate;\n  height: 36px;" in css
    for name in ("nsfw-wipe", "nsfw-icon-in", "nsfw-ping"):
        assert f"@keyframes {name}" in css
    guard = css.index("@media (prefers-reduced-motion: reduce) {\n  .nsfw-strip,")
    assert "animation: none !important" in css[guard:guard + 200]
