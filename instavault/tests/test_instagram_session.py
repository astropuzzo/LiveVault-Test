import json
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vault import instagram_session as sessions  # noqa: E402

TOKEN = '12345678%3AAbCdEfGhIjKlMn%3A17%3AAYfZ'
COOKIES = {'sessionid': TOKEN, 'csrftoken': 'real-browser-csrf', 'ds_user_id': '12345678'}


def response(status=200, data=None, *, html=False, headers=None, cookie=None):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    result._content = b'<html>not JSON</html>' if html else json.dumps(data or {}).encode()
    if cookie:
        result.cookies.set('csrftoken', cookie, domain='.instagram.com')
    return result


class BrowserSession:
    def __init__(self, replies):
        self.replies = list(replies)
        self.cookies = requests.cookies.RequestsCookieJar()
        self.headers = {}
        self.trust_env = True
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs, dict(self.headers)))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        self.cookies.update(item.cookies)
        return item


@pytest.fixture
def browser(monkeypatch):
    def setup(*replies):
        instance = BrowserSession(replies)
        monkeypatch.setattr(sessions.requests, 'Session', lambda: instance)
        return instance
    return setup


@pytest.mark.parametrize('source', [
    TOKEN,
    '12345678:AbCdEfGhIjKlMn:17:AYfZ',
    TOKEN.replace('%3A', '%3a'),
    TOKEN.replace('%', '%25'),
    json.dumps(TOKEN),
    'Cookie: sessionid=' + TOKEN + '; csrftoken=csrf',
    'cookie: sessionid=' + TOKEN + '; csrftoken=csrf',
    '.instagram.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\t' + TOKEN,
    '# Netscape HTTP Cookie File\n#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\t' + TOKEN,
    json.dumps({'sessionid': TOKEN}),
    json.dumps([{'domain': '.instagram.com', 'name': 'sessionid', 'value': TOKEN}]),
    json.dumps({'cookies': [{'domain': 'www.instagram.com', 'name': 'sessionid', 'value': TOKEN}]}),
], ids=['encoded', 'decoded', 'lowercase', 'double-encoded', 'quoted-json', 'header',
        'lowercase-header', 'netscape', 'http-only', 'json-dict', 'json-list', 'wrapped-list'])
def test_browser_export_formats_have_one_canonical_session(source):
    parsed = sessions.parse_cookies(source)
    assert parsed['sessionid'] == TOKEN
    assert parsed['ds_user_id'] == '12345678'


def test_literal_plus_inside_token_is_not_changed_to_space():
    result = sessions.parse_cookies('12345678:AbCdEfGhIjKlMn+literal:17:AYfZ')
    assert '%2B' in result['sessionid']
    assert '%20' not in result['sessionid']


@pytest.mark.parametrize('source', [
    '', 'not-a-session', '12345678%253Xbad', '12345678:bad\r\ninjection',
    json.dumps([{'domain': 'instagram.com.evil.test', 'name': 'sessionid', 'value': TOKEN}]),
    json.dumps([{'name': 'sessionid', 'value': TOKEN}]),
    json.dumps({'sessionid': TOKEN, 'ds_user_id': '999'}),
    json.dumps({'sessionid': None}),
    json.dumps([{'domain': '.instagram.com', 'name': [], 'value': TOKEN}]),
], ids=['missing', 'format', 'percent', 'control', 'foreign-domain', 'missing-domain', 'conflicting-owner', 'wrong-type', 'malformed-name'])
def test_invalid_inputs_fail_before_network_and_do_not_echo_credentials(source):
    with pytest.raises(sessions.SessionError) as error:
        sessions.parse_cookies(source)
    assert error.value.code == 'invalid_cookies'
    assert TOKEN not in str(error.value)


def test_positive_browser_identity_does_not_call_legacy_graphql(browser):
    instance = browser(response(data={'form_data': {'username': 'Browser_Account', 'user_id': '12345678'}}))
    account, cookies = sessions.validate_web_session(COOKIES)
    assert account == 'browser_account'
    assert cookies == COOKIES
    assert instance.trust_env is False
    assert [call[0] for call in instance.calls] == [sessions.EDIT_FORM]
    assert instance.calls[0][1] == {'timeout': (10, 20), 'allow_redirects': False}
    assert instance.headers['X-CSRFToken'] == COOKIES['csrftoken']
    assert instance.headers['X-IG-App-ID'] == '936619743392459'
    assert all(cookie.domain == '.instagram.com' and cookie.secure for cookie in instance.cookies)


