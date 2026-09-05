from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import app.workers as workers
from app.settings_store import RuntimeSettings


ROOT = Path(__file__).resolve().parents[1]


def test_session_join_window_default_and_dynamic_override(monkeypatch):
    assert RuntimeSettings().session_stitch_gap_minutes == 20
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(
        workers,
        "runtime",
        lambda: SimpleNamespace(session_stitch_gap_minutes=7),
    )
    assert workers.session_stitch_gap_seconds() == 7 * 60
    assert workers.stitch_gap_open(now - timedelta(minutes=6, seconds=59), now)
    assert workers.stitch_gap_open(now - timedelta(minutes=7), now)
    assert not workers.stitch_gap_open(now - timedelta(minutes=7, seconds=1), now)


def test_process_now_refuses_to_cut_an_active_capture():
    manager = workers.WorkerManager()
    manager._leader_file = object()
    manager.active[42] = SimpleNamespace(session_id="active-session")

    result = asyncio.run(manager.process_source_now(42))

    assert result["ok"] is False
    assert result["reason"] == "recording"


def test_processing_snapshot_is_exposed(monkeypatch):
    manager = workers.WorkerManager()
    manager.processing_current = {
        "source_id": 7,
        "source_name": "Example",
        "stage": "Verifica audio/video",
        "percent": 84.0,
    }
    monkeypatch.setattr(
        workers._legacy.WorkerManager,
        "snapshot",
        lambda self: {"active": [], "upload_current": None},
    )

    snapshot = manager.snapshot()

    assert snapshot["processing_current"]["source_id"] == 7
    assert snapshot["processing_current"]["percent"] == 84.0


def test_processing_routes_and_dashboard_controls_are_wired():
    main = (ROOT / "app/main/__init__.py").read_text(encoding="utf-8")
    settings = (ROOT / "app/settings_store.py").read_text(encoding="utf-8")
    workers_source = (ROOT / "app/workers/__init__.py").read_text(encoding="utf-8")
    js = (ROOT / "app/static/operations.js").read_text(encoding="utf-8")

    assert '@app.post("/api/sources/{source_id}/process-now")' in main
    assert '@app.patch("/api/session-processing/settings")' in main
    assert "session_stitch_gap_minutes: int = 20" in settings
    assert '"session_stitch_gap_minutes": s.session_stitch_gap_minutes' in settings
    assert "processing_current" in workers_source
    assert "force_source_id" in workers_source
    assert "Finalizza + upload ora" in js
    assert "processingProgressBar" in js
    assert "Finestra ricongiungimento (min)" in js
    assert "IN ATTESA DI FINALIZZAZIONE" in js
    assert "ELABORAZIONE" in js


def test_automatic_finalization_rolls_long_lives_and_manual_can_bypass():
    source = (ROOT / "app/workers/__init__.py").read_text(encoding="utf-8")
    assert "ready_seconds >= _legacy.SESSION_STITCH_READY_SECONDS" in source
    assert "if not forced and not self._stitch_group_ready(items, now):" in source
    assert "if forced:" in source
    assert "allow_transcode=not is_active_batch" in source
    assert "ordered_groups = sorted(" in source
    assert "self._oldest_eligible_fragment()" in source
    assert "fragment.started_at <= candidate.started_at" in source
    assert "not any(_legacy.fragment_usable_for_stitch(item) for item in current)" in source
    assert 'self.last_errors.pop(f"stitch:{source_id}:{session_id}", None)' in source


def test_facade_stitch_is_published_atomically():
    source = (ROOT / "app/workers/__init__.py").read_text(encoding="utf-8")
    assert 'temporary = output.with_name(f".{output.stem}.finalizing{output.suffix}")' in source
    assert "stitch_recording_parts(paths, temporary" in source
    assert "temporary.replace(output)" in source


def test_active_session_batch_becomes_ready_after_fifteen_minutes(tmp_path):
    manager = workers.WorkerManager()
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    path = tmp_path / "capture_part000.mp4"
    path.write_bytes(b"media")

    def fragment(duration: float, finalized_at: datetime):
        return SimpleNamespace(
            local_path=str(path),
            integrity_status="passed",
            integrity_error="",
            duration_seconds=duration,
            finalized_at=finalized_at,
        )

    assert not manager._stitch_group_ready(
        [fragment(10 * 60, now - timedelta(minutes=1))], now
    )
    assert manager._stitch_group_ready(
        [fragment(15 * 60, now - timedelta(minutes=1))], now
    )
    assert manager._stitch_group_ready(
        [fragment(2 * 60, now - timedelta(minutes=21))], now
    )
