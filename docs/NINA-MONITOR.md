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

## Current UI (QSM 1.4, deployed 2026-09-13)

Source: `nina-monitor/static/{index.html,app.js,app.css}`. The dedicated monitor
application remains the deployment target; no other application requires a restart.

Image preview and stellar evidence now lead the dashboard. The inspector shows
the actual median profile and six stars measured by QSM, eccentricity, rescue margin,
tail, secondary peak, reliable sample size, median flux and the recorded decision.
Recovered frames remain inspectable alongside retained rejections. Borderline
shapes do not become confirmed damage: their insufficient rescue margin is explicit.
Independent signal/safety rejection rules always take precedence over a cleared guide flag.

The second pass runs in N.I.N.A. only for guiding-rejection candidates, not in this
container. Older QSM builds may supply measurements without typed limits: missing
limits are shown as unavailable; the monitor never invents defaults. Disablement,
missing checks and legacy-plugin states have distinct visible explanations.
Counts in this inspector concern only the recent history sent by QSM (up to 160
frames), not all checks from the whole night. Whole-session totals remain supplied
by the plugin. Quality belongs to an exposure; session quality averages usable frames;
evidence strength is a 0–100 diagnostic index, not a calibrated probability.
Learning/error quality and missing measurements are unavailable, not zero.

Polling keeps the selected check and expanded details stable. The latest JPEG is
requested only when frame identity changes; failed attempts are throttled to five
seconds. Diagnostics, history and expandable rankings follow the evidence view.
The live signed RA/DEC chart does not plot total-vector rejection limits on its axes.

Validation: local Chromium at 1440, 900 and 430 px, using the existing 13-image
private replay (0565 retained, 0663 recovered), independent-rule rejection, missing
values, inactive checks, disabled checks and polling persistence. Private FITS,
replay data and screenshots are not committed. Targeted proxy/auth/isolation tests
and the exact-commit Linux CI remain the deployment gate.

Production verification, 2026-09-13 19:19 UTC: application image
`ctrzdfqqsdljdcb2sbdrc7ug:96dd1c7a8e2cf9b48640ddef290d289556324220`, container
`ctrzdfqqsdljdcb2sbdrc7ug-191508215832`, healthy. PR #32 and merged commit CI passed.
The deployed HTML/JS/CSS match the repository (line endings normalized). Authenticated
Chromium loaded the real preview and QSM 1.4 session: 20 frames, no stellar checks
yet; the empty analysis state was displayed correctly. Desktop/mobile had no script
errors or page overflow. The prior 13-image replay validates the populated inspector;
it is distinct from this live empty-state check.

Runtime isolation: zero mounts, user `openastro`; `ReadonlyRootfs=false` in this
existing Coolify runtime (the read-only compose smoke test is a separate configuration).
LiveVault retained container `8142ca9f5818d31f4c0fc57bab4d02317ce991c472cf9f4281ef697a278b132b`
with start time `2026-09-13T07:48:23.640955705Z`. No application secrets changed.

Rollback: redeploy the previous NINA image `a74bd25c691045e2cb25cf433133c1e74077e7ed`
through this application's Coolify history. Preserve its environment and zero mounts;
verify LiveVault container identity/uptime remains unchanged.

## Preview policy

The browser never receives a FITS/XISF path or QSM secret. QSM generates a display-only JPEG from N.I.N.A.'s `ImageSaved` bitmap:

- maximum width 1280 px;
- JPEG quality 82;
- encoded in RAM;
- FITS/XISF is not re-read;
- no preview file is persisted on ASIAIR/eMMC/NVMe;
- the browser requests the preview only when the QSM frame timestamp/index identity changes.

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

## QSM 1.4.2 stellar inspector — deployed 2026-09-21

Source: `nina-monitor/static/app.js`, merged PR #34, commit `900ccd44f86e003877ab08b1893de565748fdfe5`. The inspector displays central/extended stellar profiles, verified regions, matched photometry even when shape classification is unavailable, oldest reference age, signal versus recent expected level, signal versus initial session reference, applied session floor and fitted trend rate. Sudden signal/count loss does not require a brighter sky. A cleared guide flag cannot override a final signal rejection. Missing fields from older QSM versions remain unavailable until new exposures are assessed by 1.4.2.0.

Validation: eight inspector JavaScript checks, nine standalone-monitor tests and replay browser renders at 1440/430 pixels. The exact feature commit passed both GitHub CI runs before merge. Images decode with no script errors or horizontal overflow.

Coolify deployment `vzsbvyorzsesj3wg7dm4r5zl` finished. Runtime image is `ctrzdfqqsdljdcb2sbdrc7ug:900ccd44f86e003877ab08b1893de565748fdfe5`, container `59e346505f92`, healthy since startup at `2026-09-21T13:07:42Z`. User remains `openastro`, mounts remain empty, and health reports read-only and isolated. Deployed `/app/static/app.js` SHA-256 is `06b0938abe936d09c6521f8232a408c87ec2382b3c0602a872d7d64741dd647f`, identical to normalized repository source. Public HTTPS reaches the login page; authentication is preserved. Authenticated live stellar data was not available in this verification, so visual evidence uses the replay payload.

LiveVault container `3810db46b484` and its startup `2026-09-20T20:09:59Z` are unchanged across deployment. Its image remains `ahul2vdjkyvjiwgzpcrmxzfe:6a41125474fb550c76ddd4b40d121ed387ca46a1`. Watch paths remain `nina-monitor/**` for this module and `app/**`, `requirements.txt`, `Dockerfile`, `.dockerignore` for LiveVault. No host checkout, recording data, tokens, endpoints or services were changed.

Rollback: redeploy the previous NINA image `ctrzdfqqsdljdcb2sbdrc7ug:96dd1c7a8e2cf9b48640ddef290d289556324220` through this application's Coolify history only. Keep existing environment and routing. Do not restart LiveVault or Control Center.
