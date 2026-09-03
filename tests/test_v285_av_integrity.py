from pathlib import Path
from types import SimpleNamespace

import app.utils as utils
from app.recorder import build_ffmpeg_command
from app.source_providers import ResolvedInput


def test_live_recorder_keeps_audio_and_video_on_the_same_source_timeline():
    cmd = build_ffmpeg_command(
        [ResolvedInput("https://example.test/live.m3u8", {}, "media")],
        Path("out_%03d.mp4"),
        segment_minutes=10,
        container_format="mp4",
    )
    joined = " ".join(cmd)
    assert "-fflags +genpts+discardcorrupt" in joined
    assert "-dts_delta_threshold 1" in joined
    assert "-thread_queue_size 8192" in joined
    assert "-c copy" in joined
    assert "-c:a aac" not in joined
    assert "aresample=" not in joined
    assert "-max_interleave_delta 1000000" in joined
    assert "-avoid_negative_ts make_zero" in joined


def test_timestamp_normalization_is_applied_to_each_separate_input():
    cmd = build_ffmpeg_command(
        [
            ResolvedInput("https://example.test/video.m3u8", {}, "video"),
            ResolvedInput("https://example.test/audio.m3u8", {}, "audio"),
        ],
        Path("out_%03d.mp4"),
        segment_minutes=10,
        container_format="mp4",
    )
    joined = " ".join(cmd)
    assert joined.count("-fflags +genpts+discardcorrupt") == 2
    assert joined.count("-dts_delta_threshold 1") == 2
    assert joined.count("-thread_queue_size 8192") == 2


def test_integrity_guard_rejects_large_av_drift():
    streams = [
        {"codec_type": "video", "start_time": "0", "duration": "100", "avg_frame_rate": "30/1"},
        {"codec_type": "audio", "start_time": "0.02", "duration": "103.2"},
    ]
    assert "fuori sync" in utils._stream_timing_error(streams)


def test_integrity_guard_accepts_small_encoder_skew():
    streams = [
        {"codec_type": "video", "start_time": "0", "duration": "100", "avg_frame_rate": "30/1"},
        {"codec_type": "audio", "start_time": "0.02", "duration": "100.04"},
    ]
    assert utils._stream_timing_error(streams) == ""


def test_integrity_guard_detects_video_timestamp_hole(monkeypatch, tmp_path):
    fake = SimpleNamespace(returncode=0, stdout="0.000,0.000\n0.033,0.033\n0.066,0.066\n1.500,1.500\n", stderr="")
    monkeypatch.setattr(utils.subprocess, "run", lambda *args, **kwargs: fake)
    streams = [{"codec_type": "video", "avg_frame_rate": "30/1"}]
    error = utils._video_gap_error(tmp_path / "clip.mp4", streams)
    assert "Gap video rilevato" in error


def test_normal_provider_does_not_enable_chaturbate_sync_mode():
    cmd = build_ffmpeg_command(
        [ResolvedInput("https://example.test/live.m3u8", {}, "media")],
        Path("out_%03d.mp4"),
        segment_minutes=10,
        container_format="mp4",
    )
    joined = " ".join(cmd)
    assert "-copyts -start_at_zero" not in joined
    assert "-c:a aac" not in joined
