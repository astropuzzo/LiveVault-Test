from __future__ import annotations

import importlib.util
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE = Path(__file__).parents[1] / 'control-panel' / 'media_center.py'
spec = importlib.util.spec_from_file_location('media_center_level3_test', MODULE)
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(media, 'STATE_ROOT', tmp_path)
    monkeypatch.setattr(media, 'DB_PATH', tmp_path / 'media.sqlite3')
    monkeypatch.setattr(media, 'THUMB_ROOT', tmp_path / 'thumbs')
    monkeypatch.setattr(media, '_LIBRARY_CACHE', {})
    monkeypatch.setattr(media, '_mounted_uuids', lambda: {'USB-1'})
    with media._db() as conn:
        conn.execute("INSERT INTO media_devices(uuid,label,model,fstype,size,last_seen,last_scan) VALUES(?,?,?,?,?,?,?)", ('USB-1','Films','Lexar','exfat',1000,1,1))
        conn.execute("INSERT INTO media_items(uuid,path,name,category,mime,size,modified,scan_token) VALUES(?,?,?,?,?,?,?,?)", ('USB-1','Films/Movie.mp4','Movie.mp4','video','video/mp4',500,10,1))
    return tmp_path


def test_progress_and_favorites_are_server_side(store):
    result = media.update_progress('USB-1','Films/Movie.mp4',300,1000,client='phone')
    assert result['completed'] is False
    assert result['play_count'] == 1
    assert media.set_favorite('USB-1','Films/Movie.mp4',True)['favorite'] is True
    home = media.home()
    assert home['continue'][0]['path'] == 'Films/Movie.mp4'
    assert home['continue'][0]['position'] == pytest.approx(300)
    assert home['favorites'][0]['name'] == 'Movie.mp4'
    assert home['favorites'][0]['available'] is True


def test_completed_media_drops_out_of_continue(store):
    result = media.update_progress('USB-1','Films/Movie.mp4',960,1000)
    assert result['completed'] is True
    home = media.home()
    assert home['continue'] == []
    assert home['recently_played'][0]['completed'] == 1


def test_library_memory_survives_offline_device(store, monkeypatch):
    monkeypatch.setattr(media, '_mounted_uuids', lambda: set())
    payload = media._library_payload('USB-1')
    assert payload['mounted'] is False
    assert payload['total_files'] == 1
    home = media.home()
    media.set_favorite('USB-1','Films/Movie.mp4',True)
    home = media.home()
    assert home['favorites'][0]['available'] is False


def test_stream_registry_tracks_bytes(store):
    info = {'uuid':'USB-1','relative':'Films/Movie.mp4','name':'Movie.mp4','category':'video'}
    sid = media.stream_begin(info,'192.168.1.5',0,999)
    media.stream_touch(sid,12345)
    streams = media.active_streams()
    assert len(streams) == 1
    assert streams[0]['bytes_sent'] == 12345
    media.stream_end(sid)
    assert media.active_streams() == []


def test_catalog_combines_profile_state(store):
    media.update_progress('USB-1','Films/Movie.mp4',120,1000)
    media.set_favorite('USB-1','Films/Movie.mp4',True)
    result = media.catalog('USB-1', favorite=True)
    assert result['total'] == 1
    assert result['items'][0]['favorite'] == 1
    assert result['items'][0]['position'] == pytest.approx(120)


def test_media_connection_commits_and_closes_on_context_exit(store):
    with media._db() as conn:
        conn.execute("INSERT INTO media_favorites(uuid,path,created_at) VALUES('USB-1','new',1)")
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        conn.execute('SELECT 1')
    with media._db() as check:
        assert check.execute("SELECT COUNT(*) FROM media_favorites WHERE path='new'").fetchone()[0] == 1
    with pytest.raises(ValueError), media._db() as failed:
        failed.execute("INSERT INTO media_favorites(uuid,path,created_at) VALUES('USB-1','rollback',1)")
        raise ValueError('abort')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        failed.execute('SELECT 1')
    with media._db() as check:
        assert check.execute("SELECT COUNT(*) FROM media_favorites WHERE path='rollback'").fetchone()[0] == 0


