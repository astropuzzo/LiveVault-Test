# OpenAstro — fonte di verità operativa
Verifica host: **2026-09-08**. Leggere [AGENTS.md](AGENTS.md) prima di intervenire.
Prova storage sulla release live ba26e23: eject 11,05 s, crescita capture su buffer,
attach 11,82 s, due file verificati SHA-256, buffer vuoto, container invariato.
Verbale privato: /var/backups/openastro/20260908-maintenance/live-cycle.json.
Le vecchie indagini restano nella cronologia Git: non prevalgono sullo stato verificato.

## Accesso e repository
- Nodo attivo: ASIAIR Plus / Raspberry Pi CM4, Debian, 4 core, 4 GiB RAM,
  eMMC interna da 32 GB. È il server domestico; il vecchio VPS è dismesso.
- SSH LAN: `ssh -o BatchMode=yes astro@192.168.1.27`. Tailscale: `100.85.86.96`.
  Chiave Windows: `%USERPROFILE%/.ssh/id_ed25519`. GitHub: login `gh` esistente.
- Il fuso orario del nodo è Europe/London: tenerne conto leggendo journal e timer;
  non confonderlo con Europe/Rome del client o con il giorno cloud applicativo.
- GitHub: https://github.com/astropuzzo/LiveVault-Test.git, produzione `main`.
  Prima di modificare: fetch, status, confronto HEAD/origin/main. Branch `codex/`.
- Checkout sul nodo: `/mnt/livevault-nvme/gpt-harness/work/LiveVault-Test`.
  **Contiene modifiche non committate**: non resettarlo, pulirlo o sovrascriverlo.
- Il connettore GPT Harness gira come `gpt-harness`, servizio `gpt-harness.service`,
  app `/opt/gpt-harness`, lavoro interno `/data/gpt-harness/work`.
- Root tramite il connettore: `gpt-root -- COMMAND ARGS`; verificare con
  `gpt-root -- id`. Socket locale `/run/gpt-harness-root.sock`,
  servizio `gpt-harness-root.service`. L'utente SSH astro non accede direttamente
  al socket; il suo sudo senza password copre wrapper storage e openastro-action.
- Il namespace Harness è privato: controlli host tramite gpt-root.
  Non esporre Docker socket, /share o storage indiscriminato al gateway.
- Espulsione/attach riavviano Harness. Eseguirli come job systemd host indipendenti,
  con cwd interno; seguirli via SSH o riconnettersi. Non usare cwd sul disco da espellere.

## Credenziali: solo percorsi, mai valori
Conservare proprietari, permessi e credenziali esistenti. Non stampare segreti,
inserirli in argomenti di comandi, screenshot, Git o documentazione.

| Uso | Posizione |
| --- | --- |
| Chiave SSH/Git sul nodo | /home/astro/.ssh/id_ed25519 e known_hosts |
| Segreti LiveVault | /data/livevault-secrets/app.env; environment Coolify |
| Credenziali provider cifrate | /data/livevault/livevault.db, dipendono da APP_SECRET |
| Login Control Center | /etc/openastro-control-auth.json |
| Credenziali media/SMB | /etc/openastro-media-credentials.json |
| Gateway Harness | /etc/gpt-harness/gateway.env |
| NINA e token QSM | environment applicazione Coolify separata |
| Dispositivi/token DNS | /var/lib/openastro-control; control-panel/remote_dns.py |

Non cambiare APP_SECRET: renderebbe illeggibili i segreti provider.
Vecchia nota login utente: outputs/OpenAstro-Control-access.txt nel workspace
d'installazione precedente; esistenza non verificata.

## Servizi attivi
| Componente | Runtime e dati | URL |
| --- | --- | --- |
| LiveVault | Coolify UUID ahul2vdjkyvjiwgzpcrmxzfe; /data/livevault | https://openastro.tailf2871c.ts.net/ |
| Control Center / Media Hub | openastro-control.service; /opt/openastro-control/upload_server.py; /var/lib/openastro-control | https://openastro.tailf2871c.ts.net:8443/ |
| NINA Monitor | Coolify separato UUID ctrzdfqqsdljdcb2sbdrc7ug, nessun mount host | remoto https://openastro.tailf2871c.ts.net:8443/nina/ · LAN http://192.168.1.27:9091/ |
| Coolify | Docker + Postgres/Redis/realtime/sentinel su eMMC | https://openastro.tailf2871c.ts.net:10000/ |
| Pi-hole | pihole-FTL.service; /etc/pihole | amministrazione LAN porta 80 |
| DNS cifrato | openastro-dns.service; /opt/openastro-dns/dns_gateway.py | DoH tramite /dns-query sul Funnel Control |
| Media LAN | smbd, nmbd, minidlna, wsdd2 | SMB OPENASTRO/Media; DLNA OpenAstro Media |

Gli ingressi HTTPS pubblici principali hanno Funnel attivo. NINA non usa più una
porta Funnel dedicata: `/nina/` sul Funnel `:8443` viene instradato direttamente
al container Coolify NINA, mentre `192.168.1.27:9091` resta l’accesso LAN.
Preservare autenticazione ed esposizione configurata. Control usa `control-panel/`
e helper host distribuiti separatamente. NINA resta isolato anche se la sua UI è
incorporata nel Control Center.
Deploy: [HOSTING.md](HOSTING.md), [nina-monitor/COOLIFY.md](nina-monitor/COOLIFY.md).

