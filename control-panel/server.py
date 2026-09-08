#!/usr/bin/env python3
from __future__ import annotations

import json
import copy
import gzip
import hashlib
import hmac
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.request
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import sys
_CONTROL_DIR = str(Path(__file__).resolve().parent)
if _CONTROL_DIR not in sys.path:
    sys.path.insert(0, _CONTROL_DIR)
import media_center
import media_streaming
import remote_dns
import pihole_status as pihole_runtime


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
HOST = os.environ.get("OPENASTRO_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPENASTRO_PORT", "9090"))
STARTED_AT = time.time()
ACTION_LOG = Path("/var/log/openastro-control-actions.log")
STATE_DIR = Path("/var/lib/openastro-control")
HISTORY_FILE = STATE_DIR / "history.json"
AVAILABILITY_FILE = STATE_DIR / "availability.json"
HISTORY_RAW_SECONDS = 86400
HISTORY_RETENTION_SECONDS = 90 * 86400
AVAILABILITY_RETENTION_SECONDS = 400 * 86400
PROFILE_FILE = Path("/etc/openastro-power-profile")
AUTH_FILE = Path("/etc/openastro-control-auth.json")
DATA_UUID = "5fe2d0f6-b485-44e9-8e26-31fb0d217db2"
SHARE_UUID = "7EBD-F531"

ALLOWED_ACTIONS = {
    "eject_nvme",
    "attach_nvme",
    "restart_livevault",
    "restart_docker",
    "backup_now",
    "restart_pihole",
    "pihole_enable",
    "pihole_disable",
    "pihole_gravity",
    "power_profile",
    "media_mount",
    "media_eject",
    "media_rescan",
    "reboot",
}

_cpu_lock = threading.Lock()
_cpu_last: tuple[int, int] | None = None
_network_lock = threading.Lock()
_network_last: tuple[float, int, int] | None = None
_history_lock = threading.Lock()
_history: list[dict] = []
_availability_lock = threading.Lock()
_availability: dict = {}
_session_lock = threading.Lock()
_sessions: dict[str, dict] = {}
_login_attempts: dict[str, list[float]] = {}
_state_lock = threading.Lock()
_state_cache: dict = {}
_state_cached_at = 0.0
_action_lock = threading.Lock()


def run(command: list[str], timeout: int = 8) -> tuple[int, str]:
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


def read_text(path: str, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return default


def cpu_percent() -> float:
    global _cpu_last
    fields = [int(value) for value in read_text("/proc/stat").splitlines()[0].split()[1:]]
    idle = fields[3] + fields[4]
    total = sum(fields)
    with _cpu_lock:
        previous = _cpu_last
        _cpu_last = (total, idle)
    if previous is None or total <= previous[0]:
        load = os.getloadavg()[0]
        return round(min(100.0, load / max(1, os.cpu_count() or 1) * 100), 1)
    total_delta = total - previous[0]
    idle_delta = idle - previous[1]
    return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 1)


def memory() -> dict:
    values: dict[str, int] = {}
    for line in read_text("/proc/meminfo").splitlines():
        key, raw = line.split(":", 1)
        values[key] = int(raw.strip().split()[0]) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = max(0, total - available)
    return {"total": total, "used": used, "percent": round(used / total * 100, 1) if total else 0}


def disk(path: str) -> dict:
    mounted = os.path.ismount(path)
    if not mounted:
        return {"path": path, "mounted": False, "total": 0, "used": 0, "free": 0, "percent": 0}
    usage = shutil.disk_usage(path)
    return {
        "path": path,
        "mounted": True,
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "percent": round(usage.used / usage.total * 100, 1) if usage.total else 0,
    }


def temperature() -> float | None:
    candidates = sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
    for candidate in candidates:
        try:
            value = float(candidate.read_text().strip())
            return round(value / 1000 if value > 500 else value, 1)
        except (OSError, ValueError):
            continue
    code, output = run(["vcgencmd", "measure_temp"], 2)
    if code == 0 and "=" in output:
        try:
            return float(output.split("=", 1)[1].split("'", 1)[0])
        except ValueError:
            pass
    return None


def network_totals() -> dict:
    global _network_last
    rx = tx = 0
    interfaces: list[dict] = []
    ignored = ("lo", "docker", "br-", "veth")
    for line in read_text("/proc/net/dev").splitlines()[2:]:
        name, values = line.split(":", 1)
        name = name.strip()
        if name.startswith(ignored):
            continue
        columns = values.split()
        item_rx, item_tx = int(columns[0]), int(columns[8])
        rx += item_rx
        tx += item_tx
        interfaces.append({"name": name, "rx": item_rx, "tx": item_tx})
    now = time.monotonic()
    with _network_lock:
        previous = _network_last
        _network_last = (now, rx, tx)
    elapsed = now - previous[0] if previous else 0
    rx_rate = max(0, (rx - previous[1]) / elapsed) if elapsed > 0 else 0
    tx_rate = max(0, (tx - previous[2]) / elapsed) if elapsed > 0 else 0
    return {"rx": rx, "tx": tx, "rx_rate": round(rx_rate), "tx_rate": round(tx_rate), "interfaces": interfaces}


