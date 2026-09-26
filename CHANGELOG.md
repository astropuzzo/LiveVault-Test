# Changelog

## 3.4.19 — Cronologia NSFW: una striscia sola, attaccata alla sua riga

- La 3.4.18 disegnava lo stesso dato due volte con raggruppamenti diversi (segnaposto ogni ~13 minuti, fasce ogni ~2): colori e posizioni non coincidevano, i segnaposto sembravano appartenere alla riga sotto e i momenti brevi diventavano puntini sotto ogni segnaposto. Ora c'è un solo segno: una striscia sottile sotto la barra della sessione, nel colore della parte più esplicita, con l'icona della categoria solo sui tratti lunghi e mai sovrapposta. Niente contatori.
- Passando col mouse, una linea sottile attraversa la riga e un'etichetta sopra la riga mostra ora e parti viste. Cliccando o toccando la striscia si apre il momento sotto il puntatore (o l'elenco se sono più d'uno); da tastiera si apre l'elenco della riga.
- Tutte le righe hanno la barra alla stessa altezza, con o senza momenti NSFW.
- Motion: alla prima comparsa la striscia si apre da sinistra e le icone compaiono quando la tendina le raggiunge; un tratto ancora in corso ha un piccolo impulso in testa. Le animazioni non si ripetono ai refresh e non vengono più interrotte dal refresh successivo. Con «riduci movimento» tutto è statico.

## 3.4.18 — Cronologia NSFW leggibile: corsia dedicata e motion

- I momenti NSFW hanno una corsia sotto la barra della sessione: non coprono più REC, ONLINE e buchi. Segnaposto sopra, con una punta che indica l'inizio del momento; fasce sotto, sempre visibili anche quando durano pochi pixel.
- Fasce e segnaposto hanno il colore della parte più esplicita. Se nello stesso momento ci sono due parti, il segnaposto ha un anello bicolore invece della mini-icona sovrapposta.
- Motion: le fasce nuove si disegnano da sinistra e i segnaposto nuovi cadono con un piccolo rimbalzo, a cascata. Anima solo ciò che compare la prima volta, i refresh restano fermi. Una fascia ancora in corso scorre con un flusso di luce e ha una cometa pulsante in testa. Al passaggio su un segnaposto le sue fasce si illuminano e il resto si attenua. Con «riduci movimento» tutto resta statico.

## 3.4.17 — Fasce NSFW leggibili, anche da telefono

- Un singolo fotogramma pulito in mezzo a una sequenza NSFW non la spezza più: la fascia finisce solo se il contenuto resta pulito per almeno 60 secondi. Sui dati reali del nodo del 26/09: tinnydoll da 102 a 17 fasce, mollybabyx da 51 a 6.
- Fasce distanti pochi pixel alla scala visualizzata vengono disegnate unite, come già le icone.
- Da telefono le fasce si vedono: prima erano nascoste perché disegnate sotto la traccia, che lì viene tagliata. Ora le righe con momenti NSFW hanno la traccia un po' più alta e la fascia sta dentro.
- Da telefono la legenda della Cronologia torna su una riga: le categorie NSFW andavano a capo in colonna e lasciavano grandi spazi vuoti.

## 3.4.16 — Si fida del modello veloce, fasce NSFW continue

- Sopra la «Soglia NSFW» basta il modello veloce: il momento è NSFW senza ricontrollo. Il modello grande ricontrolla solo i fotogrammi nella fascia incerta tra «Soglia sospetto» e «Soglia NSFW», e conferma o scarta. Niente più «Da controllare» nuovi, niente ricontrollo dei fotogrammi vicini. Sospetto uguale o più alto della soglia NSFW = nessun ricontrollo.
- Le fasce NSFW in Cronologia e nei file durano finché i fotogrammi controllati restano NSFW e finiscono al primo fotogramma pulito, invece di spezzarsi in tanti trattini corti. Dal vivo si salva un segno «pulito» solo quando la sequenza si interrompe (una riga per cambio, non per campione).

## 3.4.15 — Niente falsi riavvii con i segmenti da 15 minuti

