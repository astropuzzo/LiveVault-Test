from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import instaloader
import requests
from PIL import Image, ImageOps

from . import store, security

WAKE = threading.Event()
STOP = threading.Event()
SESSION_LOCK = threading.Lock()
PENDING_LOGIN = None


class Paused(Exception):
    pass


class StorageFull(Exception):
    pass


def session_status():
    path = store.DATA / 'instagram.enc'
    try:
        item = security.decrypt_json(path.read_bytes())
        return {'configured': True, 'username': item['username'], 'checked_at': item.get('checked_at')}
    except (OSError, ValueError, KeyError):
        return {'configured': False, 'username': '', 'checked_at': None}


def loader():
    value = instaloader.Instaloader(quiet=True, sleep=True, max_connection_attempts=1,
                                   request_timeout=20, fatal_status_codes=[401, 403, 429])
    value.context.error = lambda *a, **k: None
    path = store.DATA / 'instagram.enc'
    if path.exists():
        item = security.decrypt_json(path.read_bytes())
        value.load_session(item['username'], item['cookies'])
    return value


def save_session(value):
    account = value.test_login()
    if not account:
        raise ValueError('La sessione Instagram non è valida. Accedi nuovamente.')
    content = security.encrypt_json({'username': account, 'cookies': value.save_session(), 'checked_at': store.now()})
    temporary = store.DATA / 'instagram.enc.tmp'
    with temporary.open('wb') as f:
        os.chmod(temporary, 0o600); f.write(content); f.flush(); os.fsync(f.fileno())
    temporary.replace(store.DATA / 'instagram.enc')
    WAKE.set()
    return session_status()


def instagram_login(body):
    global PENDING_LOGIN
    with SESSION_LOCK:
        if body.get('code'):
            if not PENDING_LOGIN or PENDING_LOGIN[1] < time.time() - 300:
                raise ValueError('Il tentativo è scaduto. Ripeti l’accesso.')
            value = PENDING_LOGIN[0]
            value.two_factor_login(str(body['code']).strip())
        else:
            value = instaloader.Instaloader(quiet=True, max_connection_attempts=1, request_timeout=20)
            value.context.error = lambda *a, **k: None
            try:
                value.login(store.username(body.get('username', '')), str(body.get('password', ''))[:1000])
            except instaloader.TwoFactorAuthRequiredException:
                PENDING_LOGIN = (value, time.time())
                return {'two_factor': True}
        result = save_session(value)
        PENDING_LOGIN = None
        return result


def import_cookies(body):
    account = store.username(body.get('username', ''))
    source = str(body.get('cookies', ''))[:100000]
    cookies = {}
    try:
        item = json.loads(source)
        if isinstance(item, list):
            for cookie in item:
                domain = str(cookie.get('domain', '')).lstrip('.')
                if domain == 'instagram.com' or domain.endswith('.instagram.com'):
                    cookies[str(cookie['name'])] = str(cookie['value'])
        elif isinstance(item, dict):
            cookies = {str(k): str(v) for k, v in item.items() if isinstance(v, str)}
    except json.JSONDecodeError:
        for line in source.splitlines():
            line = line.removeprefix('#HttpOnly_')
            if line.startswith('#') or not line.strip():
                continue
            columns = line.split('\t')
            domain = columns[0].lstrip('.')
            if len(columns) == 7 and (domain == 'instagram.com' or domain.endswith('.instagram.com')):
                cookies[columns[5]] = columns[6]
    if not cookies.get('sessionid'):
        raise ValueError('Importa cookie Instagram in formato JSON o Netscape, con sessionid.')
    cookies = {k: v for k, v in cookies.items() if k in {'sessionid', 'csrftoken', 'ds_user_id', 'mid', 'ig_did', 'rur'}}
    value = loader()
    value.load_session(account, cookies)
    return save_session(value)


