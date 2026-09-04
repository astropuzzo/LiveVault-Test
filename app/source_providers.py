from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests


@dataclass
class ProbeResult:
    live: bool
    status: str
    recordable: bool = True
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


@dataclass
class InputAudit:
    has_video: bool
    has_audio: bool
    error: str = ""


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    hosts: tuple[str, ...]
    input_label: str
    placeholder: str
    extractor: str
    url_template: str = ""
    username_based: bool = True
    last_broadcast: bool = False
    support_level: str = "beta"


PROVIDERS = (
    ProviderSpec(
        "chaturbate",
        "Chaturbate",
        ("chaturbate.com", "www.chaturbate.com"),
        "Username o URL Chaturbate",
        "es. rich_roxy",
        "Chaturbate",
        url_template="https://chaturbate.com/{slug}/",
        last_broadcast=True,
        support_level="stable",
    ),
    ProviderSpec(
        "stripchat",
        "Stripchat",
        ("stripchat.com", "www.stripchat.com"),
        "Username o URL Stripchat",
        "es. https://stripchat.com/nome",
        "Stripchat",
        url_template="https://stripchat.com/{slug}",
    ),
    ProviderSpec(
        "bongacams",
        "BongaCams",
        ("bongacams.com", "www.bongacams.com"),
        "Username o URL BongaCams",
        "es. https://bongacams.com/nome",
        "BongaCams",
        url_template="https://bongacams.com/{slug}",
    ),
    ProviderSpec(
        "camsoda",
        "CamSoda",
        ("camsoda.com", "www.camsoda.com"),
        "Username o URL CamSoda",
        "es. https://camsoda.com/nome",
        "Camsoda",
        url_template="https://www.camsoda.com/{slug}",
    ),
    ProviderSpec(
        "cam4",
        "CAM4",
        ("cam4.com", "www.cam4.com"),
        "Username o URL CAM4",
        "es. https://cam4.com/nome",
        "CAM4",
        url_template="https://www.cam4.com/{slug}",
    ),
    ProviderSpec(
        "twitch",
        "Twitch",
        ("twitch.tv", "www.twitch.tv"),
        "Canale o URL Twitch",
        "es. https://twitch.tv/canale",
        "twitch:stream",
        url_template="https://www.twitch.tv/{slug}",
    ),
    ProviderSpec(
        "kick",
        "Kick",
        ("kick.com", "www.kick.com"),
        "Canale o URL Kick",
        "es. https://kick.com/canale",
        "kick:live",
        url_template="https://kick.com/{slug}",
    ),
    ProviderSpec(
        "youtube",
        "YouTube Live",
        ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"),
        "URL live YouTube",
        "es. https://youtube.com/watch?v=...",
        "youtube",
        username_based=False,
    ),
)
PROVIDER_BY_ID = {provider.id: provider for provider in PROVIDERS}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$")


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


@lru_cache(maxsize=1)
def _available_extractors() -> set[str]:
    try:
        from yt_dlp.extractor import gen_extractor_classes

        return {str(getattr(extractor, "IE_NAME", "")) for extractor in gen_extractor_classes()}
    except Exception:
        return set()


def provider_catalog() -> list[dict[str, Any]]:
    available = _available_extractors()
    return [
        {
            "id": "auto",
            "label": "Rileva automaticamente",
            "input_label": "Username Chaturbate o URL della live",
            "placeholder": "Username oppure https://...",
            "last_broadcast": False,
        },
        *[
            {
                "id": item.id,
                "label": item.label,
                "input_label": item.input_label,
                "placeholder": item.placeholder,
                "last_broadcast": item.last_broadcast,
                "support_level": item.support_level,
                "extractor_available": item.extractor in available,
                "audio_verified": item.id == "chaturbate",
            }
            for item in PROVIDERS
            if item.extractor in available
        ],
    ]


def provider_label(platform: str) -> str:
    item = PROVIDER_BY_ID.get(platform)
    return item.label if item else platform


def _host_matches(hostname: str, allowed: tuple[str, ...]) -> bool:
    host = hostname.lower().rstrip(".")
    return any(host == candidate or host.endswith(f".{candidate}") for candidate in allowed)


def _public_https_url(value: str, *, allowed_hosts: tuple[str, ...] = ()) -> str:
    raw = value.strip()
    parsed = urlparse(raw)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        raise ValueError("serve un URL HTTPS pubblico senza credenziali incorporate")
    host = parsed.hostname.lower().rstrip(".")
    if not allowed_hosts or not _host_matches(host, allowed_hosts):
        raise ValueError("l'URL non appartiene a un provider abilitato")
    return parsed._replace(fragment="").geturl()


