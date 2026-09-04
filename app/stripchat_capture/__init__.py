from __future__ import annotations

import importlib.util
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests


# Keep the existing native Mouflon implementation as a fallback while this
# package shadows app/stripchat_capture.py for ``python -m app.stripchat_capture``.
# This lets RTMP-published rooms use Stripchat's low-overhead Flashphoner HLS
# path without duplicating the mature Mouflon parser.
_LEGACY_PATH = Path(__file__).resolve().parents[1] / "stripchat_capture.py"
_LEGACY_NAME = "app._stripchat_capture_mouflon"
_spec = importlib.util.spec_from_file_location(_LEGACY_NAME, _LEGACY_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - installation guard
    raise RuntimeError("Cannot load Stripchat Mouflon recorder")
_legacy = importlib.util.module_from_spec(_spec)
sys.modules[_LEGACY_NAME] = _legacy
_spec.loader.exec_module(_legacy)

# Re-export the tested parser helpers so imports keep the same public surface.
MasterSelection = _legacy.MasterSelection
MediaSegment = _legacy.MediaSegment
MediaPlaylist = _legacy.MediaPlaylist
decode_v1_name = _legacy.decode_v1_name
decode_v2_url = _legacy.decode_v2_url
parse_media_playlist = _legacy.parse_media_playlist
get_cam_state = _legacy.get_cam_state
_public_stream_id = _legacy._public_stream_id
resolve_user_id = _legacy.resolve_user_id

USER_AGENT = _legacy.USER_AGENT
STRIPCHAT_ROOT = _legacy.STRIPCHAT_ROOT
STOP_REQUESTED = False
CDN_TLDS = ("doppiocdn.com", "doppiocdn.org", "doppiocdn.live", "doppiocdn.net")


def __getattr__(name: str) -> Any:
    """Preserve compatibility with helpers that still live in the Mouflon module."""
    return getattr(_legacy, name)


def _request_stop(signum: int, frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True
    _legacy._request_stop(signum, frame)


def _flashphoner_server(state: dict[str, Any]) -> str:
    cam = state.get("cam") if isinstance(state.get("cam"), dict) else {}
    view_servers = cam.get("viewServers") if isinstance(cam.get("viewServers"), dict) else {}
    raw = str(view_servers.get("flashphoner-hls") or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        raw = urlsplit(raw).hostname or ""
    raw = raw.split(".", 1)[0].strip()
    if raw.startswith("b-"):
        raw = raw[2:]
    return raw


def flashphoner_candidates(state: dict[str, Any], stream_id: str) -> list[str]:
    """Return the standard HLS URLs exposed by Stripchat's cam descriptor.

    Stripchat's Flashphoner HLS path is distinct from edge-hls/Mouflon and is
    especially important for RTMP-published rooms where the edge-hls master can
    legitimately return 404 even while the room is public.
    """
    server = _flashphoner_server(state)
    if not server:
        return []
    stream_id = str(stream_id).strip()
    if not stream_id:
        return []
    masters = [
        f"https://b-{server}.{tld}/hls/{stream_id}/master_{stream_id}.m3u8"
        for tld in CDN_TLDS
    ]
    media = [
        f"https://b-{server}.{tld}/hls/{stream_id}/{stream_id}.m3u8"
        for tld in CDN_TLDS
    ]
    return masters + media


def resolve_flashphoner_input(
    session: requests.Session,
    state: dict[str, Any],
    stream_id: str,
    quality: str,
    headers: dict[str, str],
) -> str | None:
    """Resolve a non-Mouflon Flashphoner HLS media playlist if available."""
    for candidate in flashphoner_candidates(state, stream_id):
        try:
            response = session.get(candidate, headers=headers, timeout=10)
            if response.status_code >= 400 or "#EXTM3U" not in response.text:
                continue
            if "#EXT-X-MOUFLON" in response.text:
                continue
            if "#EXT-X-STREAM-INF:" in response.text:
                media_url = _legacy._select_variant(candidate, response.text, quality)
                child = session.get(media_url, headers=headers, timeout=10)
                if child.status_code >= 400 or "#EXTM3U" not in child.text:
                    continue
                if "#EXT-X-MOUFLON" in child.text:
                    continue
                return media_url
            return candidate
        except Exception:
            continue
    return None


def build_flashphoner_ffmpeg_command(
    media_url: str,
    output_pattern: str,
    *,
    segment_seconds: int,
    max_bytes: int,
    container: str,
    headers: dict[str, str],
) -> list[str]:
    """Build one FFmpeg stream-copy process; no video/audio encoder is involved."""
    header_blob = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-nostdin",
        "-y",
        "-fflags", "+genpts+discardcorrupt",
        "-dts_delta_threshold", "1",
        "-thread_queue_size", "8192",
        "-rw_timeout", "15000000",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
    ]
    if header_blob:
        command += ["-headers", header_blob]
    command += [
        "-i", media_url,
        "-map", "0:v:0",
        "-map", "0:a:0",
        "-dn",
        "-ignore_unknown",
        "-c", "copy",
        "-max_interleave_delta", "1000000",
        "-avoid_negative_ts", "make_zero",
        "-f", "segment",
        "-segment_time", str(max(60, int(segment_seconds))),
        "-reset_timestamps", "1",
        "-segment_start_number", "1",
        "-fs", str(max(16 * 1024**2, int(max_bytes))),
    ]
    if container == "mp4":
        command += [
            "-segment_format", "mp4",
            "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof",
        ]
    else:
        command += ["-segment_format", "matroska"]
    command.append(output_pattern)
    return command


def _run_flashphoner_ffmpeg(args: Any, media_url: str, headers: dict[str, str]) -> None:
    command = build_flashphoner_ffmpeg_command(
        media_url,
        args.output_pattern,
        segment_seconds=args.segment_seconds,
        max_bytes=args.max_bytes,
        container=args.container,
        headers=headers,
    )
    print(f"Stripchat Flashphoner HLS: {media_url}", file=sys.stderr, flush=True)
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        # Inherit stderr so LiveVault's recorder log captures FFmpeg diagnostics
        # without a pipe that can fill and deadlock during a long recording.
        stderr=None,
    )
    try:
        while process.poll() is None:
            if STOP_REQUESTED:
                try:
                    process.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
                break
            time.sleep(0.25)
        try:
            return_code = process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                return_code = process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait(timeout=4)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    if not STOP_REQUESTED and return_code != 0:
        raise RuntimeError(f"Stripchat Flashphoner FFmpeg exited with code {return_code}")


def capture(args: Any) -> None:
    session = requests.Session()
    headers = _legacy._headers(args.slug)
    user_id, state = _legacy.get_cam_state(session, args.slug)
    stream_id = _legacy._public_stream_id(state, user_id)

    media_url = resolve_flashphoner_input(
        session,
        state,
        stream_id,
        args.quality,
        headers,
    )
    if media_url:
        _run_flashphoner_ffmpeg(args, media_url, headers)
        return

    print(
        "Stripchat Flashphoner HLS unavailable; falling back to edge-hls/Mouflon",
        file=sys.stderr,
        flush=True,
    )
    _legacy.capture(args)


def main() -> int:
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _request_stop)
    args = _legacy._parser().parse_args()
    args.segment_seconds = max(60, int(args.segment_seconds))
    args.max_bytes = max(16 * 1024**2, int(args.max_bytes))
    try:
        capture(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(
            f"Stripchat capture failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    return 0


__all__ = [
    "MasterSelection",
    "MediaSegment",
    "MediaPlaylist",
    "decode_v1_name",
    "decode_v2_url",
    "parse_media_playlist",
    "get_cam_state",
    "resolve_user_id",
    "flashphoner_candidates",
    "resolve_flashphoner_input",
    "build_flashphoner_ffmpeg_command",
    "capture",
    "main",
]
