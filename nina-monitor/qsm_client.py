from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

CACHE_SECONDS = 0.5
TIMEOUT_SECONDS = 2.5
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_PREVIEW_BYTES = 4 * 1024 * 1024
PREVIEW_CACHE_SECONDS = 5.0

_lock = threading.Lock()
_state_ready = threading.Condition(_lock)
_state_loading = False
_cached: dict | None = None
_cached_at = 0.0
_preview_ready = threading.Condition()
_preview_loading = False
_preview_cached: tuple[bytes, dict[str, str]] | None = None
_preview_error: Exception | None = None
_preview_cached_at = 0.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The bridge is a fixed endpoint. Never forward its secret to a redirect.
        return None


_opener = urllib.request.build_opener(_NoRedirect())


def _config() -> dict[str, str]:
    base_url = str(os.environ.get('OPENASTRO_NINA_QSM_URL', '') or '').strip().rstrip('/')
    token = str(os.environ.get('OPENASTRO_NINA_QSM_TOKEN', '') or '').strip()
    try:
        parsed = urlsplit(base_url)
        valid = parsed.scheme in ('http', 'https') and bool(parsed.hostname)
        valid = valid and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
        parsed.port  # Reject malformed ports before creating a request.
    except ValueError:
        valid = False
    if not valid:
        base_url = ''
    return {'base_url': base_url, 'token': token}


def _request(url: str, token: str, accept: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            'Accept': accept,
            'User-Agent': 'OpenAstro-NINA-Monitor/1.1',
            'X-QSM-Token': token,
        },
        method='GET',
    )


def _fetch_json(url: str, token: str) -> tuple[dict, float]:
    request = _request(url, token, 'application/json')
    started = time.monotonic()
    with _opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f'HTTP {response.status}')
        raw = response.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise RuntimeError('QSM snapshot too large')
        payload = json.loads(raw.decode('utf-8'))
        if not isinstance(payload, dict):
            raise RuntimeError('invalid QSM payload')
        return payload, (time.monotonic() - started) * 1000.0


def _fetch_preview() -> tuple[bytes, dict[str, str]]:
    config = _config()
    if not config['base_url'] or not config['token']:
        raise FileNotFoundError('NINA/QSM non configurato')

    request = _request(f"{config['base_url']}/api/v1/preview.jpg", config['token'], 'image/jpeg')
    with _opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f'HTTP {response.status}')
        content_type = str(response.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
        if content_type != 'image/jpeg':
            raise RuntimeError('invalid preview content type')
        raw = response.read(MAX_PREVIEW_BYTES + 1)
        if not raw or len(raw) > MAX_PREVIEW_BYTES:
            raise RuntimeError('invalid preview size')
        return raw, {
            'preview_utc': str(response.headers.get('X-QSM-Preview-Utc') or ''),
            'image_id': str(response.headers.get('X-QSM-Image-Id') or ''),
        }


def preview() -> tuple[bytes, dict[str, str]]:
    global _preview_loading, _preview_cached, _preview_error, _preview_cached_at
    with _preview_ready:
        if _preview_loading:
            _preview_ready.wait_for(lambda: not _preview_loading)
        if time.monotonic() - _preview_cached_at < PREVIEW_CACHE_SECONDS:
            if _preview_error is not None:
                raise _preview_error
            if _preview_cached is not None:
                return _preview_cached[0], dict(_preview_cached[1])
        _preview_loading = True
    try:
        result = _fetch_preview()
    except Exception as exc:
        with _preview_ready:
            _preview_cached = None
            _preview_error = exc
            _preview_cached_at = time.monotonic()
        raise
    else:
        with _preview_ready:
            _preview_cached = result
            _preview_error = None
            _preview_cached_at = time.monotonic()
        return result[0], dict(result[1])
    finally:
        with _preview_ready:
            _preview_loading = False
            _preview_ready.notify_all()


def _load_state() -> dict:
    config = _config()
    base_url = config['base_url']
    token = config['token']

    if not base_url or not token:
        payload = {
            'ok': True,
            'configured': False,
            'reachable': False,
            'sessionActive': False,
            'message': 'NINA/QSM non configurato.',
            'snapshot': None,
            'latencyMs': None,
            'updatedAt': int(time.time()),
        }
    else:
        try:
            snapshot, latency = _fetch_json(f'{base_url}/api/v1/snapshot', token)
            current = snapshot.get('currentFrame') if isinstance(snapshot.get('currentFrame'), dict) else None
            summary = snapshot.get('summary') if isinstance(snapshot.get('summary'), dict) else {}
            captured = int(summary.get('captured') or 0)
            payload = {
                'ok': True,
                'configured': True,
                'reachable': True,
                'sessionActive': bool(snapshot.get('available')) and (captured > 0 or current is not None),
                'message': 'QSM collegato.',
                'snapshot': snapshot,
                'latencyMs': round(latency, 1),
                'updatedAt': int(time.time()),
            }
        except urllib.error.HTTPError as exc:
            message = 'Token QSM non valido.' if exc.code in (401, 403) else f'QSM HTTP {exc.code}.'
            payload = {
                'ok': True,
                'configured': True,
                'reachable': False,
                'sessionActive': False,
                'message': message,
                'snapshot': None,
                'latencyMs': None,
                'updatedAt': int(time.time()),
            }
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError, OverflowError):
            payload = {
                'ok': True,
                'configured': True,
                'reachable': False,
                'sessionActive': False,
                'message': 'NINA/QSM non raggiungibile. Controlla il PC e il collegamento di rete.',
                'snapshot': None,
                'latencyMs': None,
                'updatedAt': int(time.time()),
            }

    return payload


def state(force: bool = False) -> dict:
    global _cached, _cached_at, _state_loading
    with _state_ready:
        if _state_loading:
            _state_ready.wait_for(lambda: not _state_loading)
            if _cached is not None:
                return dict(_cached)
        if not force and _cached is not None and time.monotonic() - _cached_at < CACHE_SECONDS:
            return dict(_cached)
        _state_loading = True
    try:
        payload = _load_state()
        with _state_ready:
            _cached = dict(payload)
            _cached_at = time.monotonic()
        return payload
    finally:
        with _state_ready:
            _state_loading = False
            _state_ready.notify_all()


def diagnostics() -> dict:
    config = _config()
    return {
        'ok': True,
        'configured': bool(config['base_url'] and config['token']),
        'qsm_url': config['base_url'],
        'token_present': bool(config['token']),
        'cache_seconds': CACHE_SECONDS,
        'timeout_seconds': TIMEOUT_SECONDS,
        'preview_max_bytes': MAX_PREVIEW_BYTES,
        'preview_cache_seconds': PREVIEW_CACHE_SECONDS,
        'policy': 'read-only',
        'module': 'openastro-nina-monitor',
    }


def reset_cache() -> None:
    global _cached, _cached_at, _preview_cached, _preview_cached_at, _preview_error
    with _lock:
        _cached = None
        _cached_at = 0.0
    with _preview_ready:
        _preview_cached = None
        _preview_error = None
        _preview_cached_at = 0.0
