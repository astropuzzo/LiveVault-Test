#!/usr/bin/env python3
"""Privileged, serialized NVMe handoff. Never lazy-unmount or discard a buffer."""
from __future__ import annotations

import ctypes
import errno
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
CONTAINER_VIEW_SETTLE_SECONDS = 1.0
CONTAINER_VIEW_REPAIR_SECONDS = 3.0
CONTAINER_VIEW_POLL_SECONDS = 0.20
CONTAINER_RECORDINGS_PATH = '/data/recordings'
SYS_OPEN_TREE = 428
SYS_MOVE_MOUNT = 429
AT_FDCWD = -100
OPEN_TREE_CLONE = 1
OPEN_TREE_CLOEXEC = os.O_CLOEXEC
MOVE_MOUNT_F_EMPTY_PATH = 0x00000004


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
    # Docker receives recording submount changes through an rslave bind of /data.
    # Reassert the host side as rshared before every switch so a drifted mount
    # propagation flag cannot strand the container on the previous filesystem.
    run('mount', '--make-rshared', str(ROOT.parent))
    # A container-side bind can keep the current host bind busy even while the
    # application is fully quiesced. Move each LiveVault container to the new
    # filesystem first using the kernel mount-clone primitive; only then replace
    # the host bind. This keeps eject/attach independent of propagation timing.
    containers = livevault_containers()
    if containers:
        preposition_container_views(containers, source)
    if os.path.ismount(RECORDINGS):
        run('umount', str(RECORDINGS))
    run('mount', '--bind', str(source), str(RECORDINGS))


def livevault_containers():
    return [item for item in run('docker', 'ps', '-q', '--filter', 'name=ahul2vdjkyvjiwgzpcrmxzfe').splitlines() if item]


def container_recordings_devices(container):
    """Return every mount stacked exactly on /data/recordings, bottom to top."""
    code = (
        'import os\n'
        'out=[]\n'
        'for line in open("/proc/self/mountinfo"):\n'
        ' p=line.split()\n'
        f' if len(p)>4 and p[4]=={CONTAINER_RECORDINGS_PATH!r}:\n'
        '  a,b=map(int,p[2].split(":")); out.append(os.makedev(a,b))\n'
        'print(",".join(map(str,out)))\n'
    )
    output = run('docker', 'exec', container, 'python', '-c', code)
    return tuple(int(item) for item in output.split(',') if item)


def container_mountpoints_on_device(container, device):
    code = (
        'import os\n'
        f'wanted={int(device)}\n'
        'out=[]\n'
        'for line in open("/proc/self/mountinfo"):\n'
        ' p=line.split()\n'
        ' if len(p)>4:\n'
        '  a,b=map(int,p[2].split(":"))\n'
        '  if os.makedev(a,b)==wanted: out.append(p[4])\n'
        'print("\\n".join(out))\n'
    )
    output = run('docker', 'exec', container, 'python', '-c', code)
    return [item for item in output.splitlines() if item]


def wait_container_view(expected, timeout):
    """Wait for exactly one desired recording mount in every running container."""
    deadline = time.monotonic() + timeout
    last = {}
    while True:
        containers = livevault_containers()
        if containers:
            current = {}
            for container in containers:
                try:
                    current[container] = container_recordings_devices(container)
                except (subprocess.CalledProcessError, ValueError):
                    current[container] = None
            last = current
            if current and all(devices == (expected,) for devices in current.values()):
                return True, current
        else:
            last = {}
        if time.monotonic() >= deadline:
            return False, last
        time.sleep(CONTAINER_VIEW_POLL_SECONDS)