def _username_from_value(value: str, spec: ProviderSpec) -> str:
    raw = value.strip()
    if "://" in raw:
        url = _public_https_url(raw, allowed_hosts=spec.hosts)
        parts = [part for part in urlparse(url).path.split("/") if part]
        if not parts:
            raise ValueError("l'URL non contiene il nome del canale")
        raw = parts[0]
    raw = raw.strip().lstrip("@").strip("/")
    if not USERNAME_RE.fullmatch(raw):
        raise ValueError(f"nome canale {spec.label} non valido")
    return raw.lower()


def _youtube_url(value: str, spec: ProviderSpec) -> str:
    safe = _public_https_url(value, allowed_hosts=spec.hosts)
    parsed = urlparse(safe)
    host = (parsed.hostname or "").lower().rstrip(".")
    parts = [part for part in parsed.path.split("/") if part]
    video_id = ""
    if host == "youtu.be" and len(parts) == 1:
        video_id = parts[0]
    elif parsed.path.rstrip("/") == "/watch":
        video_id = str(parse_qs(parsed.query).get("v", [""])[0])
    elif len(parts) == 2 and parts[0] == "live":
        video_id = parts[1]
    if video_id and re.fullmatch(r"[A-Za-z0-9_-]{6,100}", video_id):
        return f"https://www.youtube.com/watch?v={video_id}"
    if len(parts) == 2 and parts[0].startswith("@") and parts[1] == "live":
        handle = parts[0][1:]
        if USERNAME_RE.fullmatch(handle):
            return f"https://www.youtube.com/@{handle}/live"
    raise ValueError("usa il link di una live YouTube o il link /@canale/live")


def detect_provider(value: str) -> str:
    raw = value.strip()
    if "://" not in raw:
        return "chaturbate"
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().rstrip(".")
    for item in PROVIDERS:
        if item.hosts and _host_matches(host, item.hosts):
            return item.id
    raise ValueError("provider non riconosciuto: scegli un servizio abilitato")


def normalize_source(platform: str, value: str) -> tuple[str, str]:
    selected = (platform or "auto").strip().lower()
    if selected == "auto":
        selected = detect_provider(value)
    spec = PROVIDER_BY_ID.get(selected)
    if spec is None:
        raise ValueError("provider non supportato")
    if spec.extractor not in _available_extractors():
        raise ValueError(f"adapter {spec.label} non disponibile in questa build")
    if spec.username_based:
        return selected, _username_from_value(value, spec)
    if selected == "youtube":
        return selected, _youtube_url(value, spec)
    return selected, _public_https_url(value, allowed_hosts=spec.hosts)


def source_url(platform: str, slug: str) -> str:
    value = slug.strip()
    spec = PROVIDER_BY_ID.get(platform)
    if spec is None:
        raise ValueError(f"Unsupported platform: {platform}")
    if not spec.username_based:
        return value
    if not spec.url_template:
        raise ValueError(f"Provider URL builder missing: {platform}")
    return spec.url_template.format(slug=value.strip("/"))


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


def _stripchat_snapshot(slug: str) -> dict[str, Any]:
    """Read Stripchat's current room state without trusting stale show history.

    yt-dlp currently treats any ``viewCam.show`` object as an active private
    show. Stripchat retains the last ended show in that field, so an offline or
    public room can be misclassified. The model flags are the authoritative
    current state.
    """
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.stripchat import StripchatIE
    from yt_dlp.utils import lowercase_escape

    username = slug.strip("/")
    url = source_url("stripchat", username)
    with YoutubeDL({"quiet": True, "no_warnings": True, "socket_timeout": 20}) as ydl:
        extractor = StripchatIE(ydl)
        webpage = extractor._download_webpage(url, username)
        data = extractor._search_json(
            r"<script\b[^>]*>\s*window\.__PRELOADED_STATE__\s*=",
            webpage,
            "data",
            username,
            transform_source=lowercase_escape,
        )
    if not isinstance(data, dict):
        raise RuntimeError("Stripchat room state is not available")
    return data


def _stripchat_room_state(data: dict[str, Any]) -> tuple[bool, bool, str, int | None]:
    view = data.get("viewCam") if isinstance(data.get("viewCam"), dict) else {}
    model = view.get("model") if isinstance(view.get("model"), dict) else {}
    live = model.get("isLive") is True and model.get("isOnline") is not False
    status = str(model.get("status") or "").strip().lower()
    show = view.get("show") if isinstance(view.get("show"), dict) else {}
    active_show = bool(show and not show.get("endedAt") and show.get("isDeleted") is not True)
    private = live and (active_show or status in {"private", "p2p", "group", "ticket"})
    try:
        model_id = int(model.get("id"))
    except (TypeError, ValueError):
        model_id = None
    return live, private, status, model_id


