# OpenAstro — fonte di verità operativa
Verifica accesso pubblico: **2026-10-06**; audit host completo: **2026-09-30**.
Leggere [AGENTS.md](AGENTS.md) prima di intervenire.
Priorità corrente: [CPU 1,8 GHz, USB e buffer](docs/STABILITY-20260909.md).
Recupero MP4 e resolver HLS: [diagnosi e rollback](docs/MP4-HLS-RECOVERY.md), verifica 2026-09-20.
Verifica pannello e frammenti brevi: [LIVE-PANEL-20260909.md](docs/LIVE-PANEL-20260909.md).
Prova storage sulla release live ba26e23: eject 11,05 s, crescita capture su buffer,
attach 11,82 s, due file verificati SHA-256, buffer vuoto, container invariato.
Verbale privato: /var/backups/openastro/20260908-maintenance/live-cycle.json.
Le vecchie indagini restano nella cronologia Git: non prevalgono sullo stato verificato.
Intervento prestazioni in corso: leggere [stato e ripresa](docs/OTTIMIZZAZIONE-STATO.md)
prima di continuare il [piano](docs/PIANO-OTTIMIZZAZIONE.md).
Una copia distribuita su eMMC è in /opt/openastro-ops; gli ingressi AGENTS.md dei
workspace del nodo puntano lì. Aggiornare quella copia insieme ai documenti Git;
SOURCE.txt identifica la revisione (ultimo allineamento completo: 2026-09-30,
incluse le guide collegate da questo file; aggiornamento mirato di AI-HANDOFF.md
e HOSTING.md per il Funnel: 2026-10-06). Il vecchio handoff duplicato è stato sostituito
da un rinvio, con originale conservato nella directory rollback.

## Accesso pubblico — ripristinato 2026-10-06

