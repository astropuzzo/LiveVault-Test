"""3.4.24: lighter hot paths (deferred nsfw_hits, idle uploader poll, pulse, thumbnails)."""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

import app.workers as workers_pkg
from app import main, nsfw_live_worker, nsfw_worker
from app.db import Base, Recording
from app.workers import WorkerManager


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'perf.db'}")
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def session_scope():
        session = maker()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    for module in (nsfw_worker, nsfw_live_worker, workers_pkg):
        monkeypatch.setattr(module, "db_session", session_scope)
    yield maker
    engine.dispose()


def _recording(maker, path: Path, **fields) -> int:
    with maker.begin() as session:
        rec = Recording(source_id=1, source_name="Demo", session_id="s1", local_path=str(path), filename=path.name,
                        started_at=datetime(2026, 9, 1, tzinfo=timezone.utc), integrity_status="passed",
                        upload_status="pending", **fields)
        session.add(rec)
        session.flush()
        return rec.id


def test_nsfw_hits_is_not_loaded_with_the_row_but_the_scan_job_has_it(factory, tmp_path):
    hits = json.dumps([{"t": 1.0, "score": 0.9}])
    rec_id = _recording(factory, tmp_path / "a.mp4", nsfw_hits=hits, nsfw_status="paused")
    with factory() as session:
        row = session.scalars(select(Recording)).one()
        assert "nsfw_hits" in inspect(row).unloaded  # not read by ordinary queries
    job = WorkerManager()._next_nsfw_job()
    assert job is not None and job.id == rec_id
    assert job.nsfw_hits == hits  # loaded before detaching: no DetachedInstanceError


def test_idle_uploader_poll_does_not_scan_fragments(factory, monkeypatch):
    manager = WorkerManager()
    monkeypatch.setattr(manager, "_oldest_eligible_fragment",
                        lambda: pytest.fail("idle poll must not load every fragment"))
    assert manager._pending_recording() is None


def test_uploader_still_yields_to_an_older_ready_fragment_batch(factory, tmp_path, monkeypatch):
    _recording(factory, tmp_path / "b.mp4")
    manager = WorkerManager()
    older = type("Fragment", (), {"started_at": datetime(2026, 8, 1)})()
    monkeypatch.setattr(manager, "_oldest_eligible_fragment", lambda: older)
    assert manager._pending_recording() is None


def test_thumbnail_url_check_accepts_inside_and_rejects_outside(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "settings", SimpleNamespace(data_dir=tmp_path))
    (tmp_path / "thumbnails").mkdir()
    inside = tmp_path / "thumbnails" / "1.jpg"
    inside.write_bytes(b"x")
    outside = tmp_path / "other.jpg"
    outside.write_bytes(b"x")
    assert main._safe_thumbnail_url(1, str(inside)) == "/api/recordings/1/thumbnail"
    assert main._safe_thumbnail_url(1, str(tmp_path / "thumbnails" / ".." / "other.jpg")) == ""
    assert main._safe_thumbnail_url(1, str(outside)) == ""
    assert main._safe_thumbnail_url(1, str(tmp_path / "thumbnails" / "missing.jpg")) == ""
    assert main._safe_thumbnail_url(1, "") == ""


# ---- the same media indexed twice (raw .capture.mp4 + its remux) ----

def _index(manager, path):
    import asyncio
    return asyncio.run(manager._index_file(source_id=1, source_name="Demo", session_id="s1", path=path, started_at=None))


def _prepare_indexing(monkeypatch, manager, digest):
    from types import SimpleNamespace
    legacy = workers_pkg._legacy
    monkeypatch.setattr(legacy, "verify_media", lambda *_a: SimpleNamespace(
        ok=True, duration=75.0, has_video=True, has_audio=True, error="", codec=lambda _kind: "h264"))
    monkeypatch.setattr(legacy, "sha256_file", lambda _p: digest)
    monkeypatch.setattr(legacy, "build_validation_receipt", lambda *_a: "{}")

    async def stable(_path, timeout=12.0):
        return True

    async def prepared(_path):
        return True

    manager._wait_until_stable = stable
    manager._prepare_mp4 = prepared
    manager.nsfw_attach_parts = lambda *a, **k: None


