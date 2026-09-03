from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# recorder.py: Chaturbate split LL-HLS -> one synthetic master + transport guard
# ---------------------------------------------------------------------------
path = "app/recorder.py"
text = read(path)

text = replace_once(
    text,
    '''    preview_path: Path\n    rollover_requested: bool = False\n''',
    '''    preview_path: Path\n    manifest_path: Path | None = None\n    synchronized_hls: bool = False\n    transport_guard: bool = False\n    rollover_requested: bool = False\n    restart_requested: bool = False\n    restart_reason: str = ""\n''',
    "RecorderSession fields",
)

marker = '''def max_output_bytes(segment_max_gb: float) -> int:\n'''
helpers = r'''def _llhls_role(item: ResolvedInput) -> str:
    if item.kind in {"video", "audio", "media"}:
        return item.kind
    lowered = item.url.lower()
    if "_video_" in lowered or "chunklist_video" in lowered:
        return "video"
    if "_audio_" in lowered or "chunklist_audio" in lowered:
        return "audio"
    return item.kind


def is_chaturbate_split_llhls(platform: str, inputs: list[ResolvedInput]) -> bool:
    """Detect the 2026 Chaturbate LL-HLS topology without consuming playlists.

    The split child playlists can carry short-lived session state.  We must not
    ffprobe each child and then open it again for recording: the recorder gets
    the first real read of the selected rendition pair.
    """
    if platform != "chaturbate" or any(_llhls_role(item) == "media" for item in inputs):
        return False
    video = next((item for item in inputs if _llhls_role(item) == "video"), None)
    audio = next((item for item in inputs if _llhls_role(item) == "audio"), None)
    if not video or not audio:
        return False
    return all(
        item.url.lower().startswith(("http://", "https://"))
        and ".m3u8" in item.url.lower()
        and "llhls" in item.url.lower()
        for item in (video, audio)
    )


def _safe_manifest_url(value: str) -> str:
    value = str(value or "").strip()
    if not value.lower().startswith(("http://", "https://")):
        raise RuntimeError("LL-HLS URL non HTTP(S)")
    if any(char in value for char in ('\r', '\n', '"')):
        raise RuntimeError("LL-HLS URL non valida per il master locale")
    return value


def build_chaturbate_synced_master(
    inputs: list[ResolvedInput],
    manifest_path: Path,
) -> tuple[list[ResolvedInput], Path]:
    """Put the selected video/audio renditions under one HLS demuxer clock.

    Chaturbate's split LL-HLS child playlists expose PROGRAM-DATE-TIME.  A
    single master lets FFmpeg correlate them; opening them as two independent
    -i inputs loses that relationship and can mux unrelated sequence numbers.
    """
    video = next((item for item in inputs if _llhls_role(item) == "video"), None)
    audio = next((item for item in inputs if _llhls_role(item) == "audio"), None)
    if not video or not audio:
        raise RuntimeError("LL-HLS split senza coppia video/audio")
    video_url = _safe_manifest_url(video.url)
    audio_url = _safe_manifest_url(audio.url)
    headers = dict(video.http_headers)
    for key, value in audio.http_headers.items():
        headers.setdefault(key, value)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        "#EXTM3U\n"
        "#EXT-X-VERSION:6\n"
        "#EXT-X-INDEPENDENT-SEGMENTS\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="livevault_audio",NAME="LiveVault Audio",'
        f'DEFAULT=YES,AUTOSELECT=YES,FORCED=NO,URI="{audio_url}"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=20000000,AUDIO="livevault_audio"\n'
        f"{video_url}\n",
        encoding="utf-8",
    )
    return [ResolvedInput(str(manifest_path.resolve()), headers, "media")], manifest_path


def stream_transport_fault(line: str) -> str:
    """Return a reason only for faults that invalidate the current HLS capture."""
    lowered = str(line or "").lower()
    if "skipping " in lowered and " segments ahead" in lowered:
        return "segmenti video scaduti"
    if "session has been invalidated" in lowered:
        return "sessione HLS invalidata"
    if "invalid nal unit size" in lowered:
        return "segmento video corrotto"
    if "missing picture in access unit" in lowered:
        return "frame video mancante"
    return ""


'''
text = replace_once(text, marker, helpers + marker, "recorder LL-HLS helpers")

