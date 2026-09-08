from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

CACHE_SECONDS = 0.5
TIMEOUT_SECONDS = 2.5
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_PREVIEW_BYTES = 4 * 1024 * 1024

_lock = threading.Lock()
_cached: dict | None = None
_cached_at = 0.0


def _config() -> dict[str, str]:
    base_url = str(os.environ.get('OPENASTRO_NINA_QSM_URL', '') or '').strip().rstrip('/')
    token = str(os.environ.get('OPENASTRO_NINA_QSM_TOKEN', '') or '').strip()
    if base_url and not base_url.startswith(('http://', 'https://')):
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
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise RuntimeError(f'HTTP {response.status}')
        raw = response.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise RuntimeError('QSM snapshot too large')
        payload = json.loads(raw.decode('utf-8'))
        if not isinstance(payload, dict):
            raise RuntimeError('invalid QSM payload')
        return payload, (time.monotonic() - started) * 1000.0


def preview() -> tuple[bytes, dict[str, str]]:
    config = _config()
    if not config['base_url'] or not config['token']:
        raise FileNotFoundError('NINA/QSM non configurato')

    request = _request(f"{config['base_url']}/api/v1/preview.jpg", config['token'], 'image/jpeg')
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
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


def state(force: bool = False) -> dict:
    global _cached, _cached_at
    now = time.monotonic()
    with _lock:
        if not force and _cached is not None and now - _cached_at < CACHE_SECONDS:
            return dict(_cached)

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
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            payload = {
                'ok': True,
                'configured': True,
                'reachable': False,
                'sessionActive': False,
                'message': f'NINA/QSM non raggiungibile: {str(exc)[:180]}',
                'snapshot': None,
                'latencyMs': None,
                'updatedAt': int(time.time()),
            }

    with _lock:
        _cached = dict(payload)
        _cached_at = now
    return payload


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
        'policy': 'read-only',
        'module': 'openastro-nina-monitor',
    }


def reset_cache() -> None:
    global _cached, _cached_at
    with _lock:
        _cached = None
        _cached_at = 0.0
