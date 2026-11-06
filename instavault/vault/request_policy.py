"""Persistent request limits shared by accounts, jobs and process restarts.

These are local ceilings, not a claim about Instagram's allowed request rate.
Reservations count failed attempts too; changing accounts cannot bypass host rest.
"""
from __future__ import annotations

import hashlib
import re
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from . import store

HOST = 'host'
GAPS = (3, 5)
LIMITS = ((False, '', 3600, 180, 'host_hour'), (False, '', 86400, 3000, 'host_day'),
          (True, '', 3600, 90, 'account_hour'), (True, '', 86400, 1800, 'account_day'),
          (False, 'maintenance', 3600, 12, 'maintenance_host_hour'),
          (False, 'maintenance', 86400, 120, 'maintenance_host_day'),
          (True, 'maintenance', 3600, 6, 'maintenance_account_hour'),
          (True, 'maintenance', 86400, 72, 'maintenance_account_day'))
RATE_ERRORS = {'ClientThrottledError', 'PleaseWaitFewMinutes', 'TooManyRequestsException',
               'RateLimitError', 'FeedbackRequired', 'SentryBlock'}
LOGIN_ERRORS = {'LoginRequired', 'ClientLoginRequired', 'ClientUnauthorizedError',
                'ChallengeRequired', 'ChallengeError', 'ChallengeUnknownStep',
                'AccountSuspended', 'AccountContactPointRequired', 'TwoFactorRequired'}


class Deferred(Exception):
    def __init__(self, until, reason):
        self.until, self.reason = float(until), str(reason)
        super().__init__('Richieste Instagram in pausa; ripresa automatica al prossimo controllo.')


def _account(account):
    return str(account or 'anonymous')


def _scope(account):
    return 'account:' + _account(account)


def initialize():
    with store.db() as c:
        c.executescript('''
          CREATE TABLE IF NOT EXISTS ig_request_state (
            scope TEXT PRIMARY KEY, cooldown_until REAL NOT NULL DEFAULT 0,
            strikes INTEGER NOT NULL DEFAULT 0, last_failure REAL NOT NULL DEFAULT 0,
            next_slot REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS ig_request_log (
            id INTEGER PRIMARY KEY, account TEXT NOT NULL, lane TEXT NOT NULL,
            endpoint TEXT NOT NULL DEFAULT '', at REAL NOT NULL);
          CREATE INDEX IF NOT EXISTS ig_request_log_time ON ig_request_log(at);
          CREATE INDEX IF NOT EXISTS ig_request_log_account ON ig_request_log(account,at);
        ''')


def _cooldown(c, account, now):
    rows = c.execute('SELECT cooldown_until,reason FROM ig_request_state WHERE scope IN (?,?)',
                     (HOST, _scope(account))).fetchall()
    return max(((r['cooldown_until'], r['reason']) for r in rows), default=(now, ''))


def _gate(c, account, lane, now):
    until, reason = _cooldown(c, account, now)
    until = max(now, until)
    for scoped, category, window, limit, label in LIMITS:
        if category and lane != category:
            continue
        clauses, args = ['at>?'], [now-window]
        if scoped:
            clauses.append('account=?'); args.append(_account(account))
        if category:
            clauses.append('lane=?'); args.append(category)
        row = c.execute('SELECT at FROM ig_request_log WHERE ' + ' AND '.join(clauses)
                        + ' ORDER BY at DESC LIMIT 1 OFFSET ?', [*args, limit-1]).fetchone()
        if row and row['at'] + window > until:
            until, reason = row['at'] + window, 'budget_' + label
    return until, reason


def ready_at(account, lane='fresh'):
    initialize()
    with store.db() as c:
        return _gate(c, account, lane, store.now())[0]


def check(account, lane='fresh'):
    initialize()
    now = store.now()
    with store.db() as c:
        until, reason = _gate(c, account, lane, now)
    if until > now:
        raise Deferred(until, reason)


def _endpoint(value):
    # Keep an endpoint category, never tokens, queries or profile/media ids.
    path = urlsplit(str(value)).path
    parts = [p for p in path.split('/') if p and p not in {'api', 'v1'}]
    return '/'.join(':id' if re.search(r'\d', p) else p for p in parts[:3])[:80]


def reserve(account, lane='fresh', endpoint=''):
    """Reserve one actual attempt atomically; return a wait of at most five seconds."""
    if lane not in {'fresh', 'maintenance'}:
        raise ValueError('Request lane must be fresh or maintenance')
    initialize()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        now = store.now()
        c.execute('DELETE FROM ig_request_log WHERE at<=?', (now-86400,))
        until, reason = _gate(c, account, lane, now)
        if until > now:
            raise Deferred(until, reason)
        scopes = (HOST, _scope(account))
        rows = c.execute('SELECT next_slot FROM ig_request_state WHERE scope IN (?,?)', scopes).fetchall()
        slot = max([float(now), *[r['next_slot'] for r in rows]])
        if slot-now > max(GAPS):
            raise Deferred(slot, 'spacing')
        c.execute('INSERT INTO ig_request_log(account,lane,endpoint,at) VALUES (?,?,?,?)',
                  (_account(account), lane, _endpoint(endpoint), now))
        for scope, gap in zip(scopes, GAPS):
            c.execute('''INSERT INTO ig_request_state(scope,next_slot) VALUES (?,?)
              ON CONFLICT(scope) DO UPDATE SET next_slot=excluded.next_slot''', (scope, slot+gap))
    return max(0, slot-now)