text = replace_once(
    text,
    '''    preview_path: Path | None = None,\n    preview_interval_seconds: int = LIVE_PREVIEW_INTERVAL_SECONDS,\n) -> list[str]:\n''',
    '''    preview_path: Path | None = None,\n    preview_interval_seconds: int = LIVE_PREVIEW_INTERVAL_SECONDS,\n    synchronized_hls: bool = False,\n) -> list[str]:\n''',
    "build_ffmpeg_command signature",
)

text = replace_once(
    text,
    '''    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"]\n    for item in inputs:\n        cmd += [\n            "-fflags", "+genpts+discardcorrupt",\n            "-dts_delta_threshold", "1",\n            "-thread_queue_size", "8192",\n            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",\n        ]\n''',
    '''    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"]\n    if synchronized_hls:\n        cmd += ["-copyts", "-start_at_zero"]\n    for item in inputs:\n        cmd += [\n            "-fflags", "+genpts+discardcorrupt",\n            "-dts_delta_threshold", "1",\n            "-thread_queue_size", "8192",\n            "-rw_timeout", "15000000",\n            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",\n        ]\n        if synchronized_hls:\n            cmd += ["-protocol_whitelist", "file,http,https,tcp,tls,crypto,data"]\n''',
    "FFmpeg synchronized input options",
)

old_codec = '''    # Keep the live capture on the source timeline. v2.8.5 re-encoded only\n    # audio while stream-copying video, which could create two independent\n    # clocks and turn normal source discontinuities into large A/V drift.\n    # Any genuinely broken timeline is repaired atomically after the segment\n    # closes, with both streams rebuilt together.\n    cmd += [\n        "-c", "copy",\n        "-copytb", "1",\n        "-max_interleave_delta", "1000000",\n        "-avoid_negative_ts", "make_zero",\n'''
new_codec = '''    # Normal providers remain pure stream-copy.  Chaturbate split LL-HLS is\n    # different: the selected A/V renditions are read by one HLS demuxer so\n    # PROGRAM-DATE-TIME remains correlated.  Only audio is encoded to AAC and\n    # allowed tiny async compensation; video remains untouched.\n    if synchronized_hls:\n        cmd += [\n            "-c:v", "copy",\n            "-c:a", "aac",\n            "-b:a", "192k",\n            "-ar", "48000",\n            "-af", "aresample=async=1",\n            "-max_muxing_queue_size", "4096",\n        ]\n    else:\n        cmd += ["-c", "copy"]\n    cmd += [\n        "-copytb", "1",\n        "-max_interleave_delta", "1000000",\n        "-avoid_negative_ts", "make_zero",\n'''
text = replace_once(text, old_codec, new_codec, "FFmpeg output codec strategy")

old_start = '''async def start_recorder(source: Source) -> RecorderSession:\n    cfg = runtime()\n    inputs = await resolve_inputs(source.platform, source.slug, source.quality)\n    audit = await audit_inputs(inputs)\n    if not audit.has_video or not audit.has_audio:\n        raise RuntimeError(f"Audio Guard ha bloccato l'avvio: {audit.error}")\n    inputs = [item for item in inputs if item.kind in {"media", "video", "audio"}]\n    local_now = datetime.now(ZoneInfo(settings.timezone))\n'''
new_start = '''async def start_recorder(source: Source) -> RecorderSession:\n    cfg = runtime()\n    inputs = await resolve_inputs(source.platform, source.slug, source.quality)\n    split_llhls = is_chaturbate_split_llhls(source.platform, inputs)\n    if not split_llhls:\n        audit = await audit_inputs(inputs)\n        if not audit.has_video or not audit.has_audio:\n            raise RuntimeError(f"Audio Guard ha bloccato l'avvio: {audit.error}")\n    inputs = [item for item in inputs if _llhls_role(item) in {"media", "video", "audio"}]\n    local_now = datetime.now(ZoneInfo(settings.timezone))\n'''
text = replace_once(text, old_start, new_start, "start recorder preflight")

text = replace_once(
    text,
    '''    directory = settings.recordings_dir / source_name / session_id\n    directory.mkdir(parents=True, exist_ok=True)\n    extension = ".mp4" if cfg.container_format == "mp4" else ".mkv"\n''',
    '''    directory = settings.recordings_dir / source_name / session_id\n    directory.mkdir(parents=True, exist_ok=True)\n    manifest_path: Path | None = None\n    if split_llhls:\n        inputs, manifest_path = build_chaturbate_synced_master(\n            inputs, directory / ".livevault-synced-master.m3u8"\n        )\n    extension = ".mp4" if cfg.container_format == "mp4" else ".mkv"\n''',
    "start recorder master creation",
)

