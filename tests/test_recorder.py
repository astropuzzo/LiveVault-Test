import base64
import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace

from app import stripchat_capture
from app.recorder import (
    build_chaturbate_synced_master,
    build_ffmpeg_command,
    build_stripchat_capture_command,
    max_output_bytes,
    safe_output_limit_bytes,
    start_recorder,
)
from app.source_providers import ResolvedInput


def test_ffmpeg_single_input_mapping_mkv():
    cmd = build_ffmpeg_command(
        [ResolvedInput("https://example.test/master.m3u8", {}, "media")],
        Path("out_%03d.mkv"),
        segment_minutes=15,
        container_format="mkv",
    )
    joined = " ".join(cmd)
    assert "-map 0:v:0" in joined
    assert "-map 0:a:0" in joined
    assert "0:a:0?" not in joined
    assert "-c copy" in joined
    assert "-c:a aac" not in joined
    assert "-f segment" in joined
    assert "-segment_format matroska" in joined


def test_ffmpeg_separate_audio_video_mapping():
    inputs = [
        ResolvedInput("https://example.test/video.m3u8", {}, "video"),
        ResolvedInput("https://example.test/audio.m3u8", {}, "audio"),
    ]
    cmd = build_ffmpeg_command(inputs, Path("out_%03d.mp4"), segment_minutes=15, container_format="mp4")
    joined = " ".join(cmd)
    assert "-map 0:v:0" in joined
    assert "-map 1:a:0" in joined


def test_ffmpeg_combined_input_keeps_audio_with_multiple_inputs():
    inputs = [
        ResolvedInput("https://example.test/combined.m3u8", {}, "media"),
        ResolvedInput("https://example.test/backup-video.m3u8", {}, "video"),
    ]
    cmd = build_ffmpeg_command(inputs, Path("out_%03d.mp4"), segment_minutes=15, container_format="mp4")
    joined = " ".join(cmd)
    assert "-map 1:v:0" in joined
    assert "-map 0:a:0" in joined


def test_ffmpeg_direct_mp4_is_fragmented_stream_copy():
    cmd = build_ffmpeg_command(
        [ResolvedInput("https://example.test/master.m3u8", {}, "media")],
        Path("out_%03d.mp4"),
        segment_minutes=10,
        container_format="mp4",
    )
    joined = " ".join(cmd)
    assert "-c copy" in joined
    assert "-c:a aac" not in joined
    assert "aresample=" not in joined
    assert "-segment_format mp4" in joined
    assert "frag_keyframe" in joined
    assert "-fflags +genpts+discardcorrupt" in joined
    assert "-dts_delta_threshold 1" in joined
    assert "-thread_queue_size 8192" in joined
    assert "-max_interleave_delta 1000000" in joined
    assert "empty_moov" in joined
    assert "-segment_time 600" in joined


def test_default_requested_limits_are_60_minutes_and_below_two_gib():
    cmd = build_ffmpeg_command(
        [ResolvedInput("https://example.test/master.m3u8", {}, "media")],
        Path("out_%03d.mp4"),
        segment_minutes=60,
        segment_max_gb=2,
        container_format="mp4",
    )
    assert cmd[cmd.index("-segment_time") + 1] == "3600"
    file_limit = int(cmd[cmd.index("-fs") + 1])
    assert file_limit == safe_output_limit_bytes(2)
    assert 1.8 * 1024**3 < file_limit < max_output_bytes(2)
    assert "0:a:0?" not in cmd


def test_ffmpeg_live_preview_uses_same_process():
    cmd = build_ffmpeg_command(
        [ResolvedInput("https://example.test/master.m3u8", {}, "media")],
        Path("out_%03d.mp4"),
        segment_minutes=10,
        container_format="mp4",
        preview_path=Path("preview.jpg"),
        preview_interval_seconds=20,
    )
    joined = " ".join(cmd)
    assert joined.count("-map 0:v:0") == 2
    assert "-c copy" in joined
    assert "-c:a aac" not in joined
    assert "-vf fps=1/20,scale=640:-2:force_original_aspect_ratio=decrease" in joined
    assert "-c:v mjpeg" in joined
    assert "-skip_frame nokey" in joined
    assert "-threads:v 1" in joined
    assert "-update 1" in joined
    assert joined.index("out_%03d.mp4") < joined.index("preview.jpg")


def test_recorder_does_not_decode_previews_without_a_viewer():
    source = inspect.getsource(start_recorder)
    assert "preview_path=None" in source


def test_stripchat_uses_dedicated_native_hls_capture():
    source = SimpleNamespace(slug="angel")
    cmd = build_stripchat_capture_command(
        source,
        Path("capture_part%03d.mp4"),
        Path("preview.jpg"),
        segment_minutes=20,
        segment_max_gb=2,
        container_format="mp4",
    )
    joined = " ".join(cmd)
    assert "-m app.stripchat_capture" in joined
    assert "--slug angel" in joined
    assert "--segment-seconds 1200" in joined
    assert "--container mp4" in joined


def test_stripchat_capture_is_browserless_and_copy_only():
    capture = (Path(__file__).resolve().parents[1] / "app" / "stripchat_capture.py").read_text(encoding="utf-8")
    lowered = capture.lower()

    assert "playwright" not in lowered
    assert "mediarecorder" not in lowered
    assert "chromium" not in lowered
    assert "libx264" not in lowered
    assert "aresample=" not in lowered
    assert "ThreadPoolExecutor(max_workers=1" in capture
    assert '"-c",' in capture and '"copy"' in capture


