import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.main import _video_media_type
from app.recorder import write_stitch_marker
from app.workers import WorkerManager


ROOT = Path(__file__).resolve().parents[1]


def test_stitch_marker_is_atomic_and_durable(tmp_path):
    marker = tmp_path / ".livevault-stitch-session.json"
    payload = {"source_id": 7, "session_id": "safe-session"}

    write_stitch_marker(marker, payload)

    assert json.loads(marker.read_text(encoding="utf-8")) == payload
    assert not marker.with_name(f"{marker.name}.tmp").exists()


def test_active_capture_can_be_exposed_while_ffmpeg_is_writing(tmp_path):
    capture = tmp_path / "part000.mp4"
    capture.write_bytes(b"fragmented-mp4")
    manager = WorkerManager()
    manager.active[9] = SimpleNamespace(
        directory=tmp_path,
        extension=".mp4",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )

    assert manager.active_capture_path(9) == capture
    assert manager.active_capture_path(10) is None


def test_stripchat_active_capture_prefers_current_webm_over_stale_mp4(tmp_path):
    stale = tmp_path / "creator_old_part001.mp4"
    stale.write_bytes(b"old")
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()
    os.utime(stale, (old_time, old_time))
    current = tmp_path / "creator_current_part001.capture.webm"
    current.write_bytes(b"webm-live")
    manager = WorkerManager()
    manager.active[9] = SimpleNamespace(
        directory=tmp_path,
        extension=".mp4",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )

    assert manager.active_capture_path(9) == current


def test_video_media_types_include_browser_playable_webm():
    assert _video_media_type(Path("capture.mp4")) == "video/mp4"
    assert _video_media_type(Path("capture.webm")) == "video/webm"
    assert _video_media_type(Path("capture.mkv")) == "video/x-matroska"


def test_misnamed_media_recorder_mp4_is_detected_from_signature(tmp_path):
    capture = tmp_path / "part001.capture.webm"
    capture.write_bytes(b"\x00\x00\x00\x24ftypisom")

    assert _video_media_type(capture) == "video/mp4"


def test_active_capture_prefers_independently_finalized_browser_preview(tmp_path):
    source = tmp_path / "creator_part001.capture.mp4"
    source.write_bytes(b"fragmented-mp4")
    preview = tmp_path / ".active-preview.mp4"
    preview.write_bytes(b"finalized-preview")
    manager = WorkerManager()
    manager.active[9] = SimpleNamespace(
        directory=tmp_path,
        extension=".mp4",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )

    selected = manager.playable_active_capture_path(9)

    assert selected == preview
    assert source.read_bytes() == b"fragmented-mp4"


def test_recovery_and_local_preview_controls_are_wired():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")

    for route in (
        '@app.get("/api/fragments/{fragment_id}/view")',
        '@app.get("/api/sources/{source_id}/capture")',
        '@app.post("/api/recovery/run")',
        '@app.delete("/api/status/errors")',
    ):
        assert route in main
    assert '"local_captures": local_captures' in main
    assert 'data-profile-action="local-capture"' in js
    assert 'data-local-video=' in js
    assert 'id="retryRecoveryBtn"' in html
    assert 'id="clearErrorsBtn"' in html
    assert '<button id="healthPill"' in html
