# OpenAstro / LiveVault — AI Handoff & Operations Runbook

> **Purpose:** this file is the canonical handoff for a future AI chat working on the OpenAstro / LiveVault server. Read this file first, then verify the live state before making changes. Do not make the user re-explain architecture, access, root, GitHub, storage safety, or UI QA conventions already documented here.
>
> **Last live verification:** 2026-09-07. Snapshot values can become stale; architecture and safety rules are authoritative unless the live node proves otherwise.

---

## 0. Future-chat start instruction

The user should be able to say only:

> **Read `AI-HANDOFF.md` in `astropuzzo/LiveVault-Test`, verify the live node, and continue from there. Do not make me repeat completed setup.**

The AI should then:

1. Connect through the existing GPT Harness MCP/tooling.
2. Read this file and `git log -10 --oneline`.
3. Run the **First 60 seconds** checks below.
4. Inspect the relevant source before editing.
5. Preserve all unrelated working systems.
6. Test, deploy narrowly, visually inspect UI changes, and only then push.

Do **not** ask the user for root access, GitHub credentials, server paths, the Coolify URL, or storage UUIDs unless a live verification shows that the documented setup no longer exists.

---

# 1. System at a glance

OpenAstro is an ASIAIR Plus / Raspberry Pi CM4 running Debian with several independent layers:

```text
Internet / LAN
     |
     +-- Tailscale Funnel
     |     +-- https://openastro.tailf2871c.ts.net/        -> LiveVault :8080
     |     +-- https://openastro.tailf2871c.ts.net:8443/   -> Control Center :9090
     |     +-- https://openastro.tailf2871c.ts.net:10000/  -> Coolify :8000
     |
     +-- LAN only
           +-- SMB  \\OPENASTRO\Media
           +-- DLNA "OpenAstro Media"

CM4 / eMMC
     +-- Debian + systemd
     +-- Docker/Coolify state
     +-- OpenAstro Control Center
     +-- GPT Harness gateway + root bridge
     +-- 4 GiB emergency recording buffer image
     +-- Media Hub persistent DB/history/cache

USB / external storage
     +-- SERVER NVMe ext4  -> /mnt/livevault-nvme
     |      +-- LiveVault recordings
     |      +-- GPT Harness repo/workspace
     +-- SHARE exFAT       -> /share
     +-- removable media   -> /srv/openastro-media/* (read-only)
```

**Priority order when making decisions:**

1. LiveVault recording integrity.
2. NVMe / filesystem integrity and failover.
3. Core node services / networking.
4. Control Center.
5. Media Hub direct play.
6. Media transcoding / cosmetic or background work.

A UI feature or media feature must never be allowed to endanger an active recording.

---

# 2. Access model: GPT Harness and root

## GPT Harness

The ChatGPT-side server tool is the dedicated GPT Harness MCP gateway.

- Main service: `gpt-harness.service`
- Service user/group: `gpt-harness:gpt-harness` (`uid=997`, `gid=984` at last check)
- App: `/opt/gpt-harness`
- Main writable work area: `/data/gpt-harness/work`
- NVMe workspace: `/mnt/livevault-nvme/gpt-harness/work`
- Repository: `/mnt/livevault-nvme/gpt-harness/work/LiveVault-Test`
- The service is intentionally sandboxed and **cannot** see/use:
  - `/run/docker.sock`
  - `/share`
  - `/data/recordings`
  - `/var/lib/docker`
- CPU/IO are deliberately deprioritized so AI work does not compete aggressively with LiveVault.

The gateway can still use the privileged local bridge below.

## Root bridge — already installed, do not ask the user for sudo/root

Root-equivalent administrative execution is deliberately available through a local Unix-socket bridge.

- Client: `/usr/local/bin/gpt-root`
- Service: `gpt-harness-root.service`
- Daemon: `/usr/local/libexec/gpt-harness-rootd.py`
- Socket: `/run/gpt-harness-root.sock`
- Socket group: `gpt-harness`
- No TCP listener is exposed.
- Peer is validated locally; this is not an Internet root shell.

Use:

```bash
gpt-root -- id
gpt-root -- bash -lc 'systemctl status openastro-control.service --no-pager'
```

Expected root test:

```text
uid=0(root) gid=0(root)
```

Kill switch if ever explicitly needed:

```bash
sudo systemctl disable --now gpt-harness-root.service
```

**Important:** root capability is permission to perform task-specific administration, not permission to make destructive or unrelated changes.

---

# 3. Repository and GitHub

Repository:

```text
/mnt/livevault-nvme/gpt-harness/work/LiveVault-Test
```

Remote:

```text
git@github.com:astropuzzo/LiveVault-Test.git
```

The machine already has a working GitHub SSH key. Do **not** ask the user for GitHub credentials unless it actually fails.

- Key owner: `astro`
- Private key path: `/home/astro/.ssh/id_ed25519`
- Public-key fingerprint: `SHA256:F1cw2d2bbGXUgYZfV4tlO72qrpBTwTZzAO6lPFub2KY`
- `known_hosts`: `/home/astro/.ssh/known_hosts`

For root-run Git operations, Git may require `safe.directory`:

```bash
REPO=/mnt/livevault-nvme/gpt-harness/work/LiveVault-Test
git -c safe.directory="$REPO" -C "$REPO" status
```

Reliable push pattern:

