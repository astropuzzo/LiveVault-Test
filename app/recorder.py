from __future__ import annotations

import asyncio
import json
import os
import signal
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import settings
from .db import Source
from .settings_store import runtime
from .source_providers import ResolvedInput, audit_inputs, resolve_inputs
from .utils import probe_media, safe_name, utcnow

LIVE_PREVIEW_INTERVAL_SECONDS = 20
LIVE_PREVIEW_MAX_AGE_SECONDS = 90
STITCH_MARKER_NAME = ".livevault-stitch-session.json"


def live_preview_path(source_id: int) -> Path:
    return settings.data_dir / "live_previews" / f"{int(source_id)}.jpg"


@dataclass
class RecorderSession:
    source_id: int
    source_name: str
    session_id: str
    directory: Path
    process: asyncio.subprocess.Process
    started_at: datetime
    extension: str
    max_file_bytes: int
    safe_stop_bytes: int
    preview_path: Path
    manifest_path: Path | None = None
    synchronized_hls: bool = False
    transport_guard: bool = False
    rollover_requested: bool = False
    restart_requested: bool = False
    restart_reason: str = ""


def _ffmpeg_headers(headers: dict[str, str]) -> str:
    allowed = {"user-agent", "referer", "origin", "cookie", "authorization"}
    lines = []
    for key, value in headers.items():
        if key.lower() in allowed and "\n" not in value and "\r" not in value:
            lines.append(f"{key}: {value}")
    return "\r\n".join(lines) + ("\r\n" if lines else "")


def _llhls_role(item: ResolvedInput) -> str:
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
    # The child URLs are signed. Keep transport headers off the local
    # synthetic master itself: FFmpeg associates input AVOptions with the
    # top-level file: protocol and some distro builds reject HTTP-only options.
    return [ResolvedInput(str(manifest_path.resolve()), {}, "media")], manifest_path


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


def max_output_bytes(segment_max_gb: float) -> int:
    return max(1, int(float(segment_max_gb) * 1024**3))


def safe_output_limit_bytes(segment_max_gb: float) -> int:
    """Leave enough headroom for buffered packets and the container trailer."""
    maximum = max_output_bytes(segment_max_gb)
    reserve = max(64 * 1024**2, int(maximum * 0.05))
    return max(1, maximum - reserve)


def build_ffmpeg_command(
    inputs: list[ResolvedInput],
    output_pattern: Path,
    *,
    segment_minutes: int | None = None,
    segment_max_gb: float | None = None,
    container_format: str | None = None,
    preview_path: Path | None = None,
    preview_interval_seconds: int = LIVE_PREVIEW_INTERVAL_SECONDS,
    synchronized_hls: bool = False,
) -> list[str]:
    cfg = runtime()
    segment_minutes = int(segment_minutes or cfg.segment_minutes)
    segment_max_gb = float(segment_max_gb or cfg.segment_max_gb)
    container_format = (container_format or cfg.container_format).lower()
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"]
    if synchronized_hls:
        cmd += ["-copyts", "-start_at_zero"]
    for item in inputs:
        cmd += [
            "-fflags", "+genpts+discardcorrupt",
            "-dts_delta_threshold", "1",
            "-thread_queue_size", "8192",
            "-rw_timeout", "15000000",
        ]
        # HTTP AVOptions must only be attached to a top-level HTTP(S)
        # input. Our synchronized Chaturbate master is a local file containing
        # signed remote child URLs; binding -headers/-reconnect to that file can
        # make FFmpeg abort before recording starts (Option ... not found).
        is_http_input = item.url.lower().startswith(("http://", "https://"))
        if is_http_input:
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        if synchronized_hls:
            cmd += ["-protocol_whitelist", "file,http,https,tcp,tls,crypto,data"]
        if is_http_input:
            headers = _ffmpeg_headers(item.http_headers)
            if headers:
                cmd += ["-headers", headers]
        cmd += ["-i", item.url]

    video_idx = next((i for i, item in enumerate(inputs) if item.kind == "video"), None)
    if video_idx is None:
        video_idx = next((i for i, item in enumerate(inputs) if item.kind == "media"), None)
    audio_idx = next((i for i, item in enumerate(inputs) if item.kind == "audio"), None)
    if audio_idx is None:
        audio_idx = next((i for i, item in enumerate(inputs) if item.kind == "media"), None)
    if len(inputs) == 1:
        video_map = "0:v:0"
        audio_map = "0:a:0"
    else:
        video_map = f"{video_idx}:v:0" if video_idx is not None else "0:v:0"
        audio_map = f"{audio_idx}:a:0" if audio_idx is not None else "0:a:0"
    cmd += ["-map", video_map, "-map", audio_map]

    # Normal providers remain pure stream-copy.  Chaturbate split LL-HLS is
    # different: the selected A/V renditions are read by one HLS demuxer so
    # PROGRAM-DATE-TIME remains correlated.  Only audio is encoded to AAC and
    # allowed tiny async compensation; video remains untouched.
    if synchronized_hls:
        cmd += [
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-af", "aresample=async=1",
            "-max_muxing_queue_size", "4096",
        ]
    else:
        cmd += ["-c", "copy"]
    cmd += [
        "-copytb", "1",
        "-max_interleave_delta", "1000000",
        "-avoid_negative_ts", "make_zero",
        "-f", "segment",
        "-segment_time", str(max(60, segment_minutes * 60)),
        "-reset_timestamps", "1",
        "-fs", str(safe_output_limit_bytes(segment_max_gb)),
    ]
    if container_format == "mp4":
        cmd += [
            "-segment_format", "mp4",
            "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
        ]
    else:
        cmd += ["-segment_format", "matroska"]
    cmd += [str(output_pattern)]
    if preview_path is not None:
        interval = max(5, int(preview_interval_seconds))
        cmd += [
            "-map", video_map,
            "-an",
            "-vf", f"fps=1/{interval},scale=640:-2:force_original_aspect_ratio=decrease",
            "-c:v", "mjpeg",
            "-q:v", "6",
            "-f", "image2",
            "-update", "1",
            "-atomic_writing", "1",
            str(preview_path),
        ]
    return cmd