- Con il segmento a 15 minuti la parte chiusa viene unita e cancellata mentre la live continua: il controllo «nessun dato HLS scritto» sommava solo i file rimasti, vedeva il totale calare e dopo 35 s riavviava una capture sana (tinnydoll alle 10:26 UTC, subito dopo la prima parte unita). Ora conta tutto ciò che la capture ha scritto, parti già unite comprese.
- Un file cancellato mentre si elencano le parti non può più far fallire il controllo della capture.

## 3.4.14 — Log delle capture più chiaro, copertura dal vivo non azzerata

- Il log `capture chiusa` distingue le fermate decise da LiveVault o dall'utente (fermata manuale, cambio storage, buffer pieno, disco in emergenza, pausa globale, arresto servizio) dai veri problemi dello stream: prima una sorgente messa in pausa a mano risultava «fine stream o errore».
- Una registrazione già collegata all'analisi dal vivo non perde più la sua copertura quando entra in coda per l'analisi completa (sul nodo la 1106 era passata da 73% a 0%).
- Dopo un riavvio del container l'analisi completa aspetta 90 s che le registrazioni riprendano, invece di partire e fermarsi subito.

## 3.4.13 — Il campionamento dal vivo non si ferma più per il carico

- Sul nodo il carico medio conta anche l'I/O USB (1,6–2 a riposo): ogni unione di file o ripartenza lo portava sopra la soglia e il campionamento si fermava. La prima registrazione dopo la 3.4.12 si è chiusa col 73% di copertura ed è finita in coda per l'analisi completa. Ora il campionamento (modello piccolo, bassa priorità, 15–30% di un core) continua sempre; sopra la soglia aspetta solo la verifica col modello grande.

## 3.4.12 — L'analisi dal vivo arriva davvero sui file, niente scansioni accanto alle live

- Bug vero dietro la doppia analisi: l'unione delle parti usata in produzione non collegava mai i segni NSFW dal vivo al file finale (sul nodo 1669 segni, nessuno collegato; copertura sempre 0). Ogni registrazione veniva quindi rianalizzata da capo. Ora i segni e la copertura passano sul file unito e con copertura ≥ 85% la registrazione si chiude come analizzata dal vivo.
- Con l'analisi dal vivo attiva, l'analisi completa dei file aspetta che nessuna live sia in registrazione (riprende da dove era); mentre si registra gli helper NSFW usano sempre 1 core. «Core CPU» vale solo per l'analisi completa a registrazioni ferme. Stamattina le scansioni a 3 core accanto alle live tenevano la CPU al 91–99% e nelle stesse ore le capture hanno perso fino a metà del video.
- La copertura dal vivo conta come continui i campioni distanti fino a 2,5 passi (la stessa tolleranza che unisce i momenti): con quattro live insieme l'85% ora è raggiungibile.
- Ogni capture chiusa scrive una riga nei log del container con motivo, durata, dati scritti e ultime righe di errore: i riavvii a raffica che in Cronologia alternano NON REC / IN ELABORAZIONE ora si possono diagnosticare.
- Corretto un errore saltuario di `/api/status` («dictionary changed size during iteration»).

## 3.4.11 — Analisi NSFW più leggera sulla CPU

- Sul nodo l'analisi usava oltre 3 core su 4 (python 233% + ffmpeg 92%, load ~6,5) pur a nice 19: i thread di onnxruntime giravano a vuoto (spinning) e ffmpeg decodificava con un thread per core. Ora spinning disattivato, ffmpeg `-threads 1`, OMP/OpenBLAS/MKL a un thread per gli helper.

## 3.4.10 — Niente doppia analisi dopo il live

- Bug: l'analisi dal vivo delle live Stripchat registrava i risultati sul file grezzo `.capture.mp4`, ma il file finale è il `.mp4` rimuxato; i nomi non combaciavano, la copertura risultava 0 e ogni registrazione veniva rianalizzata da capo. Ora i due nomi sono collegati: con copertura ≥ 85% basta il live.
- Prima di avviare un'analisi completa già in coda si riprova l'abbinamento con i dati del live (salva i file singoli accodati prima del fix).

