"""Parse Instagram cookies and verify them against Instagram's browser API.

Only a response identifying the authenticated account is positive evidence.
An unavailable endpoint, a server-side block or a network error does not prove
that the user's browser session is invalid.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, unquote, urlparse

import requests

ROOT = 'https://www.instagram.com/'
EDIT_FORM = ROOT + 'api/v1/accounts/edit/web_form_data/'
CURRENT_USER = ROOT + 'api/v1/accounts/current_user/?edit=true'
TIMEOUT = (10, 20)
USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36')
_NAMES = {'sessionid', 'csrftoken', 'ds_user_id', 'mid', 'ig_did', 'rur', 'datr', 'ig_nrcb'}
_MESSAGES = {
    'invalid_cookies': 'Non trovo cookie Instagram completi e validi. Incolla sessionid o un’esportazione JSON/Netscape.',
    'session_rejected': 'Instagram richiede un nuovo accesso per questa sessione. Aggiorna i cookie dal browser e riprova.',
    'rate_limited': 'Instagram chiede una pausa prima di altre richieste. Attendi prima di riprovare.',
    'verification_required': 'Instagram richiede una verifica dell’account. Completala nell’app o nel browser, poi riprova.',
    'verification_unavailable': 'Non è possibile verificare la sessione Instagram in questo momento. Riprova più tardi.',
    'network_error': 'Il server non riesce a raggiungere Instagram. Riprova tra poco.',
}


class SessionError(ValueError):
    """An intentionally safe error: no response body, cookie or raw exception."""

    def __init__(self, code, *, source='input', http_status=None):
        self.code = code if code in _MESSAGES else 'verification_unavailable'
        self.source = source if source in {'input', 'bootstrap', 'edit_form', 'current_user'} else 'input'
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.message = _MESSAGES[self.code]
        super().__init__(self.message)


def _domain(value):
    if not isinstance(value, str):
        return False
    value = value.lower().lstrip('.')
    return value == 'instagram.com' or value.endswith('.instagram.com')


def _value(value):
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise SessionError('invalid_cookies')
    if any(ord(char) < 32 or ord(char) > 126 for char in value) or ';' in value:
        raise SessionError('invalid_cookies')
    return value


def _sessionid(value):
    value = _value(value).strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    if re.search(r'%(?![0-9a-fA-F]{2})', value):
        raise SessionError('invalid_cookies')
    try:
        decoded = unquote(value, errors='strict')
        # One extra URI-encoding layer is recognizable at the numeric owner
        # separator. Do not repeatedly unquote opaque token contents.
        if re.match(r'^\d+%3a', decoded, re.IGNORECASE):
            decoded = unquote(decoded, errors='strict')
    except UnicodeError:
        raise SessionError('invalid_cookies') from None
    _value(decoded)
    match = re.fullmatch(r'(\d{1,30}):([^\s;]+)', decoded)
    if not match or len(decoded) < 20:
        raise SessionError('invalid_cookies')
    # unquote_plus would corrupt a literal '+' inside the bearer credential.
    return quote(decoded, safe=''), match[1]


def parse_cookies(source) -> dict:
    """Accept common browser exports without sending any network request."""
    if not isinstance(source, str) or not source or len(source) > 200000:
        raise SessionError('invalid_cookies')
    text = source.lstrip('\ufeff').strip()
    cookies = {}

    def add(name, value):
        if not isinstance(name, str):
            raise SessionError('invalid_cookies')
        if name not in _NAMES:
            return
        value = _value(value)
        if name in cookies and cookies[name] != value:
            raise SessionError('invalid_cookies')
        cookies[name] = value

    try:
        item = json.loads(text)
    except json.JSONDecodeError:
        item = text
    if isinstance(item, dict) and 'cookies' in item:
        item = item['cookies']
    if isinstance(item, list):
        if len(item) > 1000:
            raise SessionError('invalid_cookies')
        for cookie in item:
            if not isinstance(cookie, dict):
                raise SessionError('invalid_cookies')
            if _domain(cookie.get('domain')):
                add(cookie.get('name'), cookie.get('value'))
    elif isinstance(item, dict):
        for name, value in item.items():
            add(name, value)
    elif isinstance(item, str):
        text = re.sub(r'^cookie\s*:\s*', '', item.strip(), count=1, flags=re.IGNORECASE)
        if '\t' not in text and '\n' not in text and re.search(r'(?:^|;)\s*sessionid\s*=', text):
            for part in text.split(';'):
                name, separator, value = part.strip().partition('=')
                if separator:
                    add(name, value)
        elif '\t' in text or '\n' in text:
            for line in text.splitlines():
                line = line.removeprefix('#HttpOnly_')
                if not line.strip() or line.startswith('#'):
                    continue
                columns = line.split('\t')
                if len(columns) == 7 and _domain(columns[0]):
                    add(columns[5], columns[6])
        else:
            add('sessionid', text)
    else:
        raise SessionError('invalid_cookies')
    if not cookies.get('sessionid'):
        raise SessionError('invalid_cookies')
    normalized, owner = _sessionid(cookies['sessionid'])
    if cookies.get('ds_user_id', owner) != owner:
        raise SessionError('invalid_cookies')
    cookies.update(sessionid=normalized, ds_user_id=owner)
    return cookies


def _get(session, url, source):
    try:
        return session.get(url, timeout=TIMEOUT, allow_redirects=False)
    except requests.RequestException:
        raise SessionError('network_error', source=source) from None


def _inspect(response, source):
    """Classify explicit auth/rate/challenge signals before schema fallback."""
    status = response.status_code
    try:
        data = response.json()
        data = data if isinstance(data, dict) else None
    except (ValueError, UnicodeError):
        data = None
    message = ' '.join(str(data.get(key, ''))[:2000] for key in
                       ['message', 'error_type', 'error', 'reason']) if data else ''
    lower = message.casefold()
    location = urlparse(response.headers.get('Location', '')).path.lower()
    error_args = {'source': source, 'http_status': status}
    if status == 429 or any(term in lower for term in
                            ['please wait', 'wait a few minutes', 'too_many_requests', 'rate_limit']):
        raise SessionError('rate_limited', **error_args)
    if ((data and (data.get('challenge') or data.get('checkpoint_url')))
            or any(term in lower for term in ['challenge_required', 'checkpoint_required', 'feedback_required'])
            or location.startswith(('/challenge/', '/checkpoint/'))):
        raise SessionError('verification_required', **error_args)
    if ('login_required' in lower or (data and data.get('authenticated') is False)
            or location.startswith('/accounts/login')):
        raise SessionError('session_rejected', **error_args)
    # A 401 without an explicit login_required response can also be an API/IP
    # restriction. It must not be presented as proof of an expired cookie.
    if status not in {200, 404}:
        raise SessionError('verification_unavailable', **error_args)
    return data if status == 200 else None


def _update_cookies(session, response, normalized):
    for name, value in response.cookies.get_dict().items():
        if name in _NAMES:
            try:
                value = _value(value)
            except SessionError:
                continue
            normalized[name] = value
            session.cookies.set(name, value, domain='.instagram.com', path='/', secure=True)
            if name == 'csrftoken':
                session.headers['X-CSRFToken'] = value


def _identity(data, key):
    user = data.get(key) if isinstance(data, dict) else None
    if not isinstance(user, dict):
        return None
    name = user.get('username')
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?', name):
        return None
    ident = next((user[k] for k in ['user_id', 'username_id', 'pk', 'id'] if user.get(k) is not None), None)
    if ident is not None and (type(ident) not in {int, str} or not str(ident).isdigit()):
        return None
    if key == 'user' and ident is None:
        return None
    return name.lower(), str(ident) if ident is not None else None


def validate_web_session(cookies) -> tuple[str, dict]:
    """Return only positively verified browser credentials and account name."""
    if not isinstance(cookies, dict):
        raise SessionError('invalid_cookies')
    normalized = parse_cookies(json.dumps(cookies))
    with requests.Session() as session:
        session.trust_env = False
        session.headers.update({
            'User-Agent': USER_AGENT, 'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest', 'X-IG-App-ID': '936619743392459',
            'Referer': ROOT + 'accounts/edit/',
        })
        for name, value in normalized.items():
            session.cookies.set(name, value, domain='.instagram.com', path='/', secure=True)
        if not normalized.get('csrftoken'):
            response = _get(session, ROOT, 'bootstrap')
            _inspect(response, 'bootstrap')
            _update_cookies(session, response, normalized)
        if normalized.get('csrftoken'):
            session.headers['X-CSRFToken'] = normalized['csrftoken']
        response = _get(session, EDIT_FORM, 'edit_form')
        data = _inspect(response, 'edit_form')
        _update_cookies(session, response, normalized)
        identity = _identity(data, 'form_data')
        source = 'edit_form'
        if identity is None:
            # Browser API fallback only: no private mobile API or GraphQL hash.
            response = _get(session, CURRENT_USER, 'current_user')
            data = _inspect(response, 'current_user')
            _update_cookies(session, response, normalized)
            identity = _identity(data, 'user')
            source = 'current_user'
        if identity is None:
            raise SessionError('verification_unavailable', source=source,
                               http_status=response.status_code)
        account, ident = identity
        if ident is not None and ident != normalized['ds_user_id']:
            raise SessionError('session_rejected', source=source,
                               http_status=response.status_code)
        if not normalized.get('csrftoken'):
            raise SessionError('verification_unavailable', source='bootstrap')
        return account, parse_cookies(json.dumps(normalized))
