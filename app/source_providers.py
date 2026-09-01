from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any



@dataclass
class ProbeResult:
    live: bool
    status: str
    title: str = ""
    error: str = ""


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


async def probe(platform: str, slug: str, quality: str = "best") -> ProbeResult:
    url = source_url(platform, slug)
    try:
        info = await asyncio.to_thread(_extract, url, quality)
        live_status = str(info.get("live_status") or "")
        is_live = bool(info.get("is_live")) or live_status == "is_live"
        return ProbeResult(live=is_live, status="live" if is_live else (live_status or "offline"), title=str(info.get("title") or ""))
    except Exception as exc:
        text = str(exc)
        lowered = text.lower()
        if any(k in lowered for k in ("offline", "not currently broadcasting", "private show", "room is not available", "not broadcasting")):
            return ProbeResult(live=False, status="offline", error=text[-500:])
        return ProbeResult(live=False, status="error", error=text[-500:])


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
