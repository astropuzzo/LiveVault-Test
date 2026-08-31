#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
src="data/livevault.db"
ts="$(date +%Y%m%d_%H%M%S)"
dst="backups/livevault_${ts}.db"
if [ ! -f "$src" ]; then
  echo "Database non ancora presente."
  exit 0
fi
python3 - "$src" "$dst" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1:3]
source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
target = sqlite3.connect(dst)
try:
    source.backup(target)
finally:
    target.close()
    source.close()
print(f"Backup SQLite consistente: {dst}")
PY
