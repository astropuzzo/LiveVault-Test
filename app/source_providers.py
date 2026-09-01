from __future__ import annotations

import asyncio
import html as html_lib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests


@dataclass
class ProbeResult:
    live: bool
    status: str
    title: str = ""
    error: str = ""
    last_broadcast: datetime | None = None
    metadata_status: str = "unknown"
    metadata_error: str = ""


@dataclass
class ResolvedInput:
    url: str
    http_headers: dict[str, str]
    kind: str


QUALITY_FORMATS = {
    "best": "b/bv*+ba",
    "1080p": "b[height<=1080]/bv*[height<=1080]+ba/b/bv*+ba",
    "720p": "b[height<=720]/bv*[height<=720]+ba/b/bv*+ba",
    "480p": "b[height<=480]/bv*[height<=480]+ba/b/bv*+ba",
}

CHATURBATE_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://chaturbate.com",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
}
try:
    CHATURBATE_PACIFIC = ZoneInfo("America/Los_Angeles")
except ZoneInfoNotFoundError:  # pragma: no cover - tzdata is installed in production
    CHATURBATE_PACIFIC = timezone(timedelta(hours=-8), name="PST")


class ChaturbateMetadataError(RuntimeError):
    def __init__(self, status_code: int, code: str, detail: str) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(f"HTTP {status_code} {code}: {detail}".strip())


class _QuietLogger:
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
    """Parse CB last_broadcast to UTC. Naive values are Pacific time."""
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
        parsed = parsed.replace(tzinfo=CHATURBATE_PACIFIC)
    return parsed.astimezone(timezone.utc)


