# LiveVault v2.8.12

LiveVault è un recorder remoto 24/7 con dashboard web/PWA. Monitora sorgenti autorizzate, registra tramite FFmpeg in stream-copy, verifica i media, crea miniature, gestisce il buffer locale e carica automaticamente su Gofile/Pixeldrain.

> **Uso autorizzato soltanto.** Aggiungi esclusivamente trasmissioni che possiedi o per cui hai autorizzazione esplicita alla registrazione.

## Funzioni principali

### Libreria profili — v2.5.0

La Libreria separa l'identità editoriale della modella dalle sorgenti tecniche usate dal recorder. Un profilo può quindi collegare uno o più account/provider senza modificare l'identità operativa delle sorgenti esistenti.

- profilo con account collegati, timeline delle registrazioni e statistiche aggregate;
- categorie, preferiti e note per organizzare i profili;
- raccolte editoriali distinte dalle cartelle cloud Gofile;
- viste intelligenti e filtri per ritrovare rapidamente profili e registrazioni;
- selezione multipla con azioni editoriali reversibili;
- interfaccia più sobria e responsive, pensata anche per schermi piccoli.

Le copertine della Libreria vengono ricavate esclusivamente dalle miniature locali servite tramite endpoint autenticati. LiveVault non usa immagini profilo esterne e non le inserisce nella cache offline.

Categorie, preferiti, note e raccolte modificano soltanto i metadati della Libreria: **non spostano e non cancellano file locali, miniature, registrazioni o contenuti cloud**. Le cartelle cloud per sorgente continuano a essere gestite separatamente dalla pipeline di upload.

### Registrazione e media

- Audio Guard pre-avvio: FFprobe controlla le tracce reali della live e blocca il recorder se manca audio o video;
- MP4 diretto come default, senza ricodifica;
- MKV opzionale e remux MKV → MP4;
- segmenti fino a 60 minuti / 2 GB;
- audio obbligatorio nel mapping FFmpeg;
- miniature persistenti;
- anteprima e download locale dalla dashboard;
- FFprobe + packet scan + SHA-256;
- ricontrollo integrità immediatamente prima dell'upload.

### Upload

- Gofile come provider primario;
- Pixeldrain come fallback;
- retry e priorità per-file;
- `Upload ora` globale o sul singolo file;
- verifica remota prima di considerare l'upload completato;
- cancellazione automatica locale opzionale dopo upload verificato.

### Monitor sorgenti

- Provider AutoPilot: incolla un URL e LiveVault seleziona l'adapter disponibile;
- adapter inclusi per Chaturbate e, in beta con preflight, Stripchat, BongaCams, CamSoda, CAM4, Twitch, Kick e YouTube Live;
- test sorgente dalla dashboard con stato e verifica reale audio/video quando la live è online;
- stato online/offline aggiornato anche quando le registrazioni sono in pausa;
- data `Last Broadcast` di Chaturbate separata dall'ultima live vista direttamente da LiveVault;
- indicazione esplicita quando Chaturbate nasconde i metadati per paese o genere, senza mostrare il falso valore `mai`;
- controllo immediato per singola camera dalla dashboard.

I blocchi geografici o di profilo imposti dal provider non vengono aggirati. Se il dato storico ufficiale è nascosto, LiveVault mantiene separata la data ufficiale dall'ultima live vista direttamente dal monitor, che continua ad aggiornarsi automaticamente.

### Gestione reale dello storage locale — v2.2.1

La dashboard ora distingue chiaramente lo storico dai byte presenti sul server:

- **Elimina locale**: elimina realmente il file MP4/MKV dal disco e mantiene la voce archivio/minimatura;
- sui file non ancora caricati richiede una conferma esplicita perché il video verrebbe perso definitivamente;
- **Elimina tutto**: elimina file locale, miniatura e voce dal database; eventuali copie cloud non vengono cancellate;
- **Libera caricati**: elimina in blocco le copie locali già caricate e verificate;
- **Pulisci locali**: elimina in blocco tutti i video locali, anche non caricati, globalmente o limitatamente alla camera filtrata;
- la pulizia globale rileva e rimuove anche `.mp4/.mkv` orfani rimasti sul disco dopo vecchie cancellazioni DB;
- le directory di registrazioni attualmente attive vengono sempre escluse dalla pulizia degli orfani;
- ogni cancellazione filesystem è confinata alle directory LiveVault: un path anomalo nel DB non può eliminare file arbitrari del server;
- la UI riporta numero di file rimossi e spazio effettivamente liberato.

