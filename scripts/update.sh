#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  if sudo docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
  else
    echo "Docker non accessibile. Esegui prima ./scripts/install.sh" >&2
    exit 1
  fi
fi

if command -v git >/dev/null 2>&1 && [ -d .git ]; then git pull --ff-only; fi
"${DOCKER[@]}" compose build --pull
"${DOCKER[@]}" compose up -d
