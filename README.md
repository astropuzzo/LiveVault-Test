# LiveVault v2.2.0

LiveVault è un recorder remoto 24/7 con web dashboard/PWA. Monitora sorgenti autorizzate, registra in segmenti con FFmpeg stream-copy, verifica i media, crea miniature e gestisce un buffer locale con upload automatico Gofile/Pixeldrain.

> **Uso autorizzato soltanto.** Aggiungi esclusivamente trasmissioni che possiedi o per cui hai autorizzazione esplicita alla registrazione.

## Cosa cambia in v2

### Media e anteprime

- **MP4 diretto come default**, segmentato con fragmented MP4 e `-c copy`: nessuna ricodifica.
- Ogni file termina dopo 60 minuti oppure prima di 2 GB; la prima soglia raggiunta chiude ordinatamente il file e avvia il successivo.
- Il mapping audio è obbligatorio: se la sorgente non restituisce audio, FFmpeg fallisce e LiveVault riprova invece di archiviare un MP4 muto.
- MKV ancora disponibile come modalità alternativa.
- Remux manuale **MKV → MP4** dalla dashboard, senza perdita/ricodifica.
- Miniatura JPEG per ogni segmento, mantenuta anche dopo la cancellazione del video locale.
- Player HTML5 per l'anteprima della copia locale.

### Integrità

Ogni segmento nuovo passa attraverso:

1. FFprobe: container, durata e stream audio/video;
2. opzionalmente FFmpeg packet/demux scan completo (`packet`, default);
3. SHA-256 locale;
4. nuovo controllo media + SHA immediatamente prima dell'upload.

Se il file cambia o il demux fallisce viene marcato `integrity_failed` e **non viene caricato né eliminato**.

Verifica remota:

- **Pixeldrain:** confronto esatto di **dimensione + SHA-256 remoto** con i byte locali;
- **Gofile:** verifica MD5 restituito dall'upload e/o dimensione remota quando disponibile. Senza un dato remoto verificabile LiveVault non considera l'upload sicuro per la cancellazione locale.

### Controlli operativi

- pausa/ripresa globale registrazioni;
- pausa/ripresa coda upload;
- stop/pausa per singola sorgente;
- `Upload ora` globale;
- `Upload ora` per singolo file con priorità;
- retry manuale;
- ricontrollo integrità;
- pulizia sicura delle sole copie locali già caricate e verificate;
- avanzamento percentuale dell'upload corrente.

### Buffer configurabile

Da **Settings** puoi impostare:

- `Buffer locale massimo (GB)`;
- hard stop controllato quando viene superato;
- soglie warning/critical/emergency del disco;
- durata segmento;
- `Delete after upload`.

Il conteggio comprende i file locali indicizzati e i segmenti attivi non ancora chiusi. Quando il buffer è pieno non partono nuove registrazioni; con hard-stop attivo le REC in corso vengono chiuse ordinatamente se il limite viene superato.

### Settings e credenziali dalla web app

Non è più necessario modificare `.env` per l'uso normale. Dopo il login puoi configurare:

- Gofile API/account token;
- Gofile Folder ID;
- cartella Gofile stabile opzionale per ogni sorgente e archivio LiveVault stabile per camera;
- regione/proxy upload;
- Pixeldrain API key;
- provider primario/fallback;
- retry e max tentativi;
- tutte le principali opzioni di registrazione/storage.

I token/key salvati dalla UI vengono **cifrati nel database con Fernet**, usando una chiave derivata da `APP_SECRET`. Le API pubbliche della dashboard restituiscono solo `configured=true/false` e le ultime 4 cifre/caratteri, mai il segreto completo.

I pulsanti **Test connessione** verificano le credenziali senza dover attendere il primo upload.

## Errori provider gestiti meglio

### Gofile HTTP 500 / risposta non JSON

L'uploader non traduce più un 500 HTML/proxy in un generico `Invalid JSON response`. Conserva codice HTTP e dettaglio utile, riprova e, in modalità `auto`, può passare dal gateway globale al proxy europeo Paris. Gofile documenta l'upload globale `POST https://upload.gofile.io/uploadfile` e i proxy regionali.

### Pixeldrain HTTP 401

Pixeldrain richiede autenticazione API per creare file. Inserisci una API key valida in **Settings → Pixeldrain** e premi **Test connessione**. Il client usa HTTP Basic con API key come password. Il piano gratuito non espone un filesystem a cartelle: LiveVault mantiene quindi un unico link di archivio per camera che raccoglie sia Gofile sia Pixeldrain, mentre su Gofile crea anche una cartella condivisa persistente.

## Installazione

Vedi **[START_HERE.md](START_HERE.md)**.

Per la configurazione cloud attualmente in uso su TierHive, con CapRover, HTTPS e deploy automatico, vedi **[HOSTING.md](HOSTING.md)**.

