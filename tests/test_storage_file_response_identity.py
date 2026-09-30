"""A capture token pins the opened inode, including deferred and ranged responses."""
import asyncio
import os

import anyio
import pytest

from app import storage_handoff as storage
from app.storage_response import StorageFileResponse


def identity(path):
    st = path.stat()
    return st.st_dev, st.st_ino


async def request(response, headers=(), method='GET', send_hook=None):
    messages = []

    async def send(message):
        messages.append(message)
        if send_hook:
            await send_hook(message)

    await response({'type': 'http', 'method': method, 'headers': list(headers),
                    'extensions': {'http.response.pathsend': {}}}, None, send)
    return messages


@pytest.fixture(autouse=True)
def storage_online(monkeypatch):
    monkeypatch.setattr(storage, 'state', lambda: {'mode': 'nvme'})


@pytest.mark.parametrize('headers,method', [((), 'GET'), (((b'range', b'bytes=0-3'),), 'GET'), ((), 'HEAD')])
def test_replacement_after_response_creation_is_404(tmp_path, headers, method):
    path = tmp_path / 'capture.mp4'
    path.write_bytes(b'ORIGINAL')
    response = StorageFileResponse(path, expected_identity=identity(path))
    path.rename(tmp_path / 'original.mp4')
    path.write_bytes(b'REPLACEMENT')
    messages = asyncio.run(request(response, headers, method))
    assert messages[0]['status'] == 404
    assert b'REPLACEMENT' not in b''.join(message.get('body', b'') for message in messages)
    assert storage.active_media_responses == 0


def test_removed_file_returns_404_and_releases_response_count(tmp_path):
    path = tmp_path / 'capture.mp4'
    path.write_bytes(b'ORIGINAL')
    response = StorageFileResponse(path, expected_identity=identity(path))
    path.unlink()
    assert asyncio.run(request(response))[0]['status'] == 404
    assert storage.active_media_responses == 0


def test_directory_replacement_returns_404(tmp_path):
    path = tmp_path / 'capture.mp4'
    path.write_bytes(b'ORIGINAL')
    response = StorageFileResponse(path, expected_identity=identity(path))
    path.rename(tmp_path / 'original.mp4')
    path.mkdir()
    assert asyncio.run(request(response))[0]['status'] == 404


@pytest.mark.skipif(os.name == 'nt', reason='POSIX FIFO and symlink replacement')
@pytest.mark.parametrize('kind', ['fifo', 'symlink'])
def test_nonregular_replacement_returns_404_without_blocking(tmp_path, kind):
    path = tmp_path / 'capture.mp4'
    path.write_bytes(b'ORIGINAL')
    response = StorageFileResponse(path, expected_identity=identity(path))
    original = tmp_path / 'original.mp4'
    path.rename(original)
    if kind == 'fifo':
        os.mkfifo(path)
    else:
        path.symlink_to(original)
    async def run():
        messages = await asyncio.wait_for(request(response), 2)
        assert messages[0]['status'] == 404
    asyncio.run(run())


@pytest.mark.parametrize('headers', [(), ((b'range', b'bytes=2-5'),), ((b'range', b'bytes=0-1,8-9'),)])
def test_rename_after_open_cannot_change_full_single_or_multiple_range_bytes(tmp_path, monkeypatch, headers):
    path = tmp_path / 'capture.mp4'
    path.write_bytes(b'0123456789')
    original_identity = identity(path)
    response = StorageFileResponse(path, media_type='video/mp4', expected_identity=original_identity)
    opened = []
    real_open = anyio.open_file

    async def moved_after_open(*args, **kwargs):
        file = await real_open(*args, **kwargs)
        opened.append(file)
        # Linux permits pathname replacement while the original file remains open.
        # Windows keeps the FD stable too, but does not permit this rename.
        if os.name != 'nt':
            path.rename(tmp_path / 'original.mp4')
            path.write_bytes(b'REPLACEMENT')
        return file

    monkeypatch.setattr(anyio, 'open_file', moved_after_open)
    messages = asyncio.run(request(response, headers))
    body = b''.join(message.get('body', b'') for message in messages)
    assert b'REPLACEMENT' not in body
    assert len(opened) == 1 and opened[0].closed
    if not headers:
        assert messages[0]['status'] == 200 and body == b'0123456789'
    elif headers[0][1] == b'bytes=2-5':
        assert messages[0]['status'] == 206 and body == b'2345'
    else:
        assert messages[0]['status'] == 206 and b'\r\n01\r\n' in body and b'\r\n89\r\n' in body
        response_headers = dict(messages[0]['headers'])
        assert len(body) == int(response_headers[b'content-length'])
    assert not any(message['type'] == 'http.response.pathsend' for message in messages)


def test_full_pinned_response_stops_at_advertised_size_if_capture_grows(tmp_path):
    path = tmp_path / 'capture.mp4'
    path.write_bytes(b'ORIGINAL')
    response = StorageFileResponse(path, expected_identity=identity(path))
    async def append_after_headers(message):
        if message['type'] == 'http.response.start':
            with path.open('ab') as handle:
                handle.write(b'APPENDED')
    messages = asyncio.run(request(response, send_hook=append_after_headers))
    assert b''.join(message.get('body', b'') for message in messages) == b'ORIGINAL'
    assert dict(messages[0]['headers'])[b'content-length'] == b'8'


def test_pinned_head_and_invalid_range_keep_standard_http_behavior(tmp_path):
    path = tmp_path / 'capture.mp4'
    path.write_bytes(b'0123456789')
    head = asyncio.run(request(StorageFileResponse(path, expected_identity=identity(path)), method='HEAD'))
    assert head[0]['status'] == 200
    assert dict(head[0]['headers'])[b'content-length'] == b'10'
    assert head[-1]['body'] == b''
    unsatisfiable = asyncio.run(request(StorageFileResponse(path, expected_identity=identity(path)), ((b'range', b'bytes=20-25'),)))
    assert unsatisfiable[0]['status'] == 416
    assert storage.active_media_responses == 0


def test_handoff_cancels_pinned_handle_before_ack_count_drops(tmp_path, monkeypatch):
    path = tmp_path / 'capture.mp4'
    path.write_bytes(b'x' * 200000)
    mode = {'value': 'nvme'}
    monkeypatch.setattr(storage, 'state', lambda: {'mode': mode['value']})
    opened = []
    real_open = anyio.open_file

    async def tracked_open(*args, **kwargs):
        file = await real_open(*args, **kwargs)
        opened.append(file)
        return file

    monkeypatch.setattr(anyio, 'open_file', tracked_open)

    async def run():
        blocked = asyncio.Event()
        async def slow_client(message):
            if message['type'] == 'http.response.body':
                blocked.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(request(StorageFileResponse(path, expected_identity=identity(path)), send_hook=slow_client))
        await blocked.wait()
        assert storage.active_media_responses == 1 and not opened[0].closed
        mode['value'] = 'quiesce'
        with pytest.raises(RuntimeError, match='storage handoff'):
            await asyncio.wait_for(task, 2)
        assert opened[0].closed and storage.active_media_responses == 0

    asyncio.run(run())
