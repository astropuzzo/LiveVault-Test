"""Host-controlled storage handoff; metadata stays on the internal drive."""
from __future__ import annotations

import asyncio
import json
import subprocess
import time
from functools import wraps

from .config import settings

BUFFER_RESERVE = 128 * 1024**2
active_media_responses = 0


class StorageQuiesced(asyncio.CancelledError):
    """An archive operation closed its handles and can safely be retried."""


def checkpoint():
    if state()['mode'] == 'quiesce':
        raise StorageQuiesced('Operazione archivio rinviata per cambio storage')


def run_probe(args, *, timeout, **kwargs):
    """Cooperatively stop a read-only scan; join it before releasing the job."""
    if state()['mode'] == 'legacy':
        return subprocess.run(args, timeout=timeout, **kwargs)
    checkpoint()
    kwargs.pop('check', None)
    kwargs.pop('capture_output', None)
    deadline = time.monotonic() + timeout
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs) as proc:
        try:
            while True:
                checkpoint()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                try:
                    stdout, stderr = proc.communicate(timeout=min(.5, remaining))
                    return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
            raise


def state() -> dict:
    path = settings.data_dir / "storage-state.json"
    try:
        value = json.loads(path.read_text())
        if value.get("mode") not in {"nvme", "buffer", "quiesce"}:
            raise ValueError("Unknown storage mode")
        value['full'] = (settings.data_dir / '.storage-buffer-full').exists()
        return value
    except FileNotFoundError:
        return {"mode": "legacy"}
    except (OSError, ValueError, TypeError):
        # Fail closed if a configured handoff cannot be read.
        return {"mode": "quiesce", "error": "Invalid storage state"}


def media_online() -> bool:
    return state()["mode"] in {"legacy", "nvme"}


def capture_allowed(required_reserve: int = BUFFER_RESERVE) -> bool:
    import shutil
    mode = state()["mode"]
    if mode == "quiesce":
        return False
    if mode == "buffer":
        if (settings.data_dir / '.storage-buffer-full').exists():
            return False
        return shutil.disk_usage(settings.recordings_dir).free > max(BUFFER_RESERVE, required_reserve)
    return True


def mark_full() -> None:
    (settings.data_dir / '.storage-buffer-full').touch()


def media_job(function=None, *, buffering=False):
    """Drain started jobs; never cancel a thread while it still owns a file."""
    if function is None:
        return lambda f: media_job(f, buffering=buffering)
    @wraps(function)
    async def wrapped(self, *args, **kwargs):
        if not media_online() and not (buffering and state()["mode"] == "buffer"):
            return None
        self._storage_jobs += 1
        try:
            return await function(self, *args, **kwargs)
        except StorageQuiesced:
            # Cooperative checkpoints have already joined subprocesses/threads.
            # Preserve original media and let NVMe recovery retry this job.
            return None
        finally:
            self._storage_jobs -= 1
    return wrapped


def acknowledge(token: str) -> None:
    path = settings.data_dir / "storage-ready.json"
    try:
        if json.loads(path.read_text()).get('token') == token:
            return
    except (OSError, ValueError):
        pass
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"token": token}))
    temporary.replace(path)
