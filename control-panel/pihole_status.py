from __future__ import annotations

import json
import remote_dns
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

PIHOLE_API = os.environ.get("OPENASTRO_PIHOLE_API", "http://127.0.0.1")
LAN_INTERFACE = os.environ.get("OPENASTRO_PIHOLE_INTERFACE", "eth0")
LAN_FALLBACK_IP = os.environ.get("OPENASTRO_PIHOLE_LAN_IP", "192.168.1.27")
REMOTE_REASON = (
    "DoT remoto non esposto: TCP 853 non è raggiungibile sull’IPv4 Iliad attuale e "
    "Android Private DNS non fornisce autenticazione client. Un endpoint ricorsivo 853 "
    "accessibile da reti mobili variabili diventerebbe utilizzabile da terzi."
)


def _run(command: list[str], timeout: int = 3) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, str(exc)


def _api(path: str) -> dict | None:
    try:
        request = urllib.request.Request(f"{PIHOLE_API}{path}", headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=1.5) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def _service_state() -> str:
    code, output = _run(["systemctl", "is-active", "pihole-FTL.service"], 2)
    if code == 0:
        return output or "active"
    if "could not be found" in output.lower() or "not-found" in output.lower():
        return "not-found"
    return output or "inactive"


def _lan_ip() -> str:
    code, output = _run(["ip", "-4", "-o", "addr", "show", "dev", LAN_INTERFACE], 2)
    if code == 0:
        for field in output.split():
            if "/" in field and field.count(".") == 3:
                return field.split("/", 1)[0]
    return LAN_FALLBACK_IP


def _dns_online() -> bool:
    if not shutil.which("dig"):
        return False
    code, output = _run(["dig", "+time=1", "+tries=1", "+short", "@127.0.0.1", "example.com", "A"], 2)
    return code == 0 and bool(output.strip())


def status() -> dict:
    service = _service_state()
    installed = Path("/etc/pihole/pihole.toml").exists() and shutil.which("pihole") is not None
    lan_ip = _lan_ip()
    version = _api("/api/info/version") if installed and service == "active" else None
    blocking = _api("/api/dns/blocking") if installed and service == "active" else None
    summary = _api("/api/stats/summary") if installed and service == "active" else None
    api_online = version is not None and blocking is not None and summary is not None
    dns_online = installed and service == "active" and _dns_online()

    local_versions = (((version or {}).get("version") or {}))
    core = (((local_versions.get("core") or {}).get("local") or {}).get("version") or "").lstrip("v")
    web = (((local_versions.get("web") or {}).get("local") or {}).get("version") or "").lstrip("v")
    ftl = (((local_versions.get("ftl") or {}).get("local") or {}).get("version") or "").lstrip("v")
    blocking_value = str((blocking or {}).get("blocking", "unknown")).lower()
    blocking_on = blocking_value in {"enabled", "on", "true"}

    query_stats = (summary or {}).get("queries") or {}
    client_stats = (summary or {}).get("clients") or {}
    gravity_stats = (summary or {}).get("gravity") or {}

    remote = remote_dns.status()

    return {
        "installed": installed,
        "service": service,
        "ftl_active": service == "active",
        "dns_online": bool(dns_online),
        "api_online": bool(api_online),
        "blocking": "on" if blocking_on else ("off" if blocking_value in {"disabled", "off", "false"} else "unknown"),
        "versions": {"core": core, "web": web, "ftl": ftl},
        "lan": {
            "interface": LAN_INTERFACE,
            "ip": lan_ip,
            "dns": lan_ip,
            "admin_url": f"http://{lan_ip}/admin/",
        },
        "remote": remote,
        "stats": {
            "queries": int(query_stats.get("total") or 0),
            "blocked": int(query_stats.get("blocked") or 0),
            "percent_blocked": float(query_stats.get("percent_blocked") or 0),
            "active_clients": int(client_stats.get("active") or 0),
            "clients": int(client_stats.get("total") or 0),
            "gravity_domains": int(gravity_stats.get("domains_being_blocked") or 0),
            "gravity_last_update": int(gravity_stats.get("last_update") or 0),
        },
        "config_complete": bool(installed and dns_online and api_online),
        "remote_config_complete": remote["reachable"],
    }
