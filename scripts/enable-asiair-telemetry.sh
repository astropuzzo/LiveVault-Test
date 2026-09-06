#!/bin/bash
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
grep -q 'Raspberry Pi Compute Module 4' /proc/device-tree/model || {
    echo 'This setup is for the ASIAIR Plus CM4 only.' >&2; exit 1;
}

# The ASIAIR Plus ADS1015 is wired to the CM4 CSI I2C pins (GPIO 44/45).
# Pin the controller explicitly instead of relying on the firmware i2c_vc mux;
# this is stable across the boot/tryboot configuration used by OpenAstro.
modprobe i2c-dev
if ! ls /dev/i2c-* >/dev/null 2>&1; then
    dtoverlay i2c0 pins_44_45
    sleep 1
fi

python3 - <<'PYCHECK'
import fcntl, glob
for path in sorted(glob.glob('/dev/i2c-*')):
    try:
        with open(path, 'r+b', buffering=0) as bus:
            fcntl.ioctl(bus.fileno(), 0x0703, 0x4b)
            bus.write(b'\x01')
            if len(bus.read(2)) == 2:
                print(f'ASIAIR ADS1015 detected on {path}')
                raise SystemExit(0)
    except OSError:
        pass
raise SystemExit('ASIAIR ADS1015 at 0x4b is unavailable.')
PYCHECK

config=/boot/firmware/config.txt
install -d -m 0755 /var/backups/openastro-telemetry
cp -p "$config" "/var/backups/openastro-telemetry/config-$(date +%Y%m%d-%H%M%S).txt"
python3 - "$config" <<'PYCFG'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
s = s.replace('dtparam=i2c_vc=on\n', '')
line = 'dtoverlay=i2c0,pins_44_45'
if line not in s.splitlines():
    s = s.rstrip() + f'\n\n[all]\n# ASIAIR Plus input voltage/current ADC bus\n{line}\n'
p.write_text(s)
PYCFG
printf 'i2c-dev\n' > /etc/modules-load.d/openastro-i2c.conf
echo 'ASIAIR sensor bus enabled now and pinned to CSI GPIO 44/45 at boot. Power-output GPIOs unchanged.'
