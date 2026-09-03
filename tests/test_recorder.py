from pathlib import Path
import inspect

from app.recorder import (
    build_chaturbate_synced_master,
    build_ffmpeg_command,
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
