from pathlib import Path
import pytest

from app.stripchat_preview import newest_preview_source, preview_paths_from_argv


ROOT = Path(__file__).resolve().parents[1]


def test_stripchat_preview_cli_paths_are_parsed():
    output, preview = preview_paths_from_argv([
        "--slug", "example",
        "--output-pattern", "/tmp/session/example_part%03d.mp4",
        "--preview", "/tmp/live_previews/24.jpg",
    ])

    assert output == "/tmp/session/example_part%03d.mp4"
    assert preview == "/tmp/live_previews/24.jpg"


def test_stripchat_preview_uses_growing_mouflon_capture(tmp_path):
    raw = tmp_path / "example_part001.capture.mp4"
    raw.write_bytes(b"growing-fragmented-mp4")
    active = tmp_path / ".active-preview.mp4"
    try:
        active.symlink_to(raw.name)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable; covered by Linux CI")
        raise

    selected = newest_preview_source(str(tmp_path / "example_part%03d.mp4"))

    assert selected in {active, raw}
    assert selected is not None and selected.stat().st_size > 0


def test_stripchat_preview_uses_direct_flashphoner_part(tmp_path):
    part = tmp_path / "example_part001.mp4"
    part.write_bytes(b"growing-flashphoner-fmp4")

    selected = newest_preview_source(str(tmp_path / "example_part%03d.mp4"))

    assert selected == part


def test_stripchat_entrypoint_starts_preview_worker():
    entrypoint = (ROOT / "app/stripchat_capture/__main__.py").read_text(encoding="utf-8")
    assert "stripchat_preview_worker" in entrypoint
    assert "with stripchat_preview_worker()" in entrypoint
