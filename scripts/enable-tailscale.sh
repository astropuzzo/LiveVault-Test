#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."; [ "$(id -u)" -eq 0 ] && SUDO="" || SUDO="sudo"
command -v tailscale >/dev/null 2>&1 || curl -fsSL https://tailscale.com/install.sh | sh
$SUDO tailscale up
python3 - <<'PY'
from pathlib import Path
p=Path('.env');u={'BIND_ADDR':'127.0.0.1','COOKIE_SECURE':'true'};out=[];seen=set()
for l in p.read_text().splitlines():
 k=l.split('=',1)[0] if '=' in l and not l.lstrip().startswith('#') else ''
 if k in u:out.append(f'{k}={u[k]}');seen.add(k)
 else:out.append(l)
for k,v in u.items():
 if k not in seen:out.append(f'{k}={v}')
p.write_text('\n'.join(out)+'\n')
PY
docker compose up -d; $SUDO tailscale serve --bg 8080; $SUDO tailscale serve status
