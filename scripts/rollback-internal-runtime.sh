#!/bin/bash
# Emergency rollback for an interrupted one-time migration, before buffer use.
set -Eeuo pipefail
[[ $EUID == 0 ]] || exit 1
[[ ! -e /etc/openastro-internal-runtime-ready ]] || { echo 'Migration completed; automatic rollback refused to protect newer recordings.'; exit 1; }
systemctl is-active --quiet openastro-internal-migration.service && { echo 'Migration still running'; exit 1; }
systemctl stop docker.socket docker.service containerd.service
backup=/var/backups/openastro-internal-migration
[[ -s "$backup/fstab" ]]
if mountpoint -q /data; then
    current=$(findmnt -nro UUID --mountpoint /data)
    if [[ "$current" != 5fe2d0f6-b485-44e9-8e26-31fb0d217db2 ]]; then
        mountpoint -q /data/livevault/recordings && umount /data/livevault/recordings
        umount /data
    fi
fi
mountpoint -q /mnt/livevault-nvme && umount /mnt/livevault-nvme
cp "$backup/fstab" /etc/fstab
cp "$backup/eject" /usr/local/sbin/livevault-storage-eject
cp "$backup/attach" /usr/local/sbin/livevault-storage-attach
rm -f /etc/systemd/system/docker.service.d/storage-handoff.conf
systemctl daemon-reload
mountpoint -q /data || mount /data
systemctl start containerd.service docker.socket docker.service
echo 'Original NVMe runtime restored; internal staging copy preserved.'
