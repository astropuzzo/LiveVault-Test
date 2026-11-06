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

from . import push, social, store, security, request_policy

WAKE = threading.Event()
STOP = threading.Event()
SESSION_LOCK = threading.Lock()


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


# Account-wide problems stop the whole pass; anything else stays in its section.
STOPPING = {'PleaseWaitFewMinutes', 'TooManyRequestsException', 'ClientThrottledError', 'FeedbackRequired',
            'ChallengeRequired', 'LoginRequired', 'ClientLoginRequired', 'AbortDownloadException'}


def highlight_link(highlight):
    # Highlights keep a stable address; a plain story's expires after a day.
    pk = str(getattr(highlight, 'pk', '') or getattr(highlight, 'unique_id', '')).split(':')[-1]
    return f'https://www.instagram.com/stories/highlights/{pk}/' if pk.isdigit() else ''


def plural(count, one, many):
    return f'{count} {one if count == 1 else many}'


def engagement(item):
    """Like and comment counts the listing already carries, or (None, None)."""
    try:
        likes, comments = getattr(item, 'likes', None), getattr(item, 'comments', None)
        return (int(likes), int(comments or 0)) if isinstance(likes, int) else (None, None)
    except (TypeError, ValueError):
        return None, None


# How often a post's commenters and likers are read again, by its age.
REFRESH_AFTER = [(2 * 86400, 6 * 3600), (7 * 86400, 86400), (30 * 86400, 3 * 86400)]
REFRESH_POSTS = 1


def refresh_due(column, limit=REFRESH_POSTS):
    rules = ' OR '.join(f'(p.published_at>=:now-{age} AND (p.{column} IS NULL OR p.{column}<:now-{every}))'
                        for age, every in REFRESH_AFTER)
    return f'''SELECT p.id, p.shortcode FROM posts p WHERE p.profile_id=:profile AND p.complete=1
        AND p.kind IN ('post','reel') AND ({rules}) ORDER BY p.published_at DESC LIMIT {int(limit)}'''


def poster_of(relative):
    return relative.rsplit('.', 1)[0] + '.poster.jpg'


def video_poster(video, target):
    """A poster from the video itself, for the stories Instagram sends
    without a preview picture. Without it the grid shows a placeholder."""
    import subprocess
    import imageio_ffmpeg
    try:
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-y', '-ss', '0.5', '-i', str(video),
                        '-frames:v', '1', '-q:v', '3', str(target)], check=True, timeout=60, capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f'InstaVault: poster {video.name}: {type(exc).__name__}', flush=True)


# The connected account needs the user: 'login' to reconnect, 'pause' to wait.
ACCOUNT_PROBLEMS = {
    'login': {'LoginRequired', 'ClientLoginRequired', 'LoginRequiredException', 'BadCredentialsException',
              'ChallengeRequired', 'ChallengeError', 'ChallengeUnknownStep', 'TwoFactorAuthRequiredException'},
    'pause': {'FeedbackRequired', 'PleaseWaitFewMinutes', 'ClientThrottledError', 'TooManyRequestsException'},
}


PAUSE_SECONDS = 3 * 3600


def removed_from_instagram(source, shortcode):
    """True only when Instagram answers that the post does not exist."""
    try:
        if hasattr(source, 'client'):
            source.client.media_info_v1(source.client.media_pk_from_code(shortcode))
        else:
            instaloader.Post.from_shortcode(source.context, shortcode)
        return False
    except Exception as exc:
        name = type(exc).__name__
        if name in STOPPING:
            raise
        return name in {'MediaNotFound', 'ClientNotFoundError', 'QueryReturnedNotFoundException'}


def session_owner(cookies):
    owner = str(cookies.get('ds_user_id') or '')
    if not owner.isdigit():
        match = re.match(r'^\d+', str(cookies.get('sessionid') or ''))
        owner = match[0] if match else ''
    return owner


def accounts():
    """The connected accounts, oldest first. A session saved before accounts
    existed is registered in place, its files untouched."""
    legacy = store.DATA / 'instagram.enc'
    with store.db() as c:
        known = c.execute("SELECT 1 FROM ig_accounts WHERE session_file='instagram.enc'").fetchone()
    if legacy.exists() and not known:
        try:
            item = security.decrypt_json(legacy.read_bytes())
            owner = session_owner(item.get('cookies') or {})
            problem = store.settings().get('session_problem') or {}
            with store.db() as c:
                c.execute("""INSERT OR IGNORE INTO ig_accounts(id,username,session_file,device_file,added_at,problem,problem_message,problem_at)
                    VALUES (?,?,'instagram.enc','instagram-device.enc',?,?,?,?)""", (owner or item['username'], item['username'],
                    item.get('checked_at') or store.now(), problem.get('kind', ''), problem.get('message', ''), problem.get('at')))
                c.execute("DELETE FROM settings WHERE key='session_problem'")
        except (OSError, ValueError, KeyError, TypeError):
            pass
    with store.db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM ig_accounts ORDER BY added_at, id')]


def usable(account):
    if account['problem'] == 'login':
        return False
    return account['problem'] != 'pause' or store.now() - (account['problem_at'] or 0) >= PAUSE_SECONDS


