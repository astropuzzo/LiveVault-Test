"""Capture rotation must preserve HLS sequence meanings and immutable byte URLs."""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

from app import live_capture_playlist as live, main, storage_handoff
from app.storage_response import StorageFileResponse
from test_mp4_index import synthetic_fmp4


@pytest.fixture(autouse=True)
def empty_capture_state():
    live._part_tokens.clear()
    live._playlists.clear()
    yield
    live._part_tokens.clear()
    live._playlists.clear()


def segments(playlist):
    lines = playlist.splitlines()
    return [(lines[i], lines[i + 1], lines[i + 2]) for i, line in enumerate(lines) if line.startswith("#EXTINF:")]


def token(uri):
    return parse_qs(urlsplit(uri).query)["part"][0]


def media(tmp_path, name, seconds):
    path = tmp_path / name
    path.write_bytes(synthetic_fmp4(seconds))
    return path.resolve()


def test_rotation_preserves_ranges_durations_urls_and_target(tmp_path):
    first = media(tmp_path, "first.mp4", 12)
    second = media(tmp_path, "second.mp4", 4)
    before = live.capture_playlist(25, first, [])
    after = live.capture_playlist(25, second, [first])
    previous = segments(before)
    assert len(previous) == 5
    assert segments(after)[:len(previous)] == previous
    assert len(segments(after)) == 7  # final first-part group + second live group
    assert "#EXT-X-TARGETDURATION:3" in before and "#EXT-X-TARGETDURATION:3" in after
    assert "#EXT-X-MEDIA-SEQUENCE:0" in after
    assert "#EXT-X-DISCONTINUITY\n" in after
    assert "#EXT-X-PLAYLIST-TYPE" not in after  # retention can remove a prefix
    assert live.capture_part_path(25, token(previous[0][2])) == first


def test_deleted_prefix_advances_sequences_and_old_token_is_unavailable(tmp_path):
    first = media(tmp_path, "first.mp4", 12)
    second = media(tmp_path, "second.mp4", 4)
    previous = live.capture_playlist(25, first, [])
    old_token = token(segments(previous)[0][2])
    first.unlink()  # Stripchat remux/stitch removes bytes independently of playback
    current = live.capture_playlist(25, second, [])
    assert "#EXT-X-MEDIA-SEQUENCE:5" in current
    assert "#EXT-X-DISCONTINUITY-SEQUENCE:1" in current
    assert live.capture_part_path(25, old_token) is None
    assert live.capture_part_path(25, token(segments(current)[0][2])) == second


def test_closed_prefix_removed_after_rotation_does_not_renumber_remaining_segments(tmp_path):
    first = media(tmp_path, "first.mp4", 12)
    second = media(tmp_path, "second.mp4", 4)
    live.capture_playlist(25, first, [])
    combined = live.capture_playlist(25, second, [first])
    second_segment = segments(combined)[-1]
    first.unlink()
    remaining = live.capture_playlist(25, second, [])
    assert "#EXT-X-MEDIA-SEQUENCE:6" in remaining
    assert segments(remaining)[0] == second_segment


def test_new_earlier_database_fragment_cannot_prepend_to_advertised_sequence(tmp_path):
    first = media(tmp_path, "first.mp4", 12)
    active = media(tmp_path, "active.mp4", 4)
    before = live.capture_playlist(25, active, [])
    after = live.capture_playlist(25, active, [first])
    assert segments(after) == segments(before)


def test_part_token_rejects_other_source_unknown_token_and_traversal(tmp_path):
    path = media(tmp_path, "active.mp4", 4)
    pinned = token(live.capture_part_uri(25, path))
    assert live.capture_part_path(26, pinned) is None
    assert live.capture_part_path(25, "a" * 32) is None
    assert live.capture_part_path(25, "../active.mp4") is None