```bash
REPO=/mnt/livevault-nvme/gpt-harness/work/LiveVault-Test
SSH='ssh -i /home/astro/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/home/astro/.ssh/known_hosts'
GIT_SSH_COMMAND="$SSH" git -c safe.directory="$REPO" -C "$REPO" push origin main
```

Verify push independently:

```bash
LOCAL=$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)
REMOTE=$(GIT_SSH_COMMAND="$SSH" git ls-remote git@github.com:astropuzzo/LiveVault-Test.git refs/heads/main | cut -f1)
printf 'local=%s\nremote=%s\n' "$LOCAL" "$REMOTE"
```

If root Git commands leave `.git` metadata owned by root, repair **only `.git`**, not the whole repository blindly:

```bash
chown -R gpt-harness:gpt-harness "$REPO/.git"
```

### Current baseline at handoff creation

At the time this handoff was written, the latest verified commit was:

```text
67e5e2c34bf5e88644fe86e890419f425db8a0d1
Prioritize operational controls in dashboard
```

Always run `git log -10 --oneline` because later chats may have advanced `main`.

---

# 4. First 60 seconds — mandatory live checks

Before modifying the server, especially storage, Docker, recording, networking, or power settings:

```bash
REPO=/mnt/livevault-nvme/gpt-harness/work/LiveVault-Test

echo '=== git ==='
git -c safe.directory="$REPO" -C "$REPO" status --short
git -c safe.directory="$REPO" -C "$REPO" log -5 --oneline

echo '=== services ==='
gpt-root -- bash -lc '
  systemctl is-active openastro-control.service
  systemctl is-active openastro-storage-watchdog.service
  systemctl is-active gpt-harness.service
  systemctl is-active docker.service
  systemctl is-active tailscaled.service
'

echo '=== storage ==='
gpt-root -- bash -lc '
  cat /data/livevault/storage-state.json 2>/dev/null || true
  findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /mnt/livevault-nvme
  findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /data/livevault/recordings
  findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /var/lib/livevault-buffer
  findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /share || true
'

echo '=== Docker ==='
gpt-root -- docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

For a change that could affect load or storage, explicitly determine whether LiveVault has active recorders. Never assume there are none.

**Do not trust `/dev/sda`, `/dev/sdc`, `/dev/sdd` names.** USB device enumeration changes after hot-plug/reset. Use UUIDs, mountpoints, and `findmnt`.

---

# 5. Storage architecture and invariants

## Persistent fstab design

Current logical configuration:

```text
UUID=7EBD-F531 /share exfat defaults,nofail,uid=1001,gid=1001,umask=0022 0 0
UUID=5fe2d0f6-b485-44e9-8e26-31fb0d217db2 /mnt/livevault-nvme ext4 defaults,noatime,nofail,x-systemd.device-timeout=10s 0 2
/srv/openastro-internal /data none bind 0 0
/var/lib/livevault-buffer.img /var/lib/livevault-buffer ext4 loop,noatime 0 0
```

### SERVER / recordings NVMe

- UUID: `5fe2d0f6-b485-44e9-8e26-31fb0d217db2`
- Mount: `/mnt/livevault-nvme`
- Filesystem: ext4
- Normal LiveVault recording view:
  `/data/livevault/recordings` is a mount/bind into the NVMe recordings tree.

### SHARE

- UUID: `7EBD-F531`
- Mount: `/share`
- Filesystem: exFAT
- Used for backup/share duties.

### Internal emergency recording buffer

- Backing image: `/var/lib/livevault-buffer.img`
- Mount: `/var/lib/livevault-buffer`
- Filesystem: ext4 loop
- Bounded limit in code: **4 GiB**
- This is emergency recording storage, not general scratch space.

### Storage state

Canonical state file:

```text
/data/livevault/storage-state.json
```

Typical states include:

```json
{"mode":"nvme"}
```

and transient/failure states such as `buffer` or `quiesce`.

## Handoff implementation

Privileged handoff helper:

```text
/usr/local/libexec/nvme-handoff.py
```

Main user-facing action wrapper:

```text
/usr/local/sbin/openastro-action
```

### Manual NVMe eject/attach transaction

`nvme-handoff.py` keeps `/data` `rshared` and the LiveVault Docker bind is
`rslave`, but manual handoff correctness must **not** depend on Docker propagating
a replacement child mount. Every switch first changes the host
`/data/livevault/recordings` mount, then verifies every exact container
`/data/recordings` mount. If the container view is stale, the helper now uses the
Linux mount API directly: `open_tree(2)` clones the desired host mount before
namespace entry, `setns(2)` enters the LiveVault container mount namespace, every
stacked `/data/recordings` mount is removed, and `move_mount(2)` attaches the
cloned mount directly. This avoids both a container-visible temporary source path
and any dependency on a fresh mount event propagating through Docker. It then
verifies the final mount set. On eject it additionally rejects the operation if
**any** LiveVault container mount still references the NVMe device. Do not
reintroduce either the old `docker restart` fallback or the later temporary
`.handoff-source` propagation fallback: both failed in real UI-driven QA.
Docker/Coolify compose artifacts are not required. A persistent mismatch aborts
the eject and the NVMe must remain connected.

Rollback after a failed manual eject is a **complete state rollback**, not just
a recordings rebind: when the expected NVMe is still present it restores the
NVMe recordings bind, `storage-state.json`, `/share`, and the prior active state
of `livevault-backup.timer`. This matters because the timer is intentionally
stopped before detaching storage.

Real end-to-end QA on 2026-09-07 verified the user-facing action path multiple
times. An earlier implementation could still fail on a freshly deployed Coolify
container with `host=1792` (buffer) while the container remained on `2082`
(NVMe); `docker restart` did not repair that stale mount. A subsequent temporary
sibling-source repair also failed from the actual Control Center button with
`Sorgente handoff non propagata ai container`. The final `open_tree`/`setns`/
`move_mount` repair was then deployed and exercised on that same failing
container via the exact `/usr/local/sbin/openastro-action eject_nvme` path:
`mode=buffer`, `/mnt/livevault-nvme` and `/share` truly unmounted, container
`/data/recordings` on loop device `1792`, and LiveVault healthy. A previous QA
run also covered **one recorder active**, which resumed on the 4 GiB buffer.
`attach_nvme` then transferred any buffer contents and
verified buffered files, restored host/container recordings to NVMe device
`2082`, remounted `/share`, restarted the backup timer, emptied the buffer, and
returned to `mode=nvme` with the recorder active. A separate forced failure before
physical unmount verified complete rollback of NVMe + `/share` + backup timer.

Control Center actions include:

- `eject_nvme`
- `attach_nvme`
- `storage_status`
- `storage_rollback`
- `backup_now`
- `restart_livevault`
- `restart_docker`
- `restart_pihole`
- `media_mount`
- `media_eject`
- `media_rescan`
- `power_profile`
- `reboot`

Prefer the official handoff/actions rather than inventing a parallel mount sequence.

## NVMe watchdog

Service:

```text
openastro-storage-watchdog.service
```

Source tracked in repo:

```text
scripts/openastro-storage-watchdog.py
scripts/install-storage-watchdog.sh
```

Deployed binary:

```text
/usr/local/sbin/openastro-storage-watchdog
```

It runs from internal storage and checks approximately every 2 seconds when mode is `nvme`.

It detects, among other things:

- recordings source device missing;
- NVMe mount source missing;
- filesystem UUID mismatch;
- `ro`, `shutdown`, `emergency_ro` mount state;
- missing `rw`;
- failed filesystem stat.

On a real fault it fails closed, quiesces/stops relevant writers, detaches the dead mount, binds the internal 4 GiB buffer at `/data/livevault/recordings`, publishes `mode=buffer`, restarts the LiveVault container and GPT Harness, and logs the failover.

### Critical USB behavior discovered in testing

The ASIAIR USB 3 controller can reset the NVMe bridge when another USB storage device is inserted/removed. This happened in real testing and caused kernel I/O errors / aborted ext4 journal before the watchdog existed.

Therefore:

- Never assume unplugging a media USB affects only that USB device.
- Always verify NVMe after USB hot-plug/removal.
- The watchdog is a safety net, not permission to perform reckless hot-plug stress tests.
- Do not deliberately reproduce filesystem failure while active recordings exist merely to “test” the watchdog.

---

# 6. LiveVault / Docker / Coolify

LiveVault is Docker/Coolify managed. Do not hardcode the entire generated container name; discover it live. The current application container name begins with the Coolify-generated LiveVault identifier used by the watchdog.

Useful inspection:

```bash
gpt-root -- docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