def test_identical_content_is_indexed_once_and_the_copy_removed(factory, tmp_path, monkeypatch):
    manager = WorkerManager()
    _prepare_indexing(monkeypatch, manager, "a" * 64)
    first = tmp_path / "x_part001.mp4"
    first.write_bytes(b"video-bytes")
    second = tmp_path / "x_part001_copy.mp4"
    second.write_bytes(b"video-bytes")
    assert _index(manager, first) is True
    assert _index(manager, second) is True
    with factory() as session:
        rows = list(session.scalars(select(Recording)).all())
    assert [row.filename for row in rows] == ["x_part001.mp4"]
    assert first.exists() and not second.exists()


def test_duplicate_raw_capture_part_is_left_to_its_remux(factory, tmp_path, monkeypatch):
    manager = WorkerManager()
    _prepare_indexing(monkeypatch, manager, "b" * 64)
    done = tmp_path / "y_part001.mp4"
    done.write_bytes(b"same")
    raw = tmp_path / "y_part001.capture.mp4"
    raw.write_bytes(b"same")
    _index(manager, done)
    _index(manager, raw)
    with factory() as session:
        assert session.scalars(select(Recording)).all().__len__() == 1
    assert raw.exists()  # not ours to delete: the remux unlinks it


def test_different_content_of_the_same_size_is_kept(factory, tmp_path, monkeypatch):
    manager = WorkerManager()
    _prepare_indexing(monkeypatch, manager, "c" * 64)
    one = tmp_path / "z_part001.mp4"
    one.write_bytes(b"1111")
    _index(manager, one)
    _prepare_indexing(monkeypatch, manager, "d" * 64)
    two = tmp_path / "z_part002.mp4"
    two.write_bytes(b"2222")
    _index(manager, two)
    with factory() as session:
        assert session.scalars(select(Recording)).all().__len__() == 2


# ---- yt-dlp: default extractor list resolved once ----

def test_probe_ydl_registers_the_same_extractors_as_plain_yt_dlp():
    yt_dlp = pytest.importorskip("yt_dlp")
    from app import source_providers as providers

    cls = providers._legacy._probe_ydl_class() if hasattr(providers, "_legacy") else providers._probe_ydl_class()
    plain = yt_dlp.YoutubeDL({"quiet": True})
    first = cls({"quiet": True})
    second = cls({"quiet": True})  # served from the cached list
    assert list(first._ies) == list(plain._ies) == list(second._ies)
    # instances (the catch-all) are never shared between downloaders
    shared = [key for key, ie in first._ies.items() if not isinstance(ie, type) and second._ies[key] is ie]
    assert shared == []
    for key, ie in second._ies.items():
        if not isinstance(ie, type):
            assert ie._downloader is second
    # an explicit extractor filter bypasses the cache
    assert list(cls({"quiet": True, "allowed_extractors": []})._ies) == []
    first.close(); second.close(); plain.close()


# ---- TLS: ChaCha20 first (CM4 has no AES instructions) ----

def test_container_prefers_chacha20_for_tls():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    config = root / "app" / "openssl-chacha.cnf"
    assert "ENV OPENSSL_CONF=/app/app/openssl-chacha.cnf" in dockerfile
    text = config.read_text(encoding="utf-8")
    suites = next(line for line in text.splitlines() if line.startswith("Ciphersuites"))
    order = suites.split("=", 1)[1].strip().split(":")
    assert order[0] == "TLS_CHACHA20_POLY1305_SHA256"
    assert {"TLS_AES_256_GCM_SHA384", "TLS_AES_128_GCM_SHA256"} <= set(order)  # servers without ChaCha still work
    assert "MinProtocol = TLSv1.2" in text


# ---- /api/recordings: cache, ETag, invalidation ----

class _Req:
    def __init__(self, headers=None):
        from starlette.datastructures import Headers
        self.headers = Headers(headers or {})
        self.cookies = {}


