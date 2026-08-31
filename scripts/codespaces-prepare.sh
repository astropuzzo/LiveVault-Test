#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[LiveVault] Preparazione ambiente GitHub Codespaces..."

# Alcune immagini Codespaces possono includere una vecchia repository Yarn
# con chiave GPG scaduta/mancante. LiveVault non usa Yarn: se quella sorgente
# impedisce apt update, la disabilitiamo e riproviamo in modo mirato.
apt_update() {
  sudo apt-get update -qq
}

if ! apt_update; then
  echo "[LiveVault] apt update fallito: controllo repository Yarn non valida..."
  mapfile -t YARN_LISTS < <(grep -RIl "dl.yarnpkg.com" /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null || true)
  if [ ${#YARN_LISTS[@]} -gt 0 ]; then
    for f in "${YARN_LISTS[@]}"; do
      echo "[LiveVault] Disabilito sorgente Yarn non necessaria: $f"
      sudo mv "$f" "$f.livevault-disabled"
    done
    apt_update
  else
    echo "[LiveVault] Nessuna sorgente Yarn trovata; impossibile correggere automaticamente apt." >&2
    exit 1
  fi
fi

sudo apt-get install -y --no-install-recommends ffmpeg ca-certificates curl >/dev/null

if [ ! -d .venv ]; then
  python -m venv .venv
fi
. .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt >/dev/null
mkdir -p data/recordings
chmod 700 data

echo "[LiveVault] Ambiente pronto. Avvia con: ./scripts/codespaces-run.sh"
