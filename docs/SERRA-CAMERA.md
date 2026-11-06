# SERRA: camera locale e timelapse nativo

Verifica: 2026-11-06. Camera Xiaomi MJSXJ03HL convertita a Thingino ufficiale
`firmware-2026-11-02`, T31N/JXQ03P/RTL8189FTV. Backup integrale originale verificato e conservato
privatamente nel workspace dell'utente, fuori da Git. Risoluzione 2304×1296:
nessun ridimensionamento negli MP4.

## Runtime e utilizzo

Pagina autenticata `#serra`, anche nel menu mobile. Foto e video mantengono
2304×1296 pixel; a 90°/270° diventano 1296×2304 senza ridimensionamento.
Il clic sulla diretta apre una foto nativa. Diretta e lettore si scollegano
fuori viewport, a scheda nascosta, cambiando sezione o uscendo dalla sessione.
Queste sospensioni non fermano il raccoglitore indipendente delle foto.

Il lettore MP4 è unico per PC e telefono: l'utente ha confermato la riproduzione
sia su Firefox Android sia su PC dopo la correzione v9. La conferma ripetuta della
sessione durante il polling non deve ricrearlo. Prima del Play si preparano otto
secondi di buffer, sei volte la velocità scelta se maggiore (massimo trenta),
oppure l'intero resto del filmato se più breve. “Preparazione video” mostra il
progresso; Play anticipato aspetta il buffer e gli stalli riprendono da soli.
Una connessione più lenta del video nativo può richiedere attese più lunghe.

`/api/serra/preview-info` restituisce un MP4 indicizzato con primo/ultimo blocco,
rotazione, livello di archivio e revisione. `mp4_view.py` costruisce solo gli
indici in memoria, fino a 4 MiB; i byte video restano nei file originali.
Content-Length, durata iniziale completa, Range 206 e timestamp continui sono
verificati. Una revisione modificata o non più conservata restituisce 410,
richiedendo la ricarica, senza mescolare risposte byte di filmati diversi.
L'anteprima non ricodifica e non crea una copia video o Blob. Il download unisce
con `-c copy`, senza una seconda copia persistente. L'aggiornamento conserva la
posizione; il video disponibile si ripete alla fine.

### Acquisizione autonoma sulla microSD

Dal 2026-11-06 la camera salva prima i JPEG nativi sulla propria microSD, nella
sola cartella `serra-spool-v1`. Il pannello resta sul server e l'archivio MP4 sul
NVMe. Non è stata formattata la scheda e i file di installazione/recupero sono
preservati. La scheda verificata è da 32 GB, exFAT; il buffer ha quota separata
8 GiB e lascia almeno 128 MiB liberi. Una cartella con identità dedicata e la
verifica del mount impediscono scritture sulla flash interna se manca la SD.

`scripts/serra-camera-spool.sh` viene installato come
`/usr/sbin/serra-camera-spool`, avviato da `S85serra-spool` a priorità Nice 10.
Usa il JPEG hardware, un'immagine temporanea in RAM e sottocartelle per ora;
nessun video viene ricodificato sulla camera. Il vecchio driver exFAT della
camera è stato provato sul dispositivo: nomi con estensioni aggiunte possono
risolvere allo stesso file (`a.jpg.meta` sovrascrive `a.jpg`). Si usano prefissi
distinti per JPEG temporanei e metadati, oltre al controllo delle dimensioni
dopo la pubblicazione. Non sostituirli con suffissi `.part`/`.meta` sulla SD.
Il CGI richiede modo 0755 per essere eseguito da uhttpd; codice e configurazione
privata sul resto della camera restano rispettivamente 0700 e 0600.
Intervallo, pausa e rotazione
sono ricevuti dal pannello, persistiti sulla camera e continuano a valere senza
server o Wi-Fi. Un campione orario indipendente alimenta lo storico 10/14/18.
La rotazione è registrata nel nome sorgente e applicata fisicamente, senza
perdita, all'importazione: gli originali sulla SD mantengono i pixel nativi.

