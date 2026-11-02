"""Bounded asynchronous Instagram sign-in, including user-supplied verification codes."""
from __future__ import annotations

import json
import logging
import queue
import re
import secrets
import threading
import time
from collections import deque

from instagrapi import Client
from instagrapi.exceptions import BadCredentials, BadPassword, ChallengeRequired, PleaseWaitFewMinutes, TwoFactorRequired
from instagrapi.mixins import challenge as challenge_module
from instagrapi.mixins.challenge import ChallengeChoice

from . import security, store, sync

logging.getLogger('instagrapi').setLevel(logging.CRITICAL)
challenge_module.print = lambda *a, **k: None
CODE_WAIT = 300
LOCK = threading.Lock()
CURRENT = None
ATTEMPTS = deque()


def device_path():
    return store.DATA / 'instagram-device.enc'


def client():
    value = Client()
    value.read_timeout = 25
    try:
        saved = security.decrypt_json(device_path().read_bytes())
        value.override_app_version = True
        value.set_settings(saved)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    # Never read stdin or change the account password in an unattended server.
    def no_password_change(*_):
        raise ValueError('Instagram richiede una verifica dall’app ufficiale. Completala lì e riprova.')
    value.change_password_handler = no_password_change
    return value


def save_device(value):
    settings = value.get_settings()
    settings['authorization_data'] = {}
    settings['cookies'] = {k: v for k, v in (settings.get('cookies') or {}).items() if k not in {'sessionid', 'ds_user_id'}}
    content = security.encrypt_json(settings)
    temporary = device_path().with_suffix('.tmp')
    with temporary.open('wb') as handle:
        __import__('os').chmod(temporary, 0o600)
        handle.write(content); handle.flush(); __import__('os').fsync(handle.fileno())
    temporary.replace(device_path())


def finish(value, cancelled=None, committed=None):
    # Client.login() already obtained an authenticated mobile session. Requiring
    # Instaloader's old GraphQL probe here incorrectly rejects that valid login.
    data = value.authorization_data or {}
    owner = data.get('ds_user_id') or value.cookie_dict.get('ds_user_id')
    account = store.username(value.username)
    cookies = dict(value.cookie_dict)
    cookies['sessionid'] = value.sessionid or ''
    if not cookies['sessionid'] or not str(owner).isdigit():
        raise ValueError('Instagram non ha restituito una sessione autenticata completa.')
    cookies['ds_user_id'] = str(owner)
    from .instagram_session import parse_cookies, validate_web_session
    cookies = parse_cookies(json.dumps(cookies))
    if not cookies.get('csrftoken'):
        verified, cookies = validate_web_session(cookies)
        if verified.lower() != account.lower():
            raise ValueError('La sessione restituita da Instagram appartiene a un altro account.')
    import instaloader
    loader = instaloader.Instaloader(quiet=True, max_connection_attempts=1, request_timeout=20)
    loader.load_session(account, cookies)
    try:
        with sync.SESSION_LOCK:
            if cancelled and cancelled.is_set():
                raise ValueError('Accesso annullato.')
            result = sync.save_session(loader, verified_identity=account, verified_via='mobile_login')
            if committed:
                committed.set()
            return result
    finally:
        loader.close()


class Attempt:
    def __init__(self, username):
        self.id = secrets.token_urlsafe(18)
        self.username = username
        self.created_at = time.time()
        self.client = client()
        self.client.challenge_code_handler = self.code
        self.codes = queue.Queue(maxsize=1)
        self.asking = threading.Event()
        self.done = threading.Event()
        self.cancelled = threading.Event()
        self.committed = threading.Event()
        self.channel = ''
        self.result = None
        self.error = None
        self.submissions = 0

    def code(self, username, choice):
        self.channel = 'sms' if choice == ChallengeChoice.SMS else 'email'
        return self.wait_code()

    def wait_code(self):
        if self.cancelled.is_set():
            return ''
        self.asking.set()
        try:
            return self.codes.get(timeout=CODE_WAIT)
        except queue.Empty:
            return ''

    def run(self, password):
        try:
            try:
                accepted = self.client.login(self.username, password)
            except TwoFactorRequired:
                self.channel = 'two_factor'
                code = self.wait_code()
                if not code:
                    raise ValueError('Il codice non è arrivato in tempo. Ricomincia l’accesso.')
                accepted = self.client.login(self.username, password, verification_code=code)
            if self.cancelled.is_set():
                raise ValueError('Accesso annullato.')
            if not accepted:
                raise ValueError('Instagram non ha completato l’accesso.')
            self.result = finish(self.client, self.cancelled, self.committed)
            with store.db() as connection:
                connection.execute('UPDATE profiles SET next_sync=0 WHERE enabled=1')
            sync.WAKE.set()
        except Exception as exc:
            self.error = exc
            step = str((getattr(self.client, 'last_json', None) or {}).get('step_name', ''))
            safe_step = step if re.fullmatch(r'[a-zA-Z_]{1,60}', step) else '-'
            print(f'InstaVault: Instagram login result={type(exc).__name__} step={safe_step}', flush=True)
        finally:
            password = None
            self.client.password = None
            try:
                save_device(self.client)
            except Exception:
                pass
            self.done.set(); self.asking.set()

    def status(self):
        common = {'attempt_id': self.id}
        if self.done.is_set():
            if self.error is not None:
                return common | {'state': 'error', 'error': message(self.error)}
            return common | {'state': 'done'} | self.result
        if self.asking.is_set():
            return common | {'state': 'code_required', 'code_required': True, 'channel': self.channel}
        return common | {'state': 'running'}


