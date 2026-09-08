# Ottimizzazione — checkpoint per ripresa

Aggiornato: 2026-09-08. **In corso; ricevute integrità e coda anteprime implementate/testate sul branch, non ancora distribuite.**
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
| Ricevute integrità / fast path | Implementato, testato | Receipt JSON versionata legata a SHA-256/dimensione/mode; hash completo pre-upload resta obbligatorio; scan media riusata solo su match; hash coopera con quiesce. Incluso split oversized |
| Anteprime / code / cancellazione | Implementato, testato | Video indicizzato prima dello storyboard; coda SQLite persistente pending/processing/ready/failed con retry/recovery; stato visibile UI; FFmpeg thumbnail coopera con quiesce; auto/manual delete bloccate mentre il file serve alla preview; split oversized allineato |
| Telemetria incrementale | Implementato, testato | SQLite/WAL a tier 10 s/24 h, 5 min/7 g, 30 min/90 g; batch 6 campioni/minuto; import JSON una tantum senza cancellarlo; export JSON compatibile per rollback; API/cadenze invariate. Baseline live history.json 1.634.479 B |
| Watchdog / catalogo | Implementato, testato | Watchdog 2 s fork-free via `/proc/1/mountinfo` + sysfs; reconcile USB 15 s salta passate invarianti; cache breve sonde Media Center. 43 test mirati verdi |
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

- Runtime ancora invariato. Branch aggiunge migrazioni SQLite additive `recordings.validation_receipt` e stato coda thumbnail (`thumbnail_status`, attempts/error/next attempt); rollback applicativo al commit precedente ignora le colonne senza perdita dei dati esistenti.
- Rollback preesistenti: `/var/backups/openastro/20260908-maintenance` (preservare).
- Test locali media dopo la coda anteprime: 73 passed su Python 3.13.5. Telemetria SQLite: 37 test Control/telemetry verdi, inclusi import legacy non distruttivo, tier retention, persistenza senza riscrivere JSON ed export rollback. Nessun job/deploy host ancora.