text = replace_once(
    text,
    '''        preview_path=preview_path,\n        preview_interval_seconds=LIVE_PREVIEW_INTERVAL_SECONDS,\n    )\n''',
    '''        preview_path=preview_path,\n        preview_interval_seconds=LIVE_PREVIEW_INTERVAL_SECONDS,\n        synchronized_hls=split_llhls,\n    )\n''',
    "start recorder synchronized command",
)

old_return = '''    return RecorderSession(\n        source.id,\n        source.name,\n        session_id,\n        directory,\n        process,\n        utcnow(),\n        extension,\n        max_output_bytes(cfg.segment_max_gb),\n        safe_output_limit_bytes(cfg.segment_max_gb),\n        preview_path,\n    )\n'''
new_return = '''    return RecorderSession(\n        source_id=source.id,\n        source_name=source.name,\n        session_id=session_id,\n        directory=directory,\n        process=process,\n        started_at=utcnow(),\n        extension=extension,\n        max_file_bytes=max_output_bytes(cfg.segment_max_gb),\n        safe_stop_bytes=safe_output_limit_bytes(cfg.segment_max_gb),\n        preview_path=preview_path,\n        manifest_path=manifest_path,\n        synchronized_hls=split_llhls,\n        transport_guard=split_llhls,\n    )\n'''
text = replace_once(text, old_return, new_return, "RecorderSession construction")

# Deterministic tail repair for old files: trim both tracks to the common media span.
insert_before_rebuild = '''async def _rebuild_av_timeline(source: Path, output: Path) -> None:\n'''
common_duration = '''def _common_av_duration(path: Path) -> float | None:\n    media = probe_media(path, require_audio=True)\n    durations: list[float] = []\n    for stream_type in ("video", "audio"):\n        stream = next(\n            (row for row in (media.streams or []) if row.get("codec_type") == stream_type),\n            None,\n        )\n        try:\n            value = float(stream.get("duration")) if stream else 0.0\n        except (TypeError, ValueError):\n            value = 0.0\n        if value > 0:\n            durations.append(value)\n    return min(durations) if len(durations) == 2 else None\n\n\n'''
text = replace_once(text, insert_before_rebuild, common_duration + insert_before_rebuild, "common A/V duration helper")

text = replace_once(
    text,
    '''    proc = await asyncio.create_subprocess_exec(\n        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",\n        "-fflags", "+genpts+discardcorrupt", "-i", str(source),\n        "-map", "0:v:0", "-map", "0:a:0", "-dn", "-ignore_unknown",\n        "-vf", "setpts=PTS-STARTPTS",\n        "-af", "aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS",\n        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",\n        "-pix_fmt", "yuv420p", "-fps_mode", "vfr",\n        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",\n        "-shortest", "-max_muxing_queue_size", "4096",\n        "-movflags", "+faststart", str(output),\n        stdout=asyncio.subprocess.DEVNULL,\n        stderr=asyncio.subprocess.PIPE,\n    )\n''',
    '''    common_duration = await asyncio.to_thread(_common_av_duration, source)\n    video_filter = "setpts=PTS-STARTPTS"\n    audio_filter = "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0"\n    if common_duration is not None and common_duration > 0.25:\n        limit = f"{common_duration:.6f}"\n        video_filter = f"trim=start=0:duration={limit},setpts=PTS-STARTPTS"\n        audio_filter = f"atrim=start=0:duration={limit},asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0"\n    command = [\n        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",\n        "-fflags", "+genpts+discardcorrupt", "-i", str(source),\n        "-map", "0:v:0", "-map", "0:a:0", "-dn", "-ignore_unknown",\n        "-vf", video_filter,\n        "-af", audio_filter,\n        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",\n        "-pix_fmt", "yuv420p", "-fps_mode", "vfr",\n        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",\n        "-shortest", "-max_muxing_queue_size", "4096",\n        "-movflags", "+faststart",\n    ]\n    if common_duration is not None and common_duration > 0.25:\n        command += ["-t", f"{common_duration:.6f}"]\n    command += [str(output)]\n    proc = await asyncio.create_subprocess_exec(\n        *command,\n        stdout=asyncio.subprocess.DEVNULL,\n        stderr=asyncio.subprocess.PIPE,\n    )\n''',
    "deterministic A/V rebuild",
)