def account_choices(profile):
    """Accounts to try for a profile: the chosen one, else the last that
    worked, then the other usable ones. Never empty while one is connected."""
    rows = accounts()
    if not rows:
        return [None]
    pinned = [r for r in rows if r['id'] == profile.get('account')]
    if pinned:
        return [pinned[0]['id']]
    good = sorted((r for r in rows if usable(r)), key=lambda r: r['id'] != profile.get('account_used'))
    return [r['id'] for r in good] or [rows[0]['id']]


def note_account_problem(exc, account=None):
    name = type(exc).__name__
    kind = next((k for k, names in ACCOUNT_PROBLEMS.items() if name in names), None)
    if name == 'AbortDownloadException':
        # Instaloader uses it both for "logged out" and for throttling.
        kind = 'login' if 'login' in str(exc).lower() else 'pause'
    if kind and account:
        with store.db() as c:
            row = c.execute('SELECT username, problem FROM ig_accounts WHERE id=?', (account,)).fetchone()
            c.execute('UPDATE ig_accounts SET problem=?,problem_message=?,problem_at=? WHERE id=?',
                      (kind, error_message(exc), store.now(), account))
        if row and not row['problem']:
            others = [r['username'] for r in accounts() if r['id'] != account and usable(r)]
            push.notify(f'InstaVault: @{row["username"]} si è fermato',
                        error_message(exc) + (f' Continuo con @{others[0]}.' if others else ''), './#settings')
    return kind


def clear_account_problem(account):
    with store.db() as c:
        c.execute("UPDATE ig_accounts SET problem='',problem_message='',problem_at=NULL WHERE id=?", (account,))


def session_status():
    rows = accounts()
    listed = [{'id': r['id'], 'username': r['username'], 'added_at': r['added_at'], 'usable': usable(r),
               'problem': {'kind': r['problem'], 'message': r['problem_message'], 'at': r['problem_at']} if r['problem'] else None}
              for r in rows]
    first = next((a for a in listed if a['usable']), listed[0] if listed else None)
    # problem: only when no account can download any more.
    stopped = first and not first['usable']
    return {'configured': bool(listed), 'username': first['username'] if first else '', 'checked_at': None,
            'problem': first['problem'] if stopped else None, 'accounts': listed}


def new_loader():
    value = instaloader.Instaloader(quiet=True, sleep=True, max_connection_attempts=1,
                                   request_timeout=20, fatal_status_codes=[401, 403, 429])
    value.context.error = lambda *a, **k: None
    return value


def loader(account=None):
    """A source signed in as `account` (default: the first usable one)."""
    rows = accounts()
    row = next((r for r in rows if r['id'] == account), None) if account else next((r for r in rows if usable(r)), rows[0] if rows else None)
    value = new_loader()
    value.account = row['id'] if row else None
    if row:
        if not usable(row):
            until = (row['problem_at'] or store.now()) + (PAUSE_SECONDS if row['problem'] == 'pause' else 12*3600)
            raise request_policy.Deferred(max(until, store.now()+600), 'account')
        item = security.decrypt_json((store.DATA / row['session_file']).read_bytes())
        if item.get('verified_via') == 'mobile_login' or (item.get('cookies') or {}).get('sessionid'):
            value.close()
            from .mobile_source import MobileSource
            device = store.DATA / row['device_file']
            if not device.exists():
                # One stable phone identity per account, kept from the first use on.
                from instagrapi import Client
                fresh = Client().get_settings()
                fresh['cookies'], fresh['authorization_data'] = {}, {}
                temporary = device.with_name(device.name + '.tmp')
                with temporary.open('wb') as handle:
                    os.chmod(temporary, 0o600); handle.write(security.encrypt_json(fresh))
                temporary.replace(device)
            settings = security.decrypt_json(device.read_bytes())
            source = MobileSource(settings, item)
            source.account = row['id']
            return source
        value.load_session(item['username'], item['cookies'])
    return value


def device_file(username):
    """The device identity a login for `username` reuses; a new account gets its own."""
    accounts()
    with store.db() as c:
        row = c.execute('SELECT device_file FROM ig_accounts WHERE lower(username)=lower(?)', (username,)).fetchone()
    return row['device_file'] if row else f'instagram-device-{username.lower()}.enc'


def save_session(value, verified_identity=None, verified_via='web_session'):
    from .instagram_session import validate_web_session
    cookies = value.save_session()
    # A mobile login is already authenticated by the mobile client. Browser
    # cookies must be verified on a browser endpoint, never the legacy GraphQL.
    if verified_identity and cookies.get('csrftoken'):
        account = store.username(verified_identity)
    else:
        account, cookies = validate_web_session(cookies)
        if verified_identity and account.lower() != verified_identity.lower():
            raise ValueError('La sessione restituita da Instagram appartiene a un altro account.')
    value.load_session(account, cookies)
    owner = session_owner(cookies)
    if not owner:
        raise ValueError('Instagram non ha indicato a quale account appartiene la sessione.')
    content = security.encrypt_json({'username': account, 'cookies': cookies,
                                    'checked_at': store.now(), 'verified_via': verified_via})
    device = device_file(account)
    with store.db() as c:
        row = c.execute('SELECT session_file FROM ig_accounts WHERE id=?', (owner,)).fetchone()
    target = store.DATA / (row['session_file'] if row else f'instagram-{owner}.enc')
    temporary = target.with_name(target.name + '.tmp')
    with temporary.open('wb') as f:
        os.chmod(temporary, 0o600); f.write(content); f.flush(); os.fsync(f.fileno())
    temporary.replace(target)
    with store.db() as c:
        c.execute("""INSERT INTO ig_accounts(id,username,session_file,device_file,added_at) VALUES (?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET username=excluded.username, problem='', problem_message='', problem_at=NULL""",
                  (owner, account, target.name, device, store.now()))
    WAKE.set()
    return session_status()