def power_state() -> dict:
    policy = Path("/sys/devices/system/cpu/cpufreq/policy0")
    governor = read_text(str(policy / "scaling_governor"), "unknown")
    current = int(read_text(str(policy / "scaling_cur_freq"), "0") or 0) // 1000
    maximum = int(read_text(str(policy / "scaling_max_freq"), "0") or 0) // 1000
    hardware_max = int(read_text(str(policy / "cpuinfo_max_freq"), "0") or 0) // 1000
    profile, wifi_policy = "balanced", "on"
    try:
        parts = PROFILE_FILE.read_text(encoding="utf-8").strip().split()
        if parts:
            profile = parts[0]
        if len(parts) > 1:
            wifi_policy = parts[1]
    except OSError:
        pass
    _, wifi_radio = run(["nmcli", "radio", "wifi"], 3)
    _, active_connections = run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"], 3)
    throttle_code = 0
    code, throttle = run(["vcgencmd", "get_throttled"], 2)
    if code == 0 and "0x" in throttle:
        try:
            throttle_code = int(throttle.rsplit("0x", 1)[1], 16)
        except ValueError:
            pass
    return {
        "profile": profile,
        "governor": governor,
        "current_mhz": current,
        "max_mhz": maximum,
        "hardware_max_mhz": hardware_max,
        "wifi_policy": wifi_policy,
        "wifi_radio": wifi_radio.strip() == "enabled",
        "hotspot": "OpenAstro-AP:" in active_connections,
        "undervoltage_now": bool(throttle_code & 0x1),
        "throttled_now": bool(throttle_code & 0x4),
        "power_event_seen": bool(throttle_code & 0x50000),
    }


def estimated_watts(cpu_usage: float, network: dict, data_mounted: bool, wifi_active: bool) -> float:
    """Legacy software model, kept separate from ASIAIR carrier-board sensors."""
    policy = Path("/sys/devices/system/cpu/cpufreq/policy0")
    current = int(read_text(str(policy / "scaling_cur_freq"), "0") or 0)
    maximum = int(read_text(str(policy / "cpuinfo_max_freq"), "1500000") or 1500000)
    frequency_ratio = max(0.4, min(1.0, current / maximum if maximum else 1.0))
    traffic = (network.get("rx_rate", 0) + network.get("tx_rate", 0)) / 12_500_000
    board = 2.8
    cpu = 0.25 + (2.6 * max(0, min(100, cpu_usage)) / 100 * frequency_ratio)
    nvme = 0.95 if data_mounted else 0
    wifi = 0.65 if wifi_active else 0
    ethernet = 0.35
    io_network = min(0.55, max(0, traffic) * 0.55)
    return round(board + cpu + nvme + wifi + ethernet + io_network, 2)


def _input_power_bus_paths() -> list[str]:
    """Prefer the CSI I2C aliases, then probe any remaining adapter.

    Raspberry Pi kernels may expose the CM4 CSI bus as i2c-10 when the
    i2c0 mux is enabled, or as i2c-0 / the parent adapter when i2c0 is
    pinned directly to GPIO 44/45. Do not make telemetry depend on a bus
    number that can change across firmware/kernel configurations.
    """
    import glob
    preferred = ["/dev/i2c-10", "/dev/i2c-0", "/dev/i2c-22"]
    discovered = sorted(glob.glob("/dev/i2c-*"), key=lambda value: int(value.rsplit("-", 1)[-1]))
    return list(dict.fromkeys(preferred + discovered))


def _read_input_power_bus(path: str) -> tuple[float, float]:
    import fcntl
    with open(path, "r+b", buffering=0) as bus:
        fcntl.ioctl(bus.fileno(), 0x0703, 0x4b)
        bus.write(b"\x01")
        original = bus.read(2)
        if len(original) != 2:
            raise OSError("ADC configuration unavailable")
        values = []
        try:
            for config, scale in ((0xE683, 21 / 2000), (0xF483, 1 / 200)):
                bus.write(bytes((1, config >> 8, config & 255)))
                time.sleep(0.005)
                bus.write(b"\x00")
                sample = bus.read(2)
                if len(sample) != 2:
                    raise OSError("Incomplete ADC sample")
                word = int.from_bytes(sample, "big", signed=True)
                raw = word >> 4
                if word & 15 or raw >= 2047 or raw < -1:
                    raise ValueError("Invalid or saturated ADC sample")
                values.append(max(0, raw) * scale)
        finally:
            bus.write(b"\x01" + original)
    volts, amps = values
    if not 1 <= volts <= 21 or not 0 <= amps <= 10:
        raise ValueError("ADC input outside supported range")
    return volts, amps


def read_input_power() -> dict:
    """Read only the ASIAIR Plus CM4 input ADC; never claim power-output GPIOs.

    ADS1015 at 0x4b on the CSI I2C bus. Channel mapping and scaling follow
    indilib/indi-3rdparty indi-asi-power/asipower.h. This is DC input power,
    including attached loads, not AC wall power or CPU-only consumption.
    """
    result = {"watts": None, "input_volts": None, "input_amps": None,
              "measurement": "unavailable", "power_source": "ASIAIR ADS1015",
              "power_scope": "DC input", "power_sample_at": None}
    for path in _input_power_bus_paths():
        try:
            volts, amps = _read_input_power_bus(path)
        except (OSError, ValueError, ImportError, PermissionError):
            continue
        result.update(watts=round(volts * amps, 3), input_volts=round(volts, 3),
                      input_amps=round(amps, 3), measurement="measured",
                      power_sample_at=time.time(), power_bus=path)
        break
    return result