async def start_recorder(source: Source, *, session_id: str | None = None) -> RecorderSession:
    cfg = runtime()
    inputs = await resolve_inputs(source.platform, source.slug, source.quality)
    split_llhls = is_chaturbate_split_llhls(source.platform, inputs)
    if not split_llhls:
        audit = await audit_inputs(inputs)
        if not audit.has_video or not audit.has_audio:
            raise RuntimeError(f"Audio Guard ha bloccato l'avvio: {audit.error}")
    inputs = [item for item in inputs if _llhls_role(item) in {"media", "video", "audio"}]
    local_now = datetime.now(ZoneInfo(settings.timezone))
    source_name = safe_name(source.name)
    session_id = session_id or f"{source_name}_{local_now:%Y-%m-%d_%H-%M-%S}"
    directory = settings.recordings_dir / source_name / session_id
    directory.mkdir(parents=True, exist_ok=True)
    # Persist enough state for crash recovery before FFmpeg writes the first part.
    (directory / STITCH_MARKER_NAME).write_text(json.dumps({
        "source_id": int(source.id),
        "source_name": source.name,
        "session_id": session_id,
        "started_at": utcnow().isoformat(),
    }, ensure_ascii=False), encoding="utf-8")
    capture_id = local_now.strftime("%Y%m%d_%H%M%S_%f")
    manifest_path: Path | None = None
    if split_llhls:
        inputs, manifest_path = build_chaturbate_synced_master(
            inputs, directory / f".livevault-synced-master-{capture_id}.m3u8"
        )
    extension = ".mp4" if cfg.container_format == "mp4" else ".mkv"
    # A reconnect within the logical 20-minute session must never overwrite part000.
    output_pattern = directory / f"{session_id}_{capture_id}_part%03d{extension}"
    preview_path = live_preview_path(source.id)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.unlink(missing_ok=True)
    cmd = build_ffmpeg_command(
        inputs,
        output_pattern,
        segment_minutes=cfg.segment_minutes,
        segment_max_gb=cfg.segment_max_gb,
        container_format=cfg.container_format,
        preview_path=preview_path,
        preview_interval_seconds=LIVE_PREVIEW_INTERVAL_SECONDS,
        synchronized_hls=split_llhls,
    )
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    return RecorderSession(
        source_id=source.id,
        source_name=source.name,
        session_id=session_id,
        directory=directory,
        process=process,
        started_at=utcnow(),
        extension=extension,
        max_file_bytes=max_output_bytes(cfg.segment_max_gb),
        safe_stop_bytes=safe_output_limit_bytes(cfg.segment_max_gb),
        preview_path=preview_path,
        manifest_path=manifest_path,
        synchronized_hls=split_llhls,
        transport_guard=split_llhls,
    )


async def stop_recorder(session: RecorderSession) -> None:
    if session.process.returncode is not None:
        return
    try:
        os.killpg(session.process.pid, signal.SIGINT)
        await asyncio.wait_for(session.process.wait(), timeout=7)
    except Exception:
        try:
            os.killpg(session.process.pid, signal.SIGTERM)
        except Exception:
            pass
        try:
            await asyncio.wait_for(session.process.wait(), timeout=2)
        except Exception:
            try:
                os.killpg(session.process.pid, signal.SIGKILL)
            except Exception:
                pass


