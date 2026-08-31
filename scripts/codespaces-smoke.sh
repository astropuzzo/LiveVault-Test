#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  echo "Codespaces virtualenv missing; run codespaces-prepare.sh first." >&2
  exit 1
fi

TMP_DATA="$(mktemp -d)"
COOKIE_JAR="$(mktemp)"
LOG_FILE="$(mktemp)"
cleanup() {
  if [ -n "${SERVER_PID:-}" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP_DATA" "$COOKIE_JAR" "$LOG_FILE"
}
trap cleanup EXIT

export LIVEVAULT_TEST_PASSWORD="codespaces-test-12345"
export APP_SECRET="codespaces-smoke-secret-0123456789-abcdefghijklmnopqrstuvwxyz"
export DATA_DIR="$TMP_DATA"
export DB_PATH="$TMP_DATA/livevault.db"
export COOKIE_SECURE="false"
export BUFFER_MAX_GB="0"
export LIVEVAULT_PORT="${LIVEVAULT_PORT:-18080}"
BASE_URL="http://127.0.0.1:${LIVEVAULT_PORT}"
export MIN_FREE_GB="0.25"
export CRITICAL_FREE_GB="0.1"
export EMERGENCY_FREE_GB="0.05"

bash scripts/codespaces-run.sh >"$LOG_FILE" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS "$BASE_URL/healthz" >/tmp/livevault-health.json 2>/dev/null; then
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    cat "$LOG_FILE" >&2
    exit 1
  fi
  sleep 1
done

curl -fsS "$BASE_URL/healthz" | .venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); assert d["version"] == "2.0.0"; assert d["ok"] is True'

curl -fsS -c "$COOKIE_JAR" \
  -H 'Content-Type: application/json' \
  -d '{"password":"codespaces-test-12345"}' \
  "$BASE_URL/api/login" \
  | .venv/bin/python -c 'import json,sys; assert json.load(sys.stdin)["ok"] is True'

curl -fsS -b "$COOKIE_JAR" "$BASE_URL/api/me" \
  | .venv/bin/python -c 'import json,sys; assert json.load(sys.stdin)["authenticated"] is True'

curl -fsS -b "$COOKIE_JAR" "$BASE_URL/api/settings" \
  | .venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); assert d["version"] == "2.0.0"; assert d["settings"]["container_format"] == "mp4"'

curl -fsS -b "$COOKIE_JAR" \
  -X PATCH -H 'Content-Type: application/json' \
  -d '{"segment_minutes":7,"buffer_max_gb":3,"container_format":"mp4"}' \
  "$BASE_URL/api/settings" \
  | .venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); assert d["settings"]["segment_minutes"] == 7; assert d["settings"]["buffer_max_gb"] == 3.0'

echo "Codespaces smoke test passed."
