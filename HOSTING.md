# OpenAstro hosting

The active server is the OpenAstro home node (`astro@192.168.1.27`).

- LiveVault: https://openastro.tailf2871c.ts.net/
- OpenAstro Control: https://openastro.tailf2871c.ts.net:8443/
- Coolify, on the LAN: http://192.168.1.27:8000/
- Coolify, inside Tailscale: http://100.85.86.96:8000/
- Coolify HTTPS: https://openastro.tailf2871c.ts.net:10000/
- NINA Monitor: https://openastro.tailf2871c.ts.net:8443/nina/ (remote, via the Control Funnel; no dedicated Funnel port) · LAN http://192.168.1.27:9091/

Application URLs, including Coolify, use HTTPS through Tailscale Funnel and application authentication (public access reverified 2026-10-06). The retired TierHive/CapRover instance is not a deployment target. Read [AGENTS.md](AGENTS.md) and [AI-HANDOFF.md](AI-HANDOFF.md) before administration.

## Public ingress diagnosis and recovery

Verified **2026-10-06** on the home node, Tailscale 1.102.3. LiveVault and Control
were reachable locally (HTTP 200 on `127.0.0.1:8080` and `127.0.0.1:9090`),
and HTTPS to the private Tailscale address had a valid certificate. Public
connections to all three Funnel ports failed during TLS with an immediate EOF.
Forced requests to each public DNS A record reproduced the failure; the node's
`peerapi_ingress` counter did not increase during those attempts. Funnel status
still reported all ports enabled, with no health warning or expired node key.

