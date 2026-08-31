#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."; [ -f .env ] || { echo "Esegui prima install.sh"; exit 1; }
read -r -s -p "Token Gofile (INVIO lascia invariato): " G; echo
read -r -s -p "API key Pixeldrain (INVIO lascia invariata): " P; echo
read -r -p "Provider primario [gofile/pixeldrain/none] (default gofile): " R; R=${R:-gofile}; case "$R" in gofile) F=pixeldrain;; pixeldrain) F=gofile;; none) F=none;; *) exit 1;; esac
G="$G" P="$P" R="$R" F="$F" python3 - <<'PY'
from pathlib import Path
import os
u={'PRIMARY_UPLOADER':os.environ['R'],'FALLBACK_UPLOADER':os.environ['F']}
if os.environ['G']:u['GOFILE_TOKEN']=os.environ['G']
if os.environ['P']:u['PIXELDRAIN_API_KEY']=os.environ['P']
p=Path('.env'); out=[]; seen=set()
for l in p.read_text().splitlines():
 k=l.split('=',1)[0] if '=' in l and not l.lstrip().startswith('#') else ''
 if k in u:out.append(f'{k}={u[k]}');seen.add(k)
 else:out.append(l)
for k,v in u.items():
 if k not in seen:out.append(f'{k}={v}')
p.write_text('\n'.join(out)+'\n')
PY
docker compose up -d
echo "Storage configurato."
