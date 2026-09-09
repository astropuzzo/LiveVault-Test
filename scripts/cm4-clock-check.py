#!/usr/bin/env python3
"""Bounded CPU/RAM verification. Never changes clocks, voltage or boot files."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import time


def command(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout.strip()


def sample():
    return {
        "epoch": time.time(),
        "temperature_c": float(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000,
        "arm_hz": int(command("vcgencmd", "measure_clock", "arm").split("=")[-1]),
        "throttled": int(command("vcgencmd", "get_throttled").split("=")[-1], 16),
        "load": list(os.getloadavg()),
    }


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--expect-mhz", type=int, required=True)
    parser.add_argument("--max-temp", type=float, default=78)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 30 <= args.seconds <= 1800 or not 60 <= args.max_temp <= 78:
        parser.error("Use 30..1800 seconds and a thermal guard <=78 C")
    args.output.mkdir(parents=True, exist_ok=False)
    start_epoch = time.time()
    before = sample()
    samples = [before]
    reason = ""
    if before["throttled"] & 0xF or before["temperature_c"] >= args.max_temp:
        raise SystemExit("Preflight temperature/throttling guard refused the test")
    policy = Path("/sys/devices/system/cpu/cpufreq/policy0")
    maximum = int((policy / "scaling_max_freq").read_text())
    if maximum != args.expect_mhz * 1000:
        raise SystemExit(f"Wrong frequency ceiling: {maximum} kHz")
    stress_command = ["stress-ng", "--cpu", "4", "--cpu-method", "all",
                      "--vm", "1", "--vm-bytes", "128M", "--vm-keep",
                      "--verify", "--timeout", f"{args.seconds}s", "--metrics-brief"]
    with (args.output / "stress.log").open("w") as stress_log, (args.output / "samples.jsonl").open("w") as trace:
        process = subprocess.Popen(stress_command, stdout=stress_log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            deadline = time.monotonic() + args.seconds + 20
            while process.poll() is None:
                reading = sample()
                samples.append(reading)
                trace.write(json.dumps(reading) + "\n")
                trace.flush()
                if reading["temperature_c"] >= args.max_temp:
                    reason = "thermal_guard"
                elif reading["throttled"] & 0xF or reading["throttled"] & ~before["throttled"]:
                    reason = "new_throttling_or_power_flag"
                elif time.monotonic() >= deadline:
                    reason = "test_deadline"
                if reason:
                    break
                time.sleep(2)
        finally:
            stop(process)
        final = sample()
        samples.append(final)
        trace.write(json.dumps(final) + "\n")
        if final["throttled"] & 0xF or final["throttled"] & ~before["throttled"]:
            reason = reason or "new_throttling_or_power_flag"
    kernel = command("journalctl", "-k", "-b", "--since", f"@{int(start_epoch)}", "--no-pager")
    errors = [line for line in kernel.splitlines() if re.search(
        r"under.?voltage|I/O error|JBD2.*error|EXT4-fs error|segfault|oom-kill|hardware error|reset SuperSpeed", line, re.I)]
    stress_text = (args.output / "stress.log").read_text()
    cpu_samples = samples[2:] or samples
    median_hz = statistics.median(row["arm_hz"] for row in cpu_samples)
    passed = not reason and process.returncode == 0 and not errors and median_hz >= args.expect_mhz * 950000
    summary = {
        "expected_mhz": args.expect_mhz, "seconds_requested": args.seconds,
        "elapsed_seconds": time.time() - start_epoch, "passed": passed,
        "reason": reason, "stress_exit_code": process.returncode,
        "max_temperature_c": max(row["temperature_c"] for row in samples),
        "median_arm_hz": median_hz, "initial_flags": before["throttled"],
        "final_flags": samples[-1]["throttled"], "kernel_errors": errors,
        "governor": (policy / "scaling_governor").read_text().strip(),
        "min_khz": int((policy / "scaling_min_freq").read_text()),
        "stress_command": stress_command, "stress_tail": stress_text.splitlines()[-15:],
        "scope": "Short CPU/RAM check, not long-term reliability or video/energy benchmark",
    }
    temporary = args.output / "summary.tmp"
    temporary.write_text(json.dumps(summary, indent=2) + "\n")
    temporary.replace(args.output / "summary.json")
    print(json.dumps(summary), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
