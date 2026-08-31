#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[LiveVault] Preparing GitHub Codespaces environment..."
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

apt_update() {
  sudo -E apt-get update -qq
}

if ! apt_update; then
  echo "[LiveVault] apt update failed; checking stale Yarn source..."
  mapfile -t YARN_LISTS < <(grep -RIl "dl.yarnpkg.com" /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null || true)
  if [ ${#YARN_LISTS[@]} -gt 0 ]; then
    for f in "${YARN_LISTS[@]}"; do
      echo "[LiveVault] Disabling unused Yarn source: $f"
      sudo mkdir -p /etc/apt/sources.list.d/livevault-disabled
      sudo mv "$f" /etc/apt/sources.list.d/livevault-disabled/"$(basename "$f")"
    done
    apt_update
  else
    echo "[LiveVault] apt update failed and no stale Yarn source was found." >&2
    exit 1
  fi
fi

sudo -E dpkg --configure -a >/dev/null 2>&1 || true
sudo -E apt-get install -y -qq --no-install-recommends \
  -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-confold" \
  ffmpeg ca-certificates curl python3-venv >/dev/null

if [ ! -x .venv/bin/python ]; then
  rm -rf .venv
  python -m venv .venv
fi
. .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
pip install -r requirements-dev.txt >/dev/null
mkdir -p data/recordings data/thumbnails
chmod 700 data

echo "[LiveVault] Codespaces environment ready."
