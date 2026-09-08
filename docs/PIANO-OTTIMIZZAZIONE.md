# Piano esecutivo: video, prestazioni e consumi OpenAstro

Preparato il **2026-09-08** dopo lettura del codice, confronto GitHub e controlli
host in sola lettura. **Le modifiche sotto sono proposte, non già applicate.**
Per accessi, deploy e vincoli prevale [AI-HANDOFF.md](../AI-HANDOFF.md).
Non duplicare qui credenziali o inventario operativo.

## Decisione

Ottimizzare prima il lavoro eseguito per ogni video: meno passaggi ridondanti,
pubblicazione indipendente dalle anteprime, code recuperabili, meno contesa sul
disco. Poi ridurre il costo fisso dei servizi. Valutare UAS e overclock soltanto
con prove distinte: oggi nessuno dei due ha un beneficio misurato su questo nodo.

Conservare qualità audio/video, ricongiungimento delle sessioni, limite fisico
configurato, ordine/priorità degli upload, player e seek, anteprime, riparazioni,
recovery dopo crash ed espulsione NVMe. Non ridurre le funzioni per far scendere
la CPU. Non promettere percentuali di accelerazione prima del confronto.

## Evidenze e limiti della diagnosi

| Verificato | Conseguenza per il lavoro |
| --- | --- |
| GitHub `main`: `ab895fdc5952622fb17abb46e3946ea659403442`; LiveVault live: `ba26e23a521fb68dc27cdb2c0bb01ddeea11122b`. Fra questi commit non cambia il codice media. | Rifare il confronto all'inizio: altre chat possono avanzare ancora. Preservare anche NINA, timeline touch, recovery e azioni archivio. |
| CM4 Rev 1.0, 4 core/4 GiB; `schedutil`, massimo 1500 MHz. Boot: `arm_boost=1`, `arm_freq=1500`, `over_voltage_delta=-35000`. | Frequenza dinamica e undervolt sono già presenti. Non presentarli come nuove ottimizzazioni. |
| Temperatura istantanea 59,9 °C; `get_throttled=0x50000`; journal con undervoltage in questo avvio. | Flag storici, non throttling attuale. Non dimostrano né stabilità sotto carico né causa dell'attesa video. |
| NVMe via bridge UnionSine MD202, VID:PID `0bda:9210`, USB 5000 Mbit/s, driver `usb-storage`. Boot contiene `usb-storage.quirks=0bc2:61b6:u,0bda:9210:u`. | Non è NVMe collegato direttamente a PCIe. La velocità del collegamento non è il throughput misurato. Indagare il motivo della disabilitazione UAS. |
| Impostazioni DB live: `segment_max_gb=2.0`, `integrity_mode=packet`, anteprime attive, `session_stitch_gap_minutes=15`. | Il default nel codice è 20 minuti, ma **il valore attivo è 15**. Non cambiare le preferenze dell'utente. |
| Il codice pubblica blocchi quando i frammenti utilizzabili raggiungono 15 minuti di contenuto; per blocchi inferiori attende la finestra di ricongiungimento e l'assenza di una continuazione attiva. La manutenzione ordinaria ripassa ogni 60 secondi. | Separare attesa intenzionale, attesa in coda ed elaborazione. Un timer più corto non accelera FFmpeg. |
| Lo stitching tenta già `-c copy`; il fallback usa x264 `veryfast`, CRF 18 e AAC. Le riparazioni A/V possono anch'esse ricodificare. | La strada normale conserva i flussi; il fallback esistente non è matematicamente senza perdita. Non aggiungere ricodifica per velocizzare. |
| Verifica completa e SHA-256 dopo montaggio; verifica completa e SHA-256 nuovamente prima dell'upload. La verifica include packet scan e un'ulteriore scansione timestamp video. | Possibile risparmio sostanziale di lavoro, da quantificare. Una lettura logica non equivale sempre a lettura fisica: interviene la cache Linux. |
| Anteprima 3×3 generata prima dell'inserimento del video finale nel DB. Estrazione già limitata a nove seek, un frame per ingresso e thread contenuti. | Separare la disponibilità del video dal completamento della miniatura; non rifare l'ottimizzazione thread già eseguita. |
| `history.json`: 1.632.494 byte nel campione precedente; riscrittura integrale ogni minuto. | Circa 2,35 GB/giorno di scritture logiche a dimensione costante, prima del filesystem. Non è una misura dell'usura NAND. |
| NINA salta già il refresh quando `document.hidden`; il pannello condivide già parte delle sonde. | Evitare interventi già presenti o rallentamenti gratuiti delle UI. |