def _parse_relative_broadcast_age(value: str, *, now: datetime | None = None) -> datetime | None:
    """Convert public-profile strings such as '20 hours ago' to UTC."""
    text = html_lib.unescape(str(value or "")).strip().lower()
    if not text:
        return None
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if text in {"now", "just now", "a few seconds ago"}:
        return now_utc
    if text == "yesterday":
        return now_utc - timedelta(days=1)

    match = re.search(
        r"\b(?P<count>\d+|a|an|one)\s*(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?)\s+ago\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    raw_count = match.group("count").lower()
    count = 1 if raw_count in {"a", "an", "one"} else int(raw_count)
    unit = match.group("unit").lower()
    if unit.startswith(("second", "sec")):
        delta = timedelta(seconds=count)
    elif unit.startswith(("minute", "min")):
        delta = timedelta(minutes=count)
    elif unit.startswith(("hour", "hr")):
        delta = timedelta(hours=count)
    elif unit.startswith("day"):
        delta = timedelta(days=count)
    elif unit.startswith("week"):
        delta = timedelta(weeks=count)
    elif unit.startswith("month"):
        delta = timedelta(days=30 * count)
    else:
        delta = timedelta(days=365 * count)
    return now_utc - delta


def _extract_last_broadcast_from_profile_html(
    body: str,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Extract exact or relative Last Broadcast data from the public room page."""
    raw = html_lib.unescape(body or "")

    # Prefer an exact ISO timestamp if Chaturbate embeds profile JSON in the page.
    for pattern in (
        r'"last_broadcast"\s*:\s*"([^"\\]+)"',
        r"'last_broadcast'\s*:\s*'([^'\\]+)'",
    ):
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            parsed = _parse_last_broadcast(match.group(1))
            if parsed is not None:
                return parsed

    # Some page variants expose only the human relative string in embedded JSON.
    for pattern in (
        r'"time_since_last_broadcast"\s*:\s*"([^"\\]+)"',
        r"'time_since_last_broadcast'\s*:\s*'([^'\\]+)'",
    ):
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            parsed = _parse_relative_broadcast_age(match.group(1), now=now)
            if parsed is not None:
                return parsed

    # Finally parse the visible Bio table: "Last Broadcast: 20 hours ago".
    plain = re.sub(r"<[^>]+>", " ", raw)
    plain = re.sub(r"\s+", " ", plain).strip()
    match = re.search(
        r"Last\s+Broadcast\s*:\s*(.{1,120}?)(?=\s+(?:Languages|Body Type|Smoke\s*/\s*Drink|Body Decorations|About Me|Wish List|I am|Interested In|Location|Real Name|Birth Date|Age)\s*:|$)",
        plain,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    value = match.group(1).strip()
    parsed = _parse_last_broadcast(value)
    if parsed is not None:
        return parsed
    parsed = _parse_relative_broadcast_age(value, now=now)
    if parsed is not None:
        return parsed
    raise ValueError(f"Chaturbate public Last Broadcast non parsabile: {value!r}")


def _browser_get(url: str, headers: dict[str, str], timeout: float = 15.0):
    try:
        from curl_cffi import requests as curl_requests
    except Exception:
        return requests.get(url, headers=headers, timeout=timeout)
    return curl_requests.get(url, headers=headers, timeout=timeout, impersonate="chrome")


def _fetch_biocontext(slug: str) -> dict[str, Any]:
    username = slug.strip("/")
    url = f"https://chaturbate.com/api/biocontext/{username}/"
    headers = dict(CHATURBATE_HEADERS)
    headers.update({
        "Referer": f"https://chaturbate.com/p/{username}/",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": "cb_legacy=1; agreeterms=1",
    })
    response = _browser_get(url, headers=headers, timeout=15)
    if response.status_code >= 400:
        try:
            error_payload = response.json()
        except Exception:
            error_payload = {}
        raise ChaturbateMetadataError(
            response.status_code,
            str(error_payload.get("code") or "metadata-error"),
            str(error_payload.get("detail") or "metadati non disponibili"),
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Chaturbate biocontext response is not an object")
    return payload


def _fetch_profile_last_broadcast(slug: str) -> datetime | None:
    """Fallback for rooms whose biocontext endpoint is 401/403 gated."""
    username = slug.strip("/")
    url = f"https://chaturbate.com/{username}/"
    headers = dict(CHATURBATE_HEADERS)
    headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://chaturbate.com/",
        "Cookie": "cb_legacy=1; agreeterms=1",
    })
    response = _browser_get(url, headers=headers, timeout=15)
    response.raise_for_status()
    parsed = _extract_last_broadcast_from_profile_html(response.text)
    if parsed is None:
        raise ValueError("la pagina pubblica non contiene il dato Last Broadcast")
    return parsed


def _fetch_online(slug: str) -> bool:
    """Read the lightweight public online flag, including for restricted rooms."""
    username = slug.strip("/")
    url = f"https://chaturbate.com/api/online/{username}/"
    headers = dict(CHATURBATE_HEADERS)
    headers.update({
        "Referer": "https://chaturbate.com/",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": "cb_legacy=1; agreeterms=1",
    })
    response = _browser_get(url, headers=headers, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("online"), bool):
        raise ValueError("Chaturbate online response is not valid")
    return payload["online"]


def _metadata_state(
    context: object,
    context_data: dict[str, Any],
    last_broadcast: datetime | None,
    *,
    profile_fallback_ok: bool = False,
    profile_error: str = "",
) -> tuple[str, str]:
    if last_broadcast is not None:
        return "available", ""
    raw = context_data.get("last_broadcast")
    if raw in (-1, "-1"):
        return "never", ""
    if raw not in (None, "", -1, "-1"):
        return "unavailable", f"Chaturbate last_broadcast non parsabile: {raw!r}"[-700:]
    if isinstance(context, ChaturbateMetadataError) and context.code == "access-denied":
        return "restricted", "Chaturbate limita i dettagli di questa camera per paese o genere del VPS"
    if isinstance(context, Exception):
        message = f"Chaturbate biocontext: {type(context).__name__}: {context}"
        if profile_error:
            message += f" | public profile: {profile_error}"
        elif profile_fallback_ok:
            message += " | public profile: dato Last Broadcast assente"
        return "unavailable", message[-1000:]
    return "unavailable", "Chaturbate non ha restituito il dato Last Broadcast"


async def probe(platform: str, slug: str, quality: str = "best") -> ProbeResult:
    url = source_url(platform, slug)
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

    profile_fallback_ok = False
    profile_error = ""
    restricted = isinstance(context, ChaturbateMetadataError) and context.code == "access-denied"
    raw_last_broadcast = context_data.get("last_broadcast")
    if (
        platform == "chaturbate"
        and last_broadcast is None
        and raw_last_broadcast not in (-1, "-1")
        and not restricted
    ):
        try:
            last_broadcast = await asyncio.to_thread(_fetch_profile_last_broadcast, slug)
            profile_fallback_ok = True
        except Exception as exc:
            profile_error = f"{type(exc).__name__}: {exc}"[-700:]

    metadata_status, metadata_error = _metadata_state(
        context,
        context_data,
        last_broadcast,
        profile_fallback_ok=profile_fallback_ok,
        profile_error=profile_error,
    )

    online: bool | None = None
    online_error = ""
    if platform == "chaturbate" and restricted:
        try:
            online = await asyncio.to_thread(_fetch_online, slug)
        except Exception as exc:
            online_error = f"{type(exc).__name__}: {exc}"[-500:]

    if not isinstance(extracted, Exception):
        info = extracted
        live_status = str(info.get("live_status") or "")
        is_live = bool(info.get("is_live")) or live_status == "is_live" or room_status == "public" or online is True
        if is_live:
            status = "live"
        else:
            status = "offline" if room_status else (live_status or "offline")
        return ProbeResult(
            live=is_live,
            status=status,
            title=str(info.get("title") or context_data.get("room_title") or ""),
            error="",
            last_broadcast=last_broadcast,
            metadata_status=metadata_status,
            metadata_error=metadata_error,
        )

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
                metadata_status=metadata_status,
                metadata_error=metadata_error,
            )
        return ProbeResult(
            live=False,
            status="offline",
            last_broadcast=last_broadcast,
            metadata_status=metadata_status,
            metadata_error=metadata_error,
        )

    if online is True:
        return ProbeResult(
            live=True,
            status="live",
            error=f"Stream rilevato online ma accesso video non riuscito: {text[-500:]}",
            last_broadcast=last_broadcast,
            metadata_status=metadata_status,
            metadata_error=metadata_error,
        )

    if online is False:
        return ProbeResult(
            live=False,
            status="offline",
            last_broadcast=last_broadcast,
            metadata_status=metadata_status,
            metadata_error=metadata_error,
        )

    if any(k in lowered for k in ("offline", "not currently broadcasting", "private show", "room is not available", "not broadcasting")):
        return ProbeResult(
            live=False,
            status="offline",
            error="",
            last_broadcast=last_broadcast,
            metadata_status=metadata_status,
            metadata_error=metadata_error,
        )
    error = text[-700:]
    if online_error:
        error = f"{error} | online check: {online_error}"[-1000:]
    return ProbeResult(
        live=False,
        status="error",
        error=error,
        last_broadcast=last_broadcast,
        metadata_status=metadata_status,
        metadata_error=metadata_error,
    )


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
