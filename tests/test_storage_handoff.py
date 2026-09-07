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
    h.mark_full()
    # Recorder closure can free temporary remux space. That must not cause an
    # endless stop/restart loop before the NVMe is returned.
    assert not h.capture_allowed()


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


def test_host_buffer_limit_is_four_gib(transfer):
    assert transfer.BUFFER_LIMIT_BYTES == 4 * 1024**3


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


def test_switch_reasserts_shared_data_mount_before_bind(monkeypatch, transfer):
    calls = []
    monkeypatch.setattr(transfer, 'run', lambda *args: calls.append(args) or '')
    monkeypatch.setattr(transfer.os.path, 'ismount', lambda _path: True)
    transfer.switch(Path('/tmp/fake-buffer'))
    assert calls[0] == ('mount', '--make-rshared', '/data')
    assert calls[1] == ('umount', '/data/livevault/recordings')
    assert calls[2] == ('mount', '--bind', '/tmp/fake-buffer', '/data/livevault/recordings')


def test_container_view_transient_propagation_does_not_restart(monkeypatch, tmp_path, transfer):
    monkeypatch.setattr(transfer, 'RECORDINGS', tmp_path)
    expected = tmp_path.stat().st_dev
    monkeypatch.setattr(transfer, 'wait_container_view', lambda _expected, _timeout: (True, {'live': expected}))
    monkeypatch.setattr(transfer, 'livevault_containers', lambda: ['live'])
    calls = []
    monkeypatch.setattr(transfer, 'run', lambda *args: calls.append(args) or '')
    transfer.verify_container_view()
    assert not any(call[:2] == ('docker', 'restart') for call in calls)


def test_container_view_stale_mount_restarts_only_livevault_then_rechecks(monkeypatch, tmp_path, transfer):
    monkeypatch.setattr(transfer, 'RECORDINGS', tmp_path)
    expected = tmp_path.stat().st_dev
    results = iter([(False, {'live': expected + 1}), (True, {'live': expected})])
    monkeypatch.setattr(transfer, 'wait_container_view', lambda _expected, _timeout: next(results))
    monkeypatch.setattr(transfer, 'livevault_containers', lambda: ['live'])
    calls = []
    monkeypatch.setattr(transfer, 'run', lambda *args: calls.append(args) or '')
    transfer.verify_container_view()
    assert ('docker', 'restart', '--time', '30', 'live') in calls


def test_container_view_persistent_mismatch_fails_closed(monkeypatch, tmp_path, transfer):
    monkeypatch.setattr(transfer, 'RECORDINGS', tmp_path)
    expected = tmp_path.stat().st_dev
    monkeypatch.setattr(transfer, 'wait_container_view', lambda _expected, _timeout: (False, {'live': expected + 1}))
    monkeypatch.setattr(transfer, 'livevault_containers', lambda: ['live'])
    monkeypatch.setattr(transfer, 'run', lambda *args: '')
    with pytest.raises(RuntimeError, match='NVMe NON espulso'):
        transfer.verify_container_view()


def test_failed_eject_rollback_restores_backup_timer_and_share(monkeypatch, transfer, tmp_path):
    root = tmp_path / 'data' / 'livevault'
    nvme = tmp_path / 'nvme'
    buffer = tmp_path / 'buffer'
    rec = root / 'recordings'
    (nvme / 'livevault' / 'recordings').mkdir(parents=True)
    buffer.mkdir(parents=True)
    rec.mkdir(parents=True)
    (root / 'storage-state.json').write_text(json.dumps({'mode': 'nvme'}))
    monkeypatch.setattr(transfer, 'ROOT', root)
    monkeypatch.setattr(transfer, 'NVME', nvme)
    monkeypatch.setattr(transfer, 'BUFFER', buffer)
    monkeypatch.setattr(transfer, 'RECORDINGS', rec)
    monkeypatch.setattr(transfer, 'service_active', lambda name: name in {'livevault-backup.timer'})
    monkeypatch.setattr(transfer.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(transfer, 'open', lambda *_args, **_kwargs: (tmp_path / 'handoff.lock').open('w'), raising=False)
    mounted = {str(nvme): True, '/share': True, str(rec): True}
    monkeypatch.setattr(transfer.os.path, 'ismount', lambda path: mounted.get(str(path), False))
    monkeypatch.setattr(transfer, 'quiesce', lambda: None)
    monkeypatch.setattr(transfer, 'switch', lambda source: None)
    monkeypatch.setattr(transfer, 'verify_container_view', lambda: (_ for _ in ()).throw(RuntimeError('propagation failed')))
    calls = []
    def fake_run(*args, **kwargs):
        calls.append(tuple(args))
        if args[:2] == ('systemctl', 'stop'):
            return ''
        return ''
    monkeypatch.setattr(transfer, 'run', fake_run)
    popen_calls = []
    monkeypatch.setattr(transfer.subprocess, 'run', lambda args, **kwargs: popen_calls.append(tuple(args)) or SimpleNamespace(returncode=0, stdout='', stderr=''))
    with pytest.raises(RuntimeError, match='propagation failed'):
        transfer.main('eject')
    assert ('systemctl', 'start', 'livevault-backup.timer') in popen_calls
