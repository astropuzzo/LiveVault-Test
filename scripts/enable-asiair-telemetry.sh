#!/bin/bash
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
grep -q 'Raspberry Pi Compute Module 4' /proc/device-tree/model || {
    echo 'This setup is for the ASIAIR Plus CM4 only.' >&2; exit 1;
}
# The ASIAIR ADCs use the CSI mux bus, not the power-output GPIOs.
[[ -e /sys/bus/i2c/devices/i2c-10 ]] || dtparam i2c_vc=on
modprobe i2c-dev
[[ -e /dev/i2c-10 ]] || { echo 'ASIAIR sensor bus unavailable.' >&2; exit 1; }
config=/boot/firmware/config.txt
if ! grep -qx 'dtparam=i2c_vc=on' "$config"; then
    install -d -m 0755 /var/backups/openastro-telemetry
    cp -p "$config" "/var/backups/openastro-telemetry/config-$(date +%Y%m%d-%H%M%S).txt"
    printf '\n[all]\n# ASIAIR Plus input voltage/current ADC bus\ndtparam=i2c_vc=on\n' >> "$config"
fi
printf 'i2c-dev\n' > /etc/modules-load.d/openastro-i2c.conf
echo 'ASIAIR sensor bus enabled now and at boot. Power-output GPIOs unchanged.'