def error_message(exc):
    name = type(exc).__name__
    if isinstance(exc, StorageFull):
        return 'Spazio riservato esaurito. Aumenta la quota o libera spazio sul disco.'
    if isinstance(exc, Paused):
        return 'Download interrotto; i contenuti già archiviati sono conservati.'
    if name in {'LoginRequiredException', 'PrivateProfileNotFollowedException', 'BadCredentialsException'}:
        return 'Instagram richiede una sessione valida che abbia accesso al profilo. Collega il tuo account nelle impostazioni.'
    if name in {'TwoFactorAuthRequiredException', 'LoginException', 'AbortDownloadException', 'TooManyRequestsException'}:
        return 'Instagram ha richiesto una verifica o una pausa. Controlla l’account nel browser e riconnetti la sessione.'
    if name in {'ProfileNotExistsException', 'QueryReturnedNotFoundException'}:
        return 'Profilo non trovato o non disponibile per questa sessione.'
    return 'Instagram non ha completato la richiesta. L’archivio è conservato; puoi riprovare più tardi.'


class Worker:
    def __init__(self):
        self.thread = None
        self.current = None
        self.leader = False

    def start(self):
        self.thread = threading.Thread(target=self.loop, name='instavault-sync', daemon=True)
        self.thread.start()

    def check(self, job_id):
        if STOP.is_set() or not store.storage_ready():
            raise Paused()
        with store.db() as c:
            state = c.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not state or state['state'] != 'running':
            raise Paused()

    def capacity(self, added=0):
        state = store.storage_status()
        config = store.settings()
        if not state['ready']:
            raise Paused()
        if state['used_bytes'] + added >= state['quota_bytes'] or state['free_bytes'] < config['min_free_gb'] * 1024**3:
            raise StorageFull()

    def download(self, url, relative, job_id):
        self.check(job_id); self.capacity()
        parsed = urlparse(url)
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or not any(host == h or host.endswith('.' + h) for h in ['cdninstagram.com', 'fbcdn.net', 'instagram.com']):
            raise ValueError('Media host non valido')
        target = store.MEDIA / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + '.instavault-part')
        digest = hashlib.sha256(); size = 0
        try:
            with requests.get(url, stream=True, timeout=(10, 25), allow_redirects=False) as response:
                response.raise_for_status()
                with partial.open('wb') as f:
                    for chunk in response.iter_content(256 * 1024):
                        if not chunk:
                            continue
                        self.check(job_id); self.capacity(size)
                        f.write(chunk); digest.update(chunk); size += len(chunk)
                    f.flush(); os.fsync(f.fileno())
            self.check(job_id)
            if not size:
                raise ValueError('File vuoto')
            partial.replace(target)
            return size, digest.hexdigest()
        finally:
            partial.unlink(missing_ok=True)

    def thumbnail(self, path):
        try:
            target = path.with_suffix('.thumb.jpg')
            with Image.open(path) as image:
                image.draft('RGB', (600, 600))
                image = ImageOps.exif_transpose(image)
                image.thumbnail((600, 750))
                image.convert('RGB').save(target, 'JPEG', quality=83, optimize=True)
                return str(target.relative_to(store.MEDIA)), image.width, image.height
        except (OSError, ValueError):
            return '', 0, 0

    def archive(self, profile, item, kind, job_id, collection=''):
        story = kind == 'story'
        shortcode = str(item.mediaid) if story else item.shortcode
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', shortcode):
            raise ValueError('Identificativo media non valido')
        external = ('story:' if story else 'post:') + shortcode
        with store.db() as c:
            existing = c.execute('SELECT * FROM posts WHERE profile_id=? AND external_key=?', (profile['id'], external)).fetchone()
            assets = list(c.execute('SELECT * FROM media WHERE post_id=?', (existing['id'],))) if existing else []
            if existing and existing['complete'] and assets and all((store.MEDIA / a['path']).is_file() for a in assets):
                if kind == 'reel' and existing['kind'] != 'reel':
                    c.execute("UPDATE posts SET kind='reel' WHERE id=?", (existing['id'],))
                return False
        self.check(job_id)
        date = item.date_utc if story else item.date_utc
        stamp = int(date.replace(tzinfo=__import__('datetime').timezone.utc).timestamp())
        caption = '' if story else (item.caption or '')
        if not story and getattr(item, 'product_type', '') == 'clips':
            kind = 'reel'
        permalink = f'https://www.instagram.com/stories/{profile["username"]}/{shortcode}/' if story else f'https://www.instagram.com/p/{shortcode}/'
        if not story and item.typename == 'GraphSidecar':
            nodes = list(item.get_sidecar_nodes())
            parts = [(n.is_video, n.video_url if n.is_video else n.display_url, n.display_url) for n in nodes]
        else:
            parts = [(item.is_video, item.video_url if item.is_video else item.url, item.url)]
        with store.db() as c:
            if existing:
                ident = existing['id']
            else:
                ident = c.execute('''INSERT INTO posts(profile_id,external_key,shortcode,kind,caption,published_at,archived_at,permalink,collection)
                    VALUES (?,?,?,?,?,?,?,?,?)''', (profile['id'], external, shortcode, kind, caption, stamp, store.now(), permalink, collection)).lastrowid
        for index, (video, url, poster) in enumerate(parts):
            self.check(job_id)
            relative = f'{profile["id"]}/{shortcode}/{index + 1}.{"mp4" if video else "jpg"}'
            previous = next((a for a in assets if a['position'] == index), None)
            if previous and previous['path'] == relative:
                previous_path = store.MEDIA / relative
                if previous_path.is_file() and previous_path.stat().st_size == previous['size']:
                    continue
            size, digest = self.download(url, relative, job_id)
            image_path = store.MEDIA / relative
            if video:
                poster_relative = f'{profile["id"]}/{shortcode}/{index + 1}.poster.jpg'
                self.download(poster, poster_relative, job_id)
                image_path = store.MEDIA / poster_relative
            thumb, width, height = self.thumbnail(image_path)
            with store.db() as c:
                c.execute('''INSERT INTO media(post_id,position,kind,path,thumb,size,sha256,width,height) VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(post_id,position) DO UPDATE SET kind=excluded.kind,path=excluded.path,thumb=excluded.thumb,
                    size=excluded.size,sha256=excluded.sha256,width=excluded.width,height=excluded.height''',
                          (ident, index, 'video' if video else 'image', relative, thumb, size, digest, width, height))
        with store.db() as c:
            c.execute('UPDATE posts SET complete=1,caption=?,kind=? WHERE id=?', (caption, kind, ident))
            c.execute('UPDATE jobs SET downloaded=downloaded+1 WHERE id=?', (job_id,))
        return True

    def stream(self, profile, iterator, kind, job_id):
        from instaloader.nodeiterator import FrozenNodeIterator
        with store.db() as c:
            cursor = c.execute('SELECT * FROM cursors WHERE profile_id=? AND kind=?', (profile['id'], kind)).fetchone()
        initial = not cursor or not cursor['complete']
        if cursor and cursor['data']:
            try:
                iterator.thaw(FrozenNodeIterator(**json.loads(cursor['data'])))
            except (ValueError, TypeError, instaloader.InvalidArgumentException):
                pass
        known = 0
        for index, item in enumerate(iterator):
            self.check(job_id)
            added = self.archive(profile, item, kind, job_id)
            known = 0 if added else known + 1
            with store.db() as c:
                c.execute('UPDATE jobs SET seen=seen+1,message=? WHERE id=?', ('Archiviazione ' + ('Reel' if kind == 'reel' else 'post'), job_id))
            if not initial and known >= 16:
                break
            if hasattr(iterator, 'freeze'):
                frozen = json.dumps(iterator.freeze()._asdict())
                with store.db() as c:
                    c.execute('INSERT OR REPLACE INTO cursors VALUES (?,?,?,0)', (profile['id'], kind, frozen))
            if index >= 74:
                return True
        with store.db() as c:
            c.execute('INSERT OR REPLACE INTO cursors VALUES (?,?,NULL,1)', (profile['id'], kind))
        return False

    def run_job(self, job):
        ident = job['id']
        with store.db() as c:
            claimed = c.execute("UPDATE jobs SET state='running',started_at=?,error='' WHERE id=? AND state='queued'", (store.now(), ident))
            if not claimed.rowcount:
                return
            profile = dict(c.execute('SELECT * FROM profiles WHERE id=?', (job['profile_id'],)).fetchone())
            c.execute("UPDATE profiles SET status='syncing',error='' WHERE id=?", (profile['id'],))
        self.current = ident
        more = False
        value = None
        try:
            value = loader()
            self.check(ident)
            remote = instaloader.Profile.from_username(value.context, profile['username'])
            with store.db() as c:
                c.execute('UPDATE profiles SET full_name=?,biography=?,instagram_id=? WHERE id=?',
                          (remote.full_name, remote.biography, str(remote.userid), profile['id']))
            if not profile['avatar']:
                avatar = f'{profile["id"]}/avatar.jpg'
                self.download(remote.profile_pic_url, avatar, ident)
                with store.db() as c:
                    c.execute('UPDATE profiles SET avatar=? WHERE id=?', (avatar, profile['id']))
            if profile['stories'] and value.context.is_logged_in:
                for story in value.get_stories([remote.userid]):
                    for item in story.get_items():
                        self.archive(profile, item, 'story', ident)
            if profile['posts']:
                more |= self.stream(profile, remote.get_posts(), 'post', ident)
            if profile['reels']:
                more |= self.stream(profile, remote.get_reels(), 'reel', ident)
            if profile['highlights'] and value.context.is_logged_in:
                for highlight in value.get_highlights(remote):
                    for item in highlight.get_items():
                        self.archive(profile, item, 'story', ident, 'In evidenza · ' + highlight.title[:150])
            with store.db() as c:
                message = 'Prima importazione in corso' if more else 'Archivio aggiornato'
                if (profile['stories'] or profile['highlights']) and not value.context.is_logged_in:
                    message += ' · Collega Instagram per storie e contenuti in evidenza'
                c.execute("UPDATE jobs SET state='done',finished_at=?,message=? WHERE id=?", (store.now(), message, ident))
                c.execute("UPDATE profiles SET status='ready',error='',last_sync=?,next_sync=? WHERE id=?",
                          (store.now(), store.now() + (60 if more else profile['interval_hours'] * 3600), profile['id']))
        except Exception as exc:
            message = error_message(exc)
            state = 'cancelled' if isinstance(exc, Paused) else 'error'
            with store.db() as c:
                c.execute('UPDATE jobs SET state=?,finished_at=?,error=? WHERE id=?', (state, store.now(), message, ident))
                c.execute('UPDATE profiles SET status=?,error=?,next_sync=? WHERE id=?',
                          (state, message, store.now() + 12 * 3600, profile['id']))
        finally:
            if value:
                value.close()
            self.current = None

    def recover_abandoned(self):
        # Only the process holding the download lease may recover old work.
        with store.db() as c:
            c.execute("UPDATE jobs SET state='queued',started_at=NULL,message='Ripresa dopo riavvio' WHERE state='running'")
            c.execute("UPDATE jobs SET state='cancelled',finished_at=? WHERE state='cancel_requested'", (store.now(),))

    def loop(self):
        # A rolling deploy must never run two downloaders against the archive.
        lease = (store.DATA / 'worker.lock').open('a+b')
        try:
            import fcntl
        except ImportError:
            fcntl = None  # Windows local preview; production runs on Linux.
        while not STOP.is_set():
            try:
                if not self.leader:
                    try:
                        if fcntl:
                            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        self.leader = True
                        self.recover_abandoned()
                    except BlockingIOError:
                        WAKE.wait(5); WAKE.clear(); continue
                if store.storage_ready():
                    if store.settings()['automatic']:
                        with store.db() as c:
                            due = list(c.execute('SELECT id FROM profiles WHERE enabled=1 AND next_sync<=?', (store.now(),)))
                        for profile in due:
                            store.enqueue(profile['id'])
                    with store.db() as c:
                        row = c.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at,id LIMIT 1").fetchone()
                    if row:
                        self.run_job(dict(row))
                        continue
            except Exception:
                # Individual failures do not terminate the unattended scheduler.
                pass
            WAKE.wait(20); WAKE.clear()
        self.leader = False
        lease.close()


worker = Worker()
