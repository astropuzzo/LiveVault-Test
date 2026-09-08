from __future__ import annotations

import json
import time as pytime
from pathlib import Path

import pytest

import importlib.util
spec = importlib.util.spec_from_file_location('control_panel_downtime', Path(__file__).parents[1] / 'control-panel/server.py')
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)


def _availability_file(tmp_path, *, boot='old', first=1000, last=1100):
    path = tmp_path / 'availability.json'
    path.write_text(json.dumps({'boot_id': boot, 'first_seen': first, 'last_seen': last, 'downtimes': []}))
    return path


def test_changed_boot_id_records_real_machine_downtime(monkeypatch, tmp_path):
    monkeypatch.setattr(panel, 'AVAILABILITY_FILE', _availability_file(tmp_path))
    monkeypatch.setattr(panel, '_boot_id', lambda: 'new')
    monkeypatch.setattr(panel, '_boot_time', lambda: 2000)
    monkeypatch.setattr(panel.time, 'time', lambda: 2100)
    monkeypatch.setattr(panel, '_history', [{'t': 1000}])
    panel._availability = {}
    panel.load_availability()
    data = panel.availability_payload(1200, 2100)
    assert data['downtime_seconds'] == 900
    assert data['downtimes'] == [{'start': 1100, 'end': 2000, 'seconds': 900, 'reason': 'system_offline'}]
    assert data['online_seconds'] == 200
    assert data['uptime_percent'] == pytest.approx(18.182, abs=0.001)


def test_control_panel_restart_same_boot_is_not_system_downtime(monkeypatch, tmp_path):
    monkeypatch.setattr(panel, 'AVAILABILITY_FILE', _availability_file(tmp_path, boot='same'))
    monkeypatch.setattr(panel, '_boot_id', lambda: 'same')
    monkeypatch.setattr(panel, '_boot_time', lambda: 900)
    monkeypatch.setattr(panel.time, 'time', lambda: 2100)
    monkeypatch.setattr(panel, '_history', [{'t': 1000}])
    panel._availability = {}
    panel.load_availability()
    data = panel.availability_payload(1200, 2100)
    assert data['downtime_seconds'] == 0
    assert data['downtimes'] == []
    assert data['uptime_percent'] == 100.0


def test_history_payload_marks_recorded_downtime_as_null_gap(monkeypatch):
    monkeypatch.setattr(panel.time, 'time', lambda: 2200)
    panel._availability = {
        'boot_id': 'x', 'boot_time': 2000, 'first_seen': 1000, 'last_seen': 2200,
        'downtimes': [{'start': 1400, 'end': 1900, 'reason': 'system_offline'}],
    }
    panel._history = [
        {'t': 1200, 'cpu': 10, 'ram': 20, 'temp': 40, 'disk': 50, 'rx': 1, 'tx': 1, 'watts': None, 'power_measurement': 'unavailable'},
        {'t': 2100, 'cpu': 30, 'ram': 40, 'temp': 45, 'disk': 51, 'rx': 2, 'tx': 2, 'watts': None, 'power_measurement': 'unavailable'},
    ]
    result = panel.history_payload(1200)
    offline = [point for point in result['points'] if point.get('offline')]
    assert len(offline) == 2
    assert all(point['cpu'] is None for point in offline)
    assert result['availability']['downtime_seconds'] == 500


def test_history_compaction_keeps_long_term_points_without_raw_90_day_growth():
    now = 10_000_000
    rows = []
    # raw day: 10-second cadence
    rows += [{'t': t} for t in range(now-86400, now+1, 10)]
    # days 2-7: minute cadence, should compact to 5 min
    rows += [{'t': t} for t in range(now-7*86400, now-86400, 60)]
    # old: 5 min cadence, should compact to 30 min
    rows += [{'t': t} for t in range(now-90*86400, now-7*86400, 300)]
    compact = panel._compact_history(rows, now)
    assert compact[-1]['t'] == now
    assert len(compact) < 15000
    assert min(row['t'] for row in compact) >= now - 90*86400


def _set_history_paths(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(panel, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(panel, 'HISTORY_FILE', tmp_path / 'history.json')
    monkeypatch.setattr(panel, 'HISTORY_DB', tmp_path / 'history.sqlite3')
    panel._history = []
    panel._history_pending = []


def test_sqlite_history_import_preserves_legacy_json(monkeypatch, tmp_path):
    _set_history_paths(monkeypatch, tmp_path)
    now = 10_000_000
    monkeypatch.setattr(panel.time, 'time', lambda: now)
    rows = [
        {'t': now - 20, 'cpu': 10, 'watts': 5.0},
        {'t': now - 2 * 86400, 'cpu': 20, 'watts': 6.0},
        {'t': now - 20 * 86400, 'cpu': 30, 'watts': 7.0},
    ]
    original = json.dumps(rows, separators=(',', ':'))
    panel.HISTORY_FILE.write_text(original)

    panel.load_history()

    assert panel.HISTORY_FILE.read_text() == original
    assert panel.HISTORY_DB.exists()
    assert [row['t'] for row in panel._history] == sorted(row['t'] for row in rows)
    # Legacy power samples keep the old compatibility semantics in memory.
    assert all(row['power_measurement'] == 'estimated' for row in panel._history)
    assert all(row['watts'] is None for row in panel._history)


def test_save_history_appends_sqlite_without_rewriting_legacy_json(monkeypatch, tmp_path):
    _set_history_paths(monkeypatch, tmp_path)
    now = 20_000_000
    monkeypatch.setattr(panel.time, 'time', lambda: now)
    legacy = '[{"t":19999900,"cpu":1}]'
    panel.HISTORY_FILE.write_text(legacy)
    panel.load_history()

    sample = {'t': now, 'cpu': 42, 'ram': 20, 'temp': 40, 'disk': 50,
              'rx': 1, 'tx': 2, 'watts': None, 'power_measurement': 'unavailable'}
    panel._history.append(sample)
    panel._history_pending.append(sample)
    panel.save_history()

    assert panel.HISTORY_FILE.read_text() == legacy
    assert panel._history_pending == []
    panel._history = []
    panel.load_history()
    assert any(row.get('t') == now and row.get('cpu') == 42 for row in panel._history)


def test_sqlite_history_uses_same_three_retention_tiers(monkeypatch, tmp_path):
    _set_history_paths(monkeypatch, tmp_path)
    now = 30_000_000
    with panel.closing(panel._history_db_connect()) as conn, conn:
        rows = [
            {'t': now - 60, 'cpu': 1},
            {'t': now - 2 * 86400, 'cpu': 2},
            {'t': now - 20 * 86400, 'cpu': 3},
            {'t': now - 91 * 86400, 'cpu': 4},
        ]
        for row in rows:
            panel._history_store_row(conn, row, now)
        counts = [conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                  for table in ('history_raw', 'history_5m', 'history_30m')]
    assert counts == [1, 1, 1]


def test_history_export_produces_old_format_with_new_sqlite_samples(monkeypatch, tmp_path):
    _set_history_paths(monkeypatch, tmp_path)
    now = 40_000_000
    monkeypatch.setattr(panel.time, 'time', lambda: now)
    panel.HISTORY_FILE.write_text('[]')
    panel.load_history()
    sample = {'t': now, 'cpu': 55, 'power_measurement': 'unavailable', 'watts': None}
    panel._history.append(sample)
    panel._history_pending.append(sample)
    panel.save_history()

    exported = tmp_path / 'rollback-history.json'
    panel.export_history_json(exported)
    payload = json.loads(exported.read_text())
    assert any(row.get('t') == now and row.get('cpu') == 55 for row in payload)
