#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "[LiveVault] Preparazione ambiente GitHub Codespaces..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends ffmpeg ca-certificates curl >/dev/null
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt >/dev/null
mkdir -p data/recordings
chmod 700 data
echo "[LiveVault] Ambiente pronto. Avvia con: ./scripts/codespaces-run.sh"
