from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
STRIPCHAT_ROOT = "https://stripchat.com"
MOUFLON_SYNC_URL = os.getenv("STRIPCHAT_MOUFLON_SYNC_URL", "https://mouflon.chantrail.com").strip()
MOUFLON_KEYS_FILE = Path(
    os.getenv("STRIPCHAT_MOUFLON_KEYS_FILE", "/data/stripchat_mouflon_keys.json")
)
HLS_EDGE_HOSTS = (
    "edge-hls.doppiocdn.com",
    "edge-hls.doppiocdn.org",
    "edge-hls.doppiocdn.net",
)
STOP_REQUESTED = False


@dataclass(frozen=True)
class MasterSelection:
    media_url: str
    psch: str
    pkey: str
    pdkey: str


@dataclass(frozen=True)
class MediaSegment:
    url: str
    identity: str
    discontinuity: bool = False


@dataclass(frozen=True)
class MediaPlaylist:
    init_url: str | None
    segments: tuple[MediaSegment, ...]
    target_duration: float
    ended: bool


def _request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def _headers(slug: str) -> dict[str, str]:
    return {
        "Accept": "*/*",
        "Referer": f"{STRIPCHAT_ROOT}/{slug}",
        "Origin": STRIPCHAT_ROOT,
        "User-Agent": USER_AGENT,
    }


def _json_get(
    session: requests.Session,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 12,
) -> dict[str, Any]:
    response = session.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Stripchat returned non-object JSON from {url}")
    return payload


def resolve_user_id(session: requests.Session, slug: str) -> int:
    """Resolve Stripchat username through the post-418 id endpoint.

    The legacy /models/username/.../cam endpoint is intentionally not used.
    """
    payload = _json_get(
        session,
        f"{STRIPCHAT_ROOT}/api/front/v2/users/username/{slug}",
        headers={"User-Agent": USER_AGENT, "Referer": f"{STRIPCHAT_ROOT}/{slug}"},
    )
    item = payload.get("item")
    try:
        return int(item["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Stripchat user id is unavailable") from exc


def get_cam_state(
    session: requests.Session,
    slug: str,
    *,
    user_id: int | None = None,
) -> tuple[int, dict[str, Any]]:
    """Fetch the id-based cam endpoint. Retry id resolution once on 404."""
    resolved = int(user_id or resolve_user_id(session, slug))
    headers = {"User-Agent": USER_AGENT, "Referer": f"{STRIPCHAT_ROOT}/{slug}"}
    for attempt in range(2):
        response = session.get(
            f"{STRIPCHAT_ROOT}/api/front/v2/models/{resolved}/cam",
            headers=headers,
            timeout=12,
        )
        if response.status_code == 404 and attempt == 0:
            resolved = resolve_user_id(session, slug)
            continue
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Stripchat cam endpoint returned invalid JSON")
        return resolved, payload
    raise RuntimeError("Stripchat cam endpoint is unavailable")


def _public_stream_id(payload: dict[str, Any], user_id: int) -> str:
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    model = user.get("user") if isinstance(user.get("user"), dict) else {}
    cam = payload.get("cam") if isinstance(payload.get("cam"), dict) else {}
    status = str(model.get("status") or "").strip().lower()
    active = cam.get("isCamActive") is True
    available = cam.get("isCamAvailable") is True
    if status != "public" or not active or not available:
        raise RuntimeError(f"Stripchat stream is not public (status={status or 'unknown'})")
    return str(cam.get("streamName") or model.get("id") or user_id)


def _load_key_file(path: Path = MOUFLON_KEYS_FILE) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("keys"), dict):
        payload = payload["keys"]
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in payload.items()
        if isinstance(key, str) and isinstance(value, str) and key and value
    }