@pytest.fixture()
def recordings_api(factory, monkeypatch):
    """main.recordings on an isolated DB, auth bypassed."""
    import app.db as dbmod
    monkeypatch.setattr(main, "require_auth", lambda _req: None)
    monkeypatch.setattr(main, "db_session", workers_pkg.db_session)
    main._recordings_cache.clear()
    from sqlalchemy.orm import Session
    # the change tracking listens on the Session class, so the isolated factory feeds it too
    assert dbmod.recordings_generation() >= 0
    return factory


def _list(headers=None, limit=500):
    return main.recordings(_Req(headers), limit=limit, offset=0)


def test_recordings_list_is_cached_until_a_recording_changes(recordings_api, tmp_path):
    import json as _json
    _recording(recordings_api, tmp_path / "a.mp4")
    first = _list()
    assert first.status_code == 200 and len(_json.loads(first.body)) == 1
    etag = first.headers["etag"]
    assert etag.startswith('W/"') and first.headers["cache-control"] == "private, no-cache"
    again = _list()
    assert again.headers["etag"] == etag and again.body == first.body  # served from cache
    assert _list({"if-none-match": etag}).status_code == 304
    _recording(recordings_api, tmp_path / "b.mp4")  # committed write -> new generation
    changed = _list()
    assert len(_json.loads(changed.body)) == 2 and changed.headers["etag"] != etag
    assert _list({"if-none-match": etag}).status_code == 200  # stale validator is not accepted


def test_recordings_list_sees_updates_and_bulk_updates(recordings_api, tmp_path):
    import json as _json
    from sqlalchemy import update
    rec_id = _recording(recordings_api, tmp_path / "c.mp4", nsfw_status="pending")
    before = _json.loads(_list().body)[0]["nsfw_status"]
    with recordings_api.begin() as session:
        session.get(Recording, rec_id).nsfw_status = "safe"  # ORM flush path
    assert _json.loads(_list().body)[0]["nsfw_status"] == "safe" != before
    with recordings_api.begin() as session:
        session.execute(update(Recording).where(Recording.id == rec_id).values(nsfw_status="nsfw"))  # bulk path
    assert _json.loads(_list().body)[0]["nsfw_status"] == "nsfw"


def test_recordings_list_gzip_and_rollback(recordings_api, tmp_path):
    import gzip as _gzip
    import json as _json
    _recording(recordings_api, tmp_path / "d.mp4")
    plain = _list()
    packed = _list({"accept-encoding": "gzip, br"})
    assert packed.headers["content-encoding"] == "gzip" and _gzip.decompress(packed.body) == plain.body
    generation = __import__("app.db", fromlist=["x"]).recordings_generation()
    session = recordings_api()
    session.add(Recording(source_id=1, source_name="x", session_id="s", local_path="p", filename="p",
                          started_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
    session.flush()
    session.rollback()  # nothing committed: the cache stays valid
    session.close()
    assert __import__("app.db", fromlist=["x"]).recordings_generation() == generation
    assert len(_json.loads(_list().body)) == 1


# ---- opening a capture URL as a page gives a player with a valid timeline ----

def _fragmented_mp4(path: Path, seconds: int = 6) -> bool:
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        return False
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=25:duration={seconds}",
         "-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p", "-movflags", "frag_keyframe+empty_moov+default_base_moof", str(path)],
        capture_output=True)
    return result.returncode == 0 and path.exists()


class _Request:
    def __init__(self, headers=None, query=None):
        from starlette.datastructures import Headers
        self.headers = Headers(headers or {})
        self.query_params = dict(query or {})
        self.cookies = {}


@pytest.fixture()
def capture(tmp_path, monkeypatch, factory):
    growing = tmp_path / "AliciaBrooks_part001.capture.mp4"
    if not _fragmented_mp4(growing):
        pytest.skip("ffmpeg not available")
    monkeypatch.setattr(main, "require_auth", lambda _req: None)
    monkeypatch.setattr(main, "settings", SimpleNamespace(recordings_dir=tmp_path, data_dir=tmp_path, timezone="UTC"))
    monkeypatch.setattr(main, "db_session", workers_pkg.db_session)
    monkeypatch.setattr(main.manager, "playable_active_capture_path", lambda source_id: growing)
    return growing


