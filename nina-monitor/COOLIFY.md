# OpenAstro NINA Monitor — Coolify deployment

Coolify is the canonical production deployment path for this module.

The NINA monitor is a **separate Coolify Application**, not a child process of OpenAstro Control and not part of the LiveVault application.

## Resource boundary

Create a new Coolify Application with:

```text
Repository: astropuzzo/LiveVault-Test
Branch: main
Base directory: /nina-monitor
Build pack: Dockerfile
Dockerfile: /Dockerfile
Container port: 9091
Health check: GET /healthz
```

Recommended resource name:

```text
openastro-nina-monitor
```

The application must have its own deployment history, logs, restart policy and environment variables.

Do **not** add:

- `/data` mounts;
- SERVER NVMe mounts;
- Media USB mounts;
- Docker socket mounts;
- `network_mode: host`;
- dependencies on the LiveVault application;
- dependencies on `openastro-control.service`.

The image itself runs as an unprivileged user and needs only outbound network access to the trusted N.I.N.A./QSM endpoint.

## Environment variables

Configure these as Coolify application environment variables/secrets:

```text
OPENASTRO_NINA_MONITOR_HOST=0.0.0.0
OPENASTRO_NINA_MONITOR_PORT=9091
OPENASTRO_NINA_MONITOR_PASSWORD_HASH=<PBKDF2 hash>
OPENASTRO_NINA_MONITOR_SECRET=<random session HMAC secret, at least 32 chars>
OPENASTRO_NINA_MONITOR_COOKIE_SECURE=1
OPENASTRO_NINA_QSM_URL=http://<NINA-PC-LAN-OR-TAILSCALE-IP>:18973
OPENASTRO_NINA_QSM_TOKEN=<same QSM token configured on the Windows N.I.N.A. account>
```

`OPENASTRO_NINA_MONITOR_PASSWORD_HASH` format:

```text
pbkdf2_sha256:<iterations>:<base64url-salt>:<base64url-digest>
```

The production app should be exposed through HTTPS. With HTTPS enabled, keep `OPENASTRO_NINA_MONITOR_COOKIE_SECURE=1`.

## Generate secrets

Run locally on any machine with Python 3:

```bash
python3 - <<'PY'
import base64, hashlib, secrets, getpass
password=getpass.getpass('NINA Monitor password: ').encode()
salt=secrets.token_bytes(18)
iterations=310_000
digest=hashlib.pbkdf2_hmac('sha256', password, salt, iterations)
b64=lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip('=')
print('OPENASTRO_NINA_MONITOR_PASSWORD_HASH=' + f'pbkdf2_sha256:{iterations}:{b64(salt)}:{b64(digest)}')
print('OPENASTRO_NINA_MONITOR_SECRET=' + secrets.token_urlsafe(48))
print('OPENASTRO_NINA_QSM_TOKEN=' + secrets.token_urlsafe(48))
PY
```

Use the generated QSM token both in Coolify and on the Windows user account that launches N.I.N.A.:

```powershell
[Environment]::SetEnvironmentVariable("QSM_REMOTE_TOKEN","<same token>","User")
[Environment]::SetEnvironmentVariable("QSM_REMOTE_PORT","18973","User")
[Environment]::SetEnvironmentVariable("QSM_REMOTE_BIND","*","User")
```

Then fully restart N.I.N.A.

## Network topology

```text
Phone / tablet
     |
     | HTTPS
     v
Coolify / reverse proxy
     |
     v
openastro-nina-monitor:9091
     |
     | trusted LAN or Tailscale only
     v
N.I.N.A. + QualitySessionMeter:18973
```

The QSM port must not be forwarded from the home router. Only the ASIAIR/OpenAstro host needs to reach it.

## Failure isolation

Restarting, redeploying or crashing `openastro-nina-monitor` must not restart or alter:

- LiveVault;
- OpenAstro Control Center;
- Media Center;
- Docker/Coolify itself;
- storage/NVMe helpers;
- any recording process.

The NINA monitor keeps no persistent application state. Coolify environment configuration lives on internal storage with the rest of Coolify state; session data and previews remain in memory only.

## Deploy gate

Before production deployment:

1. GitHub CI must be green on the exact commit.
2. Build the `nina-monitor/Dockerfile` independently.
3. Verify `/healthz` reports `isolated: true`.
4. Verify no mounts are configured for the application.
5. Verify no Docker socket is exposed.
6. Verify the QSM token never appears in browser responses/devtools payloads.
7. Verify LiveVault container uptime does not change during NINA monitor redeploy.