## Buffer e disco

Da **Settings** puoi configurare:

- buffer locale massimo in GB;
- hard-stop controllato;
- soglie warning / critical / emergency;
- durata e dimensione massima dei segmenti;
- `Delete after upload`;
- provider primario/fallback e retry.

Quando il buffer raggiunge il limite non partono nuove registrazioni. Con hard-stop attivo le registrazioni in corso vengono chiuse ordinatamente se necessario.

## Credenziali dalla dashboard

Dopo il login puoi configurare senza modificare `.env`:

- Gofile API/account token;
- Gofile Folder ID e regione upload;
- cartella Gofile dedicata per sorgente;
- Pixeldrain API key;
- provider primario/fallback.

Le credenziali sono cifrate nel database tramite Fernet con chiave derivata da `APP_SECRET`. Le API della dashboard restituiscono solo lo stato configurato/non configurato e un hint finale, mai il segreto completo.

## Installazione / server

Vedi **[START_HERE.md](START_HERE.md)**.

Per il setup server/CapRover attualmente documentato, vedi **[HOSTING.md](HOSTING.md)**.

Installazione base:

```bash
cd LiveVault
chmod +x scripts/*.sh
./scripts/install.sh
```

Aggiornamento da repository:

```bash
git pull
docker compose up -d --build
```

Mantieni sempre `.env` e `data/`.

Al primo avvio della v2.5.0, il database 2.4 viene aggiornato automaticamente e in modo idempotente: ogni sorgente esistente riceve il proprio profilo iniziale, mentre ID sorgente, storico, recorder e stato degli upload restano separati dalla nuova organizzazione editoriale. È comunque consigliato eseguire un backup prima di ogni aggiornamento importante.

## Pipeline

```text
Sorgente autorizzata
        │
      yt-dlp
        │
      FFmpeg
   stream-copy
        │
 segmento MP4/MKV
        │
FFprobe + packet scan
        │
 SHA-256 + thumbnail
        │
   coda upload
        │
 ricontrollo pre-upload
        │
 Gofile ──errore──> Pixeldrain
        │                 │
        └──── verifica ───┘
                │
        uploaded verificato
                │
      delete locale opzionale
```

## Stati upload

- `pending`: in coda;
- `uploading`: trasferimento in corso;
- `uploaded`: upload remoto verificato;
- `failed`: provider falliti;
- `waiting_config`: configurazione provider mancante;
- `integrity_failed`: file bloccato dal controllo integrità;
- `converting`: remux MP4 in corso;
- `deleting`: cancellazione locale in corso;
- `discarded`: copia locale eliminata manualmente prima dell'upload;
- `missing`: copia locale non più presente.

## Sicurezza

- password pannello PBKDF2-SHA256;
- session cookie HttpOnly + SameSite Strict (+ Secure sotto HTTPS);
- Content Security Policy senza JavaScript inline;
- rate limiting login;
- token storage cifrato;
- file locali accessibili solo tramite endpoint autenticati;
- protezione delle cancellazioni fuori dalle directory LiveVault;
- blocco cancellazione durante upload/conversione;
- `no-new-privileges` nel container.

**Conserva `APP_SECRET`.** Se viene cambiato, le credenziali salvate dalla UI non saranno più decifrabili.

## Test GitHub

La repository usa una CI focalizzata sulla build reale del server:

```bash
python -m compileall -q app tests
PYTHONPATH=. pytest -q
node --check app/static/app.js
bash -n scripts/*.sh
```

Il job **Core tests** installa anche FFmpeg e tutte le dipendenze di sviluppo. Solo dopo il verde, il job **Deploy verified main** chiede a CapRover di pubblicare l'ultima versione. I push intermedi vengono annullati per evitare riavvii a raffica e `503` temporanei.
