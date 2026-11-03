"""Biglie's Oracle slot hunt in OpenAstro Control: status, log, controls, and a phone notice when it finds the Arm
machine, fails or stops. The hunt and its hunt.sh come from the Biglie repo (tools/regia) and live in HUNT_DIR;
the panel runs hunt.sh as their owner, astro. Starting goes through openastro-action, so the hunt never lives in
the panel's cgroup and a panel restart cannot stop it."""
from __future__ import annotations

import copy
import ipaddress
import json
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import web_push

HUNT_DIR = Path("/home/astro/biglie-oci")
STATE_FILE = Path("/var/lib/openastro-control/biglie-hunt.json")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
START_COMMAND = ["sudo", "-n", "/usr/local/sbin/openastro-action", "biglie_hunt_start"]
STATES = ("searching", "running", "growing", "top", "error", "paused", "stopped")
LABELS = {"searching": "in cerca di posto", "running": "macchina attiva", "growing": "macchina attiva, cerca più spazio",
          "top": "taglia massima raggiunta", "error": "errore", "paused": "in pausa", "stopped": "fermata"}
ACTIONS = ("start", "stop", "restart", "pause", "resume", "interval")
# Seconds between the hunt's rounds that hunt.sh interval accepts.
INTERVALS = (15, 30, 60, 90)
PROBE_SECONDS = 5
WATCH_SECONDS = 30
LOG_LINES = 40
# The hunt rewrites the same error every round while Oracle keeps failing: one notice an hour is enough.
ERROR_REPEAT_SECONDS = 3600
STOP_GRACE_SECONDS = 60
# A notice no device took is sent again at every check for up to an hour (the line may be down right after a reboot).
RETRY_SECONDS = 3600
NOTICE_URL = "/#apps"

_CONTROL = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|[\x00-\x08\x0b-\x1f\x7f]")
_probe_lock = threading.Lock()
_probe: dict = {}
_probe_at = 0.0
_action_lock = threading.Lock()
_panel_stop_at = 0.0
_seen: dict | None = None
_seen_loaded = False
_failing_since: float | None = None


def _run(args: list[str], timeout: int) -> tuple[int, str]:
    try:
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, str(exc)


def _hunt(*args: str, timeout: int = 8) -> tuple[int, str]:
    return _run([str(HUNT_DIR / "hunt.sh"), *args], timeout)


def _clean(text: str, limit: int) -> str:
    return _CONTROL.sub("", str(text)).strip()[:limit]


def _count(value, top: int) -> int | None:
    return value if type(value) is int and 0 <= value <= top else None


def _epoch(value) -> int | None:
    try:
        return int(datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S%z").timestamp())
    except ValueError:
        return None


def parse_status(text: str) -> dict:
    """hunt.sh status: status.json with "alive" and "interval" added, or only those two before the first run. An older
    hunt.sh has no "interval": the card then cannot change it."""
    lines = [line for line in text.splitlines() if line.strip()]
    try:
        raw = json.loads(lines[-1]) if lines else None
    except ValueError:
        raw = None
    if not isinstance(raw, dict):
        raise ValueError("Risposta di hunt.sh status non leggibile.")
    state = raw.get("state")
    machine = None
    name, ocpus, gb = raw.get("name"), _count(raw.get("ocpus"), 64), _count(raw.get("gb"), 1024)
    if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name) and ocpus is not None and gb is not None:
        try:
            ip = str(ipaddress.ip_address(str(raw.get("ip", ""))))
        except ValueError:
            ip = None
        machine = {"name": name, "ocpus": ocpus, "gb": gb, "ip": ip}
    error, interval = raw.get("error"), raw.get("interval")
    return {
        "alive": raw.get("alive") is True,
        "interval": interval if type(interval) is int and interval in INTERVALS else None,
        "state": state if state in STATES else ("unknown" if state is not None else None),
        "round": _count(raw.get("round"), 10 ** 9),
        "updated": _epoch(raw.get("time")),
        "machine": machine,
        "error": _clean(error, 300) if isinstance(error, str) and error.strip() else None,
    }


