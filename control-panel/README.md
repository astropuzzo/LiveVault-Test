# OpenAstro Control

PWA mobile-first per monitorare e controllare il server ASIAIR/Debian. Il servizio
vive sulla memoria interna, resta accessibile senza NVMe e viene pubblicato solo
in HTTPS tramite Tailscale Funnel, con autenticazione applicativa.

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

URL previsto: `https://openastro.tailf2871c.ts.net:8443/`

## Installazione sul telefono

1. Apri l'URL qui sopra da qualunque rete in Chrome (Android) o Safari
   (iPhone/iPad) ed effettua l'accesso.
2. Android: menu e **Installa app**. iPhone/iPad: **Condividi** e
   **Aggiungi alla schermata Home**.

La porta 8443 è pubblicata in HTTPS tramite Tailscale Funnel, ma non richiede
Tailscale sul telefono. Stato e comandi sono protetti da login, cookie sicuro,
CSRF e limitazione dei tentativi. Le credenziali sono nel file locale
del precedente workspace di installazione (`outputs/OpenAstro-Control-access.txt`)
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

`sudo bash scripts/install-media-center.sh` abilita supporti USB rimovibili in sola lettura.
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

OpenAstro Control includes an isolated removable-media hub. Eligible USB filesystems are mounted read-only under `/srv/openastro-media` and are explicitly separated from LiveVault SERVER/SHARE storage.

The Media Hub provides an authenticated remote browser, direct HTTP Range streaming/downloads, an integrated browser player with resume position, local favorites, search/sort/category filters, recent-media indexing, lazy video/image thumbnails, and ffprobe metadata. LAN clients can also use `\\OPENASTRO\\Media` over SMB and `OpenAstro Media` over DLNA. SMB/DLNA are restricted to the LAN; remote access uses the existing HTTPS control-panel authentication.
