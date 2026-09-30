import importlib.util
import io
from pathlib import Path
import urllib.error


spec = importlib.util.spec_from_file_location('control_health_payload', Path(__file__).parents[1] / 'control-panel/server.py')
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)


def test_unhealthy_livevault_retains_worker_and_storage_diagnosis(monkeypatch):
    def unavailable(*args, **kwargs):
        raise urllib.error.HTTPError('http://localhost/healthz', 503, 'Unavailable', {}, io.BytesIO(b'{"ok":false,"worker":{"tasks":{"uploader":false}},"storage_handoff":{"mode":"buffer","full":true}}'))
    monkeypatch.setattr(panel.urllib.request, 'urlopen', unavailable)
    result = panel.livevault_health()
    assert result['ok'] is False
    assert result['worker']['tasks']['uploader'] is False
    assert result['storage_handoff']['full'] is True


def test_malformed_health_response_still_reports_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise urllib.error.HTTPError('http://localhost/healthz', 503, 'Unavailable', {}, io.BytesIO(b'not JSON'))
    monkeypatch.setattr(panel.urllib.request, 'urlopen', unavailable)
    assert panel.livevault_health() == {'ok': False}
