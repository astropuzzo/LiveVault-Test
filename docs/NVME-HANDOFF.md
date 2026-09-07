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
sees exactly one `/data/recordings` mount on the same filesystem device. Shared-
subtree propagation is allowed a short bounded settle period, but correctness no
longer depends on it. If a container retains a stale submount, the helper uses
Linux `open_tree(2)` to clone the already-selected host recordings mount, enters
the target mount namespace with `setns(2)`, removes every stacked
`/data/recordings` mount, and attaches the cloned mount with `move_mount(2)`.
This path does not require a source path to be visible inside the container and
does not depend on a new mount event propagating through Docker. Docker and the
LiveVault container remain online; no Coolify-generated compose artifact and no
`docker restart` fallback are required. Before physical detach the helper also
verifies that no LiveVault container mount namespace retains any mount backed by
the NVMe device. Only
after those checks pass does it inspect remaining device handles and unmount the
NVMe. GPT Harness is stopped immediately before the unmount so
its private sandbox cannot retain the removable filesystem, then restarted
eMMC-only; attach restarts it again after the NVMe is mounted so its optional
heavy-workspace path becomes writable again. No lazy unmount is used for a
manual eject. A failure remains fail-closed and restores the complete prior
state when the medium is still present: recordings bind, `/share`, storage
state and `livevault-backup.timer`.

A later real Control Center attempt exposed one more failure mode: the temporary
sibling repair source itself could fail to propagate to a newly deployed
container (`Sorgente handoff non propagata ai container`). That approach was
removed. The final kernel-mount repair above was then exercised on the same
container: `eject_nvme` completed with the NVMe and `/share` truly unmounted and
container recordings on loop device `1792`; `attach_nvme` returned host and
container recordings to NVMe device `2082`, restored `/share` and the backup
timer, and left the buffer empty.

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

## Automatic failure path

`openastro-storage-watchdog.service` runs from internal eMMC and checks the host
mount table, `/dev/disk/by-uuid` and block-device state while storage mode is
`nvme`. It intentionally does not issue filesystem data reads/probes against a
suspect NVMe because a failed USB bridge can leave such calls blocked in D-state.
When the expected recording device disappears, becomes read-only/shutdown, or is
no longer a running kernel block device, the watchdog calls
`nvme-handoff.py failover` under the same storage lock used by manual handoff.

Emergency failover first tries the same kernel mount-clone namespace primitive
used by normal handoff, but points every running LiveVault container directly at
the internal buffer *before* replacing the host `/data/livevault/recordings`
bind. Therefore the Docker daemon remains on the eMMC runtime and is not stopped.
If a dead filesystem prevents the namespace move, only the LiveVault application
container may be stop/started as a last resort after the host bind has moved to
eMMC. The dead SERVER/SHARE filesystem mounts are lazily detached only in this
unexpected-failure path; normal user eject remains strict and never uses lazy
unmount.

The storage tiers are a hard architectural contract:

- internal eMMC: OS, `/data`, Docker `/data/docker`, dependencies, databases,
  configuration, Control Center state/cache and the bounded 4 GiB failover buffer;
- SERVER NVMe: heavy LiveVault recording payloads;
- removable USB media: films and other Media Hub payloads only.

Removing a media USB does not migrate the film contents into the emergency
buffer; the Media Hub catalog/runtime stays on eMMC and the library becomes
offline cleanly. Removing/failing the SERVER NVMe redirects new LiveVault capture
to the bounded eMMC buffer until automatic UUID reattach succeeds.