Il server interroga lo spool ogni cinque secondi, fino a 24 file per giro:
`control-panel/serra_spool.py` confronta dimensione e SHA-256 del JPEG, ruota
secondo la preferenza registrata al momento dello scatto, scrive e sincronizza
file/directory NVMe e indice SQLite, poi invia la conferma. Solo tale conferma
permette alla camera di liberare le foto trasferite. Una risposta perduta non
crea duplicati: nomi locali stabili e ricevute transazionali persistono anche
dopo compressione. Ricevute confermate conservate per 24 ore; quelle senza
conferma rimangono fino alla riconciliazione. Non si spostano JPEG sulla eMMC.

Il nuovo CGI `/x/serra-spool` richiede esplicitamente la chiave API esistente;
cookie e IP attendibili non bastano. Configurazione, scatto manuale e conferme
richiedono POST. Nomi, dimensioni e digest sono validati; non può leggere o
rimuovere file fuori dallo spool. Nessuna porta o chiave SSH aggiuntiva.
La chiave API resta nel file privato del server: `sd_spool=true` abilita questo
percorso e `sd_quota_gb=8` stabilisce il buffer camera. Non pubblicare il file.

Il pannello mostra spazio SD, foto in attesa, ultimo scatto locale e ultimo
recupero. Se manca l'NVMe, la raccolta sulla camera continua e l'importazione
aspetta. Quando torna disponibile, le foto arretrate mantengono data, rotazione
e ordine cronologico, anche se arrivano dopo segmenti più recenti. Preview e
Range congelano limite temporale, ID e revisione senza mescolare finestre.
Se la camera è raggiungibile ma la SD non salva, il server può usare il vecchio
salvataggio diretto sull'NVMe. Se manca anche il Wi-Fi non può farlo.

Limiti: a buffer pieno i file non confermati vengono conservati e i nuovi scatti
locali attendono spazio; non vengono sovrascritti in silenzio. Le modifiche alle
impostazioni fatte mentre la camera è irraggiungibile sono ritentate al ritorno
del collegamento. Il firmware ha soltanto `sync` globale: lo spool raggruppa i
flush entro dieci secondi, oltre al flush delle conferme; una perdita improvvisa
di alimentazione può perdere le scritture non ancora persistite. Intervallo
1 s soggetto ai tempi effettivi di JPEG, SD e trasferimento; non è una garanzia
di frequenza esatta su questa camera. La scrittura continua consuma la SD.

### Fotogrammi aggiornati e infrarossi automatici

Correzione 2026-11-04 sera: il JPEG della camera era rimasto realmente fermo
alle 18:26:04, anche via IPC locale; il collector stava salvando copie con date
nuove. Riavviato solo Prudynt e impostato `stream2.jpeg_idle_fps=1` in
`/etc/prudynt.user.json`, con backup privato `serra-jpeg-idle-*`. Mantiene il
JPEG hardware a un fotogramma/secondo sulla camera, evitando il ciclo SDK
stop/start che precedeva il blocco; nessuno streaming continuo verso il server.
Scatti reali con orari successivi sono stati verificati nei pixel, non solo
nei timestamp del database. Le immagini perse durante il blocco non si recuperano.

Il collector confronta l'hash JPEG: con l'orologio impresso attivo, byte identici
per oltre due intervalli (minimo 15 s) generano errore e non vengono archiviati
come nuovi scatti. Il controllo locale `scripts/serra-camera-health.sh`, installato
sulla camera come `/usr/sbin/serra-camera-health`, gira da cron ogni minuto.
Usa snapshot IPC, timeout, lock e stato in RAM; dopo due controlli falliti o
identici riavvia solo Prudynt, al massimo una volta ogni 15 minuti. Non apre
porte, non contiene chiavi e non riavvia l'host. Richiede timestamp impresso;
non disabilitarlo senza adattare entrambi i controlli di freschezza.