def test_capture_opened_as_a_page_is_a_player_not_the_raw_file(capture):
    page = main.view_active_capture(25, _Request({"sec-fetch-dest": "document", "accept": "text/html"}))
    text = page.body.decode()
    assert page.media_type == "text/html"
    assert 'data-playlist="/api/sources/25/capture.m3u8"' in text
    assert 'data-raw="/api/sources/25/capture?raw=1"' in text and 'data-live="1"' in text
    assert "/static/player.js" in text and "<script>" not in text  # CSP: no inline script
    assert page.headers["cache-control"] == "no-store"
    # old browsers without Sec-Fetch-* headers ask for text/html
    assert main.view_active_capture(25, _Request({"accept": "text/html,application/xhtml+xml"})).media_type == "text/html"


def test_capture_still_serves_the_file_to_players_and_on_request(capture):
    for headers in ({"sec-fetch-dest": "video"}, {"sec-fetch-dest": "empty", "accept": "*/*"}, {"accept": "*/*"}, {}):
        response = main.view_active_capture(25, _Request(headers))
        assert Path(response.path) == capture and response.media_type == "video/mp4"
    forced = main.view_active_capture(25, _Request({"sec-fetch-dest": "document"}, {"raw": "1"}))
    assert Path(forced.path) == capture
    playlist = main.stream_active_capture(25, _Request()).body.decode()
    assert "#EXT-X-PLAYLIST-TYPE:EVENT" in playlist and "#EXT-X-ENDLIST" not in playlist
    assert "/api/sources/25/capture" in playlist and "#EXTINF" in playlist


def test_a_file_without_a_stream_index_is_never_wrapped(tmp_path, monkeypatch, factory):
    plain = tmp_path / "flat.mp4"
    if not _fragmented_mp4(plain):
        pytest.skip("ffmpeg not available")
    flat = tmp_path / "moov.mp4"
    import subprocess
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(plain), "-c", "copy", "-movflags", "+faststart", str(flat)], check=True)
    monkeypatch.setattr(main, "require_auth", lambda _req: None)
    monkeypatch.setattr(main, "settings", SimpleNamespace(recordings_dir=tmp_path, data_dir=tmp_path, timezone="UTC"))
    monkeypatch.setattr(main, "db_session", workers_pkg.db_session)
    monkeypatch.setattr(main.manager, "playable_active_capture_path", lambda source_id: flat)
    response = main.view_active_capture(25, _Request({"sec-fetch-dest": "document"}))
    assert Path(response.path) == flat  # a finished MP4 already has a valid timeline


def test_player_assets_exist_and_use_the_growing_duration():
    static = Path(__file__).resolve().parents[1] / "app" / "static"
    script = (static / "player.js").read_text(encoding="utf-8")
    assert "liveDurationInfinity: false" in script and "Registrato finora" in script
    assert (static / "player.css").is_file() and (static / "vendor" / "hls.min.js").is_file()


# ---- live playlist: append-only, whole session ----

def _segment_lines(playlist: str) -> list[str]:
    return [line for line in playlist.splitlines() if line.startswith("#EXT-X-BYTERANGE")]


def test_live_playlist_only_grows_and_lists_every_complete_fragment(tmp_path):
    from app.mp4_index import cached_index, hls_session_playlist
    full = tmp_path / "grow.mp4"
    if not _fragmented_mp4(full, seconds=8):
        pytest.skip("ffmpeg not available")
    every = cached_index(full, 0.0)
    assert len(every.segments) >= 6 and all(s.duration <= 1.5 for s in every.segments)  # one per fragment
    assert len(cached_index(full).segments) < len(every.segments)  # grouped VOD index is coarser
    data = full.read_bytes()
    previous: list[str] = []
    for count in range(1, len(every.segments) + 1):
        end = every.segments[count - 1].offset + every.segments[count - 1].length
        grown = tmp_path / "live.mp4"
        grown.write_bytes(data[: end + 37])  # 37 bytes of the next fragment: still being written
        lines = _segment_lines(hls_session_playlist([(cached_index(grown, 0.0), "/media")], live=True))
        assert len(lines) == count  # the partial fragment is not listed
        assert lines[: len(previous)] == previous  # nothing already listed ever changes
        previous = lines


