import hashlib
import io
import json
import sys
import threading
import zipfile
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


def unplug(vault):
    (vault['media'] / '.instavault-volume').unlink()


def running_job(vault):
    ident = store.enqueue(vault['profile'])
    with store.db() as c:
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (ident,))
        profile = dict(c.execute('SELECT * FROM profiles WHERE id=?', (vault['profile'],)).fetchone())
    return ident, profile


def image_item(shortcode):
    return SimpleNamespace(shortcode=shortcode, date_utc=datetime(2026, 1, 1), caption='', product_type='',
                           typename='GraphImage', is_video=False, url='https://scontent.cdninstagram.com/x.jpg')


def test_offline_downloads_fill_the_buffer_without_fetching_the_archive_again(vault, monkeypatch):
    job, profile = running_job(vault)
    worker = sync.Worker(); calls = []
    def download(url, relative, job_id):
        worker.check(job_id)
        target = store.root(worker.mode) / relative; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'original-media'); calls.append((worker.mode, relative))
        return 14, hashlib.sha256(b'original-media').hexdigest()
    monkeypatch.setattr(worker, 'download', download)
    monkeypatch.setattr(worker, 'thumbnail', lambda *_: ('', 1080, 1350))
    assert worker.archive(profile, image_item('OnNvme'), 'post', job)
    unplug(vault)
    with pytest.raises(sync.ModeChanged):
        worker.archive(profile, image_item('Buffered'), 'post', job)
    worker.mode = store.mode()
    assert worker.mode == 'buffer'
    # What the NVMe holds counts as archived, so the buffer gets only new posts.
    assert not worker.archive(profile, image_item('OnNvme'), 'post', job)
    assert worker.archive(profile, image_item('Buffered'), 'post', job)
    prefix = str(profile['id'])
    assert calls == [('media', f'{prefix}/OnNvme/1.jpg'), ('buffer', f'{prefix}/Buffered/1.jpg')]
    assert not (vault['media'] / prefix / 'Buffered').exists()
    assert [p['shortcode'] for p in store.list_posts({})['items']] == ['Buffered']


def test_buffer_stops_at_its_limit_and_at_the_emmc_floor(vault, monkeypatch):
    unplug(vault)
    worker = sync.Worker(); worker.mode = 'buffer'
    (store.buffer_root() / 'p').mkdir(parents=True)
    (store.buffer_root() / 'p' / 'a.jpg').write_bytes(b'x' * 4096)
    monkeypatch.setattr(store, 'EMMC_FLOOR', 0)
    worker.capacity()
    monkeypatch.setattr(store, 'BUFFER_LIMIT', store.buffer_usage())
    with pytest.raises(sync.BufferFull):
        worker.capacity()
    monkeypatch.setattr(store, 'BUFFER_LIMIT', 2 * 1024**3); monkeypatch.setattr(store, 'EMMC_FLOOR', 10**18)
    with pytest.raises(sync.BufferFull):
        worker.capacity()
    assert store.storage_status()['buffer_full']


def test_a_full_buffer_puts_the_job_back_in_the_queue(vault, monkeypatch):
    ident = store.enqueue(vault['profile'])
    with store.db() as c:
        job = dict(c.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone())
    def full(account=None):
        raise sync.BufferFull()
    monkeypatch.setattr(sync, 'loader', full)
    worker = sync.Worker(); worker.run_job(job)
    with store.db() as c:
        assert c.execute('SELECT state FROM jobs WHERE id=?', (ident,)).fetchone()[0] == 'queued'
        assert tuple(c.execute('SELECT status,error FROM profiles WHERE id=?', (vault['profile'],)).fetchone()) == ('queued', '')
    assert worker.full


def buffered_media(post, path, content, thumb=''):
    target = store.buffer_root() / path
    target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
    with store.db() as c:
        return c.execute("INSERT INTO media(post_id,position,kind,path,thumb,size,sha256,location) VALUES (?,0,'image',?,?,?,?,'buffer')",
                         (post, path, thumb, len(content), hashlib.sha256(content).hexdigest())).lastrowid


def test_drain_moves_the_buffer_to_the_nvme_and_keeps_both_copies_on_conflict(vault):
    buffer, media = store.buffer_root(), vault['media']
    moved = buffered_media(insert_post(vault['profile'], 'Moved', 1000), 'p/Moved/1.jpg', b'photo', 'p/Moved/1.thumb.jpg')
    (buffer / 'p/Moved/1.thumb.jpg').write_bytes(b'thumb')
    (buffer / 'p/Moved/2.jpg.instavault-part').write_bytes(b'interrupted')
    # A drain interrupted after its copy: the identical NVMe file is reused.
    (media / 'p/Moved').mkdir(parents=True); (media / 'p/Moved/1.jpg').write_bytes(b'photo')
    clash = buffered_media(insert_post(vault['profile'], 'Clash', 1000), 'p/Clash/1.jpg', b'new')
    (media / 'p/Clash').mkdir(parents=True); (media / 'p/Clash/1.jpg').write_bytes(b'old')
    sync.Worker().drain()
    assert (media / 'p/Moved/1.jpg').read_bytes() == b'photo' and (media / 'p/Moved/1.thumb.jpg').read_bytes() == b'thumb'
    assert not (buffer / 'p/Moved').exists()
    assert (media / 'p/Clash/1.jpg').read_bytes() == b'old' and (buffer / 'p/Clash/1.jpg').read_bytes() == b'new'
    assert not list(media.rglob('*.instavault-copy'))
    with store.db() as c:
        assert c.execute('SELECT location FROM media WHERE id=?', (moved,)).fetchone()[0] == 'media'
        assert c.execute('SELECT location FROM media WHERE id=?', (clash,)).fetchone()[0] == 'buffer'


def test_drain_waits_while_the_nvme_is_away(vault):
    buffered = buffered_media(insert_post(vault['profile'], 'Wait', 1000), 'p/Wait/1.jpg', b'photo')
    unplug(vault)
    sync.Worker().drain()
    assert (store.buffer_root() / 'p/Wait/1.jpg').is_file()
    with store.db() as c:
        assert c.execute('SELECT location FROM media WHERE id=?', (buffered,)).fetchone()[0] == 'buffer'


def test_offline_archive_serves_the_buffer_only(http, vault):
    root, client = http
    post = insert_post(vault['profile'], 'Offline', 1000)
    buffered = buffered_media(post, 'p/Offline/1.jpg', b'buffered')
    (vault['media'] / 'archived.jpg').write_bytes(b'nvme')
    with store.db() as c:
        archived = c.execute("INSERT INTO media(post_id,position,kind,path,size,sha256) VALUES (?,1,'image','archived.jpg',4,'t')", (post,)).lastrowid
    assert client.get(root + f'/media/{archived}').content == b'nvme'
    unplug(vault)
    assert client.get(root + f'/media/{buffered}').content == b'buffered'
    assert client.get(root + f'/media/{archived}').status_code == 404
    storage = client.get(root + '/api/summary').json()['storage']
    assert storage['mode'] == 'buffer' and storage['buffer_files'] == 1 and storage['buffer_bytes'] > 0


class FakeLoader:
    def __init__(self):
        self.cookies = None
    def load_session(self, username, cookies):
        self.cookies = dict(cookies)
    def test_login(self):
        return 'owner_account'
    def save_session(self):
        return self.cookies
    def close(self):
        pass


