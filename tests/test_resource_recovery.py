import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app import stripchat_capture, workers


def test_failed_unchanged_repairs_back_off_but_changed_media_can_retry(tmp_path, monkeypatch):
    path = tmp_path / "part.mp4"
    path.write_bytes(b"broken")
    manager = workers.WorkerManager()
    calls = []
    async def fail(_):
        calls.append(1)
        raise RuntimeError("bad timeline")
    monkeypatch.setattr(workers, "finalize_mp4_for_streaming", fail)
    async def exercise():
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await manager._prepare_mp4(path)
        assert len(calls) == 1
        path.write_bytes(b"changed media")
        with pytest.raises(RuntimeError):
            await manager._prepare_mp4(path)
        assert len(calls) == 2
    asyncio.run(exercise())


@pytest.mark.skipif(not hasattr(os, "sched_getaffinity"), reason="Linux affinity required")
def test_background_child_inherits_bounded_cpu_affinity():
    available = len(os.sched_getaffinity(0))
    result = subprocess.check_output([sys.executable, "-m", "app.media_process", sys.executable,
        "-c", "import os; print(len(os.sched_getaffinity(0)),os.nice(0))"], text=True)
    count, nice = map(int, result.split())
    assert count == max(1, available // 2)
    assert nice >= 10


def test_expired_stripchat_fragment_refreshes_without_init_only_file(tmp_path, monkeypatch):
    legacy = stripchat_capture._legacy
    playlists = ["#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\"\nold.mp4\n",
                 "#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\"\nnew.mp4\n#EXT-X-ENDLIST\n"]
    class Response:
        def __init__(self, text="", content=b"", status=200):
            self.text, self.content, self.status_code = text, content, status
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("404 expired")
    class Session:
        def get(self, url, **kwargs):
            if url.endswith("live.m3u8"): return Response(text=playlists.pop(0))
            if url.endswith("old.mp4"): return Response(status=404)
            return Response(content=b"init" if url.endswith("init.mp4") else b"media")
    selection = legacy.MasterSelection("https://example.test/live.m3u8", "", "", "")
    resolutions = []
    def resolve(*args, **kwargs):
        resolutions.append(1)
        return selection, {}
    monkeypatch.setattr(legacy.requests, "Session", Session)
    monkeypatch.setattr(legacy, "get_cam_state", lambda *a, **kw: (1, {}))
    monkeypatch.setattr(legacy, "_public_stream_id", lambda *a: "1")
    monkeypatch.setattr(legacy, "_load_key_file", lambda: {})
    monkeypatch.setattr(legacy, "select_master", resolve)
    monkeypatch.setattr(legacy.time, "sleep", lambda _: None)
    monkeypatch.setattr(legacy, "STOP_REQUESTED", False)
    outputs = []
    monkeypatch.setattr(legacy, "_remux", lambda raw, *args: outputs.append(raw.read_bytes()))
    legacy.capture(SimpleNamespace(slug="test", quality="best", output_pattern=str(tmp_path / "part%03d.mp4"),
        video_preview_base="", max_bytes=10000, segment_seconds=60, container="mp4"))
    assert len(resolutions) == 2
    assert outputs == [b"initmedia"]
