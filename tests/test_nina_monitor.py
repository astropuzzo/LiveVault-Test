from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[1] / 'control-panel' / 'nina_monitor.py'
spec = importlib.util.spec_from_file_location('nina_monitor_test', MODULE)
nina = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nina)


def reset_cache(monkeypatch):
    monkeypatch.setattr(nina, '_cached', None)
    monkeypatch.setattr(nina, '_cached_at', 0.0)


def test_unconfigured_node_returns_safe_offline_state(monkeypatch, tmp_path):
    reset_cache(monkeypatch)
    monkeypatch.setattr(nina, 'CONFIG_FILE', tmp_path / 'missing.json')
    monkeypatch.delenv('OPENASTRO_NINA_QSM_URL', raising=False)
    monkeypatch.delenv('OPENASTRO_NINA_QSM_TOKEN', raising=False)
    payload = nina.state(force=True)
    assert payload['ok'] is True
    assert payload['configured'] is False
    assert payload['reachable'] is False
    assert payload['sessionActive'] is False
    assert payload['snapshot'] is None


def test_snapshot_is_proxied_and_session_detected(monkeypatch, tmp_path):
    reset_cache(monkeypatch)
    monkeypatch.setattr(nina, 'CONFIG_FILE', tmp_path / 'missing.json')
    monkeypatch.setenv('OPENASTRO_NINA_QSM_URL', 'http://100.64.1.20:18973')
    monkeypatch.setenv('OPENASTRO_NINA_QSM_TOKEN', 'secret-token')
    seen = []
    snapshot = {
        'available': True,
        'summary': {'captured': 12, 'usable': 10, 'rejected': 2},
        'currentFrame': {'frameIndex': 12, 'status': 'ACCEPTED'},
        'frames': [],
    }
    monkeypatch.setattr(nina, '_fetch_json', lambda url, token: (seen.append((url, token)) or snapshot, 18.4))
    payload = nina.state(force=True)
    assert seen == [('http://100.64.1.20:18973/api/v1/snapshot', 'secret-token')]
    assert payload['configured'] is True
    assert payload['reachable'] is True
    assert payload['sessionActive'] is True
    assert payload['latencyMs'] == 18.4
    assert payload['snapshot'] is snapshot


def test_invalid_scheme_is_never_requested(monkeypatch, tmp_path):
    reset_cache(monkeypatch)
    monkeypatch.setattr(nina, 'CONFIG_FILE', tmp_path / 'missing.json')
    monkeypatch.setenv('OPENASTRO_NINA_QSM_URL', 'file:///etc/passwd')
    monkeypatch.setenv('OPENASTRO_NINA_QSM_TOKEN', 'secret-token')
    monkeypatch.setattr(nina, '_fetch_json', lambda *_: pytest.fail('network must not be called'))
    payload = nina.state(force=True)
    assert payload['configured'] is False
    assert payload['reachable'] is False


def test_frontend_extension_is_loaded_without_modifying_core_shell():
    static = Path(__file__).parents[1] / 'control-panel' / 'static'
    loader = (static / 'media-upload.js').read_text(encoding='utf-8')
    nina_js = (static / 'nina-monitor.js').read_text(encoding='utf-8')
    nina_css = (static / 'nina-monitor.css').read_text(encoding='utf-8')
    assert '/nina-monitor.js?v=1.0' in loader
    assert "navItem.innerHTML" in nina_js
    assert '/api/nina/state' in nina_js
    assert 'window.selectView = function' in nina_js
    assert '.nina-grid' in nina_css
    assert '@media(max-width:620px)' in nina_css
