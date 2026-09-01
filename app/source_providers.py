from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests


@dataclass
class ProbeResult:
    live: bool
    status: str
    title: str = ""
    error: str = ""
    last_broadcast: datetime | None = None


@dataclass
class ResolvedInput:
    url: str
    http_headers: dict[str, str]
    kind: str


QUALITY_FORMATS = {
    # Prefer a single muxed A/V stream. Separate video+audio remains the fallback.
    "best": "b/bv*+ba",
    "1080p": "b[height<=1080]/bv*[height<=1080]+ba/b/bv*+ba",
    "720p": "b[height<=720]/bv*[height<=720]+ba/b/bv*+ba",
    "480p": "b[height<=480]/bv*[height<=480]+ba/b/bv*+ba",
}

CHATURBATE_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://chaturbate.com",
    "Referer": "https://chaturbate.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
}


class _QuietLogger:
    """Keep expected offline probes out of the container error log."""

    def debug(self, _message: str) -> None:
        pass

    def warning(self, _message: str) -> None:
        pass

    def error(self, _message: str) -> None:
        pass


def classify_format(
    vcodec: str | None,
    acodec: str | None,
    *,
    format_id: str = "",
    format_label: str = "",
) -> str:
    has_video = bool(vcodec and vcodec != "none")
    has_audio = bool(acodec and acodec != "none")
    # Some HLS manifests expose an audio rendition but leave acodec unset.
    # yt-dlp still labels these entries as audio-only in the format metadata.
    audio_hint = "audio" in f"{format_id} {format_label}".lower()
    if not has_video and not has_audio and vcodec == "none" and audio_hint:
        has_audio = True
    if has_video and has_audio:
        return "media"
    if has_video:
        return "video"
    if has_audio:
        return "audio"
    return "unknown"


def source_url(platform: str, slug: str) -> str:
    if platform == "chaturbate":
        return f"https://chaturbate.com/{slug.strip('/')}/"
    raise ValueError(f"Unsupported platform: {platform}")


def _extract(url: str, quality: str, *, quiet: bool = True) -> dict[str, Any]:
    import yt_dlp
    params = {
        "quiet": quiet,
        "no_warnings": quiet,
        "skip_download": True,
        "format": QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"]),
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
        "logger": _QuietLogger(),
    }
    with yt_dlp.YoutubeDL(params) as ydl:
        return ydl.extract_info(url, download=False)


def _parse_last_broadcast(value: Any) -> datetime | None:
    """Parse Chaturbate biocontext.last_broadcast as an aware UTC datetime."""
    if value is None or value == -1:
        return None
    text = str(value).strip()
    if not text or text == "-1":
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fetch_biocontext(slug: str) -> dict[str, Any]:
    """Fetch public Chaturbate profile metadata, including last_broadcast."""
    username = slug.strip("/")
    url = f"https://chaturbate.com/api/biocontext/{username}/"
    response = requests.get(url, headers=CHATURBATE_HEADERS, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Chaturbate biocontext response is not an object")
    return payload


async def probe(platform: str, slug: str, quality: str = "best") -> ProbeResult:
    url = source_url(platform, slug)

    # Fetch stream metadata and profile metadata concurrently. biocontext is important
    # even while the room is offline because it exposes Chaturbate's own
    # `last_broadcast` timestamp, which is different from "last seen by LiveVault".
    context_task = asyncio.to_thread(_fetch_biocontext, slug) if platform == "chaturbate" else None
    extract_task = asyncio.to_thread(_extract, url, quality)
    if context_task is not None:
        extracted, context = await asyncio.gather(extract_task, context_task, return_exceptions=True)
    else:
        extracted = await asyncio.gather(extract_task, return_exceptions=True)
        extracted = extracted[0]
        context = {}

    context_data = context if isinstance(context, dict) else {}
    last_broadcast = _parse_last_broadcast(context_data.get("last_broadcast"))
    room_status = str(context_data.get("room_status") or "").strip().lower()

    if not isinstance(extracted, Exception):
        info = extracted
        live_status = str(info.get("live_status") or "")
        is_live = bool(info.get("is_live")) or live_status == "is_live" or room_status == "public"
        status = "live" if is_live else ("offline" if room_status else (live_status or "offline"))
        return ProbeResult(
            live=is_live,
            status=status,
            title=str(info.get("title") or context_data.get("room_title") or ""),
            last_broadcast=last_broadcast,
        )

    # yt-dlp may fail normally for an offline/private room. If biocontext succeeded,
    # retain the platform last_broadcast timestamp instead of losing it with the error.
    text = str(extracted)
    lowered = text.lower()
    if room_status:
        if room_status == "public":
            return ProbeResult(
                live=True,
                status="live",
                title=str(context_data.get("room_title") or ""),
                error=text[-500:],
                last_broadcast=last_broadcast,
            )
        return ProbeResult(live=False, status="offline", last_broadcast=last_broadcast)
    if any(k in lowered for k in ("offline", "not currently broadcasting", "private show", "room is not available", "not broadcasting")):
        return ProbeResult(live=False, status="offline", error=text[-500:], last_broadcast=last_broadcast)
    return ProbeResult(live=False, status="error", error=text[-500:], last_broadcast=last_broadcast)


async def resolve_inputs(platform: str, slug: str, quality: str = "best") -> list[ResolvedInput]:
    url = source_url(platform, slug)
    info = await asyncio.to_thread(_extract, url, quality, quiet=False)
    formats = info.get("requested_formats") or []
    result: list[ResolvedInput] = []
    seen: set[str] = set()
    if formats:
        for fmt in formats:
            media_url = fmt.get("url")
            if not media_url or media_url in seen:
                continue
            seen.add(media_url)
            kind = classify_format(
                fmt.get("vcodec"),
                fmt.get("acodec"),
                format_id=str(fmt.get("format_id") or ""),
                format_label=str(fmt.get("format") or fmt.get("format_note") or ""),
            )
            if kind == "unknown":
                continue
            result.append(ResolvedInput(media_url, dict(fmt.get("http_headers") or {}), kind))
    elif info.get("url"):
        result.append(ResolvedInput(str(info["url"]), dict(info.get("http_headers") or {}), "media"))
    if not result:
        raise RuntimeError("No playable stream URL returned by yt-dlp")
    return result