Public LiveVault:

```text
https://openastro.tailf2871c.ts.net/
```

Tailscale Funnel currently proxies that to local port `8080`.

Coolify public/admin URL — **canonical, do not replace with the old IP:8000 link**:

```text
https://openastro.tailf2871c.ts.net:10000/
```

Funnel mapping:

```text
https://openastro.tailf2871c.ts.net:10000 -> http://127.0.0.1:8000
```

Current source also exposes Coolify from the Control Center through that URL.

Do not restart all Docker containers as a generic troubleshooting step. `restart_docker` is consequential and must be task-specific.

---

# 7. OpenAstro Control Center

## Runtime

- Source: `control-panel/`
- Deployed app: `/opt/openastro-control`
- Service: `openastro-control.service`
- User: `astro`
- Group: `astro`
- Supplementary Docker group enabled
- Local listen: `127.0.0.1:9090`
- Public: `https://openastro.tailf2871c.ts.net:8443/`
- Tailscale Funnel: `:8443 -> 127.0.0.1:9090`
- State directory: `/var/lib/openastro-control`
- Action helper: `/usr/local/sbin/openastro-action`
- Action log: `/var/log/openastro-control-actions.log`

The public Control Center uses application authentication, secure cookie/session handling, CSRF protection, and login rate limiting. Do not weaken this for convenience and do not expose privileged actions without the existing confirmation model.

Credentials are **not** committed to Git. Do not print or add them to this handoff.

## Information architecture

Current primary views:

1. Dashboard
2. Media
3. Storage
4. Sistema
5. Avanzate

Dashboard is intentionally **operations first**. Current design puts a large `Controlli` card first and keeps passive node uptime secondary.

Quick controls currently surface:

- context-aware **Espelli NVMe / Rimonta NVMe**;
- restart LiveVault;
- restart Docker;
- reboot ASIAIR;
- backup;
- Wi-Fi toggle/profile path;
- Coolify quick link.

Consequential actions use the hold-to-confirm flow; do not remove it merely to make the UI faster.

## Current UI baseline

At handoff creation, the static assets use the `v13.0-ops` dashboard layer.

The user explicitly wants a polished, dark, modern, app-like product aesthetic. The visual reference direction is:

