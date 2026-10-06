import asyncio
import json
import time
from urllib.parse import quote

import pytest

from app import myfreecams as mfc, source_providers as providers


@pytest.mark.parametrize("url", [
    "https://www.myfreecams.com/#Some_Model",
    "https://myfreecams.com/Some_Model",
    "https://m.myfreecams.com/#Some_Model",
    "https://profiles.myfreecams.com/Some_Model",
    "https://share.myfreecams.com/Some_Model?tracking=1",
])
def test_mfc_links_preserve_username(url):
    assert providers.normalize_source("auto", url) == ("myfreecams", "some_model")
    assert providers.source_url("myfreecams", "some_model") == "https://www.myfreecams.com/#some_model"


def test_native_provider_and_alias_do_not_require_yt_dlp_extractor(monkeypatch):
    monkeypatch.setattr(providers, "_available_extractors", lambda: set())
    catalog = providers.provider_catalog()
    assert next(item for item in catalog if item["id"] == "myfreecams")["extractor_available"]
    assert providers.normalize_source("mfc", "@Some_Model") == ("myfreecams", "some_model")


def test_public_link_uses_matching_mfc_name_casing():
    assert providers.source_url("myfreecams", "iam_sasha", display_name="Iam_Sasha") == "https://www.myfreecams.com/#Iam_Sasha"
    assert providers.source_url("myfreecams", "iam_sasha", display_name="My favourite") == "https://www.myfreecams.com/#iam_sasha"
    assert providers.source_url("chaturbate", "example", display_name="Example") == "https://chaturbate.com/example/"


def test_source_api_link_uses_saved_mfc_username():
    from types import SimpleNamespace
    from app import main

    source = SimpleNamespace(platform="myfreecams", slug="iam_sasha", name="Iam_Sasha")
    assert main._source_public_url(source) == "https://www.myfreecams.com/#Iam_Sasha"


def test_inspection_link_uses_official_mfc_username(monkeypatch):
    from app import main

    async def probe(*_args):
        return providers.ProbeResult(False, "offline", title="Iam_Sasha")

    monkeypatch.setattr(main, "require_auth", lambda _request: None)
    monkeypatch.setattr(main, "probe", probe)
    result = asyncio.run(main.inspect_source(main.SourceInspect(platform="mfc", slug="Iam_Sasha"), None))
    assert result["source_url"] == "https://www.myfreecams.com/#Iam_Sasha"


@pytest.mark.parametrize("value", [
    "https://www.myfreecams.com/", "https://www.myfreecams.com/#",
    "https://www.myfreecams.com/#name%0Acommand",
    "https://www.myfreecams.com/foo/bar", "https://www.myfreecams.com/#/foo/bar",
    "http://www.myfreecams.com/#name", "https://user:secret@www.myfreecams.com/#name",
    "https://www.myfreecams.com:8443/#name", "https://myfreecams.com.evil.test/#name",
    "name\ncommand", "some.name",
])
def test_invalid_mfc_inputs_are_rejected(value):
    with pytest.raises(ValueError):
        providers.normalize_source("myfreecams", value)


def frame(payload, message_type="10", arg2="0"):
    message = f"{message_type} 1 2 20 {arg2} {quote(json.dumps(payload))}"
    return f"{len(message):06d}{message}"


class Socket:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.sent = []
        self.closed = False

    def recv(self, timeout):
        assert 0 < timeout <= 10
        return next(self.chunks)

    def send(self, value):
        self.sent.append(value)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True


def test_guest_lookup_handles_fragmented_and_batched_protocol_messages(monkeypatch):
    payload = {"uid": 42, "lv": 4, "vs": 0, "nm": "Example", "u": {"camserv": 800}}
    wire = frame({}, "1", "1") + frame({}, "14") + frame(payload)
    ws = Socket([wire[:2], wire[2:31], wire[31:]])
    monkeypatch.setattr(mfc, "server_config", lambda: {"websocket_servers": {"wchat1": "rfc6455"}})
    monkeypatch.setattr(mfc, "connect", lambda *_args, **_kwargs: ws)
    room = mfc.lookup("example")
    assert (room.uid, room.status, room.server) == (42, "live", "800")
    assert ws.closed
    assert ws.sent[0] == "hello fcserver\n\0"
    assert ws.sent[1] == "1 0 0 20080909 0 guest:guest\n"
    assert ws.sent[-1] == "10 0 0 20 0 example\n"


