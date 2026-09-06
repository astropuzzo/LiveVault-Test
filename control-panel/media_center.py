#!/usr/bin/env python3
from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess

MEDIA_ROOT = Path('/srv/openastro-media')
MANAGER = Path('/usr/local/sbin/openastro-media-manager')
CREDENTIALS = Path('/etc/openastro-media-credentials.json')
UUID_RE = re.compile(r'^[A-Za-z0-9._:-]{2,128}$')


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
        rel = child.relative_to(root).as_posix()
        is_dir = child.is_dir()
        mime = None if is_dir else (mimetypes.guess_type(child.name)[0] or 'application/octet-stream')
        entries.append({
            'name': child.name,
            'path': rel,
            'type': 'dir' if is_dir else 'file',
            'size': None if is_dir else st.st_size,
            'modified': int(st.st_mtime),
            'mime': mime,
            'streamable': bool(mime and (mime.startswith('video/') or mime.startswith('audio/') or mime.startswith('image/'))),
        })
    entries.sort(key=lambda row: (row['type'] != 'dir', row['name'].casefold()))
    rel_dir = '' if target == root else target.relative_to(root).as_posix()
    parent = '' if not rel_dir else str(Path(rel_dir).parent.as_posix())
    if parent == '.':
        parent = ''
    return {'ok': True, 'uuid': uuid, 'label': item['label'], 'path': rel_dir, 'parent': parent, 'items': entries[:4000]}


def file_info(uuid: str, relative: str) -> dict:
    item, root, target = _safe_target(uuid, relative, require_file=True)
    st = target.stat()
    return {
        'path': target,
        'name': target.name,
        'size': st.st_size,
        'mime': mimetypes.guess_type(target.name)[0] or 'application/octet-stream',
        'uuid': uuid,
        'relative': target.relative_to(root).as_posix(),
        'label': item['label'],
    }