def _mount_clone_into_namespace(pid, source, target, expected):
    """Clone a host mount and attach it inside another mount namespace.

    This uses the modern mount API so correctness does not depend on Docker
    shared-subtree propagation. The operation runs in a forked child because
    setns(2) permanently changes the caller's mount namespace.
    """
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            mount_fd = libc.syscall(
                SYS_OPEN_TREE,
                AT_FDCWD,
                os.fsencode(source),
                OPEN_TREE_CLONE | OPEN_TREE_CLOEXEC,
            )
            if mount_fd < 0:
                code = ctypes.get_errno()
                raise OSError(code, f'open_tree: {os.strerror(code)}')
            try:
                ns_fd = os.open(f'/proc/{pid}/ns/mnt', os.O_RDONLY | os.O_CLOEXEC)
                try:
                    if libc.setns(ns_fd, 0) != 0:
                        code = ctypes.get_errno()
                        raise OSError(code, f'setns: {os.strerror(code)}')
                finally:
                    os.close(ns_fd)

                target_b = os.fsencode(target)
                # Pop every mount stacked on the target. A bind-over alone would
                # hide the old NVMe mount while still retaining the device.
                for _ in range(8):
                    if libc.umount2(target_b, 0) == 0:
                        continue
                    code = ctypes.get_errno()
                    if code in {errno.EINVAL, errno.ENOENT}:
                        break
                    raise OSError(code, f'umount2({target}): {os.strerror(code)}')
                else:
                    raise RuntimeError(f'Troppi mount sovrapposti su {target}')

                if libc.syscall(
                    SYS_MOVE_MOUNT,
                    mount_fd,
                    b'',
                    AT_FDCWD,
                    target_b,
                    MOVE_MOUNT_F_EMPTY_PATH,
                ) != 0:
                    code = ctypes.get_errno()
                    raise OSError(code, f'move_mount: {os.strerror(code)}')
            finally:
                os.close(mount_fd)

            actual = os.stat(target).st_dev
            if actual != expected:
                raise RuntimeError(f'Namespace repair device errato: {actual} != {expected}')
        except BaseException as exc:
            try:
                os.write(write_fd, f'{type(exc).__name__}: {exc}'.encode()[:4096])
            finally:
                os.close(write_fd)
            os._exit(1)
        os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    chunks = []
    while True:
        chunk = os.read(read_fd, 4096)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(read_fd)
    _, status = os.waitpid(child, 0)
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        detail = b''.join(chunks).decode(errors='replace') or f'child status={status}'
        raise RuntimeError(f'Riallineamento mount namespace fallito: {detail}')


def preposition_container_views(containers, source):
    """Move containers to the next filesystem before the host bind is replaced."""
    expected = Path(source).stat().st_dev
    for container in containers:
        pid = run('docker', 'inspect', '-f', '{{.State.Pid}}', container)
        if not pid.isdigit() or int(pid) <= 0:
            raise RuntimeError(f'Container LiveVault non eseguibile durante handoff: {container}')
        _mount_clone_into_namespace(int(pid), source, CONTAINER_RECORDINGS_PATH, expected)


def repair_container_views(containers, expected):
    """Replace stale /data/recordings mounts without relying on propagation."""
    for container in containers:
        pid = run('docker', 'inspect', '-f', '{{.State.Pid}}', container)
        if not pid.isdigit() or int(pid) <= 0:
            raise RuntimeError(f'Container LiveVault non eseguibile durante handoff: {container}')
        _mount_clone_into_namespace(int(pid), RECORDINGS, CONTAINER_RECORDINGS_PATH, expected)


def verify_container_view():
    expected = RECORDINGS.stat().st_dev
    ok, seen = wait_container_view(expected, CONTAINER_VIEW_SETTLE_SECONDS)
    if ok:
        return

    containers = livevault_containers()
    if not containers:
        raise RuntimeError('LiveVault non disponibile: handoff annullato')

    repair_container_views(containers, expected)
    ok, seen = wait_container_view(expected, CONTAINER_VIEW_REPAIR_SECONDS)
    if ok:
        return

    detail = ', '.join(f'{container}={actual}' for container, actual in sorted(seen.items())) or 'nessuna vista leggibile'
    raise RuntimeError(
        f'Mount container non riallineato dopo repair: host={expected}; container={detail}. NVMe NON espulso'
    )


def verify_containers_detached_from_device(device):
    stale = {}
    for container in livevault_containers():
        paths = container_mountpoints_on_device(container, device)
        if paths:
            stale[container] = paths
    if stale:
        detail = '; '.join(f'{container}={paths}' for container, paths in sorted(stale.items()))
        raise RuntimeError(f'Container mantiene ancora mount NVMe: {detail}. NVMe NON espulso')

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



