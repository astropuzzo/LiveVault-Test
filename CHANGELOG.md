# Changelog

## 2.8.7 — Chaturbate LL-HLS sync

- Chaturbate split LL-HLS: video e audio passano a FFmpeg tramite un unico master sincronizzato.
- Nessun pre-probe dei child playlist LL-HLS prima della registrazione.
- Restart immediato se la sessione HLS perde segmenti o produce frame corrotti.
- Recupero MP4 A/V: trim deterministico alla durata comune e pulizia degli errori di repair risolti.

## 2.8.6 — Daily cloud

- Cartelle Gofile giornaliere per creator: `NOME CREATOR - YYYY-MM-DD`.
- Giornata calcolata in `Europe/Berlin` per allinearsi agli orari Frankfurt della UI.
- PixelDrain crea una lista giornaliera unica quando la giornata si chiude.
- Profilo creator organizzato per giornate con video e link cloud dedicati.
- Click sulla miniatura apre il singolo video remoto quando disponibile.

## 2.8.5 — 2026-09-03
- Registratore: normalizzazione PTS/DTS su ogni input live e correzione delle discontinuità oltre 1 secondo.
- Video ancora in stream-copy; audio rigenerato in AAC 48 kHz con clock asincrono per evitare drift A/V.
- Code FFmpeg dedicate per input e interleave ridotto a 1 secondo.
- Integrity Guard: blocca upload con forte offset A/V o gap video temporali.

## 2.8.4 — 2026-09-03
- Live Pulse: hover sui segmenti REC con storyboard 3x3 a 9 frame.
- Ogni segmento REC rappresenta il file reale e apre il relativo link Gofile/PixelDrain.
- API Pulse espone URL remoto, provider e thumbnail per ciascuna registrazione.
- Backfill automatico dei vecchi storyboard 2x2 quando il file locale è ancora disponibile.

## 2.8.3 — 2026-09-03

- Live Pulse ora mostra gli intervalli **LIVE** e **REC** separatamente, con inizio/fine leggibili per ogni creator.
- L'API Pulse espone gli intervalli reali di registrazione anziché una sola percentuale aggregata.
- Tutti gli orari UI principali usano `Europe/Berlin` (Francoforte), con cambio CET/CEST automatico.
- Timeline resa più leggibile su desktop e mobile senza inline style, mantenendo la CSP stretta.

## 2.8.2 — 2026-09-03

- Corretto **Live Pulse** sotto CSP stretta: rimossi gli inline style bloccati da `style-src 'self'`.
- Tick temporali ora distribuiti con CSS Grid; sessioni renderizzate in SVG con coordinate native `x`/`width`.
- La CSP non viene indebolita; cache PWA aggiornata per forzare il nuovo frontend.

## 2.8.1 — 2026-09-03

- Hotfix **Live Pulse mobile**: asse temporale leggibile, tre tick su schermi stretti e massimo cinque creator visibili.
- Righe più alte, nomi separati dalla timeline e segmenti brevi con larghezza minima maggiore.
- Finestra temporale resa robusta rispetto a timestamp incoerenti e cache PWA invalidata.

## 2.8.0 — 2026-09-02

- **Live Pulse**: timeline operativa delle ultime 12 ore con live correnti, sessioni concluse, copertura REC e sessioni perse.
- Le card della Control Room seguono ora il ciclo della sessione: LIVE/REC durante l'acquisizione, poi terminata, upload e salvata per le sessioni appena concluse.
- **Live DNA** nei profili: impronta settimanale e oraria, durata media, ora di picco e copertura.
- Archivio ridisegnato a gruppi collassabili per **giorno, creator o sessione**, con filtri per periodo, creator, provider, locale/cloud, stato e ordinamento.
- L'Archivio mostra un numero limitato di gruppi alla volta con caricamento progressivo, evitando una lista visivamente infinita.

## 2.7.1 — 2026-09-02

- Ripulita l'interfaccia dal copy descrittivo e ridondante: titoli, stati, numeri e azioni restano; spiegazioni ovvie e sottotitoli duplicati vengono rimossi.
- Compattati Control Room, Live Wall, profili, statistiche, Libreria, Archivio, modali e avviso LIVE non REC.
- Mantenuti soltanto avvisi operativi, conferme distruttive e note tecniche necessarie all'uso sicuro delle funzioni.

## 2.7.0 — 2026-09-02