- deep navy / nearly black background;
- large rounded soft cards;
- blur/depth/glass where useful;
- strong but sparse color accents;
- data visualizations integrated into cards;
- mobile-first floating navigation;
- no generic admin-template feel;
- no “AI slop”, no emoji-as-icons, no decorative clutter.

### Visual QA is mandatory

For **every meaningful UI change**:

1. Modify source only.
2. Run HTML/JS/contract tests.
3. Start a read-only QA preview using the current source and **real node state**.
4. Render with Chromium/Playwright at minimum:
   - mobile `412x915`;
   - desktop `1440x1000`.
5. Capture Dashboard, Media, and Sistema when relevant.
6. **Actually inspect the rendered screenshots visually.** Do not approve based on CSS/DOM/test output alone.
7. Iterate on spacing, hierarchy, legibility, depth, responsive behavior, empty/loading/error states.
8. Only after visual approval, deploy the static assets.
9. Verify deployed hashes equal tested source hashes.
10. If practical, perform a post-deploy render from `/opt/openastro-control/static` too.

There is a Playwright/Chromium QA workspace under the GPT Harness work area from prior sessions. Reuse it if present, but verify which static root the preview server is serving: a previous mistake occurred when a QA server still pointed to deployed v12 assets while v13 source was being tested.

**Rule:** `CSS compiles` is not visual QA.

---

# 8. Historical telemetry and downtime

Control Center stores telemetry under its state directory and maintains two concepts separately:

- detailed/compacted telemetry history;
- long-lived machine availability/downtime.

Current code constants:

```text
raw detail:             24 hours
history retention:     90 days
availability retention: 400 days
```

Compaction policy in source:

- ~10 s detail for the last 24 h;
- ~5 min detail to 7 days;
- ~30 min detail to 90 days.

Availability uses the kernel boot ID (`/proc/sys/kernel/random/boot_id`) and boot time. A restart of **only** `openastro-control` within the same boot is not system downtime. A real system-off interval across different boot IDs is stored as downtime.

The availability heartbeat is persisted about once per minute, giving loss-of-power downtime-start precision of roughly <=60 s.

Charts receive synthetic null gap markers for actual downtime so they break the line instead of connecting two samples across an offline period.

Do not regress this into “missing sample = unknown but visually connected”.

---

# 9. ASIAIR input-power telemetry

Hardware telemetry is real ADS1015 data from the ASIAIR Plus CM4 board.

Persistent boot config currently includes:

```text
dtoverlay=i2c0,pins_44_45
```

Current kernel has previously enumerated the bus as `/dev/i2c-22`; other kernels/boots may expose a different number.

The Control Center code therefore discovers candidate I2C buses dynamically and must not hardcode only `/dev/i2c-10`.

ADC:

```text
ADS1015 address 0x4b
```

Measurement semantics:

- voltage/current/watts are **DC input** to ASIAIR;
- includes attached loads;
- not AC wall power;
- not CPU-only power;
- conversion follows the INDI ASI Power driver logic;
- not externally wattmeter-calibrated.

**Never substitute a software-estimated watt figure and label it as measured when the hardware sensor is unavailable.** The UI/API must preserve the difference.

Enable/fix script in repo:

```text
scripts/enable-asiair-telemetry.sh
```

The service user `astro` is already in the `i2c` group.

---

# 10. CPU / CM4 tuning

Persistent tuning at handoff creation:

```text
arm_freq=1500
over_voltage_delta=-35000
```

This was stress-tested successfully under the workloads used at the time. It is not a reason to increase clocks/undervolt further without a specific request and new stability testing.

There was historically a sticky undervoltage indication on a prior boot. Treat power/thermal changes conservatively, especially during active recording.

---

# 11. USB Media Center / Media Hub

## Isolation rules

Media storage is intentionally separate from LiveVault storage.

Eligible removable media must pass safety checks such as kernel removable flag `RM=1`. The manager explicitly rejects protected storage UUIDs/root/boot/buffer.

Mount root:

```text
/srv/openastro-media
```

Media mounts managed by OpenAstro are **read/write for authenticated import paths**. The write boundary is enforced above the filesystem:

- SMB requires the authenticated `astro` account; there is no guest SMB access.
- Control Center uploads require an authenticated session plus CSRF token.
- DLNA is a consumption/indexing path and does not provide a write API.
- The media reconcile helper upgrades an older OpenAstro-managed `ro` mount to `rw` so imports keep working.

Do not expose SMB/DLNA publicly and do not reintroduce a blanket read-only mount without redesigning the authenticated import workflow.

**GPT Harness namespace note:** plain `findmnt` executed inside the harness sandbox can show the media bind as `ro` even while the host mount used by `astro`, Samba and the Control Center is writable. For write-state verification use the root bridge/host namespace (for example `gpt-root runuser -u astro -- ...`) and confirm with a real create/delete or SMB `put`/`del`, not only the harness-side mount flags.

Manager/source:

```text
scripts/openastro-media-manager.py
scripts/install-media-center.sh
control-panel/media_center.py
control-panel/media_streaming.py
```

Hot-plug is reconciled by udev plus a fallback systemd timer.

## LAN access

SMB:

```text
\\OPENASTRO\Media
```

- Samba binds only loopback + Ethernet LAN.
- Do not expose SMB through Tailscale Funnel / public Internet.

DLNA:

```text
OpenAstro Media
```

