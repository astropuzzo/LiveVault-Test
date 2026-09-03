from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one match in {path}, got {count}: {old[:140]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, payload: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if marker in text:
        return
    target.write_text(text.rstrip() + "\n\n" + payload.rstrip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Runtime settings: encrypted WireGuard material + public status only.
# ---------------------------------------------------------------------------
replace_once(
    "app/settings_store.py",
    'SECRET_KEYS = {"gofile_token", "pixeldrain_api_key"}',
    'SECRET_KEYS = {"gofile_token", "pixeldrain_api_key", "regional_egress_wireguard_config"}',
)
replace_once(
    "app/settings_store.py",
    '''    recording_paused: bool = False\n    upload_paused: bool = False\n''',
    '''    recording_paused: bool = False\n    upload_paused: bool = False\n    regional_egress_enabled: bool = False\n    regional_egress_name: str = "Proton VPN"\n    regional_egress_wireguard_config: str = ""\n''',
)
replace_once(
    "app/settings_store.py",
    '''        "recording_paused": s.recording_paused,\n        "upload_paused": s.upload_paused,\n    }\n''',
    '''        "recording_paused": s.recording_paused,\n        "upload_paused": s.upload_paused,\n        **__import__("app.egress", fromlist=["manager"]).manager.public_status(),\n    }\n''',
)

# ---------------------------------------------------------------------------
# Provider routing. Chaturbate is fail-closed when regional egress is enabled.
# yt-dlp, browser metadata calls, ffprobe, and resolved CDN inputs share the
# same userspace WireGuard path.
# ---------------------------------------------------------------------------
replace_once(
    "app/source_providers.py",
    '''import requests\n\n\n@dataclass\nclass ProbeResult:\n''',
    '''import requests\n\nfrom .egress import (\n    regional_egress_proxy_for_platform,\n    regional_egress_proxy_for_url,\n    subprocess_proxy_env,\n)\n\n\n@dataclass\nclass ProbeResult:\n''',
)
replace_once(
    "app/source_providers.py",
    '''class ResolvedInput:\n    url: str\n    http_headers: dict[str, str]\n    kind: str\n''',
    '''class ResolvedInput:\n    url: str\n    http_headers: dict[str, str]\n    kind: str\n    proxy_url: str = ""\n''',
)
replace_once(
    "app/source_providers.py",
    '''    params = {\n        "quiet": quiet,\n        "no_warnings": quiet,\n        "skip_download": True,\n        "format": QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"]),\n        "noplaylist": True,\n        "socket_timeout": 20,\n        "retries": 2,\n        "logger": _QuietLogger(),\n    }\n    with yt_dlp.YoutubeDL(params) as ydl:\n''',
    '''    params = {\n        "quiet": quiet,\n        "no_warnings": quiet,\n        "skip_download": True,\n        "format": QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"]),\n        "noplaylist": True,\n        "socket_timeout": 20,\n        "retries": 2,\n        "logger": _QuietLogger(),\n    }\n    proxy_url = regional_egress_proxy_for_url(url)\n    if proxy_url:\n        params["proxy"] = proxy_url\n    with yt_dlp.YoutubeDL(params) as ydl:\n''',
)
replace_once(
    "app/source_providers.py",
    '''def _browser_get(url: str, headers: dict[str, str], timeout: float = 15.0):\n    try:\n        from curl_cffi import requests as curl_requests\n    except Exception:\n        return requests.get(url, headers=headers, timeout=timeout)\n    return curl_requests.get(url, headers=headers, timeout=timeout, impersonate="chrome")\n''',
    '''def _browser_get(url: str, headers: dict[str, str], timeout: float = 15.0):\n    proxy_url = regional_egress_proxy_for_url(url)\n    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None\n    try:\n        from curl_cffi import requests as curl_requests\n    except Exception:\n        return requests.get(url, headers=headers, timeout=timeout, proxies=proxies)\n    return curl_requests.get(\n        url, headers=headers, timeout=timeout, impersonate="chrome", proxies=proxies\n    )\n''',
)
replace_once(
    "app/source_providers.py",
    '''async def resolve_inputs(platform: str, slug: str, quality: str = "best") -> list[ResolvedInput]:\n    url = source_url(platform, slug)\n    info = await asyncio.to_thread(_extract, url, quality, quiet=False)\n''',
    '''async def resolve_inputs(platform: str, slug: str, quality: str = "best") -> list[ResolvedInput]:\n    url = source_url(platform, slug)\n    proxy_url = regional_egress_proxy_for_platform(platform)\n    info = await asyncio.to_thread(_extract, url, quality, quiet=False)\n''',
)
replace_once(
    "app/source_providers.py",
    '''            result.append(ResolvedInput(media_url, dict(fmt.get("http_headers") or {}), kind))\n''',
    '''            result.append(ResolvedInput(media_url, dict(fmt.get("http_headers") or {}), kind, proxy_url))\n''',
)
replace_once(
    "app/source_providers.py",
    '''        result.append(ResolvedInput(str(info["url"]), dict(info.get("http_headers") or {}), kind))\n''',
    '''        result.append(ResolvedInput(str(info["url"]), dict(info.get("http_headers") or {}), kind, proxy_url))\n''',
)
replace_once(
    "app/source_providers.py",
    '''    proc = await asyncio.create_subprocess_exec(\n        *cmd,\n        stdout=asyncio.subprocess.PIPE,\n        stderr=asyncio.subprocess.PIPE,\n    )\n''',
    '''    proc = await asyncio.create_subprocess_exec(\n        *cmd,\n        stdout=asyncio.subprocess.PIPE,\n        stderr=asyncio.subprocess.PIPE,\n        env=subprocess_proxy_env(item.proxy_url),\n    )\n''',
)

# ---------------------------------------------------------------------------
# Recorder: preserve the proxy when split LL-HLS is wrapped in a local master.
# FFmpeg receives proxy environment variables, avoiding protocol-private options
# on the local .m3u8 (the exact compatibility issue fixed in v2.8.9).
# ---------------------------------------------------------------------------
replace_once(
    "app/recorder.py",
    '''from .config import settings\nfrom .db import Source\nfrom .settings_store import runtime\n''',
    '''from .config import settings\nfrom .db import Source\nfrom .egress import subprocess_proxy_env\nfrom .settings_store import runtime\n''',
)
replace_once(
    "app/recorder.py",
    '''    return [ResolvedInput(str(manifest_path.resolve()), headers, "media")], manifest_path\n''',
    '''    proxy_url = video.proxy_url or audio.proxy_url\n    return [ResolvedInput(str(manifest_path.resolve()), headers, "media", proxy_url)], manifest_path\n''',
)
replace_once(
    "app/recorder.py",
    '''    process = await asyncio.create_subprocess_exec(\n        *cmd,\n        stdout=asyncio.subprocess.DEVNULL,\n        stderr=asyncio.subprocess.PIPE,\n        start_new_session=True,\n    )\n''',
    '''    proxy_urls = {item.proxy_url for item in inputs if item.proxy_url}\n    if len(proxy_urls) > 1:\n        raise RuntimeError("Input recorder con egress incompatibili")\n    proxy_url = next(iter(proxy_urls), "")\n    process = await asyncio.create_subprocess_exec(\n        *cmd,\n        stdout=asyncio.subprocess.DEVNULL,\n        stderr=asyncio.subprocess.PIPE,\n        start_new_session=True,\n        env=subprocess_proxy_env(proxy_url),\n    )\n''',
)

# ---------------------------------------------------------------------------
# API/lifecycle.
# ---------------------------------------------------------------------------
replace_once(
    "app/main.py",
    '''from .file_cleanup import cleanup_empty_parents, cleanup_orphan_videos, safe_unlink\nfrom .recorder import (\n''',
    '''from .file_cleanup import cleanup_empty_parents, cleanup_orphan_videos, safe_unlink\nfrom .egress import RegionalEgressError, manager as egress_manager, sanitize_wireguard_config\nfrom .recorder import (\n''',
)
replace_once(
    "app/main.py",
    '''    pixeldrain_api_key: str | None = Field(default=None, max_length=500)\n    clear_pixeldrain_api_key: bool = False\n''',
    '''    pixeldrain_api_key: str | None = Field(default=None, max_length=500)\n    clear_pixeldrain_api_key: bool = False\n    regional_egress_enabled: bool | None = None\n    regional_egress_name: str | None = Field(default=None, max_length=80)\n    regional_egress_wireguard_config: str | None = Field(default=None, max_length=12_000)\n    clear_regional_egress_wireguard_config: bool = False\n''',
)
replace_once(
    "app/main.py",
    '''    reload_runtime()\n    await manager.start()\n    yield\n    await manager.stop()\n''',
    '''    reload_runtime()\n    await egress_manager.start()\n    await manager.start()\n    yield\n    await manager.stop()\n    await egress_manager.stop()\n''',
)
replace_once(
    "app/main.py",
    '''@app.patch("/api/settings")\ndef patch_settings(body: SettingsPatch, request: Request):\n    require_auth(request)\n    updates = body.model_dump(exclude_none=True)\n    clear_gofile = updates.pop("clear_gofile_token", False)\n    clear_pixeldrain = updates.pop("clear_pixeldrain_api_key", False)\n''',
    '''@app.patch("/api/settings")\nasync def patch_settings(body: SettingsPatch, request: Request):\n    require_auth(request)\n    updates = body.model_dump(exclude_none=True)\n    clear_gofile = updates.pop("clear_gofile_token", False)\n    clear_pixeldrain = updates.pop("clear_pixeldrain_api_key", False)\n    clear_egress = updates.pop("clear_regional_egress_wireguard_config", False)\n    egress_changed = clear_egress or any(\n        key in updates\n        for key in (\n            "regional_egress_enabled",\n            "regional_egress_name",\n            "regional_egress_wireguard_config",\n        )\n    )\n''',
)
replace_once(
    "app/main.py",
    '''    for secret_key in ("gofile_token", "pixeldrain_api_key"):\n        if secret_key in updates:\n            updates[secret_key] = updates[secret_key].strip()\n''',
    '''    for secret_key in ("gofile_token", "pixeldrain_api_key"):\n        if secret_key in updates:\n            updates[secret_key] = updates[secret_key].strip()\n    if "regional_egress_name" in updates:\n        updates["regional_egress_name"] = updates["regional_egress_name"].strip() or "VPN"\n    if "regional_egress_wireguard_config" in updates:\n        try:\n            updates["regional_egress_wireguard_config"] = sanitize_wireguard_config(\n                updates["regional_egress_wireguard_config"]\n            )\n        except RegionalEgressError as exc:\n            raise HTTPException(400, str(exc)) from exc\n''',
)
replace_once(
    "app/main.py",
    '''    if clear_pixeldrain:\n        updates["pixeldrain_api_key"] = ""\n''',
    '''    if clear_pixeldrain:\n        updates["pixeldrain_api_key"] = ""\n    if clear_egress:\n        updates["regional_egress_wireguard_config"] = ""\n''',
)
replace_once(
    "app/main.py",
    '''    set_values(updates)\n    manager.wake()\n    return {"ok": True, "settings": public_settings()}\n\n\n@app.post("/api/settings/test/{provider}")\n''',
    '''    set_values(updates)\n    if egress_changed:\n        await egress_manager.reload()\n    manager.wake()\n    return {"ok": True, "settings": public_settings()}\n\n\n@app.post("/api/settings/test/egress")\nasync def test_regional_egress(request: Request):\n    require_auth(request)\n    try:\n        return await egress_manager.test_connection()\n    except RegionalEgressError as exc:\n        raise HTTPException(502, str(exc)) from exc\n\n\n@app.post("/api/settings/test/{provider}")\n''',
)

# ---------------------------------------------------------------------------
# Docker image: wireproxy is a userspace WireGuard client. No NET_ADMIN/TUN and
# no change to CapRover's own network path are required.
# ---------------------------------------------------------------------------
(ROOT / "Dockerfile").write_text(
    '''FROM golang:1.25-bookworm AS wireproxy-builder\nRUN GOBIN=/out go install github.com/windtf/wireproxy/cmd/wireproxy@v1.1.2\n\nFROM python:3.13-slim\nENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1\nRUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl tini tzdata && rm -rf /var/lib/apt/lists/*\nCOPY --from=wireproxy-builder /out/wireproxy /usr/local/bin/wireproxy\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install -r requirements.txt\nCOPY app ./app\nRUN mkdir -p /data/recordings\nEXPOSE 8080\nHEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl --fail --silent http://127.0.0.1:8080/healthz >/dev/null || exit 1\nENTRYPOINT ["/usr/bin/tini","--"]\nCMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8080","--proxy-headers","--timeout-graceful-shutdown","35"]\n''',
    encoding="utf-8",
)

# ---------------------------------------------------------------------------
# Compact UI: one status pill and one settings box. No tutorial copy.
# ---------------------------------------------------------------------------
replace_once(
    "app/static/index.html",
    '''          <span id="healthPill" class="pill good">Online</span>\n          <button id="settingsBtn" class="btn quiet" type="button">Impostazioni</button>\n''',
    '''          <span id="healthPill" class="pill good">Online</span>\n          <span id="egressPill" class="pill hidden">VPN</span>\n          <button id="settingsBtn" class="btn quiet" type="button">Impostazioni</button>\n''',
)
replace_once(
    "app/static/index.html",
    '''      </div></div>\n      <div class="settings-section"><h3>Buffer e disco</h3><div class="form-grid">\n''',
    '''      </div></div>\n      <div class="settings-section provider-box"><div class="provider-head"><div><h3>Regional egress</h3><span id="egressState" class="provider-state">Disattivata</span></div><button id="testEgressBtn" class="btn soft" type="button">Test VPN</button></div><div class="form-grid"><label class="check"><input id="setEgressEnabled" type="checkbox"><span>VPN per Chaturbate</span></label><label class="field"><span>Nome</span><input id="setEgressName" maxlength="80" placeholder="Proton VPN"></label><label class="field span2"><span>WireGuard .conf</span><textarea id="setEgressConfig" class="egress-config" autocomplete="off" spellcheck="false" placeholder="[Interface]&#10;PrivateKey = ...&#10;..."></textarea><small id="egressHint"></small></label><label class="check span2"><input id="clearEgressConfig" type="checkbox"><span>Rimuovi configurazione WireGuard</span></label></div></div>\n      <div class="settings-section"><h3>Buffer e disco</h3><div class="form-grid">\n''',
)

replace_once(
    "app/static/app.js",
    '''  $('#setGofileToken').value = '';\n  $('#setPixeldrainKey').value = '';\n  $('#clearGofile').checked = false;\n  $('#clearPixeldrain').checked = false;\n  $('#gofileHint').textContent = settings.gofile_configured ? `Token salvato ${settings.gofile_token_hint}` : 'Nessun token salvato';\n''',
    '''  $('#setGofileToken').value = '';\n  $('#setPixeldrainKey').value = '';\n  $('#setEgressEnabled').checked = !!settings.regional_egress_enabled;\n  $('#setEgressName').value = settings.regional_egress_name || 'Proton VPN';\n  $('#setEgressConfig').value = '';\n  $('#clearEgressConfig').checked = false;\n  $('#clearGofile').checked = false;\n  $('#clearPixeldrain').checked = false;\n  const egressState = $('#egressState');\n  if (settings.regional_egress_running) egressState.textContent = settings.regional_egress_exit_ip ? `Connessa · ${settings.regional_egress_exit_ip}` : 'Connessa';\n  else if (settings.regional_egress_enabled) egressState.textContent = 'Non connessa';\n  else egressState.textContent = 'Disattivata';\n  $('#egressHint').textContent = settings.regional_egress_error || (settings.regional_egress_configured ? 'Configurazione salvata' : 'Configurazione mancante');\n  $('#gofileHint').textContent = settings.gofile_configured ? `Token salvato ${settings.gofile_token_hint}` : 'Nessun token salvato';\n''',
)
replace_once(
    "app/static/app.js",
    '''    clear_gofile_token: $('#clearGofile').checked,\n    clear_pixeldrain_api_key: $('#clearPixeldrain').checked\n  };\n''',
    '''    clear_gofile_token: $('#clearGofile').checked,\n    clear_pixeldrain_api_key: $('#clearPixeldrain').checked,\n    regional_egress_enabled: $('#setEgressEnabled').checked,\n    regional_egress_name: $('#setEgressName').value.trim() || 'VPN',\n    clear_regional_egress_wireguard_config: $('#clearEgressConfig').checked\n  };\n''',
)
replace_once(
    "app/static/app.js",
    '''  if ($('#setPixeldrainKey').value.trim()) body.pixeldrain_api_key = $('#setPixeldrainKey').value.trim();\n  setBusy(submit, true, 'Salvataggio…');\n''',
    '''  if ($('#setPixeldrainKey').value.trim()) body.pixeldrain_api_key = $('#setPixeldrainKey').value.trim();\n  if ($('#setEgressConfig').value.trim()) body.regional_egress_wireguard_config = $('#setEgressConfig').value.trim();\n  setBusy(submit, true, 'Salvataggio…');\n''',
)
replace_once(
    "app/static/app.js",
    '''  } else {\n    health.className = 'pill good'; health.textContent = 'Online';\n  }\n}\n\nfunction renderLivePauseAlert() {\n''',
    '''  } else {\n    health.className = 'pill good'; health.textContent = 'Online';\n  }\n  const egress = status.config || {};\n  const egressPill = $('#egressPill');\n  if (egress.regional_egress_enabled) {\n    egressPill.classList.remove('hidden');\n    egressPill.className = egress.regional_egress_running ? 'pill good' : 'pill warn';\n    egressPill.textContent = egress.regional_egress_running ? 'VPN' : 'VPN !';\n    egressPill.title = egress.regional_egress_error || egress.regional_egress_name || 'Regional egress';\n  } else {\n    egressPill.className = 'pill hidden';\n  }\n}\n\nfunction renderLivePauseAlert() {\n''',
)
replace_once(
    "app/static/app.js",
    '''$('#testGofileBtn').addEventListener('click', async () => {\n''',
    '''$('#testEgressBtn').addEventListener('click', async () => {\n  const button = $('#testEgressBtn');\n  setBusy(button, true, 'Test…');\n  try {\n    const patch = {\n      regional_egress_enabled: $('#setEgressEnabled').checked,\n      regional_egress_name: $('#setEgressName').value.trim() || 'VPN',\n      clear_regional_egress_wireguard_config: $('#clearEgressConfig').checked\n    };\n    if ($('#setEgressConfig').value.trim()) patch.regional_egress_wireguard_config = $('#setEgressConfig').value.trim();\n    await api('/api/settings', {method: 'PATCH', body: JSON.stringify(patch)});\n    const result = await api('/api/settings/test/egress', {method: 'POST', body: '{}'});\n    $('#egressState').textContent = result.exit_ip ? `Connessa · ${result.exit_ip}` : 'Connessa';\n    $('#egressHint').textContent = 'VPN attiva';\n    toast(result.exit_ip ? `VPN ${result.exit_ip}` : 'VPN attiva');\n    await refresh({includeRecordings: false});\n  } catch (error) {\n    $('#egressState').textContent = 'Errore';\n    $('#egressHint').textContent = error.message;\n    toast(error.message, 'bad');\n  } finally { setBusy(button, false); }\n});\n\n$('#testGofileBtn').addEventListener('click', async () => {\n''',
)

append_once(
    "app/static/enhancements.css",
    "/* LiveVault Regional Egress v2.9.0 */",
    '''/* LiveVault Regional Egress v2.9.0 */\n.egress-config{min-height:150px;resize:vertical;font:600 .72rem/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}\n@media(max-width:620px){.egress-config{min-height:180px}}''',
)

# ---------------------------------------------------------------------------
# Release/version/docs.
# ---------------------------------------------------------------------------
(ROOT / "VERSION").write_text("2.9.0\n", encoding="utf-8")
replace_once("app/main.py", 'VERSION = "2.8.9"', 'VERSION = "2.9.0"')
replace_once("app/static/sw.js", "livevault-shell-v2.8.9", "livevault-shell-v2.9.0")
replace_once("README.md", "# LiveVault v2.8.9", "# LiveVault v2.9.0")
replace_once("START_HERE.md", "# LiveVault v2.8.9 — START HERE", "# LiveVault v2.9.0 — START HERE")

changelog = ROOT / "CHANGELOG.md"
text = changelog.read_text(encoding="utf-8")
needle = "# Changelog\n\n"
entry = '''# Changelog\n\n## 2.9.0 — Regional egress\n\n- Chaturbate può usare un'uscita WireGuard separata senza cambiare la rete della VPS o di CapRover.\n- Wireproxy gira interamente in userspace: probe, metadata, yt-dlp, ffprobe e FFmpeg/HLS usano lo stesso egress.\n- Fail-closed: se la VPN è abilitata ma non disponibile, Chaturbate non ricade sull'IP diretto della VPS.\n- Configurazione WireGuard cifrata nel database; la copia runtime è temporanea e con permessi 0600.\n- Stato VPN e test dell'IP di uscita nelle Impostazioni.\n\n'''
if needle not in text:
    raise SystemExit("CHANGELOG header not found")
changelog.write_text(text.replace(needle, entry, 1), encoding="utf-8")

append_once(
    "README.md",
    "## Regional egress (v2.9.0)",
    '''## Regional egress (v2.9.0)\n\nLiveVault può instradare solo Chaturbate attraverso un profilo WireGuard standard. Il container include `wireproxy` e non richiede TUN, `NET_ADMIN` o una VPN sull'intera VPS. Nelle Impostazioni incolla una configurazione WireGuard del provider VPN, abilita **VPN per Chaturbate** e usa **Test VPN**. La configurazione è salvata cifrata; probe, yt-dlp, ffprobe e FFmpeg usano lo stesso egress. Se l'egress è abilitato ma cade, il traffico Chaturbate resta bloccato invece di tornare all'IP diretto.\n''',
)
append_once(
    "START_HERE.md",
    "## VPN per Chaturbate",
    '''## VPN per Chaturbate\n\nPer Proton VPN: crea un account, genera un file WireGuard da **Downloads → WireGuard configuration**, poi incollalo in **Impostazioni → Regional egress**. LiveVault avvia e riavvia automaticamente il tunnel userspace a ogni deploy/restart. Gli altri provider, la dashboard e gli upload restano sulla rete normale della VPS.\n''',
)

# Existing release assertions intentionally track the current app version.
for test_file in (ROOT / "tests").glob("test_*.py"):
    test_text = test_file.read_text(encoding="utf-8")
    if "2.8.9" in test_text:
        test_file.write_text(test_text.replace("2.8.9", "2.9.0"), encoding="utf-8")

(ROOT / "tests/test_regional_egress.py").write_text(
    '''from pathlib import Path\n\nimport pytest\n\nfrom app.egress import RegionalEgressError, sanitize_wireguard_config, subprocess_proxy_env, wireproxy_config\nfrom app.source_providers import ResolvedInput\n\n\nGOOD = """\n[Interface]\nPrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\nAddress = 10.2.0.2/32\nDNS = 10.2.0.1\n\n[Peer]\nPublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=\nAllowedIPs = 0.0.0.0/0, ::/0\nEndpoint = 203.0.113.10:51820\n"""\n\n\ndef test_wireguard_config_is_sanitized_for_userspace_proxy():\n    clean = sanitize_wireguard_config(GOOD)\n    assert "[Interface]" in clean\n    assert "[Peer]" in clean\n    assert "PersistentKeepalive = 25" in clean\n    rendered = wireproxy_config(GOOD)\n    assert "[Socks5]" in rendered\n    assert "BindAddress = 127.0.0.1:25344" in rendered\n    assert "[http]" in rendered\n    assert "BindAddress = 127.0.0.1:25345" in rendered\n\n\ndef test_wireguard_shell_directives_are_rejected():\n    with pytest.raises(RegionalEgressError, match="PostUp"):\n        sanitize_wireguard_config(GOOD.replace("DNS = 10.2.0.1", "DNS = 10.2.0.1\\nPostUp = touch /tmp/nope"))\n\n\ndef test_wireguard_requires_full_ipv4_egress():\n    with pytest.raises(RegionalEgressError, match="0.0.0.0/0"):\n        sanitize_wireguard_config(GOOD.replace("0.0.0.0/0, ::/0", "10.0.0.0/8"))\n\n\ndef test_subprocess_proxy_env_keeps_loopback_direct(monkeypatch):\n    monkeypatch.setenv("no_proxy", "example.test")\n    env = subprocess_proxy_env("http://127.0.0.1:25345")\n    assert env["http_proxy"] == "http://127.0.0.1:25345"\n    assert env["https_proxy"] == "http://127.0.0.1:25345"\n    assert "127.0.0.1" in env["no_proxy"]\n    assert "localhost" in env["no_proxy"]\n\n\ndef test_resolved_input_carries_egress_to_recorder():\n    item = ResolvedInput("https://cdn.example/live.m3u8", {}, "media", "http://127.0.0.1:25345")\n    assert item.proxy_url.endswith(":25345")\n\n\ndef test_v290_image_and_ui_wiring_present():\n    root = Path(__file__).resolve().parents[1]\n    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")\n    html = (root / "app/static/index.html").read_text(encoding="utf-8")\n    js = (root / "app/static/app.js").read_text(encoding="utf-8")\n    providers = (root / "app/source_providers.py").read_text(encoding="utf-8")\n    recorder = (root / "app/recorder.py").read_text(encoding="utf-8")\n    assert "wireproxy@v1.1.2" in dockerfile\n    assert "COPY --from=wireproxy-builder /out/wireproxy" in dockerfile\n    assert 'id="setEgressConfig"' in html\n    assert 'id="egressPill"' in html\n    assert "/api/settings/test/egress" in js\n    assert "regional_egress_proxy_for_url" in providers\n    assert "env=subprocess_proxy_env(item.proxy_url)" in providers\n    assert "env=subprocess_proxy_env(proxy_url)" in recorder\n''',
    encoding="utf-8",
)
