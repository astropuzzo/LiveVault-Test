"""Remote Pi-hole device registry and Control Center integration.

Secrets stay server-side. The authenticated Control Center may reveal a device's
connection material on demand, but status/list endpoints never expose the admin
probe token or unrelated devices' DoH tokens.
"""
from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import plistlib
import secrets
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

CONFIG = Path(os.environ.get("OPENASTRO_DNS_CONFIG", "/var/lib/openastro-control/remote-dns.json"))
STATUS = Path(os.environ.get("OPENASTRO_DNS_STATUS", "/var/lib/openastro-dns/status.json"))
NETWORK_STATUS = Path(os.environ.get("OPENASTRO_DOT_NETWORK_STATUS", "/var/lib/openastro-dns/network.json"))
LOCK = Path(os.environ.get("OPENASTRO_DNS_LOCK", "/var/lib/openastro-control/remote-dns.lock"))
DEFAULT_DOH_HOST = os.environ.get("OPENASTRO_DOH_HOST", "openastro.tailf2871c.ts.net")
DEFAULT_DOH_PORT = int(os.environ.get("OPENASTRO_DOH_PORT", "8443"))
MAX_DEVICES = 32


def _now() -> int:
    return int(time.time())


def _default_config() -> dict:
    return {
        "version": 2,
        "admin_token": secrets.token_urlsafe(32),
        "doh": {
            "enabled": True,
            "hostname": DEFAULT_DOH_HOST,
            "port": DEFAULT_DOH_PORT,
            "path_prefix": "/dns-query",
        },
        "dot": {
            "enabled": False,
            "base_domain": "",
            "ipv6": "",
            "port": 853,
            "cert_file": "",
            "key_file": "",
            "certificate_expires": None,
            "external_verified_at": None,
        },
        "devices": [],
    }


def _normalize(config: dict) -> dict:
    base = _default_config()
    if not isinstance(config, dict):
        return base
    base["version"] = 2
    if isinstance(config.get("admin_token"), str) and config["admin_token"]:
        base["admin_token"] = config["admin_token"]
    for section in ("doh", "dot"):
        if isinstance(config.get(section), dict):
            base[section].update(config[section])
    devices = config.get("devices")
    if isinstance(devices, list):
        base["devices"] = [item for item in devices if isinstance(item, dict)][:MAX_DEVICES]
    # Legacy single-token DoH configuration: retain access as one migrated device.
    if not base["devices"] and isinstance(config.get("token"), str) and config.get("token"):
        base["devices"] = [{
            "id": str(uuid.uuid4()),
            "name": "Dispositivo precedente",
            "platform": "generic",
            "enabled": True,
            "label": secrets.token_hex(16),
            "doh_token": config["token"],
            "created_at": _now(),
        }]
        if config.get("hostname"):
            base["doh"]["hostname"] = str(config["hostname"])
    return base