def test_part_token_does_not_read_a_replacement_at_the_same_name(tmp_path):
    path = media(tmp_path, "active.mp4", 4)
    pinned = token(live.capture_part_uri(25, path))
    original = path.with_name("original.mp4")
    path.rename(original)  # keep inode allocated so a replacement cannot reuse it
    path.write_bytes(synthetic_fmp4(6))
    assert live.capture_part_path(25, pinned) is None
    replacement = token(live.capture_part_uri(25, path))
    assert replacement != pinned


def test_replacement_after_indexing_cannot_publish_original_ranges_for_new_inode(tmp_path, monkeypatch):
    path = media(tmp_path, "active.mp4", 4)
    original_indexer = live.live_playlist_index

    def replace_after_index(path, *args):
        index = original_indexer(path, *args)
        path.rename(path.with_name("original.mp4"))
        path.write_bytes(synthetic_fmp4(12))
        return index

    with monkeypatch.context() as patch:
        patch.setattr(live, "live_playlist_index", replace_after_index)
        with pytest.raises(live.NotFragmented, match="Nessun frammento completo"):
            live.capture_playlist(25, path, [])
    assert not live._part_tokens
    assert not next(iter(live._playlists.values())).parts
    # The next poll indexes the replacement itself, so no stale ranges survive.
    current = live.capture_playlist(25, path, [])
    assert len(segments(current)) == 5
    assert live.capture_part_path(25, token(segments(current)[0][2])) == path


def test_replacement_before_uri_registration_cannot_rebind_an_advertised_part(tmp_path, monkeypatch):
    path = media(tmp_path, "active.mp4", 4)
    previous = live.capture_playlist(25, path, [])
    old_token = token(segments(previous)[0][2])
    previous_tokens = set(live._part_tokens)
    original_uri = live.capture_part_uri

    def replace_before_registration(source_id, path, **kwargs):
        path.rename(path.with_name("original.mp4"))
        path.write_bytes(synthetic_fmp4(12))
        return original_uri(source_id, path, **kwargs)

    monkeypatch.setattr(live, "capture_part_uri", replace_before_registration)
    with pytest.raises(live.NotFragmented, match="prima della pubblicazione"):
        live.capture_playlist(25, path, [])
    assert set(live._part_tokens) == previous_tokens
    assert live.capture_part_path(25, old_token) is None


def test_capture_route_serves_pinned_ranges_after_active_file_changes(tmp_path, monkeypatch):
    first = media(tmp_path, "first.mp4", 12)
    second = media(tmp_path, "second.mp4", 4)
    pinned = token(live.capture_part_uri(25, first))
    monkeypatch.setattr(main, "require_auth", lambda _request: None)
    monkeypatch.setattr(main, "settings", SimpleNamespace(recordings_dir=tmp_path))
    monkeypatch.setattr(main.manager, "playable_active_capture_path", lambda _source: second)
    monkeypatch.setattr(storage_handoff, "state", lambda: {"mode": "nvme"})
    response = main.view_active_capture(25, SimpleNamespace(headers={}), part=pinned)
    assert isinstance(response, StorageFileResponse)
    assert Path(response.path) == first

    async def request():
        messages = []
        async def send(message):
            messages.append(message)
        await response({"type": "http", "method": "GET", "headers": [(b"range", b"bytes=0-15")]}, None, send)
        assert messages[0]["status"] == 206
        assert messages[-1]["body"] == first.read_bytes()[:16]
    asyncio.run(request())
    first.unlink()
    with pytest.raises(HTTPException) as failure:
        main.view_active_capture(25, SimpleNamespace(headers={}), part=pinned)
    assert failure.value.status_code == 404


def test_pinned_route_keeps_storage_root_confinement(tmp_path, monkeypatch):
    root = tmp_path / "recordings"
    root.mkdir()
    outside = media(tmp_path, "outside.mp4", 4)
    pinned = token(live.capture_part_uri(25, outside))
    monkeypatch.setattr(main, "require_auth", lambda _request: None)
    monkeypatch.setattr(main, "settings", SimpleNamespace(recordings_dir=root))
    with pytest.raises(HTTPException) as failure:
        main.view_active_capture(25, SimpleNamespace(headers={}), part=pinned)
    assert failure.value.status_code == 404


