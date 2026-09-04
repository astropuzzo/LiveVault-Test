from __future__ import annotations

import asyncio
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.recorder import STITCH_MARKER_NAME, stitch_recording_parts
from app.workers import (
    SESSION_STITCH_GAP_SECONDS,
    SESSION_STITCH_READY_SECONDS,
    capture_output_files,
    public_recording_filename,
    stitch_gap_open,
)
from app.utils import probe_media


def test_session_gap_is_exactly_twenty_minutes():
    now = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
    assert SESSION_STITCH_GAP_SECONDS == 20 * 60
    assert stitch_gap_open(now - timedelta(minutes=19, seconds=59), now)
    assert stitch_gap_open(now - timedelta(minutes=20), now)
    assert not stitch_gap_open(now - timedelta(minutes=20, seconds=1), now)


def test_long_live_session_is_flushed_every_fifteen_minutes():
    assert SESSION_STITCH_READY_SECONDS == 15 * 60


def test_consolidated_outputs_are_not_seen_as_capture_parts(tmp_path):
    (tmp_path / "creator_part000.mp4").write_bytes(b"part")
    (tmp_path / "001_creator_2026-09-03_10-00-00.mp4").write_bytes(b"complete")

    class Session:
        directory = tmp_path
        extension = ".mp4"

    assert [path.name for path in capture_output_files(Session())] == ["creator_part000.mp4"]


def test_reconnect_only_sees_files_from_its_own_capture(tmp_path):
    (tmp_path / "creator_old_part000.mp4").write_bytes(b"old")
    (tmp_path / "creator_new_part000.mp4").write_bytes(b"new")

    class Session:
        directory = tmp_path
        extension = ".mp4"
        capture_prefix = "creator_new_part"

    assert [path.name for path in capture_output_files(Session())] == ["creator_new_part000.mp4"]


def test_public_recording_filename_is_consecutive_and_chronological():
    started = datetime(2026, 9, 3, 10, 5, 7, tzinfo=timezone.utc)
    first = public_recording_filename("Creator Name", started, 1, ".mp4")
    second = public_recording_filename("Creator Name", started + timedelta(minutes=15), 2, "mp4")
    assert first == "001_Creator_Name_2026-09-03_12-05-07.mp4"
    assert second == "002_Creator_Name_2026-09-03_12-20-07.mp4"
    assert [first, second] == sorted([second, first])


def test_upload_queue_orders_equal_priority_by_capture_start():
    workers = Path("app/workers.py").read_text(encoding="utf-8")
    assert workers.count("Recording.started_at.asc(), Recording.id.asc()") >= 2
    assert "_batch{batch}_complete" not in workers
    assert "_normalize_generated_recording_filename(rec, path)" in workers


def test_stitch_marker_and_fragment_table_are_persistent():
    recorder = Path("app/recorder.py").read_text(encoding="utf-8")
    db = Path("app/db.py").read_text(encoding="utf-8")
    workers = Path("app/workers.py").read_text(encoding="utf-8")
    assert STITCH_MARKER_NAME == ".livevault-stitch-session.json"
    assert "class RecordingFragment" in db
    assert "await self._index_fragment(" in workers
    assert "await self._finalize_closed_stitch_sessions()" in workers
    assert "session_id=logical_session_id or None" in workers
    assert "cloud_day_key(session.started_at) != cloud_day_key(utcnow())" in workers
    assert "capture_id = local_now.strftime" in recorder


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg required")
def test_real_ffmpeg_stitch_removes_offline_gap(tmp_path):
    async def run():
        parts = []
        for index, frequency in enumerate((440, 660)):
            path = tmp_path / f"part{index}.mp4"
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=320x180:r=25:d=1.2",
                "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=1.2",
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(path),
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            parts.append(path)
        # The second public interval could have happened 19 minutes later in wall-clock
        # time; stitching intentionally joins media end-to-start and does not encode that gap.
        output = tmp_path / "complete.mp4"
        await stitch_recording_parts(parts, output)
        media = probe_media(output, require_audio=True)
        assert media.ok, media.error
        assert media.duration is not None
        assert 2.0 <= media.duration <= 3.0

    asyncio.run(run())
