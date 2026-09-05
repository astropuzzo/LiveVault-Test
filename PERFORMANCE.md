# Recording performance investigation — 2026-09-05

Prepared against GitHub `origin/main` at `dfde126`, in branch
`codex/recording-efficiency`. The sensor bus was enabled and verified on the live host; application rollout follows green CI.

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
