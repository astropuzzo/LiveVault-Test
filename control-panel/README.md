# OpenAstro Control

PWA mobile-first per monitorare e controllare il server ASIAIR/Debian. Il servizio
vive sulla memoria interna, resta accessibile senza NVMe e viene pubblicato solo
in HTTPS tramite Tailscale Funnel, con autenticazione applicativa.

## Interfaccia 3.5.1 (verificata, 2026-09-30)

`static/index.html` e `static/app.css`: evoluzione cosmic glass dell’originale,
rail flottante, stato operativo centrale, monitoraggio asimmetrico e Media Hub
compatto. Tutti i 292 ID operativi e le sette viste conservati; login, player,
fullscreen e hold mantengono i contratti. Il motion condiviso usa entrate/uscite
finite, feedback dei controlli, filtri e grafici. Annullare una conferma ferma
subito hold e comando prima dell’uscita visiva. Movimento ridotto e scheda
nascosta cancellano le animazioni; la telemetria non ripete gli effetti.
Font locali; skin storiche ritirate dalla pagina e dalla precache.
Runtime 3.5.1 da `bee4949...` in `/opt/openastro-control/static`; sette asset
(incluso `app.js`) verificati SHA-256, senza restart, PID 1589194 invariato.
QA, stato rollout, limiti e rollback privato:
[UI-REDESIGN-20260930](../docs/UI-REDESIGN-20260930.md).

## Funzioni

- CPU, memoria, temperatura, uptime, rete e dischi in tempo reale
- grafici persistenti CPU, RAM, temperatura, rete e spazio disco (1h/6h/24h)
- volt, ampere e watt DC dal sensore ADS1015 della scheda ASIAIR Plus CM4
- energia Wh integrata sui campioni reali, con durata coperta e intervalli mancanti esclusi
- profili energetici ECO, Bilanciato, Performance e MAX senza overclock
- hotspot Wi-Fi attivabile o disattivabile con qualsiasi profilo, protetto dalla verifica Ethernet
- stato Docker, Tailscale, backup, LiveVault e Pi-hole
- elenco container e link rapidi alle interfacce
- rimozione sicura e riattivazione NVMe
- backup immediato, riavvio LiveVault/Docker/Pi-hole e reboot controllato
- installazione PWA dalla schermata Home
- esportazione CSV dello storico, con timestamp UTC e valori mancanti conservati
- campionamento condiviso fra client, polling sospeso nelle schede nascoste
- interfaccia coerente con LiveVault, navigazione da desktop e telefono
- Dashboard «Da controllare» e report diagnostico JSON senza segreti o log grezzi

URL previsto: `https://openastro.tailf2871c.ts.net:8443/`

## Installazione sul telefono

1. Apri l'URL qui sopra da qualunque rete in Chrome (Android) o Safari
   (iPhone/iPad) ed effettua l'accesso.
2. Android: menu e **Installa app**. iPhone/iPad: **Condividi** e
   **Aggiungi alla schermata Home**.

La porta 8443 è pubblicata in HTTPS tramite Tailscale Funnel, ma non richiede
Tailscale sul telefono. Stato e comandi sono protetti da login, cookie sicuro,
CSRF e limitazione dei tentativi. Il limite login usa l'IP del client solo come
ultimo valore `X-Forwarded-For` aggiunto dal proxy locale (connessioni da
loopback); valori precedenti o arrivati da altri peer vengono ignorati
(`control-panel/server.py`, `Handler.client_key`, 2026-09-24). Attivo nel runtime
`/opt/openastro-control` verificato il 2026-09-30; rollback: ripristinare la copia
precedente di `server.py` e riavviare solo `openastro-control.service`.
Le credenziali sono nel file locale del precedente workspace di installazione (`outputs/OpenAstro-Control-access.txt`)
e non vengono conservate in chiaro sul server o nel repository.

## Percorsi sul server

- applicazione: `/opt/openastro-control`
- servizio: `openastro-control.service`
- azioni privilegiate: `/usr/local/sbin/openastro-action`
- registro azioni: `/var/log/openastro-control-actions.log`

## Sensore di potenza ASIAIR Plus CM4

