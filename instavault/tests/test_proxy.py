import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vault import server  # noqa: E402


def handler(peer, headers):
    value = object.__new__(server.Handler)
    value.client_address = (peer, 50000)
    value.headers = headers
    return value


def test_funnel_proxy_supplies_the_client_address_for_login_limits():
    proxied = handler('172.18.0.1', {'X-Forwarded-For': '203.0.113.9, 198.51.100.7'})
    assert proxied.client_ip() == '198.51.100.7'
    assert handler('127.0.0.1', {'X-Forwarded-For': 'not-an-ip'}).client_ip() == '127.0.0.1'


def test_forwarded_address_from_a_public_peer_is_ignored():
    assert handler('93.184.216.34', {'X-Forwarded-For': '10.0.0.1'}).client_ip() == '93.184.216.34'


def test_origin_must_match_the_public_host():
    host = 'instavault.example.ts.net'
    handler('172.18.0.1', {'Origin': f'https://{host}', 'Host': host}).origin_check()
    handler('172.18.0.1', {'Origin': f'https://{host}', 'Host': '127.0.0.1:8096', 'X-Forwarded-Host': host}).origin_check()
    with pytest.raises(server.HTTPError):
        handler('172.18.0.1', {'Origin': 'https://attacker.example', 'Host': host}).origin_check()
    with pytest.raises(server.HTTPError):
        handler('93.184.216.34', {'Origin': f'https://{host}', 'Host': '127.0.0.1:8096', 'X-Forwarded-Host': host}).origin_check()
