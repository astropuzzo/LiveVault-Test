#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid as uuidlib

MEDIA_ROOT = Path('/srv/openastro-media')
MANAGER = Path('/usr/local/sbin/openastro-media-manager')
CREDENTIALS = Path('/etc/openastro-media-credentials.json')
STATE_ROOT = Path('/var/lib/openastro-control')
THUMB_ROOT = STATE_ROOT / 'media-thumbs'
DB_PATH = STATE_ROOT / 'media.sqlite3'
UUID_RE = re.compile(r'^[A-Za-z0-9._:-]{2,128}$')
MAX_LIBRARY_FILES = 20000
SCAN_TTL = 60
_LIBRARY_CACHE: dict[str, tuple[float, dict]] = {}
_DB_INIT_LOCK = threading.Lock()
_STREAM_LOCK = threading.Lock()
_ACTIVE_STREAMS: dict[str, dict] = {}

VIDEO_EXT = {'.mp4','.mkv','.avi','.mov','.m4v','.webm','.ts','.m2ts','.mts','.wmv','.flv','.mpg','.mpeg'}
AUDIO_EXT = {'.mp3','.flac','.aac','.m4a','.wav','.ogg','.opus','.wma','.alac'}
IMAGE_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tif','.tiff','.heic','.avif'}
DOC_EXT = {'.pdf','.txt','.md','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.epub'}
ARCHIVE_EXT = {'.zip','.7z','.rar','.tar','.gz','.bz2','.xz'}


def _run(args: list[str], timeout: int = 8) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(args, 124, '', '')


def valid_uuid(value: str) -> bool:
    return bool(UUID_RE.fullmatch(value or ''))


