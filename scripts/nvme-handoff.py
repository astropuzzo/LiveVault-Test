#!/usr/bin/env python3
"""Privileged, serialized NVMe handoff. Never lazy-unmount or discard a buffer."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path('/data/livevault')
NVME = Path('/mnt/livevault-nvme')
BUFFER = Path('/var/lib/livevault-buffer')
BUFFER_LIMIT_BYTES = 4 * 1024**3
RECORDINGS = ROOT / 'recordings'
DATA_UUID = '5fe2d0f6-b485-44e9-8e26-31fb0d217db2'
GPT_HARNESS_SERVICE = 'gpt-harness.service'


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()


def publish(mode, **fields):
    if mode == 'nvme':
        (ROOT / '.storage-buffer-full').unlink(missing_ok=True)
    path = ROOT / 'storage-state.json'
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as f:
        json.dump({'mode': mode, **fields}, f)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


def quiesce():
    token = uuid.uuid4().hex
    publish('quiesce', token=token)
    deadline = time.monotonic() + 140
    while time.monotonic() < deadline:
        try:
            if json.loads((ROOT / 'storage-ready.json').read_text()).get('token') == token:
                return
        except (OSError, ValueError):
            pass
        time.sleep(.25)
    raise RuntimeError('Registrazioni/operazioni ancora in chiusura: NVMe NON espulso. Riprova.')


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def merge_buffer(source, destination):
    """Verified, restartable transfer; never overwrite different media."""
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if relative.parts[0] == 'lost+found':
            continue
        if path.is_symlink():
            raise RuntimeError(f'Unexpected symlink in buffer: {relative}')
        target = destination / relative
        if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != destination.parent):
            raise RuntimeError('Unsafe destination symlink')
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if path.name == '.livevault-stitch-session.json':
                # Both directories belong to the same logical session. Preserve
                # its original start time, but reject a mismatched marker.
                old, new = json.loads(target.read_text()), json.loads(path.read_text())
                if any(old.get(k) != new.get(k) for k in ('session_id', 'source_id')):
                    raise RuntimeError('Conflicting session markers')
            elif path.stat().st_size != target.stat().st_size or digest(path) != digest(target):
                raise RuntimeError(f'Conflicting file; both copies preserved: {relative}')
        else:
            temporary = target.with_name('.' + target.name + '.handoff-copy')
            shutil.copy2(path, temporary)
            if digest(path) != digest(temporary):
                raise RuntimeError(f'Copy verification failed: {relative}')
            with temporary.open('rb') as f:
                os.fsync(f.fileno())
            temporary.replace(target)
        fd = os.open(target.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        path.unlink()
    for path in sorted(source.rglob('*'), reverse=True):
        if path.is_dir() and path.name != 'lost+found':
            try:
                path.rmdir()
            except OSError:
                pass


def switch(source):
    if os.path.ismount(RECORDINGS):
        run('umount', str(RECORDINGS))
    run('mount', '--bind', str(source), str(RECORDINGS))


def verify_container_view():
    containers = run('docker', 'ps', '-q', '--filter', 'name=ahul2vdjkyvjiwgzpcrmxzfe').splitlines()
    if not containers:
        raise RuntimeError('LiveVault non disponibile: handoff annullato')
    expected = RECORDINGS.stat().st_dev
    for container in containers:
        actual = int(run('docker', 'exec', container, 'python', '-c',
                         'import os; print(os.stat("/data/recordings").st_dev)'))
        if actual != expected:
            raise RuntimeError('Mount propagation non valida: NVMe NON espulso')


def device_blockers():
    """Return processes that still hold an fd/cwd on the NVMe filesystem."""
    device = NVME.stat().st_dev
    blockers = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            handles = list((proc / 'fd').iterdir()) + [proc / 'cwd']
        except (FileNotFoundError, PermissionError):
            continue
        for handle in handles:
            try:
                if handle.stat().st_dev != device:
                    continue
                try:
                    target = os.readlink(handle)
                except (OSError, PermissionError):
                    target = '?'
                try:
                    command = (proc / 'comm').read_text().strip()
                except (OSError, PermissionError):
                    command = '?'
                blockers.append((int(proc.name), command, handle.name, target))
                break
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                pass
    return blockers


def service_active(name):
    return subprocess.run(
        ['systemctl', 'is-active', '--quiet', name],
        capture_output=True,
    ).returncode == 0


def set_service(name, action):
    return subprocess.run(
        ['systemctl', action, name],
        text=True,
        capture_output=True,
    )



def wait_closed_device_handles():
    deadline = time.monotonic() + 20
    blockers = []
    while True:
        blockers = device_blockers()
        if not blockers:
            return
        if time.monotonic() >= deadline:
            details = '; '.join(
                f'pid={pid} process={command} {handle}->{target}'
                for pid, command, handle, target in blockers[:8]
            )
            if len(blockers) > 8:
                details += f'; +{len(blockers) - 8} altri'
            raise RuntimeError(f'NVMe ancora in uso: {details}')
        time.sleep(.5)


def main(action):
    import fcntl
    if os.geteuid() != 0:
        raise RuntimeError('Root required')
    with open('/run/lock/livevault-storage.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == 'boot':
            if not os.path.ismount(BUFFER):
                run('mount', str(BUFFER))
            run('mount', '--make-rshared', '/data')
            pending = any(p.is_file() for p in BUFFER.rglob('*') if 'lost+found' not in p.parts)
            mode = 'nvme' if os.path.ismount(NVME) and not pending else 'buffer'
            RECORDINGS.mkdir(parents=True, exist_ok=True)
            switch(NVME / 'livevault/recordings' if mode == 'nvme' else BUFFER)
            publish(mode)
            return
        previous = json.loads((ROOT / 'storage-state.json').read_text())['mode']
        if action == 'eject' and previous == 'buffer' and not os.path.ismount(NVME):
            print('NVMe già espulso; buffer interno attivo.')
            return
        if action == 'attach' and previous == 'nvme':
            print('NVMe già operativo.')
            return
        if action not in {'eject', 'attach'}:
            raise RuntimeError('Unknown action')
        if previous not in {'nvme', 'buffer'}:
            raise RuntimeError('Handoff incompleto: mantenuto in pausa per recupero amministratore')
        try:
            if action == 'attach':
                if not Path('/dev/disk/by-uuid', DATA_UUID).exists():
                    raise RuntimeError('NVMe previsto non presente')
                if not os.path.ismount(NVME):
                    run('mount', str(NVME))
                device = run('findmnt', '-nro', 'SOURCE', '--mountpoint', str(NVME))
                if run('blkid', '-s', 'UUID', '-o', 'value', device) != DATA_UUID:
                    raise RuntimeError('Disco errato: nessun file trasferito')
            quiesce()
            if action == 'eject':
                run('systemctl', 'stop', 'livevault-backup.timer', 'livevault-backup.service')
                switch(BUFFER)
                verify_container_view()
                os.sync()
                if os.path.ismount('/share'):
                    run('umount', '/share')
                # GPT Harness has an optional writable view of its NVMe workspace.
                # Stop it before detaching the filesystem so its private mount
                # namespace cannot retain the removable device; start it again
                # immediately after the host unmount, when it runs eMMC-only.
                harness_was_active = service_active(GPT_HARNESS_SERVICE)
                if harness_was_active:
                    result = set_service(GPT_HARNESS_SERVICE, 'stop')
                    if result.returncode != 0:
                        raise RuntimeError(f'Impossibile fermare GPT Harness: {result.stdout}{result.stderr}'.strip())
                try:
                    wait_closed_device_handles()
                    run('umount', str(NVME))
                finally:
                    if harness_was_active:
                        set_service(GPT_HARNESS_SERVICE, 'start')
                publish('buffer', limit_bytes=BUFFER_LIMIT_BYTES)
                print('NVMe espulso: puoi scollegarlo. Docker online; GPT Harness riavviato su eMMC; registrazione su buffer interno massimo 4 GB.')
            else:
                # Close all buffer writers before copying. Same stable paths and
                # session IDs let the existing recovery/stitcher join the parts.
                merge_buffer(BUFFER, NVME / 'livevault/recordings')
                switch(NVME / 'livevault/recordings')
                verify_container_view()
                publish('nvme')
                subprocess.run(['mount', '/share'], capture_output=True)
                subprocess.run(['systemctl', 'start', 'livevault-backup.timer'], capture_output=True)
                # If Harness stayed online while the NVMe was absent, restart it
                # so systemd recreates its sandbox with the optional NVMe RW path.
                if service_active(GPT_HARNESS_SERVICE):
                    set_service(GPT_HARNESS_SERVICE, 'restart')
                print('NVMe operativo: buffer trasferito e verificato; registrazioni riprese, stitching in coda; GPT Harness riallineato al workspace NVMe.')
        except Exception:
            # Roll back only when the expected storage can actually be restored.
            source = NVME / 'livevault/recordings' if previous == 'nvme' else BUFFER
            if (previous != 'nvme' or os.path.ismount(NVME)) and source.is_dir():
                switch(source)
                publish(previous)
            raise


if __name__ == '__main__':
    try:
        main(sys.argv[1])
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