def test_profiles_check_every_ten_minutes_with_a_random_offset(vault):
    with store.db() as c:
        profile = dict(c.execute('SELECT * FROM profiles WHERE id=?', (vault['profile'],)).fetchone())
    assert profile['interval_minutes'] == 10
    delays = {sync.next_check(profile) for _ in range(200)}
    assert min(delays) >= 570 and max(delays) <= 630 and len(delays) > 1


def test_old_archives_move_from_hours_to_ten_minutes(vault):
    with store.db() as c:
        c.execute('ALTER TABLE profiles DROP COLUMN interval_minutes')
        c.execute('ALTER TABLE profiles ADD COLUMN interval_hours INTEGER NOT NULL DEFAULT 6')
    store.initialize()
    with store.db() as c:
        assert c.execute('SELECT interval_minutes FROM profiles WHERE id=?', (vault['profile'],)).fetchone()[0] == 10


def test_interval_below_ten_minutes_is_refused(http, vault):
    root, client = http
    assert client.post(root + f'/api/profiles/{vault["profile"]}', json={'interval_minutes': 5}).status_code == 400
    assert client.post(root + f'/api/profiles/{vault["profile"]}', json={'interval_minutes': 30}).status_code == 200


from vault import login
from instagrapi.exceptions import ChallengeRequired
from instagrapi.mixins.challenge import ChallengeChoice


class FakeClient:
    """Calls the code handler the way instagrapi does during a checked login."""
    def __init__(self):
        self.challenge_code_handler = None
        self.password = None
        self.last_json = {'step_name': 'verify_email'}
        self.authorization_data = {}
        self.cookie_dict = {}
        self.sessionid = None
    def set_settings(self, settings):
        self.saved = settings
    def get_settings(self):
        return {'uuids': {'uuid': 'same-phone'}, 'cookies': {'mid': 'm', 'sessionid': self.sessionid or ''},
                'authorization_data': self.authorization_data}
    def login(self, username, password, verification_code=''):
        self.username = username
        self.password = password
        code = self.challenge_code_handler(username, ChallengeChoice.EMAIL)
        if not code:
            raise ChallengeRequired('Challenge code required. Provide it via challenge_code_handler')
        assert code == '123456'
        self.sessionid = '42%3AAbCdEfGhIjKlMnOpQrStUv%3A17%3AAYfZ'
        self.cookie_dict = {'csrftoken': 'real-csrf', 'ds_user_id': '42'}
        self.authorization_data = {'ds_user_id': '42', 'sessionid': self.sessionid}
        return True


@pytest.fixture
def instagram(vault, monkeypatch):
    fake = FakeLoader()
    monkeypatch.setattr(sync, 'loader', lambda account=None: fake)
    monkeypatch.setattr(login, 'Client', FakeClient)
    monkeypatch.setattr(login, 'CURRENT', None)
    monkeypatch.setattr(login, 'ATTEMPTS', __import__('collections').deque())
    return fake


def wait_login(result, wanted=None):
    import time
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = login.password_status(result['attempt_id'])
        if result['state'] == wanted or (wanted is None and result['state'] != 'running'):
            return result
        time.sleep(0.01)
    pytest.fail('Login test did not complete')


def test_an_emailed_code_completes_the_login_from_the_phone(instagram):
    first = wait_login(login.password_login({'username': 'owner_account', 'password': 'secret-pass'}), 'code_required')
    assert first['channel'] == 'email'
    result = wait_login(login.password_login({'code': '123456', 'attempt_id': first['attempt_id']}), 'done')
    assert result['configured'] and result['username'] == 'owner_account'
    account = sync.accounts()[0]
    saved = [security.decrypt_json((store.DATA / account[name]).read_bytes()) for name in ['session_file', 'device_file']]
    assert 'secret-pass' not in json.dumps(saved)
    assert saved[0]['cookies']['ds_user_id'] == '42' and saved[0]['verified_via'] == 'mobile_login'
    assert saved[1]['uuids'] == {'uuid': 'same-phone'} and not saved[1]['authorization_data']
    assert 'sessionid' not in saved[1]['cookies']


def test_a_wrong_code_ends_the_attempt_with_a_clear_message(instagram):
    first = wait_login(login.password_login({'username': 'owner_account', 'password': 'secret-pass'}), 'code_required')
    result = wait_login(login.password_login({'code': '999999', 'attempt_id': first['attempt_id']}), 'error')
    assert 'non ha accettato il codice' in result['error']
    with pytest.raises(ValueError, match='scaduto'):
        login.password_login({'code': '123456', 'attempt_id': first['attempt_id']})


def test_a_code_that_never_arrives_does_not_hang_the_login(instagram, monkeypatch):
    monkeypatch.setattr(login, 'CODE_WAIT', 0.2)
    first = login.password_login({'username': 'owner_account', 'password': 'secret-pass'})
    assert login.CURRENT.done.wait(5)
    assert 'non è arrivato in tempo' in login.password_status(first['attempt_id'])['error']


def test_password_request_returns_while_instagram_is_waiting(instagram):
    import time
    start = time.monotonic()
    first = login.password_login({'username': 'owner_account', 'password': 'secret-pass'})
    assert time.monotonic() - start < 1
    wait_login(first, 'code_required')
    login.cancel(first['attempt_id'])
    assert login.CURRENT.done.wait(5)
    assert not (store.DATA / 'instagram.enc').exists()


def test_a_stale_attempt_cannot_submit_a_code(instagram):
    first = wait_login(login.password_login({'username': 'owner_account', 'password': 'secret-pass'}), 'code_required')
    with pytest.raises(ValueError, match='scaduto'):
        login.password_login({'code': '123456', 'attempt_id': 'other-attempt'})
    login.cancel(first['attempt_id'])
    assert login.CURRENT.done.wait(5)


def test_login_http_request_is_nonblocking_and_status_is_private(http, instagram):
    import time
    root, client = http
    start = time.monotonic()
    response = client.post(root + '/api/instagram/login', json={'username': 'owner_account', 'password': 'secret-pass'})
    assert response.status_code == 200 and time.monotonic() - start < 1
    result = wait_login(response.json(), 'code_required')
    status_url = root + '/api/instagram/login/status?attempt_id=' + result['attempt_id']
    assert requests.get(status_url).status_code == 401
    assert client.get(status_url).json()['channel'] == 'email'
    assert requests.post(root + '/api/instagram/login/cancel', cookies=client.cookies, json={'attempt_id':result['attempt_id']}).status_code == 403
    assert client.post(root + '/api/instagram/login/cancel', json={'attempt_id':result['attempt_id']}).status_code == 200
    assert login.CURRENT.done.wait(5)


def test_mobile_totp_completes_without_legacy_graphql(instagram, monkeypatch):
    from instagrapi.exceptions import TwoFactorRequired
    class TotpClient(FakeClient):
        def login(self, username, password, verification_code=''):
            self.username = username
            if not verification_code:
                raise TwoFactorRequired('Verification code required')
            assert verification_code == '123456'
            self.sessionid = '42%3AAbCdEfGhIjKlMnOpQrStUv%3A17%3AAYfZ'
            self.cookie_dict = {'csrftoken':'real-csrf','ds_user_id':'42'}
            self.authorization_data = {'ds_user_id':'42','sessionid':self.sessionid}
            return True
    monkeypatch.setattr(login, 'Client', TotpClient)
    first = wait_login(login.password_login({'username':'owner_account','password':'secret-pass'}), 'code_required')
    assert first['channel'] == 'two_factor'
    result = wait_login(login.password_login({'attempt_id':first['attempt_id'],'code':'123456'}), 'done')
    assert result['configured']


