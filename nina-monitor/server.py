#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import time
import urllib.error
from urllib.parse import urlparse

import qsm_client

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'
HOST = os.environ.get('OPENASTRO_NINA_MONITOR_HOST', '0.0.0.0').strip() or '0.0.0.0'
PORT = int(os.environ.get('OPENASTRO_NINA_MONITOR_PORT', '9091'))
PASSWORD_HASH = os.environ.get('OPENASTRO_NINA_MONITOR_PASSWORD_HASH', '').strip()
SECRET = os.environ.get('OPENASTRO_NINA_MONITOR_SECRET', '').strip().encode('utf-8')
COOKIE_SECURE = os.environ.get('OPENASTRO_NINA_MONITOR_COOKIE_SECURE', '0').strip() == '1'
FRAME_ANCESTORS = os.environ.get('OPENASTRO_NINA_MONITOR_FRAME_ANCESTORS', "'none'").strip() or "'none'"
SESSION_SECONDS = 12 * 60 * 60
MAX_JSON_BODY = 4096
COOKIE_NAME = 'openastro_nina_session'

_LOGIN_FAILURES: dict[str, tuple[int, float]] = {}


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def _verify_password(password: str) -> bool:
    try:
        # Colon separators avoid Docker Compose variable interpolation on '$'.
        algorithm, iterations_raw, salt_raw, digest_raw = PASSWORD_HASH.split(':', 3)
        if algorithm != 'pbkdf2_sha256':
            return False
        iterations = int(iterations_raw)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = _b64d(salt_raw)
        expected = _b64d(digest_raw)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _session_cookie() -> str:
    payload = json.dumps(
        {'exp': int(time.time()) + SESSION_SECONDS, 'nonce': secrets.token_urlsafe(18)},
        separators=(',', ':'),
    ).encode('utf-8')
    encoded = _b64e(payload)
    signature = _b64e(hmac.new(SECRET, encoded.encode('ascii'), hashlib.sha256).digest())
    return f'{encoded}.{signature}'


