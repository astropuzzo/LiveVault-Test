# MyFreeCams / MFC

Verifica protocollo e runtime dal nodo: **2026-10-06**. Sorgenti:
`app/myfreecams.py`, `app/source_providers.py`; runtime nell'immagine LiveVault
Coolify sotto `/app/app`. Versione distribuita: **3.5.2**, commit runtime
`5ca0275cbaa3ac2801281380106f3236708d76c8`, PR
[47](https://github.com/astropuzzo/LiveVault-Test/pull/47).
Candidato **3.5.3**: link con maiuscole del nome pubblico, login ospite corretto
e verifica dei pacchetti A/V. Il recupero dell'audio nativo di Iam_Sasha resta aperto.

## Contratto

Il catalogo include `MyFreeCams (MFC)` tramite adapter nativo, anche se yt-dlp
non include un estrattore MFC. `mfc` è un alias in ingresso per `myfreecams`.
AutoPilot riconosce gli URL HTTPS `www.myfreecams.com/#username`, `/username`,
`m.myfreecams.com/#username`, `profiles.myfreecams.com/username` e
`share.myfreecams.com/username`. La scelta esplicita accetta anche lo username.
Il link canonico conserva il frammento `#username`: non eliminarlo come fosse
un normale anchor. Dalla 3.5.3 usa le maiuscole del nome salvato, quando coincide
con lo username normalizzato, e del nome ufficiale nella risposta di ispezione:
`https://www.myfreecams.com/#Iam_Sasha`. Un'etichetta personalizzata non sostituisce
lo username. URL con credenziali, porte diverse da 443 o username con
separatori/caratteri di controllo sono rifiutati prima del collegamento.

Il resolver legge `https://www.myfreecams.com/_js/serverconfig.js` (cache 1 h),
apre un WebSocket TLS ospite `/fcsl` su un server annunciato dal provider e
interroga il nome con `USERNAMELOOKUP`. Non usa account, password utente o DB.
Il login ospite standard è `1 0 0 20080909 0 guest:guest`; il comando 3.5.2
con versione 20071025 e nome numerico era rifiutato dal server, anche se la
ricerca pubblica del nome rispondeva comunque.
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

MFC usa `.pts` per segmenti TS o MP4; il nome del percorso `cmaf` non prova
che il contenuto sia CMAF. I demuxer HLS
FFmpeg recenti li rifiutano con `extension_picky=1`. Solo gli input risolti
dall'adapter portano `allow_mfc_pts`: ffprobe e recorder usano `-extension_picky 0`
quando disponibile; nei binari precedenti si aggiunge `.pts` all'elenco ristretto
di estensioni. La presenza dell'opzione è rilevata una volta per binario tramite
`-h demuxer=hls`; gli altri provider mantengono le proprie opzioni. La guardia A/V
continua a rifiutare video senza audio. Dalla 3.5.3 legge tre secondi e conta i
pacchetti di ciascuna traccia: una dichiarazione AAC nella PMT TS, senza pacchetti
audio, non supera il controllo. La lettura mantiene il timeout complessivo e
la chiusura del processo in caso di timeout/cancellazione. Gli altri provider
mantengono il controllo precedente. Questo controllo non misura l'udibilità,
non rileva un guasto iniziato dopo l'avvio e non ricrea audio assente alla sorgente.

## Evidenze e limiti

- Dal container produzione precedente 3.5.1: serverconfig e script ufficiali
  rispondono HTTP 200; WebSocket ospite restituisce UID 3111899, `lv=4`, `vs=127`
  per AspenRae. yt-dlp 2026.08.19 non contiene un estrattore MFC.
- Test locali Windows/Python 3.14: 131 test mirati passati (provider MFC,
  normalizzazione, metadata, stati e recorder). CI Linux/Python 3.13 verde:
  [37432438293](https://github.com/astropuzzo/LiveVault-Test/actions/runs/37432438293),
  643 Python, 81 JavaScript, shell, build/smoke NINA isolato e controllo documentale.
  CI del branch finale [37432820048](https://github.com/astropuzzo/LiveVault-Test/actions/runs/37432820048),
  PR [37432902903](https://github.com/astropuzzo/LiveVault-Test/actions/runs/37432902903)
  e main [37433229205](https://github.com/astropuzzo/LiveVault-Test/actions/runs/37433229205) verdi.
- Prova pubblica locale: MollyMayhem UID 29845158, `vs=0`, edge annunciato
  `video1103`, playlist CMAF HTTP 200; ffprobe con opzione `.pts` rileva H.264
  1280×720 e AAC.
- Prova sul nodo, con il solo modulo pubblico `79e7ea4` caricato in memoria
  in un processo separato e senza installazioni/modifiche al runtime: camera
  pubblica riletta, selezione 720p, guardia ffprobe, comando recorder esistente
  con opzioni MFC e registrazione MP4 terminata con exit 0. File QA temporaneo
  `/tmp/mfc-public-validation-20261006-000.mp4` nel container precedente:
  1484725 byte, 8,024333 s, H.264 1280×720 e AAC. Nessuna sorgente aggiunta al DB;
  non prova ancora il rollout dell'immagine applicativa.
- Rollout verificato nel container `ahul2vdjkyvjiwgzpcrmxzfe-080016657928`:
  HTTPS `/healthz` 200, versione 3.5.2, tutti i worker leader attivi, catalogo
  MyFreeCams disponibile, link `#MollyMayhem` normalizzato e guardia A/V passata.
  Comando recorder distribuito, senza opzioni aggiunte dal test: MP4
  `/tmp/mfc-production-validation-20261006-000.mp4`, 1651687 byte, 8,021667 s,
  H.264 1280×720 e AAC. La prova non ha aggiunto sorgenti al DB.
- La capture preesistente risulta ripresa: un media aperto cresce di 4075043
  byte in 4 s; worker `active_recorders=1`, storage NVMe. NINA mantiene container
  e immagine 3.5.1; Control mantiene PID 2334859. Il rilascio ha sostituito solo
  LiveVault con la normale chiusura/ripresa delle capture; la crescita non prova
  assenza di un buco temporale durante la sostituzione.
- MFC Share è accettato come link al profilo; l'adapter registra solo live
  pubbliche MFC, non acquisti, replay Share, show privati o club.
- Supporto beta: una live pubblica breve verificata; durata lunga e più MFC
  simultanee restano da provare. Lo stato è quello visibile dall'IP del nodo.
- Verifica successiva sull'audio di Iam_Sasha, UID 37174323, 2026-10-06:
  frammento locale 255 con AAC decodificabile ma quasi silenzioso (media -85,5 dB,
  massimo -78,3 dB); frammento 257 con AAC dichiarata ma zero pacchetti/campioni.
  Nessuna registrazione o riga DB è stata modificata. L'utente sente audio sul sito
  anche come ospite: non attribuire il problema alla mancanza di un account.
  Gli HLS pubblici 1080p esaminati hanno zero pacchetti audio; le qualità inferiori
  contengono AAC quasi silenziosa. La variante CMAF con audio separato esponeva
  una playlist audio ferma mentre il video proseguiva. Cambiare solo container,
  mappe FFmpeg o qualità non ha dimostrato un recupero del suono.
- Il servizio del player ufficiale annuncia un profilo H.264/Opus WebRTC.
  La prova del ricevitore in ambiente isolato non ha ricevuto media: nessun
  supporto WebRTC o dipendenza aiortc è incluso nella 3.5.3. Il browser ospite
  di diagnosi richiede verifica dell'età e non permette il confronto con il
  player dell'utente. Alle 14:38 UTC la stanza risultava offline; una nuova prova
  del flusso nativo richiede una live pubblica. Il recupero dell'audio resta non verificato;
  non considerare la presenza di AAC una prova di audio udibile né la 3.5.3
  una correzione completa dell'audio. I file già muti non contengono suono recuperabile.
- Test locali del candidato 3.5.3: 113 mirati/versione passati, inclusi link API salvato e
  ispezione, AAC dichiarata senza pacchetti e AAC con pacchetti. CI e rollout
  finale ancora da verificare; CI del primo candidato
  [37480462386](https://github.com/astropuzzo/LiveVault-Test/actions/runs/37480462386)
  verde, 649 Python e 81 JavaScript, shell, NINA isolato e controllo documentale.
  La funzione candidata eseguita in un processo separato del container 3.5.2
  accetta il campione MollyMayhem e rileva `has_audio=false` nel frammento 257
  di Iam_Sasha, senza modificare il servizio o scrivere sul DB/media.

## Rollout e rollback

Validare branch e CI prima del push su `main`; Coolify distribuisce solo LiveVault
tramite i Watch Paths esistenti. Verificare versione `/healthz`, catalogo nel
container, query ospite e guardia A/V. Conservare file, DB, segreti e immagini.
Aggiornare questa guida e AI-HANDOFF.md nella copia `/opt/openastro-ops`, con
backup mirato e riferimento sorgente in SOURCE.txt, dopo verifica runtime.
Per questo rollout: backup DB completato con `openastro-action backup_now`;
backup documentale mirato in `/opt/openastro-ops/.rollback-20261006-mfc`.
La copia operativa include solo questa guida, AI-HANDOFF.md e SOURCE.txt;
HOSTING.md e i documenti non pertinenti mantengono le rispettive revisioni.

Rollback: revert del commit MFC e redeploy del solo LiveVault, oppure ripristino
dell'immagine precedente `ahul2vdjkyvjiwgzpcrmxzfe:bee494925048f52233951514b0ec206cc1042055`
(3.5.1). Le sorgenti MFC eventualmente aggiunte restano nel DB: metterle in pausa
prima del rollback, senza cancellarle. Control/NINA, storage e APP_SECRET non
richiedono modifiche.
Per la sola 3.5.3, conservare l'immagine 3.5.2
`ahul2vdjkyvjiwgzpcrmxzfe:5ca0275cbaa3ac2801281380106f3236708d76c8`:
il rollback ripristina i link precedenti e la guardia basata sulle sole tracce
dichiarate; non risolve l'audio mancante. Nessuna migrazione o modifica credenziali.
Rollback documentale: ripristinare AI-HANDOFF.md e SOURCE.txt dal backup mirato;
MYFREECAMS.md era assente e può essere rimosso solo nel rollback di questo rollout.