def wait_storage_ready(token, timeout=8.0):
    """Give LiveVault a short chance to close writers; emergency failover must not depend on it."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        try:
            if json.loads((ROOT / 'storage-ready.json').read_text()).get('token') == token:
                return True
        except (OSError, ValueError):
            pass
        time.sleep(.20)
    return False


def emergency_detach(path):
    """Detach an already-failed removable filesystem without waiting forever on dead I/O."""
    if not os.path.ismount(path):
        return
    result = subprocess.run(['umount', str(path)], text=True, capture_output=True, timeout=8)
    if result.returncode == 0:
        return
    # Lazy detach is forbidden for normal/manual eject, but is appropriate after
    # the kernel has already lost or shut down the removable medium.
    result = subprocess.run(['umount', '-l', str(path)], text=True, capture_output=True, timeout=5)
    if result.returncode != 0:
        raise RuntimeError(f'Impossibile sganciare filesystem guasto {path}: {(result.stderr or result.stdout).strip()}')


def emergency_failover(reason='NVMe fault'):
    """Move recording writes to the eMMC buffer after an unexpected NVMe failure.

    Keep Docker and all system state on eMMC. Prefer a live mount-namespace move
    so the LiveVault container stays running; stop/start only that container as a
    last resort if an old dead-file handle prevents namespace repair.
    """
    previous = json.loads((ROOT / 'storage-state.json').read_text()).get('mode')
    if previous != 'nvme':
        return
    if not os.path.ismount(BUFFER):
        run('mount', str(BUFFER))
    token = uuid.uuid4().hex
    publish('quiesce', token=token, reason=str(reason)[:1200])
    subprocess.run(['systemctl', 'stop', 'livevault-backup.timer', 'livevault-backup.service'], capture_output=True)
    wait_storage_ready(token, 8.0)
    run('mount', '--make-rshared', '/data')

    containers = livevault_containers()
    stopped = []
    try:
        if containers:
            try:
                preposition_container_views(containers, BUFFER)
            except Exception:
                # Last resort only: Docker itself stays online; restart just the
                # LiveVault application container after host storage is on eMMC.
                for container in containers:
                    result = subprocess.run(['docker', 'stop', '--time', '15', container], text=True, capture_output=True)
                    if result.returncode == 0:
                        stopped.append(container)
                if len(stopped) != len(containers):
                    raise RuntimeError('Impossibile liberare il container LiveVault dal filesystem NVMe guasto')

        # With containers already moved/stopped, replace the host recording bind.
        emergency_detach(RECORDINGS)
        RECORDINGS.mkdir(parents=True, exist_ok=True)
        run('mount', '--bind', str(BUFFER), str(RECORDINGS))
        if containers and not stopped:
            verify_container_view()

        # SHARE is on the same physical server disk. GPT Harness has an optional
        # NVMe workspace view, so restart it from eMMC after detaching the disk.
        harness_was_active = service_active(GPT_HARNESS_SERVICE)
        if harness_was_active:
            set_service(GPT_HARNESS_SERVICE, 'stop')
        try:
            emergency_detach('/share')
            emergency_detach(NVME)
        finally:
            if harness_was_active:
                set_service(GPT_HARNESS_SERVICE, 'start')

        publish('buffer', limit_bytes=BUFFER_LIMIT_BYTES, reason=str(reason)[:1200])
        for container in stopped:
            result = subprocess.run(['docker', 'start', container], text=True, capture_output=True)
            if result.returncode != 0:
                raise RuntimeError(f'Buffer attivo ma LiveVault non ripartito: {(result.stderr or result.stdout).strip()}')
        if stopped:
            verify_container_view()
        print('Failover automatico completato: registrazioni sul buffer interno eMMC da 4 GiB; Docker resta online.')
    except Exception:
        # Never claim NVMe mode after a physical fault. If the eMMC bind exists,
        # publish buffer even if cleanup/restart of an ancillary component failed.
        try:
            if RECORDINGS.stat().st_dev == BUFFER.stat().st_dev:
                publish('buffer', limit_bytes=BUFFER_LIMIT_BYTES, reason=str(reason)[:1200], degraded=True)
        except OSError:
            pass
        raise


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
        if action == 'failover':
            emergency_failover(sys.argv[2] if len(sys.argv) > 2 else 'NVMe fault')
            return
        previous = json.loads((ROOT / 'storage-state.json').read_text())['mode']
        backup_timer_was_active = service_active('livevault-backup.timer')
        share_was_mounted = os.path.ismount('/share')
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
                verify_containers_detached_from_device(NVME.stat().st_dev)
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
            # Restore the complete pre-action service/storage state whenever the
            # expected medium is still physically present. A failed eject must
            # not silently leave backup disabled or /share detached.
            if previous == 'nvme' and not os.path.ismount(NVME) and Path('/dev/disk/by-uuid', DATA_UUID).exists():
                try:
                    run('mount', str(NVME))
                except subprocess.CalledProcessError:
                    pass
            source = NVME / 'livevault/recordings' if previous == 'nvme' else BUFFER
            restored = (previous != 'nvme' or os.path.ismount(NVME)) and source.is_dir()
            if restored:
                switch(source)
                try:
                    verify_container_view()
                except Exception:
                    # Preserve the original exception, but do not publish a mode
                    # that the running container has not positively adopted.
                    publish('quiesce', reason='rollback-container-view-failed')
                    raise
                publish(previous)
                if previous == 'nvme':
                    if share_was_mounted and not os.path.ismount('/share'):
                        subprocess.run(['mount', '/share'], capture_output=True)
                    if backup_timer_was_active:
                        subprocess.run(['systemctl', 'start', 'livevault-backup.timer'], capture_output=True)
            raise


if __name__ == '__main__':
    try:
        main(sys.argv[1])
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