Eseguire `sudo bash scripts/enable-asiair-telemetry.sh` dalla radice del repository
per abilitare il bus I2C CSI sui GPIO 44/45 anche ai successivi avvii, senza reboot.
A seconda del kernel il controller può apparire come `/dev/i2c-10`, `/dev/i2c-0` o
come adapter parent: il pannello individua automaticamente l'ADS1015 a `0x4b`.
L'utente del servizio deve appartenere al gruppo `i2c` (già configurato sul nodo).
Non installare un controller delle uscite per leggere i sensori: potrebbe cambiare
lo stato delle porte. Il pannello legge soltanto l'ADC 0x4b e ripristina la sua
configurazione dopo ogni lettura; non modifica GPIO o PWM.

Le conversioni seguono [INDI ASI Power](https://github.com/indilib/indi-3rdparty/blob/master/indi-asi-power/asipower.h).
È potenza all'ingresso DC, inclusi eventuali carichi collegati, non assorbimento
alla presa AC né consumo della sola CPU. Non è stata eseguita una calibrazione
con wattmetro esterno. Il sensore viene letto nella cache condivisa ogni cinque
secondi; lo storico campiona ogni dieci secondi.

Lo storico precedente resta marcato come stimato; non viene mescolato ai watt
misurati. I Wh coprono solo coppie di campioni misurati distanti al massimo 30 s.
Non sono una proiezione sulle 24 ore. Il modello software precedente rimane
disponibile separatamente nell'API e nel CSV, mai come sostituto di un sensore.

## Media Center USB

`sudo bash scripts/install-media-center.sh` abilita supporti USB rimovibili con import in scrittura autenticati.
Il manager accetta soltanto filesystem USB con flag kernel `RM=1` e rifiuta esplicitamente
gli UUID di `SERVER`, `SHARE`, buffer interno, root e boot. I supporti vengono montati sotto
`/srv/openastro-media/<label>-<uuid>` e condivisi come `\\OPENASTRO\Media` via SMB e
`OpenAstro Media` via DLNA esclusivamente sulla LAN Ethernet. Il pannello HTTPS espone invece
un browser autenticato con streaming HTTP Range, download, refresh ed eject sicuro, quindi i
file restano accessibili anche fuori casa senza pubblicare SMB/DLNA su Internet.

`openastro-storage-watchdog.service` è separato dal Media Center e gira dalla eMMC: se un
reset USB rende il filesystem LiveVault assente, `shutdown`, read-only o con UUID errato,
porta LiveVault sul buffer interno da 4 GiB anziché lasciare i worker sul mount guasto.

## Media Hub

OpenAstro Control includes a removable-media hub plus a persistent NVMe library. Eligible USB filesystems are mounted for authenticated read/write import under `/srv/openastro-media`. The SHARE partition remains excluded from the removable-media manager, but `/share/Media` is exposed as the non-ejectable `NVMe Media` library; `/share/livevault-backups` and the rest of SHARE are never indexed by Media Hub.

The Media Hub provides an authenticated remote browser, direct HTTP Range streaming/downloads, an integrated browser player with resume position, local favorites, search/sort/category filters, recent-media indexing, lazy video/image thumbnails, and ffprobe metadata. LAN clients can also use `\\OPENASTRO\\Media` for removable USB media, `\\OPENASTRO\\NVMeMedia` for the persistent SHARE library, and `OpenAstro Media` over DLNA. The Media upload panel displays and copies the SMB path of the currently selected library. SMB/DLNA are restricted to the LAN; remote access uses the existing HTTPS control-panel authentication.

Verifica sorgente 2026-09-30: `control-panel/static/{app.js,index.html}` aggiorna
anche il suggerimento LAN dell'import quando cambia la libreria selezionata:
NVMe usa `\\OPENASTRO\NVMeMedia`, USB usa il percorso pubblicato dal dispositivo.
Senza supporti montati il pulsante copia è disabilitato. Il cambio libreria
azzera subito file e riepilogo della precedente; le risposte in ritardo restano
legate all'UUID richiesto e non possono mostrare file del vecchio supporto.
Runtime previsto: `/opt/openastro-control/static/{app.js,index.html}`;
test Node `tests/test_control_async.cjs`, QA visiva prima del deploy host.
Rollback: ripristinare entrambi gli asset dalla copia privata precedente al deploy;
la cache del service worker segue la versione del rollout. Nessuna modifica SMB,
mount, media o schema del catalogo. Il suggerimento mostra la radice della libreria;
la sottocartella scelta nell'import web resta indicata nel campo destinazione.

### Torrent Manager

Il Media Hub include un client Transmission headless controllato soltanto dal
pannello autenticato. Il provider di ricerca predefinito è 1337x; sono accettati
anche magnet link e file `.torrent`. Il pannello mostra coda, velocità, ETA, peer,
seed/leecher dei risultati, progresso e stato. La staging resta fuori dal catalogo
in `/share/.openastro-torrents`; a completamento il payload viene spostato in
`/share/Media/Downloads` e il job Transmission viene rimosso con
`delete-local-data=false`, quindi il media finale resta disponibile.

Il servizio host è `openastro-torrent.service`, RPC esclusivamente su
`127.0.0.1:9091`; `scripts/install-torrent-manager.sh` installa/configura il
client. L'eject NVMe ferma Transmission prima dello smontaggio SHARE e l'attach lo
riprende dopo aver ricreato le directory gestite. Dettagli operativi, limiti del
parser provider e rollback: [TORRENT-MANAGER](../docs/TORRENT-MANAGER.md).

### Media Hub Level 3

The media panel keeps a persistent SQLite catalog on eMMC (`/var/lib/openastro-control/media.sqlite3`). Playback progress, completion state, favorites, recent history and remembered/offline libraries are server-side and shared by every authenticated browser. Direct HTTP media streams are tracked while active. USB media remains authenticated for writes. The persistent NVMe library is limited to `/share/Media`, follows the main NVMe attach/eject lifecycle, and never exposes the backup directory through the Media Hub.

Verifica sorgente 2026-09-29: `control-panel/media_center.py` condivide la discovery
USB per due secondi anche durante raffiche di richieste file/miniature, ma ricontrolla
il mount a ogni accesso. Le connessioni SQLite si chiudono dopo commit/rollback.
Al limite di 20.000 file, un indice con cartelle ancora da visitare è marcato
incompleto e conserva le voci precedenti delle cartelle non visitate. Il flag
si rilegge dall'evento `scan` già salvato con `media_devices.last_scan`, quindi
resta corretto dopo scadenza della cache e riavvio; nessuna migrazione di schema.
`control-panel/static/app.js` salva il progresso ogni dieci secondi, con un solo
invio per file in corso; pausa/fine/chiusura inviano l'ultima posizione in ordine.
La Home Media sospende le richieste nelle schede nascoste e condivide quelle già
in corso; una mutazione durante una richiesta accoda un nuovo campione prima di
concludere il refresh. Le miniature condividono un solo decoder FFmpeg per volta,
con un thread di decodifica e filtro; richieste simultanee riusano lo stesso JPEG
completo. Il timer locale del cursore resta a 500 ms; non produce una scrittura DB
a ogni aggiornamento.

Gli import in `control-panel/upload_server.py` pubblicano il file con un rename
atomico che rifiuta una destinazione esistente (`renameat2/RENAME_NOREPLACE` su
Linux, incluso SHARE exFAT). Due upload o un import SMB contemporaneo non possono
sovrascriversi: il perdente riceve HTTP 409 e la sola copia temporanea viene rimossa.
Se il filesystem/kernel non supporta la pubblicazione sicura, l'import fallisce
conservando la destinazione. Non cambia il formato del catalogo né i media esistenti.

Runtime verificato il 2026-09-30: `/opt/openastro-control/{upload_server.py,media_center.py,media_streaming.py}` e
`/opt/openastro-control/static/app.js`, hash identici al commit `fc023fd` validato
in CI. Backup completo privato `/var/backups/openastro/20260930-panels-audit/control-before.tar.gz`;
manifest dei nove file installati nella stessa directory. Servizio attivo,
HTTPS/diagnostica HTTP 200, stato senza sessione 401. [Prove e limiti](../docs/PANELS-AUDIT-20260929.md).
Rollback: ripristinare questi quattro file dalla copia di `/opt/openastro-control`
conservata prima del deploy e riavviare soltanto `openastro-control.service` per i
moduli Python; il riavvio invalida le sessioni del pannello. Nessun riavvio delle
app Coolify è necessario. Limiti: la discovery può impiegare due secondi per
vedere un nuovo supporto; i salvataggi periodici possono restare indietro fino a
dieci secondi se il browser viene chiuso senza inviare l'evento finale.

### Media Hub Level 5

Playback is planned per file using ffprobe. Browser-safe H.264/AAC MP4 and WebM profiles use Direct Play; compatible H.264 in other containers uses an HLS remux; incompatible audio can be converted to AAC without re-encoding video; other video can fall back to H.264/AAC HLS through the CM4 V4L2 encoder. Full video transcoding is automatically denied while LiveVault is recording or when thermal/load guards trip. Sidecar SRT/VTT/ASS subtitles are exposed as WebVTT. HLS.js 1.7.2 is vendored locally with its Apache-2.0 license.

Verifica sorgente 2026-09-29, `control-panel/media_streaming.py`: l'indisponibilità
o un payload malformato della salute LiveVault rendono ignoto il numero di capture
e bloccano la sola conversione video completa. Un HTTP 503 conserva invece il
conteggio valido eventualmente presente nel corpo; Direct Play, remux e sola
conversione audio restano disponibili. `upload_server.py`, ingresso del servizio,
avvia una pulizia HLS indipendente dai browser ogni 30 s: una sessione senza
richieste da oltre 600 s termina il solo FFmpeg della sessione e rimuove i suoi
segmenti temporanei, anche se tutti i client hanno chiuso la scheda. Un errore
temporaneo non interrompe i cicli successivi. Il limite si applica alle richieste
HLS, quindi una pausa oltre dieci minuti può richiedere la riapertura del player.
Rollback e percorsi host sono quelli descritti nel Livello 3; nessun originale
media viene eliminato dalla pulizia delle sessioni.

## Visual QA obbligatorio

### Diagnostica Dashboard — 2026-09-29

Sorgenti: `static/diagnostics.js`, `static/diagnostics.css`, `static/index.html`;
runtime `/opt/openastro-control/static`. Riusa `/api/state` e ricalcola localmente
l'età del campione; non aggiunge sonde host o richieste di rete. Gli eventi
firmware storici restano informativi; worker fermi, buffer pieno, spazio critico
e problemi attuali hanno priorità. Il JSON esportato usa una lista esplicita di
campi ammessi senza cookie/token, endpoint, percorsi, nomi file, dispositivi DNS,
log azioni o messaggi di errore grezzi. Non prova stabilità hardware prolungata.
`server.py` conserva il payload health LiveVault HTTP 503 e avvia anche la
manutenzione HLS se lanciato direttamente. Cache PWA `openastro-control-v22.0-audit`.
Rollback: ripristinare index/app/sw/server precedenti dal backup e togliere i due
asset nuovi; riavviare soltanto il servizio Control per il sorgente Python.
Stato QA/deploy e prove effettive in [audit pannelli](../docs/PANELS-AUDIT-20260929.md).

Le modifiche visive al Control Center non si considerano concluse sulla sola base di lint, test DOM o responsive contract. Il flusso di rilascio UI deve includere un rendering reale con dati del nodo, screenshot almeno a viewport mobile 412×915 e desktop 1440×1000, ispezione visiva delle schermate Dashboard/Media/Sistema e una nuova iterazione se gerarchia, densità, spaziature, profondità o stati risultano deboli. Solo dopo il visual QA si copiano gli asset statici in produzione e si verifica che gli hash deployati coincidano con quelli testati.

Il browser QA/Playwright è volutamente separato dal runtime del pannello e non è una dipendenza di produzione. Durante registrazioni LiveVault attive le catture e i test vanno eseguiti a bassa priorità e non devono avviare transcoding, scansioni media forzate o benchmark.

## Skin precedente — ritirata il 2026-09-30

Il livello glass del 2026-09-24 e le skin shell/theme/premium/magic sono
conservati nel repository per recupero storico, ma non sono caricati da
`index.html` né precache delle release 3.5.x. Il nuovo foglio principale è
`static/app.css`, preceduto soltanto dagli stili funzionali diagnostica/import.
Fonte, evidenze e rollback completo della suite in
[UI-REDESIGN-20260930](../docs/UI-REDESIGN-20260930.md). Per tornare alla UI 3.4.31
ripristinare gli asset dal backup privato del rollout; rimuovere un solo link
CSS non basta, perché il redesign ricompone anche il DOM. Nessun restart del
servizio per il ripristino statico.
