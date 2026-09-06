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