def test_mobile_header_only_login_obtains_real_web_csrf(instagram, monkeypatch):
    from vault import instagram_session
    class HeaderOnlyClient(FakeClient):
        def login(self, username, password, verification_code=''):
            self.username = username
            self.sessionid = '42%3AAbCdEfGhIjKlMnOpQrStUv%3A17%3AAYfZ'
            self.cookie_dict = {}
            self.authorization_data = {'ds_user_id':'42','sessionid':self.sessionid}
            return True
    called = []
    def web_check(cookies):
        called.append(dict(cookies))
        return 'owner_account', dict(cookies, csrftoken='real-web-csrf')
    monkeypatch.setattr(login, 'Client', HeaderOnlyClient)
    monkeypatch.setattr(instagram_session, 'validate_web_session', web_check)
    result = wait_login(login.password_login({'username':'owner_account','password':'secret-pass'}), 'done')
    assert result['configured'] and len(called)==1 and 'csrftoken' not in called[0]
    saved = security.decrypt_json((store.DATA/sync.accounts()[0]['session_file']).read_bytes())
    assert saved['cookies']['csrftoken']=='real-web-csrf'


def test_cancel_during_session_commit_reports_connection_accurately(instagram, monkeypatch):
    first = wait_login(login.password_login({'username':'owner_account','password':'secret-pass'}), 'code_required')
    entered, release = threading.Event(), threading.Event()
    original = sync.save_session
    def delayed_save(*args, **kwargs):
        entered.set(); assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(sync, 'save_session', delayed_save)
    login.password_login({'attempt_id':first['attempt_id'],'code':'123456'})
    assert entered.wait(5)
    result = []
    cancellation = threading.Thread(target=lambda: result.append(login.cancel(first['attempt_id'])))
    cancellation.start(); release.set(); cancellation.join(5)
    assert result == [{'ok':True,'already_connected':True}]
    assert wait_login(first,'done')['configured']


def test_mobile_login_loader_never_uses_anonymous_instaloader(vault, monkeypatch):
    from vault import mobile_source
    saved={'username':'owner_account','cookies':{'sessionid':'42%3AAbCdEfGhIjKlMnOpQrStUv%3A17%3AAYfZ','csrftoken':'real-csrf','ds_user_id':'42'},'verified_via':'mobile_login'}
    (store.DATA/'instagram.enc').write_bytes(security.encrypt_json(saved))
    (store.DATA/'instagram-device.enc').write_bytes(security.encrypt_json({'uuids':{'uuid':'stable'}}))
    seen=[]
    marker=SimpleNamespace()
    monkeypatch.setattr(mobile_source,'MobileSource',lambda settings,session: (seen.append((settings,session)) or marker))
    assert sync.loader() is marker
    assert seen[0][0]['uuids']['uuid']=='stable' and seen[0][1]['verified_via']=='mobile_login'


def test_an_unavailable_story_category_does_not_block_posts(vault, monkeypatch):
    import contextlib
    with store.db() as c:
        c.execute('UPDATE profiles SET reels=0 WHERE id=?',(vault['profile'],))
    job=store.enqueue(vault['profile'])
    profile=SimpleNamespace(full_name='Photographer',biography='',userid=42,profile_pic_url='https://cdninstagram.com/avatar.jpg',get_posts=lambda:iter([]))
    class Source:
        context=SimpleNamespace(is_logged_in=True)
        def profile(self,username):return profile
        def get_stories(self,ids):raise RuntimeError('story endpoint unavailable')
        def close(self):pass
    monkeypatch.setattr(sync,'loader',lambda account=None:Source())
    worker=sync.Worker()
    monkeypatch.setattr(worker,'download',lambda *a:(1,'digest'))
    with store.db() as c:
        c.execute("UPDATE profiles SET avatar='existing.jpg' WHERE id=?",(vault['profile'],))
        row=dict(c.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone())
    worker.run_job(row)
    with store.db() as c:
        result=c.execute('SELECT state,message FROM jobs WHERE id=?',(job,)).fetchone()
        assert result['state']=='done' and 'Storie' in result['message']
        assert c.execute("SELECT data FROM cursors WHERE kind='pause:story'").fetchone()


def typed_post(profile, key, kind, collection=''):
    with store.db() as c:
        return c.execute('''INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete,collection)
            VALUES (?,?,?,?,1000,?,1,?)''', (profile, key, key, kind, store.now(), collection)).lastrowid


def test_each_kind_has_its_own_gallery_and_highlights_group_by_collection(vault):
    profile = vault['profile']
    for key, kind, collection in [('p1', 'post', ''), ('r1', 'reel', ''), ('s1', 'story', ''),
                                  ('h1', 'story', 'In evidenza · Mare'), ('h2', 'story', 'In evidenza · Mare'),
                                  ('h3', 'story', 'In evidenza · Monti')]:
        typed_post(profile, key, kind, collection)
    shown = lambda **q: sorted(p['shortcode'] for p in store.list_posts({'profile': profile, **q})['items'])
    assert shown(kind='post') == ['p1'] and shown(kind='reel') == ['r1'] and shown(kind='story') == ['s1']
    assert shown(kind='highlight') == ['h1', 'h2', 'h3']
    assert shown(kind='highlight', collection='In evidenza · Mare') == ['h1', 'h2']
    assert {c['name']: c['count'] for c in store.collections(profile)} == {'In evidenza · Mare': 2, 'In evidenza · Monti': 1}
    row = store.profile_rows()[0]
    assert (row['count_post'], row['count_reel'], row['count_story'], row['count_highlight']) == (1, 1, 1, 3)


def test_a_private_profile_says_whether_a_follow_request_waits():
    viewer = SimpleNamespace(context=SimpleNamespace(user_id='1'))
    public = SimpleNamespace(is_private=False, userid='2')
    waiting = SimpleNamespace(is_private=True, followed_by_viewer=False, requested_by_viewer=True, userid='3')
    unknown = SimpleNamespace(is_private=True, followed_by_viewer=False, requested_by_viewer=False, userid='4')
    followed = SimpleNamespace(is_private=True, followed_by_viewer=True, userid='5')
    assert [sync.access_of(viewer, p) for p in [public, waiting, unknown, followed]] == ['', 'requested', 'not_followed', '']


def test_instagram_errors_are_named_instead_of_a_generic_failure():
    class UnknownError(Exception): pass
    class FeedbackRequired(Exception): pass
    class SomethingNew(Exception): pass
    assert 'blocco temporaneo' in sync.error_message(UnknownError())
    assert '«Please wait a few minutes»' in sync.error_message(UnknownError('Please wait a few minutes'))
    assert 'qualche ora' in sync.error_message(FeedbackRequired())
    assert 'SomethingNew' in sync.error_message(SomethingNew())


