from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1] if Path(__file__).name == "test_pihole_control.py" else Path.cwd()
CONTROL = ROOT / "control-panel"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

status_spec = importlib.util.spec_from_file_location("pihole_status_test", CONTROL / "pihole_status.py")
pihole_status = importlib.util.module_from_spec(status_spec)
status_spec.loader.exec_module(pihole_status)

panel_spec = importlib.util.spec_from_file_location("control_panel_pihole", CONTROL / "server.py")
panel = importlib.util.module_from_spec(panel_spec)
panel_spec.loader.exec_module(panel)


def test_status_distinguishes_dns_blocking_and_remote_state(monkeypatch):
    monkeypatch.setattr(pihole_status.Path, "exists", lambda self: str(self) == "/etc/pihole/pihole.toml")
    monkeypatch.setattr(pihole_status.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pihole_status, "_service_state", lambda: "active")
    monkeypatch.setattr(pihole_status, "_dns_online", lambda: True)
    monkeypatch.setattr(pihole_status, "_lan_ip", lambda: "192.168.1.27")

    def fake_api(path: str):
        if path == "/api/info/version":
            return {
                "version": {
                    "core": {"local": {"version": "v6.4.3"}},
                    "web": {"local": {"version": "v6.6"}},
                    "ftl": {"local": {"version": "v6.7"}},
                }
            }
        if path == "/api/dns/blocking":
            return {"blocking": "disabled"}
        if path == "/api/stats/summary":
            return {
                "queries": {"total": 100, "blocked": 21, "percent_blocked": 21},
                "clients": {"active": 3, "total": 5},
                "gravity": {"domains_being_blocked": 80000, "last_update": 42},
            }
        raise AssertionError(path)

    monkeypatch.setattr(pihole_status, "_api", fake_api)
    result = pihole_status.status()
    assert result["installed"] is True
    assert result["ftl_active"] is True
    assert result["dns_online"] is True
    assert result["api_online"] is True
    assert result["blocking"] == "off"
    assert result["lan"]["dns"] == "192.168.1.27"
    assert result["versions"] == {"core": "6.4.3", "web": "6.6", "ftl": "6.7"}
    assert result["stats"]["active_clients"] == 3
    assert result["stats"]["gravity_domains"] == 80000
    assert result["remote"]["configured"] is False
    assert result["remote"]["dot"] == "not_configured"
    assert result["remote"]["port"] == pihole_status.remote_dns.DEFAULT_DOH_PORT


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


def test_pihole_status_endpoint_requires_session_and_returns_runtime_state(control_server, monkeypatch):
    monkeypatch.setattr(panel.pihole_runtime, "status", lambda: {"installed": True, "blocking": "on", "dns_online": True})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(control_server + "/api/pihole/status")
    assert error.value.code == 401

    request = urllib.request.Request(
        control_server + "/api/pihole/status",
        headers={"Cookie": "openastro_session=test"},
    )
    with urllib.request.urlopen(request) as response:
        assert json.load(response) == {"installed": True, "blocking": "on", "dns_online": True}


@pytest.mark.parametrize(
    "path,action",
    [
        ("/api/pihole/blocking/enable", "pihole_enable"),
        ("/api/pihole/blocking/disable", "pihole_disable"),
        ("/api/pihole/restart", "restart_pihole"),
        ("/api/pihole/gravity", "pihole_gravity"),
    ],
)
def test_pihole_mutations_are_fixed_csrf_protected_actions(control_server, monkeypatch, path, action):
    calls: list[tuple] = []
    monkeypatch.setattr(panel, "run", lambda *args: calls.append(args) or (0, "done"))
    request = urllib.request.Request(
        control_server + path,
        data=b"{}",
        method="POST",
        headers={"Cookie": "openastro_session=test", "Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403
    assert not calls

    request.add_header("X-CSRF-Token", "csrf-test")
    with urllib.request.urlopen(request) as response:
        body = json.load(response)
    assert body["ok"] is True
    assert calls == [(["sudo", "-n", "/usr/local/sbin/openastro-action", action], 290)]


def test_pihole_ui_contract_and_firewall_are_explicit():
    html = (CONTROL / "static/index.html").read_text(encoding="utf-8")
    js = (CONTROL / "static/app.js").read_text(encoding="utf-8")
    firewall = (CONTROL / "openastro-pihole-firewall").read_text(encoding="utf-8")
    assert 'id="piholeToggle"' in html
    assert 'id="piholeAdminLink"' in html
    assert 'id="piholeRemoteDot"' in html
    assert 'id="piholeDohState"' in html
    assert 'id="piholeDotState"' in html
    assert "setPiholeState" in js
    assert "/api/pihole/blocking/enable" in js
    assert "/api/pihole/blocking/disable" in js
    assert 'ip saddr 192.168.1.0/24 udp dport 53 accept' in firewall
    assert 'udp dport 53 drop' in firewall
    assert 'tcp dport 53 drop' in firewall
    assert 'tcp dport 853 drop' in firewall