Nel campione DB di questa indagine: nove frammenti, 1,47 GiB totali; nessuna
Recording locale non cancellata. Non esiste una tabella di tempi delle singole
fasi. Mancano quindi tempi comparabili per attribuire la lentezza a una fase.
I precedenti campioni di CPU/I/O e circa 7,57 W medi dell'ingresso DC non sono un
benchmark controllato; la misura DC include i carichi collegati.

## 0. Baseline breve e mappa dei punti da modificare

1. Leggere `AGENTS.md`, `AI-HANDOFF.md`, `HOSTING.md`, `PERFORMANCE.md` e
   `docs/NVME-HANDOFF.md`. Verificare remoto, checkout e immagini live. Creare un
   branch `codex/`; non resettare il checkout sporco sul nodo.
2. Seguire il codice effettivo: `app/main/__init__.py` installa
   `app/workers/size_policy.py` sul manager e poi applica il wrapper errori.
   `app/workers/__init__.py` estende `app/workers.py`. Modificare solo il file
   legacy può lasciare invariato il percorso live. Verificare import e wrapper.
3. Aggiungere misure leggere per job/fase: tempo monotono, attesa e suo motivo,
   CPU dei figli, byte logici e fisici letti/scritti, numero di probe/remux,
   RSS massimo, modo copia/riparazione, tempo al primo playback e upload concluso.
   Aggregare per fase, con retention limitata; niente log per pacchetto/frame,
   token, URL privati o nomi delle sorgenti.
4. Usare copie di un piccolo set di frammenti chiusi: caso compatibile,
   singolo file già pronto, caso problematico A/V. Aggiungere un blocco vicino al
   limite configurato per prestazioni/dimensione. Stesso set e stesse registrazioni
   concorrenti prima/dopo; una coppia di prove, ripetere solo se rumorosa.
5. Separare secondi/GiB di elaborazione dalla finestra di 15 minuti. Osservare
   CPU, I/O wait/PSI, code disco, RAM/swap, crescita capture e alimentazione.
   Integrare i watt nel tempo per ottenere **Wh/job**; misurare anche watt a riposo
   con gli stessi carichi. Se il sensore non è affidabile, riportare energia ignota.

Non lanciare stress test o scritture raw sui dischi in produzione. Non svuotare
la page cache del sistema per costruire un benchmark artificiale. Annotare
condizioni di cache e workload. File prova in scratch dedicato sullo storage
previsto, mai nel buffer di emergenza; pulizia limitata al manifest della prova.

## 1. Ridurre i passaggi sul video senza indebolire l'integrità

**File:** `app/workers.py`, `app/workers/__init__.py`, `app/recorder.py`,
`app/utils.py`, `app/db.py`; rispettare `app/workers/size_policy.py`.

### Riutilizzare una verifica soltanto dopo aver provato l'identità dei byte

- Dopo l'ultima modifica al contenitore, eseguire verifica completa e SHA-256.
  Salvare una ricevuta: digest, dimensione, modo e versione del validatore,
  esito, metadati A/V e avvisi. Deve riferirsi al file finale, mai ai frammenti
  o ai byte precedenti alla riscrittura dell'indice MP4.
- Prima dell'upload **ricalcolare SHA-256 sul file completo**. Se coincide con
  una ricevuta valida e compatibile, riusare l'esito media senza ripetere packet
  scan e scansione gap. Non basarsi soltanto su percorso, dimensione o mtime.
