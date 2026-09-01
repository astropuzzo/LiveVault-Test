#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  bash scripts/codespaces-prepare.sh
fi

if [ -n "${LIVEVAULT_TEST_PASSWORD:-}" ]; then
  LV_PASSWORD="$LIVEVAULT_TEST_PASSWORD"
else
  read -r -s -p "Temporary LiveVault password (minimum 10 characters): " LV_PASSWORD
  echo
fi
if [ ${#LV_PASSWORD} -lt 10 ]; then
  echo "Password too short."
  exit 1
fi

export APP_PASSWORD="$LV_PASSWORD"
export APP_PASSWORD_HASH=""
export APP_SECRET="${APP_SECRET:-$(.venv/bin/python - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)}"
export DATA_DIR="${DATA_DIR:-$PWD/data}"
export DB_PATH="${DB_PATH:-$PWD/data/livevault.db}"
export TZ="${TZ:-Europe/Rome}"
export POLL_SECONDS="${POLL_SECONDS:-30}"
export MAX_PROBE_CONCURRENCY="${MAX_PROBE_CONCURRENCY:-2}"
export SEGMENT_MINUTES="${SEGMENT_MINUTES:-60}"
export SEGMENT_MAX_GB="${SEGMENT_MAX_GB:-2}"
export CONTAINER_FORMAT="${CONTAINER_FORMAT:-mp4}"
export INTEGRITY_MODE="${INTEGRITY_MODE:-packet}"
export GENERATE_THUMBNAILS="${GENERATE_THUMBNAILS:-true}"
export BUFFER_MAX_GB="${BUFFER_MAX_GB:-4}"
export BUFFER_HARD_STOP="${BUFFER_HARD_STOP:-true}"
export MIN_FREE_GB="${MIN_FREE_GB:-2}"
export CRITICAL_FREE_GB="${CRITICAL_FREE_GB:-1}"
export EMERGENCY_FREE_GB="${EMERGENCY_FREE_GB:-0.5}"
export DELETE_AFTER_UPLOAD="${DELETE_AFTER_UPLOAD:-true}"
export UPLOAD_RETRY_SECONDS="${UPLOAD_RETRY_SECONDS:-60}"
export MAX_UPLOAD_ATTEMPTS="${MAX_UPLOAD_ATTEMPTS:-6}"
export PRIMARY_UPLOADER="${PRIMARY_UPLOADER:-gofile}"
export FALLBACK_UPLOADER="${FALLBACK_UPLOADER:-pixeldrain}"
# Provider credentials are now entered/tested from Settings in the web UI.
export COOKIE_SECURE="${COOKIE_SECURE:-true}"
unset LV_PASSWORD

. .venv/bin/activate
echo
echo "LiveVault v2 test is starting on port 8080."
echo "Open the PORTS tab and use the private HTTPS URL for port 8080."
echo "Provider API keys can be configured after login in Settings."
echo "CTRL+C stops the test instance."
echo
exec uvicorn app.main:app --host 0.0.0.0 --port "${LIVEVAULT_PORT:-8080}" --proxy-headers --forwarded-allow-ips='*'