def measured_energy(points: list[dict]) -> dict:
    """Integrate real samples only; missing data and gaps >30s are not zero load."""
    watt_seconds = covered = 0.0
    for left, right in zip(points, points[1:]):
        if any(p.get("power_measurement") != "measured" or p.get("watts") is None
               for p in (left, right)):
            continue
        elapsed = right["t"] - left["t"]
        if 0 < elapsed <= 30:
            watt_seconds += (left["watts"] + right["watts"]) * 0.5 * elapsed
            covered += elapsed
    return {"wh": round(watt_seconds / 3600, 4) if covered else None,
            "covered_seconds": round(covered),
            "average_watts": round(watt_seconds / covered, 3) if covered else None}


def sample_metrics() -> dict:
    snapshot = cached_state()
    host, data = snapshot["host"], snapshot["storage"]["data"]
    network = host["network"]
    return {
        "t": snapshot["timestamp"],
        "cpu": host["cpu_percent"],
        "ram": host["memory"]["percent"],
        "temp": host["temperature"],
        "disk": data["percent"] if data["mounted"] else None,
        "rx": network["rx_rate"],
        "tx": network["tx_rate"],
        "watts": snapshot["power"]["watts"],
        "power_measurement": snapshot["power"]["measurement"],
        "input_volts": snapshot["power"]["input_volts"],
        "input_amps": snapshot["power"]["input_amps"],
        "estimated_watts": snapshot["power"]["estimated_watts"],
    }


def _boot_id() -> str:
    return read_text("/proc/sys/kernel/random/boot_id", "unknown").strip()


def _boot_time() -> int:
    try:
        uptime = float(read_text("/proc/uptime", "0").split()[0])
    except (ValueError, IndexError):
        uptime = 0.0
    return max(0, int(time.time() - uptime))


def _merge_downtimes(rows: list[dict]) -> list[dict]:
    clean = []
    for row in sorted(rows, key=lambda item: int(item.get("start", 0))):
        start = int(row.get("start", 0)); end = int(row.get("end", 0))
        if start <= 0 or end <= start:
            continue
        if clean and start <= clean[-1]["end"] + 2:
            clean[-1]["end"] = max(clean[-1]["end"], end)
        else:
            clean.append({"start": start, "end": end, "reason": str(row.get("reason", "offline"))})
    return clean


def save_availability() -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with _availability_lock:
            payload = json.dumps(_availability, separators=(",", ":"))
        temporary = AVAILABILITY_FILE.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(AVAILABILITY_FILE)
    except OSError:
        pass


