#!/bin/bash
# One-time ASIAIR migration. Run as a host systemd job, outside Docker.
set -Eeuo pipefail
[[ $EUID == 0 ]] || exit 1
target=/srv/openastro-internal
[[ "$(realpath -m "$target")" == /srv/openastro-internal ]] || exit 1
[[ ! -e /etc/openastro-internal-runtime-ready ]] || { echo 'Already migrated'; exit 0; }
mountpoint -q /data
[[ $(findmnt -nro UUID --mountpoint /data) == 5fe2d0f6-b485-44e9-8e26-31fb0d217db2 ]]
mkdir -p "$target" /mnt/livevault-nvme /var/lib/livevault-buffer
backup=/var/backups/openastro-internal-migration
mkdir -p "$backup"
cp -an /etc/fstab "$backup/fstab"
cp -an /etc/docker/daemon.json "$backup/docker-daemon.json"
cp -an /usr/local/sbin/livevault-storage-eject "$backup/eject"
cp -an /usr/local/sbin/livevault-storage-attach "$backup/attach"
# The limit is enforced by an independent filesystem, not a sampling timer.
if [[ ! -e /var/lib/livevault-buffer.img ]]; then
    fallocate -l 2G /var/lib/livevault-buffer.img
    mkfs.ext4 -q -m 0 /var/lib/livevault-buffer.img
fi
rsync -aHAXx --numeric-ids --exclude='/livevault/recordings/***' --exclude='/lost+found/***' --exclude='/gpt-harness/***' /data/ "$target/"
systemctl stop livevault-backup.timer livevault-backup.service
systemctl stop docker.socket docker.service containerd.service
# Final consistent copy includes SQLite WALs and Docker/Coolify state. Deletion
# is confined to the verified new migration destination, never the NVMe.
rsync -aHAXx --delete --numeric-ids --exclude='/livevault/recordings/***' --exclude='/lost+found/***' --exclude='/gpt-harness/***' /data/ "$target/"
sync
umount /data
python3 - <<'PY'
from pathlib import Path
p=Path('/etc/fstab')
s=p.read_text().replace(' /data ext4 ', ' /mnt/livevault-nvme ext4 ')
s += '\n# OpenAstro internal runtime and bounded removable-storage buffer\n'
s += '/srv/openastro-internal /data none bind 0 0\n'
s += '/var/lib/livevault-buffer.img /var/lib/livevault-buffer ext4 loop,noatime 0 0\n'
p.write_text(s)
PY
systemctl daemon-reload
mount /mnt/livevault-nvme
mount /data
mount /var/lib/livevault-buffer
cat > /usr/local/sbin/livevault-storage-eject <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/local/libexec/nvme-handoff.py eject
EOF
cat > /usr/local/sbin/livevault-storage-attach <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/local/libexec/nvme-handoff.py attach
EOF
chmod 755 /usr/local/sbin/livevault-storage-{eject,attach}
cat > /etc/systemd/system/openastro-storage-boot.service <<'EOF'
[Unit]
Description=Select NVMe or bounded internal recording buffer
After=local-fs.target
Before=docker.service
RequiresMountsFor=/data /var/lib/livevault-buffer
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/libexec/nvme-handoff.py boot
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/docker.service.d/storage-handoff.conf <<'EOF'
[Unit]
Requires=openastro-storage-boot.service
After=openastro-storage-boot.service
EOF
cat > /etc/systemd/system/livevault-storage-attach.service <<'EOF'
[Unit]
Description=Restore recording storage after NVMe reconnect
After=docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/livevault-storage-attach
TimeoutStartSec=300
EOF
systemctl daemon-reload
systemctl enable openastro-storage-boot.service
systemctl start openastro-storage-boot.service
touch /etc/openastro-internal-runtime-ready
systemctl start containerd.service docker.socket docker.service
systemctl start livevault-backup.timer
echo 'Runtime migrated; original NVMe runtime retained as rollback copy.'
