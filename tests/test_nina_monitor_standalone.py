from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import concurrent.futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import urllib.error
import urllib.request

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
    css = (ROOT / 'static' / 'app.css').read_text(encoding='utf-8')
    assert 'api/state' in js and '/api/state' not in js
    assert 'api/preview.jpg' in js and '/api/preview.jpg' not in js
    assert 'href="app.css?v=3.5.1"' in html and 'src="app.js?v=3.5.1"' in html
    assert 'src="motion.js?v=3.5.1"' in html
    assert '[hidden]{display:none!important}' in css
    assert 'guidingLive' in js
    assert 'ANDAMENTO DELLA SESSIONE' in html
    assert 'Quality' in js and '0–100' in js
    assert 'Guide RMS' in js and 'baseline' in html
    assert 'PHD2 live' in js
    assert 'GUIDA LIVE' in html
    assert 'ULTIMO LIGHT' in html
    assert 'id="pluginTimeline"' in html
    assert 'H=420' in js
    assert 'min-width:1000px' in css and 'min-width:760px' in css
    assert 'id="sessionEventsBody"' in html
    assert 'id="bestAcceptedBody"' in html and 'id="worstAcceptedBody"' in html
    assert 'id="rejectedTableBody"' in html
    assert 'id="frameHistoryBody"' in html
    assert 'id="frameInspector"' in html
    assert 'fileDisposition' in js and 'fileName' in js
    assert 'tl-ref-zero' in css and 'tl-ref-guide' in css
    assert 'read_only: true' in compose
    assert 'cap_drop:' in compose and 'ALL' in compose
    assert '/var/run/docker.sock' not in compose
    assert '/mnt/livevault-nvme' not in compose
    assert '/srv/openastro-media' not in compose
    assert 'USER openastro' in dockerfile


def test_stellar_inspection_is_available_before_diagnostic_history():
    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    assert html.index('id="previewPanel"') < html.index('id="stellarPanel"') < html.index('id="pluginTimeline"')
    assert 'id="stellarSelect"' in html and 'id="stellarEvidence"' in html
    assert 'id="stellarTooltip"' in html


def test_proxy_preserves_stellar_evidence_without_inventing_limits(monkeypatch):
    qsm_client.reset_cache()
    monkeypatch.setenv('OPENASTRO_NINA_QSM_URL', 'http://127.0.0.1:18973')
    monkeypatch.setenv('OPENASTRO_NINA_QSM_TOKEN', 'test-only-token')
    frame = {'frameIndex': 6, 'status': 'REJECTED', 'quality': None,
             'guideFalsePositive': False, 'imageEvidenceAvailable': True,
             'starProofPng': 'aGVsbG8=', 'starEccentricity': .58,
             'starRescueEccentricityLimit': .55}
    snapshot = {'available': True, 'summary': {'captured': 1}, 'currentFrame': frame, 'frames': [frame]}
    monkeypatch.setattr(qsm_client, '_fetch_json', lambda *_: (snapshot, 1))
    result = qsm_client.state(force=True)['snapshot']
    assert result['currentFrame'] == frame
    assert result['frames'][0]['quality'] is None
    assert 'starTailLimitPercent' not in result['frames'][0]


@pytest.mark.parametrize('force', [False, True])
def test_snapshot_requests_share_inflight_fetch_and_cache_after_io(monkeypatch, force):
    qsm_client.reset_cache()
    monkeypatch.setenv('OPENASTRO_NINA_QSM_URL', 'http://127.0.0.1:18973')
    monkeypatch.setenv('OPENASTRO_NINA_QSM_TOKEN', 'test-only-token')
    started, release = threading.Event(), threading.Event()
    calls = []

    def fetch(*_):
        calls.append(True)
        started.set()
        assert release.wait(3)
        return {'available': True, 'summary': {'captured': 1}}, 600

    monkeypatch.setattr(qsm_client, '_fetch_json', fetch)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        requests = [pool.submit(qsm_client.state, force=force) for _ in range(4)]
        assert started.wait(1)
        # The transfer outlasts CACHE_SECONDS; age must begin when it finishes.
        time.sleep(qsm_client.CACHE_SECONDS + 0.05)
        release.set()
        results = [request.result(timeout=2) for request in requests]
    assert len(calls) == 1
    assert all(result['reachable'] for result in results)
    assert qsm_client.state()['reachable'] is True
    assert len(calls) == 1


def test_preview_requests_share_bytes_and_metadata(monkeypatch):
    qsm_client.reset_cache()
    started, release = threading.Event(), threading.Event()
    calls = []

    def fetch():
        calls.append(True)
        started.set()
        assert release.wait(3)
        return b'jpeg-bytes', {'preview_utc': '2026-09-29T19:35:00Z', 'image_id': '6'}

    monkeypatch.setattr(qsm_client, '_fetch_preview', fetch)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        requests = [pool.submit(qsm_client.preview) for _ in range(4)]
        assert started.wait(1)
        release.set()
        results = [request.result(timeout=2) for request in requests]
    assert len(calls) == 1
    assert all(result == results[0] for result in results)
    results[0][1]['image_id'] = 'modified-client-copy'
    assert qsm_client.preview()[1]['image_id'] == '6'


