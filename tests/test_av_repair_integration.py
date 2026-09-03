import asyncio
import shutil
import subprocess

import pytest

from app.recorder import finalize_mp4_for_streaming, mp4_is_streaming_ready
from app.utils import probe_media


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg tools are required for the real A/V integration test",
)


def test_real_ffmpeg_repair_closes_large_audio_tail(tmp_path):
    """Regression for the 11s-style failure seen in production.

    Build a fragmented MP4 whose video lasts ~2s while audio lasts ~5s.
    The normal copy-remux cannot make those timelines agree; LiveVault must
    therefore rebuild both streams and return a normal, seekable MP4.
    """
    path = tmp_path / "mismatched.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=5",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )

    before = probe_media(path)
    assert not before.ok
    assert "A/V fuori sync" in before.error
    assert not mp4_is_streaming_ready(path)

    changed = asyncio.run(finalize_mp4_for_streaming(path, require_space=False))
    assert changed is True
    assert mp4_is_streaming_ready(path)

    after = probe_media(path)
    assert after.ok, after.error
    video = next(row for row in after.streams or [] if row.get("codec_type") == "video")
    audio = next(row for row in after.streams or [] if row.get("codec_type") == "audio")
    delta = abs(float(video["duration"]) - float(audio["duration"]))
    assert delta < 0.25
    assert not list(tmp_path.glob(".*.finalizing.mp4"))
