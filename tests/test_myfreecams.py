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
    wire = frame({}, "311") + frame(payload)
    ws = Socket([wire[:2], wire[2:31], wire[31:]])
    monkeypatch.setattr(mfc, "server_config", lambda: {"websocket_servers": {"wchat1": "rfc6455"}})
    monkeypatch.setattr(mfc, "connect", lambda *_args, **_kwargs: ws)
    room = mfc.lookup("example")
    assert (room.uid, room.status, room.server) == (42, "live", "800")
    assert ws.closed
    assert ws.sent[0] == "hello fcserver\n\0"
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
