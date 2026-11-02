from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import re
import secrets
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


class ModeChanged(Paused):
    pass


class BufferFull(StorageFull):
    pass


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def fsync_directory(path):
    if hasattr(os, 'O_DIRECTORY'):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def next_check(profile):
    # A few seconds of random offset, so the checks never run like a clock.
    return profile['interval_minutes'] * 60 + random.randint(-30, 30)


def poster_of(relative):
    return relative.rsplit('.', 1)[0] + '.poster.jpg'


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
        raise ValueError('Instagram non ha accettato la sessione. Ricopia sessionid dal browser in cui sei collegato, o accedi di nuovo.')
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
    # The account name comes from Instagram itself in save_session.
    account = store.username(body['username']) if body.get('username') else 'instavault'
    source = str(body.get('cookies', ''))[:100000].strip()
    text = source.removeprefix('Cookie:').strip()
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
        lines = text.splitlines()
        if len(lines) == 1 and '\t' not in text and 'sessionid=' in text:
            # The Cookie request header copied from the browser: name=value; name=value
            for part in text.split(';'):
                name, _, value = part.strip().partition('=')
                cookies[name] = value
            lines = []
        elif len(lines) == 1 and re.fullmatch(r'[A-Za-z0-9%:_.-]{20,400}', text):
            # Only the value of the sessionid cookie.
            cookies['sessionid'] = text
            lines = []
        for line in lines:
            line = line.removeprefix('#HttpOnly_')
            if line.startswith('#') or not line.strip():
                continue
            columns = line.split('\t')
            domain = columns[0].lstrip('.')
            if len(columns) == 7 and (domain == 'instagram.com' or domain.endswith('.instagram.com')):
                cookies[columns[5]] = columns[6]
    if not cookies.get('sessionid'):
        raise ValueError('Non trovo sessionid: incolla il suo valore, la riga Cookie o i cookie in formato JSON o Netscape.')
    cookies = {k: v for k, v in cookies.items() if k in {'sessionid', 'csrftoken', 'ds_user_id', 'mid', 'ig_did', 'rur'}}
    # instaloader needs a csrftoken; Instagram checks only that header and
    # cookie match, so a fresh one works when only sessionid was pasted.
    cookies.setdefault('csrftoken', secrets.token_hex(16))
    owner = cookies['sessionid'].split('%3A', 1)[0]
    if owner.isdigit():
        cookies.setdefault('ds_user_id', owner)
    value = loader()
    value.load_session(account, cookies)
    return save_session(value)


def login_message(exc):
    """Says which check Instagram asked for when logging in with a password."""
    text = str(exc).lower()
    if 'checkpoint' in text:
        return ('Instagram vuole confermare che sei tu. Apri l’app Instagram, conferma l’accesso («Sono stato io») '
                'e riprova qui entro pochi minuti, oppure collega con il cookie del browser.')
    if 'wait a few minutes' in text or type(exc).__name__ == 'TooManyRequestsException':
        return 'Instagram chiede una pausa prima di un altro accesso. Riprova tra 30 minuti oppure collega con il cookie del browser.'
    if 'blocked ip' in text or 'json decode' in text or 'unexpected response' in text:
        return 'Instagram non accetta l’accesso con password da questo server. Collega con il cookie del browser.'
    if type(exc).__name__ == 'BadCredentialsException':
        return 'Username o password Instagram non corretti.'
    return error_message(exc)


