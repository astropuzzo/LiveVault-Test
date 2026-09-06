#!/bin/bash
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
install -o root -g root -m 0755 "$ROOT/scripts/openastro-storage-watchdog.py" /usr/local/sbin/openastro-storage-watchdog
cat >/etc/systemd/system/openastro-storage-watchdog.service <<'UNIT'
[Unit]
Description=OpenAstro NVMe failover watchdog
After=docker.service openastro-storage-boot.service
Wants=docker.service

[Service]
Type=simple
ExecStart=/usr/local/sbin/openastro-storage-watchdog
Restart=always
RestartSec=2
Nice=-5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now openastro-storage-watchdog.service
systemctl is-active --quiet openastro-storage-watchdog.service
echo 'OpenAstro NVMe failover watchdog active.'
