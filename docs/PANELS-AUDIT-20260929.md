# Audit pannelli ASIAIR — 2026-09-29/30

Baseline verificata: GitHub `main=5fddda9`; LiveVault 3.4.30, immagine
`ahul2vdjkyvjiwgzpcrmxzfe:0242aa3c8e5ee95d6e2a0cdfb61e003de28f1028`;
NINA immagine `ctrzdfqqsdljdcb2sbdrc7ug:900ccd44f86e003877ab08b1893de565748fdfe5`.
Release 3.4.31 distribuita il 30 settembre dal commit `fc023fd8908d41fc897c78b73a65f97695ab26e0`;
runtime e prove sono indicati sotto. Worktree isolato; checkout Windows e checkout Harness preservati.

## Verifiche sul nodo

SSH con chiave esistente e namespace host attraverso il root bridge GPT Harness.
Nessuna nuova credenziale, esposizione pubblica o modifica ai mount/profili CPU.
Backup consistente completato con `openastro-action backup_now` prima dei fix.
HTTPS LiveVault, Control e NINA raggiungibili; NINA LAN è associato a
`192.168.1.27:9091`, non a `127.0.0.1:9091`. NINA resta senza mount e con utente
`openastro`. Nessuna unità systemd fallita.

Campioni a registrazione attiva: temperatura 54–55,5 °C, massimo CPU 1800 MHz,
RAM disponibile circa 2,5 GiB; swap storicamente occupata, senza swap-in/out nei
brevi campioni `vmstat`. Firmware `0x50000`: eventi storici, nessun throttling
attuale. Journal kernel del 29 settembre senza warning. Queste osservazioni non
certificano alimentatore, cavo USB o stabilità sotto tutti i carichi. NVMe e SHARE
montati con gli UUID documentati; nessun eject, reboot, restart Docker o prune.

Control installato non coincideva interamente con GitHub. Hash LF normalizzati
identificano `server.py` alla revisione `5dafe0b` e `static/app.js` a `68fd33c`;
gli altri sorgenti/statici confrontati coincidono con la baseline. Nessuna copia
ricorsiva del runtime verso il PC: il confronto usa solo hash di file noti e
cronologia Git. La copia di rollback resta privata sul nodo.

## Problemi e comportamento corretto

| Area | Difetto verificato | Correzione |
| --- | --- | --- |
| LiveVault player | Risposte tardive riaprono/sostituiscono video o profili chiusi/cambiati | Identità della richiesta e invalidazione alla chiusura; risultati locali prima dell'assegnazione |
| LiveVault aggiornamenti | Refresh dopo mutazione perso; range statistiche ignorato durante caricamento | Passata successiva coalescente; vince la selezione più recente |
| Stati provider | AWAY normalizzato a TIP-JAR; biocontext pubblico copre un esito AWAY del resolver | AWAY distinto e non registrabile; stati correnti del resolver prevalgono sui metadati pubblici in ritardo |
| Timeline capture | Gruppi/range cambiano alla rotazione; URL dinamico può leggere la parte successiva | URL fissato all'inode verificato sul descrittore; gruppi uniformi, sequenze monotone ed epoche per GOP maggiori; 404 sui byte rimossi |
| File concorrenti | Retry/upload/recupero/cancellazione possono sovrascrivere lo stato di un worker | Claim condizionali e occupazione per registrazione prima delle operazioni sui byte |
| Import Media | Due import possono sostituire lo stesso nome | Rename atomico senza sostituzione, HTTP 409 al concorrente |
| Catalogo Media | Scan limitato cancella voci non visitate e perde il flag dopo la cache | Conserva le voci e rilegge la completezza dall'evento scan esistente |
| Media prestazioni | Discovery ripetuta per miniatura/file, decoder concorrenti, connessioni SQLite aperte | Sonde condivise, cache/decoder serializzato, chiusura del DB |
| Media progressi | Scritture ogni 500 ms e risposte vecchie dopo un preferito | Massimo una scrittura periodica ogni 10 s, salvataggio finale ordinato, refresh dopo mutazioni |
| Media selezione | Hint SMB fisso, risposte tardive legate al nuovo disco | Percorso/copia del supporto selezionato; file vecchi invalidati immediatamente per UUID |
| Transcoding | Health LiveVault assente interpretato come zero capture | Stato ignoto blocca la ricodifica video; direct/remux/audio restano disponibili |
| HLS Media | Job abbandonato resta attivo senza richieste browser | Manutenzione autonoma ogni 30 s, scadenza di inattività esistente di 600 s |
| Control diagnosi | HTTP 503 perde il dettaglio worker/storage | Mantiene il payload diagnostico con `ok=false` |
| NINA cache | Richieste parallele duplicate, cache già scaduta dopo I/O lento | Una richiesta condivisa e TTL misurato dal completamento |
| NINA accesso QSM | Redirect inoltra il token a un altro host | Redirect rifiutati; errori del browser senza URL/configurazioni private |
| NINA immagini | JPEG più recente presentato come frame valutato; preview sintetica ferma gli aggiornamenti reali | Preview LIGHT indipendente, timestamp proprio, refresh 15 s e cache RAM 5 s con ETag |
| NINA inspector | Indici riutilizzati o cambi di snapshot mantengono valutazioni vecchie | Identità timestamp+indice, dettagli aggiornati, recuperi fotometrici visibili |
| NINA polling | Richieste bloccate/logout applicano risposte tardive | Timeout/abort e identità della sessione UI |
| Profili CPU | Testo MAX dichiara 1,5 GHz mentre il limite nodo è 1,8 GHz | Testo riferito al limite configurato, senza cambiare CPU o profili |
| Deploy | Webhook manuale e GitHub App accodano due deploy LiveVault dello stesso commit | Disattivato il solo webhook manuale ridondante, con configurazione conservata |

