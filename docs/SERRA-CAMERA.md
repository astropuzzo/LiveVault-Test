# SERRA: camera locale e timelapse nativo

Verifica: 2026-11-04. Camera Xiaomi MJSXJ03HL convertita a Thingino ufficiale
`firmware-2026-11-02`, T31N/JXQ03P/RTL8189FTV. Backup integrale originale verificato e conservato
privatamente nel workspace dell'utente, fuori da Git. Risoluzione 2304×1296:
nessun ridimensionamento negli MP4.

## Runtime e utilizzo

Pagina autenticata `#serra` nel Control Center esistente, anche nel menu mobile.
Diretta MJPEG nativa a 3 fps, scatti ogni 1/5/10/15/30 secondi o intervalli più
lunghi, pausa/ripresa, limite circolare 1–16 GiB (predefinito 4 GiB).
Impostazione iniziale: uno scatto ogni 15 s. Raccolta indipendente dal browser.

Il timelapse H.264 nativo a 24 fps viene compresso in piccoli blocchi, con x264
`veryfast`, CRF 28, un solo thread. Il primo blocco richiede due scatti; i
successivi vengono pubblicati dopo 120 scatti o circa cinque minuti, fino a
300 scatti per blocco. Il pulsante di aggiornamento anticipa la compressione.
Il servizio ha Nice=15 e CPUQuota=75% di **un** core, non del server intero.

Finestre 1/6/12/24/48/72 ore, 7/14/30 giorni: la playlist HLS usa direttamente
intervalli byte degli stessi blocchi MP4. Il download MP4 li unisce con `-c copy`
senza ricodifica e senza una seconda copia persistente. Le finestre mostrano solo
il periodo già raccolto e ancora conservato; 4 GiB non garantiscono 72 ore per
qualunque scena/intervallo. Il limite comprende JPEG pendenti, MP4 e spazio
temporaneo riservato all'encoder. Per far posto si eliminano solo gli elementi
più vecchi dell'archivio della serra, mai altri media o il backup del firmware.
L'anteprima ripete il video disponibile e aggiorna la playlist a ogni nuovo blocco,
conservando la posizione di visione; non resta in attesa a fine video.

Sorgente `control-panel/serra_camera.py`, runtime `/opt/openastro-control/serra_camera.py`.
Servizio `openastro-serra.service`; indice e impostazioni in
`/var/lib/openastro-control/serra`. Credenziali API private, mai esposte alla UI,
in `/var/lib/openastro-control/serra-camera.json` (0600). Il servizio Control
carica anch'esso `/etc/openastro-serra.env` (o un drop-in equivalente), mantenendo
`ProtectHome=true`. La chiave API della
camera è dedicata; niente cloud o applicazioni Xiaomi. Nessuna password qui.

## Storage e recupero

Media solo nel mount configurato, sottocartelle `serra/{pending,segments}`.
Configurare `OPENASTRO_SERRA_MOUNT` e `OPENASTRO_SERRA_UUID` nel file locale
`/etc/openastro-serra.env`, che non va pubblicato. UUID assente, mount assente o UUID diverso:
nessuna scrittura sulla memoria di sistema. Sotto 2 GiB liberi l'acquisizione
attende, senza cancellare dati esterni. La diretta resta indipendente dall'NVMe.

`scripts/nvme-handoff.py` pubblica per il solo servizio installato il modo storage
in `/run/openastro-serra-storage.json` (root, 0644), senza il token privato.
Scatti, compressione ed export cooperano con `quiesce`/`buffer`. L'encoder viene
interrotto e i JPEG non ancora verificati restano per il tentativo successivo.
Directory aperte impediscono una scrittura accidentale sul disco di sistema
durante uno smontaggio. Non usare lazy unmount.

Il recupero riconcilia JPEG pendenti, blocchi pubblicati e indice dopo un riavvio;
ogni codifica verifica numero di campioni e dimensioni prima di eliminare i JPEG.
Sessione Control richiesta su tutti gli endpoint `/api/serra/*`; modifiche solo
con CSRF valido. Download e diretta sono limitati a uno e due client concorrenti.

## Validazione e rollback

Test Linux Python 3.13.5: quota/ordine di eliminazione, protezione input attivi,
lock di export, ripresa dopo interruzione, finestre e playlist senza segreti,
vero encode/decode FFmpeg 2304×1296 con conteggio fotogrammi identico.
Controlli JavaScript e regressioni frontend. CI del branch richiesta prima della
fusione; prove live e limiti operativi vanno riportati separatamente dai test.

Distribuzione mirata: backup dei file Control modificati e dell'helper storage in
`/var/backups/openastro/serra-20261104-*`; conservare il checkout host sporco.
Ripristinare quei file e fermare/disabilitare `openastro-serra.service`, quindi
riavviare soltanto `openastro-control.service`. Archivio e backup firmware si
conservano. Non riavviare Docker, LiveVault o l'intero server per questo rollback.
