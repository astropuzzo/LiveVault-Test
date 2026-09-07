# Removable NVMe recording storage

The ASIAIR host keeps Docker, Coolify, SQLite, settings and previews on internal
eMMC. The existing `/data` runtime paths are retained through an internal bind
mount. Only `/data/livevault/recordings` switches between the NVMe and a separate
4 GiB ext4 loop filesystem on eMMC. The application bind must use `rslave`
propagation; the host `/data` mount is shared.

`scripts/migrate-internal-runtime.sh` performs the one-time host migration.
Install `scripts/nvme-handoff.py` at `/usr/local/libexec/nvme-handoff.py` first.
Run migration as a host systemd job, never as a process inside the Docker daemon
being stopped. It preserves the old NVMe runtime as a rollback copy. Subsequent
eject/attach operations do not stop Docker.

Eject writes a tokenized quiesce request. The leader stops recording processes
cleanly, blocks new media work, and drains existing jobs without cancelling
threads that still hold files. Archive finalization/upload is deferred during
buffering. After acknowledgement the host reasserts `/data` as `rshared`, switches the
recording mount and positively verifies that every running LiveVault container
sees the same filesystem device. Propagation verification is bounded/retried;
if one stable LiveVault container retains a stale submount, the helper restarts
only that already-quiesced container and verifies again. Docker itself remains
online. Only after that check passes does it inspect remaining device handles
and unmount the NVMe. GPT Harness is stopped immediately before the unmount so
its private sandbox cannot retain the removable filesystem, then restarted
eMMC-only; attach restarts it again after the NVMe is mounted so its optional
heavy-workspace path becomes writable again. No lazy unmount is used for a
manual eject. A failure remains fail-closed and restores the complete prior
state when the medium is still present: recordings bind, `/share`, storage
state and `livevault-backup.timer`.

Buffer captures use short parts and reserve 128 MiB per active camera plus
one spare slot for closing files. The full state stays latched until NVMe
returns, including across application restarts. The independent 4 GiB filesystem enforces the hard upper bound even if
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
