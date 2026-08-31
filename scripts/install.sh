#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
command -v curl >/dev/null 2>&1 || { $SUDO apt-get update; $SUDO apt-get install -y curl; }
command -v python3 >/dev/null 2>&1 || { $SUDO apt-get update; $SUDO apt-get install -y python3; }
if ! command -v docker >/dev/null 2>&1; then curl -fsSL https://get.docker.com -o /tmp/get-docker.sh; $SUDO sh /tmp/get-docker.sh; rm -f /tmp/get-docker.sh; fi
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="$SUDO docker"
[ -f .env ] || cp .env.example .env
read -r -s -p "Password pannello LiveVault (minimo 10 caratteri): " password; echo
[ ${#password} -ge 10 ] || { echo "Password troppo corta."; exit 1; }
python3 - "$password" <<'PY'
from pathlib import Path
import base64,hashlib,secrets,sys
pw=sys.argv[1].encode(); salt=secrets.token_bytes(18); it=310000; dig=hashlib.pbkdf2_hmac('sha256',pw,salt,it); b=lambda x:base64.urlsafe_b64encode(x).decode().rstrip('=')
updates={'APP_PASSWORD':'','APP_PASSWORD_HASH':f'pbkdf2_sha256${it}${b(salt)}${b(dig)}','APP_SECRET':secrets.token_urlsafe(48)}
p=Path('.env'); lines=p.read_text().splitlines(); out=[]; seen=set()
for line in lines:
 k=line.split('=',1)[0] if '=' in line and not line.lstrip().startswith('#') else ''
 if k in updates: out.append(f'{k}={updates[k]}'); seen.add(k)
 else: out.append(line)
for k,v in updates.items():
 if k not in seen: out.append(f'{k}={v}')
p.write_text('\n'.join(out)+'\n')
PY
mkdir -p data/recordings; chmod 700 data; chmod 600 .env
$DOCKER compose up -d --build
echo "LiveVault avviato su http://IP_DEL_SERVER:8080"