Il sensore restava a luminosità 26 con soglia notte 25. Calibrata la soglia
notte a 35, giorno invariato a 50, mantenendo `enabled=true`, `force_mode=""`
e orario forzato disabilitato. Ricarica SIGHUP di daynightd; passaggio automatico
DAY→NIGHT alle 22:07:18, ISP Night, GPIO illuminatore attivo e JPEG notturno
leggibile in bianco e nero verificati. Le due soglie e i conteggi mantengono
l'isteresi; il ritorno alla luce naturale sarà verificabile al mattino.
Configurazione e logica nel [sorgente ufficiale Thingino](https://github.com/themactep/thingino-firmware/blob/master/package/thingino-daynightd/files/daynightd.c).

Rollback camera: rimuovere solo la riga cron di questo controllo e ripristinare
la configurazione dal rispettivo backup `serra-health-*`, `serra-daynight-*` o
`serra-jpeg-idle-*`; HUP per daynightd, restart del solo Prudynt se cambia il suo
JSON. Per il backend ripristinare il modulo dal backup `serra-recovery-*` e
riavviare solo Control/collector; conservare archivio, indice e impostazioni.

### Rotazione salvata negli scatti

Il selettore 0°/90°/180°/270° agisce sui pixel dei nuovi JPEG, tramite
`jpegtran -perfect -rotate`: trasformazione senza perdita, ritaglio o resize.
È necessaria la dipendenza Debian `libjpeg-turbo-progs` (anche in CI).
A 0° i byte originali restano identici. La rotazione scelta viene salvata nelle
impostazioni e nell'indice; un commento JPEG consente il recupero del metadato
anche se manca la riga SQLite dopo un'interruzione. Il commento non è un tag
EXIF di visualizzazione: i pixel sono già orientati. Il server ruota gli scatti,
mentre la sola diretta usa la trasformazione equivalente nel browser, senza
avviare una ricodifica per ogni fotogramma MJPEG.

Ogni blocco MP4 contiene un solo orientamento. “Riprese” permette di consultare
quelli precedenti, che non vengono cancellati né ruotati retroattivamente.
Il JPEG originale della camera usa multipli di 16; se una trasformazione non
può essere perfetta, lo scatto non viene pubblicato tagliato o degradato.

### Dettaglio recente e storico fino a un anno

Il limite totale di archivio resta unico: 1–16 GiB, predefinito 4 GiB. Comprende
foto pendenti di entrambi i livelli, MP4 e spazio temporaneo dell'encoder.
Impostazioni di acquisizione già salvate vengono preservate; da 1 secondo a
un'ora, con pausa/ripresa. Il dettaglio recente viene codificato con x264
`veryfast`, CRF 28, un thread, 24 fps: 120 scatti o cinque minuti, massimo 300;
il primo blocco di ogni orientamento arriva dopo almeno due scatti.

| Periodo richiesto | Livello e campioni conservati |
| --- | --- |
| 1/6/12/24/48/72 ore | Dettaglio recente, tutti gli scatti dell'intervallo scelto finché rimangono nello spazio disponibile |
| 5/7/15/30 giorni | Storico, una foto ogni ora |
| 3/6 mesi o 1 anno | Solo foto delle 10:00, 14:00 e 18:00 Europe/Rome, anche per i giorni più recenti |

Il servizio prende il campione orario anche se l'intervallo recente supera
l'ora e riusa lo stesso JPEG quando l'acquisizione coincide. Se si mette in pausa
la raccolta, si sospendono entrambi. Non vengono inventati periodi precedenti
alla raccolta o foto di ore in cui la camera era spenta. Per un giorno privo
degli orari richiesti si conserva un solo campione realmente disponibile,
il più vicino alle 14, invece di inventare o duplicare immagini. Quel campione
fuori dagli orari richiesti resta escluso dalla vista 3/6 mesi e annuale.

Lo storico viene codificato per giornata, fino a 24 campioni per blocco,
con fotogrammi indipendenti. Dopo 30 giorni un filtro sui pacchetti conserva
solo i campioni richiesti e corregge i timestamp con copia codec: niente seconda
ricodifica, nessuna perdita aggiuntiva e stessi pixel nativi. La manutenzione
lavora su un giorno alla volta, al massimo una volta al minuto; scade lo storico
oltre 365 giorni. Un anno completo corrisponde a circa 1.725 campioni invece
di tutti gli scatti frequenti. Ogni campione orario viene compresso e pubblicato
appena lo slot encoder è libero, anche se è il solo scatto del giorno. Ha
priorità sul prossimo lotto recente; non aspetta 24 foto o il cambio giornata.
Le finestre da 5 a 30 giorni possono usare il dettaglio recente finché manca
lo storico. Le viste da 3 mesi a un anno non hanno quel ripiego: attendono un
campione degli orari richiesti, senza mostrare per errore filmati notturni.

Correzione verificata 2026-11-05: la vista annuale mostrava soltanto i due
campioni delle 23:00/00:00 già compressi; quelli delle 10:00 e 14:00 erano ancora
JPEG pendenti. La raccolta degli orari funzionava, la selezione e la pubblicazione
non rispettavano la vista richiesta. Ora il filmato annuale sceglie al massimo
un campione per data e ora locale 10/14/18 fin dal primo giorno. Anteprima e
download usano la stessa selezione; il pannello mostra il numero effettivo di
foto diurne. Nessun orario mancante viene sostituito con una foto notturna.

Il filtro opera solo sugli indici del MP4: `mp4_view.build(..., selections=...)`
mappa i pacchetti I-frame originali, con nuovo indice durata/seek/Range, senza
ricodifica, resize o copia persistente. I pacchetti non selezionati restano
nell'archivio per le viste orarie. La revisione e la cache includono gli indici
dei campioni; gli URL annuali indicano `sampling=daylight`. Il vecchio endpoint
HLS non serve selezioni parziali: restituisce un errore esplicito per queste
viste; il player usa già solo MP4. Nessuna migrazione o eliminazione retroattiva.

Quando serve spazio vengono eliminati prima i dettagli recenti più vecchi,
proteggendo i campioni diradati. Non è garantita una durata precisa del dettaglio
ad alta frequenza per qualsiasi scena: restano determinanti qualità, intervallo,
orientamenti usati e quota. Il servizio ha Nice 15 e CPUQuota 75% di **un** core.
Il browser non deve restare aperto per raccolta o manutenzione.

### Pulizia manuale con anteprima

Preset “più vecchi di” 24/48/72 ore, 7/30 giorni oppure intervallo locale da/a.
Una data finale futura (per esempio fine giornata) viene limitata all'istante
del calcolo; la selezione firmata conserva quell'istante anche alla conferma.
Progresso, errori e selezione vuota sono mostrati accanto al pulsante.
“Calcola pulizia” mostra fotogrammi e spazio; nessun file viene rimosso prima
della conferma esplicita. Lo storico annuale è escluso salvo spuntare l'opzione
apposita. La selezione comprende tutti gli orientamenti del livello scelto.
Sono protetti scatti già assegnati all'encoder, export in corso, altri media e
riprese che attraversano i limiti del periodo: un file MP4 viene rimosso solo
quando tutto il suo intervallo è compreso nella selezione.

Il backend firma la selezione con HMAC e scadenza di cinque minuti. Cambiamenti
nell'archivio tra anteprima e conferma richiedono un nuovo calcolo, prima di
qualsiasi rimozione. Restano obbligatori sessione e CSRF. Le prove distruttive
si eseguono soltanto nell'archivio temporaneo di test, mai sui filmati reali.

### Diagnostica su richiesta

Solo `?serraDebug=1` abilita gli otto piccoli rapporti per pagina, su PC o mobile,
con player visibile e sessione CSRF valida. Buffer, posizione, errori, eventi e
contatori di decodifica restano nel file privato 0600
`/var/lib/openastro-control/serra/playback-diagnostics.json`: massimo sedici
rapporti da 4 KiB, senza foto, credenziali o URL. La visualizzazione normale
non invia questi rapporti. Nessuna modifica di acquisizione è causata dal lettore.

Sorgente `control-panel/serra_camera.py`, runtime `/opt/openastro-control/serra_camera.py`.
Servizio `openastro-serra.service`; indice e impostazioni in
`/var/lib/openastro-control/serra`. Credenziali API private, mai esposte alla UI,
in `/var/lib/openastro-control/serra-camera.json` (0600). Il servizio Control
carica anch'esso `/etc/openastro-serra.env` (o un drop-in equivalente), mantenendo
`ProtectHome=true`. La chiave API della
camera è dedicata; niente cloud o applicazioni Xiaomi. Nessuna password qui.

## Storage e recupero

Media del server solo nel mount configurato, sottocartelle `serra/{pending,segments}`.
Lo spool sorgente sulla camera è separato e prosegue anche senza questo mount.
Configurare `OPENASTRO_SERRA_MOUNT` e `OPENASTRO_SERRA_UUID` nel file locale
`/etc/openastro-serra.env`, che non va pubblicato. UUID assente, mount assente o UUID diverso:
nessuna scrittura sulla memoria di sistema. Sotto 2 GiB liberi l'importazione
attende, mentre la camera continua sulla SD finché c'è spazio, senza cancellare
dati esterni. La diretta resta indipendente dall'NVMe.

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

39 test Linux Python 3.13.5: migrazione dell’indice senza perdita di righe,
rotazioni JPEG con controllo dei pixel nei quattro quadranti e dimensioni native,
MP4 verticali separati, campioni orari e copia esatta dei pacchetti delle
10/14/18 nello storico vecchio, concatenazione nativa continua, pulizia firmata,
quota/ordine di eliminazione, protezione input attivi, rifiuto di JPEG fermi,
limite futuro della pulizia congelato senza cancellazioni durante il calcolo,
lock di export, ripresa dopo interruzione, finestre e playlist senza segreti,
vero encode/decode FFmpeg 2304×1296 con conteggio fotogrammi identico.
La selezione annuale verifica fuso/DST, ore mancanti, singoli scatti pubblicati,
packet hash identici per i soli tre orari, Range/seek e download uguale al preview.
Gli originali rimangono byte per byte invariati.
Le prove spool usano camera/SD isolate: perdita conferma, interruzione prima del
commit, recupero senza duplicati dopo compressione, SHA errato non confermato,
SD assente/piena, scatto autonomo, POST obbligatorio e rifiuto dei percorsi esterni.
Il recupero di blocchi fuori ordine verifica selezione temporale e indice MP4.
Dieci regressioni frontend verificano il player desktop/mobile, rotazione,
selezione anno/orientamenti, esclusione dei filmati notturni, conteggio foto diurne,
conferma della pulizia, errori visibili e selezione vuota. Controlli sintattici JavaScript. CI del branch richiesta prima della
fusione; prove live e limiti operativi vanno riportati separatamente dai test.

Distribuzione mirata: backup dei file Control modificati e dell'helper storage in
`/var/backups/openastro/serra-20261104-*`; conservare il checkout host sporco.
Ripristinare quei file e fermare/disabilitare `openastro-serra.service`, quindi
riavviare soltanto `openastro-control.service`. Archivio e backup firmware si
conservano. Non riavviare Docker, LiveVault o l'intero server per questo rollback.
Per annullare soltanto la correzione del polling v6, ripristinare `serra.js`,
il riferimento versione in `index.html` e il documento dal backup mirato
`serra-session-*`: nessun riavvio necessario, quindi ricaricare la pagina.
Per annullare soltanto il buffer v8, ripristinare i tre asset SERRA e i riferimenti
in `index.html` dal backup `serra-buffer-*`, senza riavviare servizi.


## Sicurezza della camera

Verifica 2026-11-04: SSH accetta solo chiave pubblica (`dropbear -s`), provato
anche il rifiuto del metodo password. IPv6 disabilitato in runtime e in
`/etc/sysctl.conf`, perché l'integrazione usa solo la LAN IPv4. La camera non
aveva un firewall locale installato; non confondere l'assenza di un indirizzo
IPv6 con un audit completo del router. Il resto della LAN non è stato modificato.
Foto dirette senza chiave e RTSP senza credenziali restituiscono 401; la home
web pubblica nella LAN non implica accesso anonimo alle immagini. Il pannello
richiede sessione anche per la camera ed espone HTTPS, cookie Secure/HttpOnly/
SameSite e protezione dei tentativi di login. La chiave API resta solo sul server.
Password casuali forti sono state impostate per root, HTTP e RTSP: il vecchio
default RTSP restituisce 401, la nuova password 200 e gli snapshot del pannello
continuano a funzionare. Il firmware conserva gli override in
`/etc/prudynt.user.json` (0600); leggere il solo JSON di base non descrive le
credenziali realmente attive. La copia per l’utente è privata, fuori da Git.
La lettura UPnP del gateway non ha elencato mappature verso la camera. Porte
manuali, DMZ e firewall WAN del router non sono stati verificati tramite una
sessione amministrativa: non si dichiara esclusa ogni esposizione pubblica.
Backup privato della configurazione camera in `/root/serra-security-*` e fuori
Git nel workspace. Per rollback SSH ripristinare `S30dropbear` e riavviare solo
Dropbear; per IPv6 ripristinare sysctl e applicare le impostazioni volute.
Per annullare soltanto il player desktop v9 ripristinare JS, riferimento versione
e documento dal backup `serra-desktop-*`, poi ricaricare: nessun servizio da riavviare.


## Migrazione e rollback delle funzioni v10

Distribuire solo il modulo Serra, i suoi JS/CSS, la sezione SERRA dell'HTML e
questo documento, preservando le altre sezioni runtime e il checkout host.
Prima della migrazione effettuare un backup SQLite consistente con il collector
fermo, oltre ai file e alle impostazioni. Le colonne nuove sono additive:
rotazione, livello e tempi dei campioni; le righe precedenti diventano dettaglio
recente a 0°. Non serve cancellare o ricodificare lo storico precedente.
Riavviare solo Control e il collector per caricare il nuovo backend; non Docker,
LiveVault o l'host. Le impostazioni utente e l'UUID privato non vanno sostituiti.

Per annullare solo l'interfaccia ripristinare gli asset dal backup
`serra-features-*`, lasciando il collector nuovo per gestire correttamente i due
livelli. Per un rollback completo fermare il collector e conservare tutti i
media e il DB nuovo: non rimettere in esecuzione il vecchio raccoglitore su JPEG
misti o ripristinare una copia DB che perda acquisizioni successive. Riconciliare
prima i nuovi file e i metadati con la versione da ripristinare.

## Rollback della vista diurna v12

Backup mirato `serra-daylight-*` prima di distribuire insieme `serra_camera.py`,
`mp4_view.py`, `serra.js` e il suo riferimento versione. Riavviare solo Control
e collector. Per annullare ripristinare questi file dallo stesso backup e
riavviare solo i due servizi; database, media e impostazioni restano compatibili.
Non ripristinare un vecchio database sopra le foto raccolte successivamente.

## Rollback dell'acquisizione SD v13

Backup camera `serra-sd-*` conserva la configurazione iniziale; il cron
preesistente non viene modificato. Lo spool
è un namespace aggiunto, non una sostituzione del contenuto della scheda.
Per fermare la nuova raccolta usare `S85serra-spool stop`, preservando i JPEG
non confermati. Per tornare alla raccolta diretta disabilitare `sd_spool` nel
file privato del server e riavviare soltanto `openastro-serra.service`.
Ripristinare moduli/asset dal backup server `serra-sd-*` se necessario e
riavviare solo Control/collector. Non ripristinare vecchi DB sopra le foto nuove;
la tabella aggiunta delle ricevute è compatibile con il percorso precedente.
Prima di rimuovere lo spool importare o copiare ogni foto non confermata.
