import importlib
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from vault import request_policy as policy, store


@pytest.fixture
def clock(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    instant = [1_800_000_000.0]
    monkeypatch.setattr(store, 'now', lambda: instant[0])
    policy.initialize()
    return instant


def insert_requests(account, amount, lane='fresh', at=None):
    with store.db() as c:
        c.executemany('INSERT INTO ig_request_log(account,lane,at) VALUES (?,?,?)',
                      [(account, lane, store.now() if at is None else at)] * amount)


def test_reservations_share_host_spacing_and_survive_reload(clock):
    assert policy.reserve('a') == 0
    assert policy.reserve('b') == 3
    importlib.reload(policy)
    with pytest.raises(policy.Deferred) as blocked:
        policy.reserve('c')
    assert blocked.value.reason == 'spacing'
    assert blocked.value.until == clock[0]+6
    clock[0] += 6
    assert policy.reserve('a') == 0


def test_concurrent_reservations_enforce_account_gap(clock):
    barrier = threading.Barrier(2)
    def reserve():
        barrier.wait()
        return policy.reserve('same')
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: reserve(), range(2)))
    assert sorted(results) == [0, 5]
    with store.db() as c:
        assert c.execute('SELECT COUNT(*) FROM ig_request_log').fetchone()[0] == 2


def test_maintenance_budget_preserves_fresh_discovery(clock):
    insert_requests('a', 6, 'maintenance')
    assert policy.ready_at('a', 'fresh') == clock[0]
    with pytest.raises(policy.Deferred) as blocked:
        policy.reserve('a', 'maintenance')
    assert blocked.value.until == clock[0]+3600
    assert blocked.value.reason == 'budget_maintenance_account_hour'
    assert policy.reserve('a', 'fresh') == 0
    assert policy.status()['host']['hour'] == 7


@pytest.mark.parametrize('account,lane,batches,window', [
    ('a', 'fresh', [('a', 90, 'fresh')], 3600),
    ('c', 'fresh', [('a', 90, 'fresh'), ('b', 90, 'fresh')], 3600),
    ('a', 'maintenance', [('a', 72, 'maintenance')], 86400),
    ('c', 'maintenance', [('a', 60, 'maintenance'), ('b', 60, 'maintenance')], 86400),
    ('a', 'fresh', [('a', 1800, 'fresh')], 86400),
    ('c', 'fresh', [('a', 1500, 'fresh'), ('b', 1500, 'fresh')], 86400),
])
def test_hourly_and_daily_limits_charge_all_attempts(clock, account, lane, batches, window):
    for owner, count, category in batches:
        insert_requests(owner, count, category, at=clock[0]-100)
    with pytest.raises(policy.Deferred) as blocked:
        policy.check(account, lane)
    assert blocked.value.until == clock[0]-100+window
    clock[0] = blocked.value.until
    policy.check(account, lane)


def refusal(name='ClientThrottledError', headers=None, status=429):
    response = SimpleNamespace(status_code=status, headers=headers or {}, json=lambda: {})
    return make_error(name, response)


def make_error(name, response=None):
    exc = type(name, (Exception,), {})()
    exc.response = response
    return exc


def test_throttling_blocks_host_rotation_and_exponentially_backs_off(clock):
    first = policy.note_failure('a', refusal())
    assert first == clock[0]+3*3600
    importlib.reload(policy)
    assert policy.ready_at('b') == first
    with pytest.raises(policy.Deferred):
        policy.reserve('b')
    clock[0] = first
    second = policy.note_failure('a', refusal())
    assert second == clock[0]+6*3600
    clock[0] = second
    third = policy.note_failure('a', refusal())
    assert third == clock[0]+12*3600
    clock[0] = third
    assert policy.note_failure('a', refusal()) == clock[0]+24*3600
    clock[0] += 49*3600
    assert policy.note_failure('a', refusal()) == clock[0]+3*3600


@pytest.mark.parametrize('name', ['PleaseWaitFewMinutes', 'TooManyRequestsException', 'RateLimitError', 'FeedbackRequired'])
def test_typed_refusals_without_response_are_persisted(clock, name):
    assert policy.note_failure('a', make_error(name)) == clock[0]+3*3600
    assert policy.ready_at('other') == clock[0]+3*3600


def test_retry_after_is_honored_and_bounded(clock):
    assert policy.note_failure('a', refusal(headers={'Retry-After': str(2*86400)})) == clock[0]+2*86400
    clock[0] += 3*86400
    assert policy.note_failure('a', refusal(headers={'Retry-After': str(99*86400)})) == clock[0]+7*86400


def test_authentication_blocks_only_the_affected_account(clock):
    policy.note_failure('a', make_error('ChallengeRequired'))
    assert policy.ready_at('a') == clock[0]+12*3600
    assert policy.ready_at('b') == clock[0]
    assert policy.note_failure('b', ValueError('ordinary parser error')) is None
    assert policy.status()['host']['cooldown_until'] == 0


def test_wait_is_interruptible_and_rechecks_cooldown(clock):
    stop = threading.Event(); stop.set()
    with pytest.raises(policy.Deferred) as interrupted:
        policy.wait('a', stop=stop)
    assert interrupted.value.reason == 'interrupted'
    assert policy.status()['host']['hour'] == 0
    class BlockWhileWaiting:
        def is_set(self): return False
        def wait(self, delay):
            policy.note_failure('a', make_error('FeedbackRequired'))
            return False
    with pytest.raises(policy.Deferred) as blocked:
        policy.wait('a', stop=BlockWhileWaiting())
    assert blocked.value.reason == 'throttled'
    assert policy.status()['host']['hour'] == 1


def test_a_filled_budget_does_not_cancel_its_own_reserved_attempt(clock):
    insert_requests('a', 89)
    policy.wait('a')
    with pytest.raises(policy.Deferred):
        policy.wait('a')


def test_endpoint_and_status_omit_tokens_and_raw_account_ids(clock):
    policy.reserve('123456', endpoint='https://i.instagram.com/api/v1/media/456/comments/?sessionid=SECRET')
    with store.db() as c:
        assert c.execute('SELECT endpoint FROM ig_request_log').fetchone()[0] == 'media/:id/comments'
    visible = str(policy.status())
    assert 'SECRET' not in visible and '123456' not in visible


def test_old_reservations_are_pruned_without_resetting_cooldown(clock):
    insert_requests('a', 20, at=clock[0]-86401)
    policy.note_failure('a', make_error('ChallengeRequired'))
    policy.reserve('b')
    with store.db() as c:
        assert c.execute('SELECT COUNT(*) FROM ig_request_log').fetchone()[0] == 1
    assert policy.ready_at('a') == clock[0]+12*3600


def test_verified_login_repairs_authentication_without_bypassing_a_throttle(clock):
    policy.note_failure('a', make_error('ChallengeRequired'))
    policy.authenticated('a')
    assert policy.ready_at('a') == clock[0]
    policy.note_failure('a', make_error('FeedbackRequired'))
    policy.authenticated('a')
    assert policy.ready_at('a') == clock[0]+3*3600
