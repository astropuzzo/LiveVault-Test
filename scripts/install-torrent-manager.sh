#!/bin/bash
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="/var/backups/openastro-torrent/$STAMP"
install -d -m 0700 "$BACKUP"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends transmission-daemon >/dev/null

systemctl disable --now transmission-daemon.service >/dev/null 2>&1 || true

for path in /etc/systemd/system/openastro-torrent.service /var/lib/openastro-torrent/settings.json; do
  [[ -e "$path" ]] && cp -a "$path" "$BACKUP/$(basename "$path")"
done

install -d -o astro -g astro -m 0750 /var/lib/openastro-torrent
if mountpoint -q /share; then
  install -d -o astro -g astro -m 0755 /share/.openastro-torrents
  install -d -o astro -g astro -m 0755 /share/.openastro-torrents/incomplete
  install -d -o astro -g astro -m 0755 /share/.openastro-torrents/complete
  install -d -o astro -g astro -m 0755 /share/Media
  install -d -o astro -g astro -m 0755 /share/Media/Downloads
fi

cat >/var/lib/openastro-torrent/settings.json <<'JSON'
{
  "alt-speed-down": 50,
  "alt-speed-enabled": false,
  "alt-speed-up": 50,
  "blocklist-enabled": false,
  "dht-enabled": true,
  "download-dir": "/share/.openastro-torrents/complete",
  "download-queue-enabled": true,
  "download-queue-size": 4,
  "encryption": 1,
  "idle-seeding-limit": 1,
  "idle-seeding-limit-enabled": true,
  "incomplete-dir": "/share/.openastro-torrents/incomplete",
  "incomplete-dir-enabled": true,
  "lpd-enabled": false,
  "peer-limit-global": 180,
  "peer-limit-per-torrent": 55,
  "peer-port": 51413,
  "peer-port-random-on-start": false,
  "pex-enabled": true,
  "port-forwarding-enabled": false,
  "queue-stalled-enabled": true,
  "queue-stalled-minutes": 30,
  "ratio-limit": 0.0,
  "ratio-limit-enabled": true,
  "rename-partial-files": true,
  "rpc-authentication-required": false,
  "rpc-bind-address": "127.0.0.1",
  "rpc-enabled": true,
  "rpc-host-whitelist-enabled": false,
  "rpc-port": 9091,
  "rpc-url": "/transmission/",
  "rpc-whitelist": "127.0.0.1,::1",
  "rpc-whitelist-enabled": true,
  "seed-queue-enabled": false,
  "speed-limit-down-enabled": false,
  "speed-limit-up-enabled": false,
  "start-added-torrents": true,
  "trash-original-torrent-files": true,
  "umask": 18,
  "utp-enabled": true
}
JSON
chown astro:astro /var/lib/openastro-torrent/settings.json
chmod 0640 /var/lib/openastro-torrent/settings.json
python3 -m json.tool /var/lib/openastro-torrent/settings.json >/dev/null

cat >/etc/systemd/system/openastro-torrent.service <<'UNIT'
[Unit]
Description=OpenAstro Transmission torrent client
After=network-online.target
Wants=network-online.target
ConditionPathIsMountPoint=/share

[Service]
Type=simple
User=astro
Group=astro
StateDirectory=openastro-torrent
StateDirectoryMode=0750
ExecStart=/usr/bin/transmission-daemon --foreground --config-dir /var/lib/openastro-torrent
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ProtectSystem=strict
ReadWritePaths=/var/lib/openastro-torrent /share/.openastro-torrents
RestrictAddressFamilies=AF_INET AF_INET6
MemoryMax=256M

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable openastro-torrent.service >/dev/null
if mountpoint -q /share; then
  systemctl restart openastro-torrent.service
fi

echo 'OpenAstro Torrent Manager installed.'
echo 'RPC: 127.0.0.1:9091 only'
echo 'Staging: /share/.openastro-torrents'
echo 'Library import: /share/Media/Downloads'
echo "Backup: $BACKUP"
