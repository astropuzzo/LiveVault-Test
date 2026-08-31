#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
ts="$(date +%Y%m%d_%H%M%S)"
if [ -f data/livevault.db ]; then cp data/livevault.db "backups/livevault_${ts}.db"; echo "Backup: backups/livevault_${ts}.db"; else echo "Database non ancora presente."; fi