@pytest.mark.parametrize(("state", "status", "live"), [
    (0, "live", True), (2, "away", True), (12, "private", True),
    (13, "private", True), (14, "private", True), (90, "offline", False),
    (127, "offline", False), (999, "unknown", False), (None, "unknown", False),
])
def test_authoritative_room_states(state, status, live, monkeypatch):
    fields = ["10", "1", "2", "20", "0", json.dumps({"uid": 42, "lv": 4, "vs": state})]
    room = mfc._room(fields, "example")
    monkeypatch.setattr(mfc, "lookup", lambda _slug: room)
    result = asyncio.run(providers.probe("myfreecams", "example"))
    assert (result.status, result.live, result.recordable) == (status, live, status == "live")


def test_lookup_failure_is_error_not_offline_and_closes_connection(monkeypatch):
    ws = Socket(["bad framing"])
    monkeypatch.setattr(mfc, "server_config", lambda: {"websocket_servers": {"wchat1": "rfc6455"}})
    monkeypatch.setattr(mfc, "connect", lambda *_args, **_kwargs: ws)
    result = asyncio.run(providers.probe("myfreecams", "example"))
    assert result.status == "error"
    assert not result.live and not result.recordable
    assert ws.closed


def test_missing_account_is_offline_but_missing_model_id_is_error():
    assert mfc._room(["10", "1", "2", "20", "1", "example"], "example").status == "offline"
    with pytest.raises(RuntimeError):
        mfc._room(["10", "1", "2", "20", "0", '{"lv":4,"vs":0}'], "example")


def test_protocol_parser_deadline():
    with pytest.raises(TimeoutError):
        list(mfc._messages(Socket([]), time.monotonic() - 1))


def test_server_configuration_is_cached(monkeypatch):
    calls = []
    class Response:
        text = 'var serverconfig = {"websocket_servers":{"wchat1":"rfc6455"}};'
        def raise_for_status(self):
            pass
    monkeypatch.setattr(mfc, "_config", {})
    monkeypatch.setattr(mfc, "_config_until", 0)
    def get(*_args, **_kwargs):
        calls.append(1)
        return Response()
    monkeypatch.setattr(mfc.requests, "get", get)
    assert mfc.server_config() == mfc.server_config()
    assert len(calls) == 1


def test_playlist_candidates_use_current_provider_map_and_stream_id():
    urls = mfc._playlist_candidates(mfc.Room(42, "live", "800"), {"wzobs_servers": {"800": "video300"}})
    assert urls[0] == "https://video300.myfreecams.com/NxServer/ngrp:mfc_a_100000042.f4v_cmaf/playlist_sfm4s.m3u8"
    assert urls[1].endswith(".f4v_mobile/playlist.m3u8")
    with pytest.raises(RuntimeError):
        mfc._playlist_candidates(mfc.Room(42, "live", "800"), {"wzobs_servers": {"800": "evil.test/path"}})


@pytest.mark.parametrize("status", ["private", "away", "offline", "unknown"])
def test_nonpublic_rooms_never_fetch_hls(status, monkeypatch):
    monkeypatch.setattr(mfc, "lookup", lambda _slug: mfc.Room(42, status, "800"))
    monkeypatch.setattr(mfc.requests, "get", lambda *_args, **_kwargs: pytest.fail("Nonpublic HLS request"))
    with pytest.raises(RuntimeError, match=status):
        mfc.public_playlist("example")


def test_cmaf_unavailable_uses_mobile_playlist_and_preserves_headers(monkeypatch):
    monkeypatch.setattr(mfc, "lookup", lambda _slug: mfc.Room(42, "live", "800"))
    monkeypatch.setattr(mfc, "server_config", lambda: {"h5video_servers": {"800": "video300"}})
    calls = []
    class Response:
        def __init__(self, status, text):
            self.status_code, self.text = status, text
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response(404, "missing") if len(calls) == 1 else Response(200, "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nchunk.m3u8")
    monkeypatch.setattr(mfc.requests, "get", get)
    url, headers = mfc.public_playlist("example")
    assert url.endswith(".f4v_mobile/playlist.m3u8")
    assert headers["Referer"] == "https://www.myfreecams.com/"
    assert all(not options["allow_redirects"] for _, options in calls)