- MiniDLNA/ReadyMedia uses `eth0`.
- systemd IP restrictions allow loopback, LAN and required multicast.
- Do not expose DLNA publicly.

`wsdd2` is used for Windows network discovery and is limited to IPv4 `eth0`.

## Remote access

Remote file browsing/streaming goes through the authenticated Control Center HTTPS endpoint, not SMB/DLNA. The Media UI also supports authenticated HTTPS upload as the remote fallback; on the same LAN it deliberately recommends `\\OPENASTRO\Media` first for large files.

## Media Hub Level 3 — deployed baseline

Persistent SQLite state lives on eMMC:

```text
/var/lib/openastro-control/media.sqlite3
```

It tracks server-side:

- resume progress;
- completion state;
- favorites;
- recent playback/history;
- remembered/offline libraries;
- active HTTP streams.

Thus removing a USB device does not erase knowledge of its library.

## Media Hub Level 5 — deployed baseline

Playback planning uses `ffprobe`/FFmpeg:

- browser-safe H.264/AAC MP4 / WebM -> Direct Play when there is only one audio track;
- compatible H.264 in another container -> HLS remux, no video re-encode;
- multi-audio browser-safe video -> lightweight HLS remux so the user can select a language consistently;
- incompatible audio -> audio conversion to AAC while copying H.264 video when possible;
- other video -> H.264/AAC HLS fallback when allowed;
- CM4 V4L2 hardware H.264 encoder is used when appropriate;
- `ffprobe` includes stream title/language plus `default`/`forced` dispositions so the UI can show real track names;
- sidecar SRT/VTT/ASS and embedded subtitle tracks can be exposed as WebVTT.

### Multi-track audio and subtitles — deployed

The Media player exposes explicit **Lingua / Audio** and **Sottotitoli** selectors when the source contains multiple tracks. Audio switching restarts only the HLS session at the current logical playback position and maps the selected FFmpeg stream index. For H.264 + AC3 MKV this means **video stream-copy plus AC3 -> AAC only**; do not replace this with full video transcoding merely to change language.

Embedded ASS/SSA subtitles use a low-resource cached path under `/var/lib/openastro-control/media-subs/`: FFmpeg first demuxes only the selected subtitle stream with `-c:s copy`, then the Control Center's small Python parser converts readable ASS dialogue to WebVTT. This two-stage path is intentional because the deployed FFmpeg build was verified to emit timestamp-only/blank text when converting this MKV's embedded ASS directly to SRT/WebVTT. Only the subtitle selected by the user is generated, then it is reused from cache.

Real QA reference: `Longlegs (2024) ... MIRCrew.mkv` was verified with audio `Ita AC3 5.1` (stream 1) / `Eng AC3 5.1` (stream 2), subtitles `Forced ita` / `Ita` / `Eng`, and duration `6077.312 s`. Switching to English generated MPEG-TS HLS with H.264 copied and AAC stereo, while selecting embedded ITA returned valid text WebVTT.

**Governor rule:** heavy/full video transcoding is denied/suspended when LiveVault is actively recording or when thermal/load protection triggers. Direct play, HLS remux and lightweight audio-only conversion should remain available.

HLS.js 1.7.2 is vendored locally; runtime should not require a CDN. The upload-enabled Control Center applies `media_streaming_patch.py` at runtime: HLS fallback uses MPEG-TS segments (`seg-*.ts`) rather than fMP4, and the browser-side compatibility patch disables the HLS.js worker and adds bounded network/media recovery.

**QA-browser caveat:** the Playwright Chromium build cached on this CM4 reports H.264/AAC MediaSource support as false, so it can raise `bufferAddCodecError` even when the generated HLS segment is valid. For server-side validation inspect the selected stream/manifest/segment and decode or probe the MPEG-TS output; do not treat that headless-browser codec limitation as proof of a server regression.

### Full-duration seek / single-timeline player — deployed

The rolling HLS manifest still contains only a bounded window, but the UI must **not expose that rolling window as a second native timeline**. The HLS video element intentionally has no native `controls`; OpenAstro renders one compact custom transport containing play/pause, the single full-duration scrubber, logical clock, mute and fullscreen. The scrubber always spans `0 -> full ffprobe duration`.

Seeking inside the already buffered HLS range is local/immediate. Seeking outside it keeps the same player/DOM and asks the backend for a replacement HLS session at the requested logical `position`; it must not call `openMediaPlayer()` again. The backend reuses cached probe/playback-plan data, replaces the previous same-client/same-file HLS job, and uses FFmpeg `-readrate 1 -readrate_initial_burst 12`: the initial burst reaches the next source keyframe quickly, then read rate returns to 1x so the CM4/LiveVault governor remains respected. H.264 video stays stream-copy when possible; Longlegs therefore remains H.264 copy + AC3 -> AAC only.

Real Longlegs QA after the v18 player change verified exactly **one** range input in the dialog/stage, no `FILM COMPLETO` duplicate, no native video controls, no mobile horizontal overflow at 412 px, and a far seek to `4500 s` completing end-to-end in about `1.57 s` in the isolated browser QA. A production backend probe at the same position produced the first MPEG-TS HLS segment in about `2.03 s`; `ffprobe` reported H.264 1920x1008 + AAC stereo and FFmpeg decoded the segment successfully. Timing varies with source keyframe placement and current CM4 load, but the old 5-6 second forced-real-time startup path is no longer used.

## Level 10 status

