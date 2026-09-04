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
    js = (ROOT / "app/static/attention-fix.js").read_text(encoding="utf-8")

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


def test_automatic_finalization_respects_window_but_manual_can_bypass():
    source = (ROOT / "app/workers/__init__.py").read_text(encoding="utf-8")
    assert "if not forced and stitch_gap_open(latest, now):" in source
    assert "if forced:" in source
    assert "Never consolidate the exact session that is still being written" in source
