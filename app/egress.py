from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

HTTP_PROXY_URL = "http://127.0.0.1:25345"
SOCKS5_PROXY_URL = "socks5h://127.0.0.1:25344"
INFO_URL = "http://127.0.0.1:25346/readyz"
PROVIDER_HOSTS = ("chaturbate.com",)


class RegionalEgressError(RuntimeError):
    pass


def _runtime():
    # Local import avoids a settings_store <-> egress import cycle.
    from .settings_store import runtime

    return runtime()


def _host_matches(hostname: str, allowed: tuple[str, ...]) -> bool:
    host = str(hostname or "").lower().rstrip(".")
    return any(host == candidate or host.endswith(f".{candidate}") for candidate in allowed)


def _clean_value(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not value or any(ord(char) < 32 for char in value):
        raise RegionalEgressError(f"{label} WireGuard non valido")
    return value


def sanitize_wireguard_config(raw: str) -> str:
    """Keep only the WireGuard fields wireproxy needs.

    Proton's downloaded .conf is a normal WireGuard configuration.  Rebuilding
    it from an allow-list means wg-quick shell directives such as PostUp can
    never enter the userspace proxy configuration.
    """
    text = str(raw or "").strip()
    if not text:
        raise RegionalEgressError("Configurazione WireGuard mancante")
    if len(text) > 12_000:
        raise RegionalEgressError("Configurazione WireGuard troppo grande")

    sections: dict[str, dict[str, str]] = {"interface": {}, "peer": {}}
    current = ""
    allowed = {
        "interface": {"address", "privatekey", "dns", "mtu"},
        "peer": {"publickey", "presharedkey", "endpoint", "allowedips", "persistentkeepalive"},
    }
    display = {
        "address": "Address",
        "privatekey": "PrivateKey",
        "dns": "DNS",
        "mtu": "MTU",
        "publickey": "PublicKey",
        "presharedkey": "PresharedKey",
        "endpoint": "Endpoint",
        "allowedips": "AllowedIPs",
        "persistentkeepalive": "PersistentKeepalive",
    }

    for original in text.splitlines():
        line = original.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            if section not in sections:
                raise RegionalEgressError(f"Sezione WireGuard non supportata: {section}")
            current = section
            continue
        if not current or "=" not in line:
            raise RegionalEgressError("Formato WireGuard non valido")
        key, value = (part.strip() for part in line.split("=", 1))
        lowered = key.lower()
        if lowered not in allowed[current]:
            # Proton configs should not need wg-quick routing hooks. Reject them
            # rather than silently allowing executable or routing directives.
            raise RegionalEgressError(f"Campo WireGuard non supportato: {key}")
        sections[current][lowered] = _clean_value(value, key)

    interface = sections["interface"]
    peer = sections["peer"]
    for key in ("address", "privatekey"):
        if key not in interface:
            raise RegionalEgressError(f"WireGuard: {display[key]} mancante")
    for key in ("publickey", "endpoint", "allowedips"):
        if key not in peer:
            raise RegionalEgressError(f"WireGuard: {display[key]} mancante")

    try:
        addresses = [ipaddress.ip_interface(item.strip()) for item in interface["address"].split(",")]
    except ValueError as exc:
        raise RegionalEgressError("WireGuard: Address non valido") from exc
    if not addresses:
        raise RegionalEgressError("WireGuard: Address mancante")

    try:
        networks = [ipaddress.ip_network(item.strip(), strict=False) for item in peer["allowedips"].split(",")]
    except ValueError as exc:
        raise RegionalEgressError("WireGuard: AllowedIPs non valido") from exc
    if not any(network.version == 4 and network.prefixlen == 0 for network in networks):
        raise RegionalEgressError("WireGuard: serve AllowedIPs 0.0.0.0/0 per l'egress Internet")

    endpoint = peer["endpoint"]
    endpoint_match = re.fullmatch(r"(?:\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+):(\d{1,5})", endpoint)
    if not endpoint_match or not (1 <= int(endpoint_match.group(1)) <= 65535):
        raise RegionalEgressError("WireGuard: Endpoint non valido")

    if "mtu" in interface:
        try:
            mtu = int(interface["mtu"])
        except ValueError as exc:
            raise RegionalEgressError("WireGuard: MTU non valido") from exc
        if not 576 <= mtu <= 9000:
            raise RegionalEgressError("WireGuard: MTU fuori intervallo")
    if "persistentkeepalive" in peer:
        try:
            keepalive = int(peer["persistentkeepalive"])
        except ValueError as exc:
            raise RegionalEgressError("WireGuard: PersistentKeepalive non valido") from exc
        if not 0 <= keepalive <= 65535:
            raise RegionalEgressError("WireGuard: PersistentKeepalive non valido")

    lines = ["[Interface]"]
    for key in ("address", "privatekey", "dns", "mtu"):
        if key in interface:
            lines.append(f"{display[key]} = {interface[key]}")
    lines += ["", "[Peer]"]
    for key in ("publickey", "presharedkey", "endpoint", "allowedips", "persistentkeepalive"):
        if key in peer:
            lines.append(f"{display[key]} = {peer[key]}")
    if "persistentkeepalive" not in peer:
        lines.append("PersistentKeepalive = 25")
    return "\n".join(lines) + "\n"


def wireproxy_config(raw: str) -> str:
    return (
        sanitize_wireguard_config(raw)
        + "\n[Socks5]\nBindAddress = 127.0.0.1:25344\n"
        + "\n[http]\nBindAddress = 127.0.0.1:25345\n"
    )


def subprocess_proxy_env(proxy_url: str = "") -> dict[str, str]:
    env = dict(os.environ)
    if proxy_url:
        # FFmpeg's HTTP/TLS protocols honor the lowercase http_proxy variable,
        # including HTTPS CONNECT tunnels. Keep both cases for other tools.
        env.update(
            {
                "http_proxy": proxy_url,
                "https_proxy": proxy_url,
                "HTTP_PROXY": proxy_url,
                "HTTPS_PROXY": proxy_url,
            }
        )
        no_proxy = env.get("no_proxy", env.get("NO_PROXY", ""))
        local = ["127.0.0.1", "localhost"]
        merged = ",".join([*(item for item in no_proxy.split(",") if item), *local])
        env["no_proxy"] = merged
        env["NO_PROXY"] = merged
    return env


class RegionalEgressManager:
    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._config_path: Path | None = None
        self.last_error = ""
        self.exit_ip = ""

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def configured(self) -> bool:
        return bool(str(_runtime().regional_egress_wireguard_config or "").strip())

    def public_status(self) -> dict[str, object]:
        cfg = _runtime()
        return {
            "regional_egress_enabled": bool(cfg.regional_egress_enabled),
            "regional_egress_name": str(cfg.regional_egress_name or "VPN")[:80],
            "regional_egress_configured": self.configured(),
            "regional_egress_running": self.running,
            "regional_egress_error": self.last_error[-500:],
            "regional_egress_exit_ip": self.exit_ip,
        }

    async def _stop_locked(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if self._config_path is not None:
            try:
                self._config_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._config_path = None

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def start(self) -> None:
        async with self._lock:
            await self._stop_locked()
            self.last_error = ""
            self.exit_ip = ""
            cfg = _runtime()
            if not cfg.regional_egress_enabled:
                return
            raw = str(cfg.regional_egress_wireguard_config or "").strip()
            if not raw:
                self.last_error = "VPN abilitata ma configurazione WireGuard mancante"
                return
            binary = shutil.which("wireproxy")
            if not binary:
                self.last_error = "wireproxy non disponibile nella build"
                return
            try:
                rendered = wireproxy_config(raw)
            except RegionalEgressError as exc:
                self.last_error = str(exc)
                return

            fd, name = tempfile.mkstemp(prefix="livevault-wireproxy-", suffix=".conf")
            os.close(fd)
            path = Path(name)
            path.write_text(rendered, encoding="utf-8")
            path.chmod(0o600)
            self._config_path = path
            process = await asyncio.create_subprocess_exec(
                binary,
                "-c",
                str(path),
                "-i",
                "127.0.0.1:25346",
                "-s",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            self.process = process

            # Wait only for the local proxy/health sockets. The first provider
            # request can complete the WireGuard handshake without blocking app boot.
            for _ in range(25):
                await asyncio.sleep(0.12)
                if process.returncode is not None:
                    detail = b""
                    if process.stderr is not None:
                        detail = await process.stderr.read()
                    self.last_error = (detail.decode(errors="replace").strip() or "wireproxy terminato")[-500:]
                    await self._stop_locked()
                    return
                try:
                    response = await asyncio.to_thread(requests.get, INFO_URL, timeout=0.35)
                    if response.status_code < 500:
                        return
                except requests.RequestException:
                    continue
            self.last_error = "wireproxy avviato ma health endpoint non pronto"
            await self._stop_locked()

    async def reload(self) -> None:
        await self.start()

    def proxy_for_platform(self, platform: str) -> str:
        if str(platform or "").lower() != "chaturbate":
            return ""
        cfg = _runtime()
        if not cfg.regional_egress_enabled:
            return ""
        if not self.configured():
            raise RegionalEgressError("VPN abilitata ma configurazione WireGuard mancante")
        if not self.running:
            raise RegionalEgressError(self.last_error or "VPN regionale non connessa")
        return HTTP_PROXY_URL

    def proxy_for_url(self, url: str) -> str:
        try:
            host = urlparse(str(url or "")).hostname or ""
        except ValueError:
            return ""
        if _host_matches(host, PROVIDER_HOSTS):
            return self.proxy_for_platform("chaturbate")
        return ""

    async def test_connection(self) -> dict[str, object]:
        proxy = self.proxy_for_platform("chaturbate")
        proxies = {"http": proxy, "https": proxy}
        try:
            response = await asyncio.to_thread(
                requests.get,
                "https://api.ipify.org",
                proxies=proxies,
                timeout=12,
            )
            response.raise_for_status()
            candidate = response.text.strip()
            ipaddress.ip_address(candidate)
        except Exception as exc:
            self.last_error = f"Test VPN fallito: {exc}"[-500:]
            raise RegionalEgressError(self.last_error) from exc
        self.exit_ip = candidate
        self.last_error = ""
        return {**self.public_status(), "ok": True, "exit_ip": candidate}


manager = RegionalEgressManager()


def regional_egress_proxy_for_platform(platform: str) -> str:
    return manager.proxy_for_platform(platform)


def regional_egress_proxy_for_url(url: str) -> str:
    return manager.proxy_for_url(url)