async def stitch_recording_parts(parts: list[Path], output: Path) -> None:
    """Join public-live capture parts back-to-back, deliberately removing offline gaps.

    Stream-copy is attempted first so normal sessions keep original video quality.  A
    full A/V transcode is only the compatibility fallback when the upstream changed
    codec parameters between reconnects.
    """
    parts = [Path(path) for path in parts if Path(path).is_file()]
    if not parts:
        raise RuntimeError("Nessun frammento valido da unire")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    concat_file = output.with_name(f".{output.stem}.concat.txt")

    def quote(path: Path) -> str:
        value = str(path.resolve()).replace("'", "'\\''")
        return f"file '{value}'"

    concat_file.write_text("\n".join(quote(path) for path in parts) + "\n", encoding="utf-8")

    async def run(args: list[str], timeout: int) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise RuntimeError("Stitching sessione scaduto") from exc
        return proc.returncode, (stderr or b"").decode(errors="replace")[-1800:]

    total_bytes = sum(path.stat().st_size for path in parts)
    timeout = max(180, min(3600, int(total_bytes / (4 * 1024**2))))
    base = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-fflags", "+genpts+discardcorrupt",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-map", "0:v:0", "-map", "0:a:0", "-dn", "-ignore_unknown",
    ]
    trailer = ["-movflags", "+faststart"] if output.suffix.lower() == ".mp4" else []
    try:
        code, detail = await run(base + ["-c", "copy", *trailer, str(output)], timeout)
        if code == 0 and output.is_file() and output.stat().st_size > 0:
            return
        output.unlink(missing_ok=True)
        code, fallback_detail = await run(base + [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-fps_mode", "vfr",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-af", "aresample=async=1",
            *trailer, str(output),
        ], max(timeout, 600))
        if code != 0 or not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError(fallback_detail or detail or "Stitching FFmpeg fallito")
    finally:
        concat_file.unlink(missing_ok=True)


def mp4_is_streaming_ready(path: Path) -> bool:
    """Return True only for a non-fragmented MP4 with the moov index up front."""
    if path.suffix.lower() != ".mp4":
        return True
    try:
        file_size = path.stat().st_size
        offset = 0
        found_moov = False
        found_mdat = False
        boxes_seen = 0
        with path.open("rb") as handle:
            while offset + 8 <= file_size and boxes_seen < 100_000:
                handle.seek(offset)
                header = handle.read(16)
                if len(header) < 8:
                    return False
                box_size = int.from_bytes(header[:4], "big")
                box_type = header[4:8]
                header_size = 8
                if box_size == 1:
                    if len(header) < 16:
                        return False
                    box_size = int.from_bytes(header[8:16], "big")
                    header_size = 16
                elif box_size == 0:
                    box_size = file_size - offset
                if box_size < header_size or offset + box_size > file_size:
                    return False
                if box_type == b"moof":
                    return False
                if box_type == b"mdat":
                    found_mdat = True
                elif box_type == b"moov":
                    if found_mdat:
                        return False
                    found_moov = True
                offset += box_size
                boxes_seen += 1
        return found_moov
    except (OSError, ValueError):
        return False


