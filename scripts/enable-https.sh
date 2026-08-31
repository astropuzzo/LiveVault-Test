#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."; IP=${1:-$(curl -4 -fsS --max-time 10 https://api.ipify.org)}; DOMAIN=${IP//./-}.sslip.io
DOMAIN="$DOMAIN" python3 - <<'PY'
from pathlib import Path
import os
p=Path('.env');u={'DOMAIN':os.environ['DOMAIN'],'BIND_ADDR':'127.0.0.1','COOKIE_SECURE':'true'};out=[];seen=set()
for l in p.read_text().splitlines():
 k=l.split('=',1)[0] if '=' in l and not l.lstrip().startswith('#') else ''
 if k in u:out.append(f'{k}={u[k]}');seen.add(k)
 else:out.append(l)
for k,v in u.items():
 if k not in seen:out.append(f'{k}={v}')
p.write_text('\n'.join(out)+'\n')
PY
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build
echo "HTTPS: https://${DOMAIN}"