LiveVault e Control erano sani in locale ma il Funnel chiudeva le connessioni
pubbliche durante TLS. Ripristinato con restart del solo `tailscaled.service`
alle 06:02 UTC (08:02 Europe/Rome) e successiva propagazione ai relay. Verifica pubblica: LiveVault,
Control e NINA HTTP 200, Coolify 302 al login; API protette ancora 401.
Configurazione Funnel identica prima/dopo, container e PID Control invariati,
una capture attiva su NVMe. Nessun deploy applicativo o cambio di storage/segreti.
Il blocco è nel percorso Funnel; causa iniziale esatta non dimostrata.
Evidenze private e rollback: `/var/backups/openastro/20261006-funnel`.
Diagnosi, limiti e procedura: [HOSTING.md](HOSTING.md#public-ingress-diagnosis-and-recovery).

## UI — runtime 3.5.1 verificato
Verifica **2026-09-30**: LiveVault e NINA
`:bee494925048f52233951514b0ec206cc1042055` healthy; Control attivo, sette asset
SHA-256 identici allo stesso sorgente in `/opt/openastro-control/static`,
PID invariato 1589194. CI feature
[36733526667](https://github.com/astropuzzo/LiveVault-Test/actions/runs/36733526667)
e main [36734074221](https://github.com/astropuzzo/LiveVault-Test/actions/runs/36734074221)
verdi: 583 Python e 81 JavaScript, build/prova NINA isolato.
Vetro, blu cosmico e motion esteso nei tre pannelli; AWAY distinto da TIP-JAR,
NINA con badge offline/standby corretti. Tutti i worker leader attivi, recorder 0,
1358 registrazioni, 181,33 GB liberi alla verifica; nessuna migrazione DB.
Backup Control privato originale 3.4.31 `control-before.tar.gz` e intermedio
3.5.0 `control-350-assets.tar.gz` in `/var/backups/openastro/20260930-ui-future`;
immagini precedenti conservate. `/opt/openastro-ops` distribuito contestualmente,
SOURCE.txt identifica documento e runtime; OAuth privato preservato.
Sorgenti, QA, rollout e rollback in [UI-REDESIGN-20260930](docs/UI-REDESIGN-20260930.md).
QSM configurato ma PC non raggiungibile, preview reale HTTP 502: prova JPEG aperta.
La policy del browser ha bloccato l’ultima cattura post-deploy; QA desktop/mobile
della nuova UI completata prima della pubblicazione, hash HTTPS verificati dopo.

## Audit precedente — 2026-09-30 (release 3.4.31)
Audit SSH di LiveVault, Control/Media e NINA concluso sul commit sorgente
`fc023fd8908d41fc897c78b73a65f97695ab26e0`: [evidenze e rollback](docs/PANELS-AUDIT-20260929.md).
CI feature e main verdi: 582 test Python e 61 JavaScript, build/prova NINA isolato.
Durante quell’audit LiveVault e NINA avevano immagini `:fc023fd...` healthy; Control fu aggiornato in
`/opt/openastro-control`, nove hash identici al sorgente testato. Le immagini
`0242aa3`/`900ccd44` sono rollback, non il runtime corrente. Backup Control privato:
`/var/backups/openastro/20260930-panels-audit/control-before.tar.gz`.
AWAY è distinto da TIP-JAR; claim dei file, diagnostica Control e preview NINA
con timestamp indipendente sono attivi. Nessuna migrazione DB. QSM non era
raggiungibile nel controllo finale; prova di una preview reale ancora aperta.
Il webhook manuale GitHub `674326069` accodava un secondo deploy LiveVault:
disattivato, configurazione conservata. Resta il collegamento GitHub App con
Watch Paths invariati. Procedura e ripristino in [HOSTING.md](HOSTING.md).
Sessione locale con SSH al nodo. Misure, cause, procedure e rollback in
[LIVE-PANEL-20260909.md](docs/LIVE-PANEL-20260909.md) (sezioni "Short captures
in Cronologia" e NSFW 3.3/3.4) e [MP4-HLS-RECOVERY.md](docs/MP4-HLS-RECOVERY.md)
(3.4.4); cronologia in CHANGELOG.
- 3.4.24 (2026-09-29, audit prestazioni con SSH): misure e modifiche in CHANGELOG. In sintesi:
  `nsfw_hits` differita, elenco registrazioni e Cronologia più leggeri, uploader a coda vuota
  senza scansione dei frammenti, controllo miniature con confronto di prefisso su stringa.
  Prima → dopo il primo deploy (misura dal container, 4 richieste, minimo): `/api/recordings?limit=1000`
  996 → 776 ms, pulse 168 h 1858 → 926 ms, pulse 12 h invariato (~80 ms). Nessuna modifica di schema né di
  impostazioni sul nodo. Rollback: revert del commit 3.4.24 e redeploy Coolify (immagine
  precedente `8b2921198021` = 3.4.23). Lavoro fatto da un clone pulito: il checkout locale
  Windows dell'utente era fermo in un `git revert` interrotto con conflitti (282 commit
  indietro) e non è stato toccato. Test locali su Windows: servono `PYTHONUTF8=1`,
  `PYTHONPATH` con la radice del repo e un `fcntl.py` finto fuori dal repo; 22 test host/storage
  (`pwd`, mount) falliscono solo su Windows, la CI Linux li esegue.
  Non fatto (deciso): fusione delle facciate `app/main/`, `app/workers/`, `app/source_providers/`,
  `app/stripchat_capture/` sui file omonimi: non hanno costo a runtime, ma decine di test leggono
  `app/main.py`/`app/workers.py` come testo e monkeypatchano tramite la facciata.
- 3.4.25: file indicizzati due volte (grezzo `.capture.mp4` + remux) corretti alla radice
  e scartati per SHA-256 identico; verificato il 2026-09-29 sul DB del nodo: doppioni esistenti
  id 1037/1038, 678/699, 675/677 (2,5 GB caricati due volte su Gofile, non cancellati: decide
  l'utente). Rollback: revert del commit e redeploy dell'immagine precedente (3.4.24 `a759b97`).
- 3.4.26 (CPU a riposo): `OPENSSL_CONF=/app/app/openssl-chacha.cnf` nel Dockerfile (ChaCha20
  per primo, il CM4 non ha AES hardware) e cache dell'elenco estrattori yt-dlp. Serve il
  rebuild dell'immagine: dopo il deploy verificare nel container
  `python3 -c "import ssl,socket;c=ssl.create_default_context();s=c.wrap_socket(socket.create_connection(('api.gofile.io',443)),server_hostname='api.gofile.io');print(s.cipher())"`
  (atteso `TLS_CHACHA20_POLY1305_SHA256`). Rollback: togliere la riga `ENV OPENSSL_CONF`
  o redeploy dell'immagine 3.4.25 (`38779e4`). Profilare il processo vivo, senza toccare
  l'immagine: `pip install --target /tmp/pyspy py-spy` nel container, poi
  `/tmp/pyspy/bin/py-spy record --nonblocking -d 25 -r 50 -f raw -o /tmp/spy.raw -p <pid uvicorn>`
  (funziona senza SYS_PTRACE); rimuovere `/tmp/pyspy` e `/tmp/spy.raw` a fine misura.
- 3.4.27: `/api/recordings` è in cache per (limit, offset) con ETag; si invalida da solo (commit su
  `recordings`, modalità storage, 15 s). Se un domani qualcosa scrive su `recordings` fuori da
  SQLAlchemy (SQL grezzo, script), la lista può restare vecchia fino a 15 s: usare `db_session()`.
  Rollback: revert del commit (torna la lista ricostruita a ogni richiesta).
- 3.4.28: `segment_minutes` è **5** sul nodo dal 2026-09-29 (era 15 dal 25/09; PATCH `/api/settings`,
  vale per le capture avviate dopo). Motivo: i file erano 30/45/75 min al 71-91% del tetto di 2 GB
  perché l'unione impacchetta parti intere. Rollback: Impostazioni → Registrazione → Segmento = 15.
  Effetto collaterale: in Cronologia una live in corso mostra più «parti locali» (una ogni 5 min) finché
  non si uniscono. Timeline di `/capture`: pagina player per le navigazioni del browser, playlist
  di sessione con parti chiuse + parte in scrittura (una entry per frammento); il remux Stripchat
  produce `.mp4` con `moov`, quindi per Stripchat la timeline copre solo la parte in scrittura.
  QA: ffmpeg `-re` verso un MP4 frammentato e l'app locale con l'autenticazione disattivata
  (solo test): durata 28 → 34 → 42 s, seek indietro ok, 0 stalli/salti in 40 s.
- 3.4.29 (2026-09-29): primo fix della playlist live Stripchat. I frammenti da 0,5 s
  espongono un keyframe circa ogni quattro frammenti; 3.4.28 li serviva separatamente e il seek
  poteva partire a metà GOP. `app/mp4_index.py` raggruppa solo su keyframe e mantiene un indice
  incrementale per la parte in scrittura; pubblica solo segmenti già chiusi. Test e limiti in
  [LIVE-PANEL-20260909.md](docs/LIVE-PANEL-20260909.md), sezione 3.4.29. Deploy `a6fbbbc`
  healthy, ma la verifica reale ha trovato il difetto `sidx` corretto nel candidato 3.4.30.
  Rollback: revert e redeploy 3.4.28; nessuno schema modificato.
- 3.4.30 (2026-09-29, deploy `0242aa3`): verifica reale dopo il deploy 3.4.29 sulla capture
  AliciaBrooks: due box `sidx` (104 byte) tra ogni frammento lasciavano ancora
  191 segmenti live da 0,5 s su 192 frammenti, molti senza keyframe iniziale.
  `app/mp4_index.py` include i box intermedi nel byte range e taglia soltanto sui
  keyframe. Test audio/video con layout `sidx` in `tests/test_v3424_performance.py`.
  Il recupero isola in quarantena la sola copia AngelLeeen priva di audio (4,032 s,
  507573 byte verificati sul nodo), senza caricarla muta né ripetere l'avviso.
  Stripchat prova prima i domini HLS correnti pubblicati dal provider, poi gli
  edge statici; Flashphoner copre anche il percorso `master_<id>.m3u8`.
  Test locali mirati con FFmpeg: 83 passati. La disponibilità HLS di Stripchat
  richiede una nuova live pubblica per la verifica. CI `main` run `36597522780`
  verde; container `ahul2vdjkyvjiwgzpcrmxzfe-162449552927` healthy.
  La capture attiva mollybabyx sul nodo: 73 frammenti, 24 segmenti, tutti con
  keyframe iniziale e durata >= 2 s. La copia AngelLeeen è ora in quarantena
  con nota; la pagina Stripchat pubblica `doppiocdn.media` come dominio HLS.
  Dettagli e rollback in [LIVE-PANEL-20260909.md](docs/LIVE-PANEL-20260909.md).
  Recupero e limiti in [MP4-HLS-RECOVERY.md](docs/MP4-HLS-RECOVERY.md).
- Container: gira sotto Coolify (UUID sopra); il nome **non** è `livevault`.
  Trovarlo con `docker ps --format '{{.Names}} {{.Image}}'` prima di `docker logs/exec`.
  Fuso del container Europe/Rome (nomi file), nodo Europe/London, DB in UTC.
- NSFW: NudeNet 3.4 ONNX 320n (campionamento) + 640m (verifica), modelli in
  `/data/livevault/models`; analisi dal vivo durante la registrazione (sampler +
  verifier, nice 19, sempre 1 core; dalla 3.4.13 solo il verifier aspetta sopra
  `nsfw_live_max_load`) e scansione completa solo per file non coperti
  (copertura < 85%), a registrazioni ferme quando l'analisi dal vivo è attiva.
  Impostazioni runtime NSFW sul nodo (tabella `app_settings`, non toccate):
  `nsfw_threads=3`, `nsfw_only_when_idle=false`, `nsfw_live_max_load=3.1`,
  `nsfw_step_seconds=4`, `nsfw_live_fps=0.5`.
- Regola NSFW dalla 3.4.16 (richiesta utente 2026-09-26): sopra `nsfw_threshold` decide
  il modello veloce da solo; il 640m ricontrolla solo tra `nsfw_candidate` e
  `nsfw_threshold`; niente più "da controllare" nuovi. Fasce NSFW continue: finiscono
  solo dopo 60 s di fotogrammi puliti (3.4.17, segni live `clear`). In Cronologia
  (3.4.20, richiesta utente) i momenti NSFW sono la barra stessa: fila del simbolo della
  parte nel colore dello stato (rosso REC/disco, verde cloud, blu in elaborazione); la
  3.4.18 a segnaposto e la striscia 3.4.19 sono state scartate. QA visiva con Playwright
  su dati esportati dal nodo: procedura in LIVE-PANEL, sezione NSFW.
- `segment_minutes` portato da 120 a 15 il 2026-09-25 09:50 UTC su richiesta
  utente (PATCH `/api/settings`, vale per le capture avviate dopo): ffmpeg taglia
  nello stesso processo senza buchi; niente più riavvio al limite di ~2 GB (circa
  ogni ora a 1080p). Fino alla 3.4.20 ogni parte da 15 min era pubblicata da sola
  (`_stitch_group_ready` rilasciava ogni 15 min pronti): tra il 25/09 09:50 e il
  27/09 ~08:00 UTC 143 file per 24 sessioni (40,9 h, 91,6 GB) sono finiti spezzati su
  Gofile/Pixeldrain e non sono stati riuniti (serve riscaricarli: chiedere all'utente).
  Dalla 3.4.21 le parti restano in locale e diventano un file unico a ~1,95 GB o 2 h
  (`size_policy.fragments_fill_a_file`, `MERGED_FILE_MAX_SECONDS`) o a fine live dopo
  `session_stitch_gap_minutes` (15 sul nodo); i segni NSFW live vanno sul file unito.
- 3.4.22: con i file da 1-2 h la copertura dal vivo media era 77% (credito 10 s tra
  campioni) e i file restavano «Da analizzare» senza momenti pur avendo i segni
  (tinnydoll 1265/1267/1269). Ora credito 30 s (`COVER_GAP_SECONDS`) e i momenti dal
  vivo sono pubblicati anche sotto `LIVE_COVERAGE_OK` quando c'è almeno un segno NSFW;
  quei file (`needs_full_scan`) restano in locale e in coda per l'analisi completa.
  3.4.23 (decisione utente: niente seconda analisi di ciò che è visto dal vivo): tolti
  `needs_full_scan`, la coda e la trattenuta; `LIVE_COVERAGE_OK` = 0,5 vale solo per
  chiudere «safe» un file senza segni NSFW. `nsfw_hold_delete` messo a false sul nodo
  (PATCH `/api/settings`): il file locale si cancella subito dopo l'upload verificato.
- 2026-09-27 16:40 UTC, su richiesta utente: cancellate le copie locali già caricate
  (rec 1264-1269 e la copia orfana della 370, tutte Gofile con stessa dimensione; stato
  NSFW `skipped` per le non analizzate, i momenti dal vivo restano), la cartella
  `.split-002_mollybabyx_2026-09-04...` (chunk identico alla parte 01 caricata) e la
  quarantena `.002_soft_katy_2026-09-07_16-43-28.recovery-failed.mp4` (caricata come
  rec 251): 12,55 GB. Rimasta `miss_hinata_2026-09-07_15-22-09/.001_...recovery-failed.mp4`
  (0,82 GB, moov mancante, mai caricata): non toccata. L'utente non vuole riunire i
  143 file spezzati su Gofile (25-27/09).
  Primo risultato: registrazione 1109 (tinnydoll, 901 s) chiusa `nsfw` con
  `nsfw_source=live`, copertura 0,997, senza analisi completa. Effetto collaterale
  (falso riavvio "nessun dato HLS scritto" quando la parte unita viene cancellata a
  capture in corso) corretto in 3.4.15.
  Rollback: Impostazioni → Registrazione → Segmento = 120.
- Trovato e corretto in 3.4.12 (verifica su nodo 2026-09-25): l'unione usata in
  produzione (`app/workers/__init__.py`) non collegava mai i segni live (1669
  segni orfani, copertura 0, ogni file rianalizzato); le scansioni complete a 3 core
  giravano accanto alle live (CPU 91–99% 07:06–07:48 UTC, capture con fino al 53%
  di video perso nello stesso intervallo). I motivi dei riavvii capture ora finiscono
  nei log del container (`grep 'capture chiusa'`).
- Deploy in produzione: 3.4.15 `main` bcfa13a, Coolify auto-deploy 2026-09-25
  11:56 UTC, container `ahul2vdjkyvjiwgzpcrmxzfe-115538265753` healthy (prima:
  3.4.12 bd9f744 alle 09:10, 3.4.13 bd6ad1a alle 10:09; 3.4.14 solo nel branch).
  CI verde su ogni commit; la CI di `main` era rossa dalla 3.4.10 per un test non
  isolato, corretto. Backup DB `openastro-action backup_now` prima del primo deploy.
  Rollback: redeploy da Coolify dell'immagine precedente
  (`ahul2vdjkyvjiwgzpcrmxzfe:c6b53572…` = 3.4.11) o revert dei commit
  d62209a..bcfa13a; nessuna modifica di schema.
- Pulizia bench NSFW (2026-09-25, dopo risultati NSFW in app 1102–1105): rimossi
  `/opt/nsfw-bench` (venv 240 MB, copia `640m.onnx` con SHA-256 identico al modello
  in uso, script), `/tmp/nsfw-check`, `/tmp/nsfw-hits`; script archiviati con
  manifest in `/home/astro/archive/nsfw-bench-scripts-20260925.tar.gz`. ONNX EraX,
  video di prova su NVMe, `/tmp/erax-cmp` e `/opt/erax-export` erano già stati
  rimossi a mano dall'utente (history shell di astro).
- Verifica 3.4.12 con live (09:25–10:05 UTC, wasianbby + tinnydoll Chaturbate):
  scansione completa in `waiting_idle`, helper NSFW 9–87% (mai oltre un core),
  capture 25–30%, load 1,2–2,7 fuori dalle unioni. Prima registrazione unita dopo
  il fix, 1106 wasianbby: segni collegati, `nsfw_live_coverage=0,732` (prima sempre
  0) ma < 85% perché il campionatore si fermava col load > 3,1 durante unione da
  1,9 GB + ripartenza (load 5,04): corretto in 3.4.13.
- Aperti, in ordine:
  1. Verifica Stripchat: le registrazioni Chaturbate 1109 e 1110 (tinnydoll) si sono
     chiuse dal vivo (copertura 0,997, niente analisi completa); nessuna live Stripchat
     è andata in onda pubblica dopo il deploy. Controllare la prossima di AliciaBrooks:
     `nsfw_source=live` e copertura ≥ 85% (nomi `.capture.mp4` → `.mp4`).
  2. **Registrazioni micro-frammentate**: la raffica di Top Twins (26 capture in
     75 min, 06:38–07:53 UTC) è iniziata a CPU 45–55%: il motivo del riavvio non era
     registrato. Alla prossima raffica leggere `capture chiusa` nei log. Prima
     causa osservata col nuovo log (09:48 UTC, tinnydoll): dopo 748 s ffmpeg esce
     con `HTTP 403` sul reload della playlist (URL firmato Chaturbate scaduto) e la
     capture riparte subito; da misurare il buco a ogni ripartenza. La chiusura di
     wasianbby delle ~10:07 UTC era invece una fermata dell'utente (sorgente rimossa):
     dalla 3.4.14 il log scrive `fermata manuale` e gli altri motivi interni.
  3. Qualità modello: confronto utente su 171 frame (`F:\erax\confronto.py` sul PC):
     NudeNet con BUTTOCKS a 0.5 = 43 FP, a 0.8 = 0 FP/21 FN; EraX/Felldude ~0 FP e
     ~27 FN ma 3–8× più lenti. Manca il confronto per categoria. Nessun cambio di
     modello deciso; eventuale ritaratura soglie (`nsfw_candidate`/`nsfw_threshold`).
  4. La CPU del nodo è 4 core e il load conta anche l'I/O USB (0,6–2 a riposo). Con
     più live insieme osservare load e `verify_queue`; leve: `nsfw_live_fps`,
     `nsfw_live_max_load` (ora limita solo il verifier).
- Preferenze utente: italiano, push diretto su `main` consentito, nessun trailer
  co-autore nei commit, agire senza chiedere conferme per ogni passo.

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
- Da SSH `astro` (gruppo docker, sudo solo per i wrapper sopra) i percorsi root-only
  come `/opt/openastro-ops` sono stati letti/aggiornati il 2026-09-25 con un
  container usa-e-getta dell'immagine LiveVault già presente (`--network none`,
  bind del solo percorso interessato, `cat` sui file esistenti per conservare
  owner/permessi). Rollback nella sottocartella `.rollback-<data>-docs`.
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
| Torrent Manager | `openastro-torrent.service` + `openastro-torrent-search.service` + Control worker | RPC `127.0.0.1:9091`; solver `127.0.0.1:9092`; provider 1337x verificato |

Gli ingressi HTTPS pubblici principali hanno Funnel attivo. NINA non usa più una
porta Funnel dedicata: `/nina/` sul Funnel `:8443` viene instradato direttamente
al container Coolify NINA, mentre `192.168.1.27:9091` resta l’accesso LAN.
Preservare autenticazione ed esposizione configurata. Control usa `control-panel/`
e helper host distribuiti separatamente. NINA resta isolato anche se la sua UI è
incorporata nel Control Center.
Deploy: [HOSTING.md](HOSTING.md), [nina-monitor/COOLIFY.md](nina-monitor/COOLIFY.md).

LiveVault creator hover preview: i nomi prodotti da `creatorLinkMarkup()` espongono su
puntatore fine una card lazy con massimo tre registrazioni recenti del profilo. Il
frontend attende 250 ms prima della richiesta e mantiene una cache di 90 s. Su touch,
il primo tap sul nome apre la stessa preview senza navigare e il secondo tap sullo stesso
nome apre il profilo; un tap esterno la chiude. Il focus da tastiera resta supportato sui
client desktop. Il payload dedicato è
`GET /api/sources/{source_id}/hover-preview` e non deve essere sostituito dal profilo
completo, che è molto più costoso. Per rollout senza interrompere registrazioni
attive, il frontend mantiene un fallback temporaneo al profilo completo quando il nuovo
endpoint risponde 404; dopo il redeploy applicativo usa automaticamente il payload
leggero. Asset: `app/static/creator-hover.js`; stile nella sezione "Creator hover preview" di `app/static/style.css` (unico foglio di stile dalla 3.1). La card touch usa `box-sizing:border-box` per restare entro il viewport anche con padding e bordo.
Rollback: revert della relativa modifica UI/API e redeploy LiveVault; nessun dato o
schema persistente viene modificato. Il contratto UI resta globale perché tutti i
nomi creator interattivi condividono `data-profile-link`; non duplicare richieste o
implementazioni hover nelle singole viste.

## Storage: contratto da mantenere
| Livello | Origine/mount | Contenuto |
| --- | --- | --- |
| eMMC | /srv/openastro-internal bind su /data | Docker /data/docker, DB, segreti, preview, servizi |
| SERVER NVMe ext4 | UUID 5fe2d0f6-b485-44e9-8e26-31fb0d217db2; /mnt/livevault-nvme | registrazioni pesanti; workspace IA esistente |
| SHARE exFAT | UUID 7EBD-F531; /share | backup DB, dati condivisi e libreria permanente Media Hub in `/share/Media` |
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
Attach verifica UUID, copia con SHA-256/fsync/rename atomico, poi cambia mount; l’unità ha `TimeoutStartSec=900` per non troncare trasferimenti verificati lenti dopo fault USB.
I soli symlink transienti `.active-preview.mp4/.webm` sono scartati durante il merge
quando puntano a un file `.capture` fratello valido: il media viene copiato e verificato
e LiveVault ricrea il puntatore dopo il cambio storage. Qualunque altro symlink resta
un errore di sicurezza. Collisioni diverse preservano entrambe le copie e bloccano il trasferimento.
Dettagli e recupero: [docs/NVME-HANDOFF.md](docs/NVME-HANDOFF.md).

DB, cronologia e cache Media Hub restano interni. I supporti USB rimovibili restano
gestiti sotto `/srv/openastro-media`; la partizione SHARE dell'NVMe non entra nel
gestore hot-plug ma pubblica soltanto `/share/Media` come libreria permanente
`NVMe Media`. Backup (`/share/livevault-backups`) e dati astronomici nella radice
di `/share` restano fuori dal catalogo. Import web/SMB è autenticato; SMB/DLNA
restano LAN-only. `NVMe Media` si espelle esclusivamente con l'intero NVMe.
Non ripristinare le vecchie istruzioni read-only o guest.

Torrent Manager usa Transmission headless con RPC solo loopback e un solver browser
1337x su `127.0.0.1:9092`. Verifica produzione **2026-10-01**: Control, Transmission
e solver attivi; hash runtime uguali alla sorgente validata; `/api/torrents/status`
senza sessione risponde 401. Il nodo riceve 403 dalla richiesta HTTP diretta a 1337x,
quindi usa il solver locale. Il browser challenge è disponibile anche con recorder
LiveVault attivi: il servizio resta `Nice=19`, `CPUWeight=10`, `CPUQuota=100%`,
`IOWeight=10`, `IOSchedulingClass=idle` e Chromium può restare residente fino a 600 s di inattività per riusare la sessione Cloudflare in memoria. Verifica 2026-10-01: sessione fredda `time/desc` 45,1 s, seconda richiesta provider `size/desc` 3,3 s. Le risposte riuscite restano in cache 5 minuti e il cambio Data/Seed/Leech/Dimensione riordina localmente la pagina già caricata senza nuova richiesta. I parziali e i completati non ancora importati vivono in
`/share/.openastro-torrents`, mai nel catalogo Media. Al 100% il Control ferma il job,
sposta il payload in `/share/Media/Downloads`, salva un receipt e rimuove il torrent
senza cancellare il media finale. 1337x è il provider predefinito; magnet e `.torrent`
restano ingressi alternativi. Prima di smontare SHARE l'handoff ferma
`openastro-torrent.service`; dopo attach lo riavvia se abilitato. Backup finali:
`/var/backups/openastro/torrent-manager-final-20261001-072950` e
`/var/backups/openastro/torrent-search-final-20261001-073511`. Procedure e rollback:
[docs/TORRENT-MANAGER.md](docs/TORRENT-MANAGER.md).

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

### 2026-10-01 — resoconto mensile energia DC
Il Control Panel espone in **Sistema → Energia** un riepilogo mensile basato sui
campioni reali ADS1015 già persistiti in `/var/lib/openastro-control/history.sqlite3`.
Sorgenti: `control-panel/server.py`, `control-panel/static/index.html`, `app.js` e
`app.css`. Endpoint autenticato: `/api/energy/monthly?months=3&tz=<IANA>`. I confini
del mese seguono il fuso IANA del browser, non il timezone Europe/London del nodo.
L'integrazione supporta lo storico compattato 10 s / 5 min / 30 min e non attraversa
gap oltre la cadenza attesa; inoltre sottrae i downtime registrati. Restituisce kWh,
W medi, W di picco e copertura, senza stimare periodi mancanti o campioni legacy
`estimated`. Verifica dati produzione prima del deploy: settembre 2026 ~3,721 kWh,
6,2 W medi, 13,7 W picco, copertura 83,2% perché i campioni misurati iniziano il
05/09; ottobre corrente ~0,097 kWh e ~99,6% coperto al momento della verifica.
Rollback: revert del commit UI/API e restart del solo `openastro-control.service`;
il database telemetria non cambia schema e non richiede migrazioni.

### 2026-10-02 — Media file manager + controlli Torrent completi
Il Media Hub ora gestisce direttamente i file della libreria selezionata. Endpoint
autenticato/CSRF: `POST /api/media/manage` con azioni `mkdir`, `rename`, `move`,
`delete`. La UI supporta selezione multipla (max 200), seleziona tutto, nuova cartella,
rename singolo, modalità `Sposta qui` navigabile e cancellazione ricorsiva permanente.
Le mutazioni restano confinate al mount della libreria tramite `_safe_target`; sono
rifiutati `..`, symlink, root, destinazioni esistenti e una cartella dentro se stessa.
Non esiste cestino e il move è solo intra-libreria, quindi nessun copy cross-device.
`media_items`, `media_favorites` e `media_playback` seguono rename/move e vengono
ripuliti dopo delete; nessuna migrazione schema.

Torrent Manager rende visibili per ogni job `Metti in pausa/Riprendi` e `Elimina
download`; remove manuale usa ancora `delete-local-data=true` e quindi elimina la
staging parziale, mai un media già importato. `POST /api/torrents/history/clear` marca
lo storico completati come nascosto mantenendo file finali e receipt anti-duplicazione.
QA 2026-10-02 su NVMe reale: `/share/Media` rilevato `LETTURA / SCRITTURA`, 4 elementi,
selettori 32x32 mobile / 28x28 desktop, pulsanti torrent 40 px mobile, nessun overflow;
selezione → rename/move/delete e modalità `Sposta qui` verificate senza mutazioni reali.
Rollback: ripristinare `media_center.py`, `torrent_manager.py`, `upload_server.py` e gli
asset statici dal backup rollout, poi riavviare solo `openastro-control.service`.
Transmission e LiveVault non richiedono restart.

## Prestazioni e limiti
Campione iniziale con recorder già bloccati: RAM disponibile 2,4 GiB, disco interno
51%, CPU inattiva 82–93%, niente swap/I/O wait corrente, temperatura 48,2 C.
Non è un benchmark a pieno carico. Verifiche video e recovery hanno trattenuto
l'handoff oltre 140 secondi; devono cooperare con il cambio storage e riprovare
dagli originali preservati. Storico: [PERFORMANCE.md](PERFORMANCE.md).

Kernel: undervoltage in questo avvio. vcgencmd: 0x50000, flag storici senza
throttling attuale. Alimentatore/cavo non verificabili tramite pulizia software.
Il profilo è stato successivamente portato dall'utente a 2000 MHz rimuovendo
l'undervolt. Il nuovo boot riporta 0x0; non equivale a stabilità certificata.
Dal 2026-09-09 l'utente ha sospeso l'OC: massimo 1,8 GHz già applicato senza
reboot, minimo 600 MHz schedutil, nessun offset tensione configurato. Boot normale
aggiornato e tryboot archiviato. Lo [storico CM4](docs/CM4-CLOCK-TEST.md) non è più
il profilo da ripristinare; seguire [stabilità](docs/STABILITY-20260909.md).

DNS cifrato attivo, contrariamente al vecchio handoff. Preservare nftables dedicato,
autenticazione/rate limit DoH e openastro-dot-network.timer per IPv6 DoT.
Non eseguire flush ruleset. Servizi locali attivi non provano accessibilità
telefonica/IPv6 esterna; tale verifica resta distinta.

## Provider MyFreeCams / MFC

Adapter nativo candidato **3.5.2**, protocollo ospite verificato dal runtime il
**2026-10-06**. Riconosce username, URL `#username`, profili e MFC Share; alias
`mfc` → `myfreecams`. Stato FCS autorevole, HLS solo per `vs=0`, errore di rete
distinto da offline, qualità e guardia A/V nel recorder esistente. Sorgenti,
evidenze, limiti, rollout e rollback: [MYFREECAMS.md](docs/MYFREECAMS.md).
131 test mirati passati su Windows; CI Linux/Python 3.13 verde (643 Python,
81 JavaScript). Capture pubblica sul nodo verificata: 8,024 s, H.264 720p + AAC;
prova in processo separato, rollout della nuova immagine ancora da completare.

## Provider Stripchat
Verifica 2026-09-13: la risoluzione username → model ID usa
`https://stripchat.com/api/front/users/user-ids/{username}`; il precedente
`/api/front/v2/users/username/{username}` restituisce HTTP 418 e non va
ripristinato. Il JSON corrente espone `id` al top level; il parser conserva
compatibilità con la precedente forma annidata `item.id`. Sorgenti runtime:
`app/stripchat_capture.py` e `app/source_providers/__init__.py`. Le sessioni
Stripchat usano `curl_cffi` con impersonazione Chrome (dipendenza già portata da
yt-dlp), con fallback a `requests` se non disponibile. Il successivo stato camera
resta letto da `/api/front/v2/models/{modelId}/cam`. Il nodo Harness non può
raggiungere direttamente Stripchat, quindi la verifica live dell'endpoint va
fatta dal runtime OpenAstro; unit test e parser non sostituiscono tale prova.
Rollback applicativo: revert del commit provider Stripchat e redeploy LiveVault;
farlo solo se il provider torna esplicitamente al vecchio contratto API.

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

### 2026-09-16 — processing backfill must survive per-pass failures
A production incident left validated `recording_fragments` accumulating while no new consolidated `recordings` were created. The recorder, uploader, thumbnail worker, and storage guard stayed healthy, but the separate `maintenance-backfill` task was not included in worker health and its loop terminated permanently on any uncaught exception from one maintenance pass. Keep the periodic processor resilient: storage handoff/cancellation remains retryable, ordinary per-pass exceptions are recorded under `last_errors["maintenance"]` and the loop continues, and health must expose `maintenance-backfill`. A single damaged/temporarily unreadable archive file must never stop stitching/finalization for later captures.