def _save_key_file(keys: dict[str, str], path: Path = MOUFLON_KEYS_FILE) -> None:
    if not keys:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(dict(sorted(keys.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _discover_player_keys(session: requests.Session) -> dict[str, str]:
    """Extract exact pkey:pdkey pairs from Stripchat's current MMP player bundle."""
    response = session.get(
        f"{STRIPCHAT_ROOT}/api/front/v3/config/static",
        headers={"User-Agent": USER_AGENT, "Referer": f"{STRIPCHAT_ROOT}/"},
        timeout=12,
    )
    response.raise_for_status()
    payload = response.json()
    static = payload.get("static") if isinstance(payload, dict) else None
    if not isinstance(static, dict):
        return {}

    features = static.get("features") if isinstance(static.get("features"), dict) else {}
    features_v2 = static.get("featuresV2") if isinstance(static.get("featuresV2"), dict) else {}
    external = features_v2.get("playerModuleExternalLoading")
    external = external if isinstance(external, dict) else {}
    origin = str(features.get("MMPExternalSourceOrigin") or "").rstrip("/")
    version = str(external.get("mmpVersion") or "").strip()
    if not origin or not version:
        return {}

    base = f"{origin}/v{version}"
    main = session.get(f"{base}/main.js", headers={"User-Agent": USER_AGENT}, timeout=12)
    main.raise_for_status()
    main_text = main.text
    names = re.findall(r'''(?:require\(\s*["']\./)?(Doppio[^"'()\\]+?\.js)["']''', main_text)
    if not names:
        names = re.findall(r'''(Doppio[A-Za-z0-9._-]+\.js)''', main_text)
    if not names:
        return {}

    doppio = session.get(f"{base}/{names[0]}", headers={"User-Agent": USER_AGENT}, timeout=12)
    doppio.raise_for_status()
    pairs = re.findall(r"\b([A-Za-z0-9]{12,}):([A-Za-z0-9]{12,})\b", doppio.text)
    return {pkey: pdkey for pkey, pdkey in pairs}


def _sync_worker_keys(session: requests.Session) -> dict[str, str]:
    if not MOUFLON_SYNC_URL:
        return {}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    token = os.getenv("STRIPCHAT_MOUFLON_SYNC_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = session.get(MOUFLON_SYNC_URL, headers=headers, timeout=12)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("keys") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str) and key and value
    }


def _refresh_keys(session: requests.Session, keys: dict[str, str]) -> dict[str, str]:
    merged = dict(keys)
    for loader in (_discover_player_keys, _sync_worker_keys):
        try:
            discovered = loader(session)
        except Exception:
            continue
        for pkey, pdkey in discovered.items():
            merged.setdefault(pkey, pdkey)
    if merged != keys:
        try:
            _save_key_file(merged)
        except OSError:
            pass
    return merged


def _mouflon_tags(master: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for line in master.splitlines():
        match = re.match(r"#EXT-X-MOUFLON:PSCH:([^:]+):([A-Za-z0-9]+)", line.strip())
        if match:
            pair = (match.group(1), match.group(2))
            if pair not in result:
                result.append(pair)
    return result


def _append_mouflon_query(url: str, psch: str, pkey: str) -> str:
    parts = urlsplit(url)
    if "doppiocdn." not in (parts.hostname or "").lower():
        return url
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("psch", psch)
    query.setdefault("pkey", pkey)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _select_variant(master_url: str, master: str, quality: str) -> str:
    limit = {"1080p": 1080, "720p": 720, "480p": 480}.get(quality)
    variants: list[tuple[int, int, str]] = []
    lines = [line.strip() for line in master.splitlines()]
    for index, line in enumerate(lines[:-1]):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        uri = lines[index + 1]
        if not uri or uri.startswith("#"):
            continue
        height_match = re.search(r"RESOLUTION=\d+x(\d+)", line, re.IGNORECASE)
        bandwidth_match = re.search(r"BANDWIDTH=(\d+)", line, re.IGNORECASE)
        height = int(height_match.group(1)) if height_match else 0
        bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
        if limit is None or not height or height <= limit:
            variants.append((height, bandwidth, urljoin(master_url, uri)))
    if not variants:
        raise RuntimeError("Stripchat HLS master has no playable variants")
    return max(variants, key=lambda value: (value[0], value[1]))[2]


def select_master(
    session: requests.Session,
    stream_id: str,
    quality: str,
    keys: dict[str, str],
) -> tuple[MasterSelection, dict[str, str]]:
    errors: list[str] = []
    master_headers = {
        "User-Agent": USER_AGENT,
        "Referer": f"{STRIPCHAT_ROOT}/",
        "Origin": STRIPCHAT_ROOT,
    }
    for host in HLS_EDGE_HOSTS:
        master_url = f"https://{host}/hls/{stream_id}/master/{stream_id}_auto.m3u8"
        try:
            response = session.get(master_url, headers=master_headers, timeout=12)
            response.raise_for_status()
            if "#EXTM3U" not in response.text:
                raise RuntimeError("not an HLS manifest")
            tags = _mouflon_tags(response.text)
            active_keys = keys
            selected = next(
                ((psch, pkey, active_keys[pkey]) for psch, pkey in tags if pkey in active_keys),
                None,
            )
            if selected is None:
                active_keys = _refresh_keys(session, active_keys)
                selected = next(
                    ((psch, pkey, active_keys[pkey]) for psch, pkey in tags if pkey in active_keys),
                    None,
                )
            if selected is None:
                advertised = ", ".join(pkey for _psch, pkey in tags) or "none"
                raise RuntimeError(f"Mouflon key unavailable (pkey={advertised})")
            psch, pkey, pdkey = selected
            variant = _select_variant(master_url, response.text, quality)
            return MasterSelection(
                _append_mouflon_query(variant, psch, pkey),
                psch,
                pkey,
                pdkey,
            ), active_keys
        except Exception as exc:
            errors.append(f"{host}: {exc}")
    raise RuntimeError("Stripchat HLS master unavailable: " + " | ".join(errors)[-1200:])


def _xor_decode(encoded: str, key: str, *, reverse: bool = False) -> str:
    value = encoded[::-1] if reverse else encoded
    value += "=" * (-len(value) % 4)
    encrypted = base64.b64decode(value)
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    decoded = bytes(byte ^ digest[index % len(digest)] for index, byte in enumerate(encrypted))
    return decoded.decode("utf-8")


def decode_v1_name(encoded: str, pdkey: str) -> str:
    return _xor_decode(encoded.strip(), pdkey)


_V2_SEGMENT_RE = re.compile(r"_([^_/?#]+)_(\d+(?:_part\d+)?)\.mp4(?:[?#].*)?$")


def decode_v2_url(encoded_url: str, pdkey: str) -> str:
    match = _V2_SEGMENT_RE.search(encoded_url)
    if not match:
        raise RuntimeError("Cannot parse Mouflon v2 segment URL")
    encrypted = match.group(1)
    decoded = _xor_decode(encrypted, pdkey, reverse=True)
    return encoded_url[: match.start(1)] + decoded + encoded_url[match.end(1) :]


def _segment_identity(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in {"psch", "pkey"}
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _map_uri(line: str) -> str | None:
    match = re.search(r'URI="([^"]+)"', line)
    return match.group(1) if match else None


def parse_media_playlist(
    playlist_url: str,
    body: str,
    selection: MasterSelection,
) -> MediaPlaylist:
    lines = [line.strip() for line in body.splitlines()]
    init_url: str | None = None
    target_duration = 2.0
    segments: list[MediaSegment] = []
    pending_v1: str | None = None
    pending_v2: str | None = None
    discontinuity = False

    for line in lines:
        if not line:
            continue
        if line.startswith("#EXT-X-TARGETDURATION:"):
            try:
                target_duration = max(0.5, float(line.split(":", 1)[1]))
            except ValueError:
                pass
            continue
        if line.startswith("#EXT-X-MAP:"):
            uri = _map_uri(line)
            if uri:
                init_url = _append_mouflon_query(
                    urljoin(playlist_url, uri), selection.psch, selection.pkey
                )
            continue
        if line.startswith("#EXT-X-DISCONTINUITY"):
            discontinuity = True
            continue
        if line.startswith("#EXT-X-MOUFLON:FILE:"):
            if selection.psch == "v1":
                pending_v1 = decode_v1_name(line.split(":", 2)[2], selection.pdkey)
            continue
        if line.startswith("#EXT-X-MOUFLON:URI:"):
            if selection.psch == "v2":
                raw = line.split(":", 2)[2]
                if raw.startswith("//"):
                    raw = "https:" + raw
                elif not raw.startswith(("http://", "https://")):
                    raw = "https://" + raw
                pending_v2 = decode_v2_url(raw, selection.pdkey)
            continue
        if line.startswith("#"):
            continue

        uri = line
        if pending_v2:
            uri, pending_v2 = pending_v2, None
        elif pending_v1:
            if uri.endswith("media.mp4"):
                uri = uri[: -len("media.mp4")] + pending_v1
            pending_v1 = None

        absolute = _append_mouflon_query(
            urljoin(playlist_url, uri), selection.psch, selection.pkey
        )
        segments.append(MediaSegment(absolute, _segment_identity(absolute), discontinuity))
        discontinuity = False

    return MediaPlaylist(
        init_url=init_url,
        segments=tuple(segments),
        target_duration=target_duration,
        ended="#EXT-X-ENDLIST" in body,
    )


def _download_bytes(
    session: requests.Session,
    url: str,
    headers: dict[str, str],
    *,
    attempts: int = 3,
) -> bytes:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            if not response.content:
                raise RuntimeError("empty media fragment")
            return response.content
        except Exception as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(0.35 * (attempt + 1))
    raise RuntimeError(f"Stripchat fragment download failed: {error}")


def _probe_output(path: Path) -> None:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=45,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.decode(errors="replace")[-1200:] or "ffprobe failed")
    try:
        payload = json.loads(process.stdout.decode(errors="replace"))
        kinds = {str(item.get("codec_type")) for item in payload.get("streams", [])}
    except Exception as exc:
        raise RuntimeError("ffprobe returned invalid JSON") from exc
    if not {"video", "audio"} <= kinds:
        raise RuntimeError(f"Stripchat output missing tracks: {sorted(kinds)}")


def _remux(raw: Path, output: Path, container: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.finalizing{output.suffix}")
    temporary.unlink(missing_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-fflags",
        "+genpts+discardcorrupt",
        "-copyts",
        "-start_at_zero",
        "-i",
        str(raw),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-dn",
        "-ignore_unknown",
        "-c",
        "copy",
        "-max_interleave_delta",
        "1000000",
    ]
    if container == "mp4":
        command += ["-movflags", "+faststart"]
    command.append(str(temporary))
    process = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=900,
        check=False,
    )
    detail = process.stderr.decode(errors="replace")[-1600:]
    if process.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Stripchat stream-copy finalization failed: {detail}")
    _probe_output(temporary)
    os.replace(temporary, output)
    raw.unlink(missing_ok=True)


def _output_for(pattern: str, part: int) -> Path:
    return Path(pattern.replace("%03d", f"{part:03d}"))


def _point_active_preview(raw: Path, preview_base: str) -> None:
    """Expose the growing fMP4 without running a second encoder."""
    if not preview_base:
        return
    preview = Path(preview_base)
    if preview.suffix.lower() != ".mp4":
        preview = preview.with_suffix(".mp4")
    preview.parent.mkdir(parents=True, exist_ok=True)
    temporary = preview.with_name(f".{preview.name}.link")
    try:
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(raw.name)
        os.replace(temporary, preview)
    except OSError:
        temporary.unlink(missing_ok=True)


def _open_raw(output_pattern: str, part: int, preview_base: str = "") -> tuple[Path, Any]:
    output = _output_for(output_pattern, part)
    raw = output.with_suffix(".capture.mp4")
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.unlink(missing_ok=True)
    handle = raw.open("wb")
    _point_active_preview(raw, preview_base)
    return raw, handle


def capture(args: argparse.Namespace) -> None:
    session = requests.Session()
    headers = _headers(args.slug)
    user_id, state = get_cam_state(session, args.slug)
    stream_id = _public_stream_id(state, user_id)
    keys = _load_key_file()
    selection, keys = select_master(session, stream_id, args.quality, keys)

    seen_order: deque[str] = deque()
    seen: set[str] = set()
    init_cache: dict[str, bytes] = {}
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stripchat-remux")
    finalize_futures: list[Future[None]] = []
    part = 1
    raw, handle = _open_raw(args.output_pattern, part, args.video_preview_base)
    bytes_written = 0
    part_started = time.monotonic()
    current_init = ""
    last_new_segment = time.monotonic()
    last_status_check = 0.0
    refreshes = 0

    def remember(identity: str) -> None:
        seen.add(identity)
        seen_order.append(identity)
        while len(seen_order) > 4096:
            old = seen_order.popleft()
            seen.discard(old)

    def submit_current() -> None:
        nonlocal raw, handle, bytes_written, part_started, part, current_init
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        if bytes_written <= 0:
            raw.unlink(missing_ok=True)
        else:
            output = _output_for(args.output_pattern, part)
            finalize_futures.append(executor.submit(_remux, raw, output, args.container))
            part += 1
        raw, handle = _open_raw(args.output_pattern, part, args.video_preview_base)
        bytes_written = 0
        part_started = time.monotonic()
        current_init = ""

    try:
        while not STOP_REQUESTED:
            response = session.get(selection.media_url, headers=headers, timeout=12)
            if response.status_code >= 400 or "#EXTM3U" not in response.text:
                refreshes += 1
                if refreshes > 4:
                    response.raise_for_status()
                    raise RuntimeError("Stripchat media playlist is unavailable")
                time.sleep(0.5)
                user_id, state = get_cam_state(session, args.slug, user_id=user_id)
                stream_id = _public_stream_id(state, user_id)
                selection, keys = select_master(session, stream_id, args.quality, keys)
                continue
            refreshes = 0
            playlist = parse_media_playlist(selection.media_url, response.text, selection)
            new_segments = [segment for segment in playlist.segments if segment.identity not in seen]
            if new_segments and not playlist.init_url:
                raise RuntimeError("Stripchat HLS playlist is missing its fMP4 init segment")

            for segment in new_segments:
                if STOP_REQUESTED:
                    break
                if segment.discontinuity and bytes_written > 0:
                    submit_current()
                if playlist.init_url and current_init != playlist.init_url:
                    if bytes_written > 0:
                        submit_current()
                    init = init_cache.get(playlist.init_url)
                    if init is None:
                        init = _download_bytes(session, playlist.init_url, headers)
                        init_cache[playlist.init_url] = init
                    handle.write(init)
                    bytes_written += len(init)
                    current_init = playlist.init_url

                fragment = _download_bytes(session, segment.url, headers)
                handle.write(fragment)
                handle.flush()
                bytes_written += len(fragment)
                remember(segment.identity)
                last_new_segment = time.monotonic()

                if (
                    bytes_written >= args.max_bytes
                    or time.monotonic() - part_started >= args.segment_seconds
                ):
                    submit_current()

            now = time.monotonic()
            if playlist.ended:
                break
            if now - last_status_check >= 20:
                last_status_check = now
                try:
                    user_id, state = get_cam_state(session, args.slug, user_id=user_id)
                    _public_stream_id(state, user_id)
                except Exception:
                    if now - last_new_segment > max(12.0, playlist.target_duration * 4):
                        break
            if not new_segments:
                if now - last_new_segment > max(45.0, playlist.target_duration * 12):
                    raise RuntimeError("Stripchat HLS stalled: no new media fragments")
                time.sleep(min(2.0, max(0.35, playlist.target_duration / 2)))
    finally:
        try:
            handle.flush()
            if bytes_written > 0:
                os.fsync(handle.fileno())
        except OSError:
            pass
        handle.close()
        if bytes_written > 0:
            output = _output_for(args.output_pattern, part)
            finalize_futures.append(executor.submit(_remux, raw, output, args.container))
        else:
            raw.unlink(missing_ok=True)
        executor.shutdown(wait=True, cancel_futures=False)
        if args.video_preview_base:
            preview = Path(args.video_preview_base)
            if preview.suffix.lower() != ".mp4":
                preview = preview.with_suffix(".mp4")
            try:
                preview.unlink(missing_ok=True)
            except OSError:
                pass

    errors = []
    for future in finalize_futures:
        try:
            future.result()
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        raise RuntimeError(" | ".join(errors)[-2000:])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Low-overhead native Stripchat HLS recorder")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--output-pattern", required=True)
    parser.add_argument("--segment-seconds", type=int, default=3600)
    parser.add_argument("--max-bytes", type=int, default=2 * 1024**3)
    parser.add_argument("--container", choices=("mp4", "mkv"), default="mp4")
    parser.add_argument("--quality", choices=("best", "1080p", "720p", "480p"), default="best")
    # Kept for command-line compatibility with pre-2.8.21 launches. Preview generation
    # is now on-demand from the growing fMP4 capture and does not run another encoder.
    parser.add_argument("--preview", default="")
    parser.add_argument("--video-preview-base", default="")
    return parser


def main() -> int:
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _request_stop)
    args = _parser().parse_args()
    args.segment_seconds = max(60, int(args.segment_seconds))
    args.max_bytes = max(16 * 1024**2, int(args.max_bytes))
    try:
        capture(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Stripchat capture failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
