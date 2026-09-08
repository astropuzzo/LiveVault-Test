# Ottimizzazione — checkpoint per ripresa

Aggiornato: 2026-09-08. **In corso; prima ottimizzazione implementata e testata sul branch, non ancora distribuita.**
Richiesta utente: applicare il piano e documentare ogni passaggio riprendibile.
Piano: [PIANO-OTTIMIZZAZIONE.md](PIANO-OTTIMIZZAZIONE.md).
Accessi/vincoli: [AI-HANDOFF.md](../AI-HANDOFF.md).

## Stato corrente

- Workspace Windows: `C:/Users/gianm/Documents/ChatGPT/ASTROAIR`.
- Branch: `codex/video-efficiency`, base GitHub `ab895fd` verificata.
- Runtime precedente atteso: LiveVault `ba26e23`; ricontrollo host in corso.
- Checkout host sporco da preservare; non usarlo per deploy o test modificanti.
- Nessun reboot, cambio UAS, overclock o modifica ai servizi eseguito.

## Passi e verifiche

| Passo | Stato | Evidenza / prossimo passo |
| --- | --- | --- |
| Stato remoto e documenti | Completato | `main=ab895fd`, creato branch dedicato e questo checkpoint |
| Baseline e misure ripetibili | In corso | Fixture/test isolati attivi su Python 3.13; benchmark host comparabile ancora da eseguire |
| Ricevute integrità / fast path | Implementato, testato | Receipt JSON versionata legata a SHA-256/dimensione/mode; hash completo pre-upload resta obbligatorio; scan media riusata solo su match; hash coopera con quiesce. 30 test mirati verdi |
| Anteprime / code / cancellazione | Da fare | Preservare ordine upload, recovery e file richiesti dalle anteprime |
| Telemetria incrementale | Da fare | Migrazione JSON reversibile, frequenze/retention invariate |
| Watchdog / catalogo | Da fare | Stessi controlli UUID/namespace e intervallo di rilevamento |
| CI e confronto prestazioni | Da fare | Linux/Python 3.13; confronti sul medesimo workload |
| Deploy / prova live / documenti host | Da fare | Backup, commit CI verde, verifica capture e ciclo storage |
| UAS / overclock | Non abilitati | Restano condizionati ai prerequisiti del piano |

## Come riprendere

1. Leggere questa pagina e `git status`, confrontare branch/remoto/runtime.
2. Conservare modifiche non committate: potrebbero essere il passo in lavorazione.
3. Riprendere la prima riga non completata; non ripetere test già documentati
   senza cambiamenti o dubbi nuovi. Verificare eventuali job CI/host ancora attivi.
4. Aggiornare questo file dopo ogni implementazione, prova e deploy, distinguendo
   codice locale, commit validato e runtime. Tenere checkpoint Git sul branch.

## Rollback e lavori attivi

- Runtime ancora invariato. Branch aggiunge migrazione SQLite additiva `recordings.validation_receipt` con default vuoto; rollback applicativo al commit precedente ignora la colonna senza perdita dei dati esistenti.
- Rollback preesistenti: `/var/backups/openastro/20260908-maintenance` (preservare).
- Test locali branch: `tests/test_media_validation_receipts.py`, `test_media_integrity.py`, `test_storage_handoff.py` -> 30 passed su Python 3.13.5. Nessun job/deploy host ancora.