def _db() -> sqlite3.Connection:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA foreign_keys=ON')
    with _DB_INIT_LOCK:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS media_devices (
            uuid TEXT PRIMARY KEY,
            label TEXT NOT NULL DEFAULT 'USB',
            model TEXT NOT NULL DEFAULT '',
            fstype TEXT NOT NULL DEFAULT '',
            size INTEGER NOT NULL DEFAULT 0,
            last_seen INTEGER NOT NULL DEFAULT 0,
            last_scan INTEGER NOT NULL DEFAULT 0,
            total_files INTEGER NOT NULL DEFAULT 0,
            total_bytes INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS media_items (
            uuid TEXT NOT NULL,
            path TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            mime TEXT NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            modified INTEGER NOT NULL DEFAULT 0,
            duration REAL,
            width INTEGER,
            height INTEGER,
            video_codec TEXT,
            audio_codec TEXT,
            bit_rate INTEGER,
            scan_token INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (uuid, path)
        );
        CREATE INDEX IF NOT EXISTS media_items_uuid_category ON media_items(uuid, category);
        CREATE INDEX IF NOT EXISTS media_items_uuid_modified ON media_items(uuid, modified DESC);
        CREATE TABLE IF NOT EXISTS media_playback (
            uuid TEXT NOT NULL,
            path TEXT NOT NULL,
            position REAL NOT NULL DEFAULT 0,
            duration REAL NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            last_played INTEGER NOT NULL DEFAULT 0,
            play_count INTEGER NOT NULL DEFAULT 0,
            client TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (uuid, path)
        );
        CREATE TABLE IF NOT EXISTS media_favorites (
            uuid TEXT NOT NULL,
            path TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (uuid, path)
        );
        CREATE TABLE IF NOT EXISTS media_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            kind TEXT NOT NULL,
            uuid TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS media_events_ts ON media_events(ts DESC);
        ''')
        conn.commit()
    return conn


def _discover() -> list[dict]:
    result = _run([str(MANAGER), 'list'])
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout or '[]')
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _service(name: str) -> str:
    result = _run(['systemctl', 'is-active', name], 3)
    return result.stdout.strip() or 'unknown'


def _remember_device(item: dict) -> None:
    now = int(time.time())
    with _db() as conn:
        conn.execute('''
            INSERT INTO media_devices(uuid,label,model,fstype,size,last_seen)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(uuid) DO UPDATE SET label=excluded.label,model=excluded.model,
                fstype=excluded.fstype,size=excluded.size,last_seen=excluded.last_seen
        ''', (item['uuid'], item.get('label') or 'USB', item.get('model') or '', item.get('fstype') or '', int(item.get('size') or 0), now))


def _mounted_uuids() -> set[str]:
    result = set()
    for item in _discover():
        try:
            if os.path.ismount(Path(item['mountpoint'])):
                result.add(str(item['uuid']))
        except (KeyError, OSError):
            pass
    return result


def status() -> dict:
    devices = []
    present = _discover()
    present_ids = set()
    for item in present:
        present_ids.add(item['uuid'])
        _remember_device(item)
        target = Path(item['mountpoint'])
        mounted = os.path.ismount(target)
        usage = None
        if mounted:
            try:
                du = shutil.disk_usage(target)
                usage = {'total': du.total, 'used': du.used, 'free': du.free}
            except OSError:
                mounted = False
        devices.append({
            'uuid': item['uuid'], 'label': item['label'], 'fstype': item['fstype'],
            'model': item['model'], 'size': item['size'], 'mountpoint': item['mountpoint'],
            'mounted': mounted, 'read_only': mounted, 'usage': usage, 'remembered': True,
        })
    with _db() as conn:
        for row in conn.execute('SELECT * FROM media_devices ORDER BY last_seen DESC'):
            if row['uuid'] in present_ids:
                continue
            devices.append({
                'uuid': row['uuid'], 'label': row['label'], 'fstype': row['fstype'], 'model': row['model'],
                'size': row['size'], 'mountpoint': '', 'mounted': False, 'read_only': True, 'usage': None,
                'remembered': True, 'last_seen': row['last_seen'], 'offline': True,
            })
    return {
        'devices': devices,
        'smb': _service('smbd.service'),
        'discovery': _service('wsdd2.service'),
        'dlna': _service('minidlna.service'),
        'smb_path': r'\\OPENASTRO\Media',
        'dlna_name': 'OpenAstro Media',
        'remote_browser': True,
        'player': True,
        'library': True,
        'thumbnails': shutil.which('ffmpeg') is not None,
        'probe': shutil.which('ffprobe') is not None,
        'persistent_profile': True,
        'active_streams': len(active_streams()),
    }


def credentials() -> dict:
    try:
        data = json.loads(CREDENTIALS.read_text(encoding='utf-8'))
        return {'username': str(data.get('username', 'astro')), 'password': str(data.get('password', ''))}
    except (OSError, json.JSONDecodeError):
        return {'username': 'astro', 'password': ''}


def _device(uuid: str, *, require_mounted: bool = True) -> dict:
    if not valid_uuid(uuid):
        raise ValueError('UUID media non valido')
    for item in _discover():
        if item.get('uuid') == uuid:
            target = Path(item['mountpoint'])
            if require_mounted and not os.path.ismount(target):
                raise FileNotFoundError('Supporto non montato')
            _remember_device(item)
            return item
    if require_mounted:
        raise FileNotFoundError('Supporto non presente')
    with _db() as conn:
        row = conn.execute('SELECT * FROM media_devices WHERE uuid=?', (uuid,)).fetchone()
    if not row:
        raise FileNotFoundError('Supporto sconosciuto')
    return dict(row)


def _safe_target(uuid: str, relative: str, *, require_file: bool | None = None) -> tuple[dict, Path, Path]:
    item = _device(uuid)
    root = Path(item['mountpoint']).resolve(strict=True)
    relative = relative.strip('/')
    candidate = root
    for part in Path(relative).parts if relative else ():
        if part in {'', '.', '..'}:
            raise ValueError('Percorso non valido')
        candidate = candidate / part
        if candidate.is_symlink():
            raise PermissionError('Link simbolici non consentiti')
    target = candidate.resolve(strict=True)
    if target != root and root not in target.parents:
        raise PermissionError('Percorso fuori dal supporto')
    if require_file is True and not target.is_file():
        raise FileNotFoundError('File non trovato')
    if require_file is False and not target.is_dir():
        raise NotADirectoryError('Cartella non trovata')
    return item, root, target


def _category(path: Path, mime: str | None = None) -> str:
    ext = path.suffix.lower()
    mime = mime or mimetypes.guess_type(path.name)[0] or ''
    if ext in VIDEO_EXT or mime.startswith('video/'):
        return 'video'
    if ext in AUDIO_EXT or mime.startswith('audio/'):
        return 'audio'
    if ext in IMAGE_EXT or mime.startswith('image/'):
        return 'image'
    if ext in DOC_EXT or mime.startswith('text/') or mime == 'application/pdf':
        return 'document'
    if ext in ARCHIVE_EXT:
        return 'archive'
    return 'other'


def _entry(root: Path, child: Path, st: os.stat_result) -> dict:
    rel = child.relative_to(root).as_posix()
    is_dir = child.is_dir()
    mime = None if is_dir else (mimetypes.guess_type(child.name)[0] or 'application/octet-stream')
    category = 'dir' if is_dir else _category(child, mime)
    return {
        'name': child.name, 'path': rel, 'type': 'dir' if is_dir else 'file', 'category': category,
        'size': None if is_dir else st.st_size, 'modified': int(st.st_mtime), 'mime': mime,
        'streamable': bool(mime and (mime.startswith('video/') or mime.startswith('audio/') or mime.startswith('image/'))),
        'thumbnail': bool(category in {'video','image'}),
    }


def list_directory(uuid: str, relative: str = '') -> dict:
    item, root, target = _safe_target(uuid, relative, require_file=False)
    entries = []
    for child in target.iterdir():
        try:
            if child.is_symlink():
                continue
            st = child.stat()
        except OSError:
            continue
        entries.append(_entry(root, child, st))
    entries.sort(key=lambda row: (row['type'] != 'dir', row['name'].casefold()))
    rel_dir = '' if target == root else target.relative_to(root).as_posix()
    parent = '' if not rel_dir else str(Path(rel_dir).parent.as_posix())
    if parent == '.': parent = ''
    return {'ok': True, 'uuid': uuid, 'label': item['label'], 'path': rel_dir, 'parent': parent, 'items': entries[:4000]}


def file_info(uuid: str, relative: str) -> dict:
    item, root, target = _safe_target(uuid, relative, require_file=True)
    st = target.stat()
    mime = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
    return {
        'path': target, 'name': target.name, 'size': st.st_size, 'mime': mime, 'uuid': uuid,
        'relative': target.relative_to(root).as_posix(), 'label': item['label'],
        'category': _category(target, mime), 'modified': int(st.st_mtime),
    }


def _upsert_item(conn: sqlite3.Connection, uuid: str, row: dict, token: int) -> None:
    conn.execute('''
      INSERT INTO media_items(uuid,path,name,category,mime,size,modified,scan_token)
      VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(uuid,path) DO UPDATE SET name=excluded.name,category=excluded.category,mime=excluded.mime,
        size=excluded.size,modified=excluded.modified,scan_token=excluded.scan_token
    ''', (uuid,row['path'],row['name'],row['category'],row['mime'],int(row['size']),int(row['modified']),token))


def _library_payload(uuid: str) -> dict:
    mounted = uuid in _mounted_uuids()
    with _db() as conn:
        device = conn.execute('SELECT * FROM media_devices WHERE uuid=?', (uuid,)).fetchone()
        if not device:
            raise FileNotFoundError('Supporto sconosciuto')
        counts = {key: 0 for key in ('video','audio','image','document','archive','other')}
        bytes_by = {key: 0 for key in counts}
        for row in conn.execute('SELECT category,COUNT(*) c,COALESCE(SUM(size),0) b FROM media_items WHERE uuid=? GROUP BY category',(uuid,)):
            if row['category'] in counts:
                counts[row['category']] = int(row['c']); bytes_by[row['category']] = int(row['b'])
        recent = [dict(row) for row in conn.execute('''SELECT name,path,size,modified,mime,category FROM media_items WHERE uuid=? ORDER BY modified DESC LIMIT 24''',(uuid,))]
        for row in recent:
            row['streamable'] = row['category'] in {'video','audio','image'}; row['thumbnail'] = row['category'] in {'video','image'}
    return {'ok':True,'uuid':uuid,'label':device['label'],'mounted':mounted,'counts':counts,'bytes':bytes_by,
            'total_files':sum(counts.values()),'total_bytes':sum(bytes_by.values()),'recent':recent,
            'last_scan':device['last_scan'],'truncated':False,'scan_limit':MAX_LIBRARY_FILES}


def library_summary(uuid: str, *, force: bool = False) -> dict:
    item = _device(uuid)
    root = Path(item['mountpoint']).resolve(strict=True)
    now_mono = time.monotonic()
    cached = _LIBRARY_CACHE.get(uuid)
    if not force and cached and now_mono - cached[0] < 15:
        return cached[1]
    now = int(time.time())
    with _db() as conn:
        device = conn.execute('SELECT last_scan FROM media_devices WHERE uuid=?',(uuid,)).fetchone()
        last_scan = int(device['last_scan']) if device else 0
    if force or now - last_scan >= SCAN_TTL:
        token = int(time.time_ns() // 1_000_000)
        scanned = 0; total_bytes = 0; truncated = False
        stack = [root]
        with _db() as conn:
            while stack and scanned < MAX_LIBRARY_FILES:
                folder = stack.pop()
                try: children = list(os.scandir(folder))
                except OSError: continue
                for entry in children:
                    if scanned >= MAX_LIBRARY_FILES:
                        truncated = True; break
                    try:
                        if entry.is_symlink(): continue
                        path = Path(entry.path)
                        if entry.is_dir(follow_symlinks=False): stack.append(path); continue
                        if not entry.is_file(follow_symlinks=False): continue
                        st = entry.stat(follow_symlinks=False)
                    except OSError: continue
                    scanned += 1; total_bytes += st.st_size
                    mime = mimetypes.guess_type(entry.name)[0] or 'application/octet-stream'
                    row = {'name':entry.name,'path':path.relative_to(root).as_posix(),'size':st.st_size,
                           'modified':int(st.st_mtime),'mime':mime,'category':_category(path,mime)}
                    _upsert_item(conn, uuid, row, token)
            if not truncated:
                conn.execute('DELETE FROM media_items WHERE uuid=? AND scan_token<>?', (uuid, token))
            conn.execute('UPDATE media_devices SET last_scan=?,total_files=?,total_bytes=? WHERE uuid=?', (now, scanned, total_bytes, uuid))
            conn.execute('INSERT INTO media_events(ts,kind,uuid,detail) VALUES(?,?,?,?)',(now,'scan',uuid,json.dumps({'files':scanned,'bytes':total_bytes,'truncated':truncated})))
        payload = _library_payload(uuid); payload['truncated'] = truncated
    else:
        payload = _library_payload(uuid)
    _LIBRARY_CACHE[uuid] = (now_mono, payload)
    return payload


def catalog(uuid: str, *, category: str='all', query: str='', sort: str='recent', limit: int=200, offset: int=0, favorite: bool=False) -> dict:
    _device(uuid, require_mounted=False)
    limit = max(1,min(500,int(limit))); offset=max(0,int(offset))
    where=['i.uuid=?']; args:list[object]=[uuid]
    if category not in {'all','video','audio','image','document','archive','other'}: category='all'
    if category!='all': where.append('i.category=?'); args.append(category)
    if query:
        where.append('LOWER(i.name) LIKE ?'); args.append('%'+query.casefold()+'%')
    join='LEFT JOIN media_favorites f ON f.uuid=i.uuid AND f.path=i.path LEFT JOIN media_playback p ON p.uuid=i.uuid AND p.path=i.path'
    if favorite: where.append('f.path IS NOT NULL')
    order={'name':'i.name COLLATE NOCASE ASC','size':'i.size DESC','recent':'i.modified DESC','played':'p.last_played DESC'}.get(sort,'i.modified DESC')
    sql=f'''SELECT i.*, CASE WHEN f.path IS NULL THEN 0 ELSE 1 END favorite,
            COALESCE(p.position,0) position,COALESCE(p.duration,0) playback_duration,COALESCE(p.completed,0) completed,
            COALESCE(p.last_played,0) last_played FROM media_items i {join} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ? OFFSET ?'''
    args += [limit,offset]
    with _db() as conn:
        rows=[dict(r) for r in conn.execute(sql,args)]
        total=conn.execute(f'''SELECT COUNT(*) c FROM media_items i {join} WHERE {' AND '.join(where)}''',args[:-2]).fetchone()['c']
    mounted=uuid in _mounted_uuids()
    for row in rows:
        row['streamable']=row['category'] in {'video','audio','image'}; row['thumbnail']=row['category'] in {'video','image'}; row['mounted']=mounted
    return {'ok':True,'uuid':uuid,'items':rows,'total':int(total),'offset':offset,'limit':limit,'mounted':mounted}


def _enrich_profile_rows(rows: list[sqlite3.Row], mounted: set[str]) -> list[dict]:
    out=[]
    for r in rows:
        row=dict(r); row['available']=row['uuid'] in mounted; row['thumbnail']=row.get('category') in {'video','image'}; out.append(row)
    return out


def home(uuid: str|None=None) -> dict:
    mounted=_mounted_uuids()
    where='WHERE i.uuid=?' if uuid else ''
    args=(uuid,) if uuid else ()
    with _db() as conn:
        cont=conn.execute(f'''SELECT i.uuid,i.path,i.name,i.category,i.mime,i.size,i.modified,p.position,p.duration,p.last_played,p.completed
            FROM media_playback p JOIN media_items i ON i.uuid=p.uuid AND i.path=p.path {where} AND p.completed=0 AND p.position>10 AND p.duration>0 AND p.position<p.duration*0.95 ORDER BY p.last_played DESC LIMIT 20''' if uuid else '''SELECT i.uuid,i.path,i.name,i.category,i.mime,i.size,i.modified,p.position,p.duration,p.last_played,p.completed FROM media_playback p JOIN media_items i ON i.uuid=p.uuid AND i.path=p.path WHERE p.completed=0 AND p.position>10 AND p.duration>0 AND p.position<p.duration*0.95 ORDER BY p.last_played DESC LIMIT 20''', args).fetchall()
        recent=conn.execute(f'''SELECT i.uuid,i.path,i.name,i.category,i.mime,i.size,i.modified,p.position,p.duration,p.last_played,p.completed FROM media_playback p JOIN media_items i ON i.uuid=p.uuid AND i.path=p.path {where} ORDER BY p.last_played DESC LIMIT 20''' if uuid else '''SELECT i.uuid,i.path,i.name,i.category,i.mime,i.size,i.modified,p.position,p.duration,p.last_played,p.completed FROM media_playback p JOIN media_items i ON i.uuid=p.uuid AND i.path=p.path ORDER BY p.last_played DESC LIMIT 20''',args).fetchall()
        fav=conn.execute(f'''SELECT i.uuid,i.path,i.name,i.category,i.mime,i.size,i.modified,f.created_at FROM media_favorites f JOIN media_items i ON i.uuid=f.uuid AND i.path=f.path {where} ORDER BY f.created_at DESC LIMIT 20''' if uuid else '''SELECT i.uuid,i.path,i.name,i.category,i.mime,i.size,i.modified,f.created_at FROM media_favorites f JOIN media_items i ON i.uuid=f.uuid AND i.path=f.path ORDER BY f.created_at DESC LIMIT 20''',args).fetchall()
        events=[dict(r) for r in conn.execute('SELECT ts,kind,uuid,path,detail FROM media_events ORDER BY ts DESC LIMIT 30')]
        stats=dict(conn.execute('''SELECT COUNT(*) known_files,COALESCE(SUM(size),0) known_bytes FROM media_items''').fetchone())
    return {'ok':True,'continue':_enrich_profile_rows(cont,mounted),'recently_played':_enrich_profile_rows(recent,mounted),
            'favorites':_enrich_profile_rows(fav,mounted),'active_streams':active_streams(),'events':events,'stats':stats}


def update_progress(uuid: str, relative: str, position: float, duration: float, *, client: str='') -> dict:
    if not valid_uuid(uuid): raise ValueError('UUID media non valido')
    position=max(0.0,float(position or 0)); duration=max(0.0,float(duration or 0)); now=int(time.time())
    completed=1 if duration>0 and position>=duration*0.95 else 0
    with _db() as conn:
        item=conn.execute('SELECT 1 FROM media_items WHERE uuid=? AND path=?',(uuid,relative)).fetchone()
        if not item:
            info=file_info(uuid,relative)
            _upsert_item(conn,uuid,{'path':relative,'name':info['name'],'category':info['category'],'mime':info['mime'],'size':info['size'],'modified':info['modified']},0)
        old=conn.execute('SELECT position,play_count FROM media_playback WHERE uuid=? AND path=?',(uuid,relative)).fetchone()
        play_count=int(old['play_count']) if old else 0
        if (not old or float(old['position']) < 10) and position >= 10: play_count += 1
        conn.execute('''INSERT INTO media_playback(uuid,path,position,duration,completed,last_played,play_count,client) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(uuid,path) DO UPDATE SET position=excluded.position,duration=excluded.duration,completed=excluded.completed,last_played=excluded.last_played,play_count=?,client=excluded.client''',
            (uuid,relative,position,duration,completed,now,play_count,client,play_count))
    return {'ok':True,'position':position,'duration':duration,'completed':bool(completed),'play_count':play_count}


def set_favorite(uuid: str, relative: str, enabled: bool) -> dict:
    if not valid_uuid(uuid): raise ValueError('UUID media non valido')
    with _db() as conn:
        if not conn.execute('SELECT 1 FROM media_items WHERE uuid=? AND path=?',(uuid,relative)).fetchone():
            info=file_info(uuid,relative); _upsert_item(conn,uuid,{'path':relative,'name':info['name'],'category':info['category'],'mime':info['mime'],'size':info['size'],'modified':info['modified']},0)
        if enabled: conn.execute('INSERT OR REPLACE INTO media_favorites(uuid,path,created_at) VALUES(?,?,?)',(uuid,relative,int(time.time())))
        else: conn.execute('DELETE FROM media_favorites WHERE uuid=? AND path=?',(uuid,relative))
    return {'ok':True,'favorite':bool(enabled)}


def probe_file(uuid: str, relative: str) -> dict:
    info=file_info(uuid,relative)
    result=_run(['ffprobe','-v','error','-show_entries','format=duration,size,bit_rate,format_name:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels,channel_layout','-of','json',str(info['path'])],timeout=8)
    probe=None
    if result.returncode==0:
        try: probe=json.loads(result.stdout or '{}')
        except json.JSONDecodeError: probe=None
    if probe:
        fmt=probe.get('format') or {}; streams=probe.get('streams') or []; video=next((s for s in streams if s.get('codec_type')=='video'),{}); audio=next((s for s in streams if s.get('codec_type')=='audio'),{})
        with _db() as conn:
            conn.execute('''UPDATE media_items SET duration=?,width=?,height=?,video_codec=?,audio_codec=?,bit_rate=? WHERE uuid=? AND path=?''',
                (float(fmt.get('duration') or 0) or None,video.get('width'),video.get('height'),video.get('codec_name'),audio.get('codec_name'),int(fmt.get('bit_rate') or 0) or None,uuid,relative))
    return {'ok':True,**{k:v for k,v in info.items() if k!='path'},'probe':probe}


def thumbnail_info(uuid: str, relative: str) -> dict:
    info=file_info(uuid,relative)
    if info['category'] not in {'video','image'}: raise FileNotFoundError('Anteprima non disponibile')
    THUMB_ROOT.mkdir(parents=True,exist_ok=True)
    try: os.chmod(THUMB_ROOT,0o750)
    except OSError: pass
    identity=f"{uuid}\0{relative}\0{info['modified']}\0{info['size']}".encode(); name=hashlib.sha256(identity).hexdigest()+'.jpg'; target=THUMB_ROOT/name
    if not target.exists() or target.stat().st_size<100:
        tmp=target.with_suffix('.tmp.jpg'); args=['ffmpeg','-hide_banner','-loglevel','error','-y']
        if info['category']=='video': args += ['-ss','5','-i',str(info['path']),'-frames:v','1']
        else: args += ['-i',str(info['path']),'-frames:v','1']
        args += ['-vf','scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2','-q:v','5',str(tmp)]
        result=_run(args,timeout=12)
        if result.returncode!=0 or not tmp.exists(): tmp.unlink(missing_ok=True); raise FileNotFoundError('Generazione anteprima fallita')
        tmp.replace(target)
    st=target.stat(); return {'path':target,'name':target.name,'size':st.st_size,'mime':'image/jpeg'}


def stream_begin(info: dict, client: str='', start: int=0, end: int|None=None) -> str:
    sid=uuidlib.uuid4().hex[:16]; now=time.time()
    with _STREAM_LOCK:
        _ACTIVE_STREAMS[sid]={'id':sid,'uuid':info.get('uuid',''),'path':info.get('relative',''),'name':info.get('name',''),'category':info.get('category',''),'client':client,'started':now,'last_seen':now,'bytes_sent':0,'range_start':start,'range_end':end}
    return sid


def stream_touch(sid: str, amount: int) -> None:
    with _STREAM_LOCK:
        row=_ACTIVE_STREAMS.get(sid)
        if row: row['bytes_sent'] += int(amount); row['last_seen']=time.time()


def stream_end(sid: str) -> None:
    with _STREAM_LOCK:
        row=_ACTIVE_STREAMS.pop(sid,None)
    if row:
        try:
            with _db() as conn: conn.execute('INSERT INTO media_events(ts,kind,uuid,path,detail) VALUES(?,?,?,?,?)',(int(time.time()),'stream',row['uuid'],row['path'],json.dumps({'bytes':row['bytes_sent'],'seconds':round(time.time()-row['started'],1),'client':row['client']})))
        except Exception: pass


def active_streams() -> list[dict]:
    now=time.time()
    with _STREAM_LOCK:
        stale=[sid for sid,row in _ACTIVE_STREAMS.items() if now-row['last_seen']>120]
        for sid in stale: _ACTIVE_STREAMS.pop(sid,None)
        return [{**row,'seconds':round(now-row['started'],1)} for row in _ACTIVE_STREAMS.values()]


def diagnostics() -> dict:
    with _db() as conn:
        db_stats={
            'devices':conn.execute('SELECT COUNT(*) c FROM media_devices').fetchone()['c'],
            'items':conn.execute('SELECT COUNT(*) c FROM media_items').fetchone()['c'],
            'favorites':conn.execute('SELECT COUNT(*) c FROM media_favorites').fetchone()['c'],
            'playback':conn.execute('SELECT COUNT(*) c FROM media_playback').fetchone()['c'],
        }
    return {'ok':True,'database':str(DB_PATH),'database_bytes':DB_PATH.stat().st_size if DB_PATH.exists() else 0,'stats':db_stats,'active_streams':active_streams(),'ffmpeg':shutil.which('ffmpeg'),'ffprobe':shutil.which('ffprobe')}