- Dashboard trasformata in **Control Room**: le creator LIVE salgono automaticamente in una zona dedicata sopra alle offline.
- Aggiunte card LIVE con **preview JPEG 16:9 aggiornata ogni 20 secondi**, prodotta dallo stesso processo FFmpeg della registrazione senza aprire una seconda connessione alla live.
- Le LIVE non registrate sono evidenziate come priorità massima, con motivo della pausa e azione rapida per riprendere la REC.
- Aggiunta **Live Wall** full-screen e responsive per monitorare contemporaneamente tutte le creator online.
- Le creator offline sono ora compatte e collassabili; il loro stato non compete più visivamente con le live.
- Aggiunto **Focus** persistente per fissare creator importanti: le Focus live hanno priorità e le Focus offline restano visibili sopra alla lista collassata.
- I nomi creator continuano ad aprire direttamente il profilo da Control Room, Focus e Live Wall.
- Le preview sono protette dall'autenticazione, scadono rapidamente e vengono rimosse alla fine della sessione di registrazione.

## 2.6.0 — 2026-09-02

- Aggiunto avviso **floating** che compare esclusivamente quando una creator è LIVE ma la registrazione è ferma per pausa globale o della singola creator; i nomi nell'avviso aprono subito il profilo e, con pausa globale, è disponibile anche il ripristino rapido delle REC.
- Le creator messe in pausa continuano a essere monitorate (senza registrarle), così una live non passa inosservata.
- I nomi creator sono ora cliccabili da Dashboard, Libreria, Archivio, REC attive e classifiche statistiche.
- Nuovo registro persistente delle **sessioni live**, inclusi i periodi online mentre le registrazioni sono in pausa.
- Nuova sezione **Statistiche** globale con range 7/30/90/365 giorni, tempo online, giorni online, sessioni live, tempo registrato, copertura, grafici giornalieri/orari e confronto creator.
- Ogni profilo creator include le stesse statistiche e grafici dedicati. Lo storico antecedente alla 2.6.0 viene ricostruito dalle registrazioni come stima minima, senza inventare tempo online non osservato.

## 2.5.2 — 2026-09-02

- Aggiunto **Elimina definitivamente** per le creator nella Libreria e nel profilo.
- La cancellazione permanente rimuove profilo, categorie/raccolte collegate e tutte le configurazioni sorgente associate, fermando prima eventuali recorder attivi.
- Le registrazioni già acquisite, i file locali e le copie cloud vengono deliberatamente conservati nell'Archivio per evitare perdita accidentale di media.
- La conferma UI distingue chiaramente l'archiviazione reversibile dalla cancellazione definitiva della creator.

## 2.5.1 — 2026-09-02

- Finalizza atomicamente gli MP4 frammentati prima dell'upload, con durata completa e indice `faststart` per lo streaming.
- Blocca file ancora in scrittura o MP4 senza durata valida prima che raggiungano Gofile/Pixeldrain.
- Genera anteprime storyboard 2×2 da quattro momenti diversi della registrazione.
- Impedisce a due container di registrare o caricare contemporaneamente durante i deploy `start-first`.
- Recupera automaticamente gli MP4 locali delle versioni precedenti che non erano ancora stati caricati correttamente.

## 2.5.0 — 2026-09-02

- Aggiunta la Libreria profili: un profilo editoriale può collegare più sorgenti/account/provider senza cambiare l'identità operativa usata da recorder e upload.
- Aggiunta la migrazione automatica e idempotente dal database 2.4, con creazione del profilo iniziale per le sorgenti già presenti.
- Aggiunti categorie, preferiti, note e raccolte editoriali per organizzare i profili.
- Tenute separate le raccolte editoriali dalle cartelle cloud per sorgente: categorie e raccolte non spostano né cancellano file locali, miniature, registrazioni o contenuti cloud.
- Aggiunti profilo dettagliato, account collegati, timeline delle registrazioni e statistiche aggregate.
- Aggiunte viste intelligenti, filtri e azioni multiple limitate a modifiche editoriali reversibili.
- La rimozione di una sorgente ora la archivia senza spezzare profilo, registrazioni o cartelle cloud; il ripristino resta esplicito.
- Chiuso il race condition tra controllo sorgente e pausa: un controllo già in corso non può riavviare il recorder dopo la disattivazione.
- Le copertine della Libreria derivano esclusivamente da miniature locali disponibili tramite endpoint autenticati; nessuna immagine profilo esterna viene caricata o memorizzata offline.
- Ridisegnata la dashboard con un aspetto più sobrio, gerarchia visiva più chiara e comportamento responsive su desktop e mobile.

## 2.4.0 — 2026-09-01

- Aggiunto Provider AutoPilot con rilevamento URL e registry backend visibile automaticamente nella dashboard.
- Aggiunti adapter beta per Stripchat, BongaCams, CamSoda, CAM4, Twitch, Kick e YouTube Live; gli extractor mancanti nella build non vengono proposti.
- Aggiunto il preflight `Testa sorgente`: quando la live è online verifica davvero le tracce con FFprobe senza esporre gli URL media.
- Aggiunto Audio Guard fail-closed prima di ogni REC: nessun recorder parte se non sono confermate entrambe le tracce video e audio.
- Corretto il monitor globale: la pausa registrazioni non sospende più i controlli online né `Ultima live vista`.
- Separato esplicitamente il dato storico ufficiale dall'ultima live osservata, senza aggirare restrizioni geografiche o di profilo.
- Bloccati URL arbitrari e redirect non previsti: sono accettati solo host e forme URL dei provider abilitati.
- Aggiunto Dependabot settimanale per aggiornamenti yt-dlp controllati da test e deploy verificato.

