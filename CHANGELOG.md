# Changelog

## 1.1.2

- Aggiunto test gratuito nativo su GitHub Codespaces, senza Docker.
- Aggiunta configurazione `.devcontainer` con port forwarding 8080.
- Aggiunti `codespaces-prepare.sh` e `codespaces-run.sh`.
- Preset di test: polling 30s, segmenti 10 min, retry 60s e soglie disco ridotte.
- Le credenziali del test restano solo nelle variabili d’ambiente del processo e non vengono scritte nel repository.
- Aggiunta guida `TEST_FREE_CODESPACES.md`.

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
