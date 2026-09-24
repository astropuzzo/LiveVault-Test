# MP4 e HLS — verifica 2026-09-20

Sorgenti: `app/mp4_fragments.py`, `app/recorder.py`, `app/stripchat_capture.py`.
Runtime iniziale verificato: LiveVault Coolify `9fae149`, Python 3.13.15,
FFmpeg 7.1.5, storage `nvme`. Checkout host sporco preservato.
Runtime finale verificato: release `6a41125474fb550c76ddd4b40d121ed387ca46a1`,
container `ahul2vdjkyvjiwgzpcrmxzfe-200629962506` healthy; deploy Coolify
`c2aajyqvdur3dd7fgz83j5yr` finished. PR #35 integrata.

## Diagnosi e correzione

Le registrazioni 861, 862, 863, 864 e 913 hanno box completi, init ftyp/moov e
payload audio/video presenti. I trun video contengono campioni di zero byte:
861 al moof 63; 862 ai moof 5/12/98; 863 al moof 20; 864 ai moof 12/14;
913 al moof 767. Il difetto è nell'indice dei frammenti della capture Mouflon.
La normalizzazione dell'11 settembre copriva soltanto campioni vuoti iniziali
nel layout 0x301; questi file ne contengono anche interni/finali e usano i
layout 0x305 (first_sample_flags) e 0x701 (flag per campione). Questo spiega
perché ripetere la finalizzazione ogni cinque minuti non risolveva l'errore.

La normalizzazione ora riconosce trun v0 con durata/dimensione esplicite,
data_offset e flag 0x301/0x305/0x701, un trun e un tfdt per traf. Rimuove
solo voci senza payload: le durate iniziali avanzano tfdt, quelle interne/finali
si sommano al precedente campione reale. Tutti i campioni mantenuti conservano
DTS, flag, byte e offset; la durata finale resta invariata. Se viene rimosso il
primo campione, viene rimosso anche il suo first_sample_flags: il successivo
continua a ereditare i default tfhd/trex. Un box free occupa lo spazio liberato,
quindi dimensioni di moof/traf, mdat e riferimenti sidx non cambiano.

Overflow, run interamente vuoti, versioni/layout ignoti o tabelle ausiliarie
che richiederebbero reindicizzazione restano non riparati. La capture usa la
normalizzazione prima della scrittura; il recupero usa una copia separata dopo
l'errore di header. Sostituzione originale soltanto dopo i controlli A/V
esistenti; cancellazione e storage handoff attendono il lavoro cooperativo.
Non sono stati ridotti i controlli né nascosti gli errori o modificati i retry.

Il resolver Mouflon mantiene la verifica della variante: un master HTTP 200
con child 404 non impedisce di tentare altri edge. Disponibilità del provider
e difetti non riconosciuti restano distinti da questa correzione.

## Evidenze e limiti

40 test mirati Windows passati (normalizzazione, capture e recorder). I test
coprono tempi dei campioni, durata totale, offset, payload, flag, idempotenza,
overflow, tabelle ausiliarie, copia separata e normalizzazione prima di scrivere.
CI Linux/Python 3.13 verde: 363 test, verifiche JS/shell/container e documentali.
Run PR `35534383687`, run main `35534467269` entrambi conclusi con successo.
Sul runtime Linux, tutte e cinque le copie superano sia il remux della capture
sia `finalize_mp4_for_streaming`, seguiti da `verify_media(..., 'packet')`.

| Registrazione | Durata verificata | Avviso già presente nel contenuto |
| --- | --- | --- |
| 861 | 42,018 s | nessuno |
| 862 | 56,012 s | gap video 1,20 s |
| 863 | 12,786 s | nessuno |
| 864 | 8,016 s | gap video 0,83 s |
| 913 | 420,020 s | gap video 1,62 s |

Non si ricreano frame mai ricevuti. Dopo il deploy, il worker normale ha
recuperato tutti e cinque i file: `integrity_status=passed`, `upload_status=uploaded`,
`integrity_error` e `last_error` vuoti. Nessun editing SQL. La normale policy
post-upload ha rimosso le copie locali operative; gli originali e le copie di
prova restano nella directory privata di recupero.
Health finale: storage nvme, recovery idle, un recorder attivo, 150,72 GiB liberi.
Capture sunshine_bliss cresciuta da 21.495.836 a 25.427.996 byte in 5 secondi.
Questa è prova di scrittura corrente; non una prova di stabilità USB prolungata.

Separatamente, il journal host del 20 settembre alle 20:51 BST mostra errori
I/O USB, journal ext4 abortito e successiva riconnessione del bridge RTL9210;
alle 20:55 il filesystem UUID `5fe2d0f6-b485-44e9-8e26-31fb0d217db2` è tornato
rw e lo stato applicativo è nvme. La finestra journal del 17 settembre
12:00–14:00 non restituisce eventi: non prova assenza di guasti storici.
La struttura riparabile dei cinque file identifica il difetto MP4 specifico,
ma questa modifica non risolve né certifica il collegamento fisico USB/NVMe.
Seguire [stabilità storage](STABILITY-20260909.md); nessun reboot, fsck su
mount attivi, cambio clock, restart Docker o intervento indiscriminato.

## Recupero e rollback

Backup DB eseguito con `openastro-action backup_now` prima delle prove.
Directory privata host `/data/livevault/mp4-recovery-20260920`, nel container
`/data/mp4-recovery-20260920`: cinque `ID.original.mp4`, `manifest.json` con
SHA-256 verificati contro i sorgenti, copie `ID.verified.mp4` e
`ID.capture-remux.mp4`, risultati `validation.json`. Non eliminare gli originali.
Pulizia 2026-09-24: rimosse le copie `ID.verified.mp4`, `ID.capture-remux.mp4`
e `403/404.corrected.mp4` (circa 495 MB) dopo aver verificato nel DB che le
registrazioni 403, 404, 861–864 e 913 sono `uploaded` su Gofile con integrità
`passed`; restano originali, manifest, log e `validation.json` (339 MB).
Una nuova copia corretta si rigenera dall'originale con la procedura sopra.
Le prove sono state eseguite con un modulo candidato in `/tmp` e copie separate,
senza sostituire il codice del processo applicativo in esecuzione.

Rollback applicativo: immagine Coolify `9fae149b5daba71c9b6a8b5c80e6b94a3bd08060`.
Immagine precedente verificata ancora presente sul nodo. Nessuna migrazione DB. Per rollback media, coordinare la pausa dei worker e
ripristinare il corrispondente originale verificato, quindi rivalidare i metadati;
gli originali riproducono l'errore noto. Conservare anche l'archivio precedente
`/data/livevault/mp4-recovery-20260911` per 403/404: il loro recupero della
release `606e9c3` era già stato verificato l'11 settembre.
