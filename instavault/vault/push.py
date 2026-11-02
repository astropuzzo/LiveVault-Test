"""Web push (RFC 8030/8291/8292) with the standard library and cryptography:
the text is encrypted for the browser, so the push service cannot read it."""
from __future__ import annotations

import base64
import json
import os
import time
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import store


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def point(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def keys():
    # One VAPID key pair for this install, kept next to the database.
    path = store.DATA / 'vapid.pem'
    if not path.exists():
        pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        temporary = path.with_suffix('.tmp')
        with temporary.open('wb') as f:
            os.chmod(temporary, 0o600); f.write(pem)
        temporary.replace(path)
    private = serialization.load_pem_private_key(path.read_bytes(), password=None)
    return private, b64(point(private))


def vapid_header(endpoint):
    private, public = keys()
    header = b64(json.dumps({'typ': 'JWT', 'alg': 'ES256'}).encode())
    claims = b64(json.dumps({'aud': '{0.scheme}://{0.netloc}'.format(urlparse(endpoint)), 'exp': int(time.time()) + 12 * 3600,
                             'sub': 'https://instavault.tailf2871c.ts.net'}).encode())
    r, s = decode_dss_signature(private.sign(f'{header}.{claims}'.encode(), ec.ECDSA(hashes.SHA256())))
    return f'vapid t={header}.{claims}.{b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))}, k={public}'


def hkdf(salt, info, length, secret):
    return HKDF(hashes.SHA256(), length, salt, info).derive(secret)


def encrypt(p256dh, auth, plaintext):
    # RFC 8291: one-off key, shared secret with the browser's key, aes128gcm record.
    browser = unb64(p256dh)
    local = ec.generate_private_key(ec.SECP256R1())
    shared = local.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), browser))
    key = hkdf(unb64(auth), b'WebPush: info\x00' + browser + point(local), 32, shared)
    salt = os.urandom(16)
    cek = hkdf(salt, b'Content-Encoding: aes128gcm\x00', 16, key)
    nonce = hkdf(salt, b'Content-Encoding: nonce\x00', 12, key)
    return salt + (4096).to_bytes(4, 'big') + bytes([65]) + point(local) + AESGCM(cek).encrypt(nonce, plaintext + b'\x02', None)


def subscribe(subscription):
    endpoint = str(subscription.get('endpoint', ''))
    secrets = subscription.get('keys') or {}
    try:
        p256dh, auth = str(secrets['p256dh']), str(secrets['auth'])
        valid = endpoint.startswith('https://') and len(endpoint) <= 1000 and len(unb64(p256dh)) == 65 and len(unb64(auth)) == 16
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError('Iscrizione alle notifiche non valida.')
    with store.db() as c:
        c.execute('INSERT OR REPLACE INTO push_subscriptions VALUES (?,?,?,?)', (endpoint, p256dh, auth, store.now()))


def unsubscribe(endpoint):
    with store.db() as c:
        c.execute('DELETE FROM push_subscriptions WHERE endpoint=?', (str(endpoint),))


def notify(title, body, url='./'):
    """Sends one notice to every subscribed browser. Never raises."""
    with store.db() as c:
        rows = [dict(r) for r in c.execute('SELECT * FROM push_subscriptions')]
    payload = json.dumps({'title': title[:120], 'body': body[:300], 'url': url}, ensure_ascii=False).encode()
    sent = 0
    for row in rows:
        host = urlparse(row['endpoint']).netloc
        try:
            reply = requests.post(row['endpoint'], data=encrypt(row['p256dh'], row['auth'], payload), timeout=10, headers={
                'Authorization': vapid_header(row['endpoint']), 'TTL': '86400', 'Urgency': 'normal',
                'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream'})
            if reply.status_code in (404, 410):
                unsubscribe(row['endpoint'])
            elif reply.ok:
                sent += 1
            else:
                print(f'InstaVault: push {host}: HTTP {reply.status_code}', flush=True)
        except Exception as exc:
            print(f'InstaVault: push {host}: {type(exc).__name__}', flush=True)
    return sent