def test_capture_state_and_token_cache_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "MAX_CAPTURE_PART_TOKENS", 2)
    monkeypatch.setattr(live, "MAX_CAPTURE_PLAYLISTS", 2)
    monkeypatch.setattr(live, "MAX_CAPTURE_PLAYLIST_PARTS", 2)
    paths = [media(tmp_path, f"part{i}.mp4", 4) for i in range(4)]
    for path in paths:
        live.capture_playlist(25, path, [])
    playlist = next(iter(live._playlists.values()))
    assert len(playlist.parts) == 2
    assert playlist.media_sequence > 0 and playlist.discontinuity_sequence > 0
    for source in range(3):
        live.capture_playlist(source, paths[-1], [])
    assert len(live._part_tokens) <= 2 and len(live._playlists) <= 2


def test_larger_gop_gets_a_new_playlist_epoch_with_preserved_history(tmp_path, monkeypatch):
    from app.mp4_index import FragmentIndex, Segment
    path = media(tmp_path, "active.mp4", 4)
    sample = {"index": FragmentIndex(100, (Segment(100, 50, 2.0),), False)}
    monkeypatch.setattr(live, "live_playlist_index", lambda *_args: sample["index"])
    before, old_epoch = live.capture_playlist_snapshot(25, path, [])
    sample["index"] = FragmentIndex(100, (Segment(100, 50, 2.0), Segment(150, 200, 8.0)), False)
    after, epoch = live.capture_playlist_snapshot(25, path, [])
    assert old_epoch != epoch
    assert "#EXT-X-TARGETDURATION:3" in before and "#EXT-X-TARGETDURATION:8" in after
    assert segments(after)[:1] == segments(before)
    assert "#EXT-X-MEDIA-SEQUENCE:0" in after
    monkeypatch.setattr(main, "require_auth", lambda _request: None)
    monkeypatch.setattr(main, "settings", SimpleNamespace(recordings_dir=tmp_path))
    monkeypatch.setattr(main.manager, "playable_active_capture_path", lambda _source: path)
    monkeypatch.setattr(main, "_session_closed_parts", lambda *_args: [])
    redirect = main.stream_active_capture(25, SimpleNamespace(headers={}), epoch=old_epoch)
    assert redirect.status_code == 307 and redirect.headers["location"].endswith(f"epoch={epoch}")
    current = main.stream_active_capture(25, SimpleNamespace(headers={}), epoch=epoch)
    assert current.status_code == 200 and current.body.decode() == after


def test_indexes_follow_inode_replacement_even_if_larger_or_same_size_and_mtime(tmp_path):
    from app.mp4_index import GrowingIndex, LivePlaylistIndex, cached_index, live_playlist_index
    path = media(tmp_path, "active.mp4", 12)
    reader = GrowingIndex(path)
    assert len(reader.poll()) == 12
    original_live = live_playlist_index(path)
    original_stat = path.stat()
    original_cached = cached_index(path)
    path.rename(path.with_name("original.mp4"))
    # Same byte count and timestamps, but double the duration and a new inode.
    path.write_bytes(synthetic_fmp4(12, sample_ticks=1024))
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert path.stat().st_size == original_stat.st_size
    assert cached_index(path).duration == original_cached.duration * 2
    assert live_playlist_index(path).segments != original_live.segments
    path.rename(path.with_name("replacement.mp4"))
    path.write_bytes(synthetic_fmp4(18, samples=50))  # a larger replacement
    assert live_playlist_index(path).segments == LivePlaylistIndex(path).snapshot().segments
    assert len(live_playlist_index(path).segments) == 17
    fragments = reader.poll()
    assert len(fragments) == 18 and fragments[0].time == 0
