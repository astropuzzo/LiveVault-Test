#!/usr/bin/env python3
from __future__ import annotations

import json
import copy
import gzip
import hashlib
import hmac
import mimetypes
import os
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
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
HOST = os.environ.get("OPENASTRO_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPENASTRO_PORT", "9090"))
STARTED_AT = time.time()
ACTION_LOG = Path("/var/log/openastro-control-actions.log")
STATE_DIR = Path("/var/lib/openastro-control")
HISTORY_FILE = STATE_DIR / "history.json"
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
    "power_profile",
    "reboot",
}

_cpu_lock = threading.Lock()
_cpu_last: tuple[int, int] | None = None
_network_lock = threading.Lock()
_network_last: tuple[float, int, int] | None = None
_history_lock = threading.Lock()
_history: list[dict] = []
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


def read_input_power() -> dict:
    """Read only the ASIAIR Plus CM4 input ADC; never claim power-output GPIOs.

    ADS1015 at 0x4b on the CSI I2C mux. Channel mapping and scaling follow
    indilib/indi-3rdparty indi-asi-power/asipower.h. This is DC input power,
    including attached loads, not AC wall power or CPU-only consumption.
    """
    result = {"watts": None, "input_volts": None, "input_amps": None,
              "measurement": "unavailable", "power_source": "ASIAIR ADS1015",
              "power_scope": "DC input", "power_sample_at": None}
    try:
        import fcntl
        with open("/dev/i2c-10", "r+b", buffering=0) as bus:
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
        result.update(watts=round(volts * amps, 3), input_volts=round(volts, 3),
                      input_amps=round(amps, 3), measurement="measured",
                      power_sample_at=time.time())
    except (OSError, ValueError, ImportError):
        pass
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


def load_history() -> None:
    global _history
    try:
        loaded = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        cutoff = int(time.time()) - 86400
        _history = [item for item in loaded if isinstance(item, dict) and item.get("t", 0) >= cutoff]
        for item in _history:
            if "power_measurement" not in item:
                item["estimated_watts"] = item.get("watts")
                item["watts"] = None
                item["power_measurement"] = "estimated"
    except (OSError, ValueError, json.JSONDecodeError):
        _history = []


def save_history() -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with _history_lock:
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
        cutoff = sample["t"] - 86400
        with _history_lock:
            _history.append(sample)
            while _history and _history[0].get("t", 0) < cutoff:
                _history.pop(0)
        writes += 1
        if writes % 6 == 0:
            save_history()
        time.sleep(10)


def history_payload(seconds: int) -> dict:
    seconds = max(900, min(86400, seconds))
    cutoff = int(time.time()) - seconds
    with _history_lock:
        points = [item.copy() for item in _history if item.get("t", 0) >= cutoff]
    energy = measured_energy(points)
    measured = [p["watts"] for p in points if p.get("power_measurement") == "measured" and p.get("watts") is not None]
    energy["peak_watts"] = max(measured) if measured else None
    if len(points) > 240:
        stride = (len(points) + 239) // 240
        latest = points[-1]
        points = points[::stride]
        if points[-1]["t"] != latest["t"]:
            points.append(latest)
    return {"range": seconds, "points": points, "sample_seconds": 10, "energy": energy}


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
    pihole = service_state("pihole-FTL.service")
    docker = service_state("docker.service")
    cpu = cpu_percent()
    network = network_totals()
    data_disk = disk("/data")
    share_disk = disk("/share")
    power = power_state()
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
            "data": data_disk,
            "share": share_disk,
            "data_present": Path(f"/dev/disk/by-uuid/{DATA_UUID}").exists(),
            "share_present": Path(f"/dev/disk/by-uuid/{SHARE_UUID}").exists(),
        },
        "services": {
            "docker": docker,
            "tailscale": service_state("tailscaled.service"),
            "backup": service_state("livevault-backup.timer"),
            "pihole": pihole,
        },
        "power": power,
        "containers": containers,
        "livevault": livevault_health(),
        "interfaces": [
            {"name": "LiveVault", "detail": "Registrazioni e archivio", "url": "https://openastro.tailf2871c.ts.net/", "available": True},
            {"name": "Coolify", "detail": "Deploy e container · richiede Tailscale", "url": "http://100.85.86.96:8000", "available": docker == "active"},
            {"name": "Pi-hole", "detail": "DNS e blocco pubblicità", "url": "http://100.85.86.96/admin/", "available": pihole == "active"},
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
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'")
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
            code, output = run(command, 175)
            with _state_lock:
                _state_cache.clear()
        finally:
            _action_lock.release()
        self.send_json({"ok": code == 0, "action": action, "message": output[-1200:]}, 200 if code == 0 else 500)


if __name__ == "__main__":
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    load_history()
    threading.Thread(target=history_loop, name="telemetry", daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"OpenAstro Control listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
