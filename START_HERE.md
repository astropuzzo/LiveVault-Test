# LiveVault v2.3.0 — START HERE

Guida breve per avviare LiveVault su una normale VPS Linux. Non dipende da un provider cloud specifico.

> Usa esclusivamente sorgenti che possiedi o per cui hai autorizzazione esplicita alla registrazione.

## Requisiti

- VPS Ubuntu 22.04/24.04 o Debian recente;
- almeno 1 vCPU e 1 GB RAM; 2 GB consigliati;
- 15–50 GB di disco a seconda del buffer;
- accesso SSH/root o utente con `sudo`;
- Docker/Compose, installabili anche dallo script di setup.

## Installazione da repository

```bash
git clone https://github.com/astropuzzo/LiveVault-Test.git LiveVault
cd LiveVault
chmod +x scripts/*.sh
./scripts/install.sh
```

Lo script crea `.env`, genera `APP_SECRET`, configura la password del pannello e avvia LiveVault.

Per la prima prova:

```text
http://IP_SERVER:8080
```

Per l'uso permanente usa HTTPS tramite il reverse proxy del server. Per il setup hosting documentato vedi **[HOSTING.md](HOSTING.md)**.

## Aggiornamento

Se LiveVault è già installato dal repository:

```bash
cd LiveVault
git pull
docker compose up -d --build
```

Non cancellare `.env` o `data/`.

## Configurazione dal pannello

Dopo il login apri **Settings**. Puoi configurare:

- durata e dimensione massima segmenti;
- MP4/MKV;
- polling e concorrenza;
- controllo integrità;
- miniature;
- buffer massimo;
- soglie disco;
- provider primario/fallback;
- retry;
- token Gofile;
- API key Pixeldrain;
- regione Gofile.

Le credenziali vengono cifrate nel database tramite una chiave derivata da `APP_SECRET`.

## Impostazione consigliata per VPS piccola

```text
Container: MP4
Segmento: 60 min
Dimensione massima file: 2 GB
Integrità: Packet scan + SHA-256
Miniature: ON
Buffer massimo: 8–12 GB
Hard stop buffer: ON
Delete after upload: ON
Primary: Gofile
Fallback: Pixeldrain
```

## Aggiungi una sorgente

**Aggiungi sorgente** → username/URL → qualità → conferma autorizzazione → **Salva**.

Quando diventa live, LiveVault registra automaticamente. Ogni segmento chiuso viene verificato, hashato, corredato di miniatura, messo in coda, ricontrollato e infine caricato sul primary/fallback.

## Gestione file locali

Da v2.2.1 la dashboard gestisce realmente i byte sul server:

- **Elimina locale**: rimuove il file MP4/MKV ma conserva la voce archivio e la miniatura;
- per un file non ancora caricato viene richiesta una conferma esplicita;
- **Elimina tutto**: rimuove file locale, miniatura e voce archivio; non cancella il cloud;
- **Libera caricati**: elimina in blocco le copie locali già caricate/verificate;
- **Pulisci locali**: elimina in blocco tutti i video locali, anche non caricati;
- se non stai filtrando una singola camera, la pulizia cerca anche vecchi file MP4/MKV orfani non più presenti nel database;
- i recorder attivi vengono esclusi automaticamente dalla pulizia degli orfani.

La dashboard mostra quanti file sono stati realmente rimossi e quanto spazio è stato liberato.

## Controlli operativi

- **Pausa registrazioni**: chiude in modo controllato le REC attive e blocca nuovi avvii;
- **Pausa upload**: blocca i prossimi upload;
- **Upload ora** globale: riattiva la coda e riprova i falliti;
- **Upload ora** sul singolo file: gli assegna priorità;
- **Ricontrolla**: ripete integrità + SHA-256;
- **→ MP4**: remux MKV → MP4 senza ricodifica;
- **Vedi**: anteprima della copia locale.
- **Controlla ora**: aggiorna subito una singola camera, anche se le registrazioni sono in pausa.

Se Chaturbate limita una camera per paese o genere del VPS, `Ultima live CB` viene mostrata come **non disponibile**, non come `mai`. Il controllo online leggero resta attivo e LiveVault memorizza separatamente l'ultima live osservata direttamente.

## Diagnostica server

```bash
./scripts/status.sh
```

Log live:

```bash
docker compose logs -f livevault
```

Backup consistente SQLite/WAL:

```bash
./scripts/backup.sh
```

## Controllo GitHub prima di aggiornare il server

GitHub Actions esegue il job **Core tests** su ogni push. Verifica che sia verde prima di fare `git pull` sul server.

La CI controlla:

- compilazione Python;
- suite pytest;
- FFmpeg/media tests;
- sintassi JavaScript;
- sintassi degli script shell.