- Ricevuta assente/obsoleta: percorso completo attuale. Digest inatteso:
  errore di integrità e conservazione del locale. Normalizzazione/riparazione:
  invalidare la ricevuta, verificare nuovamente e aggiornare digest e metadati.
- Proteggere il file da modifiche e cancellazione concorrenti con proprietà del
  job e descrittore stabile; controllare stat prima/dopo hash e passaggi di stato.
  Non indebolire verifica remota, retry o criterio di cancellazione post-upload.
- Rendere la scansione timestamp incrementale, con memoria limitata, timeout e
  cancellazione che termini e attenda il processo. Conservare la semantica
  attuale: il gap è un avviso, non un nuovo errore bloccante.

Questo elimina una verifica ridondante dimostrando che esamina lo stesso
contenuto già validato. Fondere due programmi FFmpeg/ffprobe in un nuovo parser
è una seconda scelta: farlo solo se rimane costoso e si dimostra copertura
equivalente. Non sostituire `packet` con `quick` globalmente.

### Evitare il remux quando non serve

- Per **un solo frammento già finale**, compatibile, sotto limite e validato,
  pubblicare tramite rename o hard link nello stesso filesystem con recovery
  transazionale. Non rimontare byte identici. Se occorre riparare o normalizzare,
  usare staging separato: mai modificare un hard link dell'originale in-place.
- Il controllo `mp4_is_streaming_ready()` è già presente: non attribuire un
  remux a ogni chiamata di `_prepare_mp4()`. Misurare le effettive esecuzioni.
