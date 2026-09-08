from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).parents[1] / 'nina-monitor'
sys.path.insert(0, str(ROOT))

import qsm_client


def _load_server(monkeypatch, password: str = 'CorrectHorseBatteryStaple'):
    salt = b'0123456789abcdef12'
    iterations = 120_000
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip('=')
    monkeypatch.setenv(
        'OPENASTRO_NINA_MONITOR_PASSWORD_HASH',
        f'pbkdf2_sha256:{iterations}:{b64(salt)}:{b64(digest)}',
    )
    monkeypatch.setenv('OPENASTRO_NINA_MONITOR_SECRET', 'x' * 48)
    monkeypatch.setenv('OPENASTRO_NINA_MONITOR_PORT', '9091')
    spec = importlib.util.spec_from_file_location('nina_monitor_server_test', ROOT / 'server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unconfigured_qsm_is_safe_offline(monkeypatch):
    qsm_client.reset_cache()
    monkeypatch.delenv('OPENASTRO_NINA_QSM_URL', raising=False)
    monkeypatch.delenv('OPENASTRO_NINA_QSM_TOKEN', raising=False)
    payload = qsm_client.state(force=True)
    assert payload['configured'] is False
    assert payload['reachable'] is False
    assert payload['sessionActive'] is False
    assert payload['snapshot'] is None


def test_snapshot_proxy_preserves_live_guiding(monkeypatch):
    qsm_client.reset_cache()
    monkeypatch.setenv('OPENASTRO_NINA_QSM_URL', 'http://100.64.1.20:18973')
    monkeypatch.setenv('OPENASTRO_NINA_QSM_TOKEN', 'secret-token')
    seen = []
    snapshot = {
        'available': True,
        'summary': {'captured': 12, 'usable': 10, 'rejected': 2},
        'currentFrame': {'frameIndex': 12, 'status': 'ACCEPTED'},
        'guidingLive': {'hasData': True, 'rmsTotalArcsec': 0.62, 'series': [{'raArcsec': 0.1, 'decArcsec': -0.2}]},
        'frames': [],
    }
    monkeypatch.setattr(qsm_client, '_fetch_json', lambda url, token: (seen.append((url, token)) or snapshot, 18.4))
    payload = qsm_client.state(force=True)
    assert seen == [('http://100.64.1.20:18973/api/v1/snapshot', 'secret-token')]
    assert payload['reachable'] is True
    assert payload['sessionActive'] is True
    assert payload['snapshot']['guidingLive']['rmsTotalArcsec'] == 0.62


def test_invalid_qsm_scheme_is_never_requested(monkeypatch):
    qsm_client.reset_cache()
    monkeypatch.setenv('OPENASTRO_NINA_QSM_URL', 'file:///etc/passwd')
    monkeypatch.setenv('OPENASTRO_NINA_QSM_TOKEN', 'secret-token')
    monkeypatch.setattr(qsm_client, '_fetch_json', lambda *_: pytest.fail('network must not be called'))
    payload = qsm_client.state(force=True)
    assert payload['configured'] is False
    assert payload['reachable'] is False


def test_password_hash_and_signed_session(monkeypatch):
    server = _load_server(monkeypatch)
    assert server._verify_password('CorrectHorseBatteryStaple') is True
    assert server._verify_password('wrong') is False
    cookie = server._session_cookie()
    assert server._valid_session(cookie) is True
    tampered = cookie[:-1] + ('A' if cookie[-1] != 'A' else 'B')
    assert server._valid_session(tampered) is False


def test_frame_ancestors_defaults_to_deny(monkeypatch):
    monkeypatch.delenv('OPENASTRO_NINA_MONITOR_FRAME_ANCESTORS', raising=False)
    server = _load_server(monkeypatch)
    assert server.FRAME_ANCESTORS == "'none'"


def test_frame_ancestors_can_allow_control_center(monkeypatch):
    origin = 'https://openastro.tailf2871c.ts.net:8443'
    monkeypatch.setenv('OPENASTRO_NINA_MONITOR_FRAME_ANCESTORS', origin)
    server = _load_server(monkeypatch)
    assert server.FRAME_ANCESTORS == origin


def test_standalone_assets_and_docker_contract_exist():
    assert (ROOT / 'Dockerfile').is_file()
    assert (ROOT / 'docker-compose.yml').is_file()
    assert (ROOT / 'COOLIFY.md').is_file()
    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    compose = (ROOT / 'docker-compose.yml').read_text(encoding='utf-8')
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert 'NINA Monitor' in html
    assert '/api/state' in js
    assert '/api/preview.jpg' in js
    assert 'guidingLive' in js
    assert 'read_only: true' in compose
    assert 'cap_drop:' in compose and 'ALL' in compose
    assert '/var/run/docker.sock' not in compose
    assert '/mnt/livevault-nvme' not in compose
    assert '/srv/openastro-media' not in compose
    assert 'USER openastro' in dockerfile
