from pathlib import Path

import pytest

from app.egress import RegionalEgressError, sanitize_wireguard_config, subprocess_proxy_env, wireproxy_config
from app.source_providers import ResolvedInput


GOOD = """
[Interface]
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Address = 10.2.0.2/32
DNS = 10.2.0.1

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 203.0.113.10:51820
"""


def test_wireguard_config_is_sanitized_for_userspace_proxy():
    clean = sanitize_wireguard_config(GOOD)
    assert "[Interface]" in clean
    assert "[Peer]" in clean
    assert "PersistentKeepalive = 25" in clean
    rendered = wireproxy_config(GOOD)
    assert "[Socks5]" in rendered
    assert "BindAddress = 127.0.0.1:25344" in rendered
    assert "[http]" in rendered
    assert "BindAddress = 127.0.0.1:25345" in rendered


def test_wireguard_shell_directives_are_rejected():
    with pytest.raises(RegionalEgressError, match="PostUp"):
        sanitize_wireguard_config(GOOD.replace("DNS = 10.2.0.1", "DNS = 10.2.0.1\nPostUp = touch /tmp/nope"))


def test_wireguard_requires_full_ipv4_egress():
    with pytest.raises(RegionalEgressError, match="0.0.0.0/0"):
        sanitize_wireguard_config(GOOD.replace("0.0.0.0/0, ::/0", "10.0.0.0/8"))


def test_subprocess_proxy_env_keeps_loopback_direct(monkeypatch):
    monkeypatch.setenv("no_proxy", "example.test")
    env = subprocess_proxy_env("http://127.0.0.1:25345")
    assert env["http_proxy"] == "http://127.0.0.1:25345"
    assert env["https_proxy"] == "http://127.0.0.1:25345"
    assert "127.0.0.1" in env["no_proxy"]
    assert "localhost" in env["no_proxy"]


def test_resolved_input_carries_egress_to_recorder():
    item = ResolvedInput("https://cdn.example/live.m3u8", {}, "media", "http://127.0.0.1:25345")
    assert item.proxy_url.endswith(":25345")


def test_v290_image_and_ui_wiring_present():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    html = (root / "app/static/index.html").read_text(encoding="utf-8")
    js = (root / "app/static/app.js").read_text(encoding="utf-8")
    providers = (root / "app/source_providers.py").read_text(encoding="utf-8")
    recorder = (root / "app/recorder.py").read_text(encoding="utf-8")
    assert "wireproxy@v1.1.2" in dockerfile
    assert "COPY --from=wireproxy-builder /out/wireproxy" in dockerfile
    assert 'id="setEgressConfig"' in html
    assert 'id="egressPill"' in html
    assert "/api/settings/test/egress" in js
    assert "regional_egress_proxy_for_url" in providers
    assert "env=subprocess_proxy_env(item.proxy_url)" in providers
    assert "env=subprocess_proxy_env(proxy_url)" in recorder