text = replace_once(
    text,
    '''async def finalize_mp4_for_streaming(path: Path, *, require_space: bool = True) -> bool:\n    """Atomically turn a crash-resistant fragmented MP4 into a seekable final MP4."""\n    if path.suffix.lower() != ".mp4" or mp4_is_streaming_ready(path):\n        return False\n''',
    '''async def finalize_mp4_for_streaming(path: Path, *, require_space: bool = True) -> bool:\n    """Atomically normalize MP4, including already-seekable files with A/V drift."""\n    if path.suffix.lower() != ".mp4":\n        return False\n    if mp4_is_streaming_ready(path):\n        existing = await asyncio.to_thread(probe_media, path, require_audio=True)\n        if existing.ok or not _is_av_timing_error(existing.error):\n            return False\n''',
    "repair seekable A/V drift",
)

write(path, text)


# ---------------------------------------------------------------------------
# workers.py: restart broken LL-HLS captures immediately + salvage old A/V files
# ---------------------------------------------------------------------------
path = "app/workers.py"
text = read(path)
text = replace_once(
    text,
    '''    start_recorder,\n    stop_recorder,\n)\n''',
    '''    start_recorder,\n    stop_recorder,\n    stream_transport_fault,\n)\n''',
    "worker recorder import",
)

text = replace_once(
    text,
    '''            retryable_probe = rec.upload_status == "integrity_failed" and any(\n                marker in error_text for marker in RETRYABLE_MEDIA_ERRORS\n            )\n            if mp4_is_streaming_ready(path) and not retryable_probe:\n                continue\n''',
    '''            repairable_media = rec.upload_status == "integrity_failed" and (\n                any(marker in error_text for marker in RETRYABLE_MEDIA_ERRORS)\n                or "a/v fuori sync" in error_text\n            )\n            if mp4_is_streaming_ready(path) and not repairable_media:\n                continue\n''',
    "worker repairable A/V selection",
)

text = replace_once(
    text,
    '''                if integrity.ok:\n                    self._retry_after.pop(rec.id, None)\n                elif any(marker in (integrity.error or "").lower() for marker in RETRYABLE_MEDIA_ERRORS):\n''',
    '''                if integrity.ok:\n                    self._retry_after.pop(rec.id, None)\n                    self.last_errors.pop(f"mp4-repair:{rec.id}", None)\n                elif any(marker in (integrity.error or "").lower() for marker in RETRYABLE_MEDIA_ERRORS):\n''',
    "clear stale mp4 repair error",
)

text = replace_once(
    text,
    '''                if text:\n                    tail.append(text)\n                    tail = tail[-10:]\n''',
    '''                if text:\n                    tail.append(text)\n                    tail = tail[-10:]\n                    if session.transport_guard and not session.restart_requested:\n                        reason = stream_transport_fault(text)\n                        if reason:\n                            session.restart_requested = True\n                            session.restart_reason = reason\n''',
    "stderr transport guard",
)

text = replace_once(
    text,
    '''            while session.process.returncode is None:\n                await asyncio.sleep(1)\n                files = sorted(session.directory.glob(f"*{session.extension}"), key=lambda p: p.stat().st_mtime)\n''',
    '''            while session.process.returncode is None:\n                await asyncio.sleep(1)\n                if session.restart_requested:\n                    await stop_recorder(session)\n                    continue\n                files = sorted(session.directory.glob(f"*{session.extension}"), key=lambda p: p.stat().st_mtime)\n''',
    "watcher immediate restart",
)

text = replace_once(
    text,
    '''            with contextlib.suppress(OSError):\n                session.preview_path.unlink(missing_ok=True)\n            total_session_bytes = 0\n''',
    '''            with contextlib.suppress(OSError):\n                session.preview_path.unlink(missing_ok=True)\n            if session.manifest_path is not None:\n                with contextlib.suppress(OSError):\n                    session.manifest_path.unlink(missing_ok=True)\n            total_session_bytes = 0\n''',
    "cleanup synchronized master",
)