def test_a_collab_is_stored_once_and_listed_under_both_profiles(vault, monkeypatch):
    job, profile = running_job(vault)
    with store.db() as c:
        other = dict(c.execute('SELECT * FROM profiles WHERE id=?', (c.execute(
            'INSERT INTO profiles(username,created_at) VALUES (?,?)', ('friend', store.now())).lastrowid,)).fetchone())
    worker = sync.Worker(); calls = []
    def download(url, relative, job_id):
        target = store.MEDIA / relative; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'collab'); calls.append(relative)
        return 6, hashlib.sha256(b'collab').hexdigest()
    monkeypatch.setattr(worker, 'download', download)
    monkeypatch.setattr(worker, 'thumbnail', lambda *_: ('', 1080, 1350))
    assert worker.archive(profile, image_item('Collab1'), 'post', job)
    assert not worker.archive(other, image_item('Collab1'), 'post', job)
    assert len(calls) == 1
    mine = store.list_posts({'profile': profile['id']})['items']
    theirs = store.list_posts({'profile': other['id']})['items']
    assert [p['id'] for p in mine] == [p['id'] for p in theirs]
    assert mine[0]['profiles'] == ['photographer', 'friend']
    assert len(store.list_posts({})['items']) == 1
    counts = {r['username']: r['count_post'] for r in store.profile_rows()}
    assert counts == {'photographer': 1, 'friend': 1}


def test_old_duplicate_collabs_merge_into_one_shared_post(vault):
    with store.db() as c:
        other = c.execute('INSERT INTO profiles(username,created_at) VALUES (?,?)', ('friend', store.now())).lastrowid
        for owner in [vault['profile'], other]:
            post = c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete) VALUES (?,?,?,?,1,1,1)",
                             (owner, 'post:Dup', 'Dup', 'post')).lastrowid
            c.execute("INSERT INTO media(post_id,position,kind,path,size,sha256) VALUES (?,0,'image',?,1,'x')", (post, f'{owner}/Dup/1.jpg'))
    store.initialize()
    assert len(store.list_posts({})['items']) == 1
    assert store.list_posts({'profile': other})['items'][0]['profiles'] == ['photographer', 'friend']


def test_a_profile_downloads_as_one_zip_with_highlights_in_their_folder(http, vault):
    root, client = http
    with store.db() as c:
        for code, kind, collection in [('P1', 'post', ''), ('H1', 'story', 'In evidenza · Mare'), ('Gone', 'post', '')]:
            post = c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete,collection) VALUES (?,?,?,?,1767225600,1,1,?)",
                             (vault['profile'], 'post:' + code, code, kind, collection)).lastrowid
            c.execute("INSERT INTO media(post_id,position,kind,path,size,sha256) VALUES (?,0,'image',?,4,'x')", (post, f'{vault["profile"]}/{code}/1.jpg'))
    for code in ['P1', 'H1']:
        target = vault['media'] / str(vault['profile']) / code / '1.jpg'
        target.parent.mkdir(parents=True); target.write_bytes(b'jpeg')
    response = client.get(f'{root}/api/profiles/{vault["profile"]}/download')
    assert response.status_code == 200 and response.headers['Content-Type'] == 'application/zip'
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert sorted(archive.namelist()) == ['in evidenza/Mare/2026-01-01_H1_1.jpg', 'mancanti.txt', 'post/2026-01-01_P1_1.jpg']
    assert archive.read('post/2026-01-01_P1_1.jpg') == b'jpeg'
    only = client.get(f'{root}/api/profiles/{vault["profile"]}/download', params={'collection': 'In evidenza · Mare'})
    assert zipfile.ZipFile(io.BytesIO(only.content)).namelist() == ['in evidenza/Mare/2026-01-01_H1_1.jpg']


def test_comments_are_saved_and_name_authors_already_archived(vault, monkeypatch):
    job, profile = running_job(vault)
    with store.db() as c:
        c.execute('INSERT INTO profiles(username,created_at) VALUES (?,?)', ('friend', store.now()))
    worker = sync.Worker()
    monkeypatch.setattr(worker, 'download', lambda url, relative, job_id: (target := store.MEDIA / relative, target.parent.mkdir(parents=True, exist_ok=True), target.write_bytes(b'x'), (1, hashlib.sha256(b'x').hexdigest()))[-1])
    monkeypatch.setattr(worker, 'thumbnail', lambda *_: ('', 1080, 1350))
    worker.source = SimpleNamespace(comments=lambda code: [
        {'id': '1', 'username': 'friend', 'full_name': 'Friend', 'text': 'Bellissima', 'created_at': 5, 'likes': 2},
        {'id': '2', 'username': 'stranger', 'full_name': '', 'text': 'Dove?', 'created_at': 6, 'likes': 0}])
    assert worker.archive(profile, image_item('WithComments'), 'post', job)
    # Capturing media must not spend one comments request for every new post.
    with store.db() as c:
        assert c.execute('SELECT COUNT(*) FROM comments').fetchone()[0] == 0
    worker.backfill_comments(profile)
    with store.db() as c:
        row = c.execute("SELECT * FROM posts WHERE shortcode='WithComments'").fetchone()
        detail = store.post_json(c, row, True)
    assert [(k['username'], k['text'], bool(k['profile_id'])) for k in detail['comments']] == [('friend', 'Bellissima', True), ('stranger', 'Dove?', False)]
    assert row['comments_at']


def test_a_comment_refusal_never_stops_the_download(vault, monkeypatch):
    job, profile = running_job(vault)
    worker = sync.Worker()
    monkeypatch.setattr(worker, 'download', lambda url, relative, job_id: (target := store.MEDIA / relative, target.parent.mkdir(parents=True, exist_ok=True), target.write_bytes(b'x'), (1, hashlib.sha256(b'x').hexdigest()))[-1])
    monkeypatch.setattr(worker, 'thumbnail', lambda *_: ('', 1080, 1350))
    def refuse(code):
        raise RuntimeError('Please wait a few minutes')
    worker.source = SimpleNamespace(comments=refuse)
    assert worker.archive(profile, image_item('NoComments'), 'post', job)
    worker.backfill_comments(profile)
    assert worker.comments_blocked
    assert store.list_posts({})['items'][0]['shortcode'] == 'NoComments'


def test_only_recent_posts_missing_from_instagram_are_marked_deleted_and_can_come_back(vault):
    profile = vault['profile']
    for code, stamp in [('Old', 100), ('Kept', 300), ('Gone', 400), ('New', 500)]:
        with store.db() as c:
            c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete) VALUES (?,?,?,'post',?,1,1)",
                      (profile, 'post:' + code, code, stamp))
    # Instagram listed New and Kept, down to Kept's date: Gone vanished, Old is outside the window.
    assert store.mark_deleted(profile, ('post', 'reel'), {'New', 'Kept'}, 300) == 1
    assert [p['shortcode'] for p in store.list_posts({'deleted': '1'})['items']] == ['Gone']
    store.mark_deleted(profile, ('post', 'reel'), {'New', 'Kept', 'Gone'}, 300)
    assert store.list_posts({'deleted': '1'})['items'] == []


def test_a_story_removed_within_its_day_is_marked_deleted(vault):
    profile = vault['profile']
    with store.db() as c:
        for code, age in [('Live', 600), ('Removed', 1200), ('Expired', 90000)]:
            c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete) VALUES (?,?,?,'story',?,1,1)",
                      (profile, 'story:' + code, code, store.now() - age))
    assert store.mark_deleted(profile, (), {'Live'}, store.now() - 86400 + 600, story=True) == 1
    assert [p['shortcode'] for p in store.list_posts({'deleted': '1'})['items']] == ['Removed']