def password_login(body):
    global CURRENT
    with LOCK:
        if body.get('code'):
            attempt = CURRENT
            if not attempt or body.get('attempt_id') != attempt.id or attempt.done.is_set() or not attempt.asking.is_set():
                raise ValueError('Il tentativo è scaduto. Ricomincia l’accesso.')
            code = str(body['code']).strip().replace(' ', '')
            if not re.fullmatch(r'[0-9A-Za-z-]{4,16}', code):
                raise ValueError('Controlla il codice di verifica.')
            if attempt.submissions >= 5:
                raise ValueError('Troppi codici errati. Ricomincia l’accesso.')
            attempt.submissions += 1
            attempt.asking.clear(); attempt.codes.put_nowait(code)
        else:
            if CURRENT and not CURRENT.done.is_set():
                raise ValueError('Un accesso è già in corso. Attendi o annullalo prima di riprovare.')
            account = store.username(body.get('username', ''))
            password = str(body.get('password', ''))[:1000]
            if not password:
                raise ValueError('Scrivi la password di Instagram.')
            while ATTEMPTS and ATTEMPTS[0] < time.time() - 900:
                ATTEMPTS.popleft()
            if len(ATTEMPTS) >= 5:
                raise ValueError('Troppi tentativi di accesso. Attendi 15 minuti prima di riprovare.')
            ATTEMPTS.append(time.time())
            attempt = CURRENT = Attempt(account)
            threading.Thread(target=attempt.run, args=(password,), name='instagram-login', daemon=True).start()
            password = None
    return attempt.status()


def password_status(attempt_id):
    with LOCK:
        if not CURRENT or CURRENT.id != attempt_id:
            raise ValueError('Il tentativo è scaduto. Ricomincia l’accesso.')
        return CURRENT.status()


def cancel(attempt_id):
    with LOCK:
        if CURRENT and CURRENT.id == attempt_id and not CURRENT.done.is_set():
            with sync.SESSION_LOCK:
                if CURRENT.committed.is_set():
                    return {'ok': True, 'already_connected': True}
                CURRENT.cancelled.set()
                try:
                    CURRENT.codes.put_nowait('')
                except queue.Full:
                    pass
        elif CURRENT and CURRENT.id == attempt_id and CURRENT.result:
            return {'ok': True, 'already_connected': True}
    return {'ok': True}


def cancel_current():
    with LOCK:
        if CURRENT and not CURRENT.done.is_set():
            CURRENT.cancelled.set()
            try:
                CURRENT.codes.put_nowait('')
            except queue.Full:
                pass


def message(exc):
    from .instagram_session import SessionError
    text = str(exc).lower()
    if isinstance(exc, SessionError):
        return str(exc)
    if type(exc) is ValueError:
        return str(exc)
    if isinstance(exc, (BadPassword, BadCredentials)):
        return 'Instagram non ha accettato username e password. Verifica le credenziali nell’app ufficiale.'
    if isinstance(exc, PleaseWaitFewMinutes):
        return 'Instagram chiede una pausa prima di un altro accesso. Riprova tra 30 minuti.'
    if isinstance(exc, ChallengeRequired) and ('not provided' in text or 'code required' in text):
        return 'Il codice non è arrivato in tempo. Ricomincia l’accesso: Instagram ne manderà uno nuovo.'
    if isinstance(exc, ChallengeRequired):
        return 'Instagram richiede una conferma dall’app ufficiale. Completala sul telefono, poi riprova qui o importa la sessione del browser.'
    if isinstance(exc, AssertionError):
        return 'Instagram non ha accettato il codice. Ricomincia l’accesso.'
    return 'Instagram non ha completato l’accesso. Riprova o collega la sessione del browser.'