## 2.3.0 — 2026-09-01

- Corretto il falso `Ultima live CB mai`: i metadati bloccati per paese/genere sono ora indicati come non disponibili.
- Aggiunto il controllo online leggero per continuare a rilevare le camere con profilo ristretto.
- Separati l'orario ufficiale Chaturbate e l'ultima live osservata direttamente da LiveVault.
- Aggiunto `Controlla ora` per ogni sorgente; il monitor continua ad aggiornarsi anche con le registrazioni in pausa.
- Resi visibili gli errori dei metadati senza trasformare una camera offline in un errore generale.
- Serializzato il deploy: viene pubblicato solo `main` dopo il superamento dei test, evitando riavvii a raffica e 503 temporanei.
- Allineate le versioni applicative e aggiunto `tzdata` per test e installazioni portabili.

## 2.2.3 - 2026-09-01

- Aggiunto fallback per `Ultima live CB` quando `api/biocontext/{username}/` risponde 401/403 per room gated: LiveVault legge la pagina pubblica della room invece di fermarsi su errore/`mai`.
- Il fallback cerca prima un `last_broadcast` ISO embedded, poi `time_since_last_broadcast`, infine la voce visibile `Last Broadcast: ...` della pagina profilo.
- I valori relativi come `20 hours ago`, `5 days ago` o `yesterday` vengono convertiti in un timestamp UTC approssimato; i timestamp ISO restano la fonte preferita e precisa.
- Un 401 di `biocontext` non rende più la sorgente `Errore` se la pagina pubblica fornisce correttamente il dato; l'errore metadata viene mostrato solo quando falliscono entrambe le fonti.
- Aggiunti test di regressione per biocontext 401 → fallback pagina pubblica, parsing ISO/relativo e fallimento di entrambe le fonti.

## 2.2.2 - 2026-09-01

- `Ultima live` ora usa il `last_broadcast` restituito direttamente dai metadata pubblici Chaturbate (`api/biocontext/{username}/`), non l'ultima live osservata localmente da LiveVault.
- Il timestamp viene aggiornato anche quando la camera è offline, quindi LiveVault può mostrare una trasmissione precedente avvenuta mentre il server era spento.
- Rimossi gli override locali che impostavano `Ultima live` a `adesso` solo perché il recorder era attivo o aveva appena terminato una sessione.
- Aggiunti test offline/live sul parsing ISO UTC e sul metadata `last_broadcast`.
- La card sorgente mostra ora la data/ora Chaturbate completa direttamente a schermo, oltre al tempo relativo.

## 2.2.1 - 2026-09-01

- Aggiunta cancellazione reale del file locale per ogni registrazione, anche se non caricata, con conferma esplicita e protezione da upload/conversioni in corso.
- `Elimina tutto` rimuove file locale, miniatura e voce DB in un'unica operazione; il file cloud non viene toccato.
- Nuova pulizia bulk dei video locali, globale o limitata alla camera filtrata, con conteggio file e spazio liberato.
- Pulizia automatica dei file MP4/MKV orfani lasciati da vecchie cancellazioni DB, senza toccare le cartelle dei recorder attivi.
- Tutte le cancellazioni sono confinate alle directory LiveVault per impedire rimozioni accidentali fuori dallo storage applicativo.

## 2.2.0 - 2026-09-01

- Segmenti impostati a 60 minuti con tetto rigido di 2 GB e rollover automatico alla prima soglia raggiunta.
- Audio reso obbligatorio nel mapping FFmpeg: non vengono più accettati né caricati nuovi file solo-video.
- Preferenza per stream combinati audio/video e riconoscimento delle rendition HLS audio anche quando il manifest non dichiara esplicitamente il codec.
- Cartella Gofile pubblica e stabile opzionale per ogni sorgente, con migrazione dei file Gofile precedenti quando l'API lo consente.
- Archivio LiveVault stabile per camera, valido anche per i file caricati su Pixeldrain Free.
- Filtri sorgente esatti, link cloud/archivio separati e impostazione del limite file dalla dashboard.

## 2.1.0 - 2026-09-01

