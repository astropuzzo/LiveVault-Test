import json
from pathlib import Path
from types import SimpleNamespace

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
    manager.active[9] = SimpleNamespace(directory=tmp_path, extension=".mp4")

    assert manager.active_capture_path(9) == capture
    assert manager.active_capture_path(10) is None


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
