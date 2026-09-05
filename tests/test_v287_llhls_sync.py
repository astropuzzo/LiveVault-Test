import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.recorder import (
    build_chaturbate_synced_master,
    build_ffmpeg_command,
    is_chaturbate_split_llhls,
    stream_transport_fault,
)
from app.source_providers import ResolvedInput


def split_inputs():
    return [
        ResolvedInput(
            "https://edge.example.test/v1/chunklist_4_video_123_llhls.m3u8?session=abc",
            {"User-Agent": "LiveVault-Test"},
            "video",
        ),
        ResolvedInput(
            "https://edge.example.test/v1/chunklist_6_audio_123_llhls.m3u8?session=abc",
            {"User-Agent": "LiveVault-Test"},
            "audio",
        ),
    ]


def test_split_llhls_detection_is_chaturbate_only():
    inputs = split_inputs()
    assert is_chaturbate_split_llhls("chaturbate", inputs)
    assert not is_chaturbate_split_llhls("stripchat", inputs)
    assert not is_chaturbate_split_llhls(
        "chaturbate",
        [ResolvedInput("https://edge.example.test/master.m3u8", {}, "media")],
    )


def test_synced_master_contains_one_selected_audio_group_and_video(tmp_path):
    inputs, manifest = build_chaturbate_synced_master(
        split_inputs(), tmp_path / ".livevault-synced-master.m3u8"
    )
    body = manifest.read_text(encoding="utf-8")
    assert len(inputs) == 1
    assert inputs[0].kind == "media"
    assert inputs[0].url == str(manifest.resolve())
    assert body.count("#EXT-X-MEDIA:TYPE=AUDIO") == 1
    assert 'DEFAULT=YES,AUTOSELECT=YES' in body
    assert "chunklist_6_audio_123_llhls.m3u8?session=abc" in body
    assert "chunklist_4_video_123_llhls.m3u8?session=abc" in body


def test_synced_command_uses_one_hls_clock_and_no_audio_first_pts_reset(tmp_path):
    inputs, manifest = build_chaturbate_synced_master(
        split_inputs(), tmp_path / ".livevault-synced-master.m3u8"
    )
    cmd = build_ffmpeg_command(
        inputs,
        tmp_path / "out_%03d.mp4",
        segment_minutes=10,
        container_format="mp4",
        synchronized_hls=True,
    )
    joined = " ".join(cmd)
    assert joined.count(" -i ") == 1
    assert "-protocol_whitelist file,http,https,tcp,tls,crypto,data" in joined
    assert "-rw_timeout 15000000" in joined
    assert "-live_start_index -1" in joined
    assert "-copyts -start_at_zero" in joined
    assert "-c:v copy" in joined
    assert "-c:a aac" in joined
    assert "aresample=async=1" in joined
    assert "first_pts=0" not in joined
    assert str(manifest.resolve()) in joined


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_real_ffmpeg_reads_synthetic_split_master(tmp_path):
    video = tmp_path / "video.m3u8"
    audio = tmp_path / "audio.m3u8"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-t", "2",
            "-c:v", "libx264", "-an", "-hls_time", "1", "-hls_list_size", "0",
            "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init_video.mp4",
            "-hls_segment_filename", str(tmp_path / "v%03d.m4s"), str(video),
        ],
        check=True,
        cwd=tmp_path,
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000", "-t", "2",
            "-c:a", "aac", "-vn", "-hls_time", "1", "-hls_list_size", "0",
            "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init_audio.mp4",
            "-hls_segment_filename", str(tmp_path / "a%03d.m4s"), str(audio),
        ],
        check=True,
        cwd=tmp_path,
    )
    # Same syntax as production, with local child playlists for a hermetic CI check.
    manifest = tmp_path / "master.m3u8"
    manifest.write_text(
        "#EXTM3U\n#EXT-X-VERSION:6\n#EXT-X-INDEPENDENT-SEGMENTS\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="livevault_audio",NAME="LiveVault Audio",DEFAULT=YES,AUTOSELECT=YES,FORCED=NO,URI="audio.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=20000000,AUDIO="livevault_audio"\nvideo.m3u8\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-protocol_whitelist", "file,http,https,tcp,tls,crypto,data",
            "-show_entries", "stream=codec_type", "-of", "json", str(manifest),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    kinds = {stream["codec_type"] for stream in payload["streams"]}
    assert kinds == {"video", "audio"}


def test_transport_guard_restarts_only_on_destructive_hls_faults():
    assert stream_transport_fault("[hls] skipping 36 segments ahead, expired from playlists")
    assert stream_transport_fault("The specified session has been invalidated for some reason")
    assert stream_transport_fault("Invalid NAL unit size (123 > 45)") == ""
    assert stream_transport_fault("missing picture in access unit with size 22123") == ""
    assert stream_transport_fault("Failed to open an initialization section in playlist 1")
    assert stream_transport_fault("Error when loading first segment 'https://edge.example/seg.m4s'")
    assert stream_transport_fault("Error opening input file /data/recordings/demo/.livevault-synced-master-x.m3u8")
    assert stream_transport_fault("Opening next HLS segment") == ""


def test_worker_has_immediate_transport_restart_and_repair_cleanup():
    source = (Path(__file__).resolve().parents[1] / "app" / "workers.py").read_text(encoding="utf-8")
    assert "session.transport_guard and not session.restart_requested" in source
    assert "if session.restart_requested:" in source
    assert "controlled_restart = session.restart_requested" in source
    assert "HLS_CAPTURE_STALL_SECONDS = 35" in source
    assert "HLS_RESTART_BACKOFF_SECONDS = 12" in source
    assert "nessun nuovo dato scritto; riavvio automatico" in source
    assert 'self.last_errors.pop(f"mp4-repair:{rec.id}", None)' in source
    assert 'or "a/v fuori sync" in error_text' in source
