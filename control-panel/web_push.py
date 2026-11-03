"""Web Push (RFC 8030/8291/8292) for OpenAstro Control, the mechanism InstaVault already uses to reach the owner's
phone (instavault/vault/push.py), with the panel's own key and subscriptions. The text is encrypted for the
browser, so the push service cannot read it. cryptography is imported on use: without it only notices stop."""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

STATE_DIR = Path("/var/lib/openastro-control")
SUBJECT = "https://openastro.tailf2871c.ts.net:8443"
# Browser push services only, so a stolen session cannot point the server at the LAN or any other address.
PUSH_HOSTS = ("googleapis.com", "google.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")
MAX_SUBSCRIPTIONS = 20
SEND_TIMEOUT = 10

_lock = threading.Lock()
_key = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(request):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(request, timeout=SEND_TIMEOUT)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _point(key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _write_private(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    temporary.replace(path)


def keys():
    """The panel's VAPID key pair, made on first use beside the panel's other state."""
    global _key
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    with _lock:
        if _key is None:
            path = STATE_DIR / "vapid.pem"
            if not path.exists():
                _write_private(path, ec.generate_private_key(ec.SECP256R1()).private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            _key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        return _key, b64(_point(_key))


def public_key() -> str:
    return keys()[1]


def vapid_header(endpoint: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    private, public = keys()
    header = b64(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64(json.dumps({"aud": "{0.scheme}://{0.netloc}".format(urlparse(endpoint)),
                             "exp": int(time.time()) + 12 * 3600, "sub": SUBJECT}).encode())
    r, s = decode_dss_signature(private.sign(f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"vapid t={header}.{claims}.{b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={public}"


def _hkdf(salt: bytes, info: bytes, length: int, secret: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(hashes.SHA256(), length, salt, info).derive(secret)


def encrypt(p256dh: str, auth: str, plaintext: bytes) -> bytes:
    # RFC 8291: a one-off key, a secret shared with the browser's key, one aes128gcm record.
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    browser = unb64(p256dh)
    local = ec.generate_private_key(ec.SECP256R1())
    shared = local.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), browser))
    key = _hkdf(unb64(auth), b"WebPush: info\x00" + browser + _point(local), 32, shared)
    salt = os.urandom(16)
    cek = _hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, key)
    nonce = _hkdf(salt, b"Content-Encoding: nonce\x00", 12, key)
    return salt + (4096).to_bytes(4, "big") + bytes([65]) + _point(local) + AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)


def _allowed(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == item or host.endswith("." + item) for item in PUSH_HOSTS)


def _load() -> list[dict]:
    try:
        rows = json.loads((STATE_DIR / "push-subscriptions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and all(isinstance(row.get(k), str) for k in ("endpoint", "p256dh", "auth"))]


def _save(rows: list[dict]) -> None:
    _write_private(STATE_DIR / "push-subscriptions.json", json.dumps(rows).encode("utf-8"))


def subscribe(subscription: dict) -> None:
    endpoint = str(subscription.get("endpoint", ""))
    if len(endpoint) > 1000 or not _allowed(endpoint):
        host = (urlparse(endpoint).hostname or "nessuno")[:100]
        raise ValueError(f"Servizio di notifica del browser non accettato: {host}.")
    secrets = subscription.get("keys") or {}
    try:
        p256dh, auth = str(secrets["p256dh"]), str(secrets["auth"])
        valid = len(unb64(p256dh)) == 65 and len(unb64(auth)) == 16
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError("Iscrizione alle notifiche non valida.")
    with _lock:
        rows = [row for row in _load() if row["endpoint"] != endpoint]
        rows.append({"endpoint": endpoint, "p256dh": p256dh, "auth": auth, "created": int(time.time())})
        _save(rows[-MAX_SUBSCRIPTIONS:])


def unsubscribe(endpoint: str) -> None:
    with _lock:
        rows = _load()
        kept = [row for row in rows if row["endpoint"] != str(endpoint)]
        if len(kept) != len(rows):
            _save(kept)


def count() -> int:
    with _lock:
        return len(_load())


def notify(title: str, body: str, url: str = "/", urgency: str = "normal") -> int:
    """Sends one notice to every subscribed browser and returns how many took it. Never raises."""
    try:
        with _lock:
            rows = _load()
        payload = json.dumps({"title": title[:120], "body": body[:300], "url": url}, ensure_ascii=False).encode()
    except Exception as exc:
        print(f"OpenAstro push: {type(exc).__name__}", flush=True)
        return 0
    sent = 0
    for row in rows:
        host = urlparse(row["endpoint"]).netloc
        if not _allowed(row["endpoint"]):
            continue
        try:
            request = urllib.request.Request(row["endpoint"], data=encrypt(row["p256dh"], row["auth"], payload), method="POST", headers={
                "Authorization": vapid_header(row["endpoint"]), "TTL": "86400", "Urgency": urgency,
                "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream"})
            with _open(request) as reply:
                status = reply.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except Exception as exc:
            print(f"OpenAstro push {host}: {type(exc).__name__}", flush=True)
            continue
        if status in (404, 410):
            unsubscribe(row["endpoint"])
        elif 200 <= status < 300:
            sent += 1
        else:
            print(f"OpenAstro push {host}: HTTP {status}", flush=True)
    return sent
