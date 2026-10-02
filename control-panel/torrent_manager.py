#!/usr/bin/env python3
from __future__ import annotations

import base64
import html
import json
import os
import re
import threading
import time
from html.parser import HTMLParser
from http.client import RemoteDisconnected
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

import media_center


RPC_URL = os.environ.get("OPENASTRO_TRANSMISSION_RPC", "http://127.0.0.1:9091/transmission/rpc")
SEARCH_BASE = os.environ.get("OPENASTRO_1337X_BASE", "https://1337x.to").rstrip("/")
SEARCH_SOLVER = os.environ.get("OPENASTRO_TORRENT_SEARCH_SOLVER", "http://127.0.0.1:9092/fetch")
STATE_ROOT = Path("/var/lib/openastro-control")
IMPORT_STATE = STATE_ROOT / "torrent-imports.json"
SHARE_ROOT = Path("/share")
STAGING_ROOT = SHARE_ROOT / ".openastro-torrents"
COMPLETE_ROOT = STAGING_ROOT / "complete"
INCOMPLETE_ROOT = STAGING_ROOT / "incomplete"
MEDIA_ROOT = SHARE_ROOT / "Media"
IMPORT_ROOT = MEDIA_ROOT / "Downloads"

TORRENT_FIELDS = [
    "id", "name", "status", "totalSize", "percentDone", "rateDownload", "rateUpload",
    "eta", "peersConnected", "peersGettingFromUs", "peersSendingToUs", "uploadedEver",
    "downloadedEver", "uploadRatio", "leftUntilDone", "downloadDir", "error",
    "errorString", "hashString", "addedDate", "doneDate", "metadataPercentComplete",
]
IMPORT_FIELDS = TORRENT_FIELDS + ["files"]
STATUS_LABELS = {
    0: "Pausa",
    1: "Verifica in coda",
    2: "Verifica",
    3: "Download in coda",
    4: "Download",
    5: "Seed in coda",
    6: "Seed",
}
_PROVIDER_LOCK = threading.Lock()
_SEARCH_CACHE_LOCK = threading.Lock()
_SEARCH_CACHE: dict[tuple, tuple[float, dict]] = {}
_SEARCH_CACHE_TTL = 300.0
_IMPORT_LOCK = threading.Lock()


class TorrentError(RuntimeError):
    pass