def test_mfc_inputs_pass_quality_headers_and_actual_audio_guard(monkeypatch):
    headers = {"Referer": "https://www.myfreecams.com/"}
    monkeypatch.setattr(mfc, "public_playlist", lambda _slug: ("https://video300.myfreecams.com/live.m3u8", headers))
    def extract(url, quality, **kwargs):
        assert quality == "720p"
        assert kwargs["http_headers"] == headers
        return {"url": url, "vcodec": "h264", "acodec": "aac", "http_headers": headers}
    monkeypatch.setattr(providers, "_extract", extract)
    inputs = asyncio.run(providers.resolve_inputs("myfreecams", "example", "720p"))
    assert len(inputs) == 1 and inputs[0].kind == "media"
    assert inputs[0].http_headers == headers
    assert inputs[0].allow_mfc_pts


@pytest.mark.parametrize("help_text, expected", [
    ("-extension_picky <boolean>", ("-extension_picky", "0")),
    ("-allowed_extensions <string>", ("-allowed_extensions", "aac,m3u8,m4s,mp4,ts,pts,cmfv,cmfa")),
])
def test_mfc_hls_options_support_current_and_older_ffmpeg(help_text, expected, monkeypatch):
    mfc.hls_options.cache_clear()
    calls = []
    class Result:
        stdout, stderr = help_text, ""
    def run(*_args, **_kwargs):
        calls.append(1)
        return Result()
    monkeypatch.setattr(mfc.subprocess, "run", run)
    try:
        assert mfc.hls_options("ffprobe") == expected
        assert mfc.hls_options("ffprobe") == expected
        assert len(calls) == 1
    finally:
        mfc.hls_options.cache_clear()


def test_mfc_pts_setting_reaches_recorder_and_audio_guard(monkeypatch):
    from app.recorder import build_ffmpeg_command
    monkeypatch.setattr(mfc, "hls_options", lambda _program: ("-extension_picky", "0"))
    item = providers.ResolvedInput("https://video300.myfreecams.com/live.m3u8", {}, "media", allow_mfc_pts=True)
    command = build_ffmpeg_command([item], "/tmp/mfc-%03d.mp4")
    assert command[command.index("-extension_picky") + 1] == "0"
    assert command.index("-extension_picky") < command.index("-i")
    assert "-extension_picky" not in build_ffmpeg_command([providers.ResolvedInput("https://cdn.example/live.m3u8", {}, "media")], "/tmp/normal-%03d.mp4")
    class Process:
        returncode = 0
        async def communicate(self):
            return b'{"streams":[{"codec_type":"video","nb_read_packets":"10"}]}', b""
    async def launch(*args, **_kwargs):
        assert args[args.index("-extension_picky") + 1] == "0"
        return Process()
    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", launch)
    audit = asyncio.run(providers.audit_inputs([item]))
    assert audit.has_video and not audit.has_audio
    assert "audio assente" in audit.error


@pytest.mark.parametrize("audio_packets", [None, "0", "12"])
def test_mfc_empty_audio_track_does_not_block_recording(monkeypatch, audio_packets):
    monkeypatch.setattr(mfc, "hls_options", lambda _program: ())
    item = providers.ResolvedInput("https://video300.myfreecams.com/live.m3u8", {}, "media", allow_mfc_pts=True)
    class Process:
        returncode = 0
        async def communicate(self):
            payload = {"streams": [{"codec_type": "video", "nb_read_packets": "10"}, {"codec_type": "audio", "nb_read_packets": audio_packets}]}
            return json.dumps(payload).encode(), b""
    async def launch(*args, **_kwargs):
        assert "-count_packets" not in args
        return Process()
    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", launch)
    audit = asyncio.run(providers.audit_inputs([item]))
    assert audit.has_video and audit.has_audio
    assert not audit.error