def remove_account(account):
    """Disconnects one account: its session and device files go, profiles fall back to automatic."""
    rows = accounts()
    row = next((r for r in rows if r['id'] == account), None)
    if not row:
        raise ValueError('Account non trovato.')
    for name in {row['session_file'], row['device_file']}:
        (store.DATA / name).unlink(missing_ok=True)
    with store.db() as c:
        c.execute('DELETE FROM ig_accounts WHERE id=?', (account,))
        c.execute("UPDATE profiles SET account='' WHERE account=?", (account,))
        c.execute("UPDATE profiles SET account_used='' WHERE account_used=?", (account,))


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
    if name in {'ProfileNotExistsException', 'QueryReturnedNotFoundException', 'UserNotFound'}:
        return 'Profilo non trovato: controlla lo username o se l’account esiste ancora.'
    if name in {'LoginRequired', 'ClientLoginRequired'}:
        return 'La sessione Instagram è scaduta. Ricollega l’account nelle impostazioni.'
    if name in {'ChallengeRequired', 'ChallengeError', 'ChallengeUnknownStep'}:
        return 'Instagram chiede di confermare che sei tu. Apri l’app Instagram, completa la verifica, poi premi Aggiorna.'
    if name == 'FeedbackRequired':
        return 'Instagram ha limitato per qualche ora l’account collegato. InstaVault riprova da solo più tardi.'
    if name in {'PleaseWaitFewMinutes', 'ClientThrottledError', 'RateLimitError'}:
        return 'Instagram chiede una pausa. InstaVault rispetta la pausa condivisa e riprende automaticamente quando termina.'
    if name in {'ConnectionError', 'ConnectTimeout', 'ReadTimeout', 'ClientConnectionError', 'ClientRequestTimeout'}:
        return 'Connessione a Instagram non riuscita. Riprova da solo al prossimo controllo.'
    detail = instagram_detail(exc)
    if name in {'UnknownError', 'ClientError', 'ClientBadRequestError', 'ClientJSONDecodeError'}:
        return ('Instagram ha rifiutato questa richiesta' + (f' («{detail}»)' if detail else '') +
                '. Di solito è un blocco temporaneo: riprovo tra un’ora, quello già scaricato resta.')
    if isinstance(exc, ValueError) and detail:
        return detail + ' Riprovo tra un’ora; il resto dell’archivio è aggiornato.'
    return f'Errore imprevisto ({name}' + (f': {detail}' if detail else '') + '). Riprovo tra un’ora.'


def instagram_detail(exc):
    # Instagram's own words, when the reply carried any, cut to one line.
    text = ''
    response = getattr(exc, 'response', None)
    try:
        payload = response.json() if response is not None else {}
        text = payload.get('message') or payload.get('error_title') or ''
    except Exception:
        pass
    text = str(text or exc or '').strip().splitlines()[0] if str(text or exc or '').strip() else ''
    return re.sub(r'https?://\S+', '', text)[:140].strip()


ACCESS_MESSAGES = {
    'requested': 'Profilo privato: la richiesta di seguirlo è in attesa. InstaVault scarica tutto appena viene accettata.',
    'not_followed': 'Profilo privato: l’account collegato non lo segue. Chiedi di seguirlo per poterlo archiviare.',
}


def access_of(value, remote):
    """'' when the connected account can see the profile, else why not."""
    if hasattr(value, 'access'):
        return value.access(remote)
    if not getattr(remote, 'is_private', False) or getattr(remote, 'followed_by_viewer', False):
        return ''
    if str(getattr(remote, 'userid', '')) == str(getattr(value.context, 'user_id', '') or ''):
        return ''
    return 'requested' if getattr(remote, 'requested_by_viewer', False) else 'not_followed'


def follow(profile_id):
    """Sends the follow request for a private profile from the connected account."""
    with store.db() as c:
        row = c.execute('SELECT username, account, account_used FROM profiles WHERE id=?', (profile_id,)).fetchone()
    if not row:
        raise ValueError('Profilo non trovato.')
    value = loader(account_choices(dict(row))[0])
    try:
        if not hasattr(value, 'follow'):
            raise ValueError('Per chiedere di seguire da qui collega Instagram con username e password. '
                             'In alternativa invia la richiesta dall’app.')
        remote = value.profile(row['username'])
        value.follow(remote.userid)
        access = value.access(remote)
    finally:
        value.close()
    with store.db() as c:
        c.execute('UPDATE profiles SET access=? WHERE id=?', (access, profile_id))
    return access


