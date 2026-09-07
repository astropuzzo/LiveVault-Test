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

`QSM_REMOTE_TOKEN` is mandatory. If it is absent the QSM HTTP bridge does not start. V1 exposes only:

```text
GET /healthz
GET /api/v1/snapshot
```

The snapshot route requires `X-QSM-Token` or `Authorization: Bearer ...`. There are no write/control routes.

Keep Windows Firewall limited to the trusted LAN/Tailscale path used by the OpenAstro node.

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
```

The QSM token is never returned to the browser.

## Current UI slice

The `NINA` section currently shows:

- QSM connectivity and ASIAIR-to-PC latency;
- session active/idle state;
- current Quality and Confidence;
- latest exposure-integrated Guide RMS;
- captured / usable / rejected counters and acceptance rate;
- target and filter;
- star/background deltas;
- current QSM frame state/cause;
- recent Quality/Confidence/RMS graph;
- recent warning/reject/error list.

Polling is 2 seconds and only runs while the NINA page is active.

## Next channels

Two data channels remain intentionally separate instead of being faked from QSM frame summaries:

1. **real-time guiding** — direct PHD2 telemetry (RA/DEC/total RMS and guide trace);
2. **latest-image preview** — lightweight JPEG/WebP generated from the current N.I.N.A. image, never the full FITS payload.

The monitoring surface stays read-only until those channels are stable and field-tested.