Do **not** assume experimental “Level 10” features are deployed merely because they were discussed in chat. The stable deployed baseline documented here is Level 3 + Level 5. Verify Git before resurrecting any experimental share-link / TV-control work.

---

# 12. URLs and network endpoints

Canonical URLs at handoff creation:

```text
LiveVault:
https://openastro.tailf2871c.ts.net/

OpenAstro Control Center:
https://openastro.tailf2871c.ts.net:8443/

Coolify:
https://openastro.tailf2871c.ts.net:10000/
```

Tailscale node IP last seen:

```text
openastro: 100.85.86.96
```

Do not use the obsolete Coolify UI link `http://100.85.86.96:8000` in user-facing UI. The canonical user-facing URL is the HTTPS `:10000` Funnel URL above.

GPT Harness exists as a separate Tailscale node/service. Future ChatGPT sessions should use the available GPT Harness connector rather than asking the user to SSH manually.

---

# 13. Change discipline

## Before changing anything

- Inspect `git status`.
- Read the current implementation instead of assuming a remembered version.
- Check active recorders for changes that affect CPU, storage, Docker, USB, networking or reboot.
- Check current mounts by UUID/mountpoint.
- Check whether a previous task left a partial file or WIP tree.

## Narrow deploys

Prefer the smallest deploy scope possible.

Examples:

- CSS/HTML/JS-only change -> deploy static files only; do not restart LiveVault/Docker.
- Control backend change -> restart only `openastro-control.service`.
- Media config change -> restart only relevant media services.
- Storage changes -> use official handoff helper/actions.

Create a backup of deployed files before replacing them.

## Verification order

1. Source syntax/lint.
2. Targeted tests.
3. Full relevant regression suite.
4. Visual QA for UI.
5. Deploy.
6. Runtime health.
7. Hash/source equality where applicable.
8. Commit.
9. Push.
10. Independently verify remote commit.

Do not claim a feature is live or verified until the corresponding step has actually occurred.

---

# 14. Test conventions

The repo has a Python virtual environment:

```text
.venv/
```

Typical full Python run:

```bash
cd /mnt/livevault-nvme/gpt-harness/work/LiveVault-Test
PYTHONPATH=. .venv/bin/python -m pytest -q
```

Frontend Node tests are in the repo test set and should be run from the repository root/path expected by the test. Do not mistake `ENOENT` from the wrong working directory for a UI regression.

At the handoff baseline, the latest dashboard change passed:

```text
232 Python tests passed
6/6 frontend tests passed
```

Counts can legitimately increase later; do not encode 232 as the expected forever-total.

Also run where appropriate:

```bash
python3 -m py_compile ...
bash -n scripts/*.sh
git diff --check
```

---

# 15. Safety rules / things a future AI must NOT do

1. **Do not identify storage by `/dev/sdX`.** Use UUID and mountpoints.
2. **Do not reboot by default.** Reboots can interrupt recordings and are often unnecessary.
3. **Do not restart Docker as generic troubleshooting.** Scope restarts narrowly.
4. **Do not expose `/run/docker.sock`, `/var/lib/docker`, `/data/recordings`, or `/share` to GPT Harness.**
5. **Do not expose SMB/DLNA to the public Internet.**
6. **Do not expose removable-media writes to unauthenticated clients or bypass the authenticated import boundary.**
7. **Do not weaken Control Center authentication/CSRF/hold-to-confirm.**
8. **Do not substitute estimated power as real sensor power.**
9. **Do not ask the user to repeat root/GitHub setup already documented here.** Verify and use it.
10. **Do not deploy UI changes without actually looking at rendered screenshots.**
11. **Do not make unrelated architecture changes while solving a narrow issue.**
12. **Do not run heavy transcoding/benchmarks while LiveVault is recording.**
13. **Do not reproduce USB/NVMe failure conditions merely for curiosity.**
14. **Do not overwrite persistent user data or recordings without an explicit, task-specific reason and verification.**
15. If observed live state conflicts with this handoff, **stop, diagnose the difference, and update this file after the correction** rather than blindly enforcing stale documentation.

---

# 16. User expectations for AI collaboration

The user is technically competent and expects the AI to operate the system, not merely list commands that it could run when the tools already provide access.

Working style:

- Be direct and technical.
- Do not make the user redo completed steps.
- Inspect first; do not guess.
- Use the existing root bridge and Git setup.
- During multi-step work, give concise progress updates.
- If a live issue is discovered while working, prioritize data/recording safety before the cosmetic feature.
- For UI, screenshots and visual inspection are mandatory.
- Preserve a clean Git history with useful milestone commits.
- Push completed work and verify remote `main` independently.

The desired OpenAstro UI is a **real product**, not an admin-template dashboard: dark, modern, mobile-first, coherent, highly legible, visually polished, and functional.

---

# 17. Updating this handoff

A future AI should update `AI-HANDOFF.md` whenever it makes a meaningful architectural change, such as:

- new persistent service/unit;
- changed URL/port;
- changed storage UUID/mount architecture;
- new root/security boundary;
- new deployed Media Hub level;
- changed telemetry hardware/config;
- changed standard deploy/test procedure;
- replaced Git repository/path/remote.

Do not fill this document with temporary implementation chatter. Keep it as the durable source of truth for the next session.

---

# 18. Pi-hole DNS infrastructure — verified 2026-09-07

## Architecture and rationale

