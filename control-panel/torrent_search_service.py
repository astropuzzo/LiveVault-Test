#!/usr/bin/env python3
from __future__ import annotations

import html
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from scrapling.fetchers import StealthySession


HOST = "127.0.0.1"
PORT = int(os.environ.get("OPENASTRO_TORRENT_SEARCH_PORT", "9092"))
IDLE_SECONDS = max(30, int(os.environ.get("OPENASTRO_TORRENT_SEARCH_IDLE", "120")))
ALLOWED_HOSTS = {
    "1337x.to",
    "1337x.st",
    "x1337x.cc",
    "x1337x.ws",
    "x1337x.eu",
}
MAX_BODY = 2_000_000

_session: StealthySession | None = None
_cleared = False
_last_used = 0.0


def _safe_url(value: str) -> str:
    parsed = urlparse(str(value))
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS:
        raise ValueError("Provider URL non consentito.")
    if not (parsed.path.startswith("/search/") or parsed.path.startswith("/sort-search/") or parsed.path.startswith("/torrent/")):
        raise ValueError("Provider path non consentito.")
    return parsed.geturl()


def _body(response) -> str:
    value = getattr(response, "html_content", None)
    if value is None:
        value = str(response)
    elif isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    else:
        value = str(value)
    if len(value.encode("utf-8", "replace")) > MAX_BODY:
        raise RuntimeError("Risposta provider troppo grande.")
    return value


def _challenge(response, body: str) -> bool:
    status = int(getattr(response, "status", 0) or 0)
    lowered = body.casefold()
    return status in {403, 429, 503} or "just a moment" in lowered or "cf-chl-" in lowered


def _new_session() -> StealthySession:
    session = StealthySession(
        headless=True,
        solve_cloudflare=True,
        network_idle=False,
        disable_resources=True,
        timeout=60_000,
        max_pages=1,
    )
    session.start()
    return session


def _close_session_locked() -> None:
    global _session, _cleared
    if _session is not None:
        try:
            _session.close()
        except Exception:
            pass
    _session = None
    _cleared = False


def fetch_provider(url: str) -> str:
    global _session, _cleared, _last_used
    url = _safe_url(url)
    if _session is None:
        _session = _new_session()
    try:
        response = _session.fetch(
            url,
            solve_cloudflare=not _cleared,
            network_idle=False,
            disable_resources=True,
            wait=250,
            timeout=60_000,
        )
        body = _body(response)
        if _challenge(response, body):
            response = _session.fetch(
                url,
                solve_cloudflare=True,
                network_idle=False,
                disable_resources=True,
                wait=250,
                timeout=60_000,
            )
            body = _body(response)
        if _challenge(response, body):
            raise RuntimeError("Cloudflare challenge non risolto.")
        _cleared = True
        _last_used = time.monotonic()
        return body
    except Exception:
        _close_session_locked()
        raise


class SearchHTTPServer(HTTPServer):
    def service_actions(self) -> None:
        global _last_used
        if _session is not None and _last_used and time.monotonic() - _last_used >= IDLE_SECONDS:
            _close_session_locked()
            _last_used = 0.0


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenAstroTorrentSearch/1"

    def log_message(self, fmt, *args):
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send(200, b'{"ok":true}', "application/json")
            return
        if parsed.path != "/fetch":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            url = parse_qs(parsed.query, keep_blank_values=False).get("url", [""])[0]
            body = fetch_provider(url).encode("utf-8", "replace")
            self._send(200, body, "text/html; charset=utf-8")
        except ValueError as exc:
            self._send(400, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
        except Exception as exc:
            message = f"provider fetch failed: {exc}"
            self._send(502, message.encode("utf-8", "replace"), "text/plain; charset=utf-8")


def main() -> None:
    SearchHTTPServer((HOST, PORT), Handler).serve_forever(poll_interval=1.0)


if __name__ == "__main__":
    main()
