import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vault import security, store  # noqa: E402


@pytest.fixture
def private_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'SECRETS', tmp_path)
    salt, password = 'test-salt', 'test-password'
    auth = {'username': 'test-user', 'salt': salt,
            'hash': hashlib.sha256((salt + ':' + password).encode()).hexdigest()}
    (tmp_path / 'auth.json').write_text(json.dumps(auth))
    (tmp_path / 'secret').write_bytes(b'private-test-key-' * 3)
    return tmp_path, auth


def test_existing_control_password_and_wrong_credentials(private_setup):
    assert security.check_password('test-user', 'test-password')
    assert not security.check_password('test-user', 'wrong')
    assert not security.check_password('wrong', 'test-password')
    assert not security.check_password('test-user', 'x' * 1025)
    assert not security.check_password(None, 'test-password')


def test_signed_session_rejects_tampering_and_supplies_bound_csrf(private_setup):
    token = security.issue_session()
    assert security.verify_session(token)
    assert not security.verify_session(token[:-1] + ('A' if token[-1] != 'A' else 'B'))
    assert security.csrf_token(token) == security.csrf_token(token)
    assert security.csrf_token(token) != security.csrf_token(security.issue_session())
    assert security.csrf_token(token) != token.split('.')[1]
    with pytest.raises(ValueError):
        security.csrf_token('invalid')


def test_session_expires_and_rejects_future_issue_time(private_setup, monkeypatch):
    clock = [1_000_000]
    monkeypatch.setattr(security.time, 'time', lambda: clock[0])
    token = security.issue_session()
    clock[0] += security.MAX_AGE - 1
    assert security.verify_session(token)
    clock[0] += 1
    assert not security.verify_session(token)
    clock[0] = 999_900
    assert not security.verify_session(token)


def test_password_change_revokes_cookies_but_preserves_instagram_data(private_setup):
    path, auth = private_setup
    token = security.issue_session()
    encrypted = security.encrypt_json({'sessionid': 'private-instagram-test-cookie'})
    auth['hash'] = hashlib.sha256(b'test-salt:new-password').hexdigest()
    (path / 'auth.json').write_text(json.dumps(auth))
    assert not security.verify_session(token)
    assert security.check_password('test-user', 'new-password')
    assert security.decrypt_json(encrypted)['sessionid'] == 'private-instagram-test-cookie'


def test_encrypted_session_is_not_plaintext_and_rejects_tampering(private_setup):
    value = {'username': 'account', 'cookies': {'sessionid': 'private-cookie', 'csrftoken': 'private-csrf'}}
    encrypted = security.encrypt_json(value)
    assert b'private-cookie' not in encrypted
    assert b'account' not in encrypted
    assert security.decrypt_json(encrypted) == value
    with pytest.raises(ValueError):
        security.decrypt_json(encrypted[:-4] + b'AAAA')


@pytest.mark.parametrize('secret', [None, b'', b'short', b'x' * 4097],
                         ids=['missing', 'empty', 'short', 'oversized'])
def test_missing_or_invalid_secret_fails_closed(private_setup, secret):
    path, _ = private_setup
    if secret is None:
        (path / 'secret').unlink()
    else:
        (path / 'secret').write_bytes(secret)
    assert not security.check_password('test-user', 'test-password')
    assert not security.verify_session('invalid')
    with pytest.raises((OSError, ValueError)):
        security.issue_session()
    with pytest.raises((OSError, ValueError)):
        security.encrypt_json({'sessionid': 'not-stored'})


@pytest.mark.parametrize('raw', ['', '[]', '{', '{"username":"u","salt":"s","hash":"wrong"}'],
                         ids=['empty', 'array', 'invalid-json', 'invalid-hash'])
def test_missing_or_malformed_auth_fails_closed(private_setup, raw):
    path, _ = private_setup
    (path / 'auth.json').write_text(raw)
    assert not security.check_password('test-user', 'test-password')
    with pytest.raises((ValueError, TypeError)):
        security.issue_session()


def test_oversized_and_wrong_type_json_is_rejected(private_setup):
    with pytest.raises(ValueError):
        security.encrypt_json({'cookie': 'x' * (1024 * 1024)})
    with pytest.raises(ValueError):
        security.encrypt_json(['not-a-session-object'])
    with pytest.raises(ValueError):
        security.decrypt_json('not-bytes')