def test_unavailable_preview_is_cached_to_avoid_retry_storm(monkeypatch):
    qsm_client.reset_cache()
    calls = []

    def fetch():
        calls.append(True)
        raise urllib.error.HTTPError('http://qsm.test', 404, 'Not found', {}, None)

    monkeypatch.setattr(qsm_client, '_fetch_preview', fetch)
    for _ in range(3):
        with pytest.raises(urllib.error.HTTPError) as failure:
            qsm_client.preview()
        assert failure.value.code == 404
    assert len(calls) == 1


@pytest.mark.parametrize('route', ['snapshot', 'preview.jpg'])
def test_qsm_redirects_never_forward_secret(monkeypatch, route):
    qsm_client.reset_cache()
    received = []

    class Sink(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get('X-QSM-Token'))
            self.send_response(200)
            self.end_headers()
        def log_message(self, *_):
            pass

    sink = ThreadingHTTPServer(('127.0.0.1', 0), Sink)

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location', f'http://127.0.0.1:{sink.server_port}/receive')
            self.end_headers()
        def log_message(self, *_):
            pass

    source = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
    for service in (source, sink):
        threading.Thread(target=service.serve_forever, daemon=True).start()
    monkeypatch.setenv('OPENASTRO_NINA_QSM_URL', f'http://127.0.0.1:{source.server_port}')
    monkeypatch.setenv('OPENASTRO_NINA_QSM_TOKEN', 'test-only-token')
    try:
        if route == 'snapshot':
            result = qsm_client.state(force=True)
            assert result['reachable'] is False
            assert result['message'] == 'QSM HTTP 302.'
        else:
            with pytest.raises(urllib.error.HTTPError) as failure:
                qsm_client.preview()
            assert failure.value.code == 302
        assert received == []
    finally:
        source.shutdown()
        sink.shutdown()
        source.server_close()
        sink.server_close()


@pytest.mark.parametrize('url', ['http://user:secret@example.test', 'http://example.test:invalid', 'http://example.test/?token=secret'])
def test_qsm_config_rejects_ambiguous_or_secret_bearing_urls(monkeypatch, url):
    qsm_client.reset_cache()
    monkeypatch.setenv('OPENASTRO_NINA_QSM_URL', url)
    monkeypatch.setenv('OPENASTRO_NINA_QSM_TOKEN', 'test-only-token')
    monkeypatch.setattr(qsm_client, '_fetch_json', lambda *_: pytest.fail('network must not be called'))
    assert qsm_client.state(force=True)['configured'] is False
    assert qsm_client.diagnostics()['qsm_url'] == ''


def test_upstream_exception_cannot_expose_qsm_token(monkeypatch):
    qsm_client.reset_cache()
    monkeypatch.setenv('OPENASTRO_NINA_QSM_URL', 'http://127.0.0.1:18973')
    monkeypatch.setenv('OPENASTRO_NINA_QSM_TOKEN', 'test-only-token')

    def fetch(*_):
        raise ValueError('Invalid header value: test-only-token')

    monkeypatch.setattr(qsm_client, '_fetch_json', fetch)
    result = qsm_client.state(force=True)
    assert result['reachable'] is False
    assert 'test-only-token' not in str(result)


def test_preview_conditional_request_keeps_timestamp_without_resending_jpeg(monkeypatch):
    server = _load_server(monkeypatch)
    monkeypatch.setattr(server.Handler, '_require_session', lambda _: True)
    monkeypatch.setattr(qsm_client, 'preview', lambda: (b'jpeg-bytes', {'preview_utc': '2026-09-29T19:35:00Z', 'image_id': '6'}))
    service = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    threading.Thread(target=service.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{service.server_port}/api/preview.jpg'
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            assert response.read() == b'jpeg-bytes'
            etag = response.headers['ETag']
        request = urllib.request.Request(url, headers={'If-None-Match': etag})
        with pytest.raises(urllib.error.HTTPError) as unchanged:
            urllib.request.urlopen(request, timeout=2)
        assert unchanged.value.code == 304
        assert unchanged.value.headers['X-QSM-Preview-Utc'] == '2026-09-29T19:35:00Z'
        assert unchanged.value.read() == b''
    finally:
        service.shutdown()
        service.server_close()


def test_new_static_assets_resolve_under_the_reverse_proxy_prefix(monkeypatch):
    server = _load_server(monkeypatch)
    service = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        for route, name in [('motion.js?v=3.5.1', 'motion.js'), ('fonts/mona-sans-latin-wght.woff2', 'fonts/mona-sans-latin-wght.woff2')]:
            with urllib.request.urlopen(f'http://127.0.0.1:{service.server_port}/{route}', timeout=2) as response:
                assert response.status == 200
                assert response.read() == (ROOT / 'static' / name).read_bytes()
                assert 'default-src' in response.headers['Content-Security-Policy']
    finally:
        service.shutdown(); service.server_close(); thread.join(timeout=2)
