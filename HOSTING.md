# OpenAstro hosting

The active server is the OpenAstro home node (`astro@192.168.1.27`).

- LiveVault: https://openastro.tailf2871c.ts.net/
- OpenAstro Control: https://openastro.tailf2871c.ts.net:8443/
- Coolify, on the LAN: http://192.168.1.27:8000/
- Coolify, inside Tailscale: http://100.85.86.96:8000/

Application URLs use HTTPS through Tailscale Funnel and application authentication. Coolify itself remains private. The retired TierHive/CapRover instance is not a deployment target.

## Deployment model

OpenAstro uses **one Coolify Application per normal web/application module**. Each application has its own image, healthcheck, logs, restart/deployment history, resource limits and Watch Paths.

Current/canonical model:

| Component | Runtime | Auto update | Storage / privilege notes |
|---|---|---|---|
| LiveVault | Coolify application | `main` + LiveVault Watch Paths | heavy recordings may use the SERVER NVMe through the established storage layer |
| NINA Monitor | **separate Coolify application** | `main` + `nina-monitor/**` | no NVMe/USB mounts, no Docker socket, read-only app |
| Future normal web apps | separate Coolify applications | `main` + module-specific Watch Paths | no cross-module mounts unless explicitly required |
| OpenAstro host-control helpers | host systemd / narrow helpers | host deployment only | need host access for power, storage, firewall, sensors or systemd |
| Docker/Coolify itself | host service | platform-managed | never exposed inside application containers |

Do not force host-control code into Coolify merely for uniformity. Mounting `/var/run/docker.sock`, giving `privileged: true`, or exposing unrestricted host paths would make an application compromise equivalent to host compromise. If the Control Center is later moved behind Coolify, split it into an unprivileged web frontend plus a narrow host agent first.

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

## Backup and rollback

Run `sudo -n /usr/local/sbin/openastro-action backup_now` before changes that touch persistent host/application state. The existing backup job uses SQLite's backup API and writes consistent databases to `/share/livevault-backups`; recording media is not part of these database backups.

For Coolify-managed apps, roll back the affected application through its own deployment history. Never roll back/restart all applications for a one-module defect.

Keep a compressed copy of `/opt/openastro-control` before replacing host Control Center files. Host-control rollback remains separate from Coolify application rollback.

## Measurements

Panel telemetry is sampled every 10 seconds and retained for 24 hours. Expensive state probes are shared across clients for five seconds. Browsers stop polling when hidden. The ASIAIR Plus carrier-board ADS1015 measures input voltage/current; the panel derives DC watts and integrates Wh only over covered sample intervals. See `control-panel/README.md` for sensor setup, scope and calibration limits.