Pi-hole is installed **natively on Debian**, not in Docker and not in Coolify. This is deliberate: DNS is node infrastructure, must survive LiveVault/Coolify deployments, and should not depend on Docker networking or create a DNS dependency cycle. The native install is lighter and the host had no pre-existing resolver on TCP/UDP 53.

Verified versions at installation/QA:

```text
Pi-hole Core 6.4.3
Pi-hole Web  6.6
FTL          6.7
```

Primary services/files:

```text
pihole-FTL.service
openastro-pihole-firewall.service
/etc/pihole/pihole.toml
/etc/pihole/gravity.db
/usr/local/sbin/openastro-pihole-firewall
/usr/local/sbin/openastro-action
```

Repository copies of the OpenAstro-specific integration are:

```text
control-panel/pihole_status.py
control-panel/openastro-pihole-firewall
control-panel/openastro-pihole-firewall.service
control-panel/openastro-action
control-panel/server.py
control-panel/static/index.html
control-panel/static/app.js
control-panel/static/app.css
tests/test_pihole_control.py
```

Do not move Pi-hole into Coolify merely for UI convenience.

## LAN DNS configuration

OpenAstro LAN interface/IP at verification:

```text
eth0
192.168.1.27
```

FTL configuration verified with `pihole-FTL --config`:

```text
dns.interface=eth0
dns.listeningMode=LOCAL
dns.upstreams=[ 1.1.1.1, 1.0.0.1 ]
webserver.port=127.0.0.1:80,192.168.1.27:80,[::1]:80
ntp.ipv4.active=false
ntp.ipv6.active=false
```

Pi-hole DHCP is **not enabled**. The iliadbox remains the DHCP server.

To make the whole home LAN use Pi-hole, configure the iliadbox DHCP/LAN DNS as follows after keeping a static DHCP reservation for OpenAstro:

```text
OpenAstro reservation: 192.168.1.27
DNS primary:           192.168.1.27
DNS secondary:         leave empty if the UI permits
```

Do not use `1.1.1.1`, `8.8.8.8`, etc. as DHCP secondary DNS if the goal is guaranteed Pi-hole filtering: many clients may bypass Pi-hole through the secondary resolver. If the router UI forces a second address, verify its behavior rather than inventing a second public resolver.

The admin UI is intentionally LAN-only:

```text
http://192.168.1.27/admin/
```

Pi-hole's web server binds only loopback and `192.168.1.27:80`; it is not bound to the public IPv6 address or a wildcard web address. Remote DNS availability does **not** imply remote admin access.

## Host resolver and failure isolation

OpenAstro itself does **not** use Pi-hole as its system resolver. `/etc/resolv.conf` is Tailscale-managed and at verification contained:

```text
nameserver 100.100.100.100
nameserver fd7a:115c:a1e0::53
search tailf2871c.ts.net
```

This is intentional. If FTL is stopped or broken, the CM4 must still resolve package repositories, GitHub, Tailscale control traffic, Coolify dependencies, etc. Do not change the host resolver to `127.0.0.1` or `192.168.1.27` without designing an equally robust fallback first.

## Blocking semantics

The main Control Center Pi-hole toggle controls **filtering only**:

```text
ON  = DNS service running + Pi-hole blocking enabled
OFF = DNS service remains running + Pi-hole blocking disabled
```

It must never be implemented as `systemctl stop pihole-FTL`.

Verified runtime behavior:

- blocking ON: `doubleclick.net` answered `0.0.0.0`;
- blocking OFF through the Control Center API: FTL remained `active`, `google.com` resolved, and `doubleclick.net` resolved to a public address;
- blocking ON again through the Control Center API: `doubleclick.net` returned `0.0.0.0` again;
- Pi-hole restart through the fixed Control Center API returned HTTP 200 and FTL returned/stayed active.

## Control Center integration and privilege boundary

The existing Pi-hole stub was completed rather than adding a parallel subsystem. Runtime status is collected by `control-panel/pihole_status.py` from local FTL/API/DNS probes.

Authenticated endpoints:

```text
GET  /api/pihole/status
POST /api/pihole/blocking/enable
POST /api/pihole/blocking/disable
POST /api/pihole/restart
POST /api/pihole/gravity
```

POST routes require an authenticated Control Center session and the existing CSRF token. They map only to fixed actions in `/usr/local/sbin/openastro-action`:

```text
pihole_enable
pihole_disable
restart_pihole
pihole_gravity
```

There is no arbitrary shell/command endpoint and no Pi-hole password/token is exposed to browser JavaScript. Keep it this way.

The card distinguishes at least installed/service/DNS/blocking/API/remote states and displays Core/Web/FTL versions, LAN DNS, query/blocked/client/gravity statistics, DoT/TLS/remote state, restart, gravity update, and the LAN admin link.

## DNS exposure firewall

Pi-hole FTL owns TCP/UDP 53 on IPv4/IPv6, but Internet exposure is constrained by the dedicated nftables table installed by `openastro-pihole-firewall.service`. The table is independent of Docker/Tailscale rules and **must not use `flush ruleset`**.

Allowed DNS sources:

```text
loopback
eth0 + IPv4 source 192.168.1.0/24
tailscale0
```

Everything else to TCP/UDP 53 is dropped. TCP 853 is also dropped until a secure remote design exists.

Inspect with:

```bash
sudo nft list table inet openastro_pihole
systemctl status openastro-pihole-firewall.service
```