def wait(account, lane='fresh', endpoint='', stop=None):
    if stop is not None and stop.is_set():
        raise Deferred(store.now()+60, 'interrupted')
    delay = reserve(account, lane, endpoint)
    if stop is not None:
        if stop.wait(delay):
            raise Deferred(store.now()+60, 'interrupted')
    elif delay:
        time.sleep(delay)
    # Another request may have discovered a block while this slot was waiting.
    with store.db() as c:
        until, reason = _cooldown(c, account, store.now())
    if until > store.now():
        raise Deferred(until, reason)


def _retry_after(response, now):
    value = str(getattr(response, 'headers', {}).get('Retry-After', '')).strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp()-now
        except (ValueError, TypeError, OverflowError):
            return 0
    return max(0, min(seconds, 7*86400))


def note_failure(account, exc):
    """Record explicit throttling or authentication refusal; return its resume time."""
    response = getattr(exc, 'response', None)
    if response is None and hasattr(exc, 'status_code'):
        response = exc
    code = getattr(response, 'status_code', None)
    message = ''
    try:
        body = response.json() if response is not None else {}
        message = str(body.get('message', '')) + ' ' + str(body.get('error_type', ''))
    except (ValueError, AttributeError, TypeError):
        pass
    name = type(exc).__name__
    rate = name in RATE_ERRORS or code == 429 or any(s in message.lower() for s in
              ['feedback_required', 'rate_limit_error', 'sentry_block', 'please wait a few minutes'])
    login = name in LOGIN_ERRORS or code == 401 or any(s in message for s in ['login_required', 'challenge_required'])
    if not (rate or login):
        return None
    initialize()
    until = 0
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        now = store.now()
        for scope in (HOST, _scope(account)) if rate else (_scope(account),):
            row = c.execute('SELECT * FROM ig_request_state WHERE scope=?', (scope,)).fetchone()
            strikes = row['strikes'] if row else 0
            if rate:
                strikes = strikes+1 if row and now-row['last_failure'] < 48*3600 else 1
                duration = max(min(3*3600 * 2**min(strikes-1, 3), 24*3600), _retry_after(response, now))
            else:
                duration = 12*3600
            expiry = max(now+duration, row['cooldown_until'] if row else 0)
            until = max(until, expiry)
            c.execute('''INSERT INTO ig_request_state(scope,cooldown_until,strikes,last_failure,reason)
              VALUES (?,?,?,?,?) ON CONFLICT(scope) DO UPDATE SET cooldown_until=excluded.cooldown_until,
              strikes=excluded.strikes,last_failure=excluded.last_failure,reason=excluded.reason''',
                      (scope, expiry, strikes, now, 'throttled' if rate else 'authentication'))
    return until


def status():
    initialize()
    now = store.now()
    with store.db() as c:
        states = {r['scope']: dict(r) for r in c.execute('SELECT * FROM ig_request_state')}
        accounts = [r[0] for r in c.execute('SELECT DISTINCT account FROM ig_request_log')]
        def metrics(account=None):
            condition, args = (' AND account=?', [account]) if account is not None else ('', [])
            result = {}
            for label, window in [('hour', 3600), ('day', 86400)]:
                row = c.execute("SELECT COUNT(*),COALESCE(SUM(lane='maintenance'),0) FROM ig_request_log WHERE at>?" + condition,
                                [now-window, *args]).fetchone()
                result[label], result['maintenance_' + label] = row[0], row[1]
            state = states.get(HOST if account is None else _scope(account), {})
            result.update(cooldown_until=state.get('cooldown_until', 0), reason=state.get('reason', ''), strikes=state.get('strikes', 0))
            return result
        # Status can be logged/displayed without exposing Instagram account ids.
        accounts += [scope[8:] for scope in states if scope.startswith('account:')]
        return {'host': metrics(), 'accounts': [dict(key=hashlib.sha256(a.encode()).hexdigest()[:12], **metrics(a))
                                                for a in sorted(set(accounts))]}


def authenticated(account):
    """A newly verified login repairs authentication, never a throttle/budget."""
    initialize()
    with store.db() as c:
        c.execute("UPDATE ig_request_state SET cooldown_until=0,reason='' WHERE scope=? AND reason='authentication'",
                  (_scope(account),))
