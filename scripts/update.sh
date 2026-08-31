#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v git >/dev/null 2>&1 && [ -d .git ]; then git pull --ff-only; fi
docker compose build --pull
docker compose up -d
