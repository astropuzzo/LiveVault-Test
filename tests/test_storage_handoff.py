import asyncio
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import storage_handoff as h


def test_buffer_parts_leave_real_capture_capacity_after_trailer_reserve():
    from app.recorder import safe_output_limit_bytes
    assert safe_output_limit_bytes(h.BUFFER_SEGMENT_GB) == 64 * 1024**2


@pytest.fixture
def control(tmp_path, monkeypatch):
    monkeypatch.setattr(h, 'settings', SimpleNamespace(data_dir=tmp_path, recordings_dir=tmp_path))
    def mode(value):
        (tmp_path / 'storage-state.json').write_text(json.dumps({'mode': value}))
    return mode


def test_invalid_control_fails_closed(control, tmp_path):
    control('broken')
    assert not h.capture_allowed()
    assert not h.media_online()
    (tmp_path / 'storage-state.json').write_text('{')
    assert not h.capture_allowed()


def test_buffer_reserves_space_and_never_enables_archive_jobs(control, monkeypatch):
    control('buffer')
    monkeypatch.setattr('shutil.disk_usage', lambda _: SimpleNamespace(free=h.BUFFER_RESERVE))
    assert not h.capture_allowed()
    assert not h.media_online()
    monkeypatch.setattr('shutil.disk_usage', lambda _: SimpleNamespace(free=h.BUFFER_RESERVE + 1))
    assert h.capture_allowed()


def test_quiesce_drains_existing_job_without_cancelling_it(control):
    async def exercise():
        entered, finish = asyncio.Event(), asyncio.Event()
        owner = SimpleNamespace(_storage_jobs=0)
        @h.media_job
        async def job(self):
            entered.set()
            await finish.wait()
            return 42
        control('nvme')
        running = asyncio.create_task(job(owner))
        await entered.wait()
        control('quiesce')
        assert owner._storage_jobs == 1
        assert await job(owner) is None
        finish.set()
        assert await running == 42
        assert owner._storage_jobs == 0
    asyncio.run(exercise())


@pytest.fixture
def transfer():
    spec = importlib.util.spec_from_file_location('handoff_script', Path(__file__).parents[1] / 'scripts/nvme-handoff.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name != 'posix', reason='host transfer uses Linux directory fsync')
def test_transfer_is_restartable_and_preserves_original_marker(tmp_path, transfer):
    source, dest = tmp_path / 'buffer', tmp_path / 'nvme'
    source.mkdir(); dest.mkdir()
    marker = '.livevault-stitch-session.json'
    (source / marker).write_text(json.dumps({'source_id': 1, 'session_id': 'same', 'started_at': 'later'}))
    (dest / marker).write_text(json.dumps({'source_id': 1, 'session_id': 'same', 'started_at': 'earlier'}))
    (source / 'part.mp4').write_bytes(b'footage')
    # Simulate power loss after destination rename, before source unlink.
    (dest / 'part.mp4').write_bytes(b'footage')
    transfer.merge_buffer(source, dest)
    assert (dest / 'part.mp4').read_bytes() == b'footage'
    assert json.loads((dest / marker).read_text())['started_at'] == 'earlier'
    assert not list(source.iterdir())
    transfer.merge_buffer(source, dest)


def test_transfer_refuses_conflicting_media(tmp_path, transfer):
    source, dest = tmp_path / 'buffer', tmp_path / 'nvme'
    source.mkdir(); dest.mkdir()
    (source / 'part.mp4').write_bytes(b'original')
    (dest / 'part.mp4').write_bytes(b'different')
    with pytest.raises(RuntimeError, match='Conflicting'):
        transfer.merge_buffer(source, dest)
    assert (source / 'part.mp4').read_bytes() == b'original'
    assert (dest / 'part.mp4').read_bytes() == b'different'
