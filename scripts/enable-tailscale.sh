#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "Errore: .env non trovato. Esegui prima ./scripts/install.sh" >&2
  exit 1
fi

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

if ! command -v curl >/dev/null 2>&1; then
  $SUDO apt-get update
  $SUDO apt-get install -y curl
fi

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Installazione Tailscale..."
  curl -fsSL https://tailscale.com/install.sh | sh
fi

echo
echo "Connessione della VPS al tuo account Tailscale."
echo "Se compare un URL, aprilo nel browser e autorizza il dispositivo."
$SUDO tailscale up

python3 - <<'PY'
from pathlib import Path
p = Path('.env')
updates = {'BIND_ADDR': '127.0.0.1', 'COOKIE_SECURE': 'true'}
lines = p.read_text().splitlines()
out=[]; seen=set()
for line in lines:
    if '=' in line and not line.lstrip().startswith('#'):
        k=line.split('=',1)[0]
        if k in updates:
            out.append(f'{k}={updates[k]}')
            seen.add(k)
            continue
    out.append(line)
for k,v in updates.items():
    if k not in seen:
        out.append(f'{k}={v}')
p.write_text('\n'.join(out)+'\n')
PY

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  if $SUDO docker info >/dev/null 2>&1; then DOCKER=($SUDO docker); else
    echo "Docker non accessibile. Esegui prima ./scripts/install.sh" >&2
    exit 1
  fi
fi

"${DOCKER[@]}" compose up -d

echo
echo "Attivo HTTPS privato Tailscale verso LiveVault..."
$SUDO tailscale serve --bg 8080

DNS="$($SUDO tailscale status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("Self") or {}).get("DNSName", "").rstrip("."))' 2>/dev/null || true)"
IP="$($SUDO tailscale ip -4 2>/dev/null | head -n1 || true)"

echo
if [[ -n "$DNS" ]]; then
  echo "LiveVault disponibile nel tuo tailnet: https://${DNS}/"
else
  echo "Tailscale attivo. IP privato: ${IP:-non rilevato}"
  echo "Esegui 'tailscale serve status' per vedere l'URL HTTPS."
fi
echo "Installa Tailscale anche sul telefono/PC e accedi con lo stesso account."
echo "La porta 8080 resta legata a 127.0.0.1 e non viene esposta pubblicamente."
