# MP4 e HLS — verifica 2026-09-11

Sorgenti: app/mp4_fragments.py, app/recorder.py, app/stripchat_capture.py.
Runtime iniziale verificato: LiveVault Coolify 607a151, storage nvme.
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
CI Linux/Python 3.13 in corso; deploy non ancora eseguito.
Il file 403 segnala un gap video di 0,97 s:
la correzione non ricrea fotogrammi mai ricevuti.

## Recupero e rollback

Directory privata persistente nel container: /data/mp4-recovery-20260911
(host /data/livevault/mp4-recovery-20260911). Copie corrette 403.corrected.mp4
e 404.corrected.mp4. Originali ancora nei percorsi DB finché il recupero
non viene applicato; prima della sostituzione conservarli nella directory
privata insieme a manifest SHA-256 e backup DB tramite procedura HOSTING.md.
Non cancellare registrazioni per eliminare gli allarmi.

Rollback codice: immagine Coolify 607a151. Nessuna migrazione DB.
Rollback media: ripristinare la copia originale corrispondente dal backup
privato, poi rivalidare i metadati; gli originali riproducono l'errore noto.