- Corretto il mapping FFmpeg: gli input combinati mantengono sia video sia audio anche quando yt-dlp restituisce più formati.
- Ogni nuovo file deve contenere una traccia video e una audio prima di entrare in upload; codec e presenza stream restano visibili nello storico.
- FFprobe usa un'analisi iniziale limitata e non tratta più il solo timeout della durata come corruzione del file.
- Attesa esplicita della chiusura stabile dei segmenti e arresto parallelo dei recorder durante reboot/deploy.
- Health check Docker reale sui worker e sullo spazio disco.
- Corretto il pulsante Cloud: un URL vuoto non viene più trasformato nell'indirizzo della dashboard.
- Dashboard con conteggi reali di file/sessioni/upload, dimensioni e durata per sorgente, ultimo live, cambio stato e link diretti.
- Timestamp API normalizzati in UTC e riparazione automatica dei vecchi `started_at` incoerenti.
- Log dei normali controlli offline reso silenzioso per lasciare visibili gli errori effettivi.

## 2.0.0 - 2026-08-31

- Dashboard operativa v2 con controlli globali pausa REC, pausa upload e avvio immediato coda.
- MP4 diretto fragmented in stream-copy come container predefinito; MKV opzionale e remux MKV→MP4 dalla UI.
- Miniature persistenti e player video locale.
- Controllo integrità FFprobe/FFmpeg packet scan + SHA-256 alla finalizzazione e immediatamente prima dell'upload.
- Verifica remota Gofile tramite MD5/size e Pixeldrain tramite dimensione + SHA-256 remoto; niente cancellazione locale senza verifica.
- Buffer locale massimo configurabile in GB, hard-stop controllato e soglie disco modificabili live.
- Upload priority per-file (`Upload ora`), retry globale e progresso upload.
- Settings completi dalla UI senza restart.
- Token Gofile e API key Pixeldrain cifrati nel DB con Fernet derivato da APP_SECRET e test connessione integrato.
- Gofile: errori HTTP/non-JSON diagnostici, retry e fallback regionale; corretto il caso HTTP 500 mascherato da `Invalid JSON response`.
- Pixeldrain: gestione esplicita autenticazione API e test account per diagnosticare HTTP 401.
- Migrazione SQLite automatica da v1.x senza perdere storico; backfill progressivo miniature per file locali esistenti.
- Corretto thumbnail seek sui clip corti e compatibilità JPEG con FFmpeg recente.
- Settings di default aggiornati per VPS piccole.
- Suite QA ampliata per MP4, uploader, multipart streaming, integrità e miniature.
- Backup SQLite corretto: snapshot consistente via `sqlite3.backup()` anche con WAL attivo.

## 1.1.1 - 2026-08-31

- Aggiunta guida `START_HERE.md` passo-passo per utenti non tecnici.
- Aggiunto `scripts/enable-tailscale.sh` per accesso HTTPS privato senza IPv4 pubblico.
- Percorso documentato Azure Free + Bastion Developer + Tailscale.
- Indicazioni esplicite per evitare risorse Azure a pagamento involontarie.

## 1.1.0 - 2026-08-31

- Redesign completo della dashboard desktop/mobile con layout più compatto, metriche storage, stato upload e banner REC.
- PWA installabile con service worker e cache limitata ai soli asset statici; nessuna API autenticata viene memorizzata offline.
- Rimossi gli handler JavaScript inline incompatibili con la Content Security Policy; azioni UI ora gestite via event delegation.
- Aggiunta modifica delle sorgenti, pausa/riattivazione più chiara e stato `paused` coerente.
- Ricerca e filtro delle registrazioni per stato; azioni bulk per retry falliti e pulizia delle copie locali già verificate.
- Coda upload migliorata: i nuovi segmenti hanno priorità e un upload fallito usa backoff per-file senza bloccare tutta la coda.
- Recupero automatico degli upload rimasti `uploading` dopo crash/reboot.
- Poll delle sorgenti concorrente con `MAX_PROBE_CONCURRENCY` configurabile.
- Snapshot worker esteso con uptime, dimensione della sessione attiva e upload corrente/task health.
- Health check più informativo e statistiche coda/storage estese.
- Corretto l'esempio di naming dei segmenti nel README.
- Aggiunti test statici anti-regressione per CSP/PWA.

## 1.0.0 - 2026-08-31

- Prima release completa di LiveVault.
- PWA responsive con autenticazione.
- Monitor automatico di sorgenti Chaturbate autorizzate tramite yt-dlp.
- Registrazione FFmpeg stream-copy e segmentazione configurabile.
- Remux MKV -> MP4 senza ricodifica quando il disco lo permette.
- Upload Gofile con Pixeldrain fallback, retry e verifica prima della cancellazione locale.
- Recupero segmenti dopo crash/reboot.
- Gestione automatica delle soglie di spazio disco.
- Docker/Compose provider-independent.
- HTTPS gratuito opzionale con Caddy + sslip.io.
- Script installazione, configurazione storage, update, backup e diagnostica.
- Test unitari e GitHub Actions CI.
