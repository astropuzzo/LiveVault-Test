#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/control-panel"
DEST="/opt/openastro-control"
SERVICE_SRC="$SRC/openastro-control.service"
SERVICE_DEST="/etc/systemd/system/openastro-control.service"
ENV_FILE="/etc/openastro-nina-monitor.env"

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

usage() {
  cat <<'EOF'
Usage:
  ./scripts/install-nina-monitor.sh <NINA_PC_HOST_OR_IP> [QSM_TOKEN]

Examples:
  ./scripts/install-nina-monitor.sh 192.168.1.50
  ./scripts/install-nina-monitor.sh 100.90.80.70 my-existing-long-token

If QSM_TOKEN is omitted, a strong random token is generated. The script installs
only OpenAstro Control/NINA-monitor files on internal storage; it does not touch
LiveVault recordings, SERVER NVMe, Media USB contents, or Docker workloads.
EOF
}

HOST="${1:-}"
TOKEN="${2:-}"
if [ -z "$HOST" ]; then usage; exit 2; fi
if [[ "$HOST" == http://* || "$HOST" == https://* ]]; then
  BASE_URL="${HOST%/}"
else
  BASE_URL="http://${HOST}:18973"
fi

if [ -z "$TOKEN" ]; then
  if command -v python3 >/dev/null 2>&1; then
    TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
  elif command -v openssl >/dev/null 2>&1; then
    TOKEN="$(openssl rand -base64 48 | tr -d '\n' | tr '/+' '_-')"
  else
    echo "python3 or openssl is required to generate QSM token" >&2
    exit 1
  fi
fi

if [ ${#TOKEN} -lt 32 ]; then
  echo "Refusing weak QSM token: use at least 32 characters." >&2
  exit 1
fi

for required in \
  "$SRC/upload_server.py" \
  "$SRC/nina_monitor.py" \
  "$SRC/static/media-upload.js" \
  "$SRC/static/nina-monitor.js" \
  "$SRC/static/nina-monitor.css" \
  "$SERVICE_SRC"; do
  [ -f "$required" ] || { echo "Missing $required" >&2; exit 1; }
done

# Keep runtime/config on internal eMMC with the existing Control Center.
$SUDO install -d -m 0755 -o astro -g astro "$DEST" "$DEST/static"
$SUDO install -m 0644 -o astro -g astro "$SRC/upload_server.py" "$DEST/upload_server.py"
$SUDO install -m 0644 -o astro -g astro "$SRC/nina_monitor.py" "$DEST/nina_monitor.py"
$SUDO install -m 0644 -o astro -g astro "$SRC/static/media-upload.js" "$DEST/static/media-upload.js"
$SUDO install -m 0644 -o astro -g astro "$SRC/static/nina-monitor.js" "$DEST/static/nina-monitor.js"
$SUDO install -m 0644 -o astro -g astro "$SRC/static/nina-monitor.css" "$DEST/static/nina-monitor.css"
$SUDO install -m 0644 "$SERVICE_SRC" "$SERVICE_DEST"

TMP_ENV="$(mktemp)"
trap 'rm -f "$TMP_ENV"' EXIT
cat >"$TMP_ENV" <<EOF
OPENASTRO_NINA_QSM_URL=${BASE_URL}
OPENASTRO_NINA_QSM_TOKEN=${TOKEN}
EOF
$SUDO install -m 0640 -o root -g astro "$TMP_ENV" "$ENV_FILE"

# Syntax check before replacing the running process.
python3 -m py_compile "$SRC/nina_monitor.py" "$SRC/upload_server.py"
if command -v node >/dev/null 2>&1; then
  node --check "$SRC/static/nina-monitor.js"
fi

$SUDO systemctl daemon-reload
$SUDO systemctl enable openastro-control.service >/dev/null
$SUDO systemctl restart openastro-control.service
sleep 1
$SUDO systemctl is-active --quiet openastro-control.service || {
  echo "openastro-control failed to start; recent logs:" >&2
  $SUDO journalctl -u openastro-control.service -n 60 --no-pager >&2 || true
  exit 1
}

echo
echo "OpenAstro NINA Monitor installed and active."
echo "ASIAIR -> QSM: ${BASE_URL}"
echo
echo "On the Windows account that launches N.I.N.A., run this PowerShell once:"
echo
printf '[Environment]::SetEnvironmentVariable("QSM_REMOTE_TOKEN","%s","User")\n' "$TOKEN"
printf '[Environment]::SetEnvironmentVariable("QSM_REMOTE_PORT","18973","User")\n'
printf '[Environment]::SetEnvironmentVariable("QSM_REMOTE_BIND","*","User")\n'
echo
echo "Then fully restart N.I.N.A. so QualitySessionMeter inherits the variables."
echo "Do not expose port 18973 to the public Internet; allow only your trusted LAN/Tailscale path."
echo
echo "QSM token (save it somewhere private):"
echo "$TOKEN"
