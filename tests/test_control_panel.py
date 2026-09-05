from __future__ import annotations

import importlib.util
import io
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("control_panel", Path(__file__).parents[1] / "control-panel/server.py")
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)


def test_state_cache_shares_probes_but_isolates_session_fields(monkeypatch):
    calls = []
    monkeypatch.setattr(panel, "_state_cache", {})
    monkeypatch.setattr(panel, "state", lambda: calls.append(1) or {"host": {"cpu": 7}})
    first = panel.cached_state()
    first["csrf"] = "private"
    first["host"]["cpu"] = 99
    assert panel.cached_state() == {"host": {"cpu": 7}}
    assert len(calls) == 1
    monkeypatch.setattr(panel, "_state_cached_at", time.monotonic() - 6)
    panel.cached_state()
    assert len(calls) == 2


@pytest.mark.parametrize("body", [b"[]", b"null", b'"text"', b"{" , b"x" * 4097])
def test_payload_rejects_non_objects_and_oversized_requests(body):
    request = SimpleNamespace(headers={"Content-Length": str(len(body))}, rfile=io.BytesIO(body))
    with pytest.raises(ValueError):
        panel.Handler.read_payload(request)


def test_history_retains_missing_readings_and_latest_sample(monkeypatch):
    now = int(time.time())
    rows = [{"t": now - 1000 + i, "temp": None} for i in range(501)]
    monkeypatch.setattr(panel, "_history", rows)
    result = panel.history_payload(3600)
    assert result["points"][-1] == rows[-1]
    assert result["points"][0] == rows[0]
    assert all(row["temp"] is None for row in result["points"])
    assert len(result["points"]) <= 241


@pytest.fixture
def control_server(monkeypatch):
    monkeypatch.setattr(panel.Handler, "log_message", lambda *args: None)
    monkeypatch.setattr(panel, "_sessions", {"test": {"expires": time.time() + 60, "csrf": "csrf-test"}})
    server = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_protected_state_and_missing_asset_are_real_http_errors(control_server):
    for path, expected in [("/api/state", 401), ("/missing.js", 404)]:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(control_server + path)
        assert error.value.code == expected


def test_action_requires_csrf_and_serializes_operations(control_server, monkeypatch):
    calls = []
    monkeypatch.setattr(panel, "run", lambda *args: calls.append(args) or (0, "done"))
    payload = json.dumps({"action": "backup_now"}).encode()
    request = urllib.request.Request(control_server + "/api/action", data=payload, headers={"Cookie": "openastro_session=test"})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403 and not calls
    request.add_header("X-CSRF-Token", "csrf-test")
    panel._action_lock.acquire()
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 409 and not calls
    finally:
        panel._action_lock.release()
    with urllib.request.urlopen(request) as response:
        assert json.load(response)["ok"]
    assert len(calls) == 1


def test_static_assets_are_compressed_without_changing_content(control_server):
    import gzip
    request = urllib.request.Request(control_server + "/app.css", headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(request) as response:
        assert response.headers["Content-Encoding"] == "gzip"
        assert gzip.decompress(response.read()) == (panel.STATIC / "app.css").read_bytes()
