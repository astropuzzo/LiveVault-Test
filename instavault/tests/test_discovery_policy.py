"""Network deferrals must preserve discovery, pagination and saved originals."""
import json
from types import SimpleNamespace

import pytest

from test_archive import vault, running_job, image_item, run_with_accounts, source_for
from test_mobile_source import Client, SESSION, item
from vault import mobile_source as mobile, request_policy as policy, store, sync


def test_sdk_swallowed_exceptions_cannot_complete_a_throttled_page(vault, monkeypatch):
    from instagrapi.exceptions import PrivateError
    sent = []
    class Sender(Client):
        def _send_private_request(self, endpoint, **kwargs):
            sent.append(endpoint)
        def user_medias_paginated_v1(self, *args, **kwargs):
            try:
                self._send_private_request('feed/user/456/')
            except PrivateError:
                raise
            except Exception:
                return [], ''  # the SDK's dangerous generic fallback
            return [item()], ''
    client = Sender()
    source = mobile.MobileSource({}, SESSION, client=client)
    monkeypatch.setattr(policy, 'wait', lambda *a: (_ for _ in ()).throw(policy.Deferred(store.now()+100, 'budget')))
    with pytest.raises(policy.Deferred):
        next(source.profile('photographer').get_posts())
    assert sent == []


def test_transport_refusal_blocks_other_accounts_before_the_next_send(vault):
    class ClientThrottledError(Exception): pass
    calls = []
    class Sender(Client):
        def _send_private_request(self, *args, **kwargs):
            calls.append('sent'); raise ClientThrottledError()
    first = mobile.MobileSource({}, SESSION, client=Sender())
    with pytest.raises(ClientThrottledError):
        first.client._send_private_request('media/123/comments/')
    other = mobile.MobileSource({}, dict(SESSION, cookies={'ds_user_id':'999','sessionid':'999:session'}), client=Sender())
    with pytest.raises(policy.Deferred):
        other.client._send_private_request('feed/user/456/')
    assert calls == ['sent']
    assert policy.status()['host']['strikes'] == 1


def test_latest_pass_stops_before_requesting_history_and_preserves_its_cursor(vault, monkeypatch):
    job, profile = running_job(vault)
    client = Client(); source = mobile.MobileSource({}, SESSION, client=client)
    remote = source.profile('photographer')
    client.pages['post',''] = ([item(str(n)) for n in range(12)], 'history')
    client.pages['post','history'] = AssertionError('second page must not be fetched')
    with store.db() as c:
        c.execute("INSERT INTO cursors VALUES (?,'post','history-is-pending',0)", (profile['id'],))
    worker = sync.Worker(); monkeypatch.setattr(worker, 'archive', lambda *a: False)
    assert not worker.stream(profile, remote.get_posts(), 'post', job, latest=True)
    with store.db() as c:
        assert c.execute("SELECT data FROM cursors WHERE kind='post'").fetchone()[0] == 'history-is-pending'
    assert len([call for call in client.calls if call[0] == 'post']) == 1


def test_expired_signed_urls_refresh_the_same_historical_page(vault):
    client = Client(); source = mobile.MobileSource({}, SESSION, client=client)
    remote = source.profile('photographer')
    client.pages['post',''] = ([item('Head')], 'old-page')
    client.pages['post','old-page'] = ([item('Older1'),item('Older2')], 'next-page')
    iterator = remote.get_posts(); next(iterator); next(iterator)
    snapshot = iterator.freeze(); snapshot['created_at'] -= 3*86400
    restored = remote.get_posts(); restored.restore(json.loads(json.dumps(snapshot)))
    assert next(restored).shortcode == 'Older1'
    assert next(restored).shortcode == 'Older2'
    assert client.calls[-1] == ('post','456',12,'old-page')


def test_comments_throttle_is_not_swallowed_as_a_successful_job(vault, monkeypatch):
    class ClientThrottledError(Exception): pass
    worker = sync.Worker()
    worker.source = SimpleNamespace(comments=lambda *_: (_ for _ in ()).throw(ClientThrottledError()))
    with pytest.raises(ClientThrottledError):
        worker.save_comments(1,'A')
    assert worker.comments_blocked


def test_stories_and_fresh_posts_precede_maintenance(vault, monkeypatch):
    events = []
    Source = source_for()
    class Ordered(Source):
        def get_stories(self, ids): events.append('stories'); return iter([])
        def profile(self, username):
            remote = super().profile(username)
            remote.get_posts = lambda: events.append('posts') or iter([])
            return remote
    monkeypatch.setattr(sync.Worker, 'backfill_media', lambda *a: events.append('backfill') or True)
    monkeypatch.setattr(sync.Worker, 'refresh_recent', lambda *a: events.append('comments'))
    _, result, _ = run_with_accounts(vault, monkeypatch, Ordered, stories=1)
    assert result['state'] == 'done'
    assert events[:2] == ['stories','posts']
    assert events.index('posts') < events.index('backfill') < events.index('comments')
    # Secondary pending work must not switch to the old 60-second profile loop.
    with store.db() as c:
        assert c.execute('SELECT next_sync FROM profiles').fetchone()[0] >= store.now()+540
