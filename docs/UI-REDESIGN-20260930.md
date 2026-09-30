# UI OpenAstro 3.5.0 — verifica e rollout

Verifica: **2026-09-30**. Candidato del branch `codex/ui-future-20260930`;
produzione ancora 3.4.31 (`fc023fd8908d41fc897c78b73a65f97695ab26e0`)
al momento della preparazione. Aggiornare questo stato dopo il rollout verificato.

## Sorgenti e comportamento

- LiveVault: `app/static/{index.html,style.css,ui.js,player.css,motion.js,sw.js}`.
  Shell, login, quattro viste e player ricostruiti. Live prima della cronologia;
  filtri archivio progressivi e righe con sei campi correttamente riallineati.
  Le geometrie della timeline, crop storyboard 3×3 e maschere NSFW restano dedicate
  ai dati; AWAY conserva etichetta e stato distinti da TIP-JAR.
- Control/Media: `control-panel/static/{index.html,app.css,motion.js,sw.js,fonts/}`.
  Dashboard con uptime reale, sette viste, libreria prima dell’import, player
  completo e logout raggiungibile anche dal telefono. Solo CSS funzionali
  diagnostica/import precedono il nuovo `app.css`; vecchie skin non caricate.
- NINA: `nina-monitor/static/{index.html,app.css,motion.js,fonts/}` e `server.py`.
  Observatory: preview → prove stellari → telemetria → revisione. Tutti gli ID
  operativi conservati; l’inspector resta accessibile. Nuove route statiche
  allowlist per font e motion, funzionanti dietro il prefisso `/nina/`.

Mona Sans variable locale con licenza OFL; nessuna CDN o dipendenza JavaScript
aggiunta. Superfici solide, contrasto ink/ivory/lime, accento azzurro NINA;
colori semantici per registrazione, warning, AWAY e TIP-JAR.

Il motion condiviso usa Web Animations/IntersectionObserver con fallback
leggibile. Entrate finite (560 ms, ritardo massimo 220 ms), una volta per
superficie; nessun loop di animazione o tracker del cursore. Cambio viste
osservato anche con `pushState`. Stop nelle schede nascoste, cancellazione
immediata con movimento ridotto, recupero della BFCache. Gli aggiornamenti
periodici di telemetria non rianimano i contenuti.

## Verifiche

QA con browser reale su anteprime locali in sola lettura: POST rifiutati, dati
Control/NINA esportati nel precedente audit e fixture LiveVault con live,
AWAY, TIP-JAR, archivio e statistiche. Non sono misure correnti del nodo.
QA desktop 1440×1000, tablet 1024 e telefoni 412/360: viste Control e
LiveVault, filtri, griglia/lista e profilo creator; selezione/revisione frame
NINA. Il profilo mobile ora ridispone metriche, Live DNA e calendario senza
overflow. Evidenziazione dei file problematici e preview touch ripristinate.
Login dei tre pannelli verificati anche a 360 px; Control nasconde la shell
bloccata per eliminare il doppio scroll. Player HLS provato con clip sintetica
640×360: readyState 4, durata 4,04 s, playback riuscito. Stato NINA offline
e stato non configurato verificati senza simulare un’immagine astronomica.
Smoke del runtime da completare dopo il deploy. Prova di JPEG reale NINA ancora
vincolata alla disponibilità del PC/QSM; il placeholder non simula immagini.

Test Node: regressioni frontend, player, async, NINA, diagnostica e otto
regressioni motion. Test Python: asset relative e nuove route NINA, contratti
UI/ID/cache e versione. CI Linux completa obbligatoria prima di main.
Verifica locale finale: 69 test JavaScript e 48 test Python mirati passati;
sintassi di tutti i JavaScript e compileall Python riusciti. Suite completa
Windows: 554 passati, 7 saltati dopo i due fix CSS; 22 casi host/storage non
eseguibili correttamente su Windows (`pwd`, namespace/mount e path POSIX).
Il conteggio 554 comprende il rerun dei tre test UI interessati. CI Linux
[36717455425](https://github.com/astropuzzo/LiveVault-Test/actions/runs/36717455425)
verde: 583 Python, 69 JavaScript, sintassi shell, build e smoke NINA isolato.
L’ultimo fix del login richiede una nuova CI prima della promozione.

## Deploy e rollback

LiveVault è distribuito da Coolify da `app/**`; NINA da `nina-monitor/**`.
Main dopo CI verde è il trigger. Non riattivare il webhook GitHub manuale
`674326069`, già disabilitato per evitare deploy duplicati.
Control serve file da `/opt/openastro-control/static`: aggiornamento atomico
solo degli asset noti dopo verifica SHA-256; nessun restart del servizio
necessario per questi file statici. Non toccare configurazioni, sessioni,
registrazioni, DB, servizi storage o impostazioni di acquisizione.

Preparare sul nodo backup privato
`/var/backups/openastro/20260930-ui-future/control-before.tar.gz`
prima del primo asset. Conservare immagini Coolify `:fc023fd...` dei due
moduli e manifest degli hash installati. Rollback: redeploy delle immagini
3.4.31 e ripristino dei soli asset Control dal backup, poi reload del browser;
la cache PWA usa `openastro-control-v3.5.0` / `livevault-shell-v3.5.0`.
Nessuna migrazione dati da annullare. Anche la copia documentale
`/opt/openastro-ops` deve essere allineata e avere SOURCE.txt con revisione e
rollback; il documento OAuth privato, estraneo a Git, va preservato.
