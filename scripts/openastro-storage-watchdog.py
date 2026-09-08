#!/usr/bin/env python3
"""OpenAstro recording-storage watchdog.

System/Docker/state live on internal eMMC. The server NVMe is only the heavy
recording tier. If that removable tier disappears or becomes unusable, request
a serialized emergency handoff to the bounded eMMC recording buffer.

The steady-state check is intentionally fork-free: it reads PID-1/host mount
information from procfs and block state from sysfs. Never add filesystem data
reads against the SERVER NVMe here; a failed USB bridge can make them block in
uninterruptible D-state.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import time

UUID = '5fe2d0f6-b485-44e9-8e26-31fb0d217db2'
STATE = Path('/data/livevault/storage-state.json')
REC = Path('/data/livevault/recordings')
NVME = Path('/mnt/livevault-nvme')
BUFFER = Path('/var/lib/livevault-buffer')
HANDOFF = Path('/usr/local/libexec/nvme-handoff.py')
LOG = Path('/var/log/openastro-storage-watchdog.log')
MOUNTINFO = Path('/proc/1/mountinfo')
SYS_DEV_BLOCK = Path('/sys/dev/block')
SYS_CLASS_BLOCK = Path('/sys/class/block')


def log(message: str) -> None:
    line = f'{time.strftime("%Y-%m-%dT%H:%M:%S%z")} {message}'
    print(line, flush=True)
    try:
        with LOG.open('a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except OSError:
        pass


def run(args: list[str], timeout: int = 5) -> subprocess.CompletedProcess[str]:
    """Reserved for the exceptional failover helper, never the 2-second health path."""
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(args, 124, '', '')


def mode() -> str:
    try:
        return str(json.loads(STATE.read_text()).get('mode', 'unknown'))
    except Exception:
        return 'unknown'


def _mount_unescape(value: str) -> str:
    """Decode the octal escapes used by /proc/*/mountinfo."""
    for encoded, literal in (('\\040', ' '), ('\\011', '\t'), ('\\012', '\n'), ('\\134', '\\')):
        value = value.replace(encoded, literal)
    return value


def mount_table() -> dict[str, dict]:
    """Return host mount metadata keyed by target without spawning findmnt."""
    rows: dict[str, dict] = {}
    try:
        lines = MOUNTINFO.read_text(encoding='utf-8').splitlines()
    except OSError:
        return rows
    for line in lines:
        left, sep, right = line.partition(' - ')
        if not sep:
            continue
        fields = left.split()
        tail = right.split()
        if len(fields) < 6 or len(tail) < 3:
            continue
        target = _mount_unescape(fields[4])
        major_minor = fields[2]
        root = _mount_unescape(fields[3])
        options = set(fields[5].split(',')) | set(tail[2].split(','))
        rows[target] = {
            'major_minor': major_minor,
            'root': root,
            'source': _mount_unescape(tail[1]),
            'fstype': tail[0],
            'options': options,
        }
    return rows


def expected_device() -> str | None:
    """Resolve the UUID symlink without probing filesystem media."""
    link = Path('/dev/disk/by-uuid') / UUID
    try:
        return str(link.resolve(strict=True))
    except OSError:
        return None


def device_major_minor(device: str | None) -> str | None:
    if not device:
        return None
    try:
        info = os.stat(device)
    except OSError:
        return None
    if not stat.S_ISBLK(info.st_mode):
        return None
    return f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'


def _block_name(major_minor: str) -> str | None:
    try:
        return (SYS_DEV_BLOCK / major_minor).resolve(strict=True).name
    except OSError:
        return None


def _parent_block_name(name: str) -> str:
    """Resolve a partition to its disk parent through sysfs, without lsblk."""
    node = SYS_CLASS_BLOCK / name
    try:
        if (node / 'partition').exists():
            resolved = node.resolve(strict=True)
            return resolved.parent.name
    except OSError:
        pass
    return name


def kernel_device_running(major_minor: str | None) -> bool:
    if not major_minor:
        return False
    name = _block_name(major_minor)
    if not name:
        return False
    parent = _parent_block_name(name)
    state = SYS_CLASS_BLOCK / parent / 'device/state'
    if not state.exists():
        return True
    try:
        return state.read_text(encoding='utf-8').strip().lower() in {'running', 'live'}
    except OSError:
        return False


def healthy_nvme() -> tuple[bool, str]:
    """Use host mount-table/devfs/sysfs checks only; never issue data I/O to SERVER."""
    expected = expected_device()
    expected_mm = device_major_minor(expected)
    if not expected or not expected_mm:
        return False, 'NVMe UUID device missing'

    mounts = mount_table()
    rec = mounts.get(str(REC))
    nvme = mounts.get(str(NVME))
    if not rec:
        return False, 'recordings mount missing'
    if not nvme:
        return False, 'NVMe mount missing'
    if rec['major_minor'] != expected_mm or nvme['major_minor'] != expected_mm:
        return False, 'filesystem UUID device mismatch'

    bad = {'ro', 'shutdown', 'emergency_ro'}
    unhealthy = (rec['options'] | nvme['options']) & bad
    if unhealthy:
        return False, f'filesystem unhealthy options={sorted(unhealthy)}'
    if 'rw' not in rec['options'] or 'rw' not in nvme['options']:
        return False, 'filesystem not writable'
    if not kernel_device_running(expected_mm):
        return False, 'kernel block device not running'
    return True, 'ok'


def request_failover(reason: str) -> tuple[bool, str]:
    if not HANDOFF.is_file():
        return False, 'handoff helper missing'
    result = run(['/usr/bin/python3', str(HANDOFF), 'failover', reason[:1200]], 120)
    detail = (result.stdout or result.stderr or '').strip()
    return result.returncode == 0, detail


def main() -> None:
    last_report = ''
    while True:
        try:
            current_mode = mode()
            if current_mode == 'nvme':
                ok, reason = healthy_nvme()
                if not ok:
                    log(f'NVMe fault detected: {reason}')
                    succeeded, detail = request_failover(reason)
                    if succeeded:
                        log(f'Failover complete: {detail or "recordings on internal eMMC buffer"}')
                    else:
                        log(f'Failover request failed: {detail or "unknown error"}')
                    last_report = ''
                elif last_report != 'ok':
                    log('NVMe watchdog healthy')
                    last_report = 'ok'
            elif current_mode == 'buffer':
                # Buffer mode is healthy degraded operation. Reattachment is driven
                # by the UUID-specific udev/systemd attach service when NVMe returns.
                last_report = 'buffer'
            else:
                last_report = ''
        except Exception as exc:
            log(f'watchdog error: {type(exc).__name__}: {exc}')
        time.sleep(2)


if __name__ == '__main__':
    main()
