import asyncio
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.recorder import finalize_mp4_for_streaming, mp4_is_streaming_ready
from app.utils import generate_thumbnail, sha256_file, verify_media


def test_thumbnail_storyboard_uses_nine_fast_seeks(tmp_path: Path, monkeypatch):
    media = tmp_path / "large.mp4"
    media.write_bytes(b"placeholder")
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"old")
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"new-storyboard")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.utils.subprocess.run", fake_run)
    assert generate_thumbnail(media, thumb, 100.0)

    command = calls[0]
    assert command.count("-ss") == 9
    assert command.count("-i") == 9
    assert "hstack=inputs=3" in command[command.index("-filter_complex") + 1]
    assert "vstack=inputs=3" in command[command.index("-filter_complex") + 1]
    assert thumb.read_bytes() == b"new-storyboard"
    assert not list(tmp_path.glob(".thumb-*"))


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
    dimensions = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(thumb),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert dimensions.stdout.strip() == "960x540"


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


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg/ffprobe missing")
def test_fragmented_mp4_is_finalized_for_streaming(tmp_path: Path):
    media = tmp_path / "fragmented.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100",
            "-t", "1", "-shortest", "-c:v", "mpeg4", "-c:a", "aac",
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof", str(media),
        ],
        check=True,
        timeout=20,
    )
    assert not mp4_is_streaming_ready(media)
    assert asyncio.run(finalize_mp4_for_streaming(media)) is True
    assert mp4_is_streaming_ready(media)
    result = verify_media(media, "quick")
    assert result.ok
    assert result.duration and result.duration > 0
    assert result.has_video and result.has_audio


def test_video_timestamp_gap_is_warning_not_integrity_failure(tmp_path: Path, monkeypatch):
    media = tmp_path / "gap.mp4"
    media.write_bytes(b"placeholder")
    quick = __import__("app.utils", fromlist=["IntegrityResult"]).IntegrityResult(
        True, 60.0, "", [{"codec_type": "video", "avg_frame_rate": "30/1"}, {"codec_type": "audio"}]
    )

    monkeypatch.setattr("app.utils.probe_media", lambda *_args, **_kwargs: quick)
    monkeypatch.setattr(
        "app.utils.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr("app.utils._video_gap_error", lambda *_args, **_kwargs: "Gap video rilevato: 0.94s senza frame continui")

    result = verify_media(media, "packet")
    assert result.ok
    assert result.warning == "Gap video rilevato: 0.94s senza frame continui"