text = replace_once(
    text,
    '''            size_rollover = session.rollover_requested or total_session_bytes >= session.safe_stop_bytes\n            with db_session() as db:\n''',
    '''            size_rollover = session.rollover_requested or total_session_bytes >= session.safe_stop_bytes\n            controlled_restart = session.restart_requested\n            with db_session() as db:\n''',
    "controlled restart state",
)

text = replace_once(
    text,
    '''                    elif not source.enabled or self._stopping or runtime().recording_paused or size_rollover:\n                        # A controlled stop (deploy/global pause/rollover) is not evidence\n''',
    '''                    elif (\n                        not source.enabled or self._stopping or runtime().recording_paused\n                        or size_rollover or controlled_restart\n                    ):\n                        # A controlled stop (deploy/global pause/rollover/HLS restart) is not evidence\n''',
    "worker controlled HLS restart status",
)

text = replace_once(
    text,
    '''                    if size_rollover:\n                        source.last_error = ""\n''',
    '''                    if size_rollover or controlled_restart:\n                        source.last_error = ""\n''',
    "clear source error after controlled restart",
)
write(path, text)


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------
write(
    "tests/test_v287_llhls_sync.py",
    r'''import asyncio
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
    assert stream_transport_fault("Invalid NAL unit size (123 > 45)")
    assert stream_transport_fault("missing picture in access unit with size 22123")
    assert stream_transport_fault("Opening next HLS segment") == ""


def test_worker_has_immediate_transport_restart_and_repair_cleanup():
    source = (Path(__file__).resolve().parents[1] / "app" / "workers.py").read_text(encoding="utf-8")
    assert "session.transport_guard and not session.restart_requested" in source
    assert "if session.restart_requested:" in source
    assert "controlled_restart = session.restart_requested" in source
    assert 'self.last_errors.pop(f"mp4-repair:{rec.id}", None)' in source
    assert 'or "a/v fuori sync" in error_text' in source
''',
)

# Existing command tests should keep normal providers in pure stream-copy mode.
path = "tests/test_v285_av_integrity.py"
text = read(path)
text += '''\n\ndef test_normal_provider_does_not_enable_chaturbate_sync_mode():\n    cmd = build_ffmpeg_command(\n        [ResolvedInput("https://example.test/live.m3u8", {}, "media")],\n        Path("out_%03d.mp4"),\n        segment_minutes=10,\n        container_format="mp4",\n    )\n    joined = " ".join(cmd)\n    assert "-copyts -start_at_zero" not in joined\n    assert "-c:a aac" not in joined\n'''
write(path, text)


# ---------------------------------------------------------------------------
# Release metadata
# ---------------------------------------------------------------------------
write("VERSION", "2.8.7\n")

path = "app/main.py"
text = read(path)
text = replace_once(text, 'VERSION = "2.8.6"', 'VERSION = "2.8.7"', "runtime version")
write(path, text)

path = "app/static/sw.js"
text = read(path)
text = replace_once(text, "livevault-shell-v2.8.6", "livevault-shell-v2.8.7", "service worker cache")
write(path, text)

path = "README.md"
text = read(path)
text = replace_once(text, "# LiveVault v2.8.6", "# LiveVault v2.8.7", "README version")
write(path, text)

path = "START_HERE.md"
text = read(path)
text = replace_once(text, "# LiveVault v2.8.6 — START HERE", "# LiveVault v2.8.7 — START HERE", "START_HERE version")
write(path, text)

path = "CHANGELOG.md"
text = read(path)
entry = '''# Changelog\n\n## 2.8.7 — Chaturbate LL-HLS sync\n\n- Chaturbate split LL-HLS: video e audio passano a FFmpeg tramite un unico master sincronizzato.\n- Nessun pre-probe dei child playlist LL-HLS prima della registrazione.\n- Restart immediato se la sessione HLS perde segmenti o produce frame corrotti.\n- Recupero MP4 A/V: trim deterministico alla durata comune e pulizia degli errori di repair risolti.\n\n'''
text = replace_once(text, "# Changelog\n\n", entry, "changelog entry")
write(path, text)

path = "tests/test_version_consistency.py"
text = read(path)
text = replace_once(text, 'assert version == "2.8.6"', 'assert version == "2.8.7"', "version test")
write(path, text)

print("v2.8.7 LL-HLS sync patch applied")
