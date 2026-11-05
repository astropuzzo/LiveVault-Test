"""Native-resolution greenhouse timelapse with a bounded, rolling MP4 archive."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import hmac
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
from zoneinfo import ZoneInfo
import mp4_view

STATE = Path(os.getenv('OPENASTRO_SERRA_STATE', '/var/lib/openastro-control/serra'))
CONFIG = Path(os.getenv('OPENASTRO_SERRA_CONFIG', '/var/lib/openastro-control/serra-camera.json'))
MOUNT = Path(os.getenv('OPENASTRO_SERRA_MOUNT', '/mnt/livevault-nvme'))
UUID = os.getenv('OPENASTRO_SERRA_UUID', '')
STORAGE_STATE = Path('/run/openastro-serra-storage.json')
INTERVALS = (1, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)
DEFAULTS = {'enabled': True, 'interval_seconds': 15, 'quota_gb': 4, 'rotation_degrees': 0}
ROTATIONS = (0, 90, 180, 270)
ROTATION_TAG = b'OpenAstro-rotation:'
HISTORY_HOURS = (10,14,18)
HISTORY_ZONE = ZoneInfo('Europe/Rome')
FPS, MAX_BATCH, RESERVE, MAX_IMAGE = 24, 300, 2 * 1024**3, 8 * 1024**2
_http = build_opener(ProxyHandler({}))
_viewers = threading.BoundedSemaphore(2)
_downloads = threading.BoundedSemaphore(1)
_previews = threading.BoundedSemaphore(1)
_plan_lock = threading.Lock()
_plan_key = _plan = None
_purge_key = os.urandom(32)


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
    with _lock('schema'):
        for table, additions in {'frames':{'rotation':'INTEGER NOT NULL DEFAULT 0','tier':"TEXT NOT NULL DEFAULT 'recent'"},
                                 'segments':{'rotation':'INTEGER NOT NULL DEFAULT 0','width':'INTEGER NOT NULL DEFAULT 2304','height':'INTEGER NOT NULL DEFAULT 1296',
                                             'tier':"TEXT NOT NULL DEFAULT 'recent'",'sample_times':"TEXT NOT NULL DEFAULT ''",'sample_seconds':'INTEGER NOT NULL DEFAULT 0'}}.items():
            columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
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
        current = settings()
        rotation = payload.get('rotation_degrees', current['rotation_degrees'])
        if type(rotation) is not int or rotation not in ROTATIONS:
            raise SerraError('Scegli una rotazione di 0, 90, 180 o 270 gradi.', 400)
        if rotation and not shutil.which('jpegtran'):
            raise SerraError('Rotazione JPEG non disponibile sul server.', 503)
        _json(STATE / 'settings.json', {**current, 'enabled': enabled, 'interval_seconds': interval, 'quota_gb': quota, 'rotation_degrees': rotation})
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


def _rotation(value=None):
    try:
        angle = int(settings()['rotation_degrees'] if value is None else value)
    except (ValueError, TypeError):
        raise SerraError('Rotazione non valida.', 400) from None
    if angle not in ROTATIONS:
        raise SerraError('Rotazione non valida.', 400)
    return angle


def _jpeg_rotation(image):
    if image[2:4] != b'\xff\xfe':
        return 0
    tag = image[6:4 + int.from_bytes(image[4:6], 'big')]
    if not tag.startswith(ROTATION_TAG):
        return 0
    return _rotation(tag[len(ROTATION_TAG):])


def rotate_jpeg(image, rotation):
    rotation = _rotation(rotation)
    if not rotation:
        return image
    try:
        result = subprocess.run(['jpegtran','-copy','all','-perfect','-rotate',str(rotation)],
                                input=image,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=8,check=True).stdout
    except (OSError, subprocess.SubprocessError):
        raise SerraError('Rotazione JPEG non riuscita: scatto non pubblicato.', 503) from None
    if len(result) > MAX_IMAGE or not result.startswith(b'\xff\xd8') or not result.endswith(b'\xff\xd9'):
        raise SerraError('Rotazione JPEG incompleta.', 503)
    tag = ROTATION_TAG + str(rotation).encode()
    # Pixels are already rotated; this comment only recovers capture metadata after a crash.
    return result[:2] + b'\xff\xfe' + struct.pack('>H',len(tag)+2) + tag + result[2:]


def photo(rotation=None):
    with _upstream('ch0.jpg') as response:
        image = response.read(MAX_IMAGE + 1)
    if len(image) > MAX_IMAGE or not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):
        raise SerraError('La camera non ha restituito una foto completa.', 502)
    return rotate_jpeg(image, _rotation(rotation))


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
            for row in list(db.execute(f"SELECT name FROM {table}{where} ORDER BY CASE tier WHEN 'recent' THEN 0 ELSE 1 END,{order}")):
                if row['name'] in protected:
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


def capture(*, recent=True):
    with _lock('capture', blocking=False), storage(write=True) as root:
        rotation = _rotation()
        image, now = photo(rotation), time.time()
        # This camera burns the clock into every JPEG. Identical bytes over
        # multiple capture intervals mean its snapshot cache has stopped.
        digest = hashlib.sha256(image).hexdigest()
        previous = _state()
        if digest != previous.get('capture_image_hash'):
            _state(capture_image_hash=digest, capture_image_changed=now)
        elif now - previous.get('capture_image_changed',now) > max(15,2*settings()['interval_seconds']):
            raise SerraError('La camera restituisce un fotogramma fermo. Scatto non archiviato; riprovo automaticamente.')
        name = str(time.time_ns()) + '.jpg'
        slot = f'{rotation}:{int(now//3600)}'
        history = _state().get('history_slot') != slot
        with _lock('media'):
            if history:
                with _db() as db:
                    begin,end = int(now//3600)*3600,(int(now//3600)+1)*3600
                    history = not (db.execute("SELECT 1 FROM frames WHERE tier='history' AND rotation=? AND ts>=? AND ts<? LIMIT 1",(rotation,begin,end)).fetchone()
                                   or db.execute("SELECT 1 FROM segments WHERE tier='history' AND rotation=? AND end>=? AND start<? LIMIT 1",(rotation,begin,end)).fetchone())
            tiers = (['recent'] if recent else []) + (['history'] if history else [])
            _room(root, len(image)*len(tiers))
            for tier in tiers:
                target = root / 'pending' / (('h' if tier=='history' else '')+name)
                temporary = target.with_suffix('.part')
                try:
                    with temporary.open('xb') as output:
                        output.write(image);output.flush();os.fsync(output.fileno())
                    os.replace(temporary, target)
                    with _db() as db:
                        db.execute('INSERT INTO frames(name,ts,size,rotation,tier) VALUES(?,?,?,?,?)', (target.name, now, len(image), rotation, tier))
                finally:
                    temporary.unlink(missing_ok=True)
        _state(last_capture=now, last_error='', history_slot=slot)
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


def compact(tier='recent'):
    batch = uuid4().hex
    temporary = batch_folder = None
    owned = False
    try:
        with _lock('encoder', blocking=False), storage(write=True) as root:
            owned = True
            with _lock('media'), _db() as db:
                rows = list(db.execute('SELECT * FROM frames WHERE batch IS NULL AND tier=? ORDER BY ts LIMIT ?', (tier,24 if tier=='history' else MAX_BATCH)))
                if not rows or (len(rows)<2 and tier!='history'):
                    return
                rotation = rows[0]['rotation']
                day = int(rows[0]['ts']//86400)
                end = next((index for index,row in enumerate(rows) if row['rotation'] != rotation or (tier=='history' and int(row['ts']//86400)!=day)),len(rows))
                rows = rows[:end]
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
                           '-filter_threads', '1', '-g', '1' if tier=='history' else '48', '-keyint_min', '1' if tier=='history' else '48', '-sc_threshold', '0',
                           *(['-bf','0','-refs','1'] if tier=='history' else []),
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
            name = ('h' if tier=='history' else '') + str(time.time_ns()) + '.mp4'
            with temporary.open('rb') as output:
                os.fsync(output.fileno())
            with _lock('media'), _db() as db:
                os.replace(temporary, root / 'segments' / name)
                db.execute('INSERT INTO segments(name,start,end,frames,size,init,duration,rotation,width,height,tier,sample_times,sample_seconds) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (name, rows[0]['ts'], rows[-1]['ts'], len(rows), (root / 'segments' / name).stat().st_size, init, len(rows) / FPS, rotation, stream['width'], stream['height'],tier,json.dumps([row['ts'] for row in rows]) if tier=='history' else '',3600 if tier=='history' else 0))
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


def window(hours, rotation=None):
    try:
        hours = int(hours)
    except (ValueError, TypeError):
        raise SerraError('Finestra temporale non valida.', 400) from None
    if not 1 <= hours <= 8760:
        raise SerraError('Scegli una finestra tra un’ora e un anno.', 400)
    with _db() as db:
        tier = 'history' if hours>72 else 'recent'
        params = (time.time()-hours*3600,_rotation(rotation))
        rows = [dict(row) for row in db.execute('SELECT * FROM segments WHERE end>=? AND rotation=? AND tier=? ORDER BY id', (*params,tier))]
        if not rows and tier=='history' and hours<=720:
            rows = [dict(row) for row in db.execute("SELECT * FROM segments WHERE end>=? AND rotation=? AND tier='recent' ORDER BY id",params)]
        return _daylight_rows(rows) if hours>720 else rows


def _daylight_rows(rows):
    """Select one actual sample per requested local hour/date, never a night fallback."""
    chosen = {}
    for row in rows:
        if row['tier'] != 'history':
            continue
        times = json.loads(row['sample_times'] or '[]')
        if len(times) != row['frames']:
            raise SerraError('Orari dello storico non disponibili: campioni esclusi dalla vista annuale.')
        for index,ts in enumerate(times):
            local = datetime.fromtimestamp(ts,HISTORY_ZONE)
            if local.hour in HISTORY_HOURS:
                key = (row['rotation'],local.date(),local.hour)
                if key not in chosen or ts < chosen[key][2]:
                    chosen[key] = (row['id'],index,ts)
    selected = {}
    for ident,index,ts in chosen.values():
        selected.setdefault(ident,[]).append((index,ts))
    result = []
    for row in rows:
        samples = sorted(selected.get(row['id'],[]))
        if samples:
            result.append({**dict(row),'sample_indices':[index for index,_ in samples],
                           'selected_times':[ts for _,ts in samples],'frames':len(samples),
                           'start':samples[0][1],'end':samples[-1][1],'duration':len(samples)/FPS})
    return result


def _build_view(root, rows):
    return mp4_view.build([root/'segments'/row['name'] for row in rows],
                          selections=[row.get('sample_indices') for row in rows])


def _revision(rows):
    return hashlib.sha256(json.dumps([(row['id'],row['name'],row['size'],row['frames'],row.get('sample_indices')) for row in rows]).encode()).hexdigest()[:20]


def maintain_history():
    """At most one old day per pass: keep 10/14/18 local time, expire after a year."""
    temporary = None
    owned = False
    try:
        if _quiescing(): return
        with _lock('encoder',blocking=False), storage() as root, _lock('media'):
            owned = True
            with _db() as db:
                candidate = db.execute("SELECT * FROM segments WHERE tier='history' AND end<? AND sample_seconds<86400 ORDER BY start LIMIT 1",(time.time()-30*86400,)).fetchone()
                rows = []
                if candidate:
                    day = int(candidate['start']//86400)*86400
                    rows = [dict(row) for row in db.execute("SELECT * FROM segments WHERE tier='history' AND rotation=? AND start>=? AND start<? ORDER BY start,id",(candidate['rotation'],day,day+86400))]
            budget = max((row['size'] for row in rows),default=0)+4*1024**2
            _room(root,budget if rows else 0,{row['name'] for row in rows})
            with _lock('retention',blocking=False), _db() as db:
                cutoff = time.time()-365*86400
                expired = False
                for table,folder,column in [('segments','segments','end'),('frames','pending','ts')]:
                    where = " AND batch IS NULL" if table=='frames' else ''
                    for row in list(db.execute(f"SELECT name FROM {table} WHERE tier='history' AND {column}<?{where}",(cutoff,))):
                        path = root/folder/row['name']
                        if not re.fullmatch(r'h\d{19,20}\.'+('jpg' if table=='frames' else 'mp4'),path.name) or path.is_symlink():
                            raise SerraError('Elemento storico non valido.')
                        path.unlink(missing_ok=True);db.execute(f'DELETE FROM {table} WHERE name=?',(row['name'],));expired=True
                db.commit()
                if expired: _state(last_history_maintenance=time.time())
                if not rows or any(row['end']<cutoff for row in rows):
                    _state(history_error='')
                    return
                samples = [(row['id'],index,ts) for row in rows for index,ts in enumerate(json.loads(row['sample_times']))]
                chosen = []
                for hour in HISTORY_HOURS:
                    candidates = [sample for sample in samples if datetime.fromtimestamp(sample[2],HISTORY_ZONE).hour==hour]
                    if candidates:
                        chosen.append(min(candidates,key=lambda sample:sample[2]%3600))
                if not chosen and samples:
                    chosen = [min(samples,key=lambda sample:abs(datetime.fromtimestamp(sample[2],HISTORY_ZONE).hour-14))]
                _state(encoding=True,reserved_bytes=budget)
                for row in rows:
                    indices = [index for ident,index,_ in chosen if ident==row['id']]
                    times = [ts for ident,_,ts in chosen if ident==row['id']]
                    path = root/'segments'/row['name']
                    if path.is_symlink() or not re.fullmatch(r'h\d{19,20}\.mp4',path.name):
                        raise SerraError('Elemento storico non valido.')
                    if not indices:
                        path.unlink();db.execute('DELETE FROM segments WHERE id=?',(row['id'],));db.commit();continue
                    if len(indices)<row['frames']:
                        temporary = path.with_name('.thin-'+uuid4().hex+'.mp4')
                        select = '+'.join(f'eq(n\\,{index})' for index in indices)
                        command = ['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-threads','1','-i',str(path),
                                   '-map','0:v:0','-an','-c:v','copy','-r',str(FPS),
                                   '-bsf:v',f'noise=amount=0:drop=not({select}),setts=pts=N*512:dts=N*512:duration=512:time_base=1/12288',
                                   '-movflags','+frag_keyframe+empty_moov+default_base_moof+negative_cts_offsets',
                                   '-fs',str(budget),'-y',str(temporary)]
                        _run_encoder(command)
                        probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-count_packets','-select_streams','v:0','-show_entries','stream=width,height,nb_read_packets','-of','json',str(temporary)],timeout=30))['streams'][0]
                        if int(probe['nb_read_packets'])!=len(indices) or (probe['width'],probe['height'])!=(row['width'],row['height']):
                            raise SerraError('Riduzione storico incompleta: originale conservato.')
                        init,size = _init_length(temporary),temporary.stat().st_size
                        os.replace(temporary,path);temporary=None
                    else:
                        init,size = row['init'],row['size']
                    db.execute('UPDATE segments SET start=?,end=?,frames=?,size=?,init=?,duration=?,sample_times=?,sample_seconds=86400 WHERE id=?',
                               (min(times),max(times),len(times),size,init,len(times)/FPS,json.dumps(times),row['id']))
                    db.commit()
                _state(history_error='',last_history_maintenance=time.time())
    except (SerraError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        if isinstance(exc,SerraError) and exc.status==409: return
        _state(history_error=str(exc) if isinstance(exc,SerraError) else 'Manutenzione storico rinviata.')
    finally:
        if temporary: temporary.unlink(missing_ok=True)
        if owned: _state(encoding=False,reserved_bytes=0)


def status():
    value = _state()
    with _db() as db:
        totals = db.execute('SELECT COUNT(*) blocks,COALESCE(SUM(frames),0) frames,COALESCE(SUM(size),0) bytes,MIN(start) oldest,MAX(end) newest FROM segments').fetchone()
        pending = db.execute('SELECT COUNT(*) n,COALESCE(SUM(size),0) bytes FROM frames').fetchone()
        orientations = [dict(row) for row in db.execute('SELECT rotation,tier,COUNT(*) blocks,SUM(frames) frames,MIN(start) oldest,MAX(end) newest FROM segments GROUP BY rotation,tier ORDER BY rotation,tier')]
        daily = _daylight_rows([dict(row) for row in db.execute("SELECT * FROM segments WHERE tier='history' AND end>=? ORDER BY id",(time.time()-365*86400,))])
        for angle in sorted({row['rotation'] for row in daily}):
            rows = [row for row in daily if row['rotation']==angle]
            orientations.append({'rotation':angle,'tier':'daylight','blocks':len(rows),
                                 'frames':sum(row['frames'] for row in rows),
                                 'oldest':min(row['start'] for row in rows),'newest':max(row['end'] for row in rows)})
    rotation = _rotation()
    online = _mounted()
    return {'ok': True, 'settings': settings(), 'storage_online': online,
            'used_bytes': totals['bytes'] + pending['bytes'], 'free_bytes': shutil.disk_usage(MOUNT).free if online else None,
            'frames': totals['frames'] + pending['n'], 'pending_frames': pending['n'], 'blocks': totals['blocks'],
            'oldest': totals['oldest'], 'newest': totals['newest'], 'output_fps': FPS, 'last_capture': value.get('last_capture'),
            'encoding': value.get('encoding', False), 'last_compact': value.get('last_compact'),
            'last_cleanup':value.get('last_cleanup'), 'last_history_maintenance':value.get('last_history_maintenance'),
            'last_error': value.get('last_error', ''), 'encode_error': value.get('encode_error', ''), 'history_error':value.get('history_error',''),
            'width': 1296 if rotation in (90,270) else 2304, 'height': 2304 if rotation in (90,270) else 1296,
            'orientations': orientations,'history':{'hourly_days':30,'daily_times':['10:00','14:00','18:00'],'max_days':365,'timezone':'Europe/Rome'}}


def _send(handler, body, content_type):
    handler.send_response(200)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'private, no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.end_headers()
    handler._serra_headers_sent = True
    handler.wfile.write(body)


def playlist(hours, rotation=None):
    rows = window(hours, rotation)
    if not rows:
        raise SerraError('Il timelapse sarà disponibile dopo i primi due scatti compressi.', 503)
    if any('sample_indices' in row for row in rows):
        raise SerraError('La vista con soli campioni diurni è disponibile come MP4 indicizzato.',400)
    lines = ['#EXTM3U', '#EXT-X-VERSION:7', '#EXT-X-PLAYLIST-TYPE:VOD', '#EXT-X-TARGETDURATION:13', f"#EXT-X-MEDIA-SEQUENCE:{rows[0]['id']}", f"#EXT-X-DISCONTINUITY-SEQUENCE:{rows[0]['id'] - 1}"]
    for index, row in enumerate(rows):
        if index:
            lines.append('#EXT-X-DISCONTINUITY')
        url = '/api/serra/segment?id=' + str(row['id'])
        lines += [f'#EXT-X-MAP:URI="{url}",BYTERANGE="{row["init"]}@0"', f'#EXTINF:{row["duration"]:.6f},',
                  f'#EXT-X-BYTERANGE:{row["size"] - row["init"]}@{row["init"]}', url]
    lines.append('#EXT-X-ENDLIST')
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


def _mp4(handler, hours, *, download=True, rotation=None):
    slots = _downloads if download else _previews
    if not slots.acquire(blocking=False):
        raise SerraError('Una riproduzione o un download MP4 è già in corso.', 429)
    process = listing = None
    try:
        with storage() as root, _lock('retention', shared=True):
            rows = window(hours, rotation)
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
            disposition = 'attachment' if download else 'inline'
            handler.send_header('Content-Disposition', f'{disposition}; filename="serra-ultime-{int(hours)}h.mp4"')
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
        slots.release()


def _export(handler, hours, rotation=None):
    try:
        daily = int(hours)>720
    except (ValueError,TypeError):
        raise SerraError('Finestra temporale non valida.',400) from None
    if daily:
        if not _downloads.acquire(blocking=False):
            raise SerraError('Un download MP4 è già in corso.',429)
        try:
            with _lock('retention',shared=True):
                info = preview_info(hours,rotation)
                return _indexed_preview(handler,parse_qs(info['url'].split('?',1)[1]),download=True)
        finally:
            _downloads.release()
    return _mp4(handler, hours, download=True, rotation=rotation)


def preview_info(hours, rotation=None):
    rotation = _rotation(rotation)
    rows = window(hours, rotation)
    if not rows:
        raise SerraError('Nessuna foto delle 10:00, 14:00 o 18:00 ancora disponibile.' if int(hours)>720 else 'Il timelapse sarà disponibile dopo i primi scatti compressi.', 503)
    return {'ok': True, 'duration': sum(row['frames'] for row in rows) / FPS,
            'sampling':'daylight' if int(hours)>720 else 'archive',
            'sample_count':sum(row['frames'] for row in rows),
            'url': f"/api/serra/preview.mp4?first={rows[0]['id']}&last={rows[-1]['id']}&rotation={rotation}&tier={rows[0]['tier']}&rev={_revision(rows)}&sampling={'daylight' if int(hours)>720 else 'archive'}&v=4"}


def _indexed_preview(handler, query, *, download=False):
    global _plan_key, _plan
    rotation = _rotation(query.get('rotation',[None])[0])
    tier = query.get('tier',['recent'])[0]
    if tier not in ('recent','history'):
        raise SerraError('Archivio non valido.',400)
    sampling = query.get('sampling',['archive'])[0]
    if sampling not in ('archive','daylight') or (sampling=='daylight' and tier!='history'):
        raise SerraError('Campionamento non valido.',400)
    try:
        first, last = int(query['first'][0]), int(query['last'][0])
    except (KeyError, ValueError, IndexError):
        info = preview_info(query.get('hours', ['24'])[0], rotation)
        handler.send_response(307)
        handler.send_header('Location', info['url'])
        handler.send_header('Content-Length', '0')
        handler.send_header('Cache-Control', 'private, no-store')
        handler.end_headers()
        return
    if first < 1 or last < first:
        raise SerraError('Finestra video non valida.', 400)
    with storage() as root:
        with _lock('retention', shared=True), _plan_lock:
            with _db() as db:
                rows = [dict(row) for row in db.execute('SELECT * FROM segments WHERE id BETWEEN ? AND ? AND rotation=? AND tier=? ORDER BY id', (first,last,rotation,tier))]
            if sampling=='daylight':
                rows = _daylight_rows(rows)
            if not rows or rows[0]['id'] != first or rows[-1]['id'] != last:
                raise SerraError('Archivio aggiornato: ricarica la finestra del timelapse.', 410)
            if query.get('rev') and not hmac.compare_digest(query['rev'][0],_revision(rows)):
                raise SerraError('Archivio aggiornato: ricarica il timelapse.',410)
            key = tuple((row['id'],row['name'],row['size'],row['frames'],row['init'],tuple(row.get('sample_indices',[]))) for row in rows)
            if key != _plan_key:
                try:
                    movie = _build_view(root,rows)
                except (ValueError, StopIteration, struct.error):
                    raise SerraError('Indice video non disponibile: scegli una finestra più breve.', 503) from None
                _plan_key, _plan = key, movie
            movie = _plan
            movie.read_allowed = lambda: not _quiescing()
        # Only metadata is retained in memory. Actual bytes come directly from
        # the original MP4 chunks, through the existing 200/206 range transport.
        connection = getattr(handler, 'connection', None)
        old_timeout = connection.gettimeout() if connection else None
        try:
            if connection:
                connection.settimeout(5)
            handler._serra_headers_sent = True
            info = {'path': movie, 'size': movie.size,'name': 'serra-timelapse.mp4', 'mime': 'video/mp4'}
            if download:
                handler.send_media_file(info,download=True)
            else:
                handler.send_media_file(info)
        finally:
            if connection:
                connection.settimeout(old_timeout)


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
            _send(handler, playlist(query.get('hours', ['24'])[0],query.get('rotation',[None])[0]), 'application/vnd.apple.mpegurl')
        elif parsed.path == '/api/serra/export.mp4':
            _export(handler, query.get('hours', ['24'])[0],query.get('rotation',[None])[0])
        elif parsed.path == '/api/serra/preview.mp4':
            _indexed_preview(handler, query)
        elif parsed.path == '/api/serra/preview-info':
            handler.send_json(preview_info(query.get('hours', ['24'])[0],query.get('rotation',[None])[0]))
        elif parsed.path == '/api/serra/segment':
            try:
                identifier = int(query.get('id', [''])[0])
            except ValueError:
                raise SerraError('Blocco non valido.', 400) from None
            with storage() as root, _lock('retention', shared=True), _db() as db:
                row = db.execute('SELECT * FROM segments WHERE id=?', (identifier,)).fetchone()
                if not row or not re.fullmatch(r'h?\d{19,20}\.mp4', row['name']):
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


def playback_report(report):
    """Keep a small private trace while diagnosing physical Android playback."""
    if not isinstance(report, dict) or report.get('build') != '20261104-7':
        raise SerraError('Diagnostica non valida.', 400)
    allowed = {'build','user_agent','position','duration','paused','ready','network','visible','hidden',
               'width','height','buffered','decoded_frames','dropped_frames','error_code','error_message','events'}
    clean = {key:value for key,value in report.items() if key in allowed}
    try:
        valid = len(json.dumps(clean, allow_nan=False)) <= 4096
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise SerraError('Diagnostica troppo grande o non valida.', 400)
    with _lock('playback-report'):
        path = STATE / 'playback-diagnostics.json'
        previous = _read(path, {'reports':[]})['reports']
        now = time.time()
        if previous and now - previous[-1]['at'] < 1:
            return {'ok':True}
        _json(path, {'reports':(previous + [{'at':now, **clean}])[-16:]})
        path.chmod(0o600)
    return {'ok':True}


def purge_archive(payload, *, execute=False):
    """Preview first; delete only that exact, authenticated selection after confirmation."""
    now = time.time()
    try:
        if not execute and payload.get('older_than_hours') is not None:
            hours = int(payload['older_than_hours'])
            if hours not in (24,48,72,168,720):
                raise ValueError()
            start, end = 0.0, now-hours*3600
        else:
            start, end = float(payload.get('from_ts',0)), float(payload['to_ts'])
        if not execute and math.isfinite(end):
            # A local end-of-day selection may include hours not recorded yet.
            # Freeze the preview at now; execution still validates this exact range.
            end = min(end,now)
        expires = float(payload['expires_at']) if execute else now+300
        if not all(math.isfinite(v) for v in (start,end,expires)) or not 0 <= start < end <= now+1 or expires < now or expires > now+301:
            raise ValueError()
    except (ValueError,TypeError,KeyError,OverflowError):
        raise SerraError('Periodo non valido o anteprima scaduta. Ricalcola la pulizia.',400) from None
    if _quiescing():
        raise SerraError('Pulizia sospesa durante la rimozione del disco.',409)
    with storage() as root, _lock('media'), _lock('retention',blocking=False,shared=not execute), _db() as db:
        scope = '' if payload.get('include_history') is True else " AND tier='recent'"
        photos = list(db.execute('SELECT name,size,ts FROM frames WHERE batch IS NULL AND ts>=? AND ts<=?'+scope+' ORDER BY name',(start,end)))
        blocks = list(db.execute('SELECT id,name,size,frames,start,end FROM segments WHERE start>=? AND end<=?'+scope+' ORDER BY id',(start,end)))
        targets = [('frames','pending',row) for row in photos]+[('segments','segments',row) for row in blocks]
        for _,folder,row in targets:
            suffix = 'jpg' if folder=='pending' else 'mp4'
            path = root/folder/row['name']
            if not re.fullmatch(r'h?\d{19,20}\.'+suffix,row['name']) or path.is_symlink() or not path.is_file():
                raise SerraError('Archivio cambiato: aggiorna la pulizia prima di confermare.',409)
        include_history = payload.get('include_history') is True
        encoded = json.dumps([start,end,expires,include_history,[[table,dict(row)] for table,_,row in targets]],sort_keys=True).encode()
        token = hmac.new(_purge_key,encoded,hashlib.sha256).hexdigest()
        result = {'ok':True,'from_ts':start,'to_ts':end,'expires_at':expires,'token':token,'include_history':include_history,
                  'photos':len(photos),'blocks':len(blocks),'frames':len(photos)+sum(row['frames'] for row in blocks),
                  'bytes':sum(row['size'] for _,_,row in targets)}
        if execute:
            if not hmac.compare_digest(str(payload.get('token','')),token):
                raise SerraError('Archivio cambiato: ricalcola i file da eliminare.',409)
            for table,folder,row in targets:
                try:
                    (root/folder/row['name']).unlink()
                except OSError:
                    db.commit()
                    raise SerraError('Pulizia interrotta: ricalcola i file rimasti.',503) from None
                db.execute(f'DELETE FROM {table} WHERE name=?',(row['name'],))
            db.commit()
            _state(last_cleanup=now)
        return result


def handle_post(payload):
    action = payload.get('action')
    if action == 'settings':
        return set_settings(payload)
    if action == 'capture':
        return capture()
    if action == 'flush':
        _state(flush_requested=True)
        return {'ok': True}
    if action == 'playback-report':
        return playback_report(payload.get('report'))
    if action in ('cleanup-preview','cleanup-delete'):
        return purge_archive(payload,execute=action=='cleanup-delete')
    raise SerraError('Operazione non valida.', 400)


def recover():
    with _lock('encoder', blocking=False), storage(write=True) as root, _lock('media'), _db() as db:
        for row in list(db.execute('SELECT name FROM segments')):
            if not (root / 'segments' / row['name']).is_file():
                db.execute('DELETE FROM segments WHERE name=?', (row['name'],))
        # A committed MP4 supersedes its pending JPEGs even if power failed during cleanup.
        for row in list(db.execute('SELECT * FROM frames')):
            published = db.execute('SELECT 1 FROM segments WHERE start<=? AND end>=? AND tier=? AND rotation=?', (row['ts'], row['ts'],row['tier'],row['rotation'])).fetchone()
            path = root / 'pending' / row['name']
            if published or not path.is_file():
                path.unlink(missing_ok=True)
                db.execute('DELETE FROM frames WHERE name=?', (row['name'],))
        db.execute('UPDATE frames SET batch=NULL')
        for path in (root / 'pending').glob('*.jpg'):
            if re.fullmatch(r'h?\d{19,20}\.jpg', path.name) and not path.is_symlink():
                with path.open('rb') as source:
                    rotation = _jpeg_rotation(source.read(64))
                db.execute('INSERT OR IGNORE INTO frames(name,ts,size,rotation,tier) VALUES(?,?,?,?,?)', (path.name, int(path.stem.lstrip('h')) / 1e9, path.stat().st_size, rotation,'history' if path.name.startswith('h') else 'recent'))
        # Incomplete output never replaces committed segments.
        for path in (root / 'segments').glob('.encode-*.mp4'):
            path.unlink(missing_ok=True)
        for path in (root / 'segments').glob('.thin-*.mp4'):
            if re.fullmatch(r'\.thin-[a-f0-9]{32}\.mp4',path.name): path.unlink(missing_ok=True)
        for folder in (root / 'pending').glob('.batch-*'):
            if folder.is_dir() and not folder.is_symlink() and re.fullmatch(r'\.batch-[a-f0-9]{32}', folder.name):
                for path in folder.glob('??????.jpg'):
                    path.unlink()
                folder.rmdir()
        for path in (root / 'pending').glob('*.part'):
            if re.fullmatch(r'h?\d{19,20}\.part', path.name):
                path.unlink()
        for path in (root / 'segments').glob('*.mp4'):
            if re.fullmatch(r'h?\d{19,20}\.mp4', path.name) and not db.execute('SELECT 1 FROM segments WHERE name=?', (path.name,)).fetchone():
                path.unlink()


def collector():
    _state(encoding=False, reserved_bytes=0)
    encoder = None
    next_capture = 0.0
    retry_encode = 0.0
    recovered = False
    previous_config = None
    last_cleanup = 0.0
    last_history = 0.0
    retry_history = 0.0
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
            history_due = _state().get('history_slot') != f"{_rotation(config.get('rotation_degrees',0))}:{int(time.time()//3600)}"
            if config['enabled'] and (now >= next_capture or (history_due and now>=retry_history)):
                try:
                    recent = now >= next_capture
                    capture(recent=recent)
                    if recent: next_capture = now + config['interval_seconds']
                except (SerraError, OSError) as exc:
                    _state(last_error=str(exc) if isinstance(exc, SerraError) else 'Scrittura scatto non riuscita.')
                    next_capture = now + 5
                    retry_history = now + 5
            if encoder is None or not encoder.is_alive():
                with _db() as db:
                    pending = db.execute("SELECT COUNT(*) n,MIN(ts) oldest FROM frames WHERE tier='recent'").fetchone()
                    history_pending = db.execute("SELECT COUNT(*) n,MIN(ts) oldest FROM frames WHERE tier='history'").fetchone()
                    count = db.execute("SELECT COUNT(*) FROM segments WHERE tier='recent' AND rotation=?",(_rotation(config.get('rotation_degrees',0)),)).fetchone()[0]
                state = _state()
                due = pending['n'] >= 120 or (pending['oldest'] and time.time() - pending['oldest'] >= 300) or not count or state.get('flush_requested')
                if history_pending['n'] and now >= retry_encode:
                    encoder = threading.Thread(target=compact,kwargs={'tier':'history'},name='serra-history-encoder',daemon=True)
                    encoder.start();retry_encode=now+10
                elif pending['n'] >= 2 and due and now >= retry_encode:
                    _state(flush_requested=False)
                    encoder = threading.Thread(target=compact, name='serra-encoder', daemon=True)
                    encoder.start()
                    retry_encode = now + 10
                elif now-last_history>=60:
                    encoder = threading.Thread(target=maintain_history,name='serra-history-maintenance',daemon=True)
                    encoder.start();last_history=now
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