async def _copy_remux(source: Path, output: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-fflags", "+genpts+discardcorrupt", "-i", str(source),
        "-map", "0", "-dn", "-ignore_unknown", "-c", "copy",
        "-max_interleave_delta", "1000000", "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart", str(output),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    timeout = max(120, min(900, int(source.stat().st_size / (8 * 1024**2))))
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.CancelledError:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        raise
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError("Finalizzazione MP4 scaduta") from exc
    if proc.returncode != 0 or not output.exists() or output.stat().st_size <= 0:
        detail = (stderr or b"").decode(errors="replace")[-1500:]
        raise RuntimeError(detail or "FFmpeg remux failed")


def _is_av_timing_error(message: str) -> bool:
    return "a/v fuori sync" in str(message or "").lower()


def _common_av_duration(path: Path) -> float | None:
    media = probe_media(path, require_audio=True)
    durations: list[float] = []
    for stream_type in ("video", "audio"):
        stream = next(
            (row for row in (media.streams or []) if row.get("codec_type") == stream_type),
            None,
        )
        try:
            value = float(stream.get("duration")) if stream else 0.0
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            durations.append(value)
    return min(durations) if len(durations) == 2 else None


async def _rebuild_av_timeline(source: Path, output: Path) -> None:
    """Rebuild both streams on one zero-based timeline and trim the bad tail.

    This is intentionally a fallback, not the normal recording path. It costs
    CPU, but it is only used when a copy-remux proves the source timeline is
    already inconsistent. Re-encoding both streams avoids repeating the
    v2.8.5 mistake of changing only the audio clock.
    """
    common_duration = await asyncio.to_thread(_common_av_duration, source)
    video_filter = "setpts=PTS-STARTPTS"
    audio_filter = "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0"
    if common_duration is not None and common_duration > 0.25:
        limit = f"{common_duration:.6f}"
        video_filter = f"trim=start=0:duration={limit},setpts=PTS-STARTPTS"
        audio_filter = f"atrim=start=0:duration={limit},asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-fflags", "+genpts+discardcorrupt", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0", "-dn", "-ignore_unknown",
        "-vf", video_filter,
        "-af", audio_filter,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-fps_mode", "vfr",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-shortest", "-max_muxing_queue_size", "4096",
        "-movflags", "+faststart",
    ]
    if common_duration is not None and common_duration > 0.25:
        command += ["-t", f"{common_duration:.6f}"]
    command += [str(output)]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    # A repair may need a full video transcode, so allow substantially more
    # time than the copy-remux path while still bounding stuck processes.
    timeout = max(300, min(3600, int(source.stat().st_size / (1024**2)) * 3))
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.CancelledError:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        raise
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError("Riparazione A/V scaduta") from exc
    if proc.returncode != 0 or not output.exists() or output.stat().st_size <= 0:
        detail = (stderr or b"").decode(errors="replace")[-1500:]
        raise RuntimeError(detail or "Ricostruzione A/V fallita")


async def _validate_final_mp4(path: Path, source_size: int | None = None) -> None:
    if not mp4_is_streaming_ready(path):
        raise RuntimeError("Indice MP4 finale non valido")
    if source_size is not None and path.stat().st_size < max(1, int(source_size * 0.8)):
        raise RuntimeError("MP4 finale troppo piccolo rispetto all'originale")
    media = await asyncio.to_thread(probe_media, path, require_audio=True)
    if not media.ok:
        raise RuntimeError(f"MP4 finale non valido: {media.error}")


async def _finalize_with_av_fallback(source: Path, output: Path, source_size: int) -> None:
    await _copy_remux(source, output)
    try:
        await _validate_final_mp4(output, source_size)
        return
    except RuntimeError as exc:
        if not _is_av_timing_error(str(exc)):
            raise
    output.unlink(missing_ok=True)
    await _rebuild_av_timeline(source, output)
    # A deliberate transcode can legitimately be far smaller than the source;
    # media validity and timing are the acceptance criteria here.
    await _validate_final_mp4(output, None)


async def finalize_mp4_for_streaming(path: Path, *, require_space: bool = True) -> bool:
    """Atomically normalize MP4, including already-seekable files with A/V drift."""
    if path.suffix.lower() != ".mp4":
        return False
    if mp4_is_streaming_ready(path):
        existing = await asyncio.to_thread(probe_media, path, require_audio=True)
        if existing.ok or not _is_av_timing_error(existing.error):
            return False
    original = path.stat()
    free = shutil.disk_usage(path.parent).free
    if require_space and free < original.st_size + 256 * 1024 * 1024:
        raise RuntimeError("Spazio insufficiente per finalizzare l'MP4")
    tmp = path.with_name(f".{path.stem}.finalizing.mp4")
    tmp.unlink(missing_ok=True)
    try:
        await _finalize_with_av_fallback(path, tmp, original.st_size)
        os.utime(tmp, ns=(original.st_atime_ns, original.st_mtime_ns))
        tmp.replace(path)
        return True
    except asyncio.CancelledError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


async def remux_to_mp4(path: Path, *, require_space: bool = True) -> Path:
    if path.suffix.lower() == ".mp4":
        return path
    if path.suffix.lower() != ".mkv":
        raise RuntimeError(f"Unsupported container for MP4 remux: {path.suffix}")
    free = shutil.disk_usage(path.parent).free
    if require_space and free < path.stat().st_size + 256 * 1024 * 1024:
        raise RuntimeError("Spazio insufficiente per il remux MP4")
    output = path.with_suffix(".mp4")
    tmp = path.with_suffix(".tmp.mp4")
    try:
        await _finalize_with_av_fallback(path, tmp, path.stat().st_size)
        tmp.replace(output)
        path.unlink(missing_ok=True)
        return output
    except asyncio.CancelledError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
