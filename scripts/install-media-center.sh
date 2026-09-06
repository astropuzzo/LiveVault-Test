#!/bin/bash
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="/var/backups/openastro-media/$STAMP"
install -d -m 0700 "$BACKUP"

LAN_CIDR=$(ip -4 -o addr show dev eth0 scope global | awk '{print $4}' | head -n1)
[[ -n "$LAN_CIDR" ]] || { echo 'eth0 has no IPv4 LAN address.' >&2; exit 1; }
LAN_IP=${LAN_CIDR%/*}
LAN_NET=$(python3 - "$LAN_CIDR" <<'PYNET'
import ipaddress,sys
print(ipaddress.ip_interface(sys.argv[1]).network)
PYNET
)

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends samba samba-common-bin minidlna wsdd2 exfatprogs ntfs-3g ffmpeg >/dev/null

for path in /etc/samba/smb.conf /etc/minidlna.conf /etc/udev/rules.d/99-openastro-media.rules; do
  [[ -e "$path" ]] && cp -a "$path" "$BACKUP/$(basename "$path")"
done

install -d -o root -g root -m 0755 /srv/openastro-media
install -o root -g root -m 0755 "$ROOT/scripts/openastro-media-manager.py" /usr/local/sbin/openastro-media-manager

cat >/etc/samba/smb.conf <<SMB
[global]
   workgroup = WORKGROUP
   server string = OpenAstro Media
   netbios name = OPENASTRO
   server role = standalone server
   security = user
   map to guest = Bad User
   guest account = nobody
   server min protocol = SMB2_10
   client min protocol = SMB2_10
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes
   dns proxy = no
   interfaces = 127.0.0.1 $LAN_IP
   bind interfaces only = yes
   hosts allow = 127.0.0.1 $LAN_NET
   hosts deny = 0.0.0.0/0 ::/0
   log file = /var/log/samba/log.%m
   max log size = 500

[Media]
   comment = OpenAstro USB Media
   path = /srv/openastro-media
   browseable = yes
   read only = yes
   guest ok = yes
   follow symlinks = no
   wide links = no
SMB

testparm -s /etc/samba/smb.conf >/dev/null

# Keep one stable authenticated SMB credential for Windows clients that reject guest shares.
CRED=/etc/openastro-media-credentials.json
if [[ ! -s "$CRED" ]]; then
  password=$(openssl rand -hex 12)
  printf '%s\n%s\n' "$password" "$password" | smbpasswd -s -a astro >/dev/null
  printf '{"username":"astro","password":"%s"}\n' "$password" > "$CRED"
  chown root:astro "$CRED"
  chmod 0640 "$CRED"
else
  chown root:astro "$CRED"
  chmod 0640 "$CRED"
fi

cat >/etc/minidlna.conf <<'DLNA'
port=8200
network_interface=eth0
media_dir=/srv/openastro-media
friendly_name=OpenAstro Media
inotify=yes
notify_interval=30
album_art_names=Cover.jpg/cover.jpg/Folder.jpg/folder.jpg/Thumb.jpg/thumb.jpg
DLNA

install -d -m 0755 /etc/systemd/system/wsdd2.service.d
cat >/etc/systemd/system/wsdd2.service.d/openastro-lan.conf <<'UNIT'
[Service]
ExecStart=
ExecStart=/usr/sbin/wsdd2 -4 -i eth0
UNIT

install -d -m 0755 /etc/systemd/system/minidlna.service.d
cat >/etc/systemd/system/minidlna.service.d/openastro-lan.conf <<UNIT
[Service]
IPAddressDeny=any
IPAddressAllow=127.0.0.0/8
IPAddressAllow=$LAN_NET
IPAddressAllow=239.255.255.250/32
UNIT

cat >/etc/systemd/system/openastro-media-reconcile.service <<'UNIT'
[Unit]
Description=OpenAstro removable media reconciliation
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/openastro-media-manager reconcile
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
UNIT

cat >/etc/systemd/system/openastro-media-reconcile.timer <<'UNIT'
[Unit]
Description=OpenAstro removable media hotplug fallback scan

[Timer]
OnBootSec=8s
OnUnitInactiveSec=15s
AccuracySec=1s
Unit=openastro-media-reconcile.service

[Install]
WantedBy=timers.target
UNIT

cat >/etc/udev/rules.d/99-openastro-media.rules <<'UDEV'
# Only schedule a reconciliation; the root helper performs all identity/safety checks.
ACTION=="add|remove", SUBSYSTEM=="block", ENV{DEVTYPE}=="partition", RUN+="/bin/systemctl --no-block start openastro-media-reconcile.service"
UDEV

systemctl daemon-reload
udevadm control --reload
systemctl enable smbd.service nmbd.service wsdd2.service minidlna.service openastro-media-reconcile.timer >/dev/null
systemctl restart smbd.service nmbd.service wsdd2.service minidlna.service
systemctl enable --now openastro-media-reconcile.timer >/dev/null
systemctl start openastro-media-reconcile.service

echo 'OpenAstro Media Center base installed.'
echo "Backup: $BACKUP"
