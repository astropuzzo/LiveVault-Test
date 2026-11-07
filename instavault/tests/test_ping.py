"""A check costs one profile card; feeds are read only when the card shows news."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import HttpUrl

from test_archive import vault
from test_mobile_source import Client, SESSION
from vault import mobile_source as mobile, request_policy as policy, store, sync

CDN = 'https://scontent.cdninstagram.com/v/t51/'


def card(media_count=10, stories=0, photo=CDN + 'same_n.jpg?_nc_ht=signature', private=False):
    return SimpleNamespace(userid=42, username='photographer', full_name='P', biography='', profile_pic_url=photo,
                           media_count=media_count, latest_reel_media=stories, is_private=private,
                           get_posts=None, get_reels=None)


def source_with(remote, reads, access=''):
    remote.get_posts = lambda: reads.append('posts') or iter([])
    remote.get_reels = lambda: reads.append('reels') or iter([])
    class Source:
        context = SimpleNamespace(is_logged_in=True)
        def __init__(self, account): self.account = account
        def profile(self, username): return remote
        def access(self, remote):
            if remote.is_private:
                reads.append('access')
            return access
        def get_stories(self, ids):
            reads.append('stories')
            return iter([])
        def close(self): pass
    return Source


def run(vault, monkeypatch, remote, reads, *, ping=True, access='', **profile):
    job = store.enqueue(vault['profile'], ping=ping)
    monkeypatch.setattr(sync, 'loader', lambda account=None: source_with(remote, reads, access)(account))
    monkeypatch.setattr(sync.push, 'notify', lambda *a, **k: 0)
    worker = sync.Worker()
    monkeypatch.setattr(worker, 'download', lambda *a: (1, 'digest'))
    values = {'avatar': 'existing.jpg', 'tagged': 0, 'highlights': 0} | profile
    with store.db() as c:
        for key, value in values.items():
            c.execute(f'UPDATE profiles SET {key}=? WHERE id=?', (value, vault['profile']))
        row = dict(c.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone())
    worker.run_job(row)
    with store.db() as c:
        return dict(c.execute('SELECT state,message,error FROM jobs WHERE id=?', (job,)).fetchone())


@pytest.fixture(autouse=True)
def no_historical_import(monkeypatch):
    monkeypatch.setattr(sync.Worker, 'backfill_media', lambda *a: False)


def settled(vault, monkeypatch, remote):
    """A first full pass, so the next scheduled check has something to compare."""
    run(vault, monkeypatch, remote, [], ping=False)


def test_a_pydantic_url_survives_serialization():
    assert mobile._plain(HttpUrl('https://scontent.cdninstagram.com/a_n.jpg')) == 'https://scontent.cdninstagram.com/a_n.jpg'


def test_every_card_is_fresh_and_carries_the_story_marker(vault):
    client = Client()
    client.last_json = {'user': {'latest_reel_media': 1794035537}}
    source = mobile.MobileSource({}, SESSION, client=client)
    assert source.profile('photographer').latest_reel_media == 1794035537
    client.last_json = {'user': {'latest_reel_media': 0}}
    assert source.profile('photographer').latest_reel_media == 0
    assert client.calls.count(('profile-v1', 'photographer')) == 2


@pytest.mark.parametrize('url', ['{}', '', 'http://cdninstagram.com/a.jpg', 'https://cdninstagram.com/', None])
def test_an_unusable_photo_address_has_no_key(url):
    assert sync.photo_key(url) == ''


def photo_setup(vault, monkeypatch, body):
    folder = vault['media'] / '1'; folder.mkdir(exist_ok=True)
    (folder / 'avatar-1.jpg').write_bytes(b'current photo')
    with store.db() as c:
        c.execute("UPDATE profiles SET avatar='1/avatar-1.jpg' WHERE id=?", (vault['profile'],))
        c.execute("INSERT INTO profile_history(profile_id,at,avatar,avatar_key) VALUES (?,1,'1/avatar-1.jpg','same_n.jpg')", (vault['profile'],))
        profile = dict(c.execute('SELECT * FROM profiles WHERE id=?', (vault['profile'],)).fetchone())
    worker = sync.Worker(); fetched = []
    def download(url, relative, job_id):
        fetched.append(url)
        if isinstance(body, Exception):
            raise body
        (vault['media'] / relative).write_bytes(body)
        return len(body), hashlib.sha256(body).hexdigest()
    monkeypatch.setattr(worker, 'download', download)
    return worker, profile, fetched


def photo_changes(vault):
    return [c for c in store.profile_history(vault['profile'])['changes'] if c['field'] == 'avatar_key']


def test_the_same_photo_is_neither_downloaded_nor_reported(vault, monkeypatch):
    worker, profile, fetched = photo_setup(vault, monkeypatch, b'unused')
    worker.record_profile(profile, card(), 1)
    worker.record_profile(profile, card(photo=CDN + 'same_n.jpg?_nc_ht=other-signature'), 1)
    assert fetched == [] and photo_changes(vault) == []


def test_a_card_without_a_photo_address_changes_nothing(vault, monkeypatch):
    worker, profile, fetched = photo_setup(vault, monkeypatch, b'unused')
    worker.record_profile(profile, card(photo='{}'), 1)
    assert fetched == [] and photo_changes(vault) == []
    with store.db() as c:
        assert c.execute('SELECT avatar FROM profiles WHERE id=?', (vault['profile'],)).fetchone()[0] == '1/avatar-1.jpg'


def test_identical_bytes_under_a_new_cdn_name_are_not_a_new_photo(vault, monkeypatch):
    worker, profile, fetched = photo_setup(vault, monkeypatch, b'current photo')
    worker.record_profile(profile, card(photo=CDN + 'renamed_n.jpg'), 1)
    worker.record_profile(profile, card(photo=CDN + 'renamed_n.jpg'), 1)
    assert len(fetched) == 1 and photo_changes(vault) == []
    assert sorted(p.name for p in (vault['media'] / '1').iterdir()) == ['avatar-1.jpg']


def test_a_really_new_photo_is_stored_and_reported_once(vault, monkeypatch):
    worker, profile, fetched = photo_setup(vault, monkeypatch, b'new photo')
    worker.record_profile(profile, card(photo=CDN + 'new_n.jpg'), 1)
    with store.db() as c:
        profile = dict(c.execute('SELECT * FROM profiles WHERE id=?', (vault['profile'],)).fetchone())
    worker.record_profile(profile, card(photo=CDN + 'new_n.jpg'), 1)
    assert len(fetched) == 1 and len(photo_changes(vault)) == 1
    assert profile['avatar'] != '1/avatar-1.jpg'


def test_a_failed_photo_download_is_tried_again_without_a_change(vault, monkeypatch):
    worker, profile, fetched = photo_setup(vault, monkeypatch, ValueError('cdn'))
    worker.record_profile(profile, card(photo=CDN + 'new_n.jpg'), 1)
    worker.record_profile(profile, card(photo=CDN + 'new_n.jpg'), 1)
    assert len(fetched) == 2 and photo_changes(vault) == []


NOW = 2_000_000
SEEN = {'account': 'a', 'story': 0, 'story_at': NOW-60, 'media': 10, 'feed_at': NOW-60}


@pytest.mark.parametrize('seen,remote,account,expected', [
    (SEEN, card(), 'a', set()),
    (None, card(), 'a', {'story', 'feed'}),
    (SEEN, card(), 'other-account', {'story', 'feed'}),
    (SEEN, card(media_count=11), 'a', {'feed'}),
    (SEEN, card(media_count=9), 'a', {'feed'}),
    (SEEN, card(stories=NOW-30), 'a', {'story'}),
    (SEEN | {'story': 5}, card(stories=0), 'a', {'story'}),
    (SEEN | {'story': NOW-300}, card(stories=NOW-300), 'a', set()),
    (SEEN | {'story': NOW-300, 'story_at': NOW-3600}, card(stories=NOW-300), 'a', {'story'}),
    (SEEN | {'story_at': NOW-sync.FULL_CHECK_AFTER}, card(), 'a', {'story'}),
    (SEEN | {'feed_at': NOW-sync.FULL_CHECK_AFTER}, card(), 'a', {'feed'}),
    (SEEN, card(stories=None), 'a', {'story'}),
    (SEEN, SimpleNamespace(), 'a', {'story', 'feed'}),
])
def test_what_a_card_justifies_reading(seen, remote, account, expected):
    assert sync.fresh_due(seen, remote, account, NOW) == expected


def test_a_quiet_profile_costs_only_its_card(vault, monkeypatch):
    settled(vault, monkeypatch, card())
    reads = []
    result = run(vault, monkeypatch, card(), reads)
    assert reads == []
    assert (result['state'], result['message']) == ('done', 'Nessuna novità')


def test_a_new_post_reads_the_feeds_and_a_new_story_reads_the_stories(vault, monkeypatch):
    settled(vault, monkeypatch, card())
    reads = []
    run(vault, monkeypatch, card(media_count=11), reads)
    assert reads == ['posts', 'reels']
    reads = []
    run(vault, monkeypatch, card(media_count=11, stories=store.now()), reads)
    assert reads == ['stories']
    reads = []
    run(vault, monkeypatch, card(media_count=11, stories=store.now()-5), reads)
    assert reads == ['stories']


def test_a_failed_read_is_repeated_at_the_next_ping(vault, monkeypatch):
    settled(vault, monkeypatch, card())
    remote = card(media_count=11)
    def broken():
        raise ValueError('page')
    original = sync.Worker.stream
    def stream(self, profile, iterator, kind, *a, **k):
        if kind == 'post' and calls.pop() == 'fail':
            broken()
        return original(self, profile, iterator, kind, *a, **k)
    calls = ['ok', 'fail']
    monkeypatch.setattr(sync.Worker, 'stream', stream)
    run(vault, monkeypatch, remote, [])
    assert sync.read_ping({'id': vault['profile']})['media'] == 10
    with store.db() as c:
        c.execute("DELETE FROM cursors WHERE kind='pause:post'")
    run(vault, monkeypatch, remote, [])
    assert sync.read_ping({'id': vault['profile']})['media'] == 11


def test_the_update_button_always_reads_everything(vault, monkeypatch):
    settled(vault, monkeypatch, card())
    reads = []
    run(vault, monkeypatch, card(), reads, ping=False)
    assert reads == ['stories', 'posts', 'reels']


def test_update_turns_a_waiting_ping_into_a_full_check(vault):
    job = store.enqueue(vault['profile'], ping=True)
    assert store.enqueue(vault['profile'], ping=True) == job
    with store.db() as c:
        assert c.execute('SELECT ping FROM jobs WHERE id=?', (job,)).fetchone()[0] == 1
    assert store.enqueue(vault['profile']) == job
    with store.db() as c:
        assert c.execute('SELECT ping FROM jobs WHERE id=?', (job,)).fetchone()[0] == 0


def test_a_postponed_maintenance_step_does_not_fail_a_quiet_ping(vault, monkeypatch):
    settled(vault, monkeypatch, card())
    with store.db() as c:
        c.execute('DELETE FROM sync_schedule')
    def postponed(*a):
        raise policy.Deferred(store.now()+600, 'budget_maintenance_host_hour')
    monkeypatch.setattr(sync.Worker, 'backfill_media', postponed)
    result = run(vault, monkeypatch, card(), [])
    assert result['state'] == 'done' and not result['error']


def test_the_removal_check_keeps_its_turn_for_a_pass_that_listed_the_posts(vault, monkeypatch):
    settled(vault, monkeypatch, card())
    with store.db() as c:
        c.execute("DELETE FROM sync_schedule WHERE kind='deleted-check'")
    run(vault, monkeypatch, card(), [])
    with store.db() as c:
        assert not c.execute("SELECT 1 FROM sync_schedule WHERE kind='deleted-check'").fetchone()
    run(vault, monkeypatch, card(media_count=11), [])
    with store.db() as c:
        assert c.execute("SELECT 1 FROM sync_schedule WHERE kind='deleted-check'").fetchone()


def test_a_pending_follow_request_is_checked_hourly_and_reads_everything_once_accepted(vault, monkeypatch):
    remote = card(private=True); reads = []
    run(vault, monkeypatch, remote, reads, access='requested')
    assert reads == ['access']
    with store.db() as c:
        assert c.execute('SELECT next_sync FROM profiles').fetchone()[0] >= store.now()+3500
    reads = []
    run(vault, monkeypatch, remote, reads)
    assert reads == ['access', 'stories', 'posts', 'reels']


def test_a_followed_private_profile_needs_no_second_request_at_each_ping(vault, monkeypatch):
    remote = card(private=True)
    run(vault, monkeypatch, remote, [], ping=False)
    reads = []
    run(vault, monkeypatch, remote, reads)
    assert reads == []


def history_row(vault, at, key, avatar=''):
    with store.db() as c:
        c.execute('INSERT INTO profile_history(profile_id,at,avatar,avatar_key) VALUES (?,?,?,?)', (vault['profile'], at, avatar, key))


def test_false_photo_changes_already_logged_disappear(vault):
    for at, key, avatar in [(1, '{}', ''), (2, 'real.jpg', '1/a.jpg'), (3, '{}', ''), (4, 'real.jpg', '1/b.jpg'), (5, '{}', '')]:
        history_row(vault, at, key, avatar)
    store.repair_photo_history()
    store.repair_photo_history()
    history = store.profile_history(vault['profile'])
    assert [c for c in history['changes'] if c['field'] == 'avatar_key'] == []
    assert all(frame['photo'] for frame in history['frames'])
    with store.db() as c:
        assert [r[0] for r in c.execute('SELECT avatar FROM profile_history ORDER BY at')] == ['1/a.jpg', '1/a.jpg', '1/a.jpg', '1/b.jpg', '1/b.jpg']


def test_a_real_photo_change_survives_the_repair(vault):
    for at, key, avatar in [(1, 'old.jpg', '1/a.jpg'), (2, '{}', ''), (3, 'new.jpg', '1/b.jpg'), (4, '{}', '')]:
        history_row(vault, at, key, avatar)
    store.repair_photo_history()
    assert len([c for c in store.profile_history(vault['profile'])['changes'] if c['field'] == 'avatar_key']) == 1


def test_the_repair_also_removes_the_old_cache_table(vault):
    with store.db() as c:
        c.execute('CREATE TABLE ig_profile_cache (account_id TEXT)')
    store.repair_photo_history()
    with store.db() as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='ig_profile_cache'").fetchone()
