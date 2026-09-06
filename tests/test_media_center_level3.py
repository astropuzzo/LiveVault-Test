from __future__ import annotations

import importlib.util
from pathlib import Path

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