def test_activity_lists_what_each_update_downloaded(http, vault):
    root, client = http
    job = store.enqueue(vault['profile'])
    with store.db() as c:
        c.execute("UPDATE jobs SET state='done',downloaded=1 WHERE id=?", (job,))
        post = c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete,job_id) VALUES (?,?,?,'post',1,1,1,?)",
                         (vault['profile'], 'post:J1', 'J1', job)).lastrowid
        media = c.execute("INSERT INTO media(post_id,position,kind,path,size,sha256) VALUES (?,0,'image','x.jpg',1,'x')", (post,)).lastrowid
    item = client.get(root + '/api/jobs').json()['items'][0]
    assert item['items'] == [{'id': post, 'kind': 'post', 'collection': '', 'media': media}]


def test_chosen_posts_download_as_one_zip_grouped_by_profile(http, vault):
    root, client = http
    ids = []
    with store.db() as c:
        for code in ['S1', 'S2', 'S3']:
            post = c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete) VALUES (?,?,?,'post',1767225600,1,1)",
                             (vault['profile'], 'post:' + code, code)).lastrowid
            c.execute("INSERT INTO media(post_id,position,kind,path,size,sha256) VALUES (?,0,'image',?,4,'x')", (post, f'{vault["profile"]}/{code}/1.jpg'))
            target = vault['media'] / str(vault['profile']) / code / '1.jpg'
            target.parent.mkdir(parents=True); target.write_bytes(b'jpeg'); ids.append(post)
    response = client.get(f'{root}/api/download', params={'posts': f'{ids[0]},{ids[2]},x'})
    assert response.headers['Content-Disposition'] == 'attachment; filename="instavault-2-contenuti.zip"'
    assert sorted(zipfile.ZipFile(io.BytesIO(response.content)).namelist()) == ['photographer/post/2026-01-01_S1_1.jpg', 'photographer/post/2026-01-01_S3_1.jpg']
    assert client.get(f'{root}/api/download', params={'posts': ''}).status_code == 404


def add_post(vault, profile, code):
    with store.db() as c:
        post = c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete) VALUES (?,?,?,'post',1,1,1)",
                         (profile, 'post:' + code, code)).lastrowid
        c.execute("INSERT INTO media(post_id,position,kind,path,thumb,size,sha256) VALUES (?,0,'image',?,?,4,'x')",
                  (post, f'{profile}/{code}/1.jpg', f'{profile}/{code}/1.thumb.jpg'))
    folder = vault['media'] / str(profile) / code; folder.mkdir(parents=True)
    (folder / '1.jpg').write_bytes(b'jpeg'); (folder / '1.thumb.jpg').write_bytes(b'jpeg')
    return post


def test_removing_a_profile_keeps_its_archive_and_adding_it_again_resumes(http, vault):
    root, client = http
    add_post(vault, vault['profile'], 'Kept')
    assert client.post(f'{root}/api/profiles/{vault["profile"]}/remove', json={'delete_data': False}).status_code == 200
    assert store.profile_rows() == [] and store.list_posts({})['items'] == []
    assert (vault['media'] / str(vault['profile']) / 'Kept' / '1.jpg').exists()
    again = client.post(f'{root}/api/profiles', json={'username': 'photographer'})
    assert again.json()['id'] == vault['profile']
    assert [p['shortcode'] for p in store.list_posts({})['items']] == ['Kept']


def test_deleting_a_profile_removes_its_files_but_hands_collabs_over(http, vault):
    root, client = http
    with store.db() as c:
        friend = c.execute('INSERT INTO profiles(username,created_at) VALUES (?,?)', ('friend', store.now())).lastrowid
    add_post(vault, vault['profile'], 'Mine')
    collab = add_post(vault, vault['profile'], 'Collab')
    with store.db() as c:
        c.execute('INSERT INTO shared VALUES (?,?)', (collab, friend))
    assert client.post(f'{root}/api/profiles/{vault["profile"]}/remove', json={'delete_data': True}).status_code == 200
    assert not (vault['media'] / str(vault['profile']) / 'Mine').exists()
    assert (vault['media'] / str(vault['profile']) / 'Collab' / '1.jpg').exists()
    assert [(p['shortcode'], p['username']) for p in store.list_posts({})['items']] == [('Collab', 'friend')]
    assert [p['username'] for p in store.profile_rows()] == ['friend']


def test_an_account_problem_is_remembered_until_a_good_check_or_new_login(vault, monkeypatch):
    monkeypatch.setattr(sync.push, 'notify', lambda *a, **k: 0)
    add_account('11', 'first_account')
    class ChallengeRequired(Exception): pass
    assert sync.note_account_problem(ChallengeRequired(), '11') == 'login'
    status = sync.session_status()
    assert status['problem']['kind'] == 'login' and not status['accounts'][0]['usable']
    class SomethingElse(Exception): pass
    assert sync.note_account_problem(SomethingElse(), '11') is None
    sync.clear_account_problem('11')
    assert sync.session_status()['problem'] is None


def test_search_finds_comments_mentions_and_a_month(vault):
    profile = vault['profile']
    with store.db() as c:
        for code, caption, stamp in [('A', 'Tramonto #mare con @virginia', 1767225600), ('B', 'Montagna', 1769904000)]:
            post = c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,caption,published_at,archived_at,complete) VALUES (?,?,?,'post',?,?,1,1)",
                             (profile, 'post:' + code, code, caption, stamp)).lastrowid
        c.execute("INSERT INTO comments(post_id,external_id,username,text,created_at) VALUES (?,?,?,?,1)", (post, '1', 'gfd_photo', 'Che neve'))
    codes = lambda **q: [p['shortcode'] for p in store.list_posts(q)['items']]
    assert codes(q='#mare') == ['A'] and codes(q='@virginia') == ['A']
    assert codes(q='neve') == ['B'] and codes(q='@gfd_photo') == ['B']
    assert codes(**{'from': '1769904000', 'to': '1772323200'}) == ['B']


def test_a_tagged_post_keeps_its_own_key_and_kind(vault, monkeypatch):
    job, profile = running_job(vault)
    worker = sync.Worker()
    monkeypatch.setattr(worker, 'download', lambda url, relative, job_id: (target := store.MEDIA / relative, target.parent.mkdir(parents=True, exist_ok=True), target.write_bytes(b'x'), (1, hashlib.sha256(b'x').hexdigest()))[-1])
    monkeypatch.setattr(worker, 'thumbnail', lambda *_: ('', 1080, 1350))
    item = image_item('Tag1'); item.product_type = 'clips'; item.owner_username = 'someone'
    assert worker.archive(profile, item, 'tagged', job)
    row = store.list_posts({'profile': profile['id'], 'kind': 'tagged'})['items'][0]
    assert (row['external_key'], row['kind'], row['owner']) == ('tagged:Tag1', 'tagged', 'someone')
    assert store.list_posts({'kind': 'feed'})['items'] == []


def test_profile_history_keeps_a_row_a_day_and_every_change(vault, monkeypatch):
    profile = vault['profile']
    base = {'followers': 100, 'following': 5, 'media_count': 10, 'full_name': 'Ana', 'biography': 'Foto', 'avatar_key': 'a.jpg', 'avatar': '1/avatar-1.jpg'}
    clock = [1767225600]
    monkeypatch.setattr(store, 'now', lambda: clock[0])
    assert store.record_history(profile, base)
    clock[0] += 600; store.record_history(profile, base | {'followers': 101})
    clock[0] += 600; store.record_history(profile, base | {'biography': 'Foto e viaggi'})
    clock[0] += 86400; assert store.record_history(profile, base | {'biography': 'Foto e viaggi', 'avatar_key': 'b.jpg', 'avatar': '1/avatar-2.jpg', 'followers': 120})
    history = store.profile_history(profile)
    assert [p['followers'] for p in history['points']] == [101, 100, 120]
    assert [(c['label'], c['before'], c['after']) for c in history['changes']] == [('Foto profilo', '', ''), ('Bio', 'Foto', 'Foto e viaggi')]
    assert history['changes'][0]['before_photo'] and history['changes'][0]['after_photo']


