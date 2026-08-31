#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Nota: dalla v2 puoi configurare e testare i provider direttamente da Settings nella web app. Questo script resta per compatibilità/recupero."

if [[ ! -f .env ]]; then
  echo "Errore: .env non trovato. Esegui prima ./scripts/install.sh" >&2
  exit 1
fi

printf 'Token Gofile (INVIO per lasciare invariato): '
IFS= read -r -s GOFILE_INPUT
echo
printf 'API key Pixeldrain (INVIO per lasciare invariata): '
IFS= read -r -s PIXELDRAIN_INPUT
echo
printf 'Provider primario [gofile/pixeldrain/none] (INVIO = gofile): '
IFS= read -r PRIMARY_INPUT
PRIMARY_INPUT="${PRIMARY_INPUT:-gofile}"
case "$PRIMARY_INPUT" in
  gofile) FALLBACK_INPUT="pixeldrain" ;;
  pixeldrain) FALLBACK_INPUT="gofile" ;;
  none) FALLBACK_INPUT="none" ;;
  *) echo "Provider non valido." >&2; exit 1 ;;
esac

GOFILE_INPUT="$GOFILE_INPUT" PIXELDRAIN_INPUT="$PIXELDRAIN_INPUT" PRIMARY_INPUT="$PRIMARY_INPUT" FALLBACK_INPUT="$FALLBACK_INPUT" python3 - <<'PY'
from pathlib import Path
import os

path = Path('.env')
updates = {
    'PRIMARY_UPLOADER': os.environ['PRIMARY_INPUT'],
    'FALLBACK_UPLOADER': os.environ['FALLBACK_INPUT'],
}
if os.environ.get('GOFILE_INPUT'):
    updates['GOFILE_TOKEN'] = os.environ['GOFILE_INPUT']
if os.environ.get('PIXELDRAIN_INPUT'):
    updates['PIXELDRAIN_API_KEY'] = os.environ['PIXELDRAIN_INPUT']

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
  if sudo docker info >/dev/null 2>&1; then DOCKER=(sudo docker); else
    echo "Configurazione salvata. Docker non accessibile: riavvia LiveVault manualmente." >&2
    exit 0
  fi
fi

"${DOCKER[@]}" compose up -d

echo "Storage legacy configurato in .env. Per la gestione quotidiana usa Settings nella web app (segreti cifrati nel DB)."
