import shutil
import subprocess
from pathlib import Path

import pytest

from app.utils import generate_thumbnail, sha256_file, verify_media


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg/ffprobe missing")
def test_integrity_and_thumbnail_pipeline(tmp_path: Path):
    media = tmp_path / "test.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100",
            "-t", "1", "-shortest", "-c:v", "mpeg4", "-c:a", "aac", str(media),
        ],
        check=True,
        timeout=20,
    )
    result = verify_media(media, "packet")
    assert result.ok
    assert result.duration and result.duration > 0
    assert len(sha256_file(media)) == 64
    thumb = tmp_path / "thumb.jpg"
    assert generate_thumbnail(media, thumb, result.duration)
    assert thumb.stat().st_size > 0


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe missing")
def test_corrupt_media_is_rejected(tmp_path: Path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert not verify_media(broken, "quick").ok


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg/ffprobe missing")
def test_video_without_audio_is_rejected(tmp_path: Path):
    media = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
            "-t", "1", "-c:v", "mpeg4", str(media),
        ],
        check=True,
        timeout=20,
    )
    result = verify_media(media, "quick")
    assert not result.ok
    assert result.has_video
    assert not result.has_audio
    assert "audio" in result.error.lower()
