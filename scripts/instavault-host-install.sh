#!/usr/bin/env bash
# Installs the host pieces of InstaVault on the OpenAstro node:
# NVMe pause/resume helper, dedicated Tailscale node with Funnel, Control
# panel tile and the operations document copy. Safe to run again.
# Usage, from the bundle folder copied to the node: sudo bash instavault-host-install.sh
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Avvialo con: sudo bash $0" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
BACKUP=/var/backups/openastro/20261102-instavault
SOCK=/run/instavault-tailscale/tailscaled.sock
CONTROL=/opt/openastro-control/static
OPS=/opt/openastro-ops
ts() { tailscale --socket="$SOCK" "$@"; }
step() { printf '\n== %s\n' "$*"; }
ts_field() { ts status --json | python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

step "1/8 Copie di sicurezza in $BACKUP"
install -d -m 700 "$BACKUP"
for file in /usr/local/libexec/nvme-handoff.py "$CONTROL/index.html" "$CONTROL/apps.css" "$CONTROL/sw.js"; do
  [[ -e "$BACKUP/$(basename "$file").before" ]] || cp -p "$file" "$BACKUP/$(basename "$file").before"
done

step "2/8 Pausa e ripresa di InstaVault durante l'espulsione dell'NVMe"
python3 - "$HERE/instavault-storage.py" "$HERE/nvme-handoff.py" <<'EOF'
import ast, sys
for path in sys.argv[1:]:
    ast.parse(open(path, encoding='utf-8').read(), path)
EOF
install -o root -g root -m 755 "$HERE/instavault-storage.py" /usr/local/libexec/instavault-storage.py
install -o root -g root -m 755 "$HERE/nvme-handoff.py" /usr/local/libexec/nvme-handoff.py
cmp "$HERE/instavault-storage.py" /usr/local/libexec/instavault-storage.py
cmp "$HERE/nvme-handoff.py" /usr/local/libexec/nvme-handoff.py
sha256sum /usr/local/libexec/instavault-storage.py /usr/local/libexec/nvme-handoff.py

# Own InstaVault login, when the bundle carries one: username, salt and
# sha256(salt:password) only, never the password. Built outside Git.
if [[ -f "$HERE/instavault-auth.json" ]]; then
  auth=/data/instavault-secrets/auth.json
  python3 - "$HERE/instavault-auth.json" <<'EOF'
import json, re, sys
value = json.load(open(sys.argv[1], encoding='utf-8'))
assert set(value) == {'username', 'salt', 'hash'} and re.fullmatch('[0-9a-f]{64}', value['hash'])
EOF
  [[ -e "$BACKUP/instavault-auth.json.before" ]] || install -m 600 "$auth" "$BACKUP/instavault-auth.json.before"
  install -o "$(stat -c %u "$auth")" -g "$(stat -c %g "$auth")" -m "$(stat -c %a "$auth")" "$HERE/instavault-auth.json" "$auth"
  rm -f "$HERE/instavault-auth.json"
  echo "Accesso di InstaVault aggiornato: le sessioni aperte vanno rifatte con le nuove credenziali."
fi

step "3/8 Utente e cartelle del nodo Tailscale (eMMC)"
id -u instavault-ts >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /data/instavault-tailscale/home --shell /usr/sbin/nologin instavault-ts
install -d -o instavault-ts -g instavault-ts -m 700 /data/instavault-tailscale /data/instavault-tailscale/node /data/instavault-tailscale/home

step "4/8 Servizio instavault-tailscale"
install -o root -g root -m 644 "$HERE/instavault-tailscale.service" /etc/systemd/system/instavault-tailscale.service
systemctl daemon-reload
systemctl enable --now instavault-tailscale.service
for _ in $(seq 1 30); do [[ -S $SOCK ]] && break; sleep 1; done
[[ -S $SOCK ]] || { echo "Il servizio non risponde. Controlla: journalctl -u instavault-tailscale -n 50" >&2; exit 1; }

step "5/8 Accesso a Tailscale"
if [[ "$(ts_field 'd["BackendState"]')" != "Running" ]]; then
  echo "Apri il link che compare qui sotto e approva il dispositivo 'instavault' con il tuo account Tailscale."
  ts up --hostname=instavault --operator=astro --timeout=10m
fi
ts set --operator=astro
DOMAIN="$(ts_field 'd["Self"]["DNSName"].rstrip(".")')"
echo "Dominio del nodo: https://$DOMAIN/"

step "6/8 Funnel pubblico verso InstaVault (127.0.0.1:8096)"
if ! ts funnel --bg http://127.0.0.1:8096; then
  echo "Tailscale non ha attivato il Funnel per questo dispositivo." >&2
  echo "Nella console admin di Tailscale (Access controls) concedi l'attributo 'funnel' anche al dispositivo instavault, poi rilancia questo script." >&2
  exit 1
fi
ts funnel status

step "7/8 Scheda InstaVault nel pannello OpenAstro"
tile="$(mktemp)"
sed "s#https://instavault.tailf2871c.ts.net/#https://$DOMAIN/#" "$HERE/control/index.html" > "$tile"
for name in index.html apps.css sw.js; do
  target="$CONTROL/$name"; source="$HERE/control/$name"
  [[ $name == index.html ]] && source="$tile"
  install -o "$(stat -c %U "$target")" -g "$(stat -c %G "$target")" -m "$(stat -c %a "$target")" "$source" "$target"
done
rm -f "$tile"

step "8/8 Copia dei documenti operativi in $OPS"
if [[ -d $OPS ]]; then
  for doc in "$HERE"/docs/*.md; do
    name="$(basename "$doc")"
    if [[ -e "$OPS/$name" ]]; then
      [[ -e "$BACKUP/ops-$name.before" ]] || cp -p "$OPS/$name" "$BACKUP/ops-$name.before"
      cat "$doc" > "$OPS/$name"
      echo "aggiornato $OPS/$name"
    fi
  done
  [[ -e $OPS/SOURCE.txt ]] && echo "$(date -u +%F) aggiornamento mirato InstaVault: $(cat "$HERE/COMMIT")" >> "$OPS/SOURCE.txt"
fi

printf '\nFatto. InstaVault risponderà su https://%s/ appena Coolify avvia il container.\n' "$DOMAIN"
echo "Ricorda: nella console Tailscale disattiva la scadenza della chiave (Disable key expiry) per il dispositivo instavault."