- Conservare la cattura frammentata, utile al recupero di registrazioni
  interrotte; un normale MP4 non chiuso correttamente ha requisiti diversi.
  [Documentazione FFmpeg](https://ffmpeg.org/ffmpeg-formats.html#Fragmentation).
- Prototipo separato per `moov_size` sui soli output finali con dimensione
  limitata: può evitare il secondo passaggio di `faststart` riservando l'indice
  davanti. Se lo spazio riservato non basta, il mux fallisce: fallback al metodo
  attuale, originali intatti. Includere la riserva nel limite fisico del file.
  [Opzioni MP4 FFmpeg](https://ffmpeg.org/ffmpeg-formats.html).

La copia dei flussi non decodifica e ricodifica il video.
[FFmpeg streamcopy](https://ffmpeg.org/ffmpeg.html#Streamcopy).
Prima di concatenare, confrontare codec, parametri/extradata, risoluzione,
timebase e audio: il concat richiede stream compatibili.
[FFmpeg concat](https://ffmpeg.org/ffmpeg-formats.html#concat).
Non introdurre silenziosamente nuovi confini di sessione o rimuovere le
riparazioni esistenti. Se l'incompatibilità impone una scelta fra file unico
ricodificato e parti originali, conservarle e rendere esplicita la scelta:
sono requisiti che non sempre si possono ottenere insieme.

**Accettazione:** stessi flussi nei casi copia, durata e sincronizzazione
coerenti, hash indipendente pre-upload ancora obbligatorio, nessuna perdita
dopo crash/retry. Meno passaggi effettivi e miglioramento misurato nel caso
compatibile. Un hash dell'intero MP4 può cambiare per il solo contenitore:
verificare la qualità con confronto dei flussi/frame decodificati su fixture
brevi, oltre ai controlli di integrità. Non inventare equivalenza dai soli codec.

## 2. Disponibilità rapida e una coda che non lavori inutilmente

**File:** manager workers, `app/db.py`, `app/storage_handoff.py`, `app/utils.py`;
UI pertinente solo per stato/anteprima, senza rifacimenti estetici.

- Dopo verifica, checksum e pubblicazione durabile, inserire il video nel DB
  e renderlo disponibile. Generare lo storyboard in coda separata e persistente,
  con gli stessi nove punti, risoluzione e qualità; mostrare lo stato in attesa.
- La coda anteprime deve proteggere il file dalla cancellazione mentre serve,
  anche se l'upload finisce prima. Retry idempotenti, recupero dopo riavvio,
  nessuna rigenerazione se il risultato per digest/versione è già presente.
- Sostituire scansioni di tutte le righe a ogni minuto con candidati indicizzati
  e job dovuti; usare SQLite esistente e query motivate da `EXPLAIN QUERY PLAN`,
  senza aggiungere Redis/Celery. Tenere una riconciliazione periodica limitata
  per recuperare file o notifiche persi, con tentativi scadenzati e senza starvation.
- Conservare ordine cronologico, priorità esplicite, giorno cloud e finestra di
  ricongiungimento. Allo scadere della finestra, risvegliare il worker: togliere
  l'attesa accidentale del prossimo giro, non i 15 minuti configurati.
- Coordinare scansione, montaggio, riparazione, upload e anteprime sullo stesso
  storage. Partire da un solo lavoro pesante di archivio concorrente alle capture;
  consentire più sovrapposizione soltanto se il confronto migliora il risultato.
  `nice` e affinità su metà core per alcuni fallback esistono già. Non applicare
  un limite indiscriminato all'intero container, che contiene anche i recorder.
- Ogni nuovo job/probe/hash lungo deve cooperare con quiesce; includere anche
  thumbnail e split oversized nella verifica delle cancellazioni. Il token ready
  può essere emesso solo dopo rilascio dei file e uscita dei processi/thread.
  Cancellare un `asyncio.to_thread` non dimostra che il lavoro sottostante sia finito.

**Accettazione:** playback pronto prima dell'anteprima; storyboard finale identico
nelle proprietà; ordine invariato; nessuna crescita illimitata della coda;
eject e buffer funzionanti anche durante questi lavori. La concorrenza si
sceglie su latenza e Wh/job, non sul desiderio di vedere tutti i core al 100%.

## 3. Meno consumo fisso, stesse funzioni

**File:** `control-panel/server.py`, `scripts/openastro-storage-watchdog.py`,
`scripts/openastro-media-manager.py`, documentazione dei rispettivi servizi.

- **Telemetria incrementale:** piccolo DB SQLite su eMMC o append journal con
  compattazione controllata. Preferenza SQLite, transazioni per i sei campioni
  del minuto, gestione WAL/checkpoint misurata. Importare il vecchio JSON senza
  distruggerlo, mantenere API, ordinamento, precisione, storico e misura downtime.
  Campionamento 10 s e persistenza non peggiore di 60 s; retention invariata:
  10 s/24 h, 5 min/7 giorni, 30 min/90 giorni. Nessun abbassamento delle garanzie
  di sincronizzazione per far apparire migliori i consumi.
- **Watchdog:** sostituire fork ripetuti di `findmnt`/`lsblk` con lettura
  `/proc/self/mountinfo` nel namespace host e sysfs. Mantenere intervallo di 2 s,
  UUID, opzioni ro/rw, stato SCSI e rilevamento cambi del device. Gestire escape
  dei mount, bind e rimozione/riapparizione: niente cache permanente del device.
- **Media reconcile:** udev già lo attiva. Ridurre il costo delle passate quando
  tutto è invariato; conservare la riconciliazione di sicurezza. Non ritardare
  rilevamento guasti né disattivare SMB/DLNA, DNS o gli altri servizi usati.
- **API/catalogo:** rendere incrementali conteggi e cataloghi ripetuti, cache
  invalidata dagli eventi reali. Le decisioni di spazio/buffer richiedono dati
  freschi e margini di sicurezza, anche se la dashboard usa una cache breve.
- **Piattaforma:** confrontare consumi a riposo di Docker/Coolify/Redis/log e timer.
  Correggere retry storm, log ripetitivi e job duplicati solo se osservati; non
  rimuovere Coolify o strumenti di accesso per ottenere numeri più bassi.
  Wi-Fi spento, gzip escluso dai video e pause refresh delle schede nascoste
  sono già presenti. Non proporre riavvii, prune o spostare Docker sul NVMe.

**Accettazione:** misurare byte effettivi scritti e CPU a parità di campioni e
richieste; mostrare riduzione rispetto alla baseline, storico equivalente e
tempi di rilevamento invariati. Migrazione reversibile senza perdere i campioni
prodotti dopo il passaggio: prevedere export compatibile per il rollback.

## 4. Storage e overclock: esperimenti condizionati

### Bridge NVMe / UAS

Il flag `u` disabilita UAS: è confermato dal kernel e dalla sua
[documentazione](https://www.kernel.org/doc/html/latest/admin-guide/kernel-parameters.html).
Il motivo locale non è stato trovato nella cronologia Git esaminata. Cercarlo
nelle configurazioni/commenti host e nei precedenti verbali, senza leggere segreti.
Non attribuire le disconnessioni del journal a UAS o al cavo senza correlazione.

Prima misurare throughput e latenza con registrazioni + archivio sul bridge
attuale. Solo con alimentazione affidabile, recovery accessibile e finestra di
manutenzione autorizzata, confrontare UAS sul **solo bridge identificato**.
Non rimuovere tutte le quirks; non effettuare unbind/rebind con mount attivi.
Backup della configurazione, arresto pulito dei lavori coinvolti, riattivazione
tramite UUID; rollback immediato per reset USB/errori I/O/hash diversi o boot fallito.
Non abilitare anche TRIM, cambiare firmware o comprare hardware nello stesso test.
UAS potrebbe aiutare la contesa; il guadagno sul montaggio sequenziale è ignoto.

I pesi I/O già configurati non provano una priorità efficace: i dispositivi usano
`mq-deadline` e nel campione `io.cost.qos` era vuoto. Verificare il supporto reale
prima di scegliere controlli; non cambiare scheduler alla cieca. I limiti BPS e
i pesi hanno meccanismi differenti.
[Cgroup I/O Linux](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#io).

### Overclock CPU

**Oggi: non procedere.** L'handoff vieta aumenti nella configurazione attuale;
questo piano non revoca il vincolo. Prima eliminare/correlare l'undervoltage,
conoscere alimentatore e carichi, verificare raffreddamento e recupero fisico
della eMMC se il nodo non avvia. Un backup accessibile solo via SSH non basta.
L'offset CPU negativo non è la stessa cosa dell'undervoltage dell'alimentazione.

Il valore standard CM4 è 1,5 GHz; i margini di altri Raspberry Pi non sono una
garanzia per questa scheda. Frequenze effettive e protezioni dipendono anche da
temperatura e tensione; misurare con `vcgencmd measure_clock arm`.
[Documentazione Raspberry Pi](https://www.raspberrypi.com/documentation/computers/config_txt.html#overclocking-options).

Se, dopo le ottimizzazioni software, il lavoro è limitato dalla CPU e l'utente
autorizza la finestra con reboot: creare un profilo sperimentale separato,
partire dal riferimento stabile documentato, cambiare un solo parametro per
volta e avanzare per piccoli passi. Nessun valore di frequenza/voltaggio è
preapprovato qui. Non aumentare insieme frequenza e undervolt; non forzare turbo
continuo, non alzare limiti termici e non modificare GPU/RAM per il remux.

Confrontare gli stessi job completi, capture concorrenti, watt/Wh e temperatura
stabilizzata. Interrompere per nuovi eventi di alimentazione/throttling, errori
di calcolo/storage o peggioramento dei consumi richiesti. I flag storici restano
latched: distinguere baseline, bit attivi e nuovi eventi journal. Un test breve
non certifica affidabilità continuativa; lasciare l'OC disabilitato se il beneficio
non è chiaro. Annotare configurazione precisa e procedura di ripristino locale.

Esempio puramente teorico: da 1,5 a 1,8 GHz è +20% di frequenza, al massimo
circa -16,7% di tempo per lavoro interamente CPU. Se solo il 20% del tempo scala
con la CPU, il vantaggio teorico totale è circa 3,3% sul tempo. Non è una previsione
per questo nodo e non dimostra risparmio energetico.

## 5. Evoluzione facoltativa, soltanto se serve ancora

Separare la sessione logica dal file scaricabile: riprodurre i frammenti già
chiusi tramite una sequenza/manifest e materializzare l'MP4 finale in background.
Può anticipare il playback ma non azzera il costo del file finale o dell'upload.
Richiede verifica di seek, cambi codec, audio, discontinuità, autenticazione,
Range, Safari/mobile e retention dei frammenti. Conservare download unico e
cloud esistenti. È una modifica di architettura: non inserirla nella prima patch.

L'accelerazione hardware non serve al percorso `-c copy`. Il Media Hub ha già
Direct Play, remux e alcuni fallback V4L2: non duplicarli in LiveVault senza
profilazione. Un encoder hardware può cambiare la qualità/compressione; non è
una sostituzione automaticamente equivalente di x264 CRF 18. Considerare
offload su un altro computer solo dopo aver misurato costo di rete, disponibilità
e Wh complessivi, mai caricando media privati su servizi non autorizzati.

## Consegne, verifiche e rollout

Ordine suggerito dei commit: **misure → ricevute/fast path → code/anteprime →
telemetria → watchdog/catalogo**. Tenere `moov_size` separato e subordinato al
benchmark. UAS/OC non devono bloccare le ottimizzazioni software sicure.

Per ogni cambiamento usare i test pertinenti già presenti e aggiungere soltanto
quelli che intercettano nuovi rischi. Riferimenti: `test_media_integrity.py`,
`test_v285_av_integrity.py`, `test_av_repair_integration.py`,
`test_v288_session_stitching.py`, `test_size_policy.py`, `test_uploaders.py`,
`test_storage_handoff.py`, `test_storage_tiering.py`, `test_telemetry_downtime.py`.

Prove essenziali del nuovo percorso: file identico e receipt riusata; modifica di
byte a dimensione/mtime invariati rifiutata; receipt assente/vecchia; repair;
limite fisico dopo mux; crash/retry nelle transizioni; anteprima in ritardo durante
upload; quiesce durante i nuovi lavori. Usare fixture brevi per guasti/corruzione,
il campione grande solo per throughput/limite. Non azionare fisicamente lo storage
per ogni unit test. CI Linux/Python 3.13 obbligatoria per il codice; QA visivo solo
se cambia la UI. Nessuna suite ridondante per il solo presente piano.

Prima del deploy: CI verde sul commit preciso, backup coerente DB e configurazioni,
rollback di immagine e migrazioni, documenti operativi aggiornati nello stesso
commit. Il deploy LiveVault può interrompere capture: usare la finestra prevista
da `HOSTING.md`, senza riavviare Docker o gli altri progetti.

Dopo il deploy verificare una volta il percorso completo NVMe → buffer → NVMe,
incluse capture che crescono, SHA-256 delle copie e assenza di worker residui.
Il precedente ciclo live è documentato in `AI-HANDOFF.md`; serve da riferimento,
non prova le nuove modifiche. Distinguere espulsione software dal distacco fisico.
Conservare i rollback privati esistenti; creare quelli del nuovo intervento
separatamente. Aggiornare anche la copia operativa `/opt/openastro-ops`.

Riepilogo finale richiesto: commit/live, secondi e Wh/job confrontabili, byte
letti/scritti e RAM, funzioni/integrità verificate, rollback esatto, limiti residui.
Se una modifica non migliora le misure o introduce regressioni, non promuoverla.

## Prompt da dare alla chat esecutrice

> Esegui il piano in `docs/PIANO-OTTIMIZZAZIONE.md`, partendo da `AGENTS.md` e
> `AI-HANDOFF.md`. Ricontrolla GitHub e runtime: altri lavori possono essere
> avanzati. Implementa le fasi software nell'ordine indicato, con modifiche
> mirate e reversibili, preservando qualità, funzioni, dati e modifiche altrui.
> Misura prima/dopo sugli stessi campioni; non fermarti a un altro piano.
> Mantieni solo interventi con beneficio dimostrato. Fai test mirati e CI,
> aggiorna la documentazione e prepara deploy/rollback concreti secondo le
> procedure vigenti. Non avviare reboot, UAS o overclock senza i prerequisiti
> e la finestra autorizzata descritti; procedi intanto sul software.
> Non promettere accelerazioni non misurate. Concludi brevemente con risultati,
> evidenze e punti aperti.