def _encrypt_mouflon(value: str, key: str, *, reverse: bool = False) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    raw = value.encode("utf-8")
    encrypted = bytes(byte ^ digest[index % len(digest)] for index, byte in enumerate(raw))
    encoded = base64.b64encode(encrypted).decode("ascii").rstrip("=")
    return encoded[::-1] if reverse else encoded


def test_stripchat_mouflon_v1_round_trip():
    key = "ubahjae7goPoodi6"
    filename = "segment_00123.mp4"
    encrypted = _encrypt_mouflon(filename, key)

    assert stripchat_capture.decode_v1_name(encrypted, key) == filename


def test_stripchat_mouflon_v2_round_trip():
    key = "anotherDecodeKey123"
    filename = "real-segment-name"
    encrypted = _encrypt_mouflon(filename, key, reverse=True)
    source = f"https://media-hls.doppiocdn.org/path/chunk_{encrypted}_123_part2.mp4"

    decoded = stripchat_capture.decode_v2_url(source, key)

    assert decoded.endswith(f"/chunk_{filename}_123_part2.mp4")


def test_stripchat_media_playlist_decodes_v2_and_keeps_discontinuity():
    key = "decodeKeyForTest123"
    plain = "camera-fragment"
    encrypted = _encrypt_mouflon(plain, key, reverse=True)
    selection = stripchat_capture.MasterSelection(
        "https://media-hls.doppiocdn.org/live/playlist.m3u8?playlistType=lowLatency",
        "v2",
        "PkeyForUnitTest123",
        key,
    )
    body = "\n".join([
        "#EXTM3U",
        "#EXT-X-TARGETDURATION:2",
        '#EXT-X-MAP:URI="init.mp4"',
        "#EXT-X-DISCONTINUITY",
        f"#EXT-X-MOUFLON:URI:https://media-hls.doppiocdn.org/live/chunk_{encrypted}_900.mp4",
        "media.mp4",
    ])

    parsed = stripchat_capture.parse_media_playlist(selection.media_url, body, selection)

    assert parsed.init_url is not None
    assert "psch=v2" in parsed.init_url
    assert "pkey=PkeyForUnitTest123" in parsed.init_url
    assert len(parsed.segments) == 1
    assert parsed.segments[0].discontinuity is True
    assert f"chunk_{plain}_900.mp4" in parsed.segments[0].url
    assert "psch=v2" in parsed.segments[0].url


def test_stripchat_status_uses_id_based_cam_endpoint():
    calls = []

    class Response:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    class Session:
        def get(self, url, **_kwargs):
            calls.append(url)
            if "/users/username/" in url:
                return Response({"item": {"id": 4242}})
            if "/models/4242/cam" in url:
                return Response({
                    "user": {"user": {"id": 4242, "status": "public"}},
                    "cam": {"isCamActive": True, "isCamAvailable": True, "streamName": "4242"},
                })
            raise AssertionError(url)

    session = Session()
    user_id, payload = stripchat_capture.get_cam_state(session, "example")
    stream_id = stripchat_capture._public_stream_id(payload, user_id)

    assert user_id == 4242
    assert stream_id == "4242"
    assert any("/api/front/v2/users/username/example" in url for url in calls)
    assert any("/api/front/v2/models/4242/cam" in url for url in calls)
    assert all("/models/username/" not in url for url in calls)


def test_local_synchronized_hls_never_receives_http_avoptions():
    cmd = build_ffmpeg_command(
        [ResolvedInput(
            "/data/recordings/test/.livevault-synced-master.m3u8",
            {"User-Agent": "LiveVault-Test", "Referer": "https://chaturbate.com/"},
            "media",
        )],
        Path("out_%03d.mp4"),
        segment_minutes=10,
        container_format="mp4",
        synchronized_hls=True,
    )
    assert "-headers" not in cmd
    assert "-reconnect" not in cmd
    assert "-reconnect_streamed" not in cmd
    assert "-reconnect_delay_max" not in cmd
    assert "-protocol_whitelist" in cmd


def test_synced_master_drops_top_level_http_headers(tmp_path):
    inputs = [
        ResolvedInput(
            "https://example.test/llhls/video.m3u8",
            {"User-Agent": "UA", "Referer": "https://chaturbate.com/"},
            "video",
        ),
        ResolvedInput(
            "https://example.test/llhls/audio.m3u8",
            {"User-Agent": "UA", "Referer": "https://chaturbate.com/"},
            "audio",
        ),
    ]
    synced, manifest = build_chaturbate_synced_master(inputs, tmp_path / "master.m3u8")
    assert manifest.is_file()
    assert synced[0].url == str(manifest.resolve())
    assert synced[0].http_headers == {}


def test_direct_http_input_still_receives_headers_and_reconnect():
    cmd = build_ffmpeg_command(
        [ResolvedInput(
            "https://example.test/master.m3u8",
            {"User-Agent": "LiveVault-Test", "Referer": "https://example.test/"},
            "media",
        )],
        Path("out_%03d.mp4"),
        segment_minutes=10,
        container_format="mp4",
    )
    assert "-headers" in cmd
    assert "-reconnect" in cmd
