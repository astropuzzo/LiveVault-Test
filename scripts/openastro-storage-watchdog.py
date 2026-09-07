#!/usr/bin/env python3
"""OpenAstro recording-storage watchdog.

System/Docker/state live on internal eMMC. The server NVMe is only the heavy
recording tier. If that removable tier disappears or becomes unusable, request
a serialized emergency handoff to the bounded eMMC recording buffer.
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


def log(message: str) -> None:
    line = f'{time.strftime("%Y-%m-%dT%H:%M:%S%z")} {message}'
    print(line, flush=True)
    try:
        with LOG.open('a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except OSError:
        pass


def run(args: list[str], timeout: int = 5) -> subprocess.CompletedProcess[str]:
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
    result = run(['findmnt', '-nro', 'SOURCE,OPTIONS', '--mountpoint', str(path)], 2)
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


def expected_device() -> str | None:
    """Resolve the UUID symlink without probing filesystem media."""
    link = Path('/dev/disk/by-uuid') / UUID
    try:
        return str(link.resolve(strict=True))
    except OSError:
        return None


def kernel_device_running(device: str | None) -> bool:
    if not device:
        return False
    name = Path(device).name
    # Partitions do not have their own SCSI state; resolve their parent via lsblk.
    result = run(['lsblk', '-nro', 'PKNAME', device], 2)
    parent = result.stdout.strip() if result.returncode == 0 else ''
    if parent:
        name = parent
    state = Path('/sys/class/block') / name / 'device/state'
    if not state.exists():
        return True
    try:
        return state.read_text().strip().lower() in {'running', 'live'}
    except OSError:
        return False


def healthy_nvme() -> tuple[bool, str]:
    """Use mount-table/devfs/sysfs checks only; never block on data I/O to a dead USB disk."""
    rec_source, rec_options = mount_info(REC)
    nvme_source, nvme_options = mount_info(NVME)
    rec_dev = device_from_source(rec_source)
    nvme_dev = device_from_source(nvme_source)
    expected = expected_device()
    if not expected:
        return False, 'NVMe UUID device missing'
    if not rec_dev:
        return False, 'recordings source device missing'
    if not nvme_dev:
        return False, 'NVMe mount source device missing'
    try:
        if Path(rec_dev).resolve() != Path(expected).resolve() or Path(nvme_dev).resolve() != Path(expected).resolve():
            return False, 'filesystem UUID device mismatch'
    except OSError:
        return False, 'filesystem device disappeared'
    bad = {'ro', 'shutdown', 'emergency_ro'}
    unhealthy = (rec_options | nvme_options) & bad
    if unhealthy:
        return False, f'filesystem unhealthy options={sorted(unhealthy)}'
    if 'rw' not in rec_options or 'rw' not in nvme_options:
        return False, 'filesystem not writable'
    if not kernel_device_running(expected):
        return False, 'kernel block device not running'
    # Do not call statvfs/blkid/read/write here. Those calls can themselves enter
    # uninterruptible D-state when a USB-NVMe bridge has failed.
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