def test_follower_count_stays_current_during_the_day(vault, monkeypatch):
    profile = vault['profile']
    base = {'followers': 100, 'following': 5, 'media_count': 10, 'full_name': 'Ana', 'biography': 'Foto', 'avatar_key': 'a.jpg', 'avatar': ''}
    clock = [1767225600]
    monkeypatch.setattr(store, 'now', lambda: clock[0])
    store.record_history(profile, base)
    clock[0] += 3600; store.record_history(profile, base | {'followers': 104, 'media_count': 11})
    clock[0] += 3600; store.record_history(profile, base | {'followers': None, 'following': None, 'media_count': None})
    points = store.profile_history(profile)['points']
    assert [(p['followers'], p['following'], p['media_count']) for p in points] == [(104, 5, 11)]
    assert points[0]['at'] == 1767225600


def test_push_text_is_encrypted_for_the_browser_only(vault):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from vault import push
    browser = ec.generate_private_key(ec.SECP256R1()); auth = b'a' * 16
    public = browser.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    record = push.encrypt(push.b64(public), push.b64(auth), b'{"title":"x"}')
    # Decrypt as the browser does (RFC 8291).
    salt, local = record[:16], record[21:86]
    shared = browser.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), local))
    key = HKDF(hashes.SHA256(), 32, auth, b'WebPush: info\x00' + public + local).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b'Content-Encoding: aes128gcm\x00').derive(key)
    nonce = HKDF(hashes.SHA256(), 12, salt, b'Content-Encoding: nonce\x00').derive(key)
    assert AESGCM(cek).decrypt(nonce, record[86:], None) == b'{"title":"x"}\x02'
    assert push.vapid_header('https://fcm.googleapis.com/x').startswith('vapid t=')


def test_push_subscriptions_are_checked_and_gone_ones_removed(http, vault, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from vault import push
    root, client = http
    assert len(push.unb64(client.get(root + '/api/push/key').json()['key'])) == 65
    assert client.post(root + '/api/push/subscribe', json={'endpoint': 'http://x', 'keys': {}}).status_code == 400
    public = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    subscription = {'endpoint': 'https://push.example/1', 'keys': {'p256dh': push.b64(public), 'auth': push.b64(b'b' * 16)}}
    assert client.post(root + '/api/push/subscribe', json=subscription).ok
    sent = []
    monkeypatch.setattr(push.requests, 'post', lambda url, **kw: sent.append(kw['headers']) or SimpleNamespace(status_code=410, ok=False))
    assert client.post(root + '/api/push/test', json={}).status_code == 400
    assert sent[0]['Content-Encoding'] == 'aes128gcm'
    with store.db() as c:
        assert c.execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0] == 0


def test_notices_cover_new_content_of_chosen_profiles_and_every_deletion(vault, monkeypatch):
    notices = []
    monkeypatch.setattr(sync.push, 'notify', lambda title, body, url='./': notices.append((title, body, url)))
    job = store.enqueue(vault['profile'])
    with store.db() as c:
        for code, kind, collection in [('S1', 'story', ''), ('S2', 'story', ''), ('H1', 'story', 'In evidenza · x'), ('P1', 'post', '')]:
            c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,collection,published_at,archived_at,complete,job_id) VALUES (?,?,?,?,?,1,1,1,?)",
                      (vault['profile'], kind + ':' + code, code, kind, collection, job))
    profile = {'username': 'photographer', 'notify': 0, 'last_sync': 1}
    sync.Worker.announce(None, profile, job, {}, False)
    assert notices == []
    sync.Worker.announce(None, profile | {'notify': 1}, job, {'post': 1}, False)
    assert notices == [('@photographer', '2 storie nuove · 1 post nuovo', './#home'),
                       ('@photographer', '1 contenuto cancellato su Instagram', './#deleted')]
    notices.clear()
    # The first import announces nothing new; during a long import only stories count.
    sync.Worker.announce(None, profile | {'notify': 1, 'last_sync': None}, job, {}, True)
    sync.Worker.announce(None, profile | {'notify': 1}, job, {}, True)
    assert notices == [('@photographer', '2 storie nuove', './#home')]


def add_account(owner, username, legacy=False):
    name = 'instagram.enc' if legacy else f'instagram-{owner}.enc'
    (store.DATA / name).write_bytes(security.encrypt_json({'username': username, 'verified_via': 'web_session',
        'cookies': {'sessionid': f'{owner}%3Aabc', 'ds_user_id': owner, 'csrftoken': 'x'}}))
    if not legacy:
        with store.db() as c:
            c.execute('INSERT INTO ig_accounts(id,username,session_file,device_file,added_at) VALUES (?,?,?,?,?)',
                      (owner, username, name, f'instagram-device-{username}.enc', int(owner)))


def test_the_original_session_becomes_the_first_account_in_place(vault):
    add_account('11', 'first_account', legacy=True)
    assert [(a['id'], a['session_file'], a['device_file']) for a in sync.accounts()] == [('11', 'instagram.enc', 'instagram-device.enc')]
    assert (store.DATA / 'instagram.enc').exists()


def test_disconnecting_one_account_keeps_the_other(http, vault):
    root, client = http
    add_account('11', 'first_account', legacy=True); add_account('22', 'second_account')
    with store.db() as c:
        c.execute("UPDATE profiles SET account='22' WHERE id=?", (vault['profile'],))
    assert client.post(root + '/api/instagram/disconnect', json={'account': '22'}).ok
    assert [a['username'] for a in client.get(root + '/api/summary').json()['instagram']['accounts']] == ['first_account']
    assert (store.DATA / 'instagram.enc').exists() and not (store.DATA / 'instagram-22.enc').exists()
    with store.db() as c:
        assert c.execute('SELECT account FROM profiles WHERE id=?', (vault['profile'],)).fetchone()[0] == ''


def source_for(private_to=None):
    profile = SimpleNamespace(full_name='P', biography='', userid=42, profile_pic_url='https://cdninstagram.com/a.jpg',
                              get_posts=lambda: iter([]))
    class Source:
        context = SimpleNamespace(is_logged_in=True)
        def __init__(self, account): self.account = account
        def profile(self, username): return profile
        def access(self, remote): return 'not_followed' if private_to and self.account != private_to else ''
        def get_stories(self, ids): return iter([])
        def close(self): pass
    return Source


def run_with_accounts(vault, monkeypatch, behaviour, **profile):
    job = store.enqueue(vault['profile'])
    used = []
    def fake_loader(account=None):
        used.append(account)
        return behaviour(account)
    monkeypatch.setattr(sync, 'loader', fake_loader)
    monkeypatch.setattr(sync, 'access_of', lambda value, remote: value.access(remote))
    monkeypatch.setattr(sync.push, 'notify', lambda *a, **k: 0)
    worker = sync.Worker(); monkeypatch.setattr(worker, 'download', lambda *a: (1, 'digest'))
    values = {'avatar': 'existing.jpg', 'reels': 0, 'stories': 0, 'tagged': 0, 'highlights': 0} | profile
    with store.db() as c:
        for key, value in values.items():
            c.execute(f'UPDATE profiles SET {key}=? WHERE id=?', (value, vault['profile']))
        row = dict(c.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone())
    worker.run_job(row)
    with store.db() as c:
        return used, dict(c.execute('SELECT state,message FROM jobs WHERE id=?', (job,)).fetchone()), \
            dict(c.execute('SELECT account_used,access FROM profiles WHERE id=?', (vault['profile'],)).fetchone())


