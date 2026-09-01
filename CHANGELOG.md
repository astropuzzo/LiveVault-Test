# Changelog

## 2.2.1 - 2026-09-01

- Aggiunta cancellazione reale del file locale per ogni registrazione, anche se non caricata, con conferma esplicita e protezione da upload/conversioni in corso.
- `Elimina tutto` rimuove file locale, miniatura e voce DB in un'unica operazione; il file cloud non viene toccato.
- Nuova pulizia bulk dei video locali, globale o limitata alla camera filtrata, con conteggio file e spazio liberato.
- Pulizia automatica dei file MP4/MKV orfani lasciati da vecchie cancellazioni DB, senza toccare le cartelle dei recorder attivi.
- Tutte le cancellazioni sono confinate alle directory LiveVault per impedire rimozioni accidentali fuori dallo storage applicativo.

## 2.2.0 - 2026-09-01

- Segmenti impostati a 60 minuti con tetto rigido di 2 GB e rollover automatico alla prima soglia raggiunta.
- Audio reso obbligatorio nel mapping FFmpeg: non vengono più accettati né caricati nuovi file solo-video.
- Preferenza per stream combinati audio/video e riconoscimento delle rendition HLS audio anche quando il manifest non dichiara esplicitamente il codec.
- Cartella Gofile pubblica e stabile opzionale per ogni sorgente, con migrazione dei file Gofile precedenti quando l'API lo consente.
- Archivio LiveVault stabile per camera, valido anche per i file caricati su Pixeldrain Free.
- Filtri sorgente esatti, link cloud/archivio separati e impostazione del limite file dalla dashboard.

## 2.1.0 - 2026-09-01

- Corretto il mapping FFmpeg: gli input combinati mantengono sia video sia audio anche quando yt-dlp restituisce più formati.
- Ogni nuovo file deve contenere una traccia video e una audio prima di entrare in upload; codec e presenza stream restano visibili nello storico.
- FFprobe usa un'analisi iniziale limitata e non tratta più il solo timeout della durata come corruzione del file.
- Attesa esplicita della chiusura stabile dei segmenti e arresto parallelo dei recorder durante reboot/deploy.
- Health check Docker reale sui worker e sullo spazio disco.
- Corretto il pulsante Cloud: un URL vuoto non viene più trasformato nell'indirizzo della dashboard.
- Dashboard con conteggi reali di file/sessioni/upload, dimensioni e durata per sorgente, ultimo live, cambio stato e link diretti.
- Timestamp API normalizzati in UTC e riparazione automatica dei vecchi `started_at` incoerenti.
- Log dei normali controlli offline reso silenzioso per lasciare visibili gli errori effettivi.

## 2.0.0 - 2026-08-31

- Dashboard operativa v2 con controlli globali pausa REC, pausa upload e avvio immediato coda.
- MP4 diretto fragmented in stream-copy come container predefinito; MKV opzionale e remux MKV→MP4 dalla UI.
- Miniature persistenti e player video locale.
- Controllo integrità FFprobe/FFmpeg packet scan + SHA-256 alla finalizzazione e immediatamente prima dell'upload.
- Verifica remota Gofile tramite MD5/size e Pixeldrain tramite dimensione + SHA-256 remoto; niente cancellazione locale senza verifica.
- Buffer locale massimo configurabile in GB, hard-stop controllato e soglie disco modificabili live.
- Upload priority per-file (`Upload ora`), retry globale e progresso upload.
- Settings completi dalla UI senza restart.
- Token Gofile e API key Pixeldrain cifrati nel DB con Fernet derivato da APP_SECRET e test connessione integrato.
- Gofile: errori HTTP/non-JSON diagnostici, retry e fallback regionale; corretto il caso HTTP 500 mascherato da `Invalid JSON response`.
- Pixeldrain: gestione esplicita autenticazione API e test account per diagnosticare HTTP 401.
- Migrazione SQLite automatica da v1.x senza perdere storico; backfill progressivo miniature per file locali esistenti.
- Corretto thumbnail seek sui clip corti e compatibilità JPEG con FFmpeg recente.
- Settings di default aggiornati per VPS piccole.
- Suite QA ampliata per MP4, uploader, multipart streaming, integrità e miniature.
- Backup SQLite corretto: snapshot consistente via `sqlite3.backup()` anche con WAL attivo.

## 1.1.1 - 2026-08-31

- Aggiunta guida `START_HERE.md` passo-passo per utenti non tecnici.
- Aggiunto `scripts/enable-tailscale.sh` per accesso HTTPS privato senza IPv4 pubblico.
- Percorso documentato Azure Free + Bastion Developer + Tailscale.
- Indicazioni esplicite per evitare risorse Azure a pagamento involontarie.

## 1.1.0 - 2026-08-31

- Redesign completo della dashboard desktop/mobile con layout più compatto, metriche storage, stato upload e banner REC.
- PWA installabile con service worker e cache limitata ai soli asset statici; nessuna API autenticata viene memorizzata offline.
- Rimossi gli handler JavaScript inline incompatibili con la Content Security Policy; azioni UI ora gestite via event delegation.
- Aggiunta modifica delle sorgenti, pausa/riattivazione più chiara e stato `paused` coerente.
- Ricerca e filtro delle registrazioni per stato; azioni bulk per retry falliti e pulizia delle copie locali già verificate.
- Coda upload migliorata: i nuovi segmenti hanno priorità e un upload fallito usa backoff per-file senza bloccare tutta la coda.
- Recupero automatico degli upload rimasti `uploading` dopo crash/reboot.
- Poll delle sorgenti concorrente con `MAX_PROBE_CONCURRENCY` configurabile.
- Snapshot worker esteso con uptime, dimensione della sessione attiva e upload corrente.
- Health check più informativo e statistiche coda/storage estese.
- Corretto l'esempio di naming dei segmenti nel README.
- Aggiunti test statici anti-regressione per CSP/PWA.

## 1.0.0 - 2026-08-31

- Prima release completa di LiveVault.
- PWA responsive con autenticazione.
- Monitor automatico di sorgenti Chaturbate autorizzate tramite yt-dlp.
- Registrazione FFmpeg stream-copy e segmentazione configurabile.
- Remux MKV -> MP4 senza ricodifica quando il disco lo permette.
- Upload Gofile con Pixeldrain fallback, retry e verifica prima della cancellazione locale.
- Recupero segmenti dopo crash/reboot.
- Gestione automatica delle soglie di spazio disco.
- Docker/Compose provider-independent.
- HTTPS gratuito opzionale con Caddy + sslip.io.
- Script installazione, configurazione storage, update, backup e diagnostica.
- Test unitari e GitHub Actions CI.
