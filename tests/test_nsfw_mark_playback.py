from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main


def test_mark_playback_uses_exact_part_or_its_remux_and_never_latest_capture(tmp_path, monkeypatch):
    raw = tmp_path / "old.capture.mp4"
    remux = tmp_path / "old.mp4"
    latest = tmp_path / "new.mp4"
    remux.write_bytes(b"old media")
    latest.write_bytes(b"new media")
    mark = SimpleNamespace(part_path=str(raw))

    @contextmanager
    def scope():
        yield SimpleNamespace(get=lambda *_: mark)

    monkeypatch.setattr(main, "db_session", scope)
    monkeypatch.setattr(main, "settings", SimpleNamespace(recordings_dir=tmp_path))
    assert main._nsfw_mark_local_path(7) == remux
    remux.unlink()
    with pytest.raises(HTTPException) as error:
        main._nsfw_mark_local_path(7)
    assert error.value.status_code == 404
    assert latest.read_bytes() == b"new media"


@pytest.mark.parametrize("endpoint", [main.view_nsfw_mark_part, main.stream_nsfw_mark_part])
def test_mark_playback_requires_auth_before_resolving_media(monkeypatch, endpoint):
    def deny(_request):
        raise HTTPException(401, "login")

    monkeypatch.setattr(main, "require_auth", deny)
    monkeypatch.setattr(main, "_nsfw_mark_local_path", lambda *_: pytest.fail("media read before auth"))
    with pytest.raises(HTTPException) as error:
        endpoint(7, None)
    assert error.value.status_code == 401
