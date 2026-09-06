from __future__ import annotations

import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "control_panel_media_regression",
    Path(__file__).parents[1] / "control-panel/server.py",
)
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)


@pytest.fixture
def control_server(monkeypatch):
    monkeypatch.setattr(panel.Handler, "log_message", lambda *args: None)
    monkeypatch.setattr(
        panel,
        "_sessions",
        {"test": {"expires": time.time() + 60, "csrf": "csrf-test"}},
    )
    server = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_static_csp_allows_hls_media_source_and_worker(control_server):
    with urllib.request.urlopen(control_server + "/") as response:
        csp = response.headers.get("Content-Security-Policy", "")
    assert "media-src 'self' blob:" in csp
    assert "worker-src 'self' blob:" in csp
    assert "style-src-attr 'unsafe-inline'" in csp


def test_hls_runtime_failure_is_structured_json(control_server, monkeypatch):
    def fail_start(*args, **kwargs):
        raise RuntimeError("Transcode HLS non avviato. ffmpeg failed")

    monkeypatch.setattr(panel.media_streaming, "start_hls", fail_start)
    payload = json.dumps({"uuid": "USB-1", "path": "movie.mkv", "position": 0}).encode()
    request = urllib.request.Request(
        control_server + "/api/media/hls/start",
        data=payload,
        headers={
            "Cookie": "openastro_session=test",
            "X-CSRF-Token": "csrf-test",
            "Content-Type": "application/json",
        },
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 503
    body = json.loads(error.value.read().decode("utf-8"))
    assert body["ok"] is False
    assert "ffmpeg failed" in body["error"]
