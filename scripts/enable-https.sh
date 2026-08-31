#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "Errore: .env non trovato. Esegui prima ./scripts/install.sh" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "Errore: curl non disponibile." >&2
  exit 1
fi

PUBLIC_IP="${1:-}"
if [[ -z "$PUBLIC_IP" ]]; then
  PUBLIC_IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org || true)"
fi

if [[ ! "$PUBLIC_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "Impossibile determinare un IPv4 pubblico valido." >&2
  echo "Puoi passarlo manualmente: ./scripts/enable-https.sh 203.0.113.10" >&2
  exit 1
fi

DOMAIN="${PUBLIC_IP//./-}.sslip.io"

PUBLIC_IP="$PUBLIC_IP" DOMAIN="$DOMAIN" python3 - <<'PY'
from pathlib import Path
import os

path = Path('.env')
updates = {
    'DOMAIN': os.environ['DOMAIN'],
    'BIND_ADDR': '127.0.0.1',
    'COOKIE_SECURE': 'true',
}
lines = path.read_text().splitlines()
seen = set()
out = []
for line in lines:
    if '=' in line and not line.lstrip().startswith('#'):
        key = line.split('=', 1)[0]
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
            continue
    out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text('\n'.join(out) + '\n')
PY

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  if sudo docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
  else
    echo "Docker non accessibile. Esegui prima ./scripts/install.sh" >&2
    exit 1
  fi
fi

"${DOCKER[@]}" compose -f docker-compose.yml -f docker-compose.https.yml up -d --build

echo
echo "HTTPS abilitato: https://${DOMAIN}"
echo "Assicurati che il firewall/VPS consenta TCP 80 e 443 (e opzionalmente UDP 443)."
echo "La porta applicativa 8080 ora è legata solo a 127.0.0.1."