## 3.4.9 — Icone NSFW più belle

- Disegno più morbido e pulito, senza riflessi e dettagli che davano un effetto inquietante: tette su busto (non sembrano più occhi), figa, cazzo, culo e buco del culo con toni rosati coerenti.

## 3.4.8 — Fix icone sparite

- In 3.4.7 un attributo duplicato nel simbolo `nsfw-anus` rendeva `icons.svg` XML non valido e il browser non mostrava più nessuna icona. Corretto; nuovo test `tests/test_icons_sprite.py` valida lo sprite.

## 3.4.7 — Nomi diretti e icone dettagliate

- Categorie chiamate come si chiamano: Figa, Cazzo, Buco del culo, Tette, Culo (Cronologia, schede, impostazioni).
- Icone ridisegnate con anatomia e ombreggiature: labbra e clitoride, glande e vene, areole e capezzoli, solco e pieghe; l'ano ha ora un'icona propria.

## 3.4.6 — Categorie NSFW leggibili in Cronologia

- Ogni pin ha il colore della parte più esplicita: genitali femminili magenta, maschili blu, ano viola, seno rosa, glutei arancio; icone più grandi (17 px) e legenda per categoria al posto della voce unica «NSFW».
- Al click (singolo o gruppo) ogni momento elenca tutte le parti viste come chip colorati; anche le schede del file le mostrano.
- «Da controllare» resta distinguibile dal bordo ambra.

## 3.4.5 — Più parti nello stesso momento, icone realistiche

- Se nello stesso fotogramma si vedono più parti (es. seno e genitali) vengono registrate tutte: l'icona mostra la più esplicita (genitali > ano > seno > glutei), la seconda compare come piccolo simbolo sovrapposto, tooltip e schede le elencano tutte.
- Nuove icone piene e ombreggiate, più realistiche e riconoscibili anche piccole.

## 3.4.4 — Recupero dei remux interrotti

- Le copie a metà dei remux Stripchat interrotti non finiscono più in quarantena quando il file grezzo `.capture.mp4` è ancora presente o già caricato; le quarantene già esistenti in quella condizione vengono eliminate in automatico (circa 17 GB sul nodo).
- Le parti grezze rimaste orfane nelle cartelle di sessione vengono ora indicizzate, unite e caricate invece di restare sul disco per sempre.

## 3.4.3 — Cronologia da telefono senza sovrapposizioni

- Colonna dei nomi opaca: le icone scorrono sotto i nomi invece di mischiarsi al testo.
- Icone raggruppate con più spazio (numero sempre leggibile), fasce sotto le barre nascoste su telefono, ultima icona non più tagliata sul bordo.
- Anteprime delle live piccole: resta solo l'etichetta di stato, niente testo sopra l'immagine.

## 3.4.2 — Cronologia leggibile da telefono

