# OpenAstro hosting

The active server is the OpenAstro home node (`astro@192.168.1.27`).

- LiveVault: https://openastro.tailf2871c.ts.net/
- OpenAstro Control: https://openastro.tailf2871c.ts.net:8443/
- Coolify, on the LAN: http://192.168.1.27:8000/
- Coolify, inside Tailscale: http://100.85.86.96:8000/

The two application URLs use HTTPS through Tailscale Funnel and application
authentication. Coolify itself remains private. The retired TierHive/CapRover
instance is not a deployment target; CI no longer calls its old webhook.

## Storage and processes

LiveVault runs in Coolify as application UUID `ahul2vdjkyvjiwgzpcrmxzfe`, with
`/data/livevault` mounted at `/data`. OpenAstro Control runs as the existing
`openastro-control.service`, from `/opt/openastro-control` on internal storage.
Its source is versioned in `control-panel/` in this repository.

Keep existing credentials and system configuration. Never replace the LiveVault
`APP_SECRET`, `/data/livevault-secrets/app.env`, or
`/etc/openastro-control-auth.json` during deployment.

## Deploy and verify

1. Run the Python suite and JavaScript checks in `.github/workflows/ci.yml`.
2. Push the reviewed commit and wait for **Core tests** to pass on GitHub.
3. Use Coolify to deploy that exact commit. Retain the previous image for rollback.
4. Copy the versioned panel assets and `server.py` to `/opt/openastro-control`,
   keeping ownership and existing service configuration. Restart only the panel
   process; its systemd unit restarts it automatically. A panel restart invalidates
   its in-memory login sessions, so sign in again with the same credentials.
5. Check both `/healthz` endpoints, sign in, verify live recorder counts agree,
   check media growth and uploads, and inspect both desktop and phone layouts.

GitHub CI validates both apps. Deployment to this private node is currently an
explicit Coolify operation; a green GitHub run alone is not a deployment.

## Backup and rollback

Run `sudo -n /usr/local/sbin/openastro-action backup_now` before deployment.
The existing backup job uses SQLite's backup API and writes consistent databases
to `/share/livevault-backups`. The daily timer retains 14 days. Recording media
is not part of these database backups.

Keep a compressed copy of `/opt/openastro-control` before replacing it.
Rollback LiveVault through Coolify's previous image/commit and restore the panel
files from that copy. Version 3.0.0 does not change the database schema.

## Measurements

Panel telemetry is sampled every 10 seconds and retained for 24 hours. Expensive
state probes are shared across clients for five seconds. Browsers stop polling
when hidden. Energy readings are software estimates, not wattmeter measurements;
the daily figure is a projection at the current load.
