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

## Current UI (source verified 2026-09-29)

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

Polling keeps the selected check and expanded details stable when its data is unchanged.
If the selected frame's verdict or measurements change, its inspector refreshes; a new
session reusing a frame index clears the old selection because identity includes UTC
timestamp and index. The rejection/recovery table includes both guiding and stellar-flux
recoveries. The latest real JPEG has its own timestamp and age, independently of the
assessed or synthetic frame. It refreshes at most every 15 seconds when frame identity
is unchanged, with a five-second throttle for a new identity or failed attempt.
Diagnostics, history and expandable rankings follow the evidence view.
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
- the browser checks the latest real JPEG every 15 seconds while the dashboard is visible,
  including synthetic sessions and before the first assessed frame;
- successful and failed upstream JPEG requests share an in-memory five-second cache;
- an unchanged JPEG returns HTTP 304 through an ETag, avoiding another image transfer to
  the browser; changed image blobs are revoked when replaced or when the user logs out;
- the displayed timestamp comes from `X-QSM-Preview-Utc`, never from the assessed frame.

This keeps remote traffic small and makes the feature independent from LiveVault storage.

### Request and cache safety (source verified 2026-09-29)

Source: `nina-monitor/qsm_client.py`, `server.py` and `static/app.js`; runtime remains
`/app` in the separate Coolify application. Concurrent snapshot requests, including
forced refreshes already in flight, share one QSM fetch. Snapshot cache age begins
after network I/O completes; an offline or slow PC cannot make the cache expire before
the result is published. The browser aborts a request after eight seconds and discards
state/preview responses from a session that has already logged out.

QSM URLs must be HTTP(S) origins or paths without embedded credentials, query strings
or fragments. Both upstream routes reject HTTP redirects rather than forwarding
`X-QSM-Token` to another endpoint. Network error text is deliberately generic so an
invalid header or URL cannot echo a secret into an authenticated browser response.

Read-only host check on 2026-09-29: NINA image
`ctrzdfqqsdljdcb2sbdrc7ug:900ccd44f86e003877ab08b1893de565748fdfe5`, container
`ctrzdfqqsdljdcb2sbdrc7ug-130640406037`, healthy, zero mounts, user `openastro`;
LAN binding is `192.168.1.27:9091` (not host localhost). `/healthz` reports
`isolated: true`, unauthenticated `/api/state` returns 401, and QSM was reachable
in a single 161 ms sample. The sampled JPEG timestamp was ten minutes newer than
the last assessed frame, confirming why preview identity must be independent.
This read-only check predates deployment of these source fixes; the sample is not
a latency benchmark or a guarantee of continuous QSM availability.

Validation: focused Python regressions cover concurrent slow fetches, preview cache,
redirect rejection, error redaction and conditional JPEG responses; JavaScript
regressions cover selection reuse, independent preview identity/freshness, recovery
history and logout/timeout races. Exact-commit Linux CI and browser QA are deployment
gates. Commit `fc023fd8908d41fc897c78b73a65f97695ab26e0` passed Linux CI
(582 Python and 61 JavaScript tests across the repository) before promotion.
Browser replay QA covered desktop 1440×1000 and mobile 412×915 with sanitized
node data. No persistent schema or N.I.N.A. control/threshold behavior changes.

Production verification, 2026-09-30: image
`ctrzdfqqsdljdcb2sbdrc7ug:fc023fd8908d41fc897c78b73a65f97695ab26e0`, container
`ctrzdfqqsdljdcb2sbdrc7ug-085252364340`, started 08:53:57 UTC, healthy,
user `openastro`, zero mounts. `/healthz` is read-only/isolated; authenticated
`/api/state` returns 200 with `configured=true`, `reachable=false`. The PC/QSM
was unavailable: preview returned 502 with a generic error containing no endpoint.
A real JPEG and conditional ETag remain unverified on the node in this check;
CI covers preview caching/304. LiveVault was intentionally updated in the same
audit, with its own Watch Paths and final startup 08:54:43 UTC. See the
[audit report](PANELS-AUDIT-20260929.md) for precise evidence and limits.

Rollback: redeploy the prior NINA image
`ctrzdfqqsdljdcb2sbdrc7ug:900ccd44f86e003877ab08b1893de565748fdfe5` through this
application's Coolify history, or revert only these monitor changes. Keep the existing
environment, authentication, zero mounts and Funnel route; verify LiveVault uptime
is unchanged. Caches are memory-only and disappear on a NINA redeploy.

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
