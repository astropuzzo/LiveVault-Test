from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_storyboard_is_nine_frame_v2():
    utils = (ROOT / "app/utils.py").read_text(encoding="utf-8")
    workers = (ROOT / "app/workers.py").read_text(encoding="utf-8")
    assert "Create a 3x3 storyboard from nine evenly spaced moments" in utils
    assert "for index in range(9)" in utils
    assert "[v6][v7][v8]hstack=inputs=3[row2]" in utils
    assert "[row0][row1][row2]vstack=inputs=3[sheet]" in utils
    assert workers.count("-sheet-v2.jpg") == 3
    assert "-sheet-v1.jpg" not in workers


def test_pulse_exposes_exact_recording_media():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "app/static/enhancements.css").read_text(encoding="utf-8")
    for token in ('"recordings": recording_segments', '"remote_url": str(recording.remote_url or "")', '"thumbnail_url": _safe_thumbnail_url'):
        assert token in main
    for token in ("pulseRecordingFiles", "cr-pulse-rec-media", "data-preview-url", 'target="_blank"', "ensurePulseMediaPreview"):
        assert token in js
    assert ".cr-pulse-media-preview" in css
    assert "position:fixed" in css


def test_release_tracks_current_version():
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "2.8.9"
    assert 'VERSION = "2.8.9"' in (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "livevault-shell-v2.8.9" in (ROOT / "app/static/sw.js").read_text(encoding="utf-8")
