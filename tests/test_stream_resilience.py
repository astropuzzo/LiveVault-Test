from __future__ import annotations

import subprocess
from pathlib import Path

import app.utils as utils


ROOT = Path(__file__).resolve().parents[1]
RECORDER = (ROOT / "app/recorder.py").read_text(encoding="utf-8")
WORKERS = (ROOT / "app/workers.py").read_text(encoding="utf-8")


def test_stitch_and_copy_remux_watch_real_progress_not_assumed_mib_per_second():
    assert "async def _wait_media_process" in RECORDER
    assert "MEDIA_IDLE_TIMEOUT_SECONDS = 300.0" in RECORDER
    assert "MEDIA_HARD_TIMEOUT_SECONDS = 6 * 60 * 60" in RECORDER
    assert 'operation="Stitching sessione"' in RECORDER
    assert 'operation="Finalizzazione MP4"' in RECORDER
    assert "total_bytes / (4 * 1024**2)" not in RECORDER
    assert "source.stat().st_size / (4 * 1024**2)" not in RECORDER
    assert "Stitching sessione scaduto" not in RECORDER
    assert "Finalizzazione MP4 scaduta" not in RECORDER


def test_quick_fragment_probe_times_out_as_deferred_not_minute_error(monkeypatch, tmp_path):
    path = tmp_path / "part000.mp4"
    path.write_bytes(b"not-empty")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(utils.subprocess, "run", timeout)
    result = utils.probe_media(path, quick=True)

    assert result.ok is False
    assert "verifica differita" in result.error
    assert "60 secondi" not in result.error


def test_live_preview_prefers_active_capture_and_orphan_errors_are_pruned():
    assert "preferred = self.playable_active_capture_path(source_id)" in WORKERS
    assert "candidates[:3]" in WORKERS
    assert "def _clear_orphan_source_errors" in WORKERS
    assert "self._clear_orphan_source_errors()" in WORKERS


def test_live_preview_uses_duration_relative_input_seek_before_sseof():
    source = (ROOT / "app/utils.py").read_text(encoding="utf-8")
    duration_seek = 'attempts.append(["-ss", f"{max(0.5, duration - 4.0):.3f}"])'
    eof_seek = 'attempts.extend((["-sseof", "-6"], ["-ss", "0.5"]))'
    assert duration_seek in source
    assert eof_seek in source
    assert source.index(duration_seek) < source.index(eof_seek)
