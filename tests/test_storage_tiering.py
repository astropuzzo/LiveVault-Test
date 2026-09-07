from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_watchdog():
    path = ROOT / "scripts/openastro-storage-watchdog.py"
    spec = importlib.util.spec_from_file_location("storage_watchdog_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_storage_watchdog_never_probes_dead_media_with_data_io():
    source = (ROOT / "scripts/openastro-storage-watchdog.py").read_text(encoding="utf-8")
    assert "os.statvfs(" not in source
    assert "['blkid'" not in source
    assert "openastro-storage-watchdog" in source or "recording-storage watchdog" in source
    assert "HANDOFF" in source
    assert "'failover'" in source


def test_watchdog_delegates_fault_to_serialized_handoff(monkeypatch):
    watchdog = _load_watchdog()
    watchdog.HANDOFF = Path("/tmp/nvme-handoff.py")
    monkeypatch.setattr(Path, "is_file", lambda self: str(self) == "/tmp/nvme-handoff.py")
    seen = []

    def fake_run(args, timeout=5):
        seen.append((list(args), timeout))
        return SimpleNamespace(returncode=0, stdout="buffer ready", stderr="")

    monkeypatch.setattr(watchdog, "run", fake_run)
    ok, detail = watchdog.request_failover("device missing")

    assert ok is True
    assert detail == "buffer ready"
    assert seen == [
        (["/usr/bin/python3", "/tmp/nvme-handoff.py", "failover", "device missing"], 120)
    ]


def test_emergency_handoff_keeps_docker_daemon_online_by_contract():
    source = (ROOT / "scripts/nvme-handoff.py").read_text(encoding="utf-8")
    start = source.index("def emergency_failover")
    end = source.index("def wait_closed_device_handles", start)
    body = source[start:end]
    assert "preposition_container_views(containers, BUFFER)" in body
    assert "['docker', 'stop'" in body  # last-resort app-container fallback only
    assert "systemctl', 'stop', 'docker" not in body
    assert "Docker resta online" in body
    assert "emergency_detach(RECORDINGS)" in body
    assert "mount', '--bind', str(BUFFER), str(RECORDINGS)" in body


def test_media_center_runtime_state_is_internal_not_on_removable_media():
    center = (ROOT / "control-panel/media_center.py").read_text(encoding="utf-8")
    streaming = (ROOT / "control-panel/media_streaming.py").read_text(encoding="utf-8")
    assert "STATE_ROOT = Path('/var/lib/openastro-control')" in center
    assert "MEDIA_ROOT = Path('/srv/openastro-media')" in center
    assert "STATE_ROOT = Path('/var/lib/openastro-control')" in streaming
    assert "HLS_ROOT = STATE_ROOT / 'media-hls'" in streaming
    assert "SUB_ROOT = STATE_ROOT / 'media-subs'" in streaming


def test_internal_runtime_migration_places_docker_on_data_emmc_tier():
    source = (ROOT / "scripts/migrate-internal-runtime.sh").read_text(encoding="utf-8")
    assert "payload['data-root'] = '/data/docker'" in source
    assert "/srv/openastro-internal /data" in source or "/srv/openastro-internal" in source