class Worker:
    def __init__(self):
        self.source = None
        self.comments_blocked = False
        self.seen = {}
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

    def archive(self, profile, item, kind, job_id, collection='', permalink=''):
        story = kind == 'story'
        shortcode = str(item.mediaid) if story else item.shortcode
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', shortcode):
            raise ValueError('Identificativo media non valido')
        # A tagged post is someone else's: its own key, so it never mixes with the profile's posts.
        external = ('story:' if story else 'tagged:' if kind == 'tagged' else 'post:') + shortcode
        likes, comments = engagement(item) if not story else (None, None)
        with store.db() as c:
            existing = c.execute('SELECT * FROM posts WHERE profile_id=? AND external_key=?', (profile['id'], external)).fetchone()
            assets = list(c.execute('SELECT * FROM media WHERE post_id=?', (existing['id'],))) if existing else []
            if existing and likes is not None:
                c.execute('UPDATE posts SET like_total=?,comment_total=?,counts_at=? WHERE id=?', (likes, comments, store.now(), existing['id']))
            if existing and existing['complete'] and assets and all(self.stored(a) for a in assets):
                if kind == 'reel' and existing['kind'] != 'reel':
                    c.execute("UPDATE posts SET kind='reel' WHERE id=?", (existing['id'],))
                return False
            if not existing:
                # A collab or repost already archived under another profile:
                # list it here too instead of downloading it again.
                twin = c.execute('SELECT id FROM posts WHERE external_key=? AND profile_id<>? AND complete=1 ORDER BY id LIMIT 1',
                                 (external, profile['id'])).fetchone()
                twin_assets = list(c.execute('SELECT * FROM media WHERE post_id=?', (twin['id'],))) if twin else []
                if twin_assets and all(self.stored(a) for a in twin_assets):
                    c.execute('INSERT OR IGNORE INTO shared VALUES (?,?)', (twin['id'], profile['id']))
                    return False
        self.check(job_id)
        date = item.date_utc if story else item.date_utc
        stamp = int(date.replace(tzinfo=__import__('datetime').timezone.utc).timestamp())
        caption = '' if story else (item.caption or '')
        if not story and kind != 'tagged' and getattr(item, 'product_type', '') == 'clips':
            kind = 'reel'
        if not permalink:
            permalink = f'https://www.instagram.com/stories/{profile["username"]}/{shortcode}/' if story else f'https://www.instagram.com/p/{shortcode}/'
        owner = str(getattr(item, 'owner_username', '') or '')[:30]
        links = json.dumps(getattr(item, 'links', []) or [], ensure_ascii=False) if story else ''
        if not story and item.typename == 'GraphSidecar':
            nodes = list(item.get_sidecar_nodes())
            parts = [(n.is_video, n.video_url if n.is_video else n.display_url, n.display_url) for n in nodes]
        else:
            parts = [(item.is_video, item.video_url if item.is_video else item.url, item.url)]
        with store.db() as c:
            if existing:
                ident = existing['id']
            else:
                ident = c.execute('''INSERT INTO posts(profile_id,external_key,shortcode,kind,caption,published_at,archived_at,permalink,collection,owner,links,job_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (profile['id'], external, shortcode, kind, caption, stamp, store.now(), permalink, collection, owner, links, job_id)).lastrowid
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
                if poster:
                    self.download(poster, poster_relative, job_id)
                else:
                    video_poster(store.root(self.mode) / relative, store.root(self.mode) / poster_relative)
                image_path = store.root(self.mode) / poster_relative
            thumb, width, height = self.thumbnail(image_path)
            with store.db() as c:
                c.execute('''INSERT INTO media(post_id,position,kind,path,thumb,size,sha256,width,height,location) VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(post_id,position) DO UPDATE SET kind=excluded.kind,path=excluded.path,thumb=excluded.thumb,
                    size=excluded.size,sha256=excluded.sha256,width=excluded.width,height=excluded.height,location=excluded.location''',
                          (ident, index, 'video' if video else 'image', relative, thumb, size, digest, width, height, self.mode))
        with store.db() as c:
            c.execute('UPDATE posts SET complete=1,caption=?,kind=?,owner=?,links=?,job_id=? WHERE id=?', (caption, kind, owner, links, job_id, ident))
            if likes is not None:
                c.execute('UPDATE posts SET like_total=?,comment_total=?,counts_at=? WHERE id=?', (likes, comments, store.now(), ident))
            c.execute('UPDATE jobs SET downloaded=downloaded+1 WHERE id=?', (job_id,))
        # Comments are read by maintenance, after fresh media is safe.
        return True

    def save_comments(self, post_id, shortcode, pages=1):
        # Comments are a bonus: a refusal stops reading them for this pass,
        # never the download of the media.
        source = self.source
        if source is None or not hasattr(source, 'comments') or self.comments_blocked:
            return False
        try:
            found = source.comments(shortcode, pages, lambda: not STOP.wait(random.uniform(2, 5))) if pages > 1 else source.comments(shortcode)
        except Exception as exc:
            self.comments_blocked = True
            if isinstance(exc, request_policy.Deferred) or type(exc).__name__ in STOPPING:
                raise
            print(f'InstaVault: comments {shortcode}: {type(exc).__name__}: {instagram_detail(exc)}', flush=True)
            return False
        with store.db() as c:
            for item in found:
                c.execute('''INSERT INTO comments(post_id,external_id,username,full_name,text,created_at,likes) VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(post_id,external_id) DO UPDATE SET text=excluded.text,likes=excluded.likes''',
                          (post_id, item['id'], item['username'], item['full_name'], item['text'], item['created_at'], item['likes']))
            c.execute('UPDATE posts SET comments_at=? WHERE id=?', (store.now(), post_id))
        return True

    def refresh_recent(self, profile):
        """Comments and likers of recent posts: hourly while new, then less often."""
        source = self.source
        args = {'profile': profile['id'], 'now': store.now()}
        with store.db() as c:
            stale = list(c.execute(refresh_due('comments_at'), args))
        for row in stale:
            # One page only; shared maintenance budgets cover comments too.
            if STOP.is_set() or not self.save_comments(row['id'], row['shortcode'], pages=1):
                break
        if not hasattr(source, 'likers') or self.comments_blocked:
            return
        with store.db() as c:
            stale = list(c.execute(refresh_due('likes_at'), args))
        for row in stale:
            if STOP.is_set():
                return
            try:
                found = source.likers(row['shortcode'])
            except Exception as exc:
                if isinstance(exc, request_policy.Deferred) or type(exc).__name__ in STOPPING:
                    raise
                print(f'InstaVault: likers {row["shortcode"]}: {type(exc).__name__}: {instagram_detail(exc)}', flush=True)
                return
            with store.db() as c:
                for user in found:
                    c.execute('INSERT OR IGNORE INTO likes(post_id,pk,username,full_name,first_seen) VALUES (?,?,?,?,?)',
                              (row['id'], user['pk'], user['username'], user['full_name'], store.now()))
                c.execute('UPDATE posts SET likes_at=? WHERE id=?', (store.now(), row['id']))

    def backfill_comments(self, profile, limit=1):
        # Posts archived before comments existed get theirs a few at a time.
        with store.db() as c:
            rows = list(c.execute('''SELECT id,shortcode FROM posts WHERE profile_id=? AND complete=1 AND kind<>'story'
                AND comments_at IS NULL ORDER BY published_at DESC LIMIT ?''', (profile['id'], limit)))
        for row in rows:
            if STOP.is_set() or not self.save_comments(row['id'], row['shortcode']):
                break

    def find_profile(self, value, profile):
        """By username; after a rename, by the Instagram id stored earlier."""
        if not hasattr(value, 'profile'):
            return instaloader.Profile.from_username(value.context, profile['username'])
        try:
            return value.profile(profile['username'])
        except Exception as exc:
            if type(exc).__name__ not in {'UserNotFound', 'ProfileNotExistsException'} or not profile.get('instagram_id') or not hasattr(value, 'profile_by_id'):
                raise
        remote = value.profile_by_id(profile['instagram_id'])
        with store.db() as c:
            if remote.username and not c.execute('SELECT 1 FROM profiles WHERE username=? AND id<>?', (remote.username, profile['id'])).fetchone():
                c.execute('UPDATE profiles SET username=? WHERE id=?', (remote.username, profile['id']))
                print(f'InstaVault: @{profile["username"]} is now @{remote.username}', flush=True)
                profile['username'] = remote.username
        return remote

    def record_profile(self, profile, remote, job_id):
        # The photo's file name on Instagram's CDN changes only with a new photo.
        url = str(remote.profile_pic_url or '')
        key = urlparse(url).path.rsplit('/', 1)[-1][:120]
        snapshot = {'followers': getattr(remote, 'followers', None), 'following': getattr(remote, 'followees', None),
                    'media_count': getattr(remote, 'mediacount', None) or getattr(remote, 'media_count', None),
                    'full_name': remote.full_name or '', 'biography': remote.biography or '', 'avatar_key': key,
                    'username': getattr(remote, 'username', '') or profile['username']}
        with store.db() as c:
            last = c.execute('SELECT avatar_key FROM profile_history WHERE profile_id=? ORDER BY at DESC, id DESC LIMIT 1', (profile['id'],)).fetchone()
        new_photo = url and self.mode == 'media' and (not profile['avatar'] or (last and last['avatar_key'] != key))
        if new_photo:
            avatar = f'{profile["id"]}/avatar-{store.now()}.jpg'
            try:
                self.download(url, avatar, job_id)
                snapshot['avatar'] = avatar
                with store.db() as c:
                    c.execute('UPDATE profiles SET avatar=? WHERE id=?', (avatar, profile['id']))
            except (Paused, StorageFull):
                raise
            except Exception as exc:
                print(f'InstaVault: @{profile["username"]} avatar: {type(exc).__name__}', flush=True)
        elif profile['avatar']:
            snapshot['avatar'] = profile['avatar']
        store.record_history(profile['id'], snapshot)

    def stream(self, profile, iterator, kind, job_id, latest=False, resume_key=None):
        from instaloader.nodeiterator import FrozenNodeIterator
        with store.db() as c:
            cursor = c.execute('SELECT * FROM cursors WHERE profile_id=? AND kind=?', (profile['id'], resume_key or kind)).fetchone()
        initial = not cursor or not cursor['complete']
        fresh = True
        if not latest and cursor and cursor['data']:
            try:
                frozen = json.loads(cursor['data'])
                if hasattr(iterator, 'restore'):
                    iterator.restore(frozen)
                else:
                    iterator.thaw(FrozenNodeIterator(**frozen))
                fresh = False
            except (ValueError, TypeError, instaloader.InvalidArgumentException):
                pass
        # Only a pass that starts from the newest post can tell what vanished.
        seen = self.seen[kind] = {'fresh': fresh, 'codes': set(), 'oldest': None}
        known = 0
        for index, item in enumerate(iterator):
            self.check(job_id)
            seen['codes'].add(item.shortcode)
            stamp = int(item.date_utc.replace(tzinfo=__import__('datetime').timezone.utc).timestamp())
            # Pinned posts and collabs come first, so the feed is not in date order.
            seen['oldest'] = min(seen['oldest'] or stamp, stamp)
            added = self.archive(profile, item, kind, job_id)
            known = 0 if added else known + 1
            with store.db() as c:
                c.execute('UPDATE jobs SET seen=seen+1,message=? WHERE id=?', ({'reel': 'Controllo dei Reel…', 'tagged': 'Controllo dei post in cui è taggato…'}.get(kind, 'Controllo dei post…'), job_id))
            if (latest and index >= 11 and known >= 3) or (not latest and (not initial or resume_key) and known >= 12):
                break
            if not latest and hasattr(iterator, 'freeze'):
                snapshot = iterator.freeze()
                frozen = json.dumps(snapshot._asdict() if hasattr(snapshot, '_asdict') else snapshot)
                with store.db() as c:
                    c.execute('INSERT OR REPLACE INTO cursors VALUES (?,?,?,0)', (profile['id'], resume_key or kind, frozen))
            if index >= (23 if latest else 11):
                if latest and hasattr(iterator, 'freeze'):
                    snapshot = iterator.freeze()
                    frozen = json.dumps(snapshot._asdict() if hasattr(snapshot, '_asdict') else snapshot)
                    key = f'catchup:{kind}:{job_id:020d}'
                    with store.db() as c:
                        c.execute('INSERT OR IGNORE INTO cursors VALUES (?,?,?,0)', (profile['id'], key, frozen))
                return True
        if not latest:
            with store.db() as c:
                if resume_key:
                    c.execute('DELETE FROM cursors WHERE profile_id=? AND kind=?', (profile['id'], resume_key))
                else:
                    c.execute('INSERT OR REPLACE INTO cursors VALUES (?,?,NULL,1)', (profile['id'], kind))
        return False

    def backfill_media(self, profile, remote, job_id):
        with store.db() as c:
            pending = c.execute("SELECT kind FROM cursors WHERE profile_id=? AND kind LIKE 'catchup:%' ORDER BY kind LIMIT 1", (profile['id'],)).fetchone()
        if pending:
            kind = pending['kind'].split(':')[1]
            method = remote.get_posts if kind == 'post' else remote.get_reels
            return self.stream(profile, method(), kind, job_id, resume_key=pending['kind'])
        for kind, enabled, method in [('post', profile['posts'], remote.get_posts),
                                       ('reel', profile['reels'], getattr(remote, 'get_reels', None))]:
            if not enabled or not method:
                continue
            with store.db() as c:
                row = c.execute('SELECT complete FROM cursors WHERE profile_id=? AND kind=?', (profile['id'], kind)).fetchone()
            if row and row['complete']:
                continue
            return self.stream(profile, method(), kind, job_id)
        return False

    def import_highlights(self, profile, value, remote, job_id):
        # Store IDs, not expiring CDN links. At most one collection's API per
        # pass; new live stories have already been archived before this runs.
        with store.db() as c:
            row = c.execute("SELECT data FROM cursors WHERE profile_id=? AND kind='highlight-progress'", (profile['id'],)).fetchone()
        state = json.loads(row['data']) if row and row['data'] else {}
        completed = set(state.get('completed', []))
        for highlight in value.get_highlights(remote):
            key = str(getattr(highlight, 'pk', '') or getattr(highlight, 'unique_id', '') or highlight.title)
            if key in completed:
                continue
            items = list(highlight.get_items())
            start = 0
            if state.get('current') == key:
                start = next((i+1 for i, item in enumerate(items) if str(item.mediaid) == state.get('last')), 0)
            for offset, item in enumerate(items[start:]):
                self.check(job_id)
                try:
                    self.archive(profile, item, 'story', job_id, 'In evidenza · ' + highlight.title[:150], highlight_link(highlight))
                except requests.HTTPError as exc:
                    if getattr(exc.response, 'status_code', None) not in (403,404,410):
                        raise
                state = {'completed': sorted(completed), 'current': key, 'last': str(item.mediaid)}
                with store.db() as c:
                    c.execute("INSERT OR REPLACE INTO cursors VALUES (?,'highlight-progress',?,0)", (profile['id'], json.dumps(state)))
                if offset >= 11:
                    return True
            completed.add(key)
            with store.db() as c:
                c.execute("INSERT OR REPLACE INTO cursors VALUES (?,'highlight-progress',?,0)",
                          (profile['id'], json.dumps({'completed': sorted(completed)})))
            return True
        with store.db() as c:
            c.execute("DELETE FROM cursors WHERE profile_id=? AND kind='highlight-progress'", (profile['id'],))
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
        self.comments_blocked = False
        account = None
        try:
            choices = account_choices(profile)
            for index, account in enumerate(choices):
                value = loader(account)
                self.source = value
                self.check(ident)
                remote = self.find_profile(value, profile)
                access = access_of(value, remote)
                with store.db() as c:
                    c.execute('UPDATE profiles SET is_private=? WHERE id=?', (int(bool(getattr(remote, 'is_private', False))), profile['id']))
                if not access or index + 1 == len(choices):
                    break
                # This account cannot see the private profile: the next one may follow it.
                value.close(); value = None
            with store.db() as c:
                c.execute('UPDATE profiles SET full_name=?,biography=?,instagram_id=? WHERE id=?',
                          (remote.full_name, remote.biography, str(remote.userid), profile['id']))
            self.record_profile(profile, remote, ident)
            # Accounts see different stories (close friends, hidden ones): only
            # the account that made the previous pass can tell what vanished.
            same_account = profile.get('account_used') in ('', account)
            with store.db() as c:
                c.execute('UPDATE profiles SET access=? WHERE id=?', (access, profile['id']))
            if access:
                # Nothing to download until the profile accepts: say so, check again later.
                with store.db() as c:
                    c.execute("UPDATE jobs SET state='done',finished_at=?,message=? WHERE id=?",
                              (store.now(), ACCESS_MESSAGES[access], ident))
                    c.execute("UPDATE profiles SET status='private',error='',last_sync=?,next_sync=? WHERE id=?",
                              (store.now(), store.now() + next_check(profile), profile['id']))
                return
            warnings = []
            successful = []
            deleted = {}
            self.seen = {}
            def section(kind, label, action, period=0, lane='fresh'):
                previous_lane = getattr(value, 'request_lane', 'fresh')
                nonlocal more
                with store.db() as c:
                    row = c.execute('SELECT data FROM cursors WHERE profile_id=? AND kind=?', (profile['id'], 'pause:' + kind)).fetchone()
                if row:
                    previous = json.loads(row['data'])
                    if previous['until'] > store.now():
                        warnings.append(previous['message']); return
                if period:
                    with store.db() as c:
                        scheduled = c.execute('SELECT next_at FROM sync_schedule WHERE profile_id=? AND kind=?', (profile['id'], kind)).fetchone()
                    if scheduled and scheduled['next_at'] > store.now():
                        return
                value.request_lane = lane
                try:
                    pending = bool(action())
                    more |= pending
                    if period:
                        with store.db() as c:
                            c.execute('INSERT OR REPLACE INTO sync_schedule VALUES (?,?,?)',
                                      (profile['id'], kind, store.now() + (1800 if pending else period)))
                    successful.append(kind)
                    with store.db() as c:
                        c.execute('DELETE FROM cursors WHERE profile_id=? AND kind=?', (profile['id'], 'pause:' + kind))
                except request_policy.Deferred as exc:
                    warnings.append(label + ': controllo secondario rinviato per lasciare spazio alle novità')
                    with store.db() as c:
                        c.execute('INSERT OR REPLACE INTO sync_schedule VALUES (?,?,?)', (profile['id'], kind, int(exc.until)+1))
                    if lane == 'fresh':
                        raise
                except (Paused, StorageFull):
                    raise
                except Exception as exc:
                    # Account verification and throttling stop the whole pass.
                    # A single unavailable content category must not block all
                    # other posts or be falsely reported as an empty archive.
                    if type(exc).__name__ in STOPPING:
                        raise
                    print(f'InstaVault: @{profile["username"]} {label}: {type(exc).__name__}: {instagram_detail(exc)}', flush=True)
                    warning = label + ': ' + error_message(exc)
                    warnings.append(warning)
                    with store.db() as c:
                        c.execute('INSERT OR REPLACE INTO cursors VALUES (?,?,?,0)',
                                  (profile['id'], 'pause:' + kind, json.dumps({'until':store.now()+3600,'message':warning})))
                finally:
                    value.request_lane = previous_lane
            if profile['stories'] and value.context.is_logged_in:
                def stories():
                    live = set()
                    for story in value.get_stories([remote.userid]):
                        for item in story.get_items():
                            live.add(str(item.mediaid))
                            self.archive(profile, item, 'story', ident)
                    # A story still inside its 24 hours that Instagram no longer lists was deleted.
                    if same_account:
                        deleted['story'] = store.mark_deleted(profile['id'], (), live, store.now() - 86400 + 600, story=True)
                section('story', 'Storie', stories)
            if profile['posts']:
                section('post', 'Post nuovi', lambda: self.stream(profile, remote.get_posts(), 'post', ident, latest=True))
            if profile['reels']:
                section('reel', 'Reel nuovi', lambda: self.stream(profile, remote.get_reels(), 'reel', ident, latest=True))
            # Announce fresh captures before slow work can hit a pause. Old
            # backfill added later in this job must not produce "new" notices.
            self.announce(profile, ident, {}, False)
            tagged = getattr(remote, 'get_tagged', None) or getattr(remote, 'get_tagged_posts', None)
            if profile.get('tagged') and value.context.is_logged_in and tagged:
                section('tagged', 'Post in cui è taggato', lambda: self.stream(profile, tagged(), 'tagged', ident, latest=True), period=6*3600, lane='maintenance')
            section('backfill', 'Archivio storico', lambda: self.backfill_media(profile, remote, ident), period=6*3600, lane='maintenance')
            if profile['highlights'] and value.context.is_logged_in:
                section('highlight', 'In evidenza', lambda: self.import_highlights(profile, value, remote, ident), period=6*3600, lane='maintenance')
            def check_deleted():
                posts_seen = self.seen.get('post')
                if posts_seen and posts_seen['fresh'] and posts_seen['oldest'] and 'post' in successful and same_account:
                    codes = posts_seen['codes'] | (self.seen.get('reel') or {}).get('codes', set())
                    since = max(posts_seen['oldest'], store.now() - 14*86400)
                    deleted['post'] = store.mark_deleted(profile['id'], ('post', 'reel'), codes, since,
                                                        gone=lambda code: removed_from_instagram(value, code), limit=1)
            section('deleted-check', 'Verifica rimossi', check_deleted, period=6*3600, lane='maintenance')
            if warnings and not successful:
                raise RuntimeError('Selected content categories temporarily unavailable')
            def interactions():
                self.refresh_recent(profile)
                self.backfill_comments(profile)
            section('interactions', 'Commenti e like', interactions, period=6*3600, lane='maintenance')
            with store.db() as c:
                message = 'Prima importazione in corso' if more else 'Archivio aggiornato'
                if (profile['stories'] or profile['highlights']) and not value.context.is_logged_in:
                    message += ' · Collega Instagram per storie e contenuti in evidenza'
                gone = sum(deleted.values())
                if gone:
                    message += ' · ' + plural(gone, 'contenuto cancellato su Instagram', 'contenuti cancellati su Instagram')
                if warnings:
                    message += ' · ' + ' · '.join(warnings)
                c.execute("UPDATE jobs SET state='done',finished_at=?,message=? WHERE id=?", (store.now(), message, ident))
                if account:
                    c.execute("UPDATE ig_accounts SET problem='',problem_message='',problem_at=NULL WHERE id=?", (account,))
                    c.execute('UPDATE profiles SET account_used=? WHERE id=?', (account, profile['id']))
                c.execute("UPDATE profiles SET status='ready',error=?,last_sync=?,next_sync=? WHERE id=?",
                          (' · '.join(warnings), store.now(), store.now() + next_check(profile), profile['id']))
            # Historical work must not suppress notices about a fresh capture.
            self.announce(profile | {'notify': 0}, ident, deleted, False)
        except request_policy.Deferred as exc:
            until = max(store.now()+60, int(exc.until)+1)
            message = 'Controlli in attesa: rispetto la pausa o il budget Instagram, archivio conservato.'
            with store.db() as c:
                c.execute("UPDATE jobs SET state='done',finished_at=?,message=? WHERE id=?", (store.now(), message, ident))
                c.execute("UPDATE profiles SET status='ready',error=?,next_sync=? WHERE id=?", (message, until, profile['id']))
        except Exception as exc:
            message = error_message(exc)
            problem = note_account_problem(exc, account)
            if not isinstance(exc, Paused):
                print(f'InstaVault: @{profile["username"]} job: {type(exc).__name__}: {instagram_detail(exc)}', flush=True)
            if isinstance(exc, BufferFull):
                self.full = True
            # A restart or a disk change interrupted the job, not the user:
            # it goes back in the queue and runs again by itself.
            retry = STOP.is_set() or isinstance(exc, (ModeChanged, BufferFull))
            # A disconnected account hands the profile to another one right away.
            # A throttled one does not: the same IP would get the next one throttled too.
            if problem == 'login' and not profile.get('account') and any(a['id'] != account for a in accounts() if usable(a)):
                retry = True
                message = 'Account scollegato da Instagram, continuo con un altro account.'
            with store.db() as c:
                if retry and c.execute("UPDATE jobs SET state='queued',started_at=NULL,message=? WHERE id=? AND state='running'",
                                       (message, ident)).rowcount:
                    c.execute("UPDATE profiles SET status='queued',error='' WHERE id=?", (profile['id'],))
                else:
                    state = 'cancelled' if isinstance(exc, Paused) else 'error'
                    c.execute('UPDATE jobs SET state=?,finished_at=?,error=? WHERE id=?', (state, store.now(), message, ident))
                    c.execute('UPDATE profiles SET status=?,error=?,next_sync=? WHERE id=?',
                              (state, message, max(store.now()+next_check(profile), int(request_policy.ready_at(account or 'anonymous'))+1), profile['id']))
        finally:
            if value:
                value.close()
            self.source = None
            self.current = None

    def announce(self, profile, job_id, deleted, more):
        # New stories and posts only for profiles with the bell on and never
        # during the first import; deletions for every profile.
        try:
            if profile.get('notify') and profile['last_sync']:
                with store.db() as c:
                    counts = dict(c.execute('''SELECT kind, COUNT(*) FROM posts WHERE job_id=? AND complete=1
                        AND NOT (kind='story' AND collection<>'') GROUP BY kind''', (job_id,)).fetchall())
                labels = [('story', 'storia nuova', 'storie nuove'), ('post', 'post nuovo', 'post nuovi'), ('reel', 'Reel nuovo', 'Reel nuovi')]
                parts = [plural(counts[k], one, many) for k, one, many in labels if counts.get(k) and (k == 'story' or not more)]
                if parts:
                    push.notify('@' + profile['username'], ' · '.join(parts), './#home')
            gone = sum(deleted.values())
            if gone:
                push.notify('@' + profile['username'], plural(gone, 'contenuto cancellato su Instagram', 'contenuti cancellati su Instagram'), './#deleted')
        except Exception as exc:
            print(f'InstaVault: notice @{profile["username"]}: {type(exc).__name__}', flush=True)

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
                    # Idle: read followers, following and likes a little at a time.
                    if store.settings()['automatic'] and social.step():
                        continue
            except Exception:
                # Individual failures do not terminate the unattended scheduler.
                pass
            WAKE.wait(20); WAKE.clear()
        self.leader = False
        lease.close()


worker = Worker()