In breve:

```bash
tar -xzf LiveVault-v2.2.0.tar.gz
cd LiveVault
chmod +x scripts/*.sh
./scripts/install.sh
```

Poi apri `http://IP_SERVER:8080` per il test iniziale e configura HTTPS per l'uso permanente.

## Requisiti indicativi

Per 1 stream:

- Ubuntu/Debian recente;
- 1 vCPU minimo;
- 1 GB RAM minimo, 2 GB consigliati;
- 15–50 GB disco;
- Docker/Compose;
- FFmpeg/FFprobe sono inclusi nell'immagine Docker.

LiveVault usa stream-copy: CPU e RAM sono molto più basse rispetto a un transcoder.

## Pipeline

```text
Sorgente autorizzata
        │
      yt-dlp
        │
      FFmpeg
  stream-copy MP4/MKV
        │
  segmento chiuso
        │
FFprobe + packet scan
        │
 SHA-256 + thumbnail
        │
   coda / priorità
        │
 ricontrollo pre-upload
        │
 Gofile ──fallisce──> Pixeldrain
   │                       │
verifica MD5/size     verifica size
   └─────────┬─────────────┘
             │
      upload verificato
             │
     delete locale opzionale
       (thumbnail resta)
```

## Stati integrità

- `passed`: media leggibile e controllo completato;
- `failed`: file non leggibile/corrotto o SHA cambiato;
- `pending`: attesa del controllo iniziale.

## Stati upload

- `pending`: in coda;
- `uploading`: trasferimento in corso;
- `uploaded`: upload remoto verificato;
- `failed`: provider falliti; retry con backoff;
- `waiting_config`: nessun provider con credenziali disponibili;
- `integrity_failed`: bloccato dal controllo file;
- `missing`: copia locale scomparsa prima dell'upload.

## Settings principali

Le impostazioni iniziali arrivano da `.env`, poi possono essere sovrascritte e persistite dalla UI:

```env
SEGMENT_MINUTES=60
SEGMENT_MAX_GB=2
CONTAINER_FORMAT=mp4
INTEGRITY_MODE=packet
GENERATE_THUMBNAILS=true
BUFFER_MAX_GB=12
BUFFER_HARD_STOP=true
MIN_FREE_GB=3
CRITICAL_FREE_GB=1.5
EMERGENCY_FREE_GB=0.75
DELETE_AFTER_UPLOAD=true
PRIMARY_UPLOADER=gofile
FALLBACK_UPLOADER=pixeldrain
```

## Sicurezza

- password pannello PBKDF2-SHA256;
- session cookie HttpOnly + SameSite Strict (+ Secure sotto HTTPS);
- CSP senza inline JavaScript;
- rate limiting login;
- token storage cifrato;
- API key mai restituite integralmente al browser;
- file locali scaricabili/visualizzabili solo tramite endpoint autenticati;
- cancellazione manuale consentita solo dopo upload verificato;
- `no-new-privileges` nel container.

**Importante:** conserva `APP_SECRET`. Se lo cambi, le credenziali cifrate dalla UI non saranno più decifrabili e dovranno essere reinserite.

## Upgrade v1.x → v2

Mantieni `.env` e `data/` e ricostruisci il container. Il database SQLite viene migrato automaticamente con nuove colonne; sorgenti, registrazioni e link esistenti restano.

I record v1 mantengono lo stato storico e, se hanno ancora una copia locale, v2 genera progressivamente le miniature mancanti. Prima di un nuovo upload v2 esegue comunque packet scan + SHA.

## Diagnostica

```bash
./scripts/status.sh
docker compose logs -f livevault
```

Backup consistente SQLite/WAL:

```bash
./scripts/backup.sh
```

## Test v2

```bash
python -m compileall -q app tests
PYTHONPATH=. pytest -q
node --check app/static/app.js
bash -n scripts/*.sh
```

## GitHub Codespaces: test obbligatorio prima del deploy

La repository include un ambiente Codespaces per collaudare la build corrente prima di installarla su una VPS.

```text
Code → Codespaces → Create codespace on main
```

Quando la preparazione termina:

```bash
./scripts/codespaces-run.sh
```

Le credenziali Gofile/Pixeldrain si inseriscono ora da **Settings** dopo il login.

È disponibile anche uno smoke test automatico:

```bash
./scripts/codespaces-smoke.sh
```

La CI GitHub ha due job obbligatori di fatto per considerare la build pronta:

- **Core tests**: test Python, FFmpeg/integrità, JavaScript, shell e devcontainer JSON;
- **Codespaces smoke**: prepara l'ambiente con lo stesso script usato dal Codespace, avvia LiveVault v2 e verifica health, login e Settings API.

Se uno dei due job è rosso, la build non va considerata pronta per il deploy.
