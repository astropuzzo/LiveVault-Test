from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request, status

from .config import settings

COOKIE_NAME = "livevault_session"
MAX_AGE = 60 * 60 * 24 * 30


def _password_material() -> str:
    return settings.app_password_hash or settings.app_password


def password_ok(candidate: str) -> bool:
    encoded = settings.app_password_hash
    if encoded:
        try:
            scheme, iterations_s, salt_b64, digest_b64 = encoded.split("$", 3)
            if scheme != "pbkdf2_sha256":
                return False
            salt = base64.urlsafe_b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
            expected = base64.urlsafe_b64decode(digest_b64 + "=" * (-len(digest_b64) % 4))
            derived = hashlib.pbkdf2_hmac("sha256", candidate.encode(), salt, int(iterations_s))
            return hmac.compare_digest(expected, derived)
        except Exception:
            return False
    return hmac.compare_digest(candidate.encode(), settings.app_password.encode())


def _signing_key() -> bytes:
    # Password changes invalidate existing sessions even when APP_SECRET stays the same.
    material = f"{settings.app_secret}\0{_password_material()}".encode()
    return hashlib.sha256(material).digest()


def create_session_token() -> str:
    issued = str(int(time.time()))
    nonce = secrets.token_urlsafe(18)
    payload = f"{issued}.{nonce}"
    sig = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def verify_session_token(token: str) -> bool:
    try:
        issued, nonce, sig_b64 = token.split(".", 2)
        issued_i = int(issued)
        if time.time() - issued_i > MAX_AGE or issued_i > time.time() + 60:
            return False
        payload = f"{issued}.{nonce}"
        expected = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        return hmac.compare_digest(expected, supplied)
    except Exception:
        return False


def require_auth(request: Request) -> None:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or not verify_session_token(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
