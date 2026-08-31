#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

need_pkg() {
  command -v "$1" >/dev/null 2>&1 && return 0
  $SUDO apt-get update
  $SUDO apt-get install -y "$2"
}

need_pkg curl curl
need_pkg python3 python3

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker non trovato. Installazione Docker Engine..."
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  $SUDO sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh
fi

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if $SUDO docker info >/dev/null 2>&1; then DOCKER="$SUDO docker"; else
    echo "Docker installato ma non accessibile. Verifica il servizio Docker e rilancia."
    exit 1
  fi
fi

if ! $DOCKER compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin non disponibile. Installa docker-compose-plugin e rilancia."
  exit 1
fi

if [ ! -f .env ]; then cp .env.example .env; fi

read -r -s -p "Password pannello LiveVault (minimo 10 caratteri): " password
echo
if [ ${#password} -lt 10 ]; then
  echo "Password troppo corta."
  exit 1
fi

python3 - "$password" <<'PY'
from pathlib import Path
import base64, hashlib, secrets, sys
password=sys.argv[1].encode()
salt=secrets.token_bytes(18)
iterations=310_000
digest=hashlib.pbkdf2_hmac('sha256', password, salt, iterations)
b64=lambda b: base64.urlsafe_b64encode(b).decode().rstrip('=')
password_hash=f"pbkdf2_sha256${iterations}${b64(salt)}${b64(digest)}"
# Docker Compose expands dollar-prefixed text while loading .env. Escaping each
# dollar preserves the literal PBKDF2 separators inside the container.
password_hash_for_compose=password_hash.replace('$', '$$')
secret=secrets.token_urlsafe(48)
p=Path('.env')
lines=p.read_text().splitlines()
seen=set(); out=[]
for line in lines:
    if line.startswith('APP_PASSWORD='):
        out.append('APP_PASSWORD='); seen.add('APP_PASSWORD')
    elif line.startswith('APP_PASSWORD_HASH='):
        out.append('APP_PASSWORD_HASH='+password_hash_for_compose); seen.add('APP_PASSWORD_HASH')
    elif line.startswith('APP_SECRET='):
        out.append('APP_SECRET='+secret); seen.add('APP_SECRET')
    else:
        out.append(line)
if 'APP_PASSWORD_HASH' not in seen: out.append('APP_PASSWORD_HASH='+password_hash_for_compose)
if 'APP_SECRET' not in seen: out.append('APP_SECRET='+secret)
p.write_text('\n'.join(out)+'\n')
PY

mkdir -p data/recordings
chmod 700 data
chmod 600 .env

$DOCKER compose up -d --build

echo
echo "LiveVault avviato."
echo "Apri: http://IP_DEL_SERVER:8080"
echo "Accedi al pannello e apri Settings > Storage per inserire/testare Gofile e Pixeldrain senza modificare .env."
