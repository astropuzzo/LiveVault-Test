# LiveVault v2 — START HERE

Guida breve per avviare LiveVault su una normale VPS Linux. Non dipende da Azure, Google, Oracle o da un provider specifico.

> Usa esclusivamente sorgenti che possiedi o per cui hai autorizzazione esplicita alla registrazione.

## Cosa serve

- VPS Ubuntu 22.04/24.04 o Debian recente;
- almeno 1 vCPU e 1 GB RAM; 2 GB consigliati se vuoi ospitare anche altri servizi;
- 15–50 GB di disco a seconda del buffer;
- accesso SSH/root o utente con `sudo`;
- il file `LiveVault-v2.2.0.tar.gz`.

## 1. Copia il pacchetto sulla VPS

Puoi usare SCP/SFTP oppure un link temporaneo. Una volta che il file è nella home della VPS:

```bash
tar -xzf LiveVault-v2.2.0.tar.gz
cd LiveVault
chmod +x scripts/*.sh
```

## 2. Installa

```bash
./scripts/install.sh
```

Lo script installa Docker se necessario, crea `.env`, genera una secret casuale, ti chiede la password del pannello e avvia il container.

Per la prima prova apri:

```text
http://IP_DELLA_VPS:8080
```

Per uso permanente metti il servizio dietro HTTPS (Tailscale, Caddy o il reverse proxy che preferisci).

## 3. Configura tutto dal pannello

Accedi e premi **Settings**. Da qui puoi cambiare senza modificare `.env`:

- durata segmenti;
- dimensione massima di ogni file;
- MP4/MKV;
- polling e concorrenza;
- controllo integrità;
- miniature;
- buffer massimo in GB;
- soglie disco;
- pausa registrazioni/upload;
- provider primario/fallback;
- retry;
- token Gofile;
- API key Pixeldrain;
- regione Gofile.

Le credenziali inserite dalla UI vengono cifrate nel database usando una chiave derivata da `APP_SECRET`; al browser viene mostrato solo l'ultimo pezzo della credenziale.

Dopo aver incollato una credenziale premi **Test connessione**. Se hai scritto una nuova key/token, il pulsante la salva e la testa immediatamente.

## 4. Impostazione consigliata per VPS piccola

Per una VPS con 20–30 GB di disco:

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

## 5. Aggiungi una sorgente

**Aggiungi sorgente** → username/URL → qualità → conferma autorizzazione → **Salva**.

L'opzione **Una raccolta cloud stabile per questa camera** crea una cartella Gofile dedicata e mostra un link unico all'archivio LiveVault della camera. L'archivio unico include anche gli upload Pixeldrain; Pixeldrain Free non offre cartelle persistenti modificabili.

Quando diventa live, LiveVault registra automaticamente. Ogni segmento chiuso viene:

1. verificato con FFprobe/FFmpeg;
2. hashato SHA-256;
3. corredato di miniatura;
4. messo in coda;
5. ricontrollato prima dell'upload;
6. caricato sul primary/fallback;
7. verificato lato provider;
8. eliminato localmente soltanto se `Delete after upload` è attivo e la verifica remota ha avuto successo.

## 6. Controlli operativi

Dal pannello puoi:

- **Pausa registrazioni**: ferma in modo controllato le REC attive e impedisce nuovi avvii;
- **Pausa upload**: blocca l'avvio dei prossimi upload (non tronca una richiesta HTTP già in corso);
- **Upload ora** globale: riattiva la coda e rimette subito in coda i falliti;
- **Upload ora** su un file: porta quel file in testa alla coda;
- **Ricontrolla**: ripete integrità + SHA;
- **→ MP4**: remux di un MKV locale in MP4 senza ricodifica;
- **Vedi**: anteprima video locale;
- **Libera**: elimina solo la copia locale già caricata/verificata.

## 7. Diagnostica server

```bash
./scripts/status.sh
```

Log live:

```bash
docker compose logs -f livevault
```

Backup DB:

```bash
./scripts/backup.sh
```

## Upgrade da v1.x

Conserva **`.env` e la cartella `data/`**. Sostituisci il codice con v2 e poi:

```bash
docker compose up -d --build
```

Al primo avvio v2 aggiorna automaticamente lo schema SQLite mantenendo sorgenti e storico. I vecchi file locali senza miniatura vengono progressivamente indicizzati per creare le preview. Prima di ogni nuovo upload viene comunque eseguito il controllo integrità v2.

## Prima della VPS: prova gratuita su GitHub Codespaces

Per testare esattamente il codice presente su `main`:

1. GitHub → **Code** → **Codespaces** → **Create codespace on main**.
2. Attendi `postCreateCommand`.
3. Esegui:

```bash
./scripts/codespaces-run.sh
```

4. Apri la porta privata `8080` dalla scheda **PORTS**.
5. Accedi e configura eventualmente Gofile/Pixeldrain da **Settings**.

Per un test rapido automatico del backend:

```bash
./scripts/codespaces-smoke.sh
```

Prima di usare il pacchetto su una VPS, controlla inoltre che GitHub Actions mostri verdi entrambi i job **Core tests** e **Codespaces smoke**.
