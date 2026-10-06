# MyFreeCams / MFC

Verifica protocollo dal nodo: **2026-10-06**. Sorgenti:
`app/myfreecams.py`, `app/source_providers.py`; runtime nell'immagine LiveVault
Coolify sotto `/app/app`. Versione candidata: **3.5.2**.

## Contratto

Il catalogo include `MyFreeCams (MFC)` tramite adapter nativo, anche se yt-dlp
non include un estrattore MFC. `mfc` è un alias in ingresso per `myfreecams`.
AutoPilot riconosce gli URL HTTPS `www.myfreecams.com/#username`, `/username`,
`m.myfreecams.com/#username`, `profiles.myfreecams.com/username` e
`share.myfreecams.com/username`. La scelta esplicita accetta anche lo username.
Il link canonico conserva il frammento `#username`: non eliminarlo come fosse
un normale anchor. URL con credenziali, porte diverse da 443 o username con
separatori/caratteri di controllo sono rifiutati prima del collegamento.

Il resolver legge `https://www.myfreecams.com/_js/serverconfig.js` (cache 1 h),
apre un WebSocket TLS ospite `/fcsl` su un server annunciato dal provider e
interroga il nome con `USERNAMELOOKUP`. Non usa account, password utente o DB.
`websockets>=14,<18`, già presente nel runtime tramite Uvicorn/yt-dlp, è ora
una dipendenza esplicita dell'adapter. Due server al massimo, apertura 6 s,
risposta 10 s per tentativo e chiusura 1 s; framing FCS incrementale con limite
2 MiB. Un errore di rete/protocollo resta `error`, non diventa `offline`.

Stati autorevoli `vs`: 0 = pubblica registrabile; 2 = away; 12/13/14 = privata,
gruppo o club non registrabili; 90/127 = offline. Stati sconosciuti restano
`unknown` non registrabili. L'assenza di ultima live ufficiale è dichiarata
`metadata_status=unsupported`; il monitor conserva il proprio ultimo avvistamento.

Prima di risolvere HLS viene riletta la camera: una stanza diventata privata,
away/offline o sconosciuta non riceve richieste media. Gli edge sono presi dalle
mappe `h5video_servers`, `wzobs_servers`, `ngvideo_servers` e confinati a
`*.myfreecams.com`; nessun edge inventato o redirect. Stream ID = UID + 100000000,
prefissi `mfc_`/`mfc_a_`. Si prova CMAF, poi mobile. La playlist pubblica passa
a yt-dlp con Origin/Referer MFC per scegliere la qualità configurata e al
recorder FFmpeg esistente: copia A/V, segmentazione, unione e guardia ffprobe
audio/video restano operative. Nessuna migrazione di dati o schema.

MFC serve frammenti CMAF `.pts`, anche quando il contenuto è MP4. I demuxer HLS
FFmpeg recenti li rifiutano con `extension_picky=1`. Solo gli input risolti
dall'adapter portano `allow_mfc_pts`: ffprobe e recorder usano `-extension_picky 0`
quando disponibile; nei binari precedenti si aggiunge `.pts` all'elenco ristretto
di estensioni. La presenza dell'opzione è rilevata una volta per binario tramite
`-h demuxer=hls`; gli altri provider mantengono le proprie opzioni. La guardia A/V
continua a rifiutare video senza audio.

## Evidenze e limiti

- Dal container produzione precedente 3.5.1: serverconfig e script ufficiali
  rispondono HTTP 200; WebSocket ospite restituisce UID 3111899, `lv=4`, `vs=127`
  per AspenRae. yt-dlp 2026.08.19 non contiene un estrattore MFC.
- Test locali Windows/Python 3.14: 131 test mirati passati (provider MFC,
  normalizzazione, metadata, stati e recorder). CI Linux/Python 3.13 e prova A/V
  pubblica: da completare prima della promozione. Non dichiarare verificata una capture reale
  sulla base della sola risposta di stato.
- Prova pubblica locale: MollyMayhem UID 29845158, `vs=0`, edge annunciato
  `video1103`, playlist CMAF HTTP 200; ffprobe con opzione `.pts` rileva H.264
  1280×720 e AAC. Da completare prova con il flusso completo del recorder.
- MFC Share è accettato come link al profilo; l'adapter registra solo live
  pubbliche MFC, non acquisti, replay Share, show privati o club.

## Rollout e rollback

Validare branch e CI prima del push su `main`; Coolify distribuisce solo LiveVault
tramite i Watch Paths esistenti. Verificare versione `/healthz`, catalogo nel
container, query ospite e guardia A/V. Conservare file, DB, segreti e immagini.
Aggiornare questa guida e AI-HANDOFF.md nella copia `/opt/openastro-ops`, con
backup mirato e riferimento sorgente in SOURCE.txt, dopo verifica runtime.

Rollback: revert del commit MFC e redeploy del solo LiveVault, oppure ripristino
dell'immagine precedente `ahul2vdjkyvjiwgzpcrmxzfe:bee494925048f52233951514b0ec206cc1042055`
(3.5.1). Le sorgenti MFC eventualmente aggiunte restano nel DB: metterle in pausa
prima del rollback, senza cancellarle. Control/NINA, storage e APP_SECRET non
richiedono modifiche.
