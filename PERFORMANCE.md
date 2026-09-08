# Recording performance investigation — 2026-09-05

## September 8 maintenance verification

Fixed the Control Center service-worker precache: it requested a nonexistent
pihole.css, causing installation failure and repeated full shell downloads.
Archive scans/remux now yield safely to storage handoff and stop queued work
between files; the acknowledgement no longer rewrites eMMC four times/second.
APT download cache fell from 133 MB to 20 KB; 23 obsolete one-shot scripts were
archived with SHA-256, and two empty storage-probe directories were removed.

After the verified NVMe/buffer/NVMe cycle on the newer ba26e23 release, both apps
were healthy, no systemd services had failed, RAM available was 2.6 GiB, and the
internal filesystem was 55% used. Two one-second samples showed 38–59% CPU idle
and zero I/O wait while capture/recovery were running. Earlier post-deploy samples
had 21–28% I/O wait: these different workloads do not establish a speedup ratio.
Historical undervoltage remains recorded (0x50000), without active throttling;
software maintenance cannot certify the physical power supply or USB cable.

Prepared against GitHub `origin/main` at `dfde126`, in branch
`codex/recording-efficiency`. Initial release `ecbae19` deployed successfully after
Linux CI passed. Authenticated active-capture playback returned an uncompressed
1024-byte HTTP 206 response. Both applications were healthy.

## Findings and changes

- Active capture playback (`/api/sources/{id}/capture`) incorrectly entered the
  text gzip middleware. Exclude it alongside recorded video, preserving byte
  ranges and avoiding compression work on already-compressed video.
- Storyboards opened nine automatically threaded decoders. Limit each decoder,
  the filter graph, and JPEG encoder to one thread; trim each tile to one frame
  and reset its timestamps before stacking. All nine positions, the 960x540
  output, and JPEG quality remain the same.
- Single-frame fallback and live JPEG extraction also use bounded decoder,
  filter, and encoder threads. Preview cadence remains unchanged.

Recording video stream-copy, audio synchronization, camera limits, background
processing, uploads, integrity checks, and recording quality are unchanged.

After deployment, a backlog A/V repair consumed about 278% process CPU (nearly
three cores). Background repair/stitch fallback now uses half the CPU count for
video encoding (two threads on this host), with single-thread decoding/filtering.
The existing preset and CRF remain unchanged. Deadlines increase proportionally
so healthy repairs can finish with the smaller pool. This preserves repair and
multitasking, with longer processing latency as the tradeoff.

## Measurements

Read-only SSH samples on the four-core Cortex-A72 server found approximately
23–26% I/O wait and 38–48% user+system CPU in three one-second intervals.
These short samples do not establish peak or sustained load. Temperature was
approximately 64–69 C during the investigation.

`vcgencmd get_throttled` returned `0x50000`: historical undervoltage and
throttling flags, without current undervoltage/throttling flags. This is a
separate issue to investigate; software changes cannot establish that the power
supply problem is resolved. Flag definitions:
https://www.raspberrypi.com/documentation/computers/os.html#get_throttled

Local Windows benchmark, generated 8-second 1280x720 H.264 source, two runs per
implementation, FFmpeg's `-benchmark` measurements:

| Measurement | Original | Optimized |
| --- | --- | --- |
| CPU time (user + system) | 1.078 / 2.187 s | 0.625 / 0.672 s |
| Peak memory | 667688 / 714136 KiB | 72124 / 71860 KiB |
| Wall time | 0.290 / 0.291 s | 0.382 / 0.379 s |

The benchmark demonstrates reduced per-storyboard resource use, with a small
latency tradeoff on this PC. It does not measure server-wide CPU, power, or
temperature savings. The reproducible local script and images are in the
ignored `.qa/` directory of the performance worktree.

## Validation

- Seven targeted media/compression tests passed with real FFmpeg, including
  960x540 storyboards, 640x360 live previews, integrity checks, and uncompressed
  active-capture range responses.
