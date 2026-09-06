#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

MEDIA_ROOT = Path('/srv/openastro-media')
MANAGER = Path('/usr/local/sbin/openastro-media-manager')
CREDENTIALS = Path('/etc/openastro-media-credentials.json')
THUMB_ROOT = Path('/var/lib/openastro-control/media-thumbs')
UUID_RE = re.compile(r'^[A-Za-z0-9._:-]{2,128}$')
MAX_LIBRARY_FILES = 15000
_LIBRARY_CACHE: dict[str, tuple[float, dict]] = {}

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


def status() -> dict:
    devices = []
    for item in _discover():
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
            'mounted': mounted, 'read_only': mounted, 'usage': usage,
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
    }


def credentials() -> dict:
    try:
        data = json.loads(CREDENTIALS.read_text(encoding='utf-8'))
        return {'username': str(data.get('username', 'astro')), 'password': str(data.get('password', ''))}
    except (OSError, json.JSONDecodeError):
        return {'username': 'astro', 'password': ''}


def _device(uuid: str) -> dict:
    if not valid_uuid(uuid):
        raise ValueError('UUID media non valido')
    for item in _discover():
        if item.get('uuid') == uuid:
            target = Path(item['mountpoint'])
            if not os.path.ismount(target):
                raise FileNotFoundError('Supporto non montato')
            return item
    raise FileNotFoundError('Supporto non presente')


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
        'name': child.name,
        'path': rel,
        'type': 'dir' if is_dir else 'file',
        'category': category,
        'size': None if is_dir else st.st_size,
        'modified': int(st.st_mtime),
        'mime': mime,
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
    if parent == '.':
        parent = ''
    return {'ok': True, 'uuid': uuid, 'label': item['label'], 'path': rel_dir, 'parent': parent, 'items': entries[:4000]}


def file_info(uuid: str, relative: str) -> dict:
    item, root, target = _safe_target(uuid, relative, require_file=True)
    st = target.stat()
    mime = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
    return {
        'path': target,
        'name': target.name,
        'size': st.st_size,
        'mime': mime,
        'uuid': uuid,
        'relative': target.relative_to(root).as_posix(),
        'label': item['label'],
        'category': _category(target, mime),
        'modified': int(st.st_mtime),
    }


def library_summary(uuid: str, *, force: bool = False) -> dict:
    item = _device(uuid)
    root = Path(item['mountpoint']).resolve(strict=True)
    now = time.monotonic()
    cached = _LIBRARY_CACHE.get(uuid)
    if not force and cached and now - cached[0] < 30:
        return cached[1]
    counts = {'video': 0, 'audio': 0, 'image': 0, 'document': 0, 'archive': 0, 'other': 0}
    bytes_by = {key: 0 for key in counts}
    recent: list[dict] = []
    scanned = 0
    stack = [root]
    while stack and scanned < MAX_LIBRARY_FILES:
        folder = stack.pop()
        try:
            children = list(os.scandir(folder))
        except OSError:
            continue
        for entry in children:
            if scanned >= MAX_LIBRARY_FILES:
                break
            try:
                if entry.is_symlink():
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            scanned += 1
            mime = mimetypes.guess_type(entry.name)[0] or 'application/octet-stream'
            category = _category(path, mime)
            counts[category] += 1
            bytes_by[category] += st.st_size
            row = {
                'name': entry.name,
                'path': path.relative_to(root).as_posix(),
                'size': st.st_size,
                'modified': int(st.st_mtime),
                'mime': mime,
                'category': category,
                'streamable': category in {'video','audio','image'},
                'thumbnail': category in {'video','image'},
            }
            recent.append(row)
    recent.sort(key=lambda row: row['modified'], reverse=True)
    payload = {
        'ok': True,
        'uuid': uuid,
        'label': item['label'],
        'counts': counts,
        'bytes': bytes_by,
        'total_files': sum(counts.values()),
        'total_bytes': sum(bytes_by.values()),
        'recent': recent[:24],
        'truncated': scanned >= MAX_LIBRARY_FILES,
        'scan_limit': MAX_LIBRARY_FILES,
    }
    _LIBRARY_CACHE[uuid] = (now, payload)
    return payload


def probe_file(uuid: str, relative: str) -> dict:
    info = file_info(uuid, relative)
    result = _run([
        'ffprobe','-v','error','-show_entries',
        'format=duration,size,bit_rate,format_name:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels,channel_layout',
        '-of','json',str(info['path'])
    ], timeout=8)
    if result.returncode != 0:
        return {'ok': True, **{k:v for k,v in info.items() if k != 'path'}, 'probe': None}
    try:
        probe = json.loads(result.stdout or '{}')
    except json.JSONDecodeError:
        probe = None
    return {'ok': True, **{k:v for k,v in info.items() if k != 'path'}, 'probe': probe}


def thumbnail_info(uuid: str, relative: str) -> dict:
    info = file_info(uuid, relative)
    if info['category'] not in {'video','image'}:
        raise FileNotFoundError('Anteprima non disponibile')
    THUMB_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(THUMB_ROOT, 0o750)
    except OSError:
        pass
    identity = f"{uuid}\0{relative}\0{info['modified']}\0{info['size']}".encode()
    name = hashlib.sha256(identity).hexdigest() + '.jpg'
    target = THUMB_ROOT / name
    if not target.exists() or target.stat().st_size < 100:
        tmp = target.with_suffix('.tmp.jpg')
        args = ['ffmpeg','-hide_banner','-loglevel','error','-y']
        if info['category'] == 'video':
            args += ['-ss','5','-i',str(info['path']),'-frames:v','1']
        else:
            args += ['-i',str(info['path']),'-frames:v','1']
        args += ['-vf','scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2','-q:v','5',str(tmp)]
        result = _run(args, timeout=12)
        if result.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            raise FileNotFoundError('Generazione anteprima fallita')
        tmp.replace(target)
    st = target.stat()
    return {'path': target, 'name': target.name, 'size': st.st_size, 'mime': 'image/jpeg'}
