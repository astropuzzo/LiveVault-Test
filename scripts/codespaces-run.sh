#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then bash scripts/codespaces-prepare.sh; fi
read -r -s -p "Password TEMPORANEA pannello LiveVault (minimo 10 caratteri): " LV_PASSWORD; echo
if [ ${#LV_PASSWORD} -lt 10 ]; then echo "Password troppo corta."; exit 1; fi
read -r -s -p "Token Gofile (INVIO per saltare): " LV_GOFILE; echo
read -r -s -p "API key Pixeldrain (INVIO per saltare): " LV_PIXELDRAIN; echo
export APP_PASSWORD="$LV_PASSWORD" APP_PASSWORD_HASH=""
export APP_SECRET="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
export DATA_DIR="$PWD/data" DB_PATH="$PWD/data/livevault.db" TZ="Europe/Rome"
export POLL_SECONDS="30" MAX_PROBE_CONCURRENCY="2" SEGMENT_MINUTES="10"
export MIN_FREE_GB="2" CRITICAL_FREE_GB="1" EMERGENCY_FREE_GB="0.5"
export DELETE_AFTER_UPLOAD="true" UPLOAD_RETRY_SECONDS="60" MAX_UPLOAD_ATTEMPTS="6"
export PRIMARY_UPLOADER="gofile" FALLBACK_UPLOADER="pixeldrain"
export GOFILE_TOKEN="$LV_GOFILE" ALLOW_GOFILE_GUEST="false" PIXELDRAIN_API_KEY="$LV_PIXELDRAIN" COOKIE_SECURE="true"
unset LV_PASSWORD LV_GOFILE LV_PIXELDRAIN
. .venv/bin/activate
echo; echo "LiveVault TEST in avvio sulla porta 8080 — apri la scheda PORTS. CTRL+C arresta."; echo
exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips='*'