- Full Python suite on Python 3.12: 194 passed, 1 skipped. The existing stale
  service-worker assertion and Windows HLS fixture working directory were corrected.
- Python compilation, all JavaScript syntax checks, six panel JavaScript tests,
  and `git diff --check` passed.
- Python 3.14 cannot collect the existing full suite because the upstream Pulse
  extension rewrites bytecode constants. Production uses Python 3.13; no change
  to that unrelated mechanism was made.

## Rollout

Validate on the production Linux/Python 3.13 toolchain before promoting to main. Follow HOSTING.md for backup and
deployment. A deployment restarts LiveVault and can briefly interrupt active
captures, so choose the recording interruption window with the user.

After deployment, compare matching workloads (same cameras, viewers, and
processing jobs), CPU and I/O wait, temperature, recording byte growth, A/V
integrity, and active playback range responses. No wattmeter measurements or
server-wide savings percentage are claimed.

## Hardware power telemetry

The ASIAIR carrier board has three responding ADS1015 ADCs on I2C bus 10.
The input ADC at 0x4b returned 12.0435 V and 0.670 A (about 8.07 W) during
bring-up. The panel now uses these hardware readings, with the INDI driver
conversion factors; external wattmeter calibration has not been performed.
Historical software estimates are retained separately and excluded from measured
Wh. Missing samples and gaps over 30 seconds are excluded from energy coverage.
See control-panel/README.md for setup and measurement scope.

## September 6 follow-up

The latest fetched main remained ecbae19; the interrupted session had not promoted
c178911. The live CPU saturation was a repeated failed A/V repair of one unchanged
fragment. Background FFmpeg now starts with affinity restricted to half the available
CPUs and lower scheduling priority. Failed unchanged MP4 repairs wait 15 minutes,
then progressively longer up to two hours, before retrying. Changed media retries
immediately. Shutdown cancellation does not count as a repair failure.

Expired Stripchat 404/410 fragments trigger bounded public-playlist refresh within
the same capture process. Completed media before a gap is finalized separately;
init-only files are no longer submitted to remux. Persistent CDN failures remain
visible after bounded retries. No access-state restrictions are bypassed.

OpenAstro Control now uses a dark palette for the page, cards, controls, charts,
and login. Asset cache versions were changed so existing installations refresh.

The actual looping fragment had 162.322 s video and 160.021 s audio. A stream-copy
trim to their common duration passed the existing media validator in 2.98 seconds
on the live host, using a disposable output and leaving the original untouched.
Tail-only timing repair now tries this before any video transcode. The integration
test asserts that a tail-only mismatch never needs the encoder when copy validates.

## September 8 telemetry persistence optimization (branch only)

Read-only production baseline before this change: `/var/lib/openastro-control/history.json`
was 1,634,479 bytes and was being atomically rewritten once per minute. At constant
size that is about 2.35 GB/day of logical file replacement, before filesystem effects;
this is not a NAND-wear measurement.

The optimization branch replaces the minute-wide history rewrite with SQLite/WAL tiers:
10-second samples for 24 hours, 5-minute samples through 7 days, and 30-minute samples
through 90 days. Six new samples are committed together once per minute; only rows that
cross a retention boundary are promoted between tiers. The existing `history.json` is
imported once and deliberately left untouched. Availability heartbeat cadence remains
one minute and sampling remains 10 seconds. The history API still uses the same in-memory
shape, ordering, downtime gaps, power semantics and retention.

Before rolling back to a release that only understands JSON, export current SQLite data
in the old format with the deployed control script:
`python3 /opt/openastro-control/upload_server.py --export-history-json /var/lib/openastro-control/history.json`.
This is an explicit rollback step; normal operation does not resume the large JSON rewrite.
No production after-measurement is claimed until this branch is deployed and observed on
a matched workload.