def test_device_requests_share_discovery_but_recheck_mount(monkeypatch):
    calls = []
    item = {'uuid': 'USB-1', 'mountpoint': '/tmp/media'}
    monkeypatch.setattr(media, '_DISCOVERY_CACHE', (0.0, []))
    monkeypatch.setattr(media, '_remember_device', lambda item: None)
    monkeypatch.setattr(media, '_item_mounted', lambda item: True)

    def probe(*args, **kwargs):
        calls.append(1)
        time.sleep(0.02)
        return SimpleNamespace(returncode=0, stdout=json.dumps([item]))

    monkeypatch.setattr(media, '_run', probe)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda _: media._device('USB-1'), range(8))) == [item] * 8
    assert len(calls) == 1
    monkeypatch.setattr(media, '_item_mounted', lambda item: False)
    with pytest.raises(FileNotFoundError, match='non montato'):
        media._device('USB-1')
    assert len(calls) == 1


def test_scan_limit_with_pending_directory_preserves_remembered_files(store, monkeypatch):
    root = store / 'media'
    root.mkdir()
    sub = root / 'folder'
    sub.mkdir()
    (sub / 'missed.mp4').write_bytes(b'C')
    (root / 'first.mp4').write_bytes(b'A')
    (root / 'last.mp4').write_bytes(b'B')
    monkeypatch.setattr(media, 'MAX_LIBRARY_FILES', 2)
    monkeypatch.setattr(media, '_device', lambda *a, **k: {'mountpoint': str(root), 'label': 'USB'})
    real_scandir = media.os.scandir
    monkeypatch.setattr(media.os, 'scandir', lambda path: sorted(real_scandir(path), key=lambda row: (not row.is_dir(), row.name)))
    with media._db() as conn:
        media._upsert_item(conn, 'USB-1', {'name': 'missed.mp4', 'path': 'folder/missed.mp4',
                                        'size': 1, 'modified': 1, 'mime': 'video/mp4', 'category': 'video'}, 1)
    result = media.library_summary('USB-1', force=True)
    assert result['truncated'] is True
    with media._db() as conn:
        assert conn.execute("SELECT 1 FROM media_items WHERE path='folder/missed.mp4'").fetchone()
    media._LIBRARY_CACHE.clear()
    monkeypatch.setattr(media.os, 'scandir', lambda path: (_ for _ in ()).throw(AssertionError('scan TTL has not expired')))
    assert media.library_summary('USB-1')['truncated'] is True
    assert media._library_payload('USB-1')['truncated'] is True
    # A later complete scan replaces the persisted incomplete result, including
    # when both scans occur within the same wall-clock second.
    monkeypatch.setattr(media.os, 'scandir', real_scandir)
    monkeypatch.setattr(media, 'MAX_LIBRARY_FILES', 100)
    assert media.library_summary('USB-1', force=True)['truncated'] is False
    media._LIBRARY_CACHE.clear()
    assert media.library_summary('USB-1')['truncated'] is False


def test_concurrent_thumbnails_generate_one_complete_jpeg(store, monkeypatch):
    commands = []
    info = {'path': store / 'movie.mp4', 'category': 'video', 'modified': 1, 'size': 500}
    monkeypatch.setattr(media, 'file_info', lambda *a: info)

    def decode(args, timeout):
        commands.append(args)
        time.sleep(0.02)
        Path(args[-1]).write_bytes(b'jpeg' * 30)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(media, '_run', decode)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: media.thumbnail_info('USB-1', 'movie.mp4'), range(8)))
    assert len(commands) == 1
    assert all(result['path'].read_bytes() == b'jpeg' * 30 for result in results)
    assert all(result['size'] == 120 for result in results)
    assert '-threads' in commands[0] and commands[0][commands[0].index('-threads') + 1] == '1'
    assert not list(media.THUMB_ROOT.glob('*.tmp.jpg'))
