import hashlib
import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vault import store, sync, server, security


@pytest.fixture
def vault(tmp_path, monkeypatch):
    data, media, secrets = [tmp_path / name for name in ['data', 'media', 'secrets']]
    for p in [data, media, secrets]:
        p.mkdir()
    (media / '.instavault-volume').write_text('instavault-v1')
    (secrets / 'secret').write_bytes(b'x' * 32)
    (secrets / 'auth.json').write_text(json.dumps({'username': 'tester', 'salt': 'salt', 'hash': hashlib.sha256(b'salt:test-password').hexdigest()}))
    monkeypatch.setattr(store, 'DATA', data); monkeypatch.setattr(store, 'MEDIA', media); monkeypatch.setattr(store, 'SECRETS', secrets)
    sync.STOP.clear(); store.initialize()
    with store.db() as c:
        ident = c.execute('INSERT INTO profiles(username,created_at) VALUES (?,?)', ('photographer', store.now())).lastrowid
    yield {'profile': ident, 'data': data, 'media': media}
    sync.STOP.clear()


@pytest.mark.parametrize('name', ['../profile', 'https://example.com/foo', 'https://instagram.com/p/example', 'name?x=1', '.test', 'a' * 31])
def test_username_rejects_paths_and_nonprofiles(name):
    with pytest.raises(ValueError):
        store.username(name)


def test_username_accepts_selected_profile():
    assert store.username('https://www.instagram.com/francesco_francia_photography/') == 'francesco_francia_photography'


def test_queue_is_idempotent_and_recovers_interrupted_job(vault):
    first = store.enqueue(vault['profile'])
    assert store.enqueue(vault['profile']) == first
    with store.db() as c:
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (first,))
    store.initialize()
    with store.db() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (first,)).fetchone()[0] == 'running'
    sync.Worker().recover_abandoned()
    with store.db() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (first,)).fetchone()[0] == 'queued'


def test_a_cancelled_selected_job_is_never_resurrected(vault):
    ident = store.enqueue(vault['profile'])
    with store.db() as c:
        stale = dict(c.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone())
        c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (ident,))
    sync.Worker().run_job(stale)
    with store.db() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (ident,)).fetchone()[0] == 'cancelled'


def insert_post(profile, key, stamp):
    with store.db() as c:
        ident = c.execute('''INSERT INTO posts(profile_id,external_key,shortcode,kind,caption,published_at,archived_at,complete)
            VALUES (?,?,?,'post','100% photograph',?,?,1)''', (profile, key, key, stamp, store.now())).lastrowid
    return ident


def test_keyset_pagination_preserves_ties_without_duplicates(vault):
    for i in range(70):
        insert_post(vault['profile'], str(i), 1000)
    a = store.list_posts({}); b = store.list_posts({'cursor': a['next']})
    assert len(a['items']) == 48 and len(b['items']) == 22 and b['next'] is None
    assert len({p['id'] for p in a['items'] + b['items']}) == 70
    assert len(store.list_posts({'q': '100%'})['items']) == 48
    assert not store.list_posts({'q': '100_'})['items']


def test_storage_guard_never_creates_a_fallback_directory(vault):
    (vault['media'] / '.instavault-volume').unlink()
    assert not store.storage_ready()
    with pytest.raises(sync.Paused):
        sync.Worker().download('https://scontent.cdninstagram.com/example.jpg', '1/image.jpg', 1)
    assert not (vault['media'] / '1').exists()


def test_quota_reserves_free_space(vault):
    with store.db() as c:
        c.execute("UPDATE settings SET value='1000000' WHERE key='min_free_gb'")
    with pytest.raises(sync.StorageFull):
        sync.Worker().capacity()


def test_post_originals_are_atomic_and_deduplicated(vault, monkeypatch):
    ident = store.enqueue(vault['profile'])
    with store.db() as c:
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (ident,))
        profile = dict(c.execute('SELECT * FROM profiles WHERE id=?', (vault['profile'],)).fetchone())
    worker = sync.Worker(); calls = []
    def download(url, relative, job):
        target = store.MEDIA / relative; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'original-media'); calls.append(url)
        return 14, hashlib.sha256(b'original-media').hexdigest()
    monkeypatch.setattr(worker, 'download', download)
    monkeypatch.setattr(worker, 'thumbnail', lambda *_: ('', 1080, 1350))
    item = SimpleNamespace(shortcode='Example1', date_utc=datetime(2026, 1, 1), caption='Original caption', product_type='', typename='GraphImage', is_video=False, url='https://scontent.cdninstagram.com/x.jpg')
    assert worker.archive(profile, item, 'post', ident)
    assert not worker.archive(profile, item, 'post', ident)
    assert len(calls) == 1
    assert store.list_posts({})['items'][0]['caption'] == 'Original caption'
    # A post also present in the reels tab must not duplicate the archive entry.
    assert not worker.archive(profile, item, 'reel', ident)
    assert store.list_posts({})['items'][0]['kind'] == 'reel'


@pytest.fixture
def http(vault):
    host = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=host.serve_forever, daemon=True); thread.start()
    root = f'http://127.0.0.1:{host.server_port}'
    session = requests.Session(); token = security.issue_session()
    session.cookies.set('instavault_session', token)
    session.headers['X-CSRF-Token'] = security.csrf_token(token)
    yield root, session
    session.close(); host.shutdown(); host.server_close(); thread.join()


def test_private_api_and_csrf_are_enforced(http):
    root, client = http
    assert requests.get(root + '/api/summary').status_code == 401
    assert client.get(root + '/instavault/api/me').status_code == 200
    assert requests.post(root + '/api/sync', cookies=client.cookies, json={}).status_code == 403
    assert client.post(root + '/api/sync', json={}).status_code == 200
    assert client.post(root + '/api/sync', json={}, headers={'Origin': 'https://unrelated.example'}).status_code == 403


def test_original_range_requests_and_missing_files(http, vault):
    root, client = http
    ident = insert_post(vault['profile'], 'range', 1000)
    (store.MEDIA / 'original.mp4').write_bytes(bytes(range(100)))
    with store.db() as c:
        media = c.execute("INSERT INTO media(post_id,position,kind,path,size,sha256) VALUES (?,0,'video','original.mp4',100,'test')", (ident,)).lastrowid
    assert requests.get(root + f'/media/{media}').status_code == 401
    response = client.get(root + f'/media/{media}', headers={'Range': 'bytes=10-19'})
    assert response.status_code == 206 and response.content == bytes(range(10, 20))
    assert response.headers['Content-Range'] == 'bytes 10-19/100'
    assert client.get(root + f'/media/{media}', headers={'Range': 'bytes=-4'}).content == bytes(range(96, 100))
    assert client.get(root + f'/media/{media}', headers={'Range': 'bytes=100-'}).status_code == 416
    (store.MEDIA / 'original.mp4').unlink()
    assert client.get(root + f'/media/{media}').status_code == 404