def error_message(exc):
    name = type(exc).__name__
    if isinstance(exc, BufferFull):
        return 'Buffer interno pieno: il download riprende quando ricolleghi l’NVMe.'
    if isinstance(exc, ModeChanged):
        return 'Disco cambiato durante il download: riprende da solo.'
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
        # Where the running job writes: 'media' (NVMe) or 'buffer' (eMMC).
        self.mode = 'media'
        self.full = False
        self.draining = False
        self.drain_after = 0

    def start(self):
        self.thread = threading.Thread(target=self.loop, name='instavault-sync', daemon=True)
        self.thread.start()

    def check(self, job_id):
        if STOP.is_set():
            raise Paused()
        if store.mode() != self.mode:
            raise ModeChanged()
        with store.db() as c:
            state = c.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not state or state['state'] != 'running':
            raise Paused()

    def capacity(self, added=0, buffered=None):
        config = store.settings()
        with store.db() as c:
            used = c.execute('SELECT COALESCE(SUM(size),0) FROM media').fetchone()[0]
        if used + added >= config['quota_gb'] * 1024**3:
            raise StorageFull()
        if self.mode == 'buffer':
            if store.buffer_full(added, buffered):
                raise BufferFull()
        elif shutil.disk_usage(store.MEDIA).free < config['min_free_gb'] * 1024**3:
            raise StorageFull()

    def stored(self, asset):
        # The NVMe archive counts as present while the disk is away, so the
        # buffer never fills up with copies of what is already archived.
        if asset['location'] == 'media' and self.mode == 'buffer':
            return True
        path = store.root(asset['location']) / asset['path']
        return path.is_file() and path.stat().st_size == asset['size']

    def download(self, url, relative, job_id):
        self.check(job_id)
        # Measured once per file: walking the buffer for every chunk is too slow.
        buffered = store.buffer_usage() if self.mode == 'buffer' else None
        self.capacity(0, buffered)
        parsed = urlparse(url)
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or not any(host == h or host.endswith('.' + h) for h in ['cdninstagram.com', 'fbcdn.net', 'instagram.com']):
            raise ValueError('Media host non valido')
        target = store.root(self.mode) / relative
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
                        self.check(job_id); self.capacity(size, buffered)
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
                return str(target.relative_to(store.root(self.mode)).as_posix()), image.width, image.height
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
            if existing and existing['complete'] and assets and all(self.stored(a) for a in assets):
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
            if previous and previous['path'] == relative and self.stored(previous):
                continue
            size, digest = self.download(url, relative, job_id)
            image_path = store.root(self.mode) / relative
            if video:
                poster_relative = poster_of(relative)
                self.download(poster, poster_relative, job_id)
                image_path = store.root(self.mode) / poster_relative
            thumb, width, height = self.thumbnail(image_path)
            with store.db() as c:
                c.execute('''INSERT INTO media(post_id,position,kind,path,thumb,size,sha256,width,height,location) VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(post_id,position) DO UPDATE SET kind=excluded.kind,path=excluded.path,thumb=excluded.thumb,
                    size=excluded.size,sha256=excluded.sha256,width=excluded.width,height=excluded.height,location=excluded.location''',
                          (ident, index, 'video' if video else 'image', relative, thumb, size, digest, width, height, self.mode))
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
        self.mode = store.mode()
        more = False
        value = None
        try:
            value = loader()
            self.check(ident)
            remote = instaloader.Profile.from_username(value.context, profile['username'])
            with store.db() as c:
                c.execute('UPDATE profiles SET full_name=?,biography=?,instagram_id=? WHERE id=?',
                          (remote.full_name, remote.biography, str(remote.userid), profile['id']))
            if not profile['avatar'] and self.mode == 'media':
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
                          (store.now(), store.now() + (60 if more else next_check(profile)), profile['id']))
        except Exception as exc:
            message = error_message(exc)
            if isinstance(exc, BufferFull):
                self.full = True
            # A restart or a disk change interrupted the job, not the user:
            # it goes back in the queue and runs again by itself.
            retry = STOP.is_set() or isinstance(exc, (ModeChanged, BufferFull))
            with store.db() as c:
                if retry and c.execute("UPDATE jobs SET state='queued',started_at=NULL,message=? WHERE id=? AND state='running'",
                                       (message, ident)).rowcount:
                    c.execute("UPDATE profiles SET status='queued',error='' WHERE id=?", (profile['id'],))
                else:
                    state = 'cancelled' if isinstance(exc, Paused) else 'error'
                    c.execute('UPDATE jobs SET state=?,finished_at=?,error=? WHERE id=?', (state, store.now(), message, ident))
                    c.execute('UPDATE profiles SET status=?,error=?,next_sync=? WHERE id=?',
                              (state, message, store.now() + 12 * 3600, profile['id']))
        finally:
            if value:
                value.close()
            self.current = None

    def copy_verified(self, source, target):
        """True once target holds the same bytes as source. A different file
        already at target is never overwritten: both copies stay."""
        digest = file_digest(source)
        if target.exists():
            return target.is_file() and file_digest(target) == digest
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.instavault-copy')
        try:
            with source.open('rb') as src, temporary.open('wb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
                dst.flush(); os.fsync(dst.fileno())
            if file_digest(temporary) != digest:
                return False
            temporary.replace(target)
            fsync_directory(target.parent)
            return True
        finally:
            temporary.unlink(missing_ok=True)

    def drain(self):
        """Moves what the buffer holds onto the NVMe, like LiveVault's
        merge_buffer: verified copy first, then the database, then delete."""
        source = store.buffer_root()
        files = sorted(p for p in source.rglob('*') if p.is_file()) if source.is_dir() else []
        if not files or time.time() < self.drain_after:
            return
        # Files left behind (a conflict, a full NVMe) are tried again later,
        # not hashed again every 20 seconds.
        self.drain_after = time.time() + 600
        self.draining = True
        try:
            reserve = store.settings()['min_free_gb'] * 1024**3
            copied = set()
            for path in files:
                if STOP.is_set() or not store.storage_ready():
                    return
                if path.name.endswith(store.SKIPPED_SUFFIXES):
                    path.unlink(missing_ok=True)  # an interrupted download, never recorded
                    continue
                if shutil.disk_usage(store.MEDIA).free - path.stat().st_size < reserve:
                    break
                relative = path.relative_to(source).as_posix()
                if self.copy_verified(path, store.MEDIA / relative):
                    copied.add(relative)
            if not store.storage_ready():
                return
            with store.db() as c:
                for row in c.execute("SELECT id,path,thumb FROM media WHERE location='buffer'").fetchall():
                    names = {row['path'], row['thumb'], poster_of(row['path'])} - {''}
                    if row['path'] in copied and all(n in copied or not (source / n).exists() for n in names):
                        c.execute("UPDATE media SET location='media' WHERE id=?", (row['id'],))
                waiting = {n for r in c.execute("SELECT path,thumb FROM media WHERE location='buffer'")
                           for n in (r['path'], r['thumb'], poster_of(r['path']))}
            for relative in copied - waiting:
                (source / relative).unlink(missing_ok=True)
            for folder in sorted((p for p in source.rglob('*') if p.is_dir()), reverse=True):
                with contextlib.suppress(OSError):
                    folder.rmdir()
        finally:
            self.draining = False

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
                mode = store.mode()
                if mode == 'media':
                    self.full = False
                    self.drain()
                elif not self.full and store.buffer_full():
                    self.full = True
                if mode == 'media' or not self.full:
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