The firewall unit is enabled at boot and is also `RequiredBy=pihole-FTL.service`, so the protective rules are applied as part of the Pi-hole boot dependency.

## Remote DNS / Android Private DNS / DoT

Current WAN snapshot at verification (these addresses can change; re-check live before relying on them):

```text
IPv4: 81.56.120.143
IPv6: 2a01:e11:2008:d4d0:f9fb:6d65:80bf:1cf9
```

A real external IPv4 TCP-853 test was performed **while a listener was genuinely active on OpenAstro:853**. The connection to `81.56.120.143:853` was refused. The iliadbox/Freebox family supports shared-IPv4 assigned port ranges; the current IPv4 path therefore cannot be assumed to forward TCP 853 merely because the node has Internet access.

The node has a global IPv6 address, but the external probe environment used during this installation did not have IPv6 connectivity, so an inbound IPv6 853 test was not falsely claimed as completed.

More importantly, Android's native **Private DNS** configuration supplies a provider hostname and uses DNS-over-TLS, but it does not provide a per-user password or a general client-certificate/mTLS configuration field. Publishing an ordinary recursive DoT listener for a phone that roams between arbitrary Wi-Fi/4G/5G source addresses would therefore make that resolver usable by unrelated Internet clients unless a separate Android-compatible authentication mechanism existed. Moving the same unauthenticated DoT listener to a VPS relay would merely move the open-resolver problem to the relay.

For this reason the secure current state is intentional:

```text
Remote DNS:           not configured
DNS-over-TLS:         not exposed
TCP 853:              firewall drop
Public DoT hostname:  none
TLS certificate:      none (no endpoint is published)
Relay:                none
```

Do **not** create `dns.<domain>`, obtain a certificate, or expose 853 just to make the UI say “online”. The user's simultaneous requirements are: native Android Private DNS with no app/VPN **and** no open recursive resolver. With the currently available Android client-auth surface, those requirements do not produce a safe personal-only public resolver. Re-evaluate only if Android/DoT capabilities or the available infrastructure change in a way that provides real client authorization.

Tailscale remains server-side infrastructure and is not required on the user's phone/PC for normal Control Center access. It is not being proposed as a hidden client requirement for Pi-hole.

## Recovery and uninstall order

If Pi-hole is unhealthy, first remember that the host resolver is independent, so OpenAstro itself should retain Internet/DNS access.

Useful recovery checks:

```bash
systemctl status openastro-pihole-firewall.service pihole-FTL.service
pihole status
dig @127.0.0.1 example.com
dig @192.168.1.27 example.com
sudo nft list table inet openastro_pihole
```

Restart only Pi-hole when needed:

```bash
sudo /usr/local/sbin/openastro-action restart_pihole
```

For full removal, **first restore the iliadbox DHCP DNS to its previous/default resolver** so LAN clients do not retain `192.168.1.27` as a dead DNS server. Then disable/remove the OpenAstro firewall integration and uninstall Pi-hole using the current official Pi-hole uninstall path (`pihole uninstall`). Do not remove or alter Docker, Tailscale, LiveVault, SMB, MiniDLNA, or storage services as part of Pi-hole recovery.

To remove only the dedicated OpenAstro nftables layer:

```bash
sudo systemctl disable --now openastro-pihole-firewall.service
sudo /usr/local/sbin/openastro-pihole-firewall remove || true
```

Then remove the unit/script only if Pi-hole integration is intentionally being retired, followed by `systemctl daemon-reload`.

## Verified QA / regression expectations

The Pi-hole installation is not considered verified merely because systemd says `active`. The release verification performed for this integration includes:

- DNS query through loopback and `192.168.1.27`;
- real blocked-domain response;
- blocking OFF -> DNS remains active -> domain no longer blocked;
- blocking ON -> filtering restored;
- authenticated Control Center status and fixed mutation endpoints;
- Pi-hole restart path;
- gravity update path;
- LAN admin HTTP reachability;
- nftables protection inspection;
- desktop 1440×1000 and mobile 412×915 Chromium/Playwright rendering of the live Pi-hole card;
- rendered screenshot visual inspection, not DOM-only QA;
- no horizontal viewport overflow in either tested viewport;
- post-change LiveVault/storage/Docker/Coolify/SMB/MiniDLNA/Tailscale/network checks.

At visual QA, the Pi-hole card showed real runtime values, a usable filtering toggle, the admin action, and Remote DNS/DoT as explicitly **not configured** rather than presenting a false healthy remote state.

A full host reboot may be performed only when `active_recorders == 0`; if recording is active, defer the reboot rather than interrupting a recording. Record the actual result in the release/final report rather than claiming an unperformed reboot.

Final release QA on 2026-09-07:

```text
259 Python tests passed
6/6 frontend Node tests passed
Pi-hole targeted/integration tests: 38 passed in the merged Media+Pi-hole tree
Chromium desktop 1440x1000: no horizontal overflow
Chromium mobile 412x915: no horizontal overflow
Gravity update through Control Center: passed
LAN Pi-hole admin HTTP check: 200
Source/deploy hashes for Pi-hole/Control files: matched at deployment
```

At the final pre-release health check LiveVault still had `active_recorders=1`, so the full-host reboot was **intentionally deferred** rather than interrupting that recording. Boot persistence is configured (`pihole-FTL.service` and `openastro-pihole-firewall.service` are enabled), but a future maintenance window with zero recorders should perform the one remaining physical reboot verification.