def test_a_private_profile_is_archived_through_the_account_that_follows_it(vault, monkeypatch):
    add_account('11', 'first_account'); add_account('22', 'second_account')
    used, job, profile = run_with_accounts(vault, monkeypatch, source_for(private_to='22'))
    assert used == ['11', '22'] and job['state'] == 'done' and profile == {'account_used': '22', 'access': ''}


def test_a_disconnected_account_hands_the_profile_to_another(vault, monkeypatch):
    add_account('11', 'first_account'); add_account('22', 'second_account')
    class ChallengeRequired(Exception): pass
    def behaviour(account):
        raise ChallengeRequired()
    used, job, _ = run_with_accounts(vault, monkeypatch, behaviour)
    assert used == ['11'] and job['state'] == 'queued'
    assert [a['usable'] for a in sync.session_status()['accounts']] == [False, True]
    assert sync.account_choices({'account': '', 'account_used': ''}) == ['22']


def test_a_pass_by_another_account_marks_no_story_deleted(vault, monkeypatch):
    add_account('11', 'first_account'); add_account('22', 'second_account')
    with store.db() as c:
        c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete) VALUES (?,?,?,'story',?,1,1)",
                  (vault['profile'], 'story:CF', 'CF', store.now() - 600))
    used, job, _ = run_with_accounts(vault, monkeypatch, source_for(), stories=1, posts=0, account='22', account_used='11')
    assert used == ['22'] and job['state'] == 'done'
    assert store.list_posts({'deleted': '1'})['items'] == []
    # The same account on the next pass does notice it.
    run_with_accounts(vault, monkeypatch, source_for(), stories=1, posts=0)
    assert [p['shortcode'] for p in store.list_posts({'deleted': '1'})['items']] == ['CF']


def test_a_post_is_marked_deleted_only_when_instagram_confirms_it(vault):
    profile = vault['profile']
    with store.db() as c:
        for code in ['Gone', 'Elsewhere', 'Seen']:
            c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete) VALUES (?,?,?,'post',?,1,1)",
                      (profile, 'post:' + code, code, store.now() - 3600))
    asked = []
    def gone(code):
        asked.append(code)
        return code == 'Gone'
    # Elsewhere was just not reached in this pass (the feed is not in date order): Instagram still has it.
    assert store.mark_deleted(profile, ('post', 'reel'), {'Seen'}, store.now() - 86400, gone=gone) == 1
    assert sorted(asked) == ['Elsewhere', 'Gone']
    assert [p['shortcode'] for p in store.list_posts({'deleted': '1'})['items']] == ['Gone']


def test_only_a_clear_not_found_counts_as_removed(vault):
    class MediaNotFound(Exception): pass
    class ClientConnectionError(Exception): pass
    def source(error):
        def info(pk):
            raise error
        return SimpleNamespace(client=SimpleNamespace(media_pk_from_code=lambda code: 1, media_info_v1=info))
    assert sync.removed_from_instagram(source(MediaNotFound()), 'X') is True
    assert sync.removed_from_instagram(source(ClientConnectionError()), 'X') is False
    alive = SimpleNamespace(client=SimpleNamespace(media_pk_from_code=lambda code: 1, media_info_v1=lambda pk: {}))
    assert sync.removed_from_instagram(alive, 'X') is False


def test_a_browser_cookie_account_reads_through_the_mobile_api_with_a_stable_device(vault, monkeypatch):
    from vault import mobile_source
    add_account('22', 'second_account')
    seen = []
    monkeypatch.setattr(mobile_source, 'MobileSource', lambda settings, session: seen.append(settings) or SimpleNamespace())
    assert sync.loader('22').account == '22'
    sync.loader('22')
    assert seen[0]['uuids'] == seen[1]['uuids'] and not seen[0]['cookies']
    assert (store.DATA / 'instagram-device-second_account.enc').exists()


def test_a_logout_redirect_counts_as_a_disconnected_account(vault, monkeypatch):
    monkeypatch.setattr(sync.push, 'notify', lambda *a, **k: 0)
    add_account('22', 'second_account')
    class AbortDownloadException(Exception): pass
    assert sync.note_account_problem(AbortDownloadException('Redirected to login page. You have been logged out'), '22') == 'login'
    assert sync.note_account_problem(AbortDownloadException('429 Too Many Requests'), '22') == 'pause'


def test_the_password_key_comes_from_launcher_sync(vault, monkeypatch):
    calls = []
    class Fake:
        uuid = 'device-uuid'
        last_response = None
        def private_request(self, endpoint, data=None, login=False):
            calls.append(endpoint)
            self.last_response = SimpleNamespace(headers={'ig-set-password-encryption-key-id': '187', 'ig-set-password-encryption-pub-key': 'KEY'})
    monkeypatch.setattr(login, 'Client', Fake)
    assert login.client('someone').password_publickeys() == (187, 'KEY') and calls == ['launcher/sync/']
    class Empty(Fake):
        def private_request(self, endpoint, data=None, login=False):
            self.last_response = SimpleNamespace(headers={})
    monkeypatch.setattr(login, 'Client', Empty)
    with pytest.raises(ValueError, match='chiave'):
        login.client('someone').password_publickeys()


def test_activity_filters_and_clearing_keep_the_archive(http, vault):
    root, client = http
    with store.db() as c:
        other = c.execute('INSERT INTO profiles(username,created_at) VALUES (?,?)', ('second', store.now())).lastrowid
    jobs = {}
    for name, profile, state, downloaded in [('empty', vault['profile'], 'done', 0), ('new', vault['profile'], 'done', 1),
                                             ('failed', other, 'error', 0), ('waiting', other, 'queued', 0)]:
        jobs[name] = store.enqueue(profile) if name != 'waiting' else None
        with store.db() as c:
            if jobs[name] is None:
                jobs[name] = c.execute("INSERT INTO jobs(profile_id,state,created_at) VALUES (?,'queued',?)", (profile, store.now())).lastrowid
            c.execute('UPDATE jobs SET state=?,downloaded=? WHERE id=?', (state, downloaded, jobs[name]))
    with store.db() as c:
        c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete,job_id) VALUES (?,'post:K','K','post',1,1,1,?)",
                  (vault['profile'], jobs['new']))
    ids = lambda **q: {j['id'] for j in client.get(root + '/api/jobs', params=q).json()['items']}
    assert ids(show='new') == {jobs['new']} and ids(show='errors') == {jobs['failed']} and ids(show='active') == {jobs['waiting']}
    assert ids(profile=other) == {jobs['failed'], jobs['waiting']}
    assert client.post(root + '/api/jobs/clear', json={'only_empty': True}).json()['removed'] == 2
    assert ids() == {jobs['new'], jobs['waiting']}
    assert client.post(root + '/api/jobs/clear', json={}).json()['removed'] == 1
    assert ids() == {jobs['waiting']}
    assert [p['shortcode'] for p in store.list_posts({})['items']] == ['K']