def _read() -> dict:
    checked = int(time.time())
    if not (HUNT_DIR / "hunt.sh").is_file():
        return {"ok": True, "installed": False, "checked": checked}
    code, output = _hunt("status")
    try:
        if code != 0:
            raise ValueError(f"hunt.sh status è uscito con il codice {code}.")
        status = parse_status(output)
    except ValueError as exc:
        return {"ok": False, "installed": True, "checked": checked, "error": str(exc)}
    code, output = _hunt("log", str(LOG_LINES))
    log = [_clean(line, 300) for line in output.splitlines()[-LOG_LINES:]] if code == 0 else []
    return {"ok": True, "installed": True, "checked": checked, "paused": (HUNT_DIR / "paused").exists(),
            **status, "log": log, "log_ok": code == 0}


def snapshot(force: bool = False) -> dict:
    """One shared read of status and log for every viewer and the watcher, at most every PROBE_SECONDS."""
    global _probe, _probe_at
    with _probe_lock:
        if force or not _probe or time.monotonic() - _probe_at >= PROBE_SECONDS:
            _probe = _read()
            _probe_at = time.monotonic()
        return copy.deepcopy(_probe)


def _start(paused: bool) -> tuple[dict, int]:
    code, output = _run(START_COMMAND, 30)
    if code != 0:
        return {"ok": False, "error": _clean(output, 300) or "Avvio non riuscito."}, 500
    message = "Ricerca avviata."
    if paused:
        message += " È in pausa: premi Riprendi per farla cercare."
    return {"ok": True, "message": message}, 200


def _stop() -> tuple[dict, int] | None:
    global _panel_stop_at
    _panel_stop_at = time.time()
    code, output = _hunt("stop", timeout=20)
    if code != 0:
        return {"ok": False, "error": _clean(output, 300) or "Arresto non riuscito."}, 500
    return None


def _interval(seconds: int, current: dict) -> tuple[dict, int]:
    if current.get("ok") and current.get("interval") is None:
        return {"ok": False, "error": "Lo script della ricerca su questo nodo non sa cambiare l’intervallo: va aggiornato dal repository Biglie."}, 409
    code, output = _hunt("interval", str(seconds))
    if code != 0:
        return {"ok": False, "error": _clean(output, 300) or "Comando non riuscito."}, 500
    if current.get("alive") is True:
        return {"ok": True, "message": f"Ora la ricerca riprova ogni {seconds} secondi."}, 200
    return {"ok": True, "message": f"La ricerca riproverà ogni {seconds} secondi quando la avvii."}, 200


def action(name: str, seconds=None) -> tuple[dict, int]:
    if name not in ACTIONS:
        return {"ok": False, "error": "Azione non consentita."}, 400
    if name == "interval" and (type(seconds) is not int or seconds not in INTERVALS):
        return {"ok": False, "error": "Intervallo non consentito: scegli 15, 30, 60 o 90 secondi."}, 400
    if not (HUNT_DIR / "hunt.sh").is_file():
        return {"ok": False, "error": "La ricerca non è installata su questo nodo."}, 409
    if not _action_lock.acquire(blocking=False):
        return {"ok": False, "error": "Un’altra azione sulla ricerca è in corso."}, 409
    try:
        current = snapshot(force=True)
        alive, paused = current.get("alive") is True, current.get("paused") is True
        if name == "interval":
            return _interval(seconds, current)
        if name in ("pause", "resume"):
            code, output = _hunt(name)
            if code != 0:
                return {"ok": False, "error": _clean(output, 300) or "Comando non riuscito."}, 500
            if name == "pause":
                return {"ok": True, "message": "Pausa attivata: vale entro 5 secondi." if alive else "Pausa attivata: la ricerca partirà in pausa."}, 200
            return {"ok": True, "message": "Ricerca ripresa." if alive else "Pausa tolta."}, 200
        if name == "stop":
            if not alive:
                return {"ok": True, "message": "La ricerca era già ferma."}, 200
            return _stop() or ({"ok": True, "message": "Ricerca fermata."}, 200)
        if name == "start" and alive:
            return {"ok": True, "message": "La ricerca è già attiva."}, 200
        if name == "restart" and alive:
            failed = _stop()
            if failed:
                return failed
        result, status = _start(paused)
        if name == "restart" and result.get("ok"):
            result["message"] = result["message"].replace("avviata", "riavviata")
        return result, status
    finally:
        _action_lock.release()
        snapshot(force=True)