Procedure specifiche e limiti: [LiveVault](LIVE-PANEL-20260909.md),
[Control](../control-panel/README.md), [NINA](NINA-MONITOR.md).

## Funzioni aggiunte

Control Dashboard include **Da controllare**: spazio, storage/buffer, worker,
servizi, temperatura, RAM, alimentazione e freschezza del campione. Eventi
firmware storici sono informazioni, distinti dai guasti attuali. Le schede aprono
la sezione pertinente; **Esporta diagnosi** salva JSON con soli valori ammessi.
Nessun endpoint/cookie/token, nome file, percorso, configurazione DNS, log azioni
o errore grezzo entra nel report. Fonti `control-panel/static/diagnostics.js`
e `diagnostics.css`; nessuna nuova sonda o richiesta di rete.

NINA mostra l'orario della preview reale e la sua età senza attribuirle le misure
di un frame differente. Nessun cambiamento a soglie QSM, sequenze N.I.N.A.,
modelli NSFW, impostazioni di registrazione/upload o schema persistente.

Segnalazione ivyquinette del 30 settembre: il codice precedente conteneva
`away` nei token TIP-JAR. Al controllo SSH successivo il provider era tornato
`public` e yt-dlp lo rilevava registrabile; quel controllo non ricostruisce lo
stato passato. Test separati coprono `away`, `offline_tipping`, ritorno pubblico
e biocontext pubblico in ritardo. AWAY non crea un errore né avvia capture;
la cronologia precedente non viene riscritta senza evidenza. Riferimento del
resolver: [yt-dlp Chaturbate](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/chaturbate.py).

## Validazione e rollout

