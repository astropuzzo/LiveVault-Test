#!/usr/bin/env python3
"""One-shot CM4 clock trials; preserve the ordinary 2 GHz boot and pause state."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess

ROOT = Path("/var/backups/openastro/20260908-clock")
CONFIG = Path("/boot/firmware/config.txt")
TRIAL = CONFIG.with_name("tryboot.txt")
APP = "ahul2vdjkyvjiwgzpcrmxzfe"


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=100).stdout.strip()


def app_python(code):
    names = run("docker", "ps", "--format", "{{.Names}}").splitlines()
    names = [name for name in names if name.startswith(APP + "-")]
    if len(names) != 1:
        raise RuntimeError("Expected exactly one running LiveVault container")
    return run("docker", "exec", names[0], "python", "-c", code)


def set_pause(recordings, uploads):
    # Token is generated/consumed inside the container; never returned or logged.
    code = '''import json,urllib.request
from app.auth import COOKIE_NAME,create_session_token
token=create_session_token()
for kind,paused in PAUSES:
    request=urllib.request.Request("http://127.0.0.1:8080/api/control/"+kind,
        data=json.dumps({"paused":paused,"stop_active":True}).encode(),
        headers={"Content-Type":"application/json","Cookie":COOKIE_NAME+"="+token})
    with urllib.request.urlopen(request,timeout=90) as response:
        assert json.load(response).get("ok") is True
print("Pause state applied")
'''.replace("PAUSES", repr([("uploads", uploads), ("recordings", recordings)]))
    app_python(code)


def validate_normal():
    normal = CONFIG.read_bytes()
    if normal != (ROOT / "config-2000.txt").read_bytes():
        raise RuntimeError("Normal config changed externally; inspect before continuing")
    if re.findall(rb"^arm_freq=(\d+)\s*$", normal, re.M) != [b"2000"]:
        raise RuntimeError("Normal config must contain exactly one 2000 MHz setting")
    return normal


def reboot(tryboot):
    command = ["systemd-run", "--unit=openastro-clock-reboot", "--collect",
               "--on-active=5s", "/usr/sbin/reboot"]
    if tryboot:
        command.append("0 tryboot")
    run(*command)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "normal-boot", "resume"])
    parser.add_argument("--mhz", type=int, choices=[2100, 2200])
    args = parser.parse_args()
    normal = validate_normal()
    pause_file = ROOT / "pause-state.json"
    if args.action == "prepare":
        if args.mhz is None:
            parser.error("prepare requires --mhz")
        prior = "baseline-2000" if args.mhz == 2100 else "trial-2100"
        if not json.loads((ROOT / prior / "summary.json").read_text())["passed"]:
            raise RuntimeError("Previous clock step did not pass")
        if TRIAL.exists():
            owned = json.loads((ROOT / "trial-state.json").read_text())
            if hashlib.sha256(TRIAL.read_bytes()).hexdigest() != owned["sha256"]:
                raise RuntimeError("tryboot.txt changed externally")
        if not pause_file.exists():
            state = app_python('from app.settings_store import reload_runtime; import json; s=reload_runtime(); print(json.dumps({"recordings":s.recording_paused,"uploads":s.upload_paused}))')
            pause_file.write_text(json.dumps(json.loads(state)) + "\n")
            pause_file.chmod(0o600)
        set_pause(True, True)
        with sqlite3.connect("file:/data/livevault/livevault.db?mode=ro", uri=True) as source:
            with sqlite3.connect(ROOT / f"livevault-pretrial-{args.mhz}.db") as target:
                source.backup(target)
        trial = re.sub(rb"^arm_freq=2000\s*$", f"arm_freq={args.mhz}\n".encode(), normal, flags=re.M)
        temporary = TRIAL.with_suffix(".tmp")
        temporary.write_bytes(trial)
        temporary.replace(TRIAL)
        (ROOT / "trial-state.json").write_text(json.dumps({
            "mhz": args.mhz, "sha256": hashlib.sha256(trial).hexdigest(),
            "ordinary_boot_mhz": 2000, "recordings_paused": True,
        }, indent=2) + "\n")
        run("sync")
        print(f"Prepared one-shot {args.mhz} MHz; ordinary config remains 2000 MHz", flush=True)
        reboot(True)
    elif args.action == "normal-boot":
        reboot(False)
        print("Ordinary 2000 MHz boot scheduled; run resume after reconnecting", flush=True)
    else:
        maximum = int(Path("/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq").read_text())
        if maximum != 2000000:
            raise RuntimeError("Return to ordinary 2000 MHz boot before resuming")
        original = json.loads(pause_file.read_text())
        set_pause(original["recordings"], original["uploads"])
        (ROOT / "resumed.json").write_text(json.dumps(original) + "\n")
        print("Original recording/upload pause settings restored", flush=True)


if __name__ == "__main__":
    main()
