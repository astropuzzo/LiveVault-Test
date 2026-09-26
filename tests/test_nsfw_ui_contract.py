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
    assert "data-nsfw-pulse-group" in nsfw and "nsfw-pin-count" in nsfw


def test_cronologia_nsfw_lane_is_readable_and_animates_only_new_items():
    """3.4.18: pins above a lane, bands under them, never over the session bar."""
    nsfw = (STATIC / "nsfw.js").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    # Only first appearances animate; the dashboard re-renders every few seconds.
    assert "seenBands" in nsfw and "seenPins" in nsfw and " enter d${" in nsfw
    # Multi-part moments: two-colour ring, no stacked mini icon on the timeline pins.
    assert "cat2Class(cls)" in nsfw and "nsfw-pulse-rail" in nsfw
    assert ".cr-pulse-track:has(> .nsfw-pulse-layer)" in css and "--pin:" in css
    for name in ("nsfw-band-draw", "nsfw-pin-pop", "nsfw-band-flow", "nsfw-comet"):
        assert f"@keyframes {name}" in css
    # Phones no longer hide the bands, and reduced motion stops every animation.
    assert ".nsfw-pulse-band {\n    display: none;" not in css
    guard = css.index("@media (prefers-reduced-motion: reduce) {\n  .nsfw-pulse-band,")
    assert "animation: none !important" in css[guard:guard + 200]