Test di regressione con richieste differite, concorrenza HTTP/SQLite e MP4
frammentati sintetici; test FFmpeg e CI Linux/Python 3.13 obbligatori sul commit
preciso prima della promozione. Windows usa un `fcntl` finto fuori da Git solo
per raccogliere i test: non prova locking/mount Linux. QA browser su snapshot
sanitizzati del nodo, eseguito il 29/30 settembre a desktop 1440×1000 e mobile 412×915, Control Dashboard,
Media, Sistema e NINA. I media della libreria e le anteprime non sono trasferiti
al PC per questo QA; la riproduzione reale è una verifica separata sul nodo.
Nessun overflow orizzontale nelle viste provate. QA LiveVault AWAY/TIP-JAR
su fixture esplicita con gli asset reali: badge, stato, legenda e distinzione
visiva corretti a entrambe le larghezze. Le prove non simulano una registrazione
reale né una preview NINA disponibile. Screenshot fuori da Git nel workspace
temporaneo `livevault-audit-data-20260929`.
Verifica locale dell'intero insieme il 30 settembre con FFmpeg 7.1:
512 test Python passati, 11 saltati escludendo cinque file dipendenti da
namespace/mount/Pi-hole Linux, come documentato nell'handoff. Successivo gruppo
scanner/claim/versione: 49 passati, uno saltato. Test Node delle quattro suite
pannelli: 61 passati. Il candidato ha versione 3.4.31 coerente con PWA e documenti.
CI Linux/Python 3.13 sul commit sorgente `fc023fd8908d41fc897c78b73a65f97695ab26e0`:
**582 test Python passati, zero saltati; 61 JavaScript passati**. Compilazione,
lint shell/JS, build/prova container NINA isolato e controllo documentale verdi.
[CI prima della promozione](https://github.com/astropuzzo/LiveVault-Test/actions/runs/36691071952),
[CI main](https://github.com/astropuzzo/LiveVault-Test/actions/runs/36692037849).

Stato del rollout: completato, immagini finali healthy al controllo SSH del 30 settembre.

| Componente | Runtime verificato |
| --- | --- |
| LiveVault | `ahul2vdjkyvjiwgzpcrmxzfe:fc023fd8908d41fc897c78b73a65f97695ab26e0`; container `ahul2vdjkyvjiwgzpcrmxzfe-084838649771`, avvio 08:54:43 UTC |
| NINA | `ctrzdfqqsdljdcb2sbdrc7ug:fc023fd8908d41fc897c78b73a65f97695ab26e0`; container `ctrzdfqqsdljdcb2sbdrc7ug-085252364340`, avvio 08:53:57 UTC; utente `openastro`, zero mount |
| Control | `/opt/openastro-control`, nove file Python/statici con SHA-256 identico al commit; solo `openastro-control.service` riavviato |

Control: backup DB consistente completato prima della sostituzione; runtime precedente
in `/var/backups/openastro/20260930-panels-audit/control-before.tar.gz` (root, 0600),
manifest installato nella stessa directory. Login HTTPS e asset diagnostici HTTP 200,
hash diagnostica identico; `/api/state` senza sessione HTTP 401. Raccolta stato con i
moduli installati riuscita: due dispositivi Media, storage montato, tutti i servizi
attivi. Questa prova non equivale a un nuovo login autenticato al processo Control:
le credenziali e le sessioni dell'utente non sono state sostituite. Export JSON
verificato dai test Node di allowlist; download browser non confermato nel replay.

LiveVault: `/healthz` 3.4.31, nove task vivi, NVMe online, recovery finale idle.
API autenticate `/api/status`, `/api/sources`, registrazioni e pulse HTTP 200.
Singolo campione a runtime dopo il build: lista 703 ms, condizionale 304 in 14 ms;
pulse 12 h 150 ms e 168 h 1443 ms. Sono campioni, senza confronto controllato prima/dopo.
Capture sorgente 25: playlist HTTP 200 in 58 ms, 53 segmenti, target 3 s; range
206 di 16 byte dal file annunciato. Target stabile e sequenza monotona nel secondo
campione. Nessuna crescita osservata nei cinque secondi; il provider è poi passato
`private` e la capture si è fermata. Controllo finale: 17 sorgenti offline, una privata,
nessuna capture attiva o errore worker, nessun upload/integrità fallito, coda vuota.
Non è una prova di crescita o seek continuo su una live Stripchat reale.

NINA: health read-only/isolated HTTP 200; `/api/state` autenticato 200 con
`configured=true`, `reachable=false`; preview 502 con messaggio generico senza URL.
PC/QSM non raggiungibile nel campione finale: JPEG reale/ETag restano da osservare
quando disponibile. Le risposte condizionali sono coperte dalla CI.

Coolify aveva due code LiveVault per lo stesso commit alle 08:48:38 UTC.
La consegna GitHub manuale conferma la coda `vedgbii45mtryhy9lg8rkutl`; la coda
`yrvstczvejpvyzhyplr2swye` e NINA usano il collegamento GitHub App. Il webhook
manuale `674326069` è disattivato senza cancellarlo. Sorgenti App, Auto Deploy,
branch e Watch Paths restano invariati; ripristino in `HOSTING.md`.

Copia dei documenti Git distribuita in `/opt/openastro-ops`; `SOURCE.txt` identifica
la revisione documentale. Originali inclusi nel backup privato
`/var/backups/openastro/20260930-panels-audit/ops-before.tar.gz`.
Il documento OAuth fuori da Git e i checkout locali/host restano preservati.

## Rollback e limiti

LiveVault: immagine 3.4.30 `0242aa3` sopra. NINA: immagine `900ccd44` sopra.
Control: ripristinare i soli nove file del manifest dal backup privato del runtime;
rimuovere i due nuovi asset diagnostici se assenti nel backup e riavviare il solo
servizio. Nessuna migrazione DB; mantenere segreti, dati e immagini precedenti.

La timeline non trattiene copie che stitching/upload rimuovono: i vecchi URL
restituiscono 404 e la finestra avanza. Il riavvio applicativo azzera cache e
sessioni UI Control: ripetere il login. Le prestazioni del nodo dipendono dal
carico; il risparmio verificabile è nelle richieste/decodifiche duplicate, senza
promettere una percentuale globale di CPU. Un audit del codice, dei log e dei
casi riprodotti non dimostra l'assenza di ogni possibile bug o guasto fisico.