class _SameHostRedirect(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]):
        super().__init__()
        self.allowed_hosts = {host.casefold() for host in allowed_hosts}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = (urlparse(newurl).hostname or "").casefold()
        if host not in self.allowed_hosts:
            raise TorrentError("Redirect del provider verso host non consentito.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _SearchParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.rows: list[dict] = []
        self._row: dict | None = None
        self._td_class = ""
        self._td_text: list[str] = []
        self._torrent_link = False
        self._torrent_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self._row = {}
        elif tag == "td" and self._row is not None:
            self._td_class = str(attrs.get("class") or "")
            self._td_text = []
        elif tag == "a" and self._row is not None:
            href = str(attrs.get("href") or "")
            if href.startswith("/torrent/"):
                self._row["detail_url"] = urljoin(self.base_url + "/", href)
                self._torrent_link = True
                self._torrent_text = []

    def handle_data(self, data):
        if self._row is None:
            return
        if self._td_class:
            self._td_text.append(data)
        if self._torrent_link:
            self._torrent_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._torrent_link:
            name = " ".join("".join(self._torrent_text).split())
            if name:
                self._row["name"] = name
            self._torrent_link = False
            self._torrent_text = []
        elif tag == "td" and self._row is not None:
            value = " ".join("".join(self._td_text).split())
            classes = set(self._td_class.split())
            if "seeds" in classes or "coll-2" in classes:
                self._row["seeders"] = _first_int(value)
            elif "leeches" in classes or "coll-3" in classes:
                self._row["leechers"] = _first_int(value)
            elif "size" in classes or "coll-4" in classes:
                match = re.search(r"\d+(?:[.,]\d+)?\s*(?:B|KB|MB|GB|TB)", value, re.I)
                self._row["size"] = match.group(0).replace(",", ".") if match else value
            elif "coll-date" in classes:
                self._row["age"] = value
            elif "uploader" in classes or "coll-5" in classes:
                self._row["uploader"] = value
            self._td_class = ""
            self._td_text = []
        elif tag == "tr":
            if self._row and self._row.get("detail_url") and self._row.get("name"):
                self.rows.append(self._row)
            self._row = None


class _MagnetParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.magnet = ""

    def handle_starttag(self, tag, attrs):
        if self.magnet or tag != "a":
            return
        href = str(dict(attrs).get("href") or "")
        if href.startswith("magnet:?"):
            self.magnet = html.unescape(href)


def _first_int(value: str) -> int:
    match = re.search(r"\d[\d,\.]*", value or "")
    if not match:
        return 0
    digits = re.sub(r"\D", "", match.group(0))
    return int(digits or 0)


def _provider_hosts() -> set[str]:
    host = (urlparse(SEARCH_BASE).hostname or "").casefold()
    return {host, f"www.{host}" if host and not host.startswith("www.") else host.replace("www.", "", 1)}


def _fetch_html(url: str, timeout: int = 12) -> str:
    host = (urlparse(url).hostname or "").casefold()
    if host not in _provider_hosts():
        raise ValueError("Host provider non consentito.")
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
        },
    )
    opener = build_opener(_SameHostRedirect(_provider_hosts()))
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(2_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
        text = body.decode(charset, "replace")
        if "Just a moment" in text or "cf-chl-" in text:
            return _fetch_html_solver(url)
        return text
    except HTTPError as exc:
        if exc.code in {403, 429, 503}:
            return _fetch_html_solver(url)
        raise TorrentError(f"1337x non disponibile (HTTP {exc.code}).") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise TorrentError(f"1337x non raggiungibile: {exc}") from exc



def _solver_endpoint(url: str) -> str:
    parsed = urlparse(SEARCH_SOLVER)
    if parsed.scheme != "http" or (parsed.hostname or "").casefold() not in {"127.0.0.1", "localhost", "::1"}:
        raise TorrentError("Solver 1337x non configurato su loopback.")
    return SEARCH_SOLVER + ("&" if parsed.query else "?") + urlencode({"url": url})


def _fetch_html_solver(url: str) -> str:
    endpoint = _solver_endpoint(url)
    last_exc: Exception | None = None
    for attempt in range(4):
        request = Request(endpoint, headers={"Accept": "text/html"})
        try:
            with build_opener().open(request, timeout=90) as response:
                body = response.read(2_000_000)
                charset = response.headers.get_content_charset() or "utf-8"
            return body.decode(charset, "replace")
        except HTTPError as exc:
            detail = exc.read(512).decode("utf-8", "replace").strip()
            raise TorrentError(detail or f"1337x solver HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError, RemoteDisconnected) as exc:
            last_exc = exc
            if attempt < 3:
                time.sleep(1.0)
                continue
    raise TorrentError(f"1337x solver non disponibile: {last_exc}") from last_exc


def providers() -> list[dict]:
    return [{"id": "1337x", "name": "1337x", "default": True, "base_url": SEARCH_BASE}]


SEARCH_SORTS = {"time", "seeders", "leechers", "size"}
SEARCH_ORDERS = {"asc", "desc"}


def search(provider: str, query: str, page: int = 1, sort: str = "seeders", order: str = "desc") -> dict:
    if provider not in {"", "1337x"}:
        raise ValueError("Provider torrent non supportato.")
    query = " ".join(str(query).split())
    if not 2 <= len(query) <= 180:
        raise ValueError("Inserisci almeno 2 caratteri di ricerca.")
    page = max(1, min(50, int(page)))
    sort = str(sort or "seeders").casefold()
    order = str(order or "desc").casefold()
    if sort not in SEARCH_SORTS:
        raise ValueError("Ordinamento torrent non supportato.")
    if order not in SEARCH_ORDERS:
        raise ValueError("Direzione ordinamento non supportata.")
    cache_key = (SEARCH_BASE, query.casefold(), page, sort, order)
    now = time.monotonic()
    with _SEARCH_CACHE_LOCK:
        cached = _SEARCH_CACHE.get(cache_key)
        if cached and now - cached[0] <= _SEARCH_CACHE_TTL:
            result = dict(cached[1])
            result["cached"] = True
            return result
        if cached:
            _SEARCH_CACHE.pop(cache_key, None)

    url = f"{SEARCH_BASE}/sort-search/{quote(query, safe='')}/{sort}/{order}/{page}/"
    with _PROVIDER_LOCK:
        parser = _SearchParser(SEARCH_BASE)
        parser.feed(_fetch_html(url))
    result = {
        "ok": True,
        "provider": "1337x",
        "query": query,
        "page": page,
        "sort": sort,
        "order": order,
        "cached": False,
        "results": parser.rows[:80],
    }
    with _SEARCH_CACHE_LOCK:
        _SEARCH_CACHE[cache_key] = (time.monotonic(), result)
        if len(_SEARCH_CACHE) > 32:
            oldest = min(_SEARCH_CACHE, key=lambda key: _SEARCH_CACHE[key][0])
            _SEARCH_CACHE.pop(oldest, None)
    return result


def magnet_from_detail(detail_url: str) -> str:
    parsed = urlparse(detail_url)
    if parsed.scheme not in {"https", "http"} or (parsed.hostname or "").casefold() not in _provider_hosts():
        raise ValueError("URL risultato non valido.")
    if not parsed.path.startswith("/torrent/"):
        raise ValueError("URL risultato 1337x non valido.")
    parser = _MagnetParser()
    parser.feed(_fetch_html(detail_url))
    if not parser.magnet:
        raise TorrentError("Magnet link non trovato nella pagina del torrent.")
    return parser.magnet


class TransmissionRPC:
    def __init__(self, url: str = RPC_URL):
        self.url = url
        self.session_id = ""
        self._lock = threading.Lock()

    def call(self, method: str, arguments: dict | None = None, *, timeout: int = 6) -> dict:
        payload = json.dumps({"method": method, "arguments": arguments or {}}).encode("utf-8")
        with self._lock:
            for _ in range(2):
                headers = {"Content-Type": "application/json"}
                if self.session_id:
                    headers["X-Transmission-Session-Id"] = self.session_id
                request = Request(self.url, data=payload, headers=headers, method="POST")
                try:
                    with build_opener().open(request, timeout=timeout) as response:
                        result = json.loads(response.read().decode("utf-8"))
                except HTTPError as exc:
                    if exc.code == 409:
                        session_id = exc.headers.get("X-Transmission-Session-Id")
                        if session_id:
                            self.session_id = session_id
                            continue
                    raise TorrentError(f"Transmission RPC HTTP {exc.code}.") from exc
                except (URLError, TimeoutError, OSError, ValueError) as exc:
                    raise TorrentError(f"Transmission non raggiungibile: {exc}") from exc
                if result.get("result") != "success":
                    raise TorrentError(str(result.get("result") or "Errore Transmission."))
                return dict(result.get("arguments") or {})
        raise TorrentError("Handshake Transmission RPC non riuscito.")


RPC = TransmissionRPC()


def _valid_magnet(value: str) -> str:
    magnet = str(value).strip()
    if not magnet.startswith("magnet:?"):
        raise ValueError("Magnet link non valido.")
    lowered = magnet.casefold()
    if "xt=urn:btih:" not in lowered and "xt=urn:btmh:" not in lowered:
        raise ValueError("Magnet BitTorrent privo di info-hash.")
    if len(magnet) > 8192:
        raise ValueError("Magnet link troppo lungo.")
    return magnet


def add_magnet(magnet: str) -> dict:
    result = RPC.call("torrent-add", {"filename": _valid_magnet(magnet), "paused": False})
    item = result.get("torrent-added") or result.get("torrent-duplicate") or {}
    return {"ok": True, "torrent": item, "duplicate": "torrent-duplicate" in result}


def add_result(detail_url: str) -> dict:
    result = add_magnet(magnet_from_detail(detail_url))
    result["provider"] = "1337x"
    return result


def add_torrent_file(raw: bytes) -> dict:
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise ValueError("File .torrent vuoto o troppo grande.")
    metainfo = base64.b64encode(raw).decode("ascii")
    result = RPC.call("torrent-add", {"metainfo": metainfo, "paused": False})
    item = result.get("torrent-added") or result.get("torrent-duplicate") or {}
    return {"ok": True, "torrent": item, "duplicate": "torrent-duplicate" in result}


def action(torrent_id: int, command: str) -> dict:
    torrent_id = int(torrent_id)
    if torrent_id <= 0:
        raise ValueError("Torrent non valido.")
    if command == "pause":
        RPC.call("torrent-stop", {"ids": [torrent_id]})
    elif command == "resume":
        RPC.call("torrent-start", {"ids": [torrent_id]})
    elif command == "remove":
        RPC.call("torrent-remove", {"ids": [torrent_id], "delete-local-data": True})
    else:
        raise ValueError("Azione torrent non valida.")
    return {"ok": True, "id": torrent_id, "action": command}


def _normalized_torrent(item: dict) -> dict:
    status = int(item.get("status") or 0)
    eta = int(item.get("eta") or -1)
    return {
        "id": int(item.get("id") or 0),
        "name": str(item.get("name") or ""),
        "status": status,
        "status_label": STATUS_LABELS.get(status, "Sconosciuto"),
        "size": int(item.get("totalSize") or 0),
        "percent": round(float(item.get("percentDone") or 0) * 100, 2),
        "metadata_percent": round(float(item.get("metadataPercentComplete") or 0) * 100, 2),
        "rate_down": int(item.get("rateDownload") or 0),
        "rate_up": int(item.get("rateUpload") or 0),
        "eta": eta if 0 <= eta < 31_536_000 else None,
        "peers": int(item.get("peersConnected") or 0),
        "peers_down": int(item.get("peersSendingToUs") or 0),
        "peers_up": int(item.get("peersGettingFromUs") or 0),
        "downloaded": int(item.get("downloadedEver") or 0),
        "uploaded": int(item.get("uploadedEver") or 0),
        "ratio": float(item.get("uploadRatio") or 0),
        "remaining": int(item.get("leftUntilDone") or 0),
        "error": int(item.get("error") or 0),
        "error_text": str(item.get("errorString") or ""),
        "hash": str(item.get("hashString") or ""),
        "added": int(item.get("addedDate") or 0),
        "done": int(item.get("doneDate") or 0),
    }


def status() -> dict:
    try:
        stats = RPC.call("session-stats")
        args = RPC.call("torrent-get", {"fields": TORRENT_FIELDS})
        torrents = [_normalized_torrent(item) for item in args.get("torrents", [])]
        return {
            "ok": True,
            "available": True,
            "client": "Transmission",
            "provider": "1337x",
            "providers": providers(),
            "auto_import": True,
            "import_path": "Media/Downloads",
            "remove_after_import": True,
            "stats": {
                "count": int(stats.get("torrentCount") or len(torrents)),
                "active": int(stats.get("activeTorrentCount") or 0),
                "paused": int(stats.get("pausedTorrentCount") or 0),
                "rate_down": int(stats.get("downloadSpeed") or 0),
                "rate_up": int(stats.get("uploadSpeed") or 0),
            },
            "torrents": torrents,
            "recent_imports": _recent_imports(),
        }
    except TorrentError as exc:
        return {
            "ok": True,
            "available": False,
            "client": "Transmission",
            "provider": "1337x",
            "providers": providers(),
            "auto_import": True,
            "import_path": "Media/Downloads",
            "remove_after_import": True,
            "stats": {"count": 0, "active": 0, "paused": 0, "rate_down": 0, "rate_up": 0},
            "torrents": [],
            "recent_imports": _recent_imports(),
            "error": str(exc),
        }


def _load_receipts() -> dict:
    try:
        value = json.loads(IMPORT_STATE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_receipts(value: dict) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    trimmed = dict(sorted(value.items(), key=lambda pair: int((pair[1] or {}).get("ts") or 0), reverse=True)[:250])
    temp = IMPORT_STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(trimmed, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temp, IMPORT_STATE)


def _recent_imports() -> list[dict]:
    with _IMPORT_LOCK:
        values = [item for item in _load_receipts().values() if not (item or {}).get("history_hidden")]
    values.sort(key=lambda item: int((item or {}).get("ts") or 0), reverse=True)
    return values[:8]


def clear_recent_imports() -> dict:
    """Hide completed-download history without removing media or dedup receipts."""
    with _IMPORT_LOCK:
        receipts = _load_receipts()
        hidden = 0
        for item in receipts.values():
            if isinstance(item, dict) and not item.get("history_hidden"):
                item["history_hidden"] = True
                hidden += 1
        _save_receipts(receipts)
    return {"ok": True, "hidden": hidden}


def _safe_source(name: str) -> Path:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise TorrentError("Nome torrent non sicuro.")
    root = COMPLETE_ROOT.resolve(strict=False)
    source = (root / name).resolve(strict=False)
    if source.parent != root:
        raise TorrentError("Percorso torrent fuori staging.")
    return source


def _unique_target(name: str, is_dir: bool) -> Path:
    IMPORT_ROOT.mkdir(parents=True, exist_ok=True)
    candidate = IMPORT_ROOT / name
    if not candidate.exists():
        return candidate
    if is_dir:
        stem, suffix = name, ""
    else:
        path = Path(name)
        stem, suffix = path.stem, path.suffix
    for index in range(2, 1000):
        candidate = IMPORT_ROOT / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise TorrentError("Troppi file omonimi in Media/Downloads.")


def _refresh_media_library() -> None:
    try:
        media_center.invalidate_device_cache()
        media_center._LIBRARY_CACHE.pop(media_center.SHARE_MEDIA_UUID, None)
        media_center.library_summary(media_center.SHARE_MEDIA_UUID, force=True)
    except Exception:
        # Import is already durable; catalog refresh can retry on the next UI scan.
        pass


def _remove_imported_job(torrent_id: int) -> None:
    RPC.call("torrent-remove", {"ids": [int(torrent_id)], "delete-local-data": False})


def _import_one(item: dict) -> bool:
    torrent_id = int(item.get("id") or 0)
    info_hash = str(item.get("hashString") or "")
    name = str(item.get("name") or "")
    if not torrent_id or not info_hash or float(item.get("percentDone") or 0) < 1:
        return False
    with _IMPORT_LOCK:
        receipts = _load_receipts()
        if info_hash in receipts:
            try:
                _remove_imported_job(torrent_id)
            except TorrentError:
                pass
            return False

    if not os.path.ismount(SHARE_ROOT):
        return False

    source = _safe_source(name)
    if not source.exists():
        return False

    RPC.call("torrent-stop", {"ids": [torrent_id]})
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            state = RPC.call("torrent-get", {"ids": [torrent_id], "fields": ["id", "status"]}).get("torrents", [])
            if not state or int(state[0].get("status") or 0) == 0:
                break
        except TorrentError:
            break
        time.sleep(0.15)

    target = _unique_target(name, source.is_dir())
    source.rename(target)

    receipt = {
        "hash": info_hash,
        "name": name,
        "path": target.relative_to(MEDIA_ROOT).as_posix(),
        "size": int(item.get("totalSize") or 0),
        "ts": int(time.time()),
    }
    with _IMPORT_LOCK:
        receipts = _load_receipts()
        receipts[info_hash] = receipt
        _save_receipts(receipts)

    try:
        _remove_imported_job(torrent_id)
    finally:
        _refresh_media_library()
    return True


def auto_import_once() -> int:
    if not os.path.ismount(SHARE_ROOT):
        return 0
    args = RPC.call("torrent-get", {"fields": IMPORT_FIELDS})
    imported = 0
    for item in args.get("torrents", []):
        if float(item.get("percentDone") or 0) >= 1:
            try:
                imported += 1 if _import_one(item) else 0
            except (OSError, TorrentError, ValueError):
                continue
    return imported


def maintenance_loop() -> None:
    while True:
        try:
            auto_import_once()
        except Exception:
            pass
        time.sleep(2.0)