def load_availability() -> None:
    global _availability
    now = int(time.time()); boot_id = _boot_id(); boot_time = _boot_time()
    try:
        loaded = json.loads(AVAILABILITY_FILE.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            loaded = {}
    except (OSError, ValueError, json.JSONDecodeError):
        loaded = {}
    with _history_lock:
        history_first = int(_history[0].get("t", now)) if _history else now
        history_last = int(_history[-1].get("t", 0)) if _history else 0
    first_seen = int(loaded.get("first_seen") or history_first or now)
    last_seen = int(loaded.get("last_seen") or history_last or 0)
    previous_boot = str(loaded.get("boot_id") or "")
    downtimes = list(loaded.get("downtimes") or [])
    # A changed kernel boot ID is a real machine reboot/power cycle. A control-panel
    # restart within the same boot is not system downtime.
    if previous_boot and previous_boot != boot_id and last_seen and boot_time > last_seen + 30:
        downtimes.append({"start": last_seen, "end": boot_time, "reason": "system_offline"})
    cutoff = now - AVAILABILITY_RETENTION_SECONDS
    downtimes = [row for row in _merge_downtimes(downtimes) if row["end"] >= cutoff]
    _availability = {
        "boot_id": boot_id,
        "boot_time": boot_time,
        "first_seen": min(first_seen, history_first),
        "last_seen": now,
        "downtimes": downtimes,
    }
    save_availability()


def touch_availability(now: int, *, persist: bool = False) -> None:
    with _availability_lock:
        _availability["last_seen"] = int(now)
        _availability["boot_id"] = _boot_id()
        _availability["boot_time"] = _boot_time()
        if not _availability.get("first_seen"):
            _availability["first_seen"] = int(now)
    if persist:
        save_availability()


def availability_payload(seconds: int, now: int | None = None) -> dict:
    now = int(now or time.time()); cutoff = now - seconds
    with _availability_lock:
        data = dict(_availability)
        rows = [dict(row) for row in data.get("downtimes", [])]
    first_seen = int(data.get("first_seen") or now)
    known_start = max(cutoff, first_seen)
    clipped = []
    downtime = 0
    for row in rows:
        start = max(cutoff, int(row.get("start", 0)))
        end = min(now, int(row.get("end", 0)))
        if end <= start:
            continue
        clipped.append({"start": start, "end": end, "seconds": end - start, "reason": row.get("reason", "offline")})
        if end > known_start:
            downtime += max(0, end - max(start, known_start))
    known = max(0, now - known_start)
    downtime = min(downtime, known)
    online = max(0, known - downtime)
    unknown = max(0, known_start - cutoff)
    return {
        "known_since": first_seen,
        "online_seconds": online,
        "downtime_seconds": downtime,
        "unknown_seconds": unknown,
        "uptime_percent": round(online / known * 100, 3) if known else None,
        "downtimes": clipped,
        "current_online": True,
        "boot_time": int(data.get("boot_time") or _boot_time()),
    }


def _compact_history(rows: list[dict], now: int) -> list[dict]:
    """Keep 10 s detail for 24 h, 5 min detail to 7 d, 30 min detail to 90 d."""
    cutoff = now - HISTORY_RETENTION_SECONDS
    recent_cutoff = now - HISTORY_RAW_SECONDS
    week_cutoff = now - 7 * 86400
    kept: list[dict] = []
    buckets: dict[tuple[int, int], dict] = {}
    for row in rows:
        t = int(row.get("t", 0))
        if t < cutoff:
            continue
        if t >= recent_cutoff:
            kept.append(row)
            continue
        size = 300 if t >= week_cutoff else 1800
        key = (size, t // size)
        buckets[key] = row
    kept.extend(buckets.values())
    kept.sort(key=lambda row: int(row.get("t", 0)))
    return kept


def load_history() -> None:
    global _history
    try:
        loaded = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        now = int(time.time())
        _history = _compact_history([item for item in loaded if isinstance(item, dict)], now)
        for item in _history:
            if "power_measurement" not in item:
                item["estimated_watts"] = item.get("watts")
                item["watts"] = None
                item["power_measurement"] = "estimated"
    except (OSError, ValueError, json.JSONDecodeError):
        _history = []


def save_history() -> None:
    global _history
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with _history_lock:
            _history = _compact_history(_history, int(time.time()))
            payload = json.dumps(_history, separators=(",", ":"))
        temporary = HISTORY_FILE.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(HISTORY_FILE)
    except OSError:
        pass


def history_loop() -> None:
    writes = 0
    while True:
        try:
            sample = sample_metrics()
        except Exception as exc:
            print(f"Telemetry sample unavailable: {type(exc).__name__}", flush=True)
            time.sleep(10)
            continue
        with _history_lock:
            _history.append(sample)
        writes += 1
        # Persist detailed telemetry every minute and the tiny availability heartbeat
        # every minute. If power is lost, downtime start precision is therefore <=60 s.
        if writes % 6 == 0:
            save_history()
            touch_availability(sample["t"], persist=True)
        else:
            touch_availability(sample["t"], persist=False)
        time.sleep(10)


def _gap_points(downtimes: list[dict]) -> list[dict]:
    points = []
    for row in downtimes:
        # Synthetic null samples force every chart line to break over real downtime.
        for t in (row["start"] + 1, max(row["start"] + 1, row["end"] - 1)):
            points.append({"t": t, "cpu": None, "ram": None, "temp": None, "disk": None,
                           "rx": None, "tx": None, "watts": None,
                           "power_measurement": "offline", "offline": True})
    return points


def history_payload(seconds: int) -> dict:
    seconds = max(900, min(90 * 86400, seconds))
    now = int(time.time()); cutoff = now - seconds
    with _history_lock:
        points = [item.copy() for item in _history if item.get("t", 0) >= cutoff]
    availability = availability_payload(seconds, now)
    energy = measured_energy(points)
    measured = [p["watts"] for p in points if p.get("power_measurement") == "measured" and p.get("watts") is not None]
    energy["peak_watts"] = max(measured) if measured else None
    if len(points) > 240:
        stride = (len(points) + 239) // 240
        latest = points[-1]
        points = points[::stride]
        if points[-1]["t"] != latest["t"]:
            points.append(latest)
    points.extend(_gap_points(availability["downtimes"]))
    points.sort(key=lambda item: item["t"])
    return {"range": seconds, "points": points, "sample_seconds": 10,
            "energy": energy, "availability": availability}


def auth_config() -> dict:
    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        if all(data.get(key) for key in ("username", "salt", "hash")):
            return data
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {}


def password_digest(salt: str, password: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def service_state(unit: str) -> str:
    code, output = run(["systemctl", "is-active", unit], 3)
    return output if output else ("inactive" if code else "active")


def docker_containers() -> list[dict]:
    code, output = run([
        "docker", "ps", "-a", "--format",
        "{{json .}}",
    ], 6)
    if code != 0:
        return []
    containers = []
    for line in output.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        containers.append({
            "name": row.get("Names", "container"),
            "image": row.get("Image", ""),
            "status": row.get("Status", "unknown"),
            "state": row.get("State", "unknown"),
            "ports": row.get("Ports", ""),
        })
    return containers


def livevault_health() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {"ok": False}


def recent_actions() -> list[str]:
    try:
        lines = ACTION_LOG.read_text(encoding="utf-8").splitlines()
        return lines[-20:][::-1]
    except OSError:
        return []


def state() -> dict:
    uptime = float(read_text("/proc/uptime", "0").split()[0])
    mem = memory()
    containers = docker_containers()
    pihole = pihole_runtime.status()
    docker = service_state("docker.service")
    cpu = cpu_percent()
    network = network_totals()
    data_disk = disk("/mnt/livevault-nvme" if Path("/etc/openastro-internal-runtime-ready").exists() else "/data")
    share_disk = disk("/share")
    power = power_state()
    media = media_center.status()
    power["estimated_watts"] = estimated_watts(cpu, network, data_disk["mounted"], power["wifi_radio"])
    power.update(read_input_power())
    return {
        "timestamp": int(time.time()),
        "host": {
            "name": read_text("/etc/hostname", "openastro"),
            "uptime": int(uptime),
            "cpu_percent": cpu,
            "cpu_count": os.cpu_count() or 1,
            "load": [round(x, 2) for x in os.getloadavg()],
            "temperature": temperature(),
            "memory": mem,
            "network": network,
        },
        "storage": {
            "root": disk("/"),
            "buffer": disk("/var/lib/livevault-buffer"),
            "data": data_disk,
            "share": share_disk,
            "data_present": Path(f"/dev/disk/by-uuid/{DATA_UUID}").exists(),
            "share_present": Path(f"/dev/disk/by-uuid/{SHARE_UUID}").exists(),
        },
        "services": {
            "docker": docker,
            "tailscale": service_state("tailscaled.service"),
            "backup": service_state("livevault-backup.timer"),
            "pihole": pihole["service"],
        },
        "power": power,
        "media": media,
        "pihole": pihole,
        "containers": containers,
        "livevault": livevault_health(),
        "interfaces": [
            {"name": "LiveVault", "detail": "Registrazioni e archivio", "url": "https://openastro.tailf2871c.ts.net/", "available": True},
            {"name": "Coolify", "detail": "Deploy e container · richiede Tailscale", "url": "https://openastro.tailf2871c.ts.net:10000/", "available": docker == "active"},
            {"name": "Pi-hole", "detail": "DNS e blocco pubblicità · solo LAN", "url": pihole["lan"]["admin_url"], "available": pihole["installed"] and pihole["dns_online"]},
            {"name": "GitHub", "detail": "Codice LiveVault", "url": "https://github.com/astropuzzo/LiveVault-Test", "available": True},
            {"name": "Tailscale", "detail": "Rete privata", "url": "https://login.tailscale.com/admin/machines", "available": True},
        ],
        "actions": recent_actions(),
        "control_uptime": int(time.time() - STARTED_AT),
        "version": "3.0.0",
    }


def cached_state() -> dict:
    """Share expensive probes across viewers, never session-specific fields."""
    global _state_cache, _state_cached_at
    with _state_lock:
        if not _state_cache or time.monotonic() - _state_cached_at >= 5:
            _state_cache = state()
            _state_cached_at = time.monotonic()
        return copy.deepcopy(_state_cache)


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenAstroControl/3.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def send_json(self, payload: dict, status: int = 200, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        compressed = "gzip" in self.headers.get("Accept-Encoding", "") and len(body) >= 1000
        if compressed:
            body = gzip.compress(body, compresslevel=4)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_media_file(self, info: dict, *, download: bool = False) -> None:
        target = info["path"]
        size = int(info["size"])
        start, end = 0, max(0, size - 1)
        status = HTTPStatus.OK
        header = self.headers.get("Range", "").strip()
        if header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
            if not match or not size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            left, right = match.groups()
            if left:
                start = int(left); end = int(right) if right else size - 1
            elif right:
                length = min(size, int(right)); start, end = size - length, size - 1
            if start < 0 or end < start or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1); status = HTTPStatus.PARTIAL_CONTENT
        length = 0 if size == 0 else end - start + 1
        stream_id = None
        if not download and info.get("uuid") and info.get("relative"):
            stream_id = media_center.stream_begin(info, self.client_key(), start, end)
        disposition = "attachment" if download else "inline"
        ascii_name = re.sub(r'[^A-Za-z0-9._ -]', '_', info["name"]) or "media"
        encoded = quote(info["name"], safe="")
        self.send_response(status)
        self.send_header("Content-Type", info["mime"])
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Disposition", f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}")
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if not length:
            return
        try:
            with target.open("rb") as handle:
                handle.seek(start); remaining = length
                while remaining:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk: break
                    self.wfile.write(chunk); remaining -= len(chunk)
                    if stream_id:
                        media_center.stream_touch(stream_id, len(chunk))
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            if stream_id:
                media_center.stream_end(stream_id)

    def read_payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 4096:
            raise ValueError("Invalid payload size")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object")
        return payload

    def client_key(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        return forwarded or self.client_address[0]

    def authenticated_session(self) -> dict | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get("openastro_session")
        if not morsel:
            return None
        now = time.time()
        with _session_lock:
            expired = [token for token, item in _sessions.items() if item["expires"] < now]
            for token in expired:
                _sessions.pop(token, None)
            session = _sessions.get(morsel.value)
            if session:
                session["expires"] = now + 2592000
            return session

    def require_session(self) -> dict | None:
        session = self.authenticated_session()
        if not session:
            self.send_json({"ok": False, "error": "Autenticazione richiesta."}, 401)
        return session

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/session":
            self.send_json({"authenticated": self.authenticated_session() is not None})
            return
        if path == "/api/state":
            session = self.require_session()
            if not session:
                return
            payload = cached_state()
            payload["csrf"] = session["csrf"]
            self.send_json(payload)
            return
        if path == "/api/pihole/apple.mobileconfig":
            if not self.require_session():
                return
            query = parse_qs(parsed.query)
            device_id = str(query.get("id", [""])[0] or "")
            try:
                body = remote_dns.apple_profile(device_id or None)
            except (ValueError, KeyError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 409)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-apple-aspen-config")
            self.send_header("Content-Disposition", 'attachment; filename="OpenAstro-Pi-hole.mobileconfig"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/pihole/connection":
            if not self.require_session():
                return
            query = parse_qs(parsed.query)
            device_id = str(query.get("id", [""])[0] or "")
            self.send_json(remote_dns.connection(device_id or None))
            return
        if path == "/api/pihole/devices":
            if not self.require_session():
                return
            self.send_json(remote_dns.list_devices())
            return
        if path == "/api/pihole/status":
            if not self.require_session():
                return
            self.send_json(pihole_runtime.status())
            return
        if path == "/api/media/credentials":
            if not self.require_session(): return
            self.send_json(media_center.credentials())
            return
        if path == "/api/media/list":
            if not self.require_session(): return
            query = parse_qs(parsed.query)
            try:
                self.send_json(media_center.list_directory(str(query.get("uuid", [""])[0]), str(query.get("path", [""])[0])))
            except (ValueError, FileNotFoundError, NotADirectoryError, PermissionError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        if path == "/api/media/file":
            if not self.require_session(): return
            query = parse_qs(parsed.query)
            try:
                info = media_center.file_info(str(query.get("uuid", [""])[0]), str(query.get("path", [""])[0]))
                self.send_media_file(info, download=query.get("download", ["0"])[0] == "1")
            except (ValueError, FileNotFoundError, PermissionError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        if path == "/api/media/library":
            if not self.require_session(): return
            query = parse_qs(parsed.query)
            try:
                self.send_json(media_center.library_summary(str(query.get("uuid", [""])[0]), force=query.get("force", ["0"])[0] == "1"))
            except (ValueError, FileNotFoundError, PermissionError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        if path == "/api/media/probe":
            if not self.require_session(): return
            query = parse_qs(parsed.query)
            try:
                self.send_json(media_center.probe_file(str(query.get("uuid", [""])[0]), str(query.get("path", [""])[0])))
            except (ValueError, FileNotFoundError, PermissionError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        if path == "/api/media/thumbnail":
            if not self.require_session(): return
            query = parse_qs(parsed.query)
            try:
                self.send_media_file(media_center.thumbnail_info(str(query.get("uuid", [""])[0]), str(query.get("path", [""])[0])), download=False)
            except (ValueError, FileNotFoundError, PermissionError) as exc:
                self.send_error(HTTPStatus.NOT_FOUND, str(exc))
            return
        if path == "/api/media/play-plan":
            if not self.require_session(): return
            query = parse_qs(parsed.query)
            try:
                audio_raw = str(query.get("audio_stream", [""])[0]).strip()
                audio_stream = int(audio_raw) if audio_raw else None
                self.send_json(media_streaming.playback_plan(str(query.get("uuid", [""])[0]), str(query.get("path", [""])[0]), audio_stream=audio_stream))
            except (ValueError, FileNotFoundError, PermissionError, RuntimeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        if path == "/api/media/hls/manifest":
            if not self.require_session(): return
            query = parse_qs(parsed.query)
            try:
                body = media_streaming.manifest_text(str(query.get("token", [""])[0])).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
            except (ValueError, FileNotFoundError, PermissionError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        if path == "/api/media/hls/segment":
            if not self.require_session(): return
            query = parse_qs(parsed.query)
            try:
                self.send_media_file(media_streaming.segment_info(str(query.get("token", [""])[0]), str(query.get("name", [""])[0])), download=False)
            except (ValueError, FileNotFoundError, PermissionError) as exc:
                self.send_error(HTTPStatus.NOT_FOUND, str(exc))
            return
        if path == "/api/media/subtitle":
            if not self.require_session(): return
            query = parse_qs(parsed.query)
            try:
                stream_raw = str(query.get("stream", [""])[0]).strip()
                stream_index = int(stream_raw) if stream_raw else None
                self.send_media_file(media_streaming.subtitle_info(str(query.get("uuid", [""])[0]), str(query.get("path", [""])[0]), stream_index=stream_index), download=False)
            except (ValueError, FileNotFoundError, PermissionError) as exc:
                self.send_error(HTTPStatus.NOT_FOUND, str(exc))
            return
        if path == "/api/media/streaming-diagnostics":
            if not self.require_session(): return
            self.send_json(media_streaming.diagnostics())
            return
        if path == "/api/media/home":
            if not self.require_session(): return
            query = parse_qs(parsed.query)
            uuid = str(query.get("uuid", [""])[0]).strip() or None
            try:
                payload = media_center.home(uuid)
                hls_jobs = media_streaming.hls_jobs()
                payload["hls_streams"] = hls_jobs
                payload["active_streams"] = list(payload.get("active_streams") or []) + [
                    {"id": "hls:" + job["token"], "uuid": job.get("uuid", ""), "path": job.get("path", ""),
                     "name": job.get("name", ""), "client": job.get("client", ""), "started": job.get("started", 0),
                     "last_seen": time.time(), "bytes_sent": 0, "seconds": job.get("seconds", 0), "mode": job.get("mode", "hls")}
                    for job in hls_jobs
                ]
                self.send_json(payload)
            except (ValueError, FileNotFoundError, PermissionError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        if path == "/api/media/catalog":
            if not self.require_session(): return
            query = parse_qs(parsed.query)
            try:
                self.send_json(media_center.catalog(
                    str(query.get("uuid", [""])[0]),
                    category=str(query.get("category", ["all"])[0]),
                    query=str(query.get("q", [""])[0]),
                    sort=str(query.get("sort", ["recent"])[0]),
                    limit=int(query.get("limit", ["200"])[0]),
                    offset=int(query.get("offset", ["0"])[0]),
                    favorite=query.get("favorite", ["0"])[0] == "1",
                ))
            except (ValueError, FileNotFoundError, PermissionError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        if path == "/api/media/streams":
            if not self.require_session(): return
            self.send_json({"ok": True, "streams": media_center.active_streams()})
            return
        if path == "/api/media/diagnostics":
            if not self.require_session(): return
            self.send_json(media_center.diagnostics())
            return
        if path == "/api/history":
            if not self.require_session():
                return
            query = parse_qs(parsed.query)
            try:
                seconds = int(query.get("range", ["3600"])[0])
            except ValueError:
                seconds = 3600
            self.send_json(history_payload(seconds))
            return
        if path == "/healthz":
            self.send_json({"ok": True, "version": "3.0.0"})
            return
        relative = "index.html" if path == "/" else path.lstrip("/")
        target = (STATIC / relative).resolve()
        if STATIC.resolve() not in target.parents and target != STATIC.resolve():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        compressed = "gzip" in self.headers.get("Accept-Encoding", "") and len(body) >= 1000
        if compressed:
            body = gzip.compress(body, compresslevel=4)
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; script-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; worker-src 'self' blob:; frame-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path == "/api/login":
            client = self.client_key()
            now = time.time()
            attempts = [stamp for stamp in _login_attempts.get(client, []) if stamp > now - 900]
            _login_attempts[client] = attempts
            if len(attempts) >= 6:
                self.send_json({"ok": False, "error": "Troppi tentativi. Riprova tra 15 minuti."}, 429)
                return
            try:
                payload = self.read_payload()
            except (ValueError, json.JSONDecodeError):
                self.send_json({"ok": False, "error": "Richiesta non valida."}, 400)
                return
            config = auth_config()
            supplied = password_digest(config.get("salt", ""), str(payload.get("password", "")))
            valid = bool(config) and hmac.compare_digest(str(payload.get("username", "")), config["username"]) and hmac.compare_digest(supplied, config["hash"])
            if not valid:
                attempts.append(now)
                self.send_json({"ok": False, "error": "Credenziali non valide."}, 401)
                return
            _login_attempts.pop(client, None)
            token = secrets.token_urlsafe(40)
            with _session_lock:
                _sessions[token] = {"expires": now + 2592000, "csrf": secrets.token_urlsafe(32)}
            cookie = f"openastro_session={token}; Path=/; Max-Age=2592000; HttpOnly; Secure; SameSite=Strict"
            self.send_json({"ok": True}, headers={"Set-Cookie": cookie})
            return
        if self.path == "/api/logout":
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            if cookie.get("openastro_session"):
                with _session_lock:
                    _sessions.pop(cookie["openastro_session"].value, None)
            self.send_json({"ok": True}, headers={"Set-Cookie": "openastro_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict"})
            return
        if self.path in {"/api/pihole/remote/test", "/api/pihole/remote/rotate"}:
            session = self.require_session()
            if not session:
                return
            if self.headers.get("X-CSRF-Token") != session["csrf"]:
                self.send_json({"ok": False, "error": "Sessione scaduta: ricarica la pagina."}, 403)
                return
            if not _action_lock.acquire(blocking=False):
                self.send_json({"ok": False, "error": "Operazione già in corso."}, 409)
                return
            try:
                result = remote_dns.probe() if self.path.endswith('/test') else remote_dns.rotate()
                self.send_json(result)
            finally:
                _action_lock.release()
            return
        if self.path in {
            "/api/pihole/devices/create",
            "/api/pihole/devices/enable",
            "/api/pihole/devices/disable",
            "/api/pihole/devices/regenerate",
            "/api/pihole/devices/delete",
        }:
            session = self.require_session()
            if not session:
                return
            if self.headers.get("X-CSRF-Token") != session["csrf"]:
                self.send_json({"ok": False, "error": "Sessione scaduta: ricarica la pagina."}, 403)
                return
            try:
                payload = self.read_payload()
                if self.path.endswith("/create"):
                    result = remote_dns.create_device(str(payload.get("name", "")), str(payload.get("platform", "generic")))
                else:
                    device_id = str(payload.get("id", ""))
                    if not device_id:
                        raise ValueError("Dispositivo non specificato.")
                    if self.path.endswith("/enable"):
                        result = remote_dns.set_device_enabled(device_id, True)
                    elif self.path.endswith("/disable"):
                        result = remote_dns.set_device_enabled(device_id, False)
                    elif self.path.endswith("/regenerate"):
                        result = remote_dns.regenerate_device(device_id)
                    else:
                        result = remote_dns.delete_device(device_id)
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                message = exc.args[0] if getattr(exc, "args", None) else "Richiesta non valida."
                self.send_json({"ok": False, "error": str(message)}, 400)
                return
            with _state_lock:
                _state_cache.clear()
            self.send_json(result)
            return

        pihole_actions = {
            "/api/pihole/blocking/enable": "pihole_enable",
            "/api/pihole/blocking/disable": "pihole_disable",
            "/api/pihole/restart": "restart_pihole",
            "/api/pihole/gravity": "pihole_gravity",
        }
        if self.path in pihole_actions:
            session = self.require_session()
            if not session:
                return
            if self.headers.get("X-CSRF-Token") != session["csrf"]:
                self.send_json({"ok": False, "error": "Sessione scaduta: ricarica la pagina."}, 403)
                return
            action = pihole_actions[self.path]
            if not _action_lock.acquire(blocking=False):
                self.send_json({"ok": False, "error": "Un’operazione è già in corso."}, 409)
                return
            try:
                code, output = run(["sudo", "-n", "/usr/local/sbin/openastro-action", action], 290)
                with _state_lock:
                    _state_cache.clear()
            finally:
                _action_lock.release()
            self.send_json({"ok": code == 0, "action": action, "message": output[-1200:]}, 200 if code == 0 else 500)
            return

        if self.path in {"/api/media/progress", "/api/media/favorite", "/api/media/hls/start", "/api/media/hls/stop"}:
            session = self.require_session()
            if not session:
                return
            if self.headers.get("X-CSRF-Token") != session["csrf"]:
                self.send_json({"ok": False, "error": "Sessione scaduta: ricarica la pagina."}, 403)
                return
            try:
                payload = self.read_payload()
                uuid = str(payload.get("uuid", ""))
                path = str(payload.get("path", ""))
                if self.path == "/api/media/progress":
                    result = media_center.update_progress(uuid, path, float(payload.get("position", 0)), float(payload.get("duration", 0)), client=self.client_key())
                elif self.path == "/api/media/favorite":
                    result = media_center.set_favorite(uuid, path, bool(payload.get("favorite", False)))
                elif self.path == "/api/media/hls/start":
                    audio_raw = payload.get("audio_stream")
                    audio_stream = int(audio_raw) if audio_raw is not None and str(audio_raw) != "" else None
                    result = media_streaming.start_hls(uuid, path, client=self.client_key(), position=float(payload.get("position", 0)), audio_stream=audio_stream)
                else:
                    result = media_streaming.stop_hls(str(payload.get("token", "")))
                self.send_json(result)
            except RuntimeError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 503)
            except (ValueError, FileNotFoundError, PermissionError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        if self.path != "/api/action":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        session = self.require_session()
        if not session:
            return
        if self.headers.get("X-CSRF-Token") != session["csrf"]:
            self.send_json({"ok": False, "error": "Sessione scaduta: ricarica la pagina."}, 403)
            return
        try:
            payload = self.read_payload()
        except (ValueError, json.JSONDecodeError):
            self.send_json({"ok": False, "error": "Richiesta non valida."}, 400)
            return
        action = str(payload.get("action", ""))
        if action not in ALLOWED_ACTIONS:
            self.send_json({"ok": False, "error": "Azione non consentita."}, 400)
            return
        command = ["sudo", "-n", "/usr/local/sbin/openastro-action", action]
        if action in {"media_mount", "media_eject"}:
            uuid = str(payload.get("uuid", ""))
            if not media_center.valid_uuid(uuid):
                self.send_json({"ok": False, "error": "UUID media non valido."}, 400)
                return
            command.append(uuid)
        if action == "power_profile":
            profile = str(payload.get("profile", ""))
            wifi = str(payload.get("wifi", ""))
            if profile not in {"eco", "balanced", "performance", "max"} or wifi not in {"on", "off"}:
                self.send_json({"ok": False, "error": "Profilo energetico non valido."}, 400)
                return
            command.extend([profile, wifi])
        if not _action_lock.acquire(blocking=False):
            self.send_json({"ok": False, "error": "Un’operazione è già in corso."}, 409)
            return
        try:
            code, output = run(command, 290)
            with _state_lock:
                _state_cache.clear()
        finally:
            _action_lock.release()
        self.send_json({"ok": code == 0, "action": action, "message": output[-1200:]}, 200 if code == 0 else 500)


if __name__ == "__main__":
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    load_history()
    load_availability()
    threading.Thread(target=history_loop, name="telemetry", daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"OpenAstro Control listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
