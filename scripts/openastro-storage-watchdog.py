#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import time

UUID = '5fe2d0f6-b485-44e9-8e26-31fb0d217db2'
STATE = Path('/data/livevault/storage-state.json')
REC = Path('/data/livevault/recordings')
INTERNAL_REC = Path('/srv/openastro-internal/livevault/recordings')
NVME = Path('/mnt/livevault-nvme')
SHARE = Path('/share')
BUFFER = Path('/var/lib/livevault-buffer')
LOCK = Path('/run/lock/livevault-storage.lock')
LIMIT = 4 * 1024**3
LOG = Path('/var/log/openastro-storage-watchdog.log')


def log(message: str) -> None:
    line = f'{time.strftime("%Y-%m-%dT%H:%M:%S%z")} {message}'
    print(line, flush=True)
    try:
        with LOG.open('a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except OSError:
        pass


def run(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(args, 124, '', '')


def mode() -> str:
    try:
        return str(json.loads(STATE.read_text()).get('mode', 'unknown'))
    except Exception:
        return 'unknown'


def mount_info(path: Path) -> tuple[str | None, set[str]]:
    result = run(['findmnt', '-nro', 'SOURCE,OPTIONS', '--mountpoint', str(path)], 3)
    if result.returncode != 0 or not result.stdout.strip():
        return None, set()
    source, _, options = result.stdout.strip().partition(' ')
    return source, set(options.split(','))


def device_from_source(source: str | None) -> str | None:
    if not source:
        return None
    device = source.split('[', 1)[0]
    try:
        info = os.stat(device)
    except OSError:
        return None
    return device if stat.S_ISBLK(info.st_mode) else None


def uuid_of(device: str | None) -> str | None:
    if not device:
        return None
    result = run(['blkid', '-s', 'UUID', '-o', 'value', device], 3)
    return result.stdout.strip() if result.returncode == 0 else None


def healthy_nvme() -> tuple[bool, str]:
    rec_source, rec_options = mount_info(REC)
    nvme_source, nvme_options = mount_info(NVME)
    rec_dev = device_from_source(rec_source)
    nvme_dev = device_from_source(nvme_source)
    if not rec_dev:
        return False, 'recordings source device missing'
    if not nvme_dev:
        return False, 'NVMe mount source device missing'
    if uuid_of(rec_dev) != UUID or uuid_of(nvme_dev) != UUID:
        return False, 'filesystem UUID mismatch'
    bad = {'ro', 'shutdown', 'emergency_ro'}
    if rec_options & bad or nvme_options & bad:
        return False, f'filesystem unhealthy options={sorted((rec_options | nvme_options) & bad)}'
    if 'rw' not in rec_options or 'rw' not in nvme_options:
        return False, 'filesystem not writable'
    try:
        os.statvfs(REC)
        os.statvfs(NVME)
    except OSError as exc:
        return False, f'filesystem stat failed: {exc}'
    return True, 'ok'


def publish_buffer(reason: str) -> None:
    temporary = STATE.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump({'mode': 'buffer', 'limit_bytes': LIMIT, 'reason': reason}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(STATE)


def container_id() -> str | None:
    result = run(['docker', 'ps', '-a', '--filter', 'name=ahul2vdjkyvjiwgzpcrmxzfe', '--format', '{{.ID}}'], 5)
    return result.stdout.splitlines()[0].strip() if result.stdout.strip() else None


def detach(path: Path) -> None:
    if not mount_info(path)[0]:
        return
    result = run(['umount', str(path)], 8)
    if result.returncode != 0:
        # Lazy detach is reserved only for an already-failed/disconnected medium.
        run(['umount', '-l', str(path)], 5)


def emergency_failover(reason: str) -> None:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if mode() != 'nvme':
            return
        ok, current = healthy_nvme()
        if ok:
            return
        reason = f'{reason}; {current}'
        log(f'NVMe fault detected: {reason}')
        cid = container_id()
        # Make workers fail closed immediately, then release dead filesystem handles.
        STATE.write_text(json.dumps({'mode': 'quiesce', 'reason': reason}), encoding='utf-8')
        run(['systemctl', 'stop', 'livevault-backup.timer'], 5)
        run(['systemctl', 'stop', 'gpt-harness.service'], 15)
        if cid:
            run(['docker', 'stop', '--time', '20', cid], 30)
        detach(REC)
        detach(INTERNAL_REC)
        detach(SHARE)
        detach(NVME)
        REC.mkdir(parents=True, exist_ok=True)
        if not mount_info(BUFFER)[0]:
            raise RuntimeError('Internal recording buffer is not mounted')
        result = run(['mount', '--bind', str(BUFFER), str(REC)], 8)
        if result.returncode != 0:
            raise RuntimeError('Cannot bind internal recording buffer')
        run(['mount', '--make-rshared', '/data'], 5)
        publish_buffer(reason)
        if cid:
            run(['docker', 'start', cid], 20)
        run(['systemctl', 'start', 'gpt-harness.service'], 15)
        log('Failover complete: LiveVault on internal 4 GiB buffer')


def main() -> None:
    last_report = ''
    while True:
        try:
            if mode() == 'nvme':
                ok, reason = healthy_nvme()
                if not ok:
                    emergency_failover(reason)
                    last_report = ''
                elif last_report != 'ok':
                    log('NVMe watchdog healthy')
                    last_report = 'ok'
            else:
                last_report = ''
        except Exception as exc:
            log(f'watchdog error: {type(exc).__name__}: {exc}')
        time.sleep(2)


if __name__ == '__main__':
    main()
