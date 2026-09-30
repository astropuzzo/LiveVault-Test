from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'control-panel'))
spec = importlib.util.spec_from_file_location('control_upload_atomic_test', ROOT / 'control-panel/upload_server.py')
upload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upload)


def test_publish_refuses_existing_file_and_preserves_both_copies(tmp_path):
    temporary = tmp_path / 'pending.part'
    target = tmp_path / 'existing.mp4'
    temporary.write_bytes(b'new')
    target.write_bytes(b'original')
    with pytest.raises(FileExistsError):
        upload._publish_upload(temporary, target)
    assert target.read_bytes() == b'original'
    assert temporary.read_bytes() == b'new'


def test_simultaneous_imports_do_not_replace_each_other(tmp_path, monkeypatch):
    monkeypatch.setattr(upload.media_center, '_safe_target', lambda *a, **k: ({}, tmp_path, tmp_path))
    monkeypatch.setattr(upload.shutil, 'disk_usage', lambda *a: SimpleNamespace(total=2**30, free=2**30))
    remembered = []
    monkeypatch.setattr(upload, '_remember_uploaded_file', lambda *a: remembered.append(a))
    barrier = threading.Barrier(2)
    responses = []
    errors = []

    class Body(io.BytesIO):
        def read(self, length):
            # Both uploads have passed the early existence check at this point.
            barrier.wait(timeout=3)
            return super().read(length)

    class Request:
        path = '/api/media/upload?uuid=USB-1&name=collision.mp4'
        headers = {'X-CSRF-Token': 'test', 'Content-Length': '3'}

        def __init__(self, body): self.rfile = Body(body)
        def require_session(self): return {'csrf': 'test'}
        def send_json(self, payload, status=200): responses.append((status, payload))
        def client_key(self): return 'test-client'

    def run(body):
        try:
            upload.Handler.do_POST(Request(body))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(body,)) for body in (b'AAA', b'BBB')]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=5)
    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(status for status, _ in responses) == [200, 409]
    assert (tmp_path / 'collision.mp4').read_bytes() in (b'AAA', b'BBB')
    assert len(remembered) == 1
    assert not list(tmp_path.glob('.openastro-upload-*'))
