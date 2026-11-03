#!/usr/bin/env bash
# Installs the Biglie slot hunt section of OpenAstro Control on the node: panel files, the openastro-action
# start action and the unit that lets the panel see /home/astro/biglie-oci. Restarts openastro-control only.
# Bundle from the repo on the PC (git archive keeps LF endings):
#   git archive --format=tar HEAD scripts/biglie-hunt-panel-install.sh control-panel AI-HANDOFF.md HOSTING.md \
#     | ssh astro@192.168.1.27 'rm -rf ~/biglie-panel && mkdir ~/biglie-panel && tar -x -C ~/biglie-panel'
#   git rev-parse HEAD | ssh astro@192.168.1.27 'cat > ~/biglie-panel/COMMIT'
# then on the node: sudo bash ~/biglie-panel/scripts/biglie-hunt-panel-install.sh
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Avvialo con: sudo bash $0" >&2; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP=/var/backups/openastro/20261103-biglie-hunt
APP=/opt/openastro-control
UNIT=/etc/systemd/system/openastro-control.service
ACTION=/usr/local/sbin/openastro-action
OPS=/opt/openastro-ops
PY_FILES=(server.py upload_server.py biglie_hunt.py web_push.py)
STATIC_FILES=(index.html app.js apps.css sw.js biglie-hunt.js)
step() { printf '\n== %s\n' "$*"; }

# Keeps owner and mode of a file that exists; a new file takes the given ones.
put() {
  local source=$1 target=$2
  if [[ -e $target ]]; then
    install -o "$(stat -c %U "$target")" -g "$(stat -c %G "$target")" -m "$(stat -c %a "$target")" "$source" "$target"
  else
    install -o "$3" -g "$4" -m "$5" "$source" "$target"
  fi
  cmp "$source" "$target"
}

step "1/6 Controlli sul pacchetto"
for name in "${PY_FILES[@]}"; do [[ -f $SRC/control-panel/$name ]] || { echo "Manca control-panel/$name" >&2; exit 1; }; done
for name in "${STATIC_FILES[@]}"; do [[ -f $SRC/control-panel/static/$name ]] || { echo "Manca control-panel/static/$name" >&2; exit 1; }; done
if grep -l $'\r' "$SRC"/control-panel/{openastro-action,openastro-control.service,*.py} >/dev/null; then
  echo "Il pacchetto ha fine riga Windows: rifallo con git archive." >&2; exit 1
fi
python3 - "${PY_FILES[@]/#/$SRC/control-panel/}" <<'EOF'
import ast, sys
for path in sys.argv[1:]:
    ast.parse(open(path, encoding='utf-8').read(), path)
EOF
bash -n "$SRC/control-panel/openastro-action"
systemd-analyze verify "$SRC/control-panel/openastro-control.service"
python3 -c 'import cryptography' 2>/dev/null || echo "Attenzione: manca python3-cryptography, il pannello funziona ma senza avvisi sul telefono."

step "2/6 Copia di sicurezza in $BACKUP"
install -d -m 700 "$BACKUP"
if [[ ! -e $BACKUP/control-before.tar.gz ]]; then
  tar -czf "$BACKUP/control-before.tar.gz" -C / "${APP#/}" "${UNIT#/}" "${ACTION#/}"
fi
cat "$SRC/COMMIT" > "$BACKUP/commit.txt" 2>/dev/null || true
ls -l "$BACKUP"

step "3/6 File del pannello in $APP"
for name in "${PY_FILES[@]}"; do put "$SRC/control-panel/$name" "$APP/$name" root root 644; done
for name in "${STATIC_FILES[@]}"; do put "$SRC/control-panel/static/$name" "$APP/static/$name" astro astro 644; done

step "4/6 Azione biglie_hunt_start e servizio del pannello"
put "$SRC/control-panel/openastro-action" "$ACTION" root root 755
put "$SRC/control-panel/openastro-control.service" "$UNIT" root root 644
systemctl daemon-reload

step "5/6 Riavvio del solo openastro-control (chi è collegato al pannello deve rifare l'accesso)"
systemctl restart openastro-control.service
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:9090/healthz >/dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:9090/healthz
echo
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9090/api/biglie/hunt)
[[ $code == 401 ]] || { echo "Atteso 401 senza accesso su /api/biglie/hunt, ricevuto $code" >&2; exit 1; }
pid=$(systemctl show -p MainPID --value openastro-control.service)
if [[ -r /proc/$pid/root/home/astro/biglie-oci/hunt.sh ]]; then echo "Il pannello vede la cartella della ricerca."; else echo "Il pannello NON vede /home/astro/biglie-oci." >&2; fi
sha256sum "${PY_FILES[@]/#/$APP/}" "${STATIC_FILES[@]/#/$APP/static/}" "$ACTION" "$UNIT"

step "6/6 Copia dei documenti operativi in $OPS"
if [[ -d $OPS ]]; then
  for doc in "$SRC"/AI-HANDOFF.md "$SRC"/HOSTING.md; do
    name="$(basename "$doc")"
    if [[ -f $doc && -e $OPS/$name ]]; then
      [[ -e $BACKUP/ops-$name.before ]] || cp -p "$OPS/$name" "$BACKUP/ops-$name.before"
      cat "$doc" > "$OPS/$name"
      echo "aggiornato $OPS/$name"
    fi
  done
  [[ -e $OPS/SOURCE.txt && -f $SRC/COMMIT ]] && echo "$(date -u +%F) ricerca server Biglie nel pannello: $(cat "$SRC/COMMIT")" >> "$OPS/SOURCE.txt"
fi

printf '\nFatto. Il pannello mostra la ricerca in App e ricavi.\n'
echo "Ripristino: tar -xzf $BACKUP/control-before.tar.gz -C / && rm -f $APP/biglie_hunt.py $APP/web_push.py $APP/static/biglie-hunt.js && systemctl daemon-reload && systemctl restart openastro-control.service"
