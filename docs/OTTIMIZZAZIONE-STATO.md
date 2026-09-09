# Ottimizzazione — checkpoint per ripresa

Aggiornato: 2026-09-08. **Codice applicativo a main e live `56ce014`; verifiche
dei deploy host e confronto prestazioni restano da completare.**
Priorità dal 2026-09-09: ritorno a 1,8 GHz, USB, frammentazione buffer e stitching.
Stato corrente: [STABILITY-20260909.md](STABILITY-20260909.md). Le prove OC
riportate sotto sono storiche e sospese.
Richiesta utente: applicare il piano e documentare ogni passaggio riprendibile.
Piano: [PIANO-OTTIMIZZAZIONE.md](PIANO-OTTIMIZZAZIONE.md).
Accessi/vincoli: [AI-HANDOFF.md](../AI-HANDOFF.md).

## Stato corrente

- Workspace Windows: `C:/Users/gianm/Documents/ChatGPT/ASTROAIR`.
- Ottimizzazioni confluite in `main=56ce014`; CI GitHub `34240792823` verde.
- Branch corrente prove OC/documenti: `codex/cm4-clock-validation` da `56ce014`.
- Runtime LiveVault `56ce014` verificato via Docker prima delle prove OC.
- Checkout host sporco da preservare; non usarlo per deploy o test modificanti.
- Prova OC temporanea autorizzata e in corso: seguire il documento CM4 per boot
  e ripristino pause. UAS invariato. Il prototipo locale precedente alle modifiche
  concorrenti è conservato in stash; non applicarlo sopra `56ce014`.

## Passi e verifiche

| Passo | Stato | Evidenza / prossimo passo |
| --- | --- | --- |
| Stato remoto e documenti | Completato | `main=ab895fd`, creato branch dedicato e questo checkpoint |
| Baseline e misure ripetibili | In corso | Fixture/test isolati attivi su Python 3.13; benchmark host comparabile ancora da eseguire |
| Ricevute integrità / fast path | Implementato, testato | Receipt JSON versionata legata a SHA-256/dimensione/mode; hash completo pre-upload resta obbligatorio; scan media riusata solo su match; hash coopera con quiesce. Incluso split oversized |
| Anteprime / code / cancellazione | Implementato, testato | Video indicizzato prima dello storyboard; coda SQLite persistente pending/processing/ready/failed con retry/recovery; stato visibile UI; FFmpeg thumbnail coopera con quiesce; auto/manual delete bloccate mentre il file serve alla preview; split oversized allineato |
| Telemetria incrementale | Implementato, testato | SQLite/WAL a tier 10 s/24 h, 5 min/7 g, 30 min/90 g; batch 6 campioni/minuto; import JSON una tantum senza cancellarlo; export JSON compatibile per rollback; API/cadenze invariate. Baseline live history.json 1.634.479 B |
| Watchdog / catalogo | Implementato, testato | Watchdog 2 s fork-free via `/proc/1/mountinfo` + sysfs; reconcile USB 15 s salta passate invarianti; cache breve sonde Media Center. 43 test mirati verdi |
| CI e confronto prestazioni | Locale completo verde | Python 3.13: 318/318; Node frontend 6/6; compile JS/Python + shell lint verdi. Reconcile vecchio steady avg 0,267 s vs nuovo warm avg 0,195 s sul nodo; watchdog vecchio 0,004299 s CPU cgroup/20,041 s. CI GitHub/benchmark post-deploy da verificare |
| Deploy / prova live / documenti host | Applicazione live verificata | LiveVault `56ce014`; verificare separatamente helper/pannello e prova storage finale |
| UAS / overclock | OC richiesto, in corso | Baseline 2 GHz passata (180 s, max 76,445 C), prova temporanea 2,1 GHz; UAS invariato. Vedere checkpoint CM4 |

## Come riprendere

1. Leggere questa pagina e `git status`, confrontare branch/remoto/runtime.
2. Conservare modifiche non committate: potrebbero essere il passo in lavorazione.
3. Riprendere la prima riga non completata; non ripetere test già documentati
   senza cambiamenti o dubbi nuovi. Verificare eventuali job CI/host ancora attivi.
4. Aggiornare questo file dopo ogni implementazione, prova e deploy, distinguendo
   codice locale, commit validato e runtime. Tenere checkpoint Git sul branch.

## Condizione host rilevata durante la validazione

Le note seguenti descrivono un avvio precedente alle prove OC. Nel nuovo avvio
a 2 GHz il nodo era in modalità NVMe e senza errori kernel osservati; non è
stata verificata la procedura di fsck eseguita nel frattempo. Non lanciare fsck
o cambiare mount sulla base del solo stato storico: ricontrollare host/namespace.

- Alle 15:31 (timezone host Europe/London) il SERVER NVMe ha registrato reset USB e I/O error reali; ext4 `sdc2` è entrato in `emergency_ro`. Il watchdog live ha effettuato failover automatico al buffer eMMC come progettato.
- Tre recorder hanno continuato sul buffer; al controllo il loop da 3,9 GiB era al 39% (1,5 GiB usati). Il mount host del SERVER è stato smontato per fsck, ma `e2fsck` ha rifiutato correttamente perché il namespace privato di `gpt-harness.service` mantiene ancora il device. Nessuna riparazione filesystem è stata eseguita finora.
- Prima di deploy/merge runtime: completare fsck offline tramite unità host indipendente che ferma temporaneamente Harness, rimonta solo se pulito, quindi usare `nvme-handoff.py attach` per quiesce, copia SHA-256 del buffer e ritorno a NVMe.

## Rollback e lavori attivi

- Runtime LiveVault verificato a `56ce014`. Migrazioni SQLite additive `recordings.validation_receipt` e stato coda thumbnail (`thumbnail_status`, attempts/error/next attempt); rollback applicativo al commit precedente ignora le colonne senza perdita dei dati esistenti. Deploy dei componenti host ancora da confrontare con i sorgenti.
- Rollback preesistenti: `/var/backups/openastro/20260908-maintenance` (preservare).
- Test locali media dopo la coda anteprime: 73 passed su Python 3.13.5. Telemetria SQLite: 37 test Control/telemetry verdi, inclusi import legacy non distruttivo, tier retention, persistenza senza riscrivere JSON ed export rollback. Nessun job/deploy host ancora.
