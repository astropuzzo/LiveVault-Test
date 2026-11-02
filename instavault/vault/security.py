"""Private application sessions and encrypted Instagram session data.

The OpenAstro password configuration is read dynamically. Password changes
invalidate application cookies without changing the encryption key for the
separately stored Instagram session.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken

from . import store

COOKIE_NAME = 'instavault_session'
MAX_AGE = 7 * 24 * 60 * 60
_MAX_JSON = 1024 * 1024


def _secret() -> bytes:
    with (store.SECRETS / 'secret').open('rb') as source:
        value = source.read(4097)
    if not 32 <= len(value) <= 4096:
        raise ValueError('Configurazione di sicurezza non disponibile.')
    return value


def _auth() -> dict:
    with (store.SECRETS / 'auth.json').open('rb') as source:
        raw = source.read(8193)
    if len(raw) > 8192:
        raise ValueError('Configurazione di accesso non disponibile.')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Configurazione di accesso non disponibile.')
    user, salt, digest = (value.get(key) for key in ('username', 'salt', 'hash'))
    if (not isinstance(user, str) or not 1 <= len(user) <= 128
            or not isinstance(salt, str) or not 1 <= len(salt) <= 256
            or not isinstance(digest, str) or not re.fullmatch('[a-fA-F0-9]{64}', digest)):
        raise ValueError('Configurazione di accesso non disponibile.')
    return {'username': user, 'salt': salt, 'hash': digest.lower()}


def _signing_key() -> bytes:
    auth = json.dumps(_auth(), sort_keys=True, separators=(',', ':')).encode()
    return hmac.digest(_secret(), b'instavault-session-key\0' + auth, 'sha256')


def check_password(username: str, password: str) -> bool:
    """Fail closed if either credentials or the application secret is absent."""
    if not isinstance(username, str) or not isinstance(password, str):
        return False
    if not 1 <= len(username) <= 128 or not 1 <= len(password) <= 1024:
        return False
    try:
        _secret()
        auth = _auth()
        supplied = hashlib.sha256((auth['salt'] + ':' + password).encode()).hexdigest()
        valid_user = hmac.compare_digest(username.encode(), auth['username'].encode())
        valid_password = hmac.compare_digest(supplied, auth['hash'])
        return valid_user and valid_password
    except (OSError, ValueError, TypeError, UnicodeError):
        return False


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def issue_session() -> str:
    """Issue a cookie only when the private authentication setup is valid."""
    key = _signing_key()
    payload = _b64(json.dumps({'v': 1, 'iat': int(time.time()),
                              'nonce': secrets.token_urlsafe(24)}, separators=(',', ':')).encode())
    signature = _b64(hmac.digest(key, b'session\0' + payload.encode(), 'sha256'))
    return payload + '.' + signature


def verify_session(token: str) -> bool:
    if not isinstance(token, str) or not 1 <= len(token) <= 1024:
        return False
    try:
        payload, signature = token.split('.')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', payload) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', signature):
            return False
        expected = _b64(hmac.digest(_signing_key(), b'session\0' + payload.encode(), 'sha256'))
        if not hmac.compare_digest(signature, expected):
            return False
        value = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        issued = value.get('iat')
        current = int(time.time())
        return (value.get('v') == 1 and type(issued) is int
                and isinstance(value.get('nonce'), str) and len(value['nonce']) >= 24
                and current - MAX_AGE < issued <= current + 30)
    except (OSError, ValueError, TypeError, UnicodeError, AttributeError):
        return False


def csrf_token(token: str) -> str:
    if not verify_session(token):
        raise ValueError('Sessione non valida.')
    return _b64(hmac.digest(_signing_key(), b'csrf\0' + token.encode(), 'sha256'))


def _fernet() -> Fernet:
    # Separate domain from cookie signing; do not bind this key to passwords.
    key = hmac.digest(_secret(), b'instavault-instagram-encryption-v1', 'sha256')
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_json(value: dict) -> bytes:
    if not isinstance(value, dict):
        raise ValueError('Dati di sessione non validi.')
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
    if len(raw) > _MAX_JSON:
        raise ValueError('Dati di sessione troppo grandi.')
    return _fernet().encrypt(raw)


def decrypt_json(value: bytes) -> dict:
    if not isinstance(value, bytes) or len(value) > 2 * _MAX_JSON:
        raise ValueError('Dati di sessione non validi.')
    try:
        raw = _fernet().decrypt(value)
        if len(raw) > _MAX_JSON:
            raise ValueError('Dati di sessione troppo grandi.')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Dati di sessione non validi.')
        return result
    except (InvalidToken, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Dati di sessione non validi.') from exc
