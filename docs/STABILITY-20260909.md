# CM4 1,8 GHz e recupero storage

Verifica: 2026-09-09. Sostituisce il profilo operativo a 2 GHz e le prove OC
descritte come storico in CM4-CLOCK-TEST.md. Nessun reboot richiesto/eseguito.

## CPU

`/boot/firmware/config.txt`: arm_freq=1800, nessun offset di tensione configurato.
Minimo runtime 600000 kHz, massimo 1800000 kHz, governor schedutil; DVFS firmware
resta automatico (dvfs=3). Il valore AVS riportato dal firmware non è un undervolt
aggiunto nel config. Wi-Fi resta off come nel profilo salvato performance/off.
`control-panel/openastro-power-profile`, installato in
`/usr/local/sbin/openastro-power-profile`, limita tutti i profili a 1800 MHz anche
prima del prossimo boot. Il profilo corrente performance conserva il minimo a
600 MHz; il profilo max selezionato esplicitamente continua a fissare il clock.
tryboot.txt della prova 2100 è stato confrontato con il checksum della ricevuta e
archiviato, poi rimosso dal boot. Non riutilizzare il vecchio runner resume a 2000.

Il clock basso non misura i watt totali del sistema. Restano alimentati eMMC,
rete, controller e periferiche; non viene promesso un consumo assoluto in idle.

## Evidenze USB

Orari host BST, un'ora indietro rispetto all'Italia:
- 04:22:31: reset ripetuti USB 3-3, bridge UnionSine MD202 RTL9210, 0bda:9210.
- 04:22:32: errori I/O; 04:22:38 journal ext4 abortito; 04:22:50 read-only.
- 04:22:51 watchdog rileva emergency_ro; 04:23:04 failover al buffer completato.
- 06:15:32 scollegamento; 06:21:34 nuova enumerazione; 06:24:20 ritorno NVMe.
- UAS già escluso; power/control=on, runtime_suspended_time=0. Non modificati.
- Undervoltage solo il giorno precedente 17:24:21–25; flag storico 0x50000.
- SMART via UUID SERVER e `smartctl -a -d sntrealtek`: WDS500G3X0C, 53 C,
  zero media/data integrity errors e zero error log entries; 3372 unsafe shutdown
  storici, non attribuibili tutti a questo evento. Non prova integrità dei video.

Il trasporto USB ha fallito; i log non distinguono box/cavo/alimentazione o CPU.
Non dichiarare risolta la causa fisica con il solo abbassamento del clock.
SHARE segnala unclean unmount; controllo offline ancora da completare.

## Buffer, player e stitching

Sorgenti: app/recorder.py, app/workers.py, app/storage_handoff.py e
app/storage_response.py; route media in app/main.py.

Buffer conserva durata/dimensione configurate: eliminati il minuto e il limite
artificiale da 64 MiB effettivi. Restano filesystem riservato da 4 GiB, controllo
spazio ogni 250 ms, riserva 128 MiB per capture più uno slot e full latch.
Al rientro, recovery indicizza i vecchi capture prefix anche se la stessa sessione
ha già una nuova capture attiva; non tocca i file del processo corrente.

Un singolo pezzo usa hard link temporaneo sullo stesso filesystem, evitando il
passaggio FFmpeg di concatenazione. Il downstream continua normalizzazione,
verifica A/V, packet scan e SHA-256 prima di rimuovere l'originale. Più pezzi
continuano con stream-copy e fallback esistente. Non è una promessa di maggiore
velocità delle ricodifiche né una rimozione dei controlli di integrità.

Il primo eject di manutenzione è fallito EBUSY: uvicorn teneva un file video
aperto per il player anche dopo l'ack. Recover ha verificato i mount e ripristinato
NVMe senza forzature. StorageFileResponse preserva HTTP Range e annulla/join la
risposta su cambio storage, anche con client lento; il cleanup aspetta il contatore
delle risposte prima dell'ack. Nuove risposte durante indisponibilità ricevono 503.

## Verifica e rollback

Backup privato eMMC: `/var/backups/openastro/20260909-stability` contiene config,
helper/profilo precedenti, tryboot, kernel-before.log, storage-before.log e log
manutenzione. Ripristinare config-before.txt e power-profile-before nei percorsi
originali per rollback CPU; il limite runtime precedente era 2000000 kHz, ma
riabilitarlo contraddice la richiesta attuale. Non ripristinare tryboot normalmente.
Rollback applicativo: immagine Coolify precedente 56ce014; nessuna migrazione DB.
Checkout host sporco preservato; deploy solo da commit GitHub validato.

Stato al commit: CPU applicata; prima CI Linux 34315849630 verde, 321 test.
Fix applicativi non ancora live; test aggiuntivo recovery nella stessa directory
e verifica reale della chiusura FileResponse in validazione sul commit finale.
Prova CPU 180 s passata: picco 65,244 C, mediana 1800,457 MHz, nessun nuovo flag
o errore kernel; limite minimo 600 MHz. Non è prova di stabilità prolungata.
Fsck e verifica live dopo deploy ancora pendenti.
Il test Windows ha 42 pass e 15 errori di piattaforma (O_CLOEXEC assente): non
sostituisce la CI Linux/Python 3.13 obbligatoria.