- Su telefono la Cronologia si scorre col dito: ogni ora è larga (circa un'ora e mezza visibile), i nomi restano fissi a sinistra, all'apertura si vede il presente e il pulsante "Ora" ci riporta; la posizione resta ferma durante gli aggiornamenti.
- Le icone NSFW troppo vicine per lo spazio reale diventano un'unica icona con il numero; toccandola si apre l'elenco dei momenti con anteprima.
- Legenda su una sola riga scorrevole.

## 3.4.1 — Correzioni segni NSFW sulla Cronologia

- Clic su un segno di una registrazione già chiusa: apre i momenti di quel file direttamente sul momento cliccato (fotogramma, tempo nel file, link al cloud), anche se l'archivio non è ancora stato caricato.
- Segni dal vivo: ogni momento conserva un'anteprima anche quando il suo primo fotogramma viene scartato dal modello preciso (ne resta al massimo una ogni 30 s di momento); dove un'anteprima manca compare un segnaposto invece di un riquadro vuoto.

## 3.4.0 — Analisi NSFW dal vivo

- I momenti con nudità vengono segnati mentre la live è in registrazione: il nodo guarda un fotogramma ogni pochi secondi del file che sta crescendo, a turno tra le live e con un tetto di CPU (la registrazione ha sempre la precedenza).
- Icone stilizzate (seno, glutei, genitali) sulla Cronologia del Monitor in tempo reale e sulla timeline dei momenti in archivio; clic per vedere il fotogramma o aprire il momento nel file.
- Il modello preciso conferma i sospetti lavorando solo su copie salvate nella memoria interna: continua anche mentre l'NVMe è staccato. Il campionamento si ferma solo durante lo stacco/riattacco e riprende da solo sul buffer interno o sull'NVMe.
- Alla chiusura della sessione i segni vengono spostati sulla timeline del file caricato (anche quando più pezzi vengono uniti): se la live è stata vista quasi tutta, non serve l'analisi completa dopo.
- L'analisi completa dei file ora prosegue anche sul buffer interno quando l'NVMe è staccato.

## 3.3.1 — Correzioni analisi NSFW e Gofile

- Anteprime dei momenti salvate durante l'analisi (prima mancavano oltre i primi minuti: estrarle dal file richiedeva più di 60 s sul nodo) e disponibili anche dopo la cancellazione del file locale.
- Miniature sempre cliccabili: con il file locale apre il video in quel punto, senza file locale ingrandisce il fotogramma e copia il tempo da usare nel player del cloud.
- Timeline dei momenti sulla durata del video.
- Gofile: sottocartella per video disattivata di default (creava cartelle col nome interno `…capture`); quando attiva usa il nome del file caricato.

## 3.3.0 — Analisi NSFW

- Nuova analisi opzionale dei video: segna i momenti con nudità con il tempo esatto del file caricato su Gofile/Pixeldrain, con anteprima, "Guarda da qui", "Copia elenco" e link al cloud.
- Due modelli: quello veloce scorre tutto il video, quello preciso conferma solo i sospetti (niente falsi allarmi tipo leggings); nei tratti lunghi ricontrolla una volta al minuto.
- Avanzamento in tempo reale (percentuale, fotogrammi, tempo rimanente), pausa automatica durante le registrazioni e allo stacco dell'NVMe con ripresa dal punto raggiunto.
- Gofile: il link di ogni registrazione ora apre solo quel video (una sottocartella per file dentro la cartella del giorno); disattivabile nelle impostazioni. I file già caricati mantengono il link alla cartella.
- Impostazioni modificabili: frequenza, soglie, core CPU, cosa cercare, attesa prima di eliminare il file locale. Filtri archivio `is:nsfw`, `is:safe`, `is:controllare`.

## 3.2.0 — Glass e nuove funzioni

- Grafica "glass": sfondo ad aurore, pannelli traslucidi sfocati, bordi luminosi, pulsante principale a gradiente; fallback senza sfocatura.
- Riproduzione locale con timeline completa subito e salto a qualsiasi punto (playlist HLS a byte-range, senza ricodifica).
- 17 nuovi siti supportati (TikTok, SOOP, CHZZK, Bigo, Picarto, TwitCasting, Rumble, Niconico e altri).
- Aggiornamento in tempo reale via SSE; previsioni live con modello orario pesato sul recente; notifiche del browser; ricerca archivio avanzata e filtri salvati.

## 3.1.0 — Nuova interfaccia

- Nuovo sistema visivo unico: un solo foglio di stile al posto di sei livelli di correzioni, font Mona Sans ospitato localmente, palette neutra con colori riservati agli stati (live, REC, attenzione).
- Monitor con indicatori a schede, controlli recorder/upload compatti, cronologia con colori sobri e legenda leggibile; Libreria a righe dense; Archivio e Analisi riallineati; pop-up come fogli dal basso su telefono.
- Conferme e rinomine in finestre integrate al posto dei pop-up del browser; eliminazione creator spostata nel menu "Altre azioni".
- I menu si chiudono cliccando fuori, con Esc o dopo la scelta; l'aggiornamento automatico non ridisegna più la vista sotto un menu aperto né quando i dati non cambiano; i gruppi dell'Archivio mantengono lo stato aperto/chiuso.
- Numeri, dimensioni, durate e date formattati in italiano (virgola decimale, "5 ore fa", "14 set").
- Corretti: grafico orario del profilo invisibile, avatar e miniature vuote deformati, titolo di sezione fisso su "Monitor".

## 3.0.0 — Workspace e OpenAstro Control

- Nuova interfaccia coerente, navigazione desktop/mobile, ricerca rapida e filtri sorgenti.
- Esportazione CSV di archivio e telemetria, caricamento progressivo dello storico.
- Un solo foglio stile, risposte testuali compresse e aggiornamenti archivio meno frequenti.
- Pannello versionato: telemetria condivisa, dati mancanti preservati, conteggio recorder corretto.
- Annullamento affidabile delle azioni, supporto tastiera e operazioni server serializzate.

## 2.8.24 — Coda elaborazione ordinata

- Le live lunghe vengono elaborate in blocchi incrementali ogni 15 minuti senza attendere la fine della sessione.
- I gruppi di frammenti pronti vengono elaborati cronologicamente prima dei normali upload, evitando sorpassi silenziosi.
- Durante una registrazione attiva viene usato solo stream-copy: qualità originale e nessuna ricodifica pesante sulla CPU.
- Anche il worker facade pubblica gli stitch atomicamente, senza esporre output parziali.

## 2.8.23 — Stitching atomico

- Il file consolidato diventa visibile solo dopo completamento e verifica, quindi un deploy non può lasciare un MP4 pubblico privo di indice `moov`.
- Il recupero scarta le vecchie righe frammento create per errore da output di stitching interrotti e rimuove il relativo falso allarme.

## 2.8.22 — Stabilità recorder LL-HLS

- Chaturbate parte dall'ultimo segmento LL-HLS completo, evitando init e primi segmenti già scaduti.
- Gli avvisi recuperabili ai confini fMP4 non causano più riavvii continui; resta attivo il watchdog sui byte realmente scritti.
- Ogni reconnect indicizza soltanto i propri file e applica un breve backoff, eliminando REC fantasma e picchi CPU da rivalidazioni duplicate.

## 2.8.21 — Autoripristino registrazioni LL-HLS

- Gli init segment e i primi segmenti LL-HLS scaduti causano subito un nuovo resolve della sorgente.
- Un watchdog riavvia le registrazioni che non scrivono dati per 35 secondi, evitando falsi stati REC.
- Le sessioni senza frammenti recuperabili non generano più errori di stitching permanenti.

## 2.8.20 — Acquisizione Stripchat alleggerita

- La registrazione mantiene la risoluzione massima ma limita la ricodifica a 30 fps stabili.
- Il clip di anteprima viene creato una sola volta invece di tenere acceso un secondo encoder.

## 2.8.19 — Clip preview locale Stripchat

- Il recorder mantiene un breve clip locale indipendente, completo e riproducibile mentre la registrazione principale resta aperta.
- Il player usa il clip finalizzato più recente anziché il contenitore principale ancora incompleto.

## 2.8.18 — Player della registrazione attiva

- `REC locale` crea al primo accesso una breve copia MP4 finalizzata e riproducibile.
- La registrazione principale continua indisturbata e il risultato della preview viene riutilizzato.

## 2.8.17 — Container anteprima Stripchat

- Il file MediaRecorder attivo conserva l'estensione del container realmente prodotto da Chromium.
- I temporanei MP4 creati dalle versioni precedenti vengono riconosciuti dalla firma `ftyp`.

## 2.8.16 — Anteprima registrazione Stripchat

- `REC locale` serve il WebM attivo con il MIME riproducibile dal browser.
- La preview seleziona il file della sessione corrente anziché un frammento precedente.

## 2.8.15 — Stripchat RTMP ingest

- Le live pubbliche Stripchat pubblicate dalla creator via RTMP vengono acquisite correttamente dal WebRTC edge, senza scambiarle per descriptor incompleti.
- Lo stream ID usa `modelId` come fallback quando `streamName` non è presente.

## 2.8.14 — Stati di accesso live

- Gli stati online non accessibili vengono distinti in privata, tip-jar e limitata per tutti i provider che li espongono.
- Chaturbate, Stripchat, CamSoda e BongaCams riconoscono direttamente i segnali di stanza non pubblica; gli altri adapter normalizzano i messaggi equivalenti restituiti dall'upstream.
- La cronologia conserva i passaggi tra live pubblica e stanza non accessibile e li mostra con intervalli e colori dedicati, senza contarli come registrazioni mancate.

## 2.8.13 — Stripchat WebRTC

- Le live pubbliche Stripchat vengono registrate dal flusso WebRTC reale, con audio e video sullo stesso clock, anziché dal playlist HLS pubblicitario.
- I playlist Stripchat marcati come pubblicità vengono rifiutati esplicitamente e non possono più produrre registrazioni false.
- Anteprima live, segmentazione e finalizzazione MP4/MKV sono integrate nel recorder dedicato con output atomico e fallback di sincronizzazione A/V.

## 2.8.12 — Control Room e rollover continuo

- Stati LIVE/REC leggibili, colori coerenti e timeline in ora locale, ridisegnata anche per mobile.
- La timeline include la registrazione ancora attiva: il rollover non appare più come un falso intervallo perso.
- Dopo il limite file, la nuova cattura riparte prima della verifica del frammento chiuso, senza vuoti causati dalla finalizzazione MP4.
- I frammenti privati non vengono più rimuxati prima dello stitching; gli errori storici di timeout vengono rivalidati automaticamente.
- Preview live generate solo su richiesta mentre la dashboard è visibile; Archivio, Pulse e lista completa non lavorano in background senza necessità.
- Le copie locali attive e i frammenti in consolidamento sono subito visibili e riproducibili dal Control Room e dal profilo.
- “Da controllare” apre ora i dettagli; gli avvisi possono essere puliti e il recupero media può essere rilanciato dalla dashboard.
- Dopo crash o perdita di alimentazione, il monitoraggio riparte senza attendere l'analisi dei file; marker atomici e recupero MP4 conservano i dati già scritti.

## 2.8.11 — Cattura concorrente

- Il consolidamento dei batch non blocca più il probe e la ripartenza della registrazione della stessa creator.

## 2.8.10 — Live recovery e stabilità media

- Le sessioni con privato/tip-jar consolidano i frammenti in batch stabili, senza decine di micro-upload né attese indefinite mentre la creator resta online.
- Gli output consolidati non vengono più scambiati per nuovi segmenti di cattura; la rimozione dal buffer è atomica per batch.
- L'anteprima live decodifica soltanto keyframe e usa un solo thread, lasciando CPU alla registrazione audio/video.
- Il fallback di transcode viene rinviato durante una cattura attiva per evitare freeze e perdita di frame sotto carico.
- L'avviso upstream `duplicated MOOV Atom` resta ignorato come rumore non fatale; l'integrità finale continua a essere verificata.
- Link Gofile e anteprime delle giornate creator puntano correttamente al singolo video.

## 2.8.9 — FFmpeg HLS compatibility

- Il master HLS sincronizzato locale non riceve più opzioni `reconnect*` HTTP incompatibili con alcune build FFmpeg.
- Gli input HTTP(S) diretti mantengono reconnect e retry di rete.
- Il percorso LL-HLS sincronizzato continua a usare il transport guard di LiveVault per riavviare la cattura in caso di sessione/segmenti invalidati.

## 2.8.8 — Session stitching

- Una registrazione logica resta aperta per 20 minuti dopo una temporanea uscita dalla live pubblica.
- I reconnect entro 20 minuti vengono uniti in un solo video, senza riempire i gap privati/offline.
- I frammenti intermedi restano locali e invisibili ad Archivio/upload finché la sessione non viene consolidata.
- Stream-copy per lo stitching quando possibile; transcode A/V solo come fallback di compatibilità.
- Rollover automatico a mezzanotte Europe/Berlin per mantenere la separazione cloud giornaliera.
- Recovery dopo riavvio tramite marker persistente della sessione logica.

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
