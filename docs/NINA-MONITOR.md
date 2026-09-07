# OpenAstro NINA Monitor

The NINA view is a read-only remote session monitor hosted by OpenAstro Control on the modified ASIAIR node.

## Architecture

```text
N.I.N.A. + QualitySessionMeter (Windows)
        |
        | trusted LAN / Tailscale, tokenized HTTP
        v
OpenAstro Control (ASIAIR, eMMC runtime)
        |
        | existing authenticated HTTPS panel
        v
phone / tablet / remote browser
```

The browser never connects directly to the N.I.N.A. PC. OpenAstro Control is the only remote-facing frontend. The feature does not depend on the SERVER NVMe or removable Media USB; configuration and runtime remain on internal eMMC with the rest of Control Center.

## QSM side

Install a QualitySessionMeter build containing `QualitySessionHttpBridge` and start N.I.N.A. with these environment variables:

```text
QSM_REMOTE_TOKEN=<long-random-secret>
QSM_REMOTE_PORT=18973
QSM_REMOTE_BIND=*
```

`QSM_REMOTE_TOKEN` is mandatory. If it is absent the QSM HTTP bridge does not start. The bridge exposes only read operations:

```text
GET /healthz
GET /api/v1/snapshot
GET /api/v1/preview.jpg
```

The `/api/v1/*` routes require `X-QSM-Token` or `Authorization: Bearer ...`. There are no write/control routes. Keep Windows Firewall limited to the trusted LAN/Tailscale path used by the OpenAstro node.

## OpenAstro side

Create `/etc/openastro-nina-monitor.env`:

```bash
OPENASTRO_NINA_QSM_URL=http://<NINA-PC-LAN-OR-TAILSCALE-IP>:18973
OPENASTRO_NINA_QSM_TOKEN=<same-long-random-secret>
```

Protect it:

```bash
sudo chown root:astro /etc/openastro-nina-monitor.env
sudo chmod 0640 /etc/openastro-nina-monitor.env
sudo systemctl daemon-reload
sudo systemctl restart openastro-control
```

OpenAstro exposes authenticated same-origin endpoints to its frontend:

```text
GET /api/nina/state
GET /api/nina/diagnostics
GET /api/nina/preview.jpg
```

The QSM token is never returned to the browser.

## Current UI slice

The `NINA` section currently shows:

- QSM connectivity and ASIAIR-to-PC latency;
- session active/idle state;
- current Quality and Confidence;
- full-session captured / usable / rejected counters and acceptance rate;
- target, filter, exposure, gain/binning and camera;
- star/background deltas and current QSM status/cause;
- **real live guiding** from N.I.N.A. `GuideEvent`, not reconstructed from the last exposure:
  - last 20 seconds;
  - RA RMS;
  - DEC RMS;
  - total RMS;
  - max excursion;
  - timestamped RA/DEC trace;
- recent Quality/Confidence/exposure-RMS session trend;
- recent warning/reject/error list;
- latest LIGHT preview through an authenticated same-origin image route.

The state poll runs every second only while the NINA page is active.

## Preview policy

The browser never receives a FITS path or the QSM secret. QSM generates a display-only JPEG from the N.I.N.A. `ImageSaved` bitmap:

- maximum width 1280 px;
- JPEG quality 82;
- encoded in RAM;
- FITS is not re-read;
- no preview file is persisted on ASIAIR/eMMC/NVMe;
- OpenAstro fetches the JPEG only when the QSM frame index advances.

This avoids high remote bandwidth and keeps preview traffic independent from LiveVault storage.

## Safety boundary

The monitor remains read-only. Remote sequence control, threshold changes, file mutation and other write actions are intentionally outside this first release until the monitoring path has been field-tested.
