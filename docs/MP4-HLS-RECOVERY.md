# MP4 e HLS — verifica 2026-09-11

Sorgenti: app/mp4_fragments.py, app/recorder.py, app/stripchat_capture.py.
Runtime verificato dopo deploy: LiveVault Coolify 606e9c3, storage nvme.
Checkout host non modificato.

## Diagnosi e correzione

Le registrazioni 403 e 404 hanno ftyp/moov e payload audio/video presenti.
Un trun video in ciascuna contiene un primo sample di zero byte (moof 10 e 821).
FFmpeg 7.1.5 interrompe la lettura con error reading header.
Non si tratta di un moov assente e ripetere il remux non corregge il difetto.

La correzione riconosce solo trun v0 con flag 0x301, durata/dimensione
esplicite, un trun e un tfdt per traf e sample vuoti iniziali seguiti da media.
Rimuove le sole voci senza payload, avanza tfdt della relativa durata e usa
un box free per preservare dimensione e offset. Non modifica mdat.
Layout sconosciuti, overflow o sample vuoti interni restano non riparati.
La capture applica la normalizzazione prima della scrittura; il recupero dei
file preesistenti usa una copia separata solo dopo errore di header.
Sostituzione originale solo dopo i controlli A/V esistenti; cancellazione e
storage handoff attendono la copia cooperativa e rimuovono il temporaneo.

Il resolver Mouflon verifica anche la variante: un master HTTP 200 con child
404 non impedisce di tentare gli altri edge. Se tutti sono indisponibili,
l'errore rimane visibile; non promette disponibilità del provider.

## Evidenze e limiti

Test locali mirati Windows e prove su copie dei due file nel runtime Linux.
Entrambe le copie superano ffprobe dopo correzione dell'indice.
Entrambe superano la verifica integrità completa sul runtime Linux: 403 dura
9,619 s, 404 dura 770,017 s. Test mirati Windows: 12 pass, 1 skip Linux affinity.
CI Linux/Python 3.13 34572489572 verde: 331 test, verifiche JS/shell e container.
PR #30 integrata; deploy Coolify japbbzdmxreilexbk9sny3mp finished, container
ahul2vdjkyvjiwgzpcrmxzfe-070310327598 healthy sulla release 606e9c3.
Le righe 403/404 sono entrambe integrity_status=passed e upload_status=uploaded:
recuperate automaticamente dal nuovo worker, senza modifica manuale del DB.
Health: recovery idle, due recorder attivi, storage nvme, spazio libero 159,81 GiB.
Capture Stripchat AliciaBrooks cresciuta da 72.215.825 a 75.248.798 byte in 5 s.
AngelLeeen era live durante la prova resolver: Flashphoner indisponibile,
Mouflon con variante verificata disponibile. Dopo deploy è private, confermato
sia dal provider sia dal DB: nessuna capture pubblica attesa in tale stato.
Il file 403 segnala un gap video di 0,97 s:
la correzione non ricrea fotogrammi mai ricevuti.

## Recupero e rollback

Directory privata persistente nel container: /data/mp4-recovery-20260911
(host /data/livevault/mp4-recovery-20260911). Copie corrette 403.corrected.mp4
e 404.corrected.mp4. Originali conservati prima del deploy come 403.original.mp4
e 404.original.mp4, verificati SHA-256 contro le sorgenti in manifest.json.
Backup DB completato tramite openastro-action backup_now prima del deploy.
I percorsi DB ora contengono i file recuperati. Conservare la directory privata.
Non cancellare registrazioni per eliminare gli allarmi.

Rollback codice: immagine Coolify 607a151, preservata. Nessuna migrazione DB.
Rollback media: ripristinare la copia originale corrispondente dal backup
privato, poi rivalidare i metadati; gli originali riproducono l'errore noto.
