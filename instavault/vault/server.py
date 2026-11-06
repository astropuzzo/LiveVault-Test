from __future__ import annotations

import gzip
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import signal
import sqlite3
import threading
import time
import zipfile
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import login, push, social, store, security, sync

mimetypes.add_type('application/manifest+json', '.webmanifest')


def file_suffix(relative):
    return os.path.splitext(relative)[1].lower()

VERSION = '1.0.0'
LOGIN_ATTEMPTS = {}
LOGIN_LOCK = threading.Lock()


class HTTPError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


class Handler(BaseHTTPRequestHandler):
    server_version = 'InstaVault'

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *args):
        # Request URLs and authentication payloads never enter the log.
        pass

    def route(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == '/instavault' or path.startswith('/instavault/'):
            path = path[len('/instavault'):] or '/'
        return path, {k: v[-1] for k, v in parse_qs(parsed.query).items()}

    def headers_common(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Robots-Tag', 'noindex, nofollow')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")

    def send_json(self, value, status=200, cookie=None):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.headers_common()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def body(self):
        if 'application/json' not in self.headers.get('Content-Type', ''):
            raise HTTPError(415, 'Usa una richiesta JSON.')
        size = int(self.headers.get('Content-Length', '0'))
        if size < 0 or size > 262144:
            raise HTTPError(413, 'Richiesta troppo grande.')
        value = json.loads(self.rfile.read(size) or b'{}')
        if not isinstance(value, dict):
            raise ValueError('Richiesta non valida.')
        return value

    def token(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
            return cookies['instavault_session'].value if 'instavault_session' in cookies else ''
        except Exception:
            return ''

    def authenticate(self):
        token = self.token()
        if not security.verify_session(token):
            raise HTTPError(401, 'Accedi a InstaVault.')
        return token

    def proxied(self):
        # The port is published on loopback only, so a private peer is the
        # host's Tailscale Funnel proxy or Docker's port forwarder.
        try:
            peer = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return peer.is_loopback or peer.is_private

    def client_ip(self):
        forwarded = self.headers.get('X-Forwarded-For', '')
        if forwarded and self.proxied():
            candidate = forwarded.split(',')[-1].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
        return self.client_address[0]

    def origin_check(self):
        origin = self.headers.get('Origin')
        if not origin:
            return
        hosts = {self.headers.get('Host', '').lower()}
        if self.proxied():
            hosts.add(self.headers.get('X-Forwarded-Host', '').lower())
        if urlparse(origin).netloc.lower() not in hosts - {''}:
            raise HTTPError(403, 'Origine non valida.')

    def mutation(self):
        self.origin_check()
        token = self.authenticate()
        if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), security.csrf_token(token)):
            raise HTTPError(403, 'Sessione da aggiornare. Ricarica la pagina.')

    def dispatch(self):
        if self.command in {'GET', 'HEAD'} and urlparse(self.path).path == '/instavault':
            self.send_response(302); self.headers_common()
            self.send_header('Location', '/instavault/'); self.send_header('Content-Length', '0'); self.end_headers(); return
        path, query = self.route()
        if self.command in {'GET', 'HEAD'}:
            return self.get(path, query)
        if self.command == 'POST' and path == '/api/login':
            self.origin_check()
            body = self.body(); ip = self.client_ip()
            with LOGIN_LOCK:
                attempts = [t for t in LOGIN_ATTEMPTS.get(ip, []) if t > time.time() - 900]
                if len(attempts) >= 6:
                    raise HTTPError(429, 'Troppi tentativi. Riprova tra 15 minuti.')
                if len(LOGIN_ATTEMPTS) > 500:
                    LOGIN_ATTEMPTS.clear()
                LOGIN_ATTEMPTS[ip] = attempts + [time.time()]
            if not security.check_password(str(body.get('username', '')), str(body.get('password', ''))):
                raise HTTPError(401, 'Nome utente o password non corretti.')
            with LOGIN_LOCK:
                LOGIN_ATTEMPTS.pop(ip, None)
            token = security.issue_session()
            secure = '; Secure' if os.environ.get('INSTAVAULT_COOKIE_SECURE', 'true') == 'true' else ''
            return self.send_json({'ok': True}, cookie=f'instavault_session={token}; Path=/; Max-Age=604800; HttpOnly; SameSite=Strict{secure}')
        self.mutation()
        body = self.body()
        if path == '/api/instagram/login/cancel':
            return self.send_json(login.cancel(str(body.get('attempt_id', ''))))
        if path == '/api/push/subscribe':
            push.subscribe(body)
            return self.send_json({'ok': True})
        if path == '/api/push/unsubscribe':
            push.unsubscribe(body.get('endpoint', ''))
            return self.send_json({'ok': True})
        if path == '/api/push/test':
            if not push.notify('InstaVault', 'Le notifiche funzionano su questo dispositivo.', './#settings'):
                raise ValueError('Nessun dispositivo ha ricevuto la prova. Riattiva le notifiche e riprova.')
            return self.send_json({'ok': True})
        if path == '/api/logout':
            secure = '; Secure' if os.environ.get('INSTAVAULT_COOKIE_SECURE', 'true') == 'true' else ''
            return self.send_json({'ok': True}, cookie='instavault_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict' + secure)
        if path == '/api/profiles' and self.command == 'POST':
            name = store.username(body.get('username', ''))
            values = self.profile_values(body)
            candidate = {'posts': 1, 'reels': 1, 'stories': 1, 'highlights': 0} | values
            if not any(candidate[k] for k in ['posts', 'reels', 'stories', 'highlights']):
                raise ValueError('Seleziona almeno un tipo di contenuto.')
            with store.db() as c:
                if c.execute('SELECT COUNT(*) FROM profiles').fetchone()[0] >= 100:
                    raise ValueError('Hai raggiunto il limite di 100 profili.')
                old = c.execute('SELECT id,removed FROM profiles WHERE username=?', (name,)).fetchone()
                if old and old['removed']:
                    # Added again: it comes back with its archive and carries on from there.
                    ident = old['id']
                    c.execute("UPDATE profiles SET removed=0,enabled=1,status='queued',error='',next_sync=0 WHERE id=?", (ident,))
                else:
                    ident = c.execute('INSERT INTO profiles(username,created_at) VALUES (?,?)', (name, store.now())).lastrowid
                if values:
                    c.execute('UPDATE profiles SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', [*values.values(), ident])
            store.enqueue(ident); sync.WAKE.set()
            return self.send_json({'ok': True, 'id': ident}, 201)
        match = re.fullmatch(r'/api/profiles/(\d+)/remove', path)
        if match:
            ident = int(match[1]); delete = bool(body.get('delete_data'))
            try:
                files = store.remove_profile(ident, delete)
            except KeyError:
                raise HTTPError(404, 'Profilo non trovato.') from None
            if delete:
                store.delete_files(files, ident)
            return self.send_json({'ok': True, 'deleted': delete})
        match = re.fullmatch(r'/api/profiles/(\d+)/follow', path)
        if match:
            try:
                with sync.SESSION_LOCK:
                    access = sync.follow(int(match[1]))
            except ValueError as exc:
                raise HTTPError(400, str(exc)) from None
            except Exception as exc:
                raise HTTPError(400, sync.error_message(exc)) from None
            store.enqueue(int(match[1])); sync.WAKE.set()
            return self.send_json({'ok': True, 'access': access})
        match = re.fullmatch(r'/api/profiles/(\d+)(/sync)?', path)
        if match:
            ident = int(match[1])
            if match[2]:
                result = store.enqueue(ident); sync.WAKE.set()
                return self.send_json({'ok': True, 'job_id': result})
            self.update_profile(ident, body); sync.WAKE.set()
            return self.send_json({'ok': True})
        if path == '/api/sync':
            ids = [p['id'] for p in store.profile_rows() if p['enabled']]
            for ident in ids:
                store.enqueue(ident)
            sync.WAKE.set()
            return self.send_json({'ok': True, 'queued': len(ids)})
        if path == '/api/jobs/clear':
            profile = str(body.get('profile') or '')
            return self.send_json({'removed': store.clear_jobs(int(profile) if profile.isdigit() else None, bool(body.get('only_empty')))})
        match = re.fullmatch(r'/api/jobs/(\d+)/cancel', path)
        if match:
            with store.db() as c:
                c.execute("UPDATE jobs SET state=CASE WHEN state='running' THEN 'cancel_requested' ELSE 'cancelled' END WHERE id=? AND state IN ('queued','running')", (int(match[1]),))
            return self.send_json({'ok': True})
        match = re.fullmatch(r'/api/posts/(\d+)/favorite', path)
        if match:
            with store.db() as c:
                c.execute('UPDATE posts SET favorite=? WHERE id=?', (bool(body.get('favorite')), int(match[1])))
            return self.send_json({'ok': True})
        if path == '/api/settings':
            values = {}
            if 'automatic' in body:
                values['automatic'] = bool(body['automatic'])
            for key, low, high in [('quota_gb', 1, 10000), ('min_free_gb', 2, 1000)]:
                if key in body:
                    number = int(body[key])
                    if not low <= number <= high:
                        raise ValueError('Valore fuori intervallo.')
                    values[key] = number
            with store.db() as c:
                c.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)', [(k, json.dumps(v)) for k, v in values.items()])
            sync.WAKE.set()
            return self.send_json({'ok': True})
        if path == '/api/instagram/login':
            try:
                result = login.password_login(body)
            except ValueError as exc:
                from .instagram_session import SessionError
                if isinstance(exc, SessionError):
                    print(f'InstaVault: cookie verification result={exc.code} source={exc.source} http={exc.http_status or 0}', flush=True)
                    status = 429 if exc.code == 'rate_limited' else 503 if exc.code in {'verification_unavailable', 'network_error'} else 400
                    return self.send_json({'error': str(exc), 'reason': exc.code}, status)
                # Plain ValueErrors carry InstaVault's own Italian message.
                raise HTTPError(400, str(exc) if type(exc) is ValueError else 'Controlla i dati inseriti e riprova.') from None
            except Exception as exc:
                raise HTTPError(400, login.message(exc)) from None
            return self.send_json(result)
        if path == '/api/instagram/disconnect':
            login.cancel_current()
            with sync.SESSION_LOCK:
                for account in [body['account']] if body.get('account') else [a['id'] for a in sync.accounts()]:
                    sync.remove_account(str(account))
            return self.send_json({'ok': True})
        raise HTTPError(404, 'Operazione non trovata.')

    def profile_values(self, body):
        values = {}
        for key in ['enabled', 'posts', 'reels', 'stories', 'highlights', 'tagged', 'notify', 'social']:
            if key in body:
                values[key] = int(bool(body[key]))
        if 'account' in body:
            account = str(body['account'] or '')
            if account and account not in {a['id'] for a in sync.accounts()}:
                raise ValueError('Questo account Instagram non è più collegato.')
            values['account'] = account
        if 'interval_minutes' in body:
            value = int(body['interval_minutes'])
            if not 10 <= value <= 10080:
                raise ValueError('Scegli un intervallo da 10 minuti a 7 giorni.')
            values['interval_minutes'] = value
        return values

    def update_profile(self, ident, body):
        values = self.profile_values(body)
        with store.db() as c:
            current = c.execute('SELECT * FROM profiles WHERE id=?', (ident,)).fetchone()
            if not current:
                raise HTTPError(404, 'Profilo non trovato.')
            candidate = dict(current) | values
            if not any(candidate[k] for k in ['posts', 'reels', 'stories', 'highlights']):
                raise ValueError('Seleziona almeno un tipo di contenuto.')
            if values:
                c.execute('UPDATE profiles SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', [*values.values(), ident])
            if values.get('enabled') == 0:
                c.execute("UPDATE jobs SET state=CASE WHEN state='running' THEN 'cancel_requested' ELSE 'cancelled' END WHERE profile_id=? AND state IN ('queued','running')", (ident,))

    def get(self, path, query):
        if path == '/healthz':
            return self.send_json({'ok': True, 'service': 'InstaVault', 'version': VERSION})
        if path == '/api/me':
            token = self.authenticate()
            return self.send_json({'ok': True, 'csrf': security.csrf_token(token), 'version': VERSION})
        if path.startswith('/api/') or path.startswith('/media/'):
            self.authenticate()
        if path == '/api/instagram/login/status':
            try:
                return self.send_json(login.password_status(query.get('attempt_id', '')))
            except ValueError as exc:
                return self.send_json({'error': str(exc)}, 400)
        if path == '/api/summary':
            with store.db() as c:
                counts = dict(c.execute('''SELECT COUNT(*) AS posts,COALESCE(SUM(kind='reel'),0) AS reels,
                  COALESCE(SUM(kind='story'),0) AS stories FROM posts WHERE complete=1
                  AND profile_id IN (SELECT id FROM profiles WHERE removed=0)''').fetchone())
                jobs = [dict(r) for r in c.execute('''SELECT j.*,p.username FROM jobs j JOIN profiles p ON p.id=j.profile_id
                    WHERE state IN ('running','queued','cancel_requested') ORDER BY j.id LIMIT 100''')]
            return self.send_json({'counts': counts, 'profiles': store.profile_rows(), 'jobs': jobs,
                                   'requests': sync.request_policy.status(),
                                   'storage': store.storage_status(), 'instagram': sync.session_status(), 'settings': store.settings(),
                                   'worker': {'alive': bool(sync.worker.thread and sync.worker.thread.is_alive()), 'leader': sync.worker.leader,
                                              'draining': sync.worker.draining}})
        if path == '/api/stories':
            return self.send_json({'items': store.recent_stories(), 'now': store.now()})
        if path == '/api/posts':
            return self.send_json(store.list_posts(query))
        match = re.fullmatch(r'/api/posts/(\d+)', path)
        if match:
            with store.db() as c:
                row = c.execute('SELECT p.*,r.username,r.avatar,r.full_name FROM posts p JOIN profiles r ON p.profile_id=r.id WHERE p.id=? AND p.complete=1', (int(match[1]),)).fetchone()
                if not row:
                    raise HTTPError(404, 'Contenuto non trovato.')
                return self.send_json(store.post_json(c, row, True))
        if path == '/api/jobs':
            return self.send_json(store.list_jobs(query))
        if path == '/api/download':
            ids = [v for v in str(query.get('posts', '')).split(',') if v.isdigit()]
            rows = store.post_files(ids)
            if not rows:
                raise HTTPError(404, 'Niente da scaricare per ora.')
            return self.stream_zip(f'instavault-{len(set(ids))}-contenuti.zip', rows, by_profile=True)
        match = re.fullmatch(r'/api/profiles/(\d+)/download', path)
        if match:
            return self.download_zip(int(match[1]), str(query.get('collection', ''))[:300])
        if path == '/api/push/key':
            return self.send_json({'key': push.keys()[1]})
        match = re.fullmatch(r'/api/profiles/(\d+)/social', path)
        if match:
            return self.send_json(social.overview(int(match[1])))
        match = re.fullmatch(r'/api/profiles/(\d+)/people', path)
        if match:
            name = str(query.get('list', ''))
            if name not in social.LISTS:
                raise HTTPError(400, 'Elenco non valido.')
            offset = str(query.get('offset', '0'))
            return self.send_json(social.people(int(match[1]), name, str(query.get('q', '')), int(offset) if offset.isdigit() else 0))
        match = re.fullmatch(r'/api/profiles/(\d+)/history', path)
        if match:
            return self.send_json(store.profile_history(int(match[1])))
        match = re.fullmatch(r'/api/history/(\d+)/photo', path)
        if match:
            with store.db() as c:
                row = c.execute('SELECT avatar FROM profile_history WHERE id=?', (int(match[1]),)).fetchone()
            if not row or not row['avatar']:
                raise HTTPError(404, 'Foto non disponibile.')
            return self.file(store.safe_file(row['avatar']), cache=True)
        match = re.fullmatch(r'/api/profiles/(\d+)/collections', path)
        if match:
            return self.send_json({'items': store.collections(int(match[1]))})
        match = re.fullmatch(r'/api/profiles/(\d+)/avatar', path)
        if match:
            with store.db() as c:
                row = c.execute('SELECT avatar FROM profiles WHERE id=?', (int(match[1]),)).fetchone()
            if not row or not row['avatar']:
                raise HTTPError(404, 'Avatar non disponibile.')
            return self.file(store.safe_file(row['avatar']), cache=True)
        match = re.fullmatch(r'/media/(\d+)(/thumb)?', path)
        if match:
            with store.db() as c:
                row = c.execute('SELECT m.* FROM media m JOIN posts p ON p.id=m.post_id WHERE m.id=? AND p.complete=1', (int(match[1]),)).fetchone()
            if not row:
                raise HTTPError(404, 'File non trovato.')
            relative = (row['thumb'] or row['path']) if match[2] else row['path']
            return self.file(store.safe_file(relative, row['location']), cache=True, download=query.get('download') == '1')
        if path == '/' or path == '/index.html':
            return self.file(store.STATIC / 'index.html')
        if path in {'/app.js', '/app.css', '/icon.svg', '/sw.js', '/manifest.webmanifest'}:
            return self.file(store.STATIC / path[1:])
        raise HTTPError(404, 'Pagina non trovata.')

    def file(self, path, cache=False, download=False):
        try:
            handle = path.open('rb')
        except OSError:
            raise HTTPError(404, 'File non disponibile.') from None
        with handle:
            size = os.fstat(handle.fileno()).st_size
            etag = '"' + hashlib.sha256(f'{path.name}:{size}:{path.stat().st_mtime_ns}'.encode()).hexdigest()[:24] + '"'
            start, end, code = 0, size - 1, 200
            header = self.headers.get('Range')
            if header:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
                if not match or not any(match.groups()):
                    return self.range_error(size)
                if match[1]:
                    start = int(match[1]); end = min(int(match[2]) if match[2] else end, end)
                else:
                    start = max(0, size - int(match[2]))
                if start >= size or end < start:
                    return self.range_error(size)
                code = 206
            elif self.headers.get('If-None-Match') == etag:
                self.send_response(304); self.headers_common(); self.send_header('ETag', etag); self.end_headers(); return
            compress = not cache and not header and path.suffix in {'.js', '.css', '.svg'} and 'gzip' in self.headers.get('Accept-Encoding', '')
            data = gzip.compress(handle.read()) if compress else None
            self.send_response(code); self.headers_common()
            self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(len(data) if data is not None else max(0, end - start + 1)))
            self.send_header('Cache-Control', 'private, max-age=86400' if cache else 'no-cache')
            self.send_header('ETag', etag); self.send_header('Accept-Ranges', 'bytes')
            if header:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            if compress:
                self.send_header('Content-Encoding', 'gzip'); self.send_header('Vary', 'Accept-Encoding')
            if download:
                self.send_header('Content-Disposition', f'attachment; filename="{path.parent.name}-{path.name}"')
            self.end_headers()
            if self.command == 'HEAD':
                return
            if data is not None:
                self.wfile.write(data); return
            handle.seek(start); remaining = max(0, end - start + 1)
            while remaining and not sync.STOP.is_set():
                chunk = handle.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk); remaining -= len(chunk)

    def download_zip(self, profile_id, collection):
        # Streamed while it is built: no temporary copy on the eMMC, originals
        # stored without recompression. Files that are not reachable right now
        # (NVMe away) are listed in mancanti.txt instead of failing the download.
        with store.db() as c:
            profile = c.execute('SELECT username FROM profiles WHERE id=?', (profile_id,)).fetchone()
        if not profile:
            raise HTTPError(404, 'Profilo non trovato.')
        rows = store.profile_files(profile_id, collection)
        if not rows:
            raise HTTPError(404, 'Niente da scaricare per ora.')
        label = re.sub(r'[^A-Za-z0-9._-]+', '-', collection.replace('In evidenza · ', ''))[:60].strip('-')
        return self.stream_zip(profile['username'] + (f'-{label}' if label else '') + '.zip', rows)

    def stream_zip(self, name, rows, by_profile=False):
        self.send_response(200); self.headers_common()
        self.send_header('Content-Type', 'application/zip')
        self.send_header('Content-Disposition', f'attachment; filename="{name}"')
        self.send_header('Cache-Control', 'no-store'); self.send_header('Connection', 'close')
        self.end_headers(); self.close_connection = True
        folders = {'post': 'post', 'reel': 'reel', 'story': 'storie', 'tagged': 'taggati'}
        missing = []
        with zipfile.ZipFile(self.wfile, 'w', zipfile.ZIP_STORED) as archive:
            for row in rows:
                if sync.STOP.is_set():
                    break
                folder = ('in evidenza/' + re.sub(r'[\\/:*?"<>|]+', '-', row['collection'].replace('In evidenza · ', '')).strip()
                          if row['collection'] else folders.get(row['kind'], 'altro'))
                if by_profile:
                    folder = row['username'] + '/' + folder
                when = datetime.fromtimestamp(row['published_at'], timezone.utc)
                arcname = f'{folder}/{when:%Y-%m-%d}_{row["shortcode"]}_{row["position"] + 1}{file_suffix(row["path"])}'
                try:
                    source = store.safe_file(row['path'], row['location'])
                    info = zipfile.ZipInfo(arcname, when.timetuple()[:6]); info.compress_type = zipfile.ZIP_STORED
                    with source.open('rb') as handle, archive.open(info, 'w', force_zip64=True) as target:
                        while chunk := handle.read(256 * 1024):
                            target.write(chunk)
                except (OSError, HTTPError, ValueError):
                    missing.append(arcname)
            if missing:
                archive.writestr('mancanti.txt', 'File non raggiungibili al momento del download '
                                 '(NVMe scollegato o file mancante):\n' + '\n'.join(missing) + '\n')

    def range_error(self, size):
        self.send_response(416); self.headers_common()
        self.send_header('Content-Range', f'bytes */{size}'); self.send_header('Content-Length', '0'); self.end_headers()

    def handle_method(self):
        try:
            self.dispatch()
        except HTTPError as exc:
            self.send_json({'error': exc.message}, exc.code)
        except sqlite3.IntegrityError:
            self.send_json({'error': 'Il profilo è già presente.'}, 409)
        except (ValueError, KeyError, json.JSONDecodeError):
            self.send_json({'error': 'Controlla i dati inseriti e riprova.'}, 400)
        except FileNotFoundError:
            self.send_json({'error': 'File o archivio non disponibile.'}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send_json({'error': 'Operazione non completata. Riprova tra poco.'}, 500)

    do_GET = handle_method
    do_HEAD = handle_method
    do_POST = handle_method
    do_PATCH = handle_method


def main():
    store.initialize()
    server = ThreadingHTTPServer((os.environ.get('HOST', '0.0.0.0'), int(os.environ.get('PORT', '8096'))), Handler)
    server.daemon_threads = True
    sync.worker.start()
    def stop(*_):
        sync.STOP.set(); sync.WAKE.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    print(f'InstaVault ready · http://{server.server_address[0]}:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if sync.worker.thread:
            sync.worker.thread.join(timeout=28)


if __name__ == '__main__':
    main()
