# CM4 — prove di frequenza, checkpoint

2026-09-08. Richiesta: verificare 2 GHz e provare progressivamente 2,1/2,2 GHz.
L'utente conferma accesso fisico per riaccendere il nodo in caso di blocco.
PTM7950 applicato dall'utente; montaggio termico non ispezionato.

## Stato verificato prima delle prove

- CM4 Rev 1.0, 4 GiB; frequenza effettiva circa 2000 MHz, `schedutil`, minimo
  kernel/firmware 600 MHz. `arm_freq=2000`, nessun `over_voltage*` nel config attivo.
- Nuovo boot: `get_throttled=0x0`, circa 64 C; nessun errore kernel osservato.
- Bootloader 2021-12-02; watchdog runtime già attivo (60 s).
- `tryboot.txt` e `autoboot.txt` assenti; configurazione normale da conservare
  a 2 GHz. Prove superiori tramite `tryboot.txt`, senza sostituire `config.txt`.
- `stress-ng` 0.19.02 installato per la prova, nessun servizio di stress permanente.
- GitHub e LiveVault sono già a `56ce014` (CI verde): il lavoro concorrente ha
  implementato e distribuito il codice applicativo delle ottimizzazioni.
  Il prototipo locale precedente è preservato nello stash con nome
  `checkpoint local receipt prototype before concurrent upstream optimization`.
  Non applicarlo sopra le modifiche più complete del remoto.
- Branch attuale: `codex/cm4-clock-validation`, base `56ce014`.

## Procedura

`scripts/cm4-clock-check.py` esegue stress CPU e 128 MiB RAM con verifica dei
risultati, campioni ogni 2 s e stop a 78 C, nuovi flag di throttling/alimentazione
o timeout. Non modifica frequenze/configurazione, non scrive sui video.
Risultati JSON e log privati su eMMC; annotare i percorsi qui.

1. Prova breve 2 GHz attuali, con servizi attivi, per riferimento.
2. Backup boot/config e DB; preparare boot temporaneo a 2,1 GHz conservando
   tensione adattiva, governor e minimo a 600 MHz. Registrazioni chiuse
   correttamente prima del reboot. Verificare boot e prova breve.
3. Solo se superata, stessa procedura a 2,2 GHz e prova più lunga.
4. Boot ordinario torna ai 2 GHz. Non rendere permanente un profilo che non ha
   passato le prove; documentare separatamente stabilità breve e prolungata.

Il watchdog Linux non garantisce recupero se il boot fallisce prima di avviarlo.
In quel caso serve riaccensione fisica: il flag tryboot è monouso e il boot
seguente usa la configurazione normale.
[Raspberry Pi tryboot](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#fail-safe-os-updates-tryboot).

Non usare i valori del post Pi 4 come profilo CM4. La documentazione non supporta
minimi sotto quello standard e non attribuisce loro risparmi significativi;
resta attiva la gestione dinamica della frequenza. Nessun `over_voltage=15`,
overclock GPU/RAM o `force_turbo`.
[Raspberry Pi frequenze](https://www.raspberrypi.com/documentation/computers/config_txt.html#overclocking-options).

## Risultati e ripresa

- 2 GHz: prova in preparazione.
- 2,1 GHz: non provati.
- 2,2 GHz: non provati.
- Backup e job: da registrare prima del reboot.
- Ottimizzazioni: riprendere poi `docs/OTTIMIZZAZIONE-STATO.md`, aggiornando lo
  stato dei deploy host con prove reali; non ripetere l'implementazione già a main.
