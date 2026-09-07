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
        'guidingLive': {'hasData': True, 'rmsTotalArcsec': 0.62, 'series': []},
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


def test_preview_uses_same_server_side_token(monkeypatch, tmp_path):
    monkeypatch.setattr(nina, 'CONFIG_FILE', tmp_path / 'missing.json')
    monkeypatch.setenv('OPENASTRO_NINA_QSM_URL', 'http://10.0.0.20:18973')
    monkeypatch.setenv('OPENASTRO_NINA_QSM_TOKEN', 'preview-secret')

    class Headers(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class Response:
        status = 200
        headers = Headers({
            'Content-Type': 'image/jpeg',
            'X-QSM-Preview-Utc': '2026-09-07T21:00:00Z',
            'X-QSM-Image-Id': 'abc',
        })
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, _limit): return b'\xff\xd8testjpeg\xff\xd9'

    seen = []
    def fake_urlopen(request, timeout):
        seen.append((request.full_url, request.get_header('X-qsm-token'), timeout))
        return Response()

    monkeypatch.setattr(nina.urllib.request, 'urlopen', fake_urlopen)
    raw, metadata = nina.preview()
    assert raw.startswith(b'\xff\xd8')
    assert seen[0][0].endswith('/api/v1/preview.jpg')
    assert seen[0][1] == 'preview-secret'
    assert metadata['image_id'] == 'abc'


def test_frontend_extension_is_loaded_without_modifying_core_shell():
    root = Path(__file__).parents[1] / 'control-panel'
    static = root / 'static'
    loader = (static / 'media-upload.js').read_text(encoding='utf-8')
    nina_js = (static / 'nina-monitor.js').read_text(encoding='utf-8')
    nina_css = (static / 'nina-monitor.css').read_text(encoding='utf-8')
    wrapper = (root / 'upload_server.py').read_text(encoding='utf-8')
    assert '/nina-monitor.js?v=1.0' in loader
    assert "navItem.dataset.route = 'nina'" in nina_js
    assert "mobileItem.dataset.route = 'nina'" in nina_js
    assert '/api/nina/state' in nina_js
    assert '/api/nina/preview.jpg' in nina_js
    assert 'guidingLive' in nina_js
    assert 'setInterval(()=>refreshNina(false),1000)' in nina_js
    assert 'window.selectView = function' in nina_js
    assert "parsed.path == '/api/nina/preview.jpg'" in wrapper
    assert '.nina-guide-ra' in nina_css
    assert '.nina-preview-wrap' in nina_css
    assert '@media(max-width:620px)' in nina_css