## Storage: contratto da mantenere
| Livello | Origine/mount | Contenuto |
| --- | --- | --- |
| eMMC | /srv/openastro-internal bind su /data | Docker /data/docker, DB, segreti, preview, servizi |
| SERVER NVMe ext4 | UUID 5fe2d0f6-b485-44e9-8e26-31fb0d217db2; /mnt/livevault-nvme | registrazioni pesanti; workspace IA esistente |
| SHARE exFAT | UUID 7EBD-F531; /share | backup DB e condivisione |
| Buffer emergenza | /var/lib/livevault-buffer.img su /var/lib/livevault-buffer | ext4 riservato 4 GiB; mai scratch |
| USB media | /srv/openastro-media/* | contenuti Media Hub, import autenticati |

Solo /data/livevault/recordings passa fra NVMe e buffer; nel container è
/data/recordings. Bind applicazione rslave, /data host condiviso.
SERVER/SHARE sono noauto in fstab. Boot su eMMC; attach tramite UUID ritardato
30 secondi per enumerazione USB. Mai identificare dischi tramite /dev/sdX.

Stato: /data/livevault/storage-state.json (nvme/buffer/quiesce).
Ack: storage-ready.json con token corrispondente.
Sorgente helper: scripts/nvme-handoff.py; installato /usr/local/libexec/nvme-handoff.py.
Wrapper: /usr/local/sbin/livevault-storage-eject e livevault-storage-attach.
Watchdog indipendente: openastro-storage-watchdog.service.

Eject chiude capture e rinvia lavoro archivio; verifica mount host/container,
smonta SERVER/SHARE e riprende su buffer. Docker resta online.
Buffer pieno: conservare file e fermare capture fino al rientro NVMe.
Attach verifica UUID, copia con SHA-256/fsync/rename atomico, poi cambia mount.
Collisioni diverse preservano entrambe le copie e bloccano il trasferimento.
Dettagli e recupero: [docs/NVME-HANDOFF.md](docs/NVME-HANDOFF.md).

DB, cronologia e cache Media Hub restano interni. Media USB scrivibili tramite
SMB autenticato o import con sessione/CSRF. SMB/DLNA restano LAN-only.
Non ripristinare le vecchie istruzioni read-only o guest.

## Controlli, backup e pulizia
Dal connettore, usare il namespace host:
```sh
gpt-root -- id
gpt-root -- cat /data/livevault/storage-state.json
gpt-root -- systemctl --failed --no-pager
gpt-root -- docker ps --format '{{.Names}} {{.Image}} {{.Status}}'
gpt-root -- findmnt -rn -o TARGET,SOURCE,FSTYPE
gpt-root -- vmstat 1 4
```
Verificare attività recorder, elaborazioni, errori kernel USB, alimentazione e
temperatura. Healthcheck e percentuali CPU storiche non provano attività reale.

Backup: `gpt-root -- /usr/local/sbin/livevault-backup`.
SQLite backup API, destinazione /share/livevault-backups, timer giornaliero;
i video non sono inclusi. Mantenere APP_SECRET nei backup sicuri esistenti.
Rollback manutenzione host: /var/backups/openastro/20260908-maintenance.
Rollback applicazioni: immagini/commit precedenti Coolify.
Pulizia verificata: 23 script monouso archiviati con checksum in
retired-one-shot-scripts.tar.gz e cleanup-manifest.json nella directory rollback;
due directory di debug vuote eliminate; cache apt da 133 MB a 20 KB.
Non cancellati checkout sporchi, media, database, segreti o immagini di rollback.

Pulire solo residui verificati. Archiviare privatamente gli script monouso prima
di rimuoverli. Conservare checkout sporchi, video originali, file ignoti e rollback.
Vietati come routine: docker system prune --volumes, git clean -fdx, chmod/chown
ricorsivi indiscriminati, fsck su mount attivi, lazy unmount nell'eject manuale.

## Prestazioni e limiti
Campione iniziale con recorder già bloccati: RAM disponibile 2,4 GiB, disco interno
51%, CPU inattiva 82–93%, niente swap/I/O wait corrente, temperatura 48,2 C.
Non è un benchmark a pieno carico. Verifiche video e recovery hanno trattenuto
l'handoff oltre 140 secondi; devono cooperare con il cambio storage e riprovare
dagli originali preservati. Storico: [PERFORMANCE.md](PERFORMANCE.md).

Kernel: undervoltage in questo avvio. vcgencmd: 0x50000, flag storici senza
throttling attuale. Alimentatore/cavo non verificabili tramite pulizia software.
Non aumentare frequenza/undervolt esistenti (profilo massimo 1500 MHz).

DNS cifrato attivo, contrariamente al vecchio handoff. Preservare nftables dedicato,
autenticazione/rate limit DoH e openastro-dot-network.timer per IPv6 DoT.
Non eseguire flush ruleset. Servizi locali attivi non provano accessibilità
telefonica/IPv6 esterna; tale verifica resta distinta.

## Obbligo di aggiornamento
Ogni modifica a runtime, servizi, storage, accessi, dipendenze o deploy deve
aggiornare nello stesso commit questa guida o il documento operativo collegato.
Correggere i fatti esistenti; non aggiungere repliche cronologiche contraddittorie.
CI richiede documentazione per modifiche operative, ma non può verificarne la verità.
Il controllo documentale viene dopo i test, così un'omissione non nasconde problemi
di codice. La revisione finale comprende anche i successivi aggiornamenti archivio,
timeline touch e monitor NINA/QSM (baseline GitHub ba26e23): conservarli.
Le verifiche packet scan scalano ora con la dimensione del file, fino a 1800 secondi;
restano interrompibili dal cambio storage. Non ripristinare il timeout fisso di 300 s.
Verificare su Linux/Python 3.13; Windows Python 3.14 non equivale alla produzione.
Separare test unitari, CI, deploy e prove fisiche. Non dichiarare assenza di perdita
dati dal solo healthcheck: controllare capture e trasferimento del buffer.
