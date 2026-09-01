from pathlib import Path

from app.recorder import build_ffmpeg_command
from app.source_providers import ResolvedInput


def test_ffmpeg_single_input_mapping_mkv():
    cmd = build_ffmpeg_command(
        [ResolvedInput("https://example.test/master.m3u8", {}, "media")],
        Path("out_%03d.mkv"),
        segment_minutes=15,
        container_format="mkv",
    )
    joined = " ".join(cmd)
    assert "-map 0:v:0?" in joined
    assert "-map 0:a:0?" in joined
    assert "-c copy" in joined
    assert "-f segment" in joined
    assert "-segment_format matroska" in joined


def test_ffmpeg_separate_audio_video_mapping():
    inputs = [
        ResolvedInput("https://example.test/video.m3u8", {}, "video"),
        ResolvedInput("https://example.test/audio.m3u8", {}, "audio"),
    ]
    cmd = build_ffmpeg_command(inputs, Path("out_%03d.mp4"), segment_minutes=15, container_format="mp4")
    joined = " ".join(cmd)
    assert "-map 0:v:0?" in joined
    assert "-map 1:a:0?" in joined


def test_ffmpeg_combined_input_keeps_audio_with_multiple_inputs():
    inputs = [
        ResolvedInput("https://example.test/combined.m3u8", {}, "media"),
        ResolvedInput("https://example.test/backup-video.m3u8", {}, "video"),
    ]
    cmd = build_ffmpeg_command(inputs, Path("out_%03d.mp4"), segment_minutes=15, container_format="mp4")
    joined = " ".join(cmd)
    assert "-map 1:v:0?" in joined
    assert "-map 0:a:0?" in joined


def test_ffmpeg_direct_mp4_is_fragmented_stream_copy():
    cmd = build_ffmpeg_command(
        [ResolvedInput("https://example.test/master.m3u8", {}, "media")],
        Path("out_%03d.mp4"),
        segment_minutes=10,
        container_format="mp4",
    )
    joined = " ".join(cmd)
    assert "-c copy" in joined
    assert "-segment_format mp4" in joined
    assert "frag_keyframe" in joined
    assert "empty_moov" in joined
    assert "-segment_time 600" in joined