The disruption was in the public Funnel path. Logs show control-session
reconnections on 2026-10-05 at 22:22 and 22:45 UTC. A stale ingress registration
after a reconnect is a plausible cause, also described by a Tailscale maintainer
in [issue 20739](https://github.com/tailscale/tailscale/issues/20739);
the exact initial trigger is not established by this audit. A requested netmap
refresh did not restore access. Restarting **only `tailscaled.service`** at
06:02 UTC (08:02 Europe/Rome), followed by relay propagation, restored it.
No Funnel reset, reauthentication, package update or application deploy was used.

Post-recovery checks through public DNS from Windows and forced public relay
addresses from the node: LiveVault 200, Control 200, NINA `/nina/` 200, Coolify
302 to login. Unauthenticated LiveVault `/api/settings` and Control `/api/state`
returned 401. The serialized Funnel configuration was identical before/after,
including `/nina` and `/dns-query`. LiveVault and NINA retained their 2026-09-30
container start times; Control retained PID 2334859, started 2026-10-02.
LiveVault 3.5.1 reported all workers active, one recorder and NVMe storage;
the same capture inode grew by 85,870,019 bytes between post-recovery samples.

Runtime: `/usr/sbin/tailscaled`, `tailscaled.service`, existing state under
`/var/lib/tailscale`; application source remains `bee494925048f52233951514b0ec206cc1042055`
for LiveVault/NINA. This intervention changed no application source or settings.
Private incident evidence and configuration snapshots:
`/var/backups/openastro/20261006-funnel` (root-only).

For a recurrence:

1. Check local backends, container health, disk space, `tailscale status`,
   `tailscale funnel status`, `tailscale netcheck` and the tailscaled journal.
2. Resolve the public DNS A records from outside Tailscale. Test with
   `curl --resolve openastro.tailf2871c.ts.net:443:<public-A-record> https://openastro.tailf2871c.ts.net/`
   and the corresponding `:8443` URL. MagicDNS on the node resolves to its private
   address, so a successful ordinary curl there does **not** verify Funnel.
3. Preserve the current Funnel JSON and logs in a private backup. If only public
   ingress is broken, reconnect the existing tunnel by restarting tailscaled
   through the established host root bridge over LAN SSH. This briefly interrupts
   Tailscale connections; leave Docker, recorders, Control and storage running.
4. Allow relay propagation, verify public HTTPS and authentication, compare the
   saved configuration, and check the active capture's growth. Persistent failure
   requires further ingress/control diagnosis, not repeated service restarts.

Rollback: this recovery changed no persistent configuration or installed version,
so no application/image/database rollback is required. Retain the original Funnel
snapshot; do not replace identities, keys or routes to undo a reconnect. Operational
document backups are kept in the incident directory. Limits: external HTTP checks
verify availability/authentication at that moment, not a guarantee against future
Funnel failures or a complete recording-integrity audit.

## Deployment model

OpenAstro uses **one Coolify Application per normal web/application module**. Each application has its own image, healthcheck, logs, restart/deployment history, resource limits and Watch Paths.

Current/canonical model:

| Component | Runtime | Auto update | Storage / privilege notes |
|---|---|---|---|
| LiveVault | Coolify application | `main` + LiveVault Watch Paths | heavy recordings may use the SERVER NVMe through the established storage layer |
| NINA Monitor | **separate Coolify application** | `main` + `nina-monitor/**` | no NVMe/USB mounts, no Docker socket, read-only app |
| Future normal web apps | separate Coolify applications | `main` + module-specific Watch Paths | no cross-module mounts unless explicitly required |
| Bonsai Sensei billing | separate Coolify application, staging | `main` + `bonsai-sensei-billing/**` | own database/config/secrets on internal storage; billing disabled pending Google setup |
| OpenAstro host-control helpers | host systemd / narrow helpers | host deployment only | need host access for power, storage, firewall, sensors or systemd |
| Docker/Coolify itself | host service | platform-managed | never exposed inside application containers |

Do not force host-control code into Coolify merely for uniformity. Mounting `/var/run/docker.sock`, giving `privileged: true`, or exposing unrestricted host paths would make an application compromise equivalent to host compromise. If the Control Center is later moved behind Coolify, split it into an unprivileged web frontend plus a narrow host agent first.

## Bonsai Sensei purchase service

Preparation started 2026-10-08 at the user's request. Source is
`bonsai-sensei-billing/`; no Bonsai game directory or game files are changed.
The Coolify application is named `Bonsai Sensei Billing` (UUID
`jxjbyszqndfoefprrxztv3fu`), Docker Compose build from `docker-compose.yml`, base
directory `/bonsai-sensei-billing`, container port 8095 and host mapping
`127.0.0.1:8095:8095`. Healthcheck is `/healthz`; `/readyz` is deliberately 503
until package ID, Google service account, player authentication and catalog
are configured. Leave `BILLING_ENABLED=false` during preparation.

Own persistent paths on internal storage:

- `/data/bonsai-sensei` mounted at `/data/bonsai-sensei`, writable only by uid 10001;
- `/data/bonsai-sensei-config` mounted read-only at `/config`, catalog initially empty;
- `/data/bonsai-sensei-secrets` mounted read-only at `/run/secrets`, including
  separate generated account-binding and receipt-encryption keys. Never publish
  or print these keys. Google credentials are not present during preparation.

Use a single process, 256 MiB memory limit, half a CPU and no elevated container
capabilities. Updates must deploy only this application. Do not share LiveVault
databases, environment secrets, recording mounts, Docker socket or host-control
helpers with the purchase service. Client requests authenticate players and
bind purchases to Google's obfuscated account ID; a shared APK secret is not
player authentication.

The Compose file explicitly applies read-only root filesystem, protected bind
mounts, dropped capabilities, no-new-privileges and resource limits. Coolify
4.4.2's Dockerfile custom-options converter does not support all those options;
do not switch back to a Dockerfile-only deployment assuming they remain active.

CI runs this module with its own pinned dependencies and vulnerability audit;
the core suite excludes its folder and continues to cover LiveVault, Control and
NINA. The host Control console will expose an authenticated `App e ricavi` view,
with a separate server-only read credential for aggregate ledger statistics.
Registered players, advertising and financial figures remain unavailable until
their authoritative sources are configured. No financial estimates are created.

Public routing and deployment verification are recorded in the module's
`README.md` when completed. Google configuration and actual Play test purchases
remain required before enabling billing. The service does not implement game
currency prices or gameplay effects. It records permanent entitlements and
consumable receipts for the later game integration.

Before installation, the established `openastro-action backup_now` was run on
2026-10-08. Rollback: stop only the Bonsai Sensei application in Coolify, remove
only its dedicated HTTPS route, and preserve its database and keys for recovery.
No existing application needs a restart or database rollback.

## Automatic deployment policy

For Coolify-managed applications:

1. production branch is `main`;
2. Auto Deploy is enabled;
3. every application has **module-specific Watch Paths**;
4. feature work is validated by GitHub CI before merge;
5. merging an already-green PR to `main` triggers only the Coolify application whose Watch Paths match the change;
6. an unrelated documentation/module commit must not recreate LiveVault or another application.

Coolify supports automatic Git deployments and monorepo Watch Paths; Watch Paths filter repository webhook deployments by changed file. Keep this filtering enabled for every app in this monorepo.

### LiveVault Watch Paths

The existing LiveVault application UUID is `ahul2vdjkyvjiwgzpcrmxzfe`.

Keep its production Watch Paths restricted to image/runtime inputs such as:

```text
app/**
requirements.txt
Dockerfile
.dockerignore
```

A NINA-monitor-only commit must therefore leave the LiveVault container uptime unchanged.

### NINA Monitor Watch Paths

Create `openastro-nina-monitor` as its own Coolify Application from this repository, base directory `/nina-monitor`, Dockerfile build, container port `9091`, healthcheck `/healthz`.

Enable Auto Deploy on `main` with:

```text
nina-monitor/**
```

Do not add root-level `Dockerfile`, `app/**`, `control-panel/**`, storage scripts or documentation-only paths to the NINA Monitor Watch Paths.

See [`nina-monitor/COOLIFY.md`](nina-monitor/COOLIFY.md) for environment variables and security boundaries.

## Storage and processes

LiveVault runs in Coolify with `/data/livevault` mounted at `/data`. Docker/Coolify state remains on internal eMMC. Heavy recording data belongs on SERVER NVMe when present through the existing storage/failover architecture.

The existing OpenAstro Control Center currently runs as `openastro-control.service` from `/opt/openastro-control` on internal storage because it performs host-level operations through narrow helpers. NINA Monitor does **not** run inside this service.

Keep existing credentials and system configuration. Never replace the LiveVault `APP_SECRET`, `/data/livevault-secrets/app.env`, `/etc/openastro-control-auth.json`, or Coolify application secrets during deployment.

## Deploy and verify

1. Run the GitHub Python/JavaScript/container CI on a feature branch.
2. Merge only a reviewed green commit to `main`.
3. Coolify Auto Deploy receives the `main` change and evaluates each application's Watch Paths.
4. Only matching applications deploy.
5. Verify the exact commit in Coolify Deployments and verify its healthcheck after replacement.
6. For NINA Monitor, additionally verify that the deployed container has no mounts and no Docker socket and that a redeploy does not alter LiveVault uptime.
7. For host-control changes, deploy only the affected host helper/service; do not bounce unrelated Coolify applications.

A green GitHub run proves source validation, not live deployment. A queued Coolify deployment is also not enough: verify that the replacement becomes healthy.

Coolify webhooks do not wait for CI: validate on a branch before promotion.
Verified 2026-09-30: both applications use GitHub App source id 1. A redundant
repository manual webhook (`674326069`, `/source/github/events/manual`) also
queued LiveVault, producing two rebuilds/replacements for `fc023fd`. Its delivery
response identifies deployment `vedgbii45mtryhy9lg8rkutl`; GitHub App queued
`yrvstczvejpvyzhyplr2swye` and the separate NINA deployment. The manual hook is now
inactive, with its configuration and secret retained. Keep the GitHub App route,
Auto Deploy, `main` and the module Watch Paths; do not enable both webhook routes.
The other legacy repository hook `673010860` was already inactive and stays so.
Rollback if the GitHub App integration stops delivering: re-enable only hook
`674326069` with `gh api --method PATCH repos/astropuzzo/LiveVault-Test/hooks/674326069 -F active=true` (one command), or its GitHub Settings toggle. Verify one intended
queue per matching app; enabling it while the App route works restores duplicates.
No Coolify secret, database setting or application environment was changed.
Check the queued commit before any manual deployment and retain the previous
image for rollback. Host panel updates remain separate and invalidate its
in-memory login sessions; sign in again with the existing credentials.

## Backup and rollback

Run `sudo -n /usr/local/sbin/openastro-action backup_now` before changes that touch persistent host/application state. The existing backup job uses SQLite's backup API and writes consistent databases to `/share/livevault-backups`; recording media is not part of these database backups.

For Coolify-managed apps, roll back the affected application through its own deployment history. Never roll back/restart all applications for a one-module defect.

Keep a compressed copy of `/opt/openastro-control` before replacing host Control Center files. Host-control rollback remains separate from Coolify application rollback.

## Measurements

Panel telemetry is sampled every 10 seconds and retained for 24 hours. Expensive state probes are shared across clients for five seconds. Browsers stop polling when hidden. The ASIAIR Plus carrier-board ADS1015 measures input voltage/current; the panel derives DC watts and integrates Wh only over covered sample intervals. See `control-panel/README.md` for sensor setup, scope and calibration limits.
