# Removable NVMe recording storage

The ASIAIR host keeps Docker, Coolify, SQLite, settings and previews on internal
eMMC. The existing `/data` runtime paths are retained through an internal bind
mount. Only `/data/livevault/recordings` switches between the NVMe and a separate
2 GiB ext4 loop filesystem on eMMC. The application bind must use `rslave`
propagation; the host `/data` mount is shared.

`scripts/migrate-internal-runtime.sh` performs the one-time host migration.
Install `scripts/nvme-handoff.py` at `/usr/local/libexec/nvme-handoff.py` first.
Run migration as a host systemd job, never as a process inside the Docker daemon
being stopped. It preserves the old NVMe runtime as a rollback copy. Subsequent
eject/attach operations do not stop Docker.

Eject writes a tokenized quiesce request. The leader stops recording processes
cleanly, blocks new media work, and drains existing jobs without cancelling
threads that still hold files. Archive finalization/upload is deferred during
buffering. After acknowledgement the host switches the recording mount, checks
the container sees the new filesystem, checks remaining device file handles,
and normally unmounts the NVMe. No lazy unmount is used. A busy device produces
an error and restores the prior recording mount.

Buffer captures use short parts and stop with 128 MiB reserved for closing
files. The independent 2 GiB filesystem enforces the hard upper bound even if
sampling or graceful stopping is delayed. A full buffer leaves the app online
and preserves its contents until the NVMe returns. Existing video on an absent
NVMe cannot be played locally until reattachment.

Attach verifies the expected disk UUID, closes buffer writers, copies each file
to a temporary NVMe destination, verifies SHA-256, fsyncs and renames it, then
removes the internal copy. Identical already-copied files make retries safe;
different collisions preserve both copies and abort. Original session markers
and stable paths allow the normal recovery, A/V validation, day/gap boundaries,
stitching and verified-upload rules to run after returning to NVMe.

The handoff takes time to close streams and drain work; it is not a zero-frame
gap guarantee. A restart chooses a nonempty internal buffer before NVMe, so
interrupted transfers cannot silently strand footage. The existing UUID udev
attach service invokes the new helper on physical reconnection.
