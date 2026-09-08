from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_media_reconcile_unchanged_snapshot_does_no_mount_work(monkeypatch, tmp_path):
    manager = _load(ROOT / 'scripts/openastro-media-manager.py', 'media_manager_cost_test')
    manager.MEDIA_ROOT = tmp_path / 'media'
    manager.MEDIA_ROOT.mkdir()
    manager.RECONCILE_STATE = tmp_path / 'state.json'
    item = {
        'uuid': 'USB-1', 'label': 'Films', 'fstype': 'exfat', 'device': '/dev/sdb1',
        'disk': '/dev/sdb', 'mountpoint': str(manager.MEDIA_ROOT / 'Films-USB1'),
    }
    present = {'USB-1': item}
    mounts = {
        item['mountpoint']: {'source': '/dev/sdb1', 'major_minor': '8:17', 'options': {'rw', 'nosuid'}}
    }
    manager.UUID_DIR = tmp_path / 'by-uuid'
    manager.UUID_DIR.mkdir()
    signature = manager._kernel_reconcile_signature(mounts)
    manager.RECONCILE_STATE.write_text(json.dumps({'signature': signature}))
    monkeypatch.setattr(manager, 'discover', lambda: (_ for _ in ()).throw(AssertionError('lsblk discovery should be skipped')))
    monkeypatch.setattr(manager, 'mount_table', lambda: mounts)
    monkeypatch.setattr(manager, 'mount_media', lambda *a, **k: (_ for _ in ()).throw(AssertionError('mount work should be skipped')))
    monkeypatch.setattr(manager, 'restart_indexer', lambda: (_ for _ in ()).throw(AssertionError('indexer should not restart')))
    manager.reconcile()


def test_media_manager_steady_reconcile_source_has_no_findmnt():
    source = (ROOT / 'scripts/openastro-media-manager.py').read_text(encoding='utf-8')
    assert '["findmnt"' not in source
    assert "MOUNTINFO = Path(\"/proc/1/mountinfo\")" in source
    assert 'RECONCILE_STATE = Path("/run/openastro-media-reconcile-state.json")' in source


def test_media_center_shares_discovery_snapshot(monkeypatch):
    media = _load(ROOT / 'control-panel/media_center.py', 'media_center_cost_test')
    media._DISCOVERY_CACHE = (0.0, [])
    calls = []
    payload = [{'uuid': 'USB-1', 'mountpoint': '/tmp/media'}]

    def fake_run(args, timeout=8):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr='')

    monkeypatch.setattr(media, '_run', fake_run)
    assert media._discover() == payload
    assert media._discover() == payload
    assert len(calls) == 1
    media.invalidate_device_cache()
    assert media._discover() == payload
    assert len(calls) == 2


def test_media_center_caches_service_state_briefly(monkeypatch):
    media = _load(ROOT / 'control-panel/media_center.py', 'media_center_service_cost_test')
    media._SERVICE_CACHE = {}
    calls = []
    monkeypatch.setattr(media, '_run', lambda args, timeout=8: calls.append(args) or SimpleNamespace(returncode=0, stdout='active\n', stderr=''))
    assert media._service('smbd.service') == 'active'
    assert media._service('smbd.service') == 'active'
    assert len(calls) == 1