def _machine_key(status: dict) -> list | None:
    machine = status.get("machine")
    return [machine["name"], machine["ocpus"], machine["gb"], machine["ip"]] if machine else None


def notices(seen: dict | None, current: dict, boot: str, now: float, panel_stop_at: float) -> tuple[list[tuple[str, str, str]], dict]:
    """Compares the hunt with what the watcher saw last; returns the notices due (title, body, urgency) and what to remember."""
    state, alive, key = current.get("state"), current.get("alive") is True, _machine_key(current)
    if seen is None:
        # First run: remember the hunt as it is, announce nothing old.
        return [], {"boot": boot, "alive": alive, "state": state, "machine": key, "error": current.get("error"), "error_at": now if state == "error" else 0}
    remember = {"boot": boot, "alive": alive, "state": state, "machine": seen.get("machine"),
                "error": seen.get("error"), "error_at": seen.get("error_at", 0)}
    due = []
    known = seen.get("machine")
    if state == "searching":
        remember["machine"] = None
    elif key and key != known:
        # A brief "running" can fall between two checks, so any new machine in the status counts, not only that state.
        ocpus, gb, ip = key[1], key[2], key[3] or "non ancora assegnato"
        if known and known[0] == key[0] and ocpus > known[1]:
            title, body = "Biglie: la macchina Arm è cresciuta", f"Ora ha {ocpus} OCPU e {gb} GB. IP {ip}."
        else:
            title, body = "Biglie: trovata la macchina Arm", f"{ocpus} OCPU e {gb} GB. IP {ip}."
        if state == "top":
            body += " È la taglia massima gratuita: la ricerca è finita."
        due.append((title, body, "high"))
        remember["machine"] = key
    if seen.get("alive") and not alive:
        label = LABELS.get(state or "", "sconosciuto")
        if boot and seen.get("boot") and seen.get("boot") != boot:
            due.append(("Biglie: ricerca ferma dopo il riavvio", "L’ASIAIR si è riavviata e la ricerca non riparte da sola. Premi Avvia nel pannello.", "normal"))
        elif state not in ("stopped", "top") and now - panel_stop_at >= STOP_GRACE_SECONDS:
            due.append(("Biglie: la ricerca si è fermata", f"Il processo non è più attivo (ultimo stato: {label}). Premi Avvia nel pannello.", "high"))
    elif alive and state == "error":
        error = current.get("error") or "Errore senza dettagli"
        if error != seen.get("error") or now - (seen.get("error_at") or 0) >= ERROR_REPEAT_SECONDS:
            text = error[:200].rstrip()
            due.append(("Biglie: errore nella ricerca", f"{text}{'' if text.endswith(('.', '!', '?')) else '.'} La ricerca riprova da sola.", "normal"))
            remember["error"], remember["error_at"] = error, now
    return due, remember


def _boot() -> str:
    try:
        return BOOT_ID.read_text(encoding="ascii").strip()
    except OSError:
        return ""


def watch_once(now: float | None = None) -> list[tuple[str, str, str]]:
    global _seen, _seen_loaded, _failing_since
    now = time.time() if now is None else now
    current = snapshot()
    if not current.get("ok") or not current.get("installed"):
        return []
    if not _seen_loaded:
        try:
            _seen = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _seen = None
        _seen_loaded = True
    due, remember = notices(_seen if isinstance(_seen, dict) else None, current, _boot(), now, _panel_stop_at)
    missed = [notice for notice in due if not web_push.notify(notice[0], notice[1], NOTICE_URL, notice[2])]
    if missed and web_push.count():
        _failing_since = _failing_since or now
        if now - _failing_since < RETRY_SECONDS:
            return due
    _failing_since = None
    if remember != _seen:
        temporary = STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(remember), encoding="utf-8")
        temporary.replace(STATE_FILE)
        _seen = remember
    return due


def watch_loop() -> None:
    """Background notices for the panel's whole life. It reads the hunt and never starts or stops it."""
    while True:
        try:
            watch_once()
        except Exception as exc:
            print(f"Biglie hunt watcher: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(WATCH_SECONDS)