def _stripchat_hls_hosts(data: dict[str, Any]) -> list[str]:
    hosts: list[str] = []

    def add(value: object) -> None:
        if isinstance(value, str):
            host = value.strip().lower().rstrip(".")
            if re.fullmatch(r"[a-z0-9.-]+", host) and "." in host and host not in hosts:
                hosts.append(host)
        elif isinstance(value, list):
            for item in value:
                add(item)

    config_v3 = data.get("configV3") if isinstance(data.get("configV3"), dict) else {}
    initial = config_v3.get("initialCommon") if isinstance(config_v3.get("initialCommon"), dict) else {}
    add(initial.get("hlsStreamHost"))
    static = config_v3.get("static") if isinstance(config_v3.get("static"), dict) else {}
    settings = static.get("featureSettings") if isinstance(static.get("featureSettings"), dict) else {}
    fallback = settings.get("hlsFallback") if isinstance(settings.get("hlsFallback"), dict) else {}
    add(fallback.get("fallbackDomains"))
    return hosts


def _stripchat_master(data: dict[str, Any], slug: str, quality: str) -> ResolvedInput:
    live, private, _status, model_id = _stripchat_room_state(data)
    if not live:
        raise RuntimeError("Stripchat room is offline")
    if private:
        raise RuntimeError("Stripchat room is private")
    if not model_id:
        raise RuntimeError("Stripchat model id is missing")

    headers = {
        "User-Agent": CHATURBATE_HEADERS["User-Agent"],
        "Referer": source_url("stripchat", slug),
        "Origin": "https://stripchat.com",
    }
    response = None
    master_url = ""
    for host in _stripchat_hls_hosts(data):
        candidate = f"https://edge-hls.{host}/hls/{model_id}/master/{model_id}_auto.m3u8"
        try:
            current = _browser_get(candidate, headers=headers, timeout=15)
            if current.status_code < 400 and "#EXTM3U" in current.text:
                response, master_url = current, candidate
                break
        except Exception:
            continue
    if response is None:
        raise RuntimeError("Stripchat HLS manifest is unavailable")

    limit = {"1080p": 1080, "720p": 720, "480p": 480}.get(quality)
    variants: list[tuple[int, int, str]] = []
    lines = [line.strip() for line in response.text.splitlines() if line.strip()]
    for index, line in enumerate(lines[:-1]):
        if not line.startswith("#EXT-X-STREAM-INF:") or lines[index + 1].startswith("#"):
            continue
        height_match = re.search(r"RESOLUTION=\d+x(\d+)", line, flags=re.IGNORECASE)
        bandwidth_match = re.search(r"BANDWIDTH=(\d+)", line, flags=re.IGNORECASE)
        height = int(height_match.group(1)) if height_match else 0
        bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
        if limit is None or not height or height <= limit:
            variants.append((height, bandwidth, urljoin(master_url, lines[index + 1])))
    media_url = max(variants, default=(0, 0, master_url), key=lambda item: (item[0], item[1]))[2]
    parsed = urlparse(media_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError("Stripchat returned an unsafe media URL")
    return ResolvedInput(media_url, headers, "media")


async def _probe_stripchat(slug: str, quality: str) -> ProbeResult:
    try:
        data = await asyncio.to_thread(_stripchat_snapshot, slug)
        live, private, _status, _model_id = _stripchat_room_state(data)
        if not live:
            return ProbeResult(False, "offline", metadata_status="unsupported")
        if private:
            return ProbeResult(True, "private", recordable=False, metadata_status="unsupported")
        await asyncio.to_thread(_stripchat_master, data, slug, quality)
        return ProbeResult(True, "live", recordable=True, title=slug, metadata_status="unsupported")
    except Exception as exc:
        return ProbeResult(False, "error", error=str(exc)[-700:], metadata_status="unsupported")


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


def _yt_dlp_live_state(info: dict[str, Any]) -> tuple[bool, str]:
    live_status = str(info.get("live_status") or "").strip().lower()
    if bool(info.get("is_live")) or live_status == "is_live":
        return True, "live"
    if live_status in {"is_upcoming", "post_live", "was_live", "not_live"}:
        return False, live_status
    return False, "offline"


async def _probe_ytdlp(platform: str, slug: str, quality: str) -> ProbeResult:
    if platform == "stripchat":
        return await _probe_stripchat(slug, quality)
    url = source_url(platform, slug)
    try:
        info = await asyncio.to_thread(_extract, url, quality)
    except Exception as exc:
        message = str(exc)
        lowered = message.lower()
        expected_offline = (
            "offline",
            "not currently broadcasting",
            "not broadcasting",
            "no live streams",
            "channel is not live",
            "livestream is offline",
            "room is not available",
        )
        if any(token in lowered for token in expected_offline):
            return ProbeResult(False, "offline", metadata_status="unsupported")
        return ProbeResult(
            False,
            "error",
            error=message[-700:],
            metadata_status="unsupported",
        )
    live, status = _yt_dlp_live_state(info)
    return ProbeResult(
        live=live,
        status=status,
        title=str(info.get("title") or info.get("channel") or info.get("uploader") or ""),
        metadata_status="unsupported",
    )


async def probe(platform: str, slug: str, quality: str = "best") -> ProbeResult:
    if platform != "chaturbate":
        return await _probe_ytdlp(platform, slug, quality)
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
    if platform == "stripchat":
        data = await asyncio.to_thread(_stripchat_snapshot, slug)
        return [await asyncio.to_thread(_stripchat_master, data, slug, quality)]
    url = source_url(platform, slug)
    info = await asyncio.to_thread(_extract, url, quality, quiet=False)
    formats = info.get("requested_formats") or []
    result: list[ResolvedInput] = []
    seen: set[str] = set()
    if formats:
        for fmt in formats:
            media_url = fmt.get("url")
            parsed_media = urlparse(str(media_url or ""))
            if (
                not media_url
                or media_url in seen
                or parsed_media.scheme not in {"http", "https"}
                or not parsed_media.hostname
                or parsed_media.username
                or parsed_media.password
            ):
                continue
            seen.add(media_url)
            kind = classify_format(
                fmt.get("vcodec"),
                fmt.get("acodec"),
                format_id=str(fmt.get("format_id") or ""),
                format_label=str(fmt.get("format") or fmt.get("format_note") or ""),
            )
            result.append(ResolvedInput(media_url, dict(fmt.get("http_headers") or {}), kind))
    elif info.get("url"):
        parsed_media = urlparse(str(info["url"]))
        if (
            parsed_media.scheme not in {"http", "https"}
            or not parsed_media.hostname
            or parsed_media.username
            or parsed_media.password
        ):
            raise RuntimeError("yt-dlp returned an unsafe media URL")
        kind = classify_format(
            info.get("vcodec"),
            info.get("acodec"),
            format_id=str(info.get("format_id") or ""),
            format_label=str(info.get("format") or info.get("format_note") or ""),
        )
        result.append(ResolvedInput(str(info["url"]), dict(info.get("http_headers") or {}), kind))
    if not result:
        raise RuntimeError("No playable stream URL returned by yt-dlp")
    return result


def _ffprobe_headers(headers: dict[str, str]) -> str:
    allowed = {"user-agent", "referer", "origin", "cookie", "authorization"}
    lines = [
        f"{key}: {value}"
        for key, value in headers.items()
        if key.lower() in allowed and "\r" not in value and "\n" not in value
    ]
    return "\r\n".join(lines) + ("\r\n" if lines else "")


async def _audit_input(item: ResolvedInput, timeout: float) -> InputAudit:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-rw_timeout", str(int(timeout * 1_000_000)),
        "-analyzeduration", "7000000",
        "-probesize", "7000000",
    ]
    headers = _ffprobe_headers(item.http_headers)
    if headers:
        cmd += ["-headers", headers]
    cmd += ["-show_entries", "stream=codec_type", "-of", "json", item.url]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 2)
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        item.kind = "unknown"
        return InputAudit(False, False, "Audio Guard: analisi stream scaduta")
    if proc.returncode != 0:
        item.kind = "unknown"
        error = stderr.decode(errors="replace").strip()[-500:] or "ffprobe non ha letto lo stream"
        return InputAudit(False, False, f"Audio Guard: {error}")
    try:
        payload = json.loads(stdout.decode(errors="replace"))
        kinds = {str(stream.get("codec_type") or "") for stream in payload.get("streams", [])}
    except Exception as exc:
        item.kind = "unknown"
        return InputAudit(False, False, f"Audio Guard: risposta ffprobe non valida ({exc})")
    has_video = "video" in kinds
    has_audio = "audio" in kinds
    item.kind = "media" if has_video and has_audio else "video" if has_video else "audio" if has_audio else "unknown"
    return InputAudit(has_video, has_audio)


async def audit_inputs(inputs: list[ResolvedInput], timeout: float = 18.0) -> InputAudit:
    """Verify the actual live tracks before FFmpeg starts; resolved metadata is not trusted."""
    results = await asyncio.gather(*(_audit_input(item, timeout) for item in inputs))
    has_video = any(result.has_video for result in results)
    has_audio = any(result.has_audio for result in results)
    errors = [result.error for result in results if result.error]
    if has_video and has_audio:
        return InputAudit(True, True)
    reason = "traccia video assente" if not has_video else "traccia audio assente"
    if errors:
        reason = f"{reason}; {' | '.join(errors)}"
    return InputAudit(has_video, has_audio, reason[-1000:])