def test_raw_sessionid_bootstraps_real_csrf_cookie(browser):
    instance = browser(response(html=True, cookie='server-issued-csrf'),
                       response(data={'form_data': {'username': 'browser_account'}}))
    account, cookies = sessions.validate_web_session(sessions.parse_cookies(TOKEN))
    assert account == 'browser_account'
    assert cookies['csrftoken'] == 'server-issued-csrf'
    assert [call[0] for call in instance.calls] == [sessions.ROOT, sessions.EDIT_FORM]
    assert instance.calls[1][2]['X-CSRFToken'] == 'server-issued-csrf'


@pytest.mark.parametrize('first', [response(404), response(html=True), response(data={'status': 'ok'})],
                         ids=['missing-endpoint', 'html', 'changed-schema'])
def test_browser_endpoint_fallback_requires_authenticated_identity(browser, first):
    instance = browser(first, response(data={'user': {'pk': 12345678, 'username': 'browser_account'}}))
    assert sessions.validate_web_session(COOKIES)[0] == 'browser_account'
    assert [call[0] for call in instance.calls] == [sessions.EDIT_FORM, sessions.CURRENT_USER]


def test_csrf_rotation_keeps_fallback_header_and_cookie_in_sync(browser):
    instance = browser(response(html=True, cookie='rotated-csrf'),
                       response(data={'user': {'pk': 12345678, 'username': 'browser_account'}}))
    account, cookies = sessions.validate_web_session(COOKIES)
    assert account == 'browser_account'
    assert cookies['csrftoken'] == 'rotated-csrf'
    assert instance.calls[1][2]['X-CSRFToken'] == 'rotated-csrf'


@pytest.mark.parametrize(('reply', 'code'), [
    (response(429, {'message': 'slow down'}), 'rate_limited'),
    (response(401, {'message': 'Please wait a few minutes before you try again.'}), 'rate_limited'),
    (response(401, {'message': 'login_required'}), 'session_rejected'),
    (response(302, headers={'Location': '/accounts/login/?next=/'}), 'session_rejected'),
    (response(400, {'message': 'checkpoint_required'}), 'verification_required'),
    (response(403, {'challenge': {'url': '/challenge/private-path'}}), 'verification_required'),
    (response(302, headers={'Location': '/challenge/123/'}), 'verification_required'),
    (response(503, html=True), 'verification_unavailable'),
    (response(403, html=True), 'verification_unavailable'),
    (response(401, html=True), 'verification_unavailable'),
    (response(302, headers={'Location': 'https://other.example/'}), 'verification_unavailable'),
], ids=['429', '401-pause', 'expired', 'login-redirect', 'checkpoint', 'challenge-json',
        'challenge-redirect', 'upstream', 'blocked', 'ambiguous-401', 'foreign-redirect'])
def test_failure_classification_never_retries_with_mobile_api(browser, reply, code):
    instance = browser(reply)
    with pytest.raises(sessions.SessionError) as error:
        sessions.validate_web_session(COOKIES)
    assert error.value.code == code
    assert error.value.http_status == reply.status_code
    assert len(instance.calls) == 1
    assert TOKEN not in str(error.value)
    assert 'private-path' not in str(error.value)


def test_schema_errors_remain_unavailable_instead_of_bad_cookie(browser):
    browser(response(data={'unexpected': True}), response(data={'user': {'username': 'no-numeric-id'}}))
    with pytest.raises(sessions.SessionError) as error:
        sessions.validate_web_session(COOKIES)
    assert error.value.code == 'verification_unavailable'


def test_verified_account_must_match_the_session_owner(browser):
    browser(response(data={'form_data': {'username': 'other_account', 'user_id': '999'}}))
    with pytest.raises(sessions.SessionError) as error:
        sessions.validate_web_session(COOKIES)
    assert error.value.code == 'session_rejected'


def test_no_real_csrf_after_successful_identity_check_is_not_saved(browser):
    browser(response(html=True), response(data={'form_data': {'username': 'browser_account'}}))
    with pytest.raises(sessions.SessionError) as error:
        sessions.validate_web_session(sessions.parse_cookies(TOKEN))
    assert error.value.code == 'verification_unavailable'
    assert error.value.source == 'bootstrap'


@pytest.mark.parametrize('failure', [requests.Timeout('raw private diagnostic'), requests.ConnectionError('raw private diagnostic')],
                         ids=['timeout', 'connection'])
def test_network_errors_do_not_expose_upstream_messages(browser, failure):
    browser(failure)
    with pytest.raises(sessions.SessionError) as error:
        sessions.validate_web_session(COOKIES)
    assert error.value.code == 'network_error'
    assert 'raw private diagnostic' not in str(error.value)
    assert error.value.__cause__ is None