def _valid_session(value: str) -> bool:
    if not value or not SECRET:
        return False
    try:
        encoded, signature = value.split('.', 1)
        expected = _b64e(hmac.new(SECRET, encoded.encode('ascii'), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return False
        payload = json.loads(_b64d(encoded).decode('utf-8'))
        return int(payload.get('exp') or 0) >= int(time.time())
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return False


def _cookie_header(value: str, max_age: int) -> str:
    suffix = '; Secure' if COOKIE_SECURE else ''
    return f'{COOKIE_NAME}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{suffix}'


def _client_key(handler: BaseHTTPRequestHandler) -> str:
    return str(handler.client_address[0] if handler.client_address else 'unknown')


def _login_allowed(client: str) -> tuple[bool, int]:
    attempts, blocked_until = _LOGIN_FAILURES.get(client, (0, 0.0))
    now = time.monotonic()
    if blocked_until > now:
        return False, max(1, int(blocked_until - now))
    if blocked_until and blocked_until <= now:
        _LOGIN_FAILURES.pop(client, None)
    return True, 0


def _login_failed(client: str) -> None:
    attempts, _ = _LOGIN_FAILURES.get(client, (0, 0.0))
    attempts += 1
    penalty = min(60, 2 ** max(0, attempts - 3)) if attempts >= 3 else 0
    _LOGIN_FAILURES[client] = (attempts, time.monotonic() + penalty)


def _login_succeeded(client: str) -> None:
    _LOGIN_FAILURES.pop(client, None)


class Handler(BaseHTTPRequestHandler):
    server_version = 'OpenAstroNinaMonitor/1.1'

    def log_message(self, fmt: str, *args) -> None:
        print(f'{self.address_string()} - {fmt % args}', flush=True)

    def _security_headers(self) -> None:
        self.send_header('X-Content-Type-Options', 'nosniff')
        if FRAME_ANCESTORS == "'none'":
            self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header(
            'Content-Security-Policy',
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
            f"connect-src 'self'; frame-ancestors {FRAME_ANCESTORS}; base-uri 'none'; form-action 'self'",
        )

    def _send_bytes(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self._security_headers()
        if extra:
            for key, value in extra.items():
                if key and value and '\r' not in value and '\n' not in value:
                    self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200, extra: dict[str, str] | None = None) -> None:
        self._send_bytes(status, json.dumps(payload, separators=(',', ':')).encode('utf-8'), 'application/json; charset=utf-8', extra)

    def _session(self) -> bool:
        raw = self.headers.get('Cookie', '')
        jar = cookies.SimpleCookie()
        try:
            jar.load(raw)
        except cookies.CookieError:
            return False
        morsel = jar.get(COOKIE_NAME)
        return bool(morsel and _valid_session(morsel.value))

    def _require_session(self) -> bool:
        if self._session():
            return True
        self._send_json({'ok': False, 'error': 'authentication required'}, 401)
        return False

    def _serve_static(self, path: str) -> None:
        mapped = {
            '/': 'index.html',
            '/index.html': 'index.html',
            '/app.js': 'app.js',
            '/app.css': 'app.css',
        }.get(path)
        if not mapped:
            self._send_json({'ok': False, 'error': 'not found'}, 404)
            return
        target = STATIC / mapped
        try:
            raw = target.read_bytes()
        except OSError:
            self._send_json({'ok': False, 'error': 'asset unavailable'}, 500)
            return
        mime = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        self._send_bytes(200, raw, f'{mime}; charset=utf-8' if mime.startswith('text/') or mime == 'application/javascript' else mime)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/healthz':
            self._send_json({
                'ok': True,
                'service': 'openastro-nina-monitor',
                'readOnly': True,
                'isolated': True,
                'port': PORT,
            })
            return

        if path == '/api/session':
            self._send_json({'ok': True, 'authenticated': self._session()})
            return

        if path == '/api/state':
            if not self._require_session():
                return
            self._send_json(qsm_client.state(force=parsed.query == 'force=1'))
            return

        if path == '/api/diagnostics':
            if not self._require_session():
                return
            payload = qsm_client.diagnostics()
            payload.update({'listen_host': HOST, 'listen_port': PORT, 'isolated_service': True})
            self._send_json(payload)
            return

        if path == '/api/preview.jpg':
            if not self._require_session():
                return
            try:
                raw, metadata = qsm_client.preview()
            except urllib.error.HTTPError as exc:
                status = 404 if exc.code == 404 else 502
                self._send_json({'ok': False, 'error': 'Preview non ancora disponibile.' if status == 404 else f'QSM preview HTTP {exc.code}.'}, status)
                return
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
                self._send_json({'ok': False, 'error': f'Preview NINA non raggiungibile: {str(exc)[:160]}'}, 502)
                return
            extra = {}
            if metadata.get('preview_utc'):
                extra['X-QSM-Preview-Utc'] = metadata['preview_utc']
            if metadata.get('image_id'):
                extra['X-QSM-Image-Id'] = metadata['image_id']
            self._send_bytes(200, raw, 'image/jpeg', extra)
            return

        self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == '/api/login':
            client = _client_key(self)
            allowed, retry = _login_allowed(client)
            if not allowed:
                self._send_json({'ok': False, 'error': 'too many attempts', 'retryAfter': retry}, 429, {'Retry-After': str(retry)})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_JSON_BODY:
                self._send_json({'ok': False, 'error': 'invalid request'}, 400)
                return
            try:
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
                password = str(payload.get('password') or '')
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                self._send_json({'ok': False, 'error': 'invalid request'}, 400)
                return
            if not _verify_password(password):
                _login_failed(client)
                self._send_json({'ok': False, 'error': 'password non valida'}, 401)
                return
            _login_succeeded(client)
            self._send_json({'ok': True}, 200, {'Set-Cookie': _cookie_header(_session_cookie(), SESSION_SECONDS)})
            return

        if parsed.path == '/api/logout':
            self._send_json({'ok': True}, 200, {'Set-Cookie': _cookie_header('', 0)})
            return

        self._send_json({'ok': False, 'error': 'read-only service'}, 405)


def validate_config() -> None:
    if not PASSWORD_HASH:
        raise RuntimeError('OPENASTRO_NINA_MONITOR_PASSWORD_HASH is required')
    if len(SECRET) < 32:
        raise RuntimeError('OPENASTRO_NINA_MONITOR_SECRET must be at least 32 characters')
    if PORT < 1024 or PORT > 65535:
        raise RuntimeError('OPENASTRO_NINA_MONITOR_PORT must be between 1024 and 65535')


def main() -> None:
    validate_config()
    mimetypes.add_type('application/javascript', '.js')
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f'OpenAstro NINA Monitor listening on http://{HOST}:{PORT}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
