"""Native-resolution greenhouse timelapse with a bounded, rolling MP4 archive."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

STATE = Path(os.getenv('OPENASTRO_SERRA_STATE', '/var/lib/openastro-control/serra'))
CONFIG = Path(os.getenv('OPENASTRO_SERRA_CONFIG', '/var/lib/openastro-control/serra-camera.json'))
MOUNT = Path(os.getenv('OPENASTRO_SERRA_MOUNT', '/mnt/livevault-nvme'))
UUID = os.getenv('OPENASTRO_SERRA_UUID', '')
STORAGE_STATE = Path('/run/openastro-serra-storage.json')
INTERVALS = (1, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)
DEFAULTS = {'enabled': True, 'interval_seconds': 15, 'quota_gb': 4}
FPS, MAX_BATCH, RESERVE, MAX_IMAGE = 24, 300, 2 * 1024**3, 8 * 1024**2
_http = build_opener(ProxyHandler({}))
_viewers = threading.BoundedSemaphore(2)
_downloads = threading.BoundedSemaphore(1)


class SerraError(Exception):
    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


def _read(path: Path, default=None):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else dict(default or {})
    except (OSError, ValueError):
        return dict(default or {})


def _json(path: Path, value: dict):
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex)
    try:
        with temporary.open('x', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _lock(name, *, blocking=True, shared=False):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o750)
    with (STATE / (name + '.lock')).open('a') as handle:
        try:
            fcntl.flock(handle, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise SerraError('Operazione già in corso. Riprova tra pochi secondi.', 409) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _state(**changes):
    with _lock('state'):
        value = _read(STATE / 'state.json')
        if changes:
            value.update(changes)
            _json(STATE / 'state.json', value)
        return value


@contextmanager
def _db():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o750)
    connection = sqlite3.connect(STATE / 'archive.sqlite3', timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA busy_timeout=10000')
    connection.execute('CREATE TABLE IF NOT EXISTS frames (name TEXT PRIMARY KEY, ts REAL, size INTEGER, batch TEXT)')
    connection.execute('CREATE TABLE IF NOT EXISTS segments (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, start REAL, end REAL, frames INTEGER, size INTEGER, init INTEGER, duration REAL)')
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def settings():
    return {**DEFAULTS, **_read(STATE / 'settings.json')}


def set_settings(payload):
    interval, enabled, quota = (payload.get(k) for k in ('interval_seconds', 'enabled', 'quota_gb'))
    if type(interval) is not int or interval not in INTERVALS or type(enabled) is not bool or type(quota) is not int or not 1 <= quota <= 16:
        raise SerraError('Intervallo, stato o limite di archivio non valido.', 400)
    with _lock('settings'):
        _json(STATE / 'settings.json', {'enabled': enabled, 'interval_seconds': interval, 'quota_gb': quota})
    return {'ok': True}


def _mounted():
    if not UUID or not MOUNT.is_mount() or MOUNT.is_symlink():
        return False
    try:
        output = subprocess.check_output(['findmnt', '-rn', '-o', 'UUID', '--target', str(MOUNT)], timeout=3, text=True)
        return output.strip() == UUID
    except (OSError, subprocess.SubprocessError):
        return False


def _quiescing():
    return _read(STORAGE_STATE).get('mode') in {'quiesce', 'buffer'}


@contextmanager
def storage(*, write=False):
    if not _mounted():
        raise SerraError('NVMe scollegato: archivio e acquisizione in attesa.')
    descriptor = os.open(MOUNT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(descriptor).st_dev == MOUNT.parent.stat().st_dev or not _mounted():
            raise SerraError('NVMe non disponibile.')
        if write and (_quiescing() or shutil.disk_usage(MOUNT).free < RESERVE):
            raise SerraError('Acquisizione in attesa: NVMe in espulsione o meno di 2 GB liberi.')
        root = MOUNT / 'serra'
        if root.is_symlink():
            raise SerraError('Percorso archivio non valido.')
        if write:
            root.mkdir(exist_ok=True)
            for name in ('pending', 'segments'):
                child = root / name
                if child.is_symlink():
                    raise SerraError('Percorso archivio non valido.')
                child.mkdir(exist_ok=True)
        yield root
    finally:
        os.close(descriptor)


def _upstream(resource, **params):
    config = _read(CONFIG)
    key, hostname = config.get('api_key', ''), config.get('hostname', '')
    if not key or not re.fullmatch(r'[A-Za-z0-9.-]+', hostname):
        raise SerraError('Collegamento alla camera da configurare.')
    hosts = [hostname]
    fallback = config.get('fallback_ip', '')
    if re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', fallback):
        hosts.append(fallback)
    query = urlencode({**params, 'token': key})
    for index, host in enumerate(hosts):
        try:
            # This uhttpd build omits custom CGI headers. Token is kept server-side.
            return _http.open(Request(f'http://{host}/x/{resource}?{query}'), timeout=8)
        except HTTPError as exc:
            raise SerraError(f'La camera risponde con errore HTTP {exc.code}.', 502) from None
        except URLError as exc:
            if isinstance(exc.reason, socket.gaierror) and index + 1 < len(hosts):
                continue
            raise SerraError('Camera non raggiungibile: controllo Wi-Fi in corso.', 502) from None
        except (OSError, TimeoutError):
            raise SerraError('La camera non risponde in tempo.', 502) from None
    raise SerraError('Camera non raggiungibile.', 502)


def photo():
    with _upstream('ch0.jpg') as response:
        image = response.read(MAX_IMAGE + 1)
    if len(image) > MAX_IMAGE or not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):
        raise SerraError('La camera non ha restituito una foto completa.', 502)
    return image


def _usage(root):
    return sum(p.stat().st_size for folder in ('pending', 'segments') for p in (root / folder).iterdir() if p.is_file() and not p.is_symlink())


def _room(root, needed, protected=()):
    """Caller holds media lock; delete only oldest media belonging to this archive."""
    limit = settings()['quota_gb'] * 1024**3
    used = _usage(root)
    actual_output = sum(p.stat().st_size for p in (root / 'segments').glob('.encode-*.mp4'))
    needed += max(0, _state().get('reserved_bytes', 0) - actual_output)
    if used + needed <= limit:
        return
    # Exports hold a shared lock: do not invalidate their input while remuxing.
    with _lock('retention', blocking=False), _db() as db:
        for table, folder in (('segments', 'segments'), ('frames', 'pending')):
            order = 'start' if table == 'segments' else 'ts'
            where = '' if table == 'segments' else ' WHERE batch IS NULL'
            for row in list(db.execute(f'SELECT name FROM {table}{where} ORDER BY {order}')):
                if table == 'frames' and row['name'] in protected:
                    continue
                target = root / folder / row['name']
                if target.is_symlink():
                    raise SerraError('Elemento archivio non valido.')
                size = target.stat().st_size if target.exists() else 0
                target.unlink(missing_ok=True)
                db.execute(f'DELETE FROM {table} WHERE name=?', (row['name'],))
                used -= size
                if used + needed <= limit:
                    return
    raise SerraError('Archivio al limite: attendo la fine della compressione o del download.')


def capture():
    with _lock('capture', blocking=False), storage(write=True) as root:
        image, now = photo(), time.time()
        name = str(time.time_ns()) + '.jpg'
        with _lock('media'):
            _room(root, len(image))
            target = root / 'pending' / name
            temporary = target.with_suffix('.part')
            try:
                with temporary.open('xb') as output:
                    output.write(image)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, target)
                with _db() as db:
                    db.execute('INSERT INTO frames(name,ts,size) VALUES(?,?,?)', (name, now, len(image)))
            finally:
                temporary.unlink(missing_ok=True)
        _state(last_capture=now, last_error='')
        return {'ok': True}


def _init_length(path):
    with path.open('rb') as source:
        offset = 0
        while True:
            header = source.read(8)
            if len(header) != 8:
                raise SerraError('Struttura MP4 non valida.')
            size, kind = struct.unpack('>I4s', header)
            head = 8
            if size == 1:
                size = struct.unpack('>Q', source.read(8))[0]
                head = 16
            if kind == b'moof':
                return offset
            if size < head:
                raise SerraError('Struttura MP4 non valida.')
            source.seek(size - head, 1)
            offset += size


def _run_encoder(command):
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 900
    try:
        while process.poll() is None:
            if _quiescing() or time.monotonic() > deadline:
                raise SerraError('Compressione rinviata. Gli scatti in attesa saranno ripresi.')
            time.sleep(.2)
        if process.returncode:
            raise SerraError('Compressione non riuscita. Nuovo tentativo automatico.')
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def compact():
    batch = uuid4().hex
    temporary = batch_folder = None
    owned = False
    try:
        with _lock('encoder', blocking=False), storage(write=True) as root:
            owned = True
            with _lock('media'), _db() as db:
                rows = list(db.execute('SELECT * FROM frames WHERE batch IS NULL ORDER BY ts LIMIT ?', (MAX_BATCH,)))
                if len(rows) < 2:
                    return
                budget = sum(row['size'] for row in rows) + 4 * 1024**2
                _room(root, budget, {row['name'] for row in rows})
                db.executemany('UPDATE frames SET batch=? WHERE name=?', [(batch, row['name']) for row in rows])
                _state(encoding=True, reserved_bytes=budget, encode_error='')
            temporary = root / 'segments' / ('.encode-' + batch + '.mp4')
            batch_folder = root / 'pending' / ('.batch-' + batch)
            batch_folder.mkdir()
            for index, row in enumerate(rows):
                os.link(root / 'pending' / row['name'], batch_folder / f'{index:06d}.jpg')
            command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-threads', '1',
                           '-framerate', str(FPS), '-i', str(batch_folder / '%06d.jpg'), '-an',
                           '-fps_mode', 'cfr', '-frames:v', str(len(rows)), '-c:v', 'libx264',
                           '-preset', 'veryfast', '-crf', '28', '-pix_fmt', 'yuv420p', '-threads', '1',
                           '-filter_threads', '1', '-g', '48', '-keyint_min', '48', '-sc_threshold', '0',
                           '-movflags', '+frag_keyframe+empty_moov+default_base_moof+negative_cts_offsets',
                           '-fs', str(budget), '-y', str(temporary)]
            _run_encoder(command)
            # Demux and count every encoded sample; no second video decode is necessary.
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_packets',
                                                        '-show_entries', 'stream=nb_read_packets,width,height', '-of', 'json', str(temporary)], timeout=60))
            stream = probe['streams'][0]
            if int(stream.get('nb_read_packets', 0)) != len(rows):
                raise SerraError('Compressione incompleta: scatti originali mantenuti.')
            init = _init_length(temporary)
            name = str(time.time_ns()) + '.mp4'
            with temporary.open('rb') as output:
                os.fsync(output.fileno())
            with _lock('media'), _db() as db:
                os.replace(temporary, root / 'segments' / name)
                db.execute('INSERT INTO segments(name,start,end,frames,size,init,duration) VALUES(?,?,?,?,?,?,?)',
                           (name, rows[0]['ts'], rows[-1]['ts'], len(rows), (root / 'segments' / name).stat().st_size, init, len(rows) / FPS))
                # Publish the index before cleanup. Crash recovery removes duplicate pending rows.
                db.commit()
                for row in rows:
                    (root / 'pending' / row['name']).unlink(missing_ok=True)
                db.executemany('DELETE FROM frames WHERE name=?', [(row['name'],) for row in rows])
            _state(last_compact=time.time(), native_width=stream['width'], native_height=stream['height'], encode_error='')
    except (SerraError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        _state(encode_error=str(exc) if isinstance(exc, SerraError) else 'Compressione in attesa. Gli scatti sono conservati.')
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
        if batch_folder and batch_folder.is_dir():
            for path in batch_folder.glob('??????.jpg'):
                path.unlink()
            batch_folder.rmdir()
        with _db() as db:
            db.execute('UPDATE frames SET batch=NULL WHERE batch=?', (batch,))
        if owned:
            _state(encoding=False, reserved_bytes=0)


def window(hours):
    try:
        hours = int(hours)
    except (ValueError, TypeError):
        raise SerraError('Finestra temporale non valida.', 400) from None
    if not 1 <= hours <= 720:
        raise SerraError('Scegli una finestra tra 1 e 720 ore.', 400)
    with _db() as db:
        return [dict(row) for row in db.execute('SELECT * FROM segments WHERE end>=? ORDER BY id', (time.time() - hours * 3600,))]


def status():
    value = _state()
    with _db() as db:
        totals = db.execute('SELECT COUNT(*) blocks,COALESCE(SUM(frames),0) frames,COALESCE(SUM(size),0) bytes,MIN(start) oldest,MAX(end) newest FROM segments').fetchone()
        pending = db.execute('SELECT COUNT(*) n,COALESCE(SUM(size),0) bytes FROM frames').fetchone()
    online = _mounted()
    return {'ok': True, 'settings': settings(), 'storage_online': online,
            'used_bytes': totals['bytes'] + pending['bytes'], 'free_bytes': shutil.disk_usage(MOUNT).free if online else None,
            'frames': totals['frames'] + pending['n'], 'pending_frames': pending['n'], 'blocks': totals['blocks'],
            'oldest': totals['oldest'], 'newest': totals['newest'], 'output_fps': FPS, 'last_capture': value.get('last_capture'),
            'encoding': value.get('encoding', False), 'last_compact': value.get('last_compact'),
            'last_error': value.get('last_error', ''), 'encode_error': value.get('encode_error', ''),
            'width': value.get('native_width', 2304), 'height': value.get('native_height', 1296)}


def _send(handler, body, content_type):
    handler.send_response(200)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'private, no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.end_headers()
    handler._serra_headers_sent = True
    handler.wfile.write(body)


def playlist(hours):
    rows = window(hours)
    if not rows:
        raise SerraError('Il timelapse sarà disponibile dopo i primi due scatti compressi.', 503)
    lines = ['#EXTM3U', '#EXT-X-VERSION:7', '#EXT-X-TARGETDURATION:13', f"#EXT-X-MEDIA-SEQUENCE:{rows[0]['id']}", f"#EXT-X-DISCONTINUITY-SEQUENCE:{rows[0]['id'] - 1}"]
    for index, row in enumerate(rows):
        if index:
            lines.append('#EXT-X-DISCONTINUITY')
        url = '/api/serra/segment?id=' + str(row['id'])
        lines += [f'#EXT-X-MAP:URI="{url}",BYTERANGE="{row["init"]}@0"', f'#EXTINF:{row["duration"]:.6f},',
                  f'#EXT-X-BYTERANGE:{row["size"] - row["init"]}@{row["init"]}', url]
    return ('\n'.join(lines) + '\n').encode()


def _live(handler):
    if not _viewers.acquire(blocking=False):
        raise SerraError('La diretta è già aperta su due dispositivi.', 429)
    try:
        with _upstream('ch0.mjpg', f=3, q=65) as response:
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('multipart/x-mixed-replace'):
                raise SerraError('Diretta non disponibile.', 502)
            handler.send_response(200)
            handler.send_header('Content-Type', content_type)
            handler.send_header('Cache-Control', 'private, no-store')
            handler.send_header('Connection', 'close')
            handler.end_headers()
            handler._serra_headers_sent = True
            handler.close_connection = True
            try:
                while handler.authenticated_session():
                    chunk = response.read1(32768)
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
            except (OSError, TimeoutError):
                pass
    finally:
        _viewers.release()


def _export(handler, hours):
    if not _downloads.acquire(blocking=False):
        raise SerraError('Un download MP4 è già in corso.', 429)
    process = listing = None
    try:
        with storage() as root, _lock('retention', shared=True):
            rows = window(hours)
            if not rows:
                raise SerraError('Non ci sono ancora blocchi MP4 nella finestra scelta.', 404)
            with tempfile.NamedTemporaryFile(mode='w', suffix='.ffconcat', dir=STATE, delete=False) as file:
                file.write('ffconcat version 1.0\n')
                for row in rows:
                    file.write(f"file '{root / 'segments' / row['name']}'\n")
                listing = Path(file.name)
            command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing),
                       '-map', '0:v:0', '-c', 'copy', '-an', '-movflags', '+frag_keyframe+empty_moov+default_base_moof', '-f', 'mp4', 'pipe:1']
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            first = process.stdout.read(32768)
            if not first:
                raise SerraError('Preparazione MP4 non riuscita.', 502)
            handler.send_response(200)
            handler.send_header('Content-Type', 'video/mp4')
            handler.send_header('Content-Disposition', f'attachment; filename="serra-ultime-{int(hours)}h.mp4"')
            handler.send_header('Cache-Control', 'private, no-store')
            handler.send_header('Connection', 'close')
            handler.end_headers()
            handler._serra_headers_sent = True
            handler.close_connection = True
            try:
                chunk = first
                while chunk and handler.authenticated_session() and not _quiescing():
                    handler.wfile.write(chunk)
                    chunk = process.stdout.read(65536)
            except (OSError, TimeoutError):
                pass
    finally:
        if process:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()
        if listing:
            listing.unlink(missing_ok=True)
        _downloads.release()


def handle_get(handler, parsed):
    query = parse_qs(parsed.query)
    try:
        if parsed.path == '/api/serra/status':
            handler.send_json(status())
        elif parsed.path == '/api/serra/live':
            _live(handler)
        elif parsed.path == '/api/serra/photo':
            _send(handler, photo(), 'image/jpeg')
        elif parsed.path == '/api/serra/playlist.m3u8':
            _send(handler, playlist(query.get('hours', ['24'])[0]), 'application/vnd.apple.mpegurl')
        elif parsed.path == '/api/serra/export.mp4':
            _export(handler, query.get('hours', ['24'])[0])
        elif parsed.path == '/api/serra/segment':
            try:
                identifier = int(query.get('id', [''])[0])
            except ValueError:
                raise SerraError('Blocco non valido.', 400) from None
            with storage() as root, _lock('retention', shared=True), _db() as db:
                row = db.execute('SELECT * FROM segments WHERE id=?', (identifier,)).fetchone()
                if not row or not re.fullmatch(r'\d{19,20}\.mp4', row['name']):
                    raise SerraError('Blocco scaduto: aggiorna la finestra del timelapse.', 404)
                path = root / 'segments' / row['name']
                if path.is_symlink() or not path.is_file():
                    raise SerraError('Blocco non disponibile.', 404)
                handler.send_media_file({'path': path, 'name': path.name, 'size': path.stat().st_size, 'mime': 'video/mp4'})
        else:
            raise SerraError('Risorsa non trovata.', 404)
    except SerraError as exc:
        handler.send_json({'ok': False, 'error': str(exc)}, exc.status)
    except (OSError, sqlite3.Error):
        if not getattr(handler, '_serra_headers_sent', False):
            handler.send_json({'ok': False, 'error': 'Risorsa temporaneamente non disponibile.'}, 503)


def handle_post(payload):
    action = payload.get('action')
    if action == 'settings':
        return set_settings(payload)
    if action == 'capture':
        return capture()
    if action == 'flush':
        _state(flush_requested=True)
        return {'ok': True}
    raise SerraError('Operazione non valida.', 400)


def recover():
    with _lock('encoder', blocking=False), storage(write=True) as root, _lock('media'), _db() as db:
        for row in list(db.execute('SELECT name FROM segments')):
            if not (root / 'segments' / row['name']).is_file():
                db.execute('DELETE FROM segments WHERE name=?', (row['name'],))
        # A committed MP4 supersedes its pending JPEGs even if power failed during cleanup.
        for row in list(db.execute('SELECT * FROM frames')):
            published = db.execute('SELECT 1 FROM segments WHERE start<=? AND end>=?', (row['ts'], row['ts'])).fetchone()
            path = root / 'pending' / row['name']
            if published or not path.is_file():
                path.unlink(missing_ok=True)
                db.execute('DELETE FROM frames WHERE name=?', (row['name'],))
        db.execute('UPDATE frames SET batch=NULL')
        for path in (root / 'pending').glob('*.jpg'):
            if re.fullmatch(r'\d{19,20}\.jpg', path.name) and not path.is_symlink():
                db.execute('INSERT OR IGNORE INTO frames(name,ts,size) VALUES(?,?,?)', (path.name, int(path.stem) / 1e9, path.stat().st_size))
        # Incomplete output never replaces committed segments.
        for path in (root / 'segments').glob('.encode-*.mp4'):
            path.unlink(missing_ok=True)
        for folder in (root / 'pending').glob('.batch-*'):
            if folder.is_dir() and not folder.is_symlink() and re.fullmatch(r'\.batch-[a-f0-9]{32}', folder.name):
                for path in folder.glob('??????.jpg'):
                    path.unlink()
                folder.rmdir()
        for path in (root / 'pending').glob('*.part'):
            if re.fullmatch(r'\d{19,20}\.part', path.name):
                path.unlink()
        for path in (root / 'segments').glob('*.mp4'):
            if re.fullmatch(r'\d{19,20}\.mp4', path.name) and not db.execute('SELECT 1 FROM segments WHERE name=?', (path.name,)).fetchone():
                path.unlink()


def collector():
    _state(encoding=False, reserved_bytes=0)
    encoder = None
    next_capture = 0.0
    retry_encode = 0.0
    recovered = False
    previous_config = None
    last_cleanup = 0.0
    while True:
        try:
            now = time.monotonic()
            config = settings()
            if config != previous_config:
                next_capture = 0.0
                previous_config = config
            if not recovered:
                recover()
                recovered = True
            if config['enabled'] and now >= next_capture:
                try:
                    capture()
                    next_capture = now + config['interval_seconds']
                except (SerraError, OSError) as exc:
                    _state(last_error=str(exc) if isinstance(exc, SerraError) else 'Scrittura scatto non riuscita.')
                    next_capture = now + 5
            if encoder is None or not encoder.is_alive():
                with _db() as db:
                    pending = db.execute('SELECT COUNT(*) n,MIN(ts) oldest FROM frames').fetchone()
                    count = db.execute('SELECT COUNT(*) FROM segments').fetchone()[0]
                state = _state()
                due = pending['n'] >= 120 or (pending['oldest'] and time.time() - pending['oldest'] >= 300) or not count or state.get('flush_requested')
                if pending['n'] >= 2 and due and now >= retry_encode:
                    _state(flush_requested=False)
                    encoder = threading.Thread(target=compact, name='serra-encoder', daemon=True)
                    encoder.start()
                    retry_encode = now + 10
            if now - last_cleanup >= 5:
                with storage() as root, _lock('media'):
                    _room(root, 0)
                last_cleanup = now
        except Exception as exc:
            _state(last_error=str(exc) if isinstance(exc, SerraError) else 'Acquisizione in attesa: nuovo tentativo automatico.')
            recovered = False
            time.sleep(3)
        time.sleep(.25)


if __name__ == '__main__':
    collector()
