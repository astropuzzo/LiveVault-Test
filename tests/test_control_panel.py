from __future__ import annotations

import importlib.util
import io
import json
import threading
import sys
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


@pytest.mark.parametrize("raw,expected", [(134, 0.67), (-1, 0.0), (2047, None), (-2, None)])
def test_adc_input_scaling_signed_samples_and_restore(monkeypatch, raw, expected):
    writes = []
    reads = iter([b"\x85\x83", (1147 << 4).to_bytes(2, "big"), (raw << 4).to_bytes(2, "big", signed=True)])
    class Bus:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def fileno(self): return 7
        def write(self, data): writes.append(data)
        def read(self, size): return next(reads)
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(ioctl=lambda *args: None))
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: Bus())
    monkeypatch.setattr(panel.time, "sleep", lambda _: None)
    result = panel.read_input_power()
    assert writes[-1] == b"\x01\x85\x83"
    if expected is None:
        assert result["watts"] is None and result["measurement"] == "unavailable"
    else:
        assert result["input_amps"] == expected
        assert result["watts"] == round(12.0435 * expected, 3)
        assert result["measurement"] == "measured"


def test_adc_missing_is_not_zero_or_estimate(monkeypatch):
    def missing(*args, **kwargs): raise FileNotFoundError()
    monkeypatch.setattr("builtins.open", missing)
    assert panel.read_input_power()["watts"] is None


def test_energy_integrates_samples_without_bridging_outages_or_estimates():
    def point(t, watts, kind="measured"):
        return {"t": t, "watts": watts, "power_measurement": kind}
    energy = panel.measured_energy([point(0, 10), point(10, 20), point(20, 30, "estimated"),
                                    point(30, 10), point(70, 10), point(80, 10)])
    assert energy["covered_seconds"] == 20
    assert energy["wh"] == round(250 / 3600, 4)
    assert energy["average_watts"] == 12.5
    assert panel.measured_energy([point(0, 0)])["wh"] is None


def test_old_energy_history_remains_estimated(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    path.write_text(json.dumps([{"t": time.time(), "watts": 6}]))
    monkeypatch.setattr(panel, "HISTORY_FILE", path)
    monkeypatch.setattr(panel, "_history", [])
    panel.load_history()
    assert panel._history[0]["watts"] is None
    assert panel._history[0]["estimated_watts"] == 6
    assert panel._history[0]["power_measurement"] == "estimated"
