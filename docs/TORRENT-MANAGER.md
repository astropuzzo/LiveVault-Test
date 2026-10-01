# OpenAstro Torrent Manager

Verifica sorgente: **2026-10-01**. Questa funzione estende Media Hub senza esporre
la WebUI o l'RPC di Transmission su LAN/Internet.

## Architettura

- client: `transmission-daemon` eseguito come utente `astro` dal servizio
  `openastro-torrent.service`;
- RPC: solo `127.0.0.1:9091`, consumato da
  `control-panel/torrent_manager.py`;
- provider ricerca predefinito: **1337x**, via parser server-side e URL/host
  vincolati; il frontend non contatta direttamente il provider;
- challenge adapter: `openastro-torrent-search.service` su `127.0.0.1:9092`;
  usa Chromium/StealthySession soltanto quando la richiesta HTTP diretta riceve
  403/429/503 o una pagina challenge, serializza le richieste sul thread del browser
  e chiude la sessione dopo 120 s di inattività;
- protezione LiveVault: il fallback browser non parte se `healthz` segnala uno o
  più recorder attivi o se lo stato recorder non è verificabile; magnet e download
  già in coda continuano invece normalmente;
- input alternativi: magnet link e file `.torrent`;
- staging: `/share/.openastro-torrents/{incomplete,complete}`, fuori da
  `/share/Media` e quindi fuori dal catalogo Media Hub;
- destinazione: `/share/Media/Downloads`;
- stato import: `/var/lib/openastro-control/torrent-imports.json`, limitato agli
  ultimi 250 receipt.

Il pannello mostra velocità download/upload, coda, torrent attivi/in pausa,
progresso, dimensione, ETA, peer, ratio, stato/errori e gli import recenti.
La ricerca mostra nome, seed, leecher, dimensione, età e uploader quando forniti
dal provider.

## Completamento e cleanup

Il worker del Control Center controlla i torrent ogni due secondi. Quando
Transmission segnala il 100% e il payload è presente nella directory `complete`:

1. ferma il singolo torrent;
2. rinomina/sposta il payload nella stessa partizione, da staging a
   `/share/Media/Downloads`;
3. scrive il receipt dell'import;
4. rimuove il job da Transmission con `delete-local-data=false`;
5. invalida/aggiorna il catalogo Media Hub.

Il receipt viene scritto prima della rimozione del job: se l'RPC fallisce dopo lo
spostamento, il ciclo successivo ritenta soltanto la rimozione e non duplica il
media. Se `/share` è assente, l'import non parte.

L'azione manuale **Annulla** è diversa: rimuove il job con
`delete-local-data=true`, quindi pulisce i dati incompleti della staging.

## Lifecycle NVMe

`scripts/nvme-handoff.py` considera `openastro-torrent.service` un writer di
SHARE. Prima di smontare `/share` durante eject ferma Transmission; dopo attach
ricrea staging/destinazione, ripristina ownership `astro` e riavvia il servizio
se abilitato. In rollback di un eject fallito riavvia il client se prima era
attivo. Transmission non deve mai trattenere file descriptor sulla partizione
durante lo smontaggio.

## Installazione

Dalla root del checkout host:

```sh
sudo bash scripts/install-torrent-manager.sh
```

Lo script installa `transmission-daemon` e `python3-venv`, disabilita il servizio
distro generico, crea il servizio OpenAstro ristretto e le directory di staging.
Prepara inoltre `/opt/openastro-torrent-search` con una venv dedicata,
`scrapling[fetchers]==0.4.15` e Chromium Playwright per il solo fallback challenge.
Il solver ascolta esclusivamente su loopback e non espone una WebUI. Non cambia
credenziali Control/LiveVault e non espone l'RPC Transmission.

Dopo l'installazione verificare:

```sh
systemctl is-active openastro-torrent.service
systemctl is-active openastro-torrent-search.service
ss -lntp | grep -E '127\.0\.0\.1:(9091|9092)'
findmnt /share
```

RPC e solver devono risultare in ascolto solo su loopback. Il solver può restare
attivo con consumo minimo: il browser viene creato alla prima challenge e chiuso
dopo l'idle configurato.

## API Control

Tutti gli endpoint richiedono una sessione Control; le mutazioni richiedono anche
il token CSRF esistente.

- `GET /api/torrents/status`
- `GET /api/torrents/search?provider=1337x&q=...`
- `POST /api/torrents/add` con `magnet` oppure `detail_url`
- `POST /api/torrents/add-file` con corpo `.torrent`
- `POST /api/torrents/action` con `pause`, `resume` o `remove`

La risoluzione di un risultato 1337x accetta solo URL del provider configurato e
path `/torrent/...`; questo evita che il backend venga usato come fetcher arbitrario.
Il solver locale applica una seconda allow-list a schema HTTPS, domini 1337x noti e
soli path `/search/`, `/sort-search/` e `/torrent/`. Le risposte HTML sono limitate
a 2 MB.

## Limiti

1337x non espone un contratto API stabile usato qui: una modifica al markup può
richiedere un aggiornamento di `_SearchParser` o `_MagnetParser`. Un errore del
provider non interrompe il client torrent e non tocca i job già esistenti. La ricerca
usa la pagina standard `search/...` e ordina i risultati per seed nel backend, così
non dipende dal sorting server-side che 1337x può disabilitare sotto carico. Quando
la protezione del provider richiede il browser, la ricerca può impiegare più tempo.
Durante una registrazione LiveVault il browser non viene avviato: il pannello mostra
l'errore temporaneo e si può riprovare a recorder fermo.

La staging vive su SHARE: con NVMe espulso non si avviano nuovi download e il
servizio resta fermo. La porta peer di Transmission resta soggetta al routing/NAT
e alle regole firewall del nodo; nessuna apertura firewall viene effettuata
dall'installer.

## Rollback

1. fermare e disabilitare `openastro-torrent.service` e
   `openastro-torrent-search.service`;
2. ripristinare `/opt/openastro-control` dal backup precedente al rollout e
   riavviare solo `openastro-control.service`;
3. ripristinare la versione precedente di `scripts/nvme-handoff.py` nel helper
   host se era stata distribuita;
4. non cancellare `/share/.openastro-torrents` durante il rollback finché non è
   stato verificato che non contenga download da conservare.

La rimozione del pacchetto Transmission è opzionale; non è necessaria per
disattivare la funzione.
