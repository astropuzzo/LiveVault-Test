# OpenAstro NINA Monitor

The NINA Monitor is a **standalone read-only Coolify application** running on the modified ASIAIR/OpenAstro node. It is deliberately isolated from LiveVault, OpenAstro Control Center, Media Center and host-control helpers.

## Architecture

```text
N.I.N.A. + QualitySessionMeter (Windows)
        |
        | trusted LAN / Tailscale, tokenized HTTP
        v
openastro-nina-monitor (dedicated Coolify container)
        |
        | HTTPS + its own login/session
        v
phone / tablet / remote browser
```

The browser never connects directly to the N.I.N.A. PC and never receives the QSM token. The monitor container has no SERVER NVMe mount, no Media USB mount and no Docker socket. Preview/session state is memory-only.

## QSM side

Install QualitySessionMeter 1.1+ and start N.I.N.A. with:

```text
QSM_REMOTE_TOKEN=<long-random-secret>
QSM_REMOTE_PORT=18973
QSM_REMOTE_BIND=*
```

`QSM_REMOTE_TOKEN` is mandatory. If it is absent, the remote QSM HTTP bridge does not start.

QSM exposes only read operations:

```text
GET /healthz
GET /api/v1/snapshot
GET /api/v1/preview.jpg
```

The `/api/v1/*` routes require `X-QSM-Token` or `Authorization: Bearer ...`. There are no remote write/control routes. Keep port `18973` reachable only from the trusted LAN/Tailscale path used by the ASIAIR/OpenAstro node; do not forward it publicly.

## Coolify side

Create a **new Coolify Application**, separate from LiveVault:

```text
Repository: astropuzzo/LiveVault-Test
Branch: main
Base directory: /nina-monitor
Build pack: Dockerfile
Dockerfile: /Dockerfile
Container port: 9091
Health check: /healthz
Auto Deploy: ON
Watch Paths: nina-monitor/**
```

The application should be named `openastro-nina-monitor` and keeps its own logs, resource limits and deployment history. On this OpenAstro node the canonical remote browser route is `https://openastro.tailf2871c.ts.net:8443/nina/`, published by Tailscale Funnel to the dedicated container. The direct LAN route remains `http://192.168.1.27:9091/`.

Required application secrets/environment variables:

```text
OPENASTRO_NINA_MONITOR_HOST=0.0.0.0
OPENASTRO_NINA_MONITOR_PORT=9091
OPENASTRO_NINA_MONITOR_PASSWORD_HASH=<PBKDF2 hash>
OPENASTRO_NINA_MONITOR_SECRET=<random session signing secret>
OPENASTRO_NINA_MONITOR_COOKIE_SECURE=1
OPENASTRO_NINA_QSM_URL=http://<NINA-PC-LAN-OR-TAILSCALE-IP>:18973
OPENASTRO_NINA_QSM_TOKEN=<same QSM token configured on Windows>
```

See [`../nina-monitor/COOLIFY.md`](../nina-monitor/COOLIFY.md) for the exact secret-generation procedure and deployment checks.

## Auto-update behavior

This repository is a monorepo. Coolify Auto Deploy must use module-specific Watch Paths:

```text
LiveVault       -> app/**, requirements.txt, Dockerfile, .dockerignore
NINA Monitor    -> nina-monitor/**
```

Therefore a commit that changes only `nina-monitor/**` updates only NINA Monitor. It must not restart LiveVault or any host service. Feature changes are validated by GitHub CI before merging to `main`; the merge then becomes the production deployment trigger.

## Current UI

The NINA Monitor currently shows:

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

The browser polls the monitor once per second while visible.

## Preview policy

The browser never receives a FITS/XISF path or QSM secret. QSM generates a display-only JPEG from N.I.N.A.'s `ImageSaved` bitmap:

- maximum width 1280 px;
- JPEG quality 82;
- encoded in RAM;
- FITS/XISF is not re-read;
- no preview file is persisted on ASIAIR/eMMC/NVMe;
- the browser requests the preview only when the QSM frame index advances.

This keeps remote traffic small and makes the feature independent from LiveVault storage.

## Isolation contract

Redeploying, restarting or crashing `openastro-nina-monitor` must not affect:

- LiveVault;
- OpenAstro Control Center;
- Media Center;
- NVMe/storage failover;
- Docker/Coolify itself;
- recording processes.

The container runs as an unprivileged user, with a read-only filesystem in the tested compose model, all Linux capabilities dropped, and no host mounts.

## Safety boundary

The first release remains read-only. Remote sequence control, threshold changes, file mutation and other write actions are intentionally outside this release until monitoring has been field-tested on real sessions.
