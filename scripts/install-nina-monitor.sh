#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/nina-monitor"
DEST="/srv/openastro-internal/nina-monitor"
ENV_FILE="$DEST/.env"
PROJECT="openastro-nina-monitor"

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

usage() {
  cat <<'EOF'
Usage:
  ./scripts/install-nina-monitor.sh <NINA_PC_HOST_OR_IP> [QSM_TOKEN] [MONITOR_PASSWORD]

Examples:
  ./scripts/install-nina-monitor.sh 192.168.1.50
  ./scripts/install-nina-monitor.sh 100.90.80.70 existing-qsm-token MyMonitorPassword

If QSM_TOKEN and/or MONITOR_PASSWORD are omitted, strong random values are generated.
The module is installed as its own Docker Compose project under internal eMMC:
  /srv/openastro-internal/nina-monitor

It does NOT modify/restart:
  - openastro-control.service
  - LiveVault containers
  - Media Center
  - storage/NVMe services
  - Docker socket permissions
and it mounts neither SERVER NVMe nor Media USB.
EOF
}

HOST="${1:-}"
QSM_TOKEN="${2:-}"
MONITOR_PASSWORD="${3:-}"
if [ -z "$HOST" ]; then usage; exit 2; fi

if [[ "$HOST" == http://* || "$HOST" == https://* ]]; then
  QSM_URL="${HOST%/}"
else
  QSM_URL="http://${HOST}:18973"
fi

command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "Docker is required" >&2; exit 1; }

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if $SUDO docker info >/dev/null 2>&1; then DOCKER="$SUDO docker"; else
    echo "Docker exists but is not accessible." >&2
    exit 1
  fi
fi
$DOCKER compose version >/dev/null 2>&1 || { echo "Docker Compose plugin is required" >&2; exit 1; }

if [ -z "$QSM_TOKEN" ]; then
  QSM_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
fi
if [ ${#QSM_TOKEN} -lt 32 ]; then
  echo "Refusing weak QSM token: use at least 32 characters." >&2
  exit 1
fi

if [ -z "$MONITOR_PASSWORD" ]; then
  MONITOR_PASSWORD="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(18))
PY
)"
fi
if [ ${#MONITOR_PASSWORD} -lt 12 ]; then
  echo "Monitor password must be at least 12 characters." >&2
  exit 1
fi

readarray -t GENERATED < <(python3 - "$MONITOR_PASSWORD" <<'PY'
import base64, hashlib, secrets, sys
password=sys.argv[1].encode()
salt=secrets.token_bytes(18)
iterations=310_000
digest=hashlib.pbkdf2_hmac('sha256', password, salt, iterations)
b64=lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip('=')
print(f"pbkdf2_sha256${iterations}${b64(salt)}${b64(digest)}")
print(secrets.token_urlsafe(48))
PY
)
PASSWORD_HASH="${GENERATED[0]}"
SESSION_SECRET="${GENERATED[1]}"

for required in Dockerfile docker-compose.yml qsm_client.py server.py static/index.html static/app.css static/app.js; do
  [ -f "$SRC/$required" ] || { echo "Missing $SRC/$required" >&2; exit 1; }
done

# Dedicated eMMC module tree. No files are copied into existing service directories.
$SUDO install -d -m 0750 -o astro -g astro "$DEST" "$DEST/static"
for file in Dockerfile docker-compose.yml qsm_client.py server.py; do
  $SUDO install -m 0644 -o astro -g astro "$SRC/$file" "$DEST/$file"
done
for file in index.html app.css app.js; do
  $SUDO install -m 0644 -o astro -g astro "$SRC/static/$file" "$DEST/static/$file"
done

TMP_ENV="$(mktemp)"
trap 'rm -f "$TMP_ENV"' EXIT
cat >"$TMP_ENV" <<EOF
NINA_MONITOR_BIND_ADDR=0.0.0.0
NINA_MONITOR_PORT=9091
OPENASTRO_NINA_MONITOR_HOST=0.0.0.0
OPENASTRO_NINA_MONITOR_PORT=9091
OPENASTRO_NINA_MONITOR_PASSWORD_HASH=${PASSWORD_HASH}
OPENASTRO_NINA_MONITOR_SECRET=${SESSION_SECRET}
OPENASTRO_NINA_MONITOR_COOKIE_SECURE=0
OPENASTRO_NINA_QSM_URL=${QSM_URL}
OPENASTRO_NINA_QSM_TOKEN=${QSM_TOKEN}
EOF
$SUDO install -m 0600 -o astro -g astro "$TMP_ENV" "$ENV_FILE"

python3 -m py_compile "$SRC/qsm_client.py" "$SRC/server.py"
if command -v node >/dev/null 2>&1; then
  node --check "$SRC/static/app.js"
fi

# This is an independent Compose project. No compose up/down is executed in the
# repository root and no existing OpenAstro/LiveVault container is addressed.
cd "$DEST"
$DOCKER compose -p "$PROJECT" config >/dev/null
$DOCKER compose -p "$PROJECT" up -d --build --remove-orphans

for _ in $(seq 1 30); do
  STATUS="$($DOCKER inspect -f '{{.State.Health.Status}}' openastro-nina-monitor 2>/dev/null || true)"
  if [ "$STATUS" = "healthy" ]; then break; fi
  if [ "$STATUS" = "unhealthy" ]; then
    $DOCKER logs --tail 80 openastro-nina-monitor >&2 || true
    exit 1
  fi
  sleep 1
done
STATUS="$($DOCKER inspect -f '{{.State.Health.Status}}' openastro-nina-monitor 2>/dev/null || true)"
if [ "$STATUS" != "healthy" ]; then
  echo "NINA monitor did not become healthy (status: ${STATUS:-unknown})." >&2
  $DOCKER logs --tail 80 openastro-nina-monitor >&2 || true
  exit 1
fi

echo
echo "OpenAstro NINA Monitor container is healthy."
echo "URL LAN: http://<ASIAIR-IP>:9091"
echo "QSM source: ${QSM_URL}"
echo "Docker project: ${PROJECT}"
echo "Persistent config: ${ENV_FILE} (internal eMMC)"
echo
echo "Monitor login password:"
echo "$MONITOR_PASSWORD"
echo
echo "Run these commands once in PowerShell under the Windows user that launches N.I.N.A.:"
printf '[Environment]::SetEnvironmentVariable("QSM_REMOTE_TOKEN","%s","User")\n' "$QSM_TOKEN"
printf '[Environment]::SetEnvironmentVariable("QSM_REMOTE_PORT","18973","User")\n'
printf '[Environment]::SetEnvironmentVariable("QSM_REMOTE_BIND","*","User")\n'
echo
echo "Then fully restart N.I.N.A. so QSM inherits the variables."
echo "Do NOT forward QSM port 18973 from your router."
