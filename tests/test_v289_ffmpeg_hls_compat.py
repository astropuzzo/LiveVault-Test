from pathlib import Path

from app.recorder import build_ffmpeg_command
from app.source_providers import ResolvedInput


def test_local_synchronized_hls_does_not_receive_http_reconnect_options():
    cmd = build_ffmpeg_command(
        [ResolvedInput("/data/recordings/demo/.livevault-synced-master.m3u8", {"User-Agent": "LiveVault"}, "media")],
        Path("out_%03d.mp4"),
        segment_minutes=10,
        container_format="mp4",
        synchronized_hls=True,
    )
    assert "-protocol_whitelist" in cmd
    assert "-reconnect" not in cmd
    assert "-reconnect_streamed" not in cmd
    assert "-reconnect_delay_max" not in cmd


def test_direct_http_hls_keeps_reconnect_options():
    cmd = build_ffmpeg_command(
        [ResolvedInput("https://example.test/live.m3u8", {}, "media")],
        Path("out_%03d.mp4"),
        segment_minutes=10,
        container_format="mp4",
    )
    assert cmd[cmd.index("-reconnect") + 1] == "1"
    assert cmd[cmd.index("-reconnect_streamed") + 1] == "1"
    assert cmd[cmd.index("-reconnect_delay_max") + 1] == "5"
