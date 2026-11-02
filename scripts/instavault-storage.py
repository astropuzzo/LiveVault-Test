#!/usr/bin/env python3
"""Host-only lifecycle for InstaVault's removable-media bind mount."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

MODULE_LABEL = 'openastro.module'
MODULE_NAME = 'instavault'
NVME = Path('/mnt/livevault-nvme')
MEDIA = NVME / 'instavault-media'
MARKER = '.instavault-volume'
MARKER_VALUE = 'instavault-v1'
DATA_UUID = '5fe2d0f6-b485-44e9-8e26-31fb0d217db2'
STATE_PATH = Path('/var/lib/openastro-instavault/storage-containers.json')
CONTAINER_ID = re.compile(r'[0-9a-f]{64}\Z')


def command(*args, timeout=55, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} failed: {(result.stderr or result.stdout).strip()[-500:]}')
    return result


def container_ids(values):
    if not isinstance(values, list) or any(not isinstance(value, str) or not CONTAINER_ID.fullmatch(value) for value in values):
        raise RuntimeError('Invalid InstaVault container state')
    return sorted(set(values))


def state_directory():
    directory = STATE_PATH.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise RuntimeError('InstaVault lifecycle directory must be root-owned and private')
    return directory


def read_state():
    if not STATE_PATH.exists():
        return []
    info = STATE_PATH.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise RuntimeError('InstaVault lifecycle state must be root-owned and private')
    value = json.loads(STATE_PATH.read_text())
    if value.get('version') != 1:
        raise RuntimeError('Unknown InstaVault lifecycle state version')
    return container_ids(value.get('containers'))


def save_state(ids):
    directory = state_directory()
    ids = container_ids(list(ids))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, prefix='.containers-', delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            json.dump({'version': 1, 'containers': ids}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(STATE_PATH)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def inspect_container(container):
    result = command('docker', 'inspect', container, check=False, timeout=15)
    if result.returncode:
        if 'no such object' in result.stderr.lower() or 'no such container' in result.stderr.lower():
            return None
        raise RuntimeError('Cannot inspect InstaVault container')
    value = json.loads(result.stdout)[0]
    if value['Id'] != container or value.get('Config', {}).get('Labels', {}).get(MODULE_LABEL) != MODULE_NAME:
        raise RuntimeError('Recorded container is no longer an InstaVault container')
    return value


def running_containers():
    result = command('docker', 'ps', '--no-trunc', '-q', '--filter', f'label={MODULE_LABEL}={MODULE_NAME}', timeout=15)
    return container_ids(result.stdout.splitlines())


def validate_volume():
    result = command('findmnt', '--json', '--output', 'UUID,TARGET', '--target', str(NVME), timeout=15)
    mounts = json.loads(result.stdout).get('filesystems', [])
    if len(mounts) != 1 or mounts[0].get('target') != str(NVME) or mounts[0].get('uuid') != DATA_UUID:
        raise RuntimeError('Expected InstaVault NVMe UUID is not mounted')
    marker = MEDIA / MARKER
    info = marker.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 64 or marker.read_text().strip() != MARKER_VALUE:
        raise RuntimeError('InstaVault media marker is missing or invalid')


def has_marker(container):
    code = (
        'from pathlib import Path; '
        f'p=Path("/media/{MARKER}"); '
        f'raise SystemExit(0 if p.is_file() and not p.is_symlink() and p.stat().st_size<=64 and p.read_text().strip()=={MARKER_VALUE!r} else 1)'
    )
    return command('docker', 'exec', container, 'python', '-c', code, check=False, timeout=15).returncode == 0


def pause():
    ids = running_containers()
    recorded = set(read_state()) | set(ids)
    # Persist all running IDs before the first stop, including partial failures.
    save_state(recorded)
    errors = []
    for container in ids:
        try:
            item = inspect_container(container)
            if item is not None and item['State']['Running']:
                command('docker', 'stop', '--time', '40', container)
                item = inspect_container(container)
                if item is not None and item['State']['Running']:
                    raise RuntimeError('InstaVault container is still running')
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            errors.append(f'{container[:12]}: {error}')
    if errors:
        raise RuntimeError('; '.join(errors))


def resume():
    validate_volume()
    recorded = set(read_state())
    candidates = recorded | set(running_containers())
    errors = []
    for container in sorted(candidates):
        try:
            item = inspect_container(container)
            if item is None:
                recorded.discard(container)  # Coolify already removed this instance.
            elif item['State']['Running']:
                if not has_marker(container):
                    recorded.add(container)
                    save_state(recorded)
                    command('docker', 'restart', '--time', '40', container, timeout=75)
                    if not has_marker(container):
                        command('docker', 'stop', '--time', '40', container)
                        raise RuntimeError('Fresh container view still has no media marker')
                recorded.discard(container)
            elif container in recorded:
                command('docker', 'start', container, timeout=30)
                if not has_marker(container):
                    command('docker', 'stop', '--time', '40', container)
                    raise RuntimeError('Started container has no media marker')
                recorded.discard(container)
            save_state(recorded)
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
            errors.append(f'{container[:12]}: {error}')
    save_state(recorded)
    if errors:
        raise RuntimeError('; '.join(errors))


def main(action):
    if os.geteuid() != 0:
        raise RuntimeError('Root required')
    if os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt'):
        raise RuntimeError('Run InstaVault storage lifecycle in the host mount namespace')
    import fcntl
    state_directory()
    with (STATE_PATH.parent / 'storage.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        if action == 'pause':
            pause()
        elif action == 'resume':
            resume()
        else:
            raise RuntimeError('Use pause or resume')


if __name__ == '__main__':
    try:
        main(sys.argv[1] if len(sys.argv) == 2 else '')
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
