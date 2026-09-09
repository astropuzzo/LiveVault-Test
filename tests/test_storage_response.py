import asyncio

import pytest
import anyio

from app import storage_handoff as storage
from app.storage_response import StorageFileResponse


def test_slow_playback_releases_handle_before_handoff_ack(tmp_path, monkeypatch):
    path = tmp_path / 'capture.mp4'
    path.write_bytes(b'x' * 200000)
    mode = {'value': 'nvme'}
    monkeypatch.setattr(storage, 'state', lambda: {'mode': mode['value']})
    opened = []
    real_open = anyio.open_file
    async def tracked_open(*args, **kwargs):
        handle = await real_open(*args, **kwargs)
        opened.append(handle)
        return handle
    monkeypatch.setattr(anyio, 'open_file', tracked_open)
    async def run():
        blocked = asyncio.Event()
        async def send(message):
            if message['type'] == 'http.response.body':
                blocked.set()
                await asyncio.Event().wait()
        scope = {'type': 'http', 'method': 'GET', 'headers': []}
        task = asyncio.create_task(StorageFileResponse(path)(scope, None, send))
        await blocked.wait()
        assert storage.active_media_responses == 1
        assert not opened[0].closed
        mode['value'] = 'quiesce'
        with pytest.raises(RuntimeError, match='storage handoff'):
            await asyncio.wait_for(task, 2)
        assert opened[0].closed
        assert storage.active_media_responses == 0
    asyncio.run(run())


def test_range_response_and_unavailable_storage(tmp_path, monkeypatch):
    path = tmp_path / 'capture.mp4'
    path.write_bytes(b'0123456789')
    mode = {'value': 'nvme'}
    monkeypatch.setattr(storage, 'state', lambda: {'mode': mode['value']})
    async def run():
        messages = []
        async def send(message): messages.append(message)
        scope = {'type': 'http', 'method': 'GET', 'headers': [(b'range', b'bytes=2-5')]}
        await StorageFileResponse(path)(scope, None, send)
        assert messages[0]['status'] == 206
        assert messages[-1]['body'] == b'2345'
        mode['value'] = 'quiesce'
        messages.clear()
        await StorageFileResponse(path)(scope, None, send)
        assert messages[0]['status'] == 503
        assert storage.active_media_responses == 0
    asyncio.run(run())