@contextmanager
def _locked():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o640)
    with os.fdopen(fd, "r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_unlocked() -> dict:
    try:
        return _normalize(json.loads(CONFIG.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return _default_config()


def configuration() -> dict:
    return _read_unlocked()


def _write(config: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(_normalize(config), handle, separators=(",", ":"), sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, CONFIG)
    os.chmod(CONFIG, 0o640)


def ensure_configuration() -> dict:
    with _locked():
        config = _read_unlocked()
        _write(config)
        return config


def _metrics() -> dict:
    try:
        data = json.loads(STATUS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _network_status() -> dict:
    try:
        raw = json.loads(NETWORK_STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    # Fixed public schema: never expose router/API credentials or link-local values.
    return {
        "ok": bool(raw.get("ok")),
        "state": str(raw.get("state") or "unknown"),
        "checked_at": raw.get("checked_at"),
        "domain": str(raw.get("domain") or ""),
        "prefix": str(raw.get("prefix") or ""),
        "service_ipv6": str(raw.get("service_ipv6") or ""),
        "delegated": bool(raw.get("delegated")),
        "settings_permission": bool(raw.get("settings_permission")),
        "primary_firewall": raw.get("primary_firewall"),
        "secondary_prefix_firewall": raw.get("secondary_prefix_firewall"),
        "reason": str(raw.get("reason") or "Rete DoT non ancora verificata."),
    }


def _device_hostname(device: dict, config: dict) -> str:
    base = str(config.get("dot", {}).get("base_domain") or "").strip(".").lower()
    label = str(device.get("label") or "").strip(".").lower()
    return f"{label}.{base}" if label and base else ""


def _public_device(device: dict, config: dict, metrics: dict) -> dict:
    item_metrics = (metrics.get("devices") or {}).get(str(device.get("id")), {})
    last_seen = item_metrics.get("last_seen")
    online = bool(item_metrics.get("active_connections")) or bool(last_seen and time.time() - float(last_seen) < 90)
    return {
        "id": str(device.get("id") or ""),
        "name": str(device.get("name") or "Dispositivo"),
        "platform": str(device.get("platform") or "generic"),
        "enabled": bool(device.get("enabled", True)),
        "hostname": _device_hostname(device, config),
        "created_at": device.get("created_at"),
        "online": online,
        "last_seen": last_seen,
        "last_ip": item_metrics.get("last_ip"),
        "active_connections": int(item_metrics.get("active_connections") or 0),
        "queries": int(item_metrics.get("queries") or 0),
        "blocked": int(item_metrics.get("blocked") or 0),
        "last_protocol": item_metrics.get("last_protocol"),
    }


def status() -> dict:
    config = configuration()
    metrics = _metrics()
    network = _network_status()
    dot = config.get("dot", {})
    doh = config.get("doh", {})
    devices = [_public_device(item, config, metrics) for item in config.get("devices", [])]
    enabled_devices = [item for item in devices if item["enabled"]]
    checked_at = metrics.get("checked_at")
    gateway_fresh = bool(checked_at and time.time() - float(checked_at) < 180)
    dot_configured = bool(dot.get("enabled") and dot.get("base_domain") and dot.get("ipv6") and dot.get("cert_file") and dot.get("key_file"))
    dot_listening = bool(gateway_fresh and metrics.get("dot_listening"))
    doh_listening = bool(gateway_fresh and metrics.get("doh_listening"))
    remote_online = bool((dot_configured and dot_listening) or (doh.get("enabled") and doh_listening))
    reason = "DNS remoto pronto." if remote_online else "Gateway remoto in configurazione. Il DNS LAN continua a funzionare normalmente."
    if dot.get("base_domain") and not dot_configured:
        reason = "Dominio DoT presente, ma TLS/IPv6 non sono ancora completamente verificati."
    if network.get("state") not in {"ready", "unknown"}:
        reason = network.get("reason") or reason
    return {
        "configured": bool(enabled_devices),
        "reachable": remote_online,
        "protocol": "DoT + DoH" if dot_configured else "DoH",
        "port": 853 if dot_configured else int(doh.get("port") or DEFAULT_DOH_PORT),
        "hostname": str(dot.get("base_domain") or doh.get("hostname") or ""),
        "dot": "active" if dot_listening else "configured" if dot_configured else "not_configured",
        "doh": "active" if doh_listening else "configured" if doh.get("enabled") else "not_configured",
        "tls": "active" if dot_listening else "not_verified",
        "certificate_expires": dot.get("certificate_expires"),
        "gateway": "active" if gateway_fresh else "stale" if checked_at else "unknown",
        "checked_at": checked_at,
        "authorized_query": bool(metrics.get("authorized_query")),
        "unauthorized_denied": bool(metrics.get("unauthorized_denied")),
        "external_verified_at": dot.get("external_verified_at"),
        "ipv6": str(dot.get("ipv6") or ""),
        "base_domain": str(dot.get("base_domain") or ""),
        "network": network,
        "devices": devices,
        "device_count": len(devices),
        "enabled_device_count": len(enabled_devices),
        "online_device_count": sum(1 for item in devices if item["online"]),
        "reason": reason,
    }


def list_devices() -> dict:
    current = status()
    return {"ok": True, "devices": current["devices"], "remote": {k: current[k] for k in (
        "dot", "doh", "hostname", "ipv6", "network", "device_count", "enabled_device_count", "online_device_count"
    )}}


def _clean_name(value: str) -> str:
    name = " ".join(str(value or "").strip().split())
    if not 1 <= len(name) <= 48:
        raise ValueError("Nome dispositivo non valido.")
    return name


def _clean_platform(value: str) -> str:
    platform = str(value or "generic").strip().lower()
    return platform if platform in {"android", "ios", "ipad", "macos", "windows", "generic"} else "generic"


def create_device(name: str, platform: str = "generic") -> dict:
    name = _clean_name(name)
    platform = _clean_platform(platform)
    with _locked():
        config = _read_unlocked()
        if len(config.get("devices", [])) >= MAX_DEVICES:
            raise ValueError("Numero massimo di dispositivi raggiunto.")
        device = {
            "id": str(uuid.uuid4()),
            "name": name,
            "platform": platform,
            "enabled": True,
            "label": secrets.token_hex(16),
            "doh_token": secrets.token_urlsafe(32),
            "created_at": _now(),
        }
        config.setdefault("devices", []).append(device)
        _write(config)
    return {"ok": True, "device": _public_device(device, config, _metrics()), "connection": connection(device["id"])}


def _find(config: dict, device_id: str) -> dict:
    for device in config.get("devices", []):
        if str(device.get("id")) == str(device_id):
            return device
    raise KeyError("Dispositivo non trovato.")


def set_device_enabled(device_id: str, enabled: bool) -> dict:
    with _locked():
        config = _read_unlocked()
        device = _find(config, device_id)
        device["enabled"] = bool(enabled)
        _write(config)
    return {"ok": True, "device": _public_device(device, config, _metrics())}


def regenerate_device(device_id: str) -> dict:
    with _locked():
        config = _read_unlocked()
        device = _find(config, device_id)
        device["label"] = secrets.token_hex(16)
        device["doh_token"] = secrets.token_urlsafe(32)
        device["enabled"] = True
        device["rotated_at"] = _now()
        _write(config)
    return {"ok": True, "device": _public_device(device, config, _metrics()), "connection": connection(device_id)}


def delete_device(device_id: str) -> dict:
    with _locked():
        config = _read_unlocked()
        before = len(config.get("devices", []))
        config["devices"] = [item for item in config.get("devices", []) if str(item.get("id")) != str(device_id)]
        if len(config["devices"]) == before:
            raise KeyError("Dispositivo non trovato.")
        _write(config)
    return {"ok": True}


def configure_dot(base_domain: str, ipv6: str, cert_file: str, key_file: str, certificate_expires=None) -> dict:
    base_domain = str(base_domain or "").strip().lower().strip(".")
    if not base_domain or len(base_domain) > 253 or any(not part or len(part) > 63 for part in base_domain.split(".")):
        raise ValueError("Dominio DoT non valido.")
    try:
        address = ipaddress.ip_address(str(ipv6))
    except ValueError as exc:
        raise ValueError("IPv6 DoT non valido.") from exc
    if address.version != 6 or not address.is_global:
        raise ValueError("Serve un indirizzo IPv6 globale.")
    if not str(cert_file).startswith("/") or not str(key_file).startswith("/"):
        raise ValueError("Percorsi TLS non validi.")
    with _locked():
        config = _read_unlocked()
        config["dot"].update({
            "enabled": True,
            "base_domain": base_domain,
            "ipv6": str(address),
            "port": 853,
            "cert_file": str(cert_file),
            "key_file": str(key_file),
            "certificate_expires": certificate_expires,
        })
        _write(config)
    return {"ok": True, "remote": status()}


def connection(device_id: str | None = None) -> dict:
    config = configuration()
    if device_id is None:
        devices = config.get("devices", [])
        if len(devices) != 1:
            return {"ok": False, "error": "Seleziona un dispositivo."}
        device = devices[0]
    else:
        try:
            device = _find(config, device_id)
        except KeyError as exc:
            return {"ok": False, "error": str(exc.args[0])}
    if not device.get("enabled", True):
        return {"ok": False, "error": "Dispositivo bloccato."}
    doh = config.get("doh", {})
    host = str(doh.get("hostname") or DEFAULT_DOH_HOST)
    port = int(doh.get("port") or DEFAULT_DOH_PORT)
    prefix = str(doh.get("path_prefix") or "/dns-query").rstrip("/")
    authority = host if port == 443 else f"{host}:{port}"
    doh_url = f"https://{authority}{prefix}/{device['doh_token']}"
    hostname = _device_hostname(device, config)
    return {
        "ok": True,
        "id": str(device["id"]),
        "name": str(device.get("name") or "Dispositivo"),
        "platform": str(device.get("platform") or "generic"),
        "hostname": hostname,
        "dot_ready": bool(config.get("dot", {}).get("enabled") and hostname),
        "doh_url": doh_url,
        "doh_ready": bool(doh.get("enabled")),
    }


def apple_profile(device_id: str | None = None) -> bytes:
    settings = connection(device_id)
    if not settings.get("ok") or not settings.get("doh_ready"):
        raise ValueError(settings.get("error") or "DoH non configurato.")
    payload = {
        "PayloadType": "com.apple.dnsSettings.managed",
        "PayloadVersion": 1,
        "PayloadIdentifier": f"net.openastro.pihole.dns.{settings['id']}",
        "PayloadUUID": str(uuid.uuid4()),
        "PayloadDisplayName": f"OpenAstro Pi-hole · {settings['name']}",
        "DNSSettings": {"DNSProtocol": "HTTPS", "ServerURL": settings["doh_url"]},
        "OnDemandRules": [{"Action": "Connect"}],
    }
    return plistlib.dumps({
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": f"net.openastro.pihole.{settings['id']}",
        "PayloadUUID": str(uuid.uuid4()),
        "PayloadDisplayName": f"OpenAstro Pi-hole · {settings['name']}",
        "PayloadRemovalDisallowed": False,
        "PayloadDescription": "DNS cifrato attraverso il tuo Pi-hole OpenAstro. Profilo personale e revocabile dal Control Center.",
        "PayloadContent": [payload],
    })


def find_by_doh_token(token: str) -> dict | None:
    config = configuration()
    for device in config.get("devices", []):
        if device.get("enabled", True) and secrets.compare_digest(str(device.get("doh_token") or ""), str(token or "")):
            return device
    return None


def find_by_sni(hostname: str) -> dict | None:
    hostname = str(hostname or "").strip().lower().strip(".")
    config = configuration()
    for device in config.get("devices", []):
        if device.get("enabled", True) and _device_hostname(device, config) == hostname:
            return device
    return None


def probe() -> dict:
    # Gateway owns the active network probe. Calling this endpoint asks it to
    # refresh immediately through its separately authenticated local endpoint.
    import urllib.request
    config = configuration()
    request = urllib.request.Request(
        "http://127.0.0.1:5054/probe",
        data=b"",
        headers={"Authorization": "Bearer " + str(config.get("admin_token") or "")},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except Exception:
        return {"ok": False, "error": "Gateway DNS non raggiungibile; consulta lo stato remoto."}


# Compatibility shim for the interrupted single-token UI/tests. New code uses
# per-device regeneration instead of a global rotate.
def rotate() -> dict:
    config = configuration()
    devices = config.get("devices", [])
    if len(devices) != 1:
        return {"ok": False, "error": "Seleziona il dispositivo da rigenerare."}
    return regenerate_device(str(devices[0]["id"]))
