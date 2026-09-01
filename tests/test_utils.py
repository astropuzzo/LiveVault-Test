from app.utils import safe_name
import asyncio

from app.source_providers import (
    ResolvedInput,
    _yt_dlp_live_state,
    audit_inputs,
    classify_format,
    detect_provider,
    normalize_source,
    provider_catalog,
    resolve_inputs,
    source_url,
)


def test_safe_name():
    assert safe_name(" hello world / x ") == "hello_world_x"
    assert safe_name("../../") == "source"


def test_source_url():
    assert source_url("chaturbate", "demo") == "https://chaturbate.com/demo/"
    assert source_url("twitch", "demo") == "https://www.twitch.tv/demo"
    assert source_url("kick", "demo") == "https://kick.com/demo"


def test_provider_catalog_and_auto_detection():
    ids = {provider["id"] for provider in provider_catalog()}
    assert {"auto", "chaturbate", "stripchat", "bongacams", "camsoda", "cam4", "twitch", "kick", "youtube"} <= ids
    assert detect_provider("demo") == "chaturbate"
    assert detect_provider("https://www.twitch.tv/example") == "twitch"
    assert detect_provider("https://youtu.be/example") == "youtube"
    try:
        detect_provider("https://media.example.test/live.m3u8")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown hosts must not become a server-side fetch target")


def test_normalize_known_sources_without_network():
    assert normalize_source("auto", "https://chaturbate.com/demo/?x=1") == ("chaturbate", "demo")
    assert normalize_source("twitch", "@Example_Channel") == ("twitch", "example_channel")
    assert normalize_source("youtube", "https://youtube.com/watch?v=abc123_DEF0#fragment") == (
        "youtube",
        "https://www.youtube.com/watch?v=abc123_DEF0",
    )


def test_provider_url_rejects_mismatch_and_credentials():
    for platform, value in (
        ("twitch", "https://kick.com/example"),
        ("youtube", "https://name:secret@youtube.com/watch?v=abc123_DEF0"),
    ):
        try:
            normalize_source(platform, value)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe or mismatched provider URL must be rejected")


def test_yt_dlp_live_state_requires_a_live_signal():
    assert _yt_dlp_live_state({"is_live": True}) == (True, "live")
    assert _yt_dlp_live_state({"live_status": "is_upcoming"}) == (False, "is_upcoming")
    assert _yt_dlp_live_state({"duration": None, "protocol": "m3u8_native"}) == (False, "offline")
    assert _yt_dlp_live_state({"duration": 120, "protocol": "https"}) == (False, "offline")


def test_audio_guard_uses_actual_ffprobe_streams(monkeypatch):
    payloads = [
        b'{"streams":[{"codec_type":"video"}]}',
        b'{"streams":[{"codec_type":"audio"}]}',
    ]

    class FakeProcess:
        returncode = 0

        def __init__(self, payload):
            self.payload = payload

        async def communicate(self):
            return self.payload, b""

    async def fake_subprocess(*_args, **_kwargs):
        return FakeProcess(payloads.pop(0))

    monkeypatch.setattr("app.source_providers.asyncio.create_subprocess_exec", fake_subprocess)
    inputs = [
        ResolvedInput("https://cdn.example/video", {}, "unknown"),
        ResolvedInput("https://cdn.example/audio", {}, "unknown"),
    ]
    result = asyncio.run(audit_inputs(inputs))

    assert result.has_video is True
    assert result.has_audio is True
    assert result.error == ""
    assert [item.kind for item in inputs] == ["video", "audio"]


def test_audio_guard_fails_closed_without_audio(monkeypatch):
    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b'{"streams":[{"codec_type":"video"}]}', b""

    async def fake_subprocess(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr("app.source_providers.asyncio.create_subprocess_exec", fake_subprocess)
    result = asyncio.run(audit_inputs([ResolvedInput("https://cdn.example/video", {}, "media")]))

    assert result.has_video is True
    assert result.has_audio is False
    assert "audio assente" in result.error


def test_resolved_media_url_rejects_local_protocol(monkeypatch):
    monkeypatch.setattr(
        "app.source_providers._extract",
        lambda *_args, **_kwargs: {"url": "file:///etc/passwd", "vcodec": "h264", "acodec": "aac"},
    )
    try:
        asyncio.run(resolve_inputs("chaturbate", "demo"))
    except RuntimeError as exc:
        assert "unsafe media URL" in str(exc)
    else:
        raise AssertionError("local media protocols must never reach FFmpeg")


def test_format_classification_preserves_combined_audio_video():
    assert classify_format("h264", "aac") == "media"
    assert classify_format("h264", "none") == "video"
    assert classify_format("none", "aac") == "audio"
    assert classify_format("none", "none") == "unknown"


def test_hls_audio_rendition_without_codec_metadata_is_kept():
    assert classify_format(
        "none",
        None,
        format_id="audio_aac_128-Audio_200_1_5",
        format_label="audio only (high)",
    ) == "audio"