def test_session_playlist_spans_parts_with_discontinuities(tmp_path):
    from app.mp4_index import cached_index, hls_session_playlist
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    if not (_fragmented_mp4(first, seconds=4) and _fragmented_mp4(second, seconds=3)):
        pytest.skip("ffmpeg not available")
    playlist = hls_session_playlist([(cached_index(first), "/api/fragments/1/view"),
                                     (cached_index(second, 0.0), "/api/sources/25/capture")], live=True)
    assert playlist.count("#EXT-X-MAP:") == 2 and playlist.count("#EXT-X-DISCONTINUITY") == 1
    assert playlist.index("/api/fragments/1/view") < playlist.index("#EXT-X-DISCONTINUITY") < playlist.rindex("/api/sources/25/capture")
    assert "#EXT-X-ENDLIST" not in playlist and "#EXT-X-PLAYLIST-TYPE:EVENT" in playlist


def test_capture_playlist_includes_earlier_local_parts_of_the_same_live(capture, factory, tmp_path, monkeypatch):
    closed = tmp_path / "AliciaBrooks_part000.mp4"
    assert _fragmented_mp4(closed, seconds=4)
    with factory.begin() as session:
        from app.db import RecordingFragment
        row = RecordingFragment(source_id=25, source_name="AliciaBrooks", session_id="live-1", local_path=str(closed),
                                filename=closed.name, started_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                                finalized_at=datetime(2026, 9, 1, 0, 5, tzinfo=timezone.utc))
        other = RecordingFragment(source_id=25, source_name="AliciaBrooks", session_id="an-older-live", local_path=str(tmp_path / "x.mp4"),
                                  filename="x.mp4", started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                                  finalized_at=datetime(2026, 8, 1, 0, 5, tzinfo=timezone.utc))
        session.add_all([row, other])
        session.flush()
        row_id = row.id
    monkeypatch.setattr(main.manager, "active", {25: SimpleNamespace(session_id="live-1")})
    playlist = main.stream_active_capture(25, _Request()).body.decode()
    assert f"/api/fragments/{row_id}/view" in playlist and "/api/sources/25/capture" in playlist
    assert playlist.index(f"/api/fragments/{row_id}/view") < playlist.index("#EXT-X-DISCONTINUITY") < playlist.rindex("/api/sources/25/capture")
    assert playlist.count("#EXT-X-MAP:") == 2  # the older live's fragment is not part of this timeline


# ---- Stripchat-like parts: 0.5 s fragments, a keyframe every fourth one ----

def _stripchat_like_mp4(path: Path, seconds: int = 12, dash_audio: bool = False) -> bool:
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        return False
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            f"testsrc=size=160x120:rate=24:duration={seconds}"]
    if dash_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-g", "48", "-keyint_min", "48", "-sc_threshold", "0", "-pix_fmt", "yuv420p",
             "-frag_duration", "500000", "-movflags", "empty_moov+default_base_moof" + ("+dash" if dash_audio else ""), str(path)]
    result = subprocess.run(args, capture_output=True)
    return result.returncode == 0 and path.exists()


def test_segments_always_start_on_a_keyframe(tmp_path):
    from app.mp4_index import GrowingIndex, build_index, live_playlist_index
    path = tmp_path / "stripchat_like.mp4"
    if not _stripchat_like_mp4(path):
        pytest.skip("ffmpeg not available")
    fragments = GrowingIndex(path).poll()
    keyframes = {fragment.offset for fragment in fragments if fragment.keyframe}
    assert len(fragments) == 24
    assert [index for index, fragment in enumerate(fragments) if fragment.keyframe] == list(range(0, 24, 4))
    assert all(fragment.duration == pytest.approx(0.5, abs=0.05) for fragment in fragments[:-1])
    finished = build_index(path, target_seconds=2.0)
    assert all(segment.offset in keyframes for segment in finished.segments)
    assert all(segment.duration >= 2.0 - 1e-6 for segment in finished.segments[:-1])
    live = live_playlist_index(path)
    assert live.segments and all(segment.offset in keyframes for segment in live.segments)
    assert all(segment.duration >= 2.0 - 1e-6 for segment in live.segments)  # never one request per 0.5 s
    assert len(live.segments) == 5  # the open final GOP is not published yet