def test_a_video_without_preview_gets_a_poster_from_its_first_second(tmp_path):
    import subprocess
    import imageio_ffmpeg
    video = tmp_path / '1.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=64x48:rate=10',
                    '-t', '2', '-pix_fmt', 'yuv420p', str(video)], check=True, timeout=60)
    sync.video_poster(video, tmp_path / '1.poster.jpg')
    from PIL import Image
    with Image.open(tmp_path / '1.poster.jpg') as image:
        assert image.size == (64, 48)


class FakeSocialClient:
    """Pages of a follower list keyed by cursor; counts requests."""
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def private_request(self, endpoint, params=None):
        self.calls.append((endpoint, dict(params or {})))
        if '/likers/' in endpoint:
            return {'users': [{'pk': 900, 'username': 'fan', 'full_name': 'Fan'}]}
        users, following = self.pages[(params or {}).get('max_id', '')]
        if isinstance(users, Exception):
            raise users
        return {'users': [{'pk': n, 'username': f'user{n}'} for n in users], 'next_max_id': following}

    def media_pk_from_code(self, code):
        return 1


def social_setup(vault, monkeypatch, pages, followers=6):
    from vault import social
    monkeypatch.setattr(social, 'pause', lambda low, high: not sync.STOP.is_set())
    monkeypatch.setattr(social, 'LISTS', ('followers',))
    monkeypatch.setattr(sync.push, 'notify', lambda *a, **k: 0)
    with store.db() as c:
        c.execute("UPDATE profiles SET instagram_id='42', next_sync=? WHERE id=?", (store.now() + 3600, vault['profile']))
        c.execute('INSERT INTO profile_history(profile_id,at,followers,following,media_count) VALUES (?,?,?,0,0)', (vault['profile'], store.now(), followers))
    add_account('11', 'first_account')
    client = FakeSocialClient(pages)
    source = SimpleNamespace(client=client, close=lambda: None)
    return social, client, (lambda account=None: source)


def run_pass(social, loader, vault):
    social.due_crawl()
    with store.db() as c:
        c.execute('UPDATE social_crawl SET next_at=0'); c.execute('UPDATE ig_accounts SET social_next=0')
    return social.step(loader)


def test_followers_are_read_in_passes_and_departures_need_two_complete_ones(vault, monkeypatch):
    pages = {'': ([1, 2, 3], '3'), '3': ([4, 5, 6], '')}
    social, client, loader = social_setup(vault, monkeypatch, pages)
    assert run_pass(social, loader, vault)
    data = social.overview(vault['profile'])
    assert data['lists'][0]['known'] == 6 and data['lists'][0]['complete_passes'] == 1 and data['events'] == []
    # User 6 leaves and 7 arrives: 7 is new at once, 6 is gone only after a second complete pass without them.
    pages.update({'': ([1, 2, 3], '3'), '3': ([4, 5, 7], '')})
    run_pass(social, loader, vault)
    assert [(e['username'], e['kind']) for e in social.overview(vault['profile'])['events']] == [('user7', 'new')]
    run_pass(social, loader, vault)
    events = [(e['username'], e['kind']) for e in social.overview(vault['profile'])['events']]
    assert ('user6', 'gone') in events


def test_an_incomplete_pass_marks_nobody_gone(vault, monkeypatch):
    pages = {'': ([1, 2, 3], '3'), '3': ([4, 5, 6], '')}
    social, client, loader = social_setup(vault, monkeypatch, pages, followers=6)
    run_pass(social, loader, vault)
    pages.update({'': ([1, 2], '')})   # Instagram returned only two people this time
    run_pass(social, loader, vault); run_pass(social, loader, vault)
    assert all(e['kind'] != 'gone' for e in social.overview(vault['profile'])['events'])


def test_a_truncated_list_is_marked_and_not_walked_again(vault, monkeypatch):
    social, client, loader = social_setup(vault, monkeypatch, {'': (list(range(49)), '')}, followers=48653)
    run_pass(social, loader, vault)
    data = social.overview(vault['profile'])['lists'][0]
    assert data['capped'] and data['complete_passes'] == 0
    calls = len(client.calls)
    run_pass(social, loader, vault)
    assert len(client.calls) == calls


def test_a_queued_download_or_stop_ends_the_batch_with_the_cursor_saved(vault, monkeypatch):
    pages = {'': ([1, 2], 'a'), 'a': ([3, 4], 'b'), 'b': ([5, 6], '')}
    social, client, loader = social_setup(vault, monkeypatch, pages)
    original = client.private_request
    def then_queue(endpoint, params=None):
        reply = original(endpoint, params)
        if '/likers/' not in endpoint:
            store.enqueue(vault['profile'])
        return reply
    client.private_request = then_queue
    run_pass(social, loader, vault)
    with store.db() as c:
        assert c.execute('SELECT cursor FROM social_crawl').fetchone()[0] == 'a'
    assert social.step(loader) is False   # downloads first


def test_an_account_problem_rests_the_account_and_spares_the_profile(vault, monkeypatch):
    class FeedbackRequired(Exception): pass
    social, client, loader = social_setup(vault, monkeypatch, {'': (FeedbackRequired('slow down'), '')})
    run_pass(social, loader, vault)
    account = sync.accounts()[0]
    assert account['problem'] == 'pause' and account['social_next'] > store.now() + 40 * 3600
    with store.db() as c:
        assert c.execute('SELECT status, error FROM profiles WHERE id=?', (vault['profile'],)).fetchone()[:] != ('error',)


def test_a_private_profile_is_read_only_by_the_account_that_sees_it(vault, monkeypatch):
    social, client, loader = social_setup(vault, monkeypatch, {'': ([1], '')}, followers=1)
    add_account('22', 'second_account')
    with store.db() as c:
        c.execute("UPDATE profiles SET is_private=1, account_used='22' WHERE id=?", (vault['profile'],))
    used = []
    run_pass(social, lambda account=None: used.append(account) or loader(account), vault)
    assert used == ['22']


def test_recent_posts_get_counts_commenters_and_likers_again_by_age(vault, monkeypatch):
    from vault import social
    profile = vault['profile']
    with store.db() as c:
        for code, age, read in [('N', 3600, store.now() - 7*3600), ('W', 5 * 86400, store.now() - 7200), ('O', 90 * 86400, None)]:
            c.execute("INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete,comments_at,likes_at) VALUES (?,?,?,'post',?,1,1,?,?)",
                      (profile, 'post:' + code, code, store.now() - age, read, read))
    asked = {'comments': [], 'likers': []}
    class Source:
        def comments(self, code, *args):
            asked['comments'].append(code)
            return [{'id': 'c' + code, 'username': 'fan', 'full_name': 'Fan', 'text': 'Bella', 'created_at': 1, 'likes': 0}]
        def likers(self, code):
            asked['likers'].append(code)
            return [{'pk': '900', 'username': 'fan', 'full_name': 'Fan'}]
    worker = sync.Worker(); worker.source = Source(); worker.comments_blocked = False
    worker.refresh_recent({'id': profile})
    # Only the hour-old post is due: the five-day one was read two hours ago, the old one is out of the window.
    assert asked == {'comments': ['N'], 'likers': ['N']}
    ranking = social.overview(profile)['ranking']
    assert ranking[0]['username'] == 'fan' and ranking[0]['likes'] == 1 and ranking[0]['comments'] == 1
    assert sync.engagement(SimpleNamespace(likes=10304, comments=120)) == (10304, 120)
    assert sync.engagement(SimpleNamespace()) == (None, None)
