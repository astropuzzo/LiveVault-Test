"""Guest-only MyFreeCams room state and public HLS resolution.

The provider's server map is cached; each lookup has a bounded connection and
receive deadline. Transport failures never become a fabricated offline state.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import unquote

import requests
from websockets.sync.client import connect

ORIGIN = "https://www.myfreecams.com"
HEADERS = {
    "Origin": ORIGIN,
    "Referer": f"{ORIGIN}/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
}
_config_lock = threading.Lock()
_config: dict = {}
_config_until = 0.0


@dataclass(frozen=True)
class Room:
    uid: int
    status: str
    server: str = ""
    title: str = ""


def server_config() -> dict:
    global _config, _config_until
    with _config_lock:
        if _config and time.monotonic() < _config_until:
            return _config
        response = requests.get(f"{ORIGIN}/_js/serverconfig.js", headers=HEADERS, timeout=10)
        response.raise_for_status()
        # The public file may be bare JSON or a JavaScript assignment.
        start, end = response.text.find("{"), response.text.rfind("}")
        payload = json.loads(response.text[start:end + 1])
        if not isinstance(payload, dict) or not isinstance(payload.get("websocket_servers"), dict):
            raise RuntimeError("Configurazione MyFreeCams non riconosciuta")
        _config, _config_until = payload, time.monotonic() + 3600
        return _config


def _hostname(value: object) -> str:
    # Hosts are selected only from the provider's map, within its own namespace.
    name = str(value or "")
    if name.endswith(".myfreecams.com"):
        name = name[:-len(".myfreecams.com")]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", name):
        raise RuntimeError("Server MyFreeCams non valido")
    return f"{name}.myfreecams.com"


def _messages(ws, deadline: float):
    buffer = ""
    while time.monotonic() < deadline:
        while len(buffer) >= 6:
            length_field = buffer[:6].strip()
            if not length_field.isdecimal():
                raise RuntimeError("Protocollo MyFreeCams non riconosciuto")
            size = int(length_field)
            if size < 1 or size > 2 * 1024 * 1024:
                raise RuntimeError("Messaggio MyFreeCams non valido")
            if len(buffer) < size + 6:
                break
            message, buffer = buffer[6:size + 6], buffer[size + 6:]
            fields = message.split(" ", 5)
            if len(fields) < 5:
                raise RuntimeError("Messaggio MyFreeCams incompleto")
            yield fields
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        chunk = ws.recv(timeout=remaining)
        if not chunk:
            raise RuntimeError("Connessione MyFreeCams chiusa")
        buffer += chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        if len(buffer) > 2 * 1024 * 1024 + 6:
            raise RuntimeError("Risposta MyFreeCams troppo grande")
    raise TimeoutError("Stato MyFreeCams non ricevuto")


def _room(fields: list[str], slug: str) -> Room:
    if len(fields) < 6:
        raise RuntimeError("Risposta MyFreeCams senza stato")
    decoded = unquote(fields[5]).strip()
    # The server echoes the requested name when no such account exists.
    if decoded.lower() == slug.lower() and fields[4] != "0":
        return Room(0, "offline")
    if fields[4] != "0":
        raise RuntimeError("Ricerca MyFreeCams rifiutata")
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise RuntimeError("Stato MyFreeCams non riconosciuto")
    if payload.get("lv") != 4:
        return Room(0, "offline")
    uid = int(payload.get("uid") or 0)
    if uid <= 0:
        raise RuntimeError("Identificativo MyFreeCams mancante")
    state = payload.get("vs")
    status = {0: "live", 2: "away", 12: "private", 13: "private", 14: "private", 90: "offline", 127: "offline"}.get(state, "unknown")
    user = payload.get("u") or {}
    return Room(uid, status, str(user.get("camserv") or ""), str(payload.get("nm") or slug))


def lookup(slug: str) -> Room:
    if not re.fullmatch(r"[A-Za-z0-9_]{1,100}", slug):
        raise ValueError("Username MyFreeCams non valido")
    config = server_config()
    servers = [key for key, protocol in config["websocket_servers"].items() if protocol == "rfc6455"]
    if not servers:
        raise RuntimeError("Nessun server MyFreeCams disponibile")
    failures = []
    for server in random.sample(servers, min(2, len(servers))):
        try:
            with connect(
                f"wss://{_hostname(server)}/fcsl", origin=ORIGIN,
                user_agent_header=HEADERS["User-Agent"], open_timeout=6,
                close_timeout=1, max_size=2 * 1024 * 1024,
            ) as ws:
                ws.send("hello fcserver\n\0")
                ws.send("1 0 0 20080909 0 guest:guest\n")
                ws.send(f"10 0 0 20 0 {slug.lower()}\n")
                for fields in _messages(ws, time.monotonic() + 10):
                    # The lookup's own response is authoritative, independently
                    # of unrelated session messages; no account is requested.
                    if fields[0] == "10" and fields[3] == "20":
                        return _room(fields, slug)
        except Exception as exc:
            failures.append(type(exc).__name__)
    raise RuntimeError(f"Stato MyFreeCams non disponibile ({', '.join(failures)})")


def _playlist_candidates(room: Room, config: dict) -> list[str]:
    result = []
    stream_id = room.uid + 100_000_000
    for mapping, prefix in (("h5video_servers", "mfc_"), ("wzobs_servers", "mfc_a_"), ("ngvideo_servers", "mfc_")):
        host = (config.get(mapping) or {}).get(room.server)
        if not host:
            continue
        root = f"https://{_hostname(host)}/NxServer/ngrp:{prefix}{stream_id}"
        result.extend((f"{root}.f4v_cmaf/playlist_sfm4s.m3u8", f"{root}.f4v_mobile/playlist.m3u8"))
    return list(dict.fromkeys(result))


def public_playlist(slug: str) -> tuple[str, dict[str, str]]:
    room = lookup(slug)  # fresh state: never reuse a formerly public/private URL
    if room.status != "live":
        raise RuntimeError(f"MyFreeCams: {room.status}; stream pubblico non disponibile")
    candidates = _playlist_candidates(room, server_config())
    for url in candidates:
        try:
            response = requests.get(url, headers=HEADERS, timeout=6, allow_redirects=False)
            if response.status_code == 200 and response.text.lstrip().startswith("#EXTM3U"):
                return url, dict(HEADERS)
        except requests.RequestException:
            continue
    raise RuntimeError("MyFreeCams pubblica, ma playlist HLS non disponibile")


@lru_cache(maxsize=2)
def hls_options(program: str) -> tuple[str, ...]:
    """Accept MFC's .pts segments on old and current FFmpeg.

    Current HLS demuxers check whether the filename matches the detected
    container. MFC uses .pts for TS or MP4 fragments; disable that check only for inputs
    resolved by this adapter. Protocol and actual A/V validation still apply.
    """
    result = subprocess.run(
        [program, "-hide_banner", "-h", "demuxer=hls"],
        capture_output=True, text=True, timeout=4, check=True,
    )
    if "-extension_picky" in result.stdout + result.stderr:
        return ("-extension_picky", "0")
    return ("-allowed_extensions", "aac,m3u8,m4s,mp4,ts,pts,cmfv,cmfa")