def test_capture_endpoint_lists_only_closed_keyframe_segments(capture):
    from app.mp4_index import GrowingIndex
    assert _stripchat_like_mp4(capture)
    keyframes = {fragment.offset for fragment in GrowingIndex(capture).poll() if fragment.keyframe}
    playlist = main.stream_active_capture(25, _Request()).body.decode()
    ranges = _segment_lines(playlist)
    assert len(ranges) == 5
    assert all(int(line.rsplit("@", 1)[1]) in keyframes for line in ranges)
    assert playlist.count("#EXTINF:2.000,") == 5


def test_sidx_boxes_between_fragments_do_not_create_half_second_segments(tmp_path):
    import subprocess
    from app.mp4_index import GrowingIndex, LivePlaylistIndex, build_index
    path = tmp_path / "stripchat_sidx.mp4"
    if not _stripchat_like_mp4(path, dash_audio=True):
        pytest.skip("ffmpeg not available")
    fragments = GrowingIndex(path).poll()
    assert len(fragments) >= 24
    assert all(fragments[i].offset - fragments[i - 1].offset - fragments[i - 1].length == 104
               for i in range(1, 8))  # two sidx boxes, as in the real capture
    keyframes = {fragment.offset for fragment in fragments if fragment.keyframe}
    finished = build_index(path, target_seconds=2.0)
    live = LivePlaylistIndex(path).snapshot()
    assert len(live.segments) >= 5
    for index in (finished, live):
        assert all(segment.offset in keyframes for segment in index.segments)
        assert all(segment.duration >= 2.0 - 1e-6 for segment in index.segments[:-1])
        assert all(fragment.offset + fragment.length <= segment.offset + segment.length
                   for segment in index.segments for fragment in fragments
                   if segment.offset <= fragment.offset < segment.offset + segment.length)
    data = path.read_bytes()
    middle = live.segments[2]
    standalone = tmp_path / "seek_middle.mp4"
    standalone.write_bytes(data[:live.init_length] + data[middle.offset:middle.offset + middle.length])
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(standalone),
                    "-f", "null", "-"], check=True, capture_output=True)


def test_capture_endpoint_groups_sidx_fragments_on_keyframes(capture):
    from app.mp4_index import GrowingIndex
    assert _stripchat_like_mp4(capture, dash_audio=True)
    keyframes = {fragment.offset for fragment in GrowingIndex(capture).poll() if fragment.keyframe}
    playlist = main.stream_active_capture(25, _Request()).body.decode()
    ranges = _segment_lines(playlist)
    assert len(ranges) >= 5
    assert all(int(line.rsplit("@", 1)[1]) in keyframes for line in ranges)
    assert playlist.count("#EXTINF:2.000,") >= 5


def test_live_index_is_incremental_append_only_and_survives_a_recreated_file(tmp_path):
    from app.mp4_index import LivePlaylistIndex
    full = tmp_path / "full.mp4"
    if not _stripchat_like_mp4(full, seconds=20):
        pytest.skip("ffmpeg not available")
    data = full.read_bytes()
    growing = tmp_path / "growing.capture.mp4"
    index = LivePlaylistIndex(growing)
    seen: list[tuple[int, int]] = []
    written = 0
    for step in range(1, 11):
        end = min(len(data), len(data) * step // 10 + 11)
        with growing.open("ab") as output:
            output.write(data[written:end])  # each poll may encounter an unfinished box
        written = end
        segments = [(segment.offset, segment.length) for segment in index.snapshot().segments]
        assert segments[: len(seen)] == seen  # nothing already listed ever changes
        seen = segments
    assert len(seen) >= 6
    parsed_to = index.reader.position
    assert parsed_to > 0 and index.snapshot().segments
    assert index.reader.position == parsed_to  # a repeated request has no new boxes to read
    growing.write_bytes(data[: len(data) // 4])  # the part was recreated (shorter) under the same name
    restarted = index.snapshot()
    assert 0 < len(restarted.segments) < len(seen)
