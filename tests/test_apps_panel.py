from __future__ import annotations

import importlib.util
import io
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("apps_control_panel", ROOT / "control-panel/server.py")
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
management = panel.app_management


def summary():
    start = date(2026, 9, 9)
    return {
        "service": "bonsai-sensei-billing", "name": "Bonsai Sensei", "version": "1.0.0",
        "status": "preparing", "billing_enabled": False,
        "generated_at": "2026-10-08T10:00:00Z",
        "purchases": {"total": 3, "active": 1, "pending_purchase": 1, "processing": 0,
                      "revoked": 1, "permanent": 2, "consumable": 1},
        "users": {"purchasing_accounts": 2, "registered": None, "active": None},
        "daily_utc": [{"date": (start + timedelta(days=i)).isoformat(), "purchases": 3 if i == 29 else 0,
                       "revocations": 1 if i == 29 else 0} for i in range(30)],
        "catalog": [{"id": "garden_pass", "kind": "permanent"}],
        "missing_configuration": ["RTDN_CONFIGURATION"],
    }


def wire_summary(monkeypatch, payload=None):
    calls = []
    monkeypatch.setattr(management, "_admin_key", lambda: "server-only-key-never-browser-123456")
    def upstream(request):
        calls.append(request)
        return io.BytesIO(json.dumps(payload if payload is not None else summary()).encode())
    monkeypatch.setattr(management, "_open_backend", upstream)
    return calls


@pytest.fixture
def control_server(monkeypatch):
    monkeypatch.setattr(panel.Handler, "log_message", lambda *args: None)
    monkeypatch.setattr(panel, "_sessions", {"apps-test": {"expires": time.time() + 60, "csrf": "unused"}})
    server = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def authenticated(url):
    return urllib.request.Request(url, headers={"Cookie": "openastro_session=apps-test"})


def test_apps_endpoint_requires_session_before_reading_secret_or_backend(control_server, monkeypatch):
    calls = wire_summary(monkeypatch)
    with pytest.raises(urllib.error.HTTPError) as failure:
        urllib.request.urlopen(control_server + "/api/apps/summary")
    assert failure.value.code == 401
    assert calls == []


def test_authenticated_summary_uses_fixed_backend_and_drops_private_fields(control_server, monkeypatch):
    raw = summary()
    raw.update({"token": "private-purchase-token", "owner": "private-owner", "error": "raw-error"})
    raw["users"]["registered"] = 99999
    raw["purchases"]["owners"] = ["private-owner"]
    raw["missing_configuration"] += ["https://private.example/?secret=raw-error"]
    calls = wire_summary(monkeypatch, raw)
    with urllib.request.urlopen(authenticated(control_server + "/api/apps/summary?url=https://evil.example/")) as response:
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        encoded = response.read().decode()
    data = json.loads(encoded)
    app = data["apps"][0]
    assert len(data["apps"]) == 1 and app["name"] == "Bonsai Sensei"
    assert app["purchases"]["total"] == 3 and app["users"]["purchasing_accounts"] == 2
    assert app["users"]["registered"] is None and app["users"]["active"] is None
    assert len(app["daily_utc"]) == 30 and app["daily_utc"][-1]["purchases"] == 3
    assert app["missing_configuration"] == ["RTDN_CONFIGURATION"]
    assert calls[0].full_url == "http://127.0.0.1:8095/internal/admin/summary"
    assert calls[0].get_header("Authorization") == "Bearer server-only-key-never-browser-123456"
    for private in ["private-purchase-token", "private-owner", "raw-error", "server-only-key", "evil.example", "127.0.0.1:8095"]:
        assert private not in encoded


@pytest.mark.parametrize("failure", [OSError("/etc/private/path secret-key"), TimeoutError("raw-upstream-token"),
    urllib.error.HTTPError("http://private/?secret=123", 500, "private-error", {}, None)])
def test_upstream_failures_return_safe_503_and_unknown_counts(control_server, monkeypatch, failure):
    wire_summary(monkeypatch)
    def unavailable(request):
        raise failure
    monkeypatch.setattr(management, "_open_backend", unavailable)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(authenticated(control_server + "/api/apps/summary"))
    assert error.value.code == 503
    encoded = error.value.read().decode()
    data = json.loads(encoded)
    app = data["apps"][0]
    assert app["available"] is False and app["purchases"] is None
    assert app["users"]["purchasing_accounts"] is None and app["daily_utc"] == []
    assert "temporaneamente non disponibile" in data["error"]
    for private in ["secret-key", "raw-upstream-token", "private-error", "/etc/private", "secret=123"]:
        assert private not in encoded


def test_missing_key_never_calls_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(management, "ADMIN_KEY_FILE", tmp_path / "absent.key")
    calls = []
    monkeypatch.setattr(management, "_open_backend", lambda request: calls.append(request))
    data, status = management.apps_summary()
    assert status == 503 and calls == []
    assert data["apps"][0]["purchases"] is None


@pytest.mark.parametrize("bad", [True, -1, 1.5, "1", None, 9007199254740992])
def test_malformed_counts_are_unknown_instead_of_fabricated_zero(monkeypatch, bad):
    raw = summary()
    raw["purchases"]["active"] = bad
    wire_summary(monkeypatch, raw)
    data, status = management.apps_summary()
    assert status == 503 and data["apps"][0]["purchases"] is None


def test_empty_real_ledger_keeps_zero_distinct_from_unavailable(monkeypatch):
    raw = summary()
    raw["purchases"] = {key: 0 for key in management.PURCHASE_FIELDS}
    raw["users"]["purchasing_accounts"] = 0
    for row in raw["daily_utc"]:
        row.update(purchases=0, revocations=0)
    wire_summary(monkeypatch, raw)
    data, status = management.apps_summary()
    app = data["apps"][0]
    assert status == 200 and app["available"] is True
    assert app["purchases"]["total"] == 0 and app["users"]["purchasing_accounts"] == 0
    assert app["revenue"]["available"] is False and app["ads"]["available"] is False


def test_backend_redirect_cannot_forward_bearer_to_another_origin():
    handler = management._NoRedirect()
    request = urllib.request.Request("http://127.0.0.1:8095/internal/admin/summary", headers={"Authorization":"Bearer private"})
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://external.example/") is None


def test_backend_timeout_and_proxy_bypass_are_fixed(monkeypatch):
    calls=[]
    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url,timeout))
            return io.BytesIO(b"{}")
    def build(*handlers):
        assert handlers[0].proxies == {}
        assert isinstance(handlers[1], management._NoRedirect)
        return Opener()
    monkeypatch.setattr(management.urllib.request, "build_opener", build)
    management._open_backend(urllib.request.Request(management.APP_REGISTRY[0]["summary_url"]))
    assert calls == [("http://127.0.0.1:8095/internal/admin/summary", 3)]
