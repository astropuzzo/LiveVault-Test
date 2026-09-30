# UI OpenAstro 3.5.1 — verifica e rollout

Verifica **2026-09-30**. Runtime 3.5.1 da
`bee494925048f52233951514b0ec206cc1042055`; LiveVault/NINA healthy, Control
attivo. CI feature [36733526667](https://github.com/astropuzzo/LiveVault-Test/actions/runs/36733526667)
e main [36734074221](https://github.com/astropuzzo/LiveVault-Test/actions/runs/36734074221)
verdi: 583 Python e 81 JavaScript, sintassi, shell, documentazione e
build/smoke NINA non-root senza mount. L’iterazione segue la scelta
dell’utente di evolvere l’originale con vetro, blu cosmico e motion.

## Sorgenti e comportamento

- LiveVault: `app/static/{index.html,style.css,app.js,ui.js,player.css,motion.js,sw.js}`.
  Rail e barra flottanti, sei metriche compatte, card live affiancate, libreria,
  archivio e analisi responsive. Geometria dei dati, crop storyboard 3×3 e
  maschere NSFW conservati; AWAY e TIP-JAR hanno stato e colore distinti.
- Control/Media: `control-panel/static/{index.html,app.css,app.js,motion.js,sw.js,fonts/}`.
  Stato operativo centrale, uptime secondario, composizione asimmetrica,
  sette viste e Media Hub compatto. Tutti i 292 ID operativi conservati;
  login, player/fullscreen/sottotitoli e conferma hold restano operativi.
- NINA: `nina-monitor/static/{index.html,app.css,app.js,motion.js,fonts/}`.
  Preview → prove stellari → telemetria → revisione; tutti i 68 ID operativi
  originali conservati, inspector e segnali di qualità restano fedeli a QSM.
  Font/motion relativi funzionano sotto `/nina/` nelle route allowlist.

Mona Sans variable locale con OFL; nessuna CDN o nuova dipendenza. Navy,
azzurro/periwinkle e gradienti di vetro; colori semantici separati. Blur solo
su shell, navigazione e finestre, senza moltiplicarlo su tutte le card.

## Motion e prestazioni

Implementazione identica nei tre `motion.js`, event-driven: entrate viewport
560 ms (stagger ≤220 ms), cambio viste anche con pushState e revisite,
pressione 280 ms, finestre/toast 380 ms in apertura e 180 ms in uscita,
filtri/lista, selezione frame 320 ms e tracciati SVG 720 ms. Identità orbitale
con ingresso finito; nessun loop o tracker del cursore. Massimo 80 effetti
concorrenti, feedback ripetuto sostituisce il precedente sullo stesso nodo.

Gli aggiornamenti automatici non rianimano grafici o details ricreati:
visita e gesto sono registrati separatamente; disclosure solo dal proprio
summary. Stop immediato su scheda nascosta/movimento ridotto, BFCache e
fallback senza Web Animations. Cancellare una conferma ferma hold e comando
subito; chiudere un player annulla la richiesta prima dell’uscita visiva.
Riaprire durante l’uscita annulla la vecchia chiusura. Le animazioni non
aggiungono richieste al server o polling.

## Verifiche

Anteprime locali in sola lettura: POST rifiutati, snapshot Control/NINA del
precedente audit e fixture LiveVault. Questi dati non sono misure correnti
del nodo. Nessuna immagine adulta scaricata; player con clip sintetica.
QA desktop iniziale: sette viste Control senza overflow/errori JavaScript,
logout senza sovrapposizione su portatile; Monitor Live con AWAY/TIP-JAR/REC,
modali e NINA con selezione frame/inspector. Su telefono: tutte le sette viste
Control a 412 px, quattro viste Live a 360 px, menu e profili dentro viewport,
legenda Cronologia con scorrimento interno, wall 16:9; tre login senza overflow.
NINA a 412 px con prove stellari e a 360 px offline. Badge corretto per
OFFLINE/STANDBY/CONFIGURA/LIVE; navigazione delle sezioni nascosta senza snapshot.
La policy del browser ha bloccato la ripresa dell’ultima anteprima: nessuna nuova
cattura del deploy 3.5.1 disponibile. QA player sintetico funzionale già completato
in 3.5.0; 3.5.1 cambia soltanto il suo CSS, coperto anche dai test di contratto.

Test locali: 52 Python mirati passati, 81 JavaScript passati (18 motion e
annullamento immediato hold inclusi). Le suite verificano async/race, preview,
player, qualità QSM, auth e contratti visuali. Sintassi/CI Linux completa
eseguite prima di main. Il primo run candidato ha rilevato tre contratti
visuali rimasti al design 3.5.0 (lime, card grande, clip globale): aggiornati
alla nuova palette, card compatta e contenimento locale della legenda già
verificato su telefono, mantenendo i controlli dei menu e dei dati. La successiva
CI completa e main sono verdi; il vecchio run 583/69 resta nella cronologia 3.5.0.

Prova JPEG reale NINA vincolata alla disponibilità PC/QSM. Placeholder e
stato offline non simulano una preview reale. Dopo il deploy: QSM configurato,
PC non raggiungibile, sessione inattiva e preview HTTP 502; limite ancora aperto.

## Deploy e rollback

LiveVault Coolify watch `app/**`; NINA watch `nina-monitor/**`, main dopo CI
verde. Non riattivare il webhook manuale GitHub disabilitato `674326069`.
Control: sette asset allowlist aggiornati atomicamente e verificati SHA-256
in `/opt/openastro-control/static`, senza restart; PID 1589194 invariato e hash
dei due file credenziali di riferimento invariati. Guardia preliminare: sei
asset uguali a Git 103abbd; `app.js` installato dall’audit aveva CRLF, contenuto
identico al sorgente dopo sola normalizzazione. Usati i suoi byte reali nella
guardia, senza accettare modifiche ignote. Sessioni e configurazioni conservate.

Runtime:
- LiveVault `ahul2vdjkyvjiwgzpcrmxzfe-150532228528`, immagine `:bee4949...`.
- NINA `ctrzdfqqsdljdcb2sbdrc7ug-150532327135`, immagine `:bee4949...`,
  utente `openastro`, zero mount e non privilegiato.
- HTTPS pubblico: indice 3.5.1 nei tre pannelli, otto asset con SHA identico al
  sorgente e API private HTTP 401 senza cookie. Nove asset Live/NINA verificati
  anche sul filesystem dei container.
- Live health 3.5.1, tutti i nove leader attivi, recorder 0, 1358 registrazioni,
  spazio libero 181,33 GB. Nessuna migrazione DB o intervento su storage/media.
- Checkout host: sedici voci sporche preservate; checkout Windows originale intatto.

Backup privato originale 3.4.31 già presente e da non sovrascrivere:
`/var/backups/openastro/20260930-ui-future/control-before.tar.gz` (0600).
Manifest asset installati: `manifest-installed.json` nella stessa directory
privata. Rollback intermedio statico 3.5.0: `control-350-assets.tar.gz` (0600),
con i sette asset effettivi prima del deploy; backup originale preservato.
Immagini 3.4.31 `:fc023fd8908d41fc897c78b73a65f97695ab26e0`
conservate per LiveVault/NINA; anche 3.5.0 `:103abbd...` è un rollback intermedio.
Ripristinare gli asset Control dal backup e redeploy immagini corrispondenti,
poi reload browser. Cache PWA `openastro-control-v3.5.1` / `livevault-shell-v3.5.1`;
nessuna migrazione dati da annullare.

`/opt/openastro-ops` distribuito contestualmente da tutti i Markdown tracciati,
con verifica SHA e SOURCE.txt per revisione documentale e runtime. Albero
precedente in `/var/backups/openastro/20260930-ui-future/ops-before.tar.gz` (0600).
`docs/GPT-HARNESS-OAUTH.md` fuori Git preservato per hash, senza riconvalidare
le sue informazioni Auth0 storiche. Per rollback documentale, ripristinare
l’albero e SOURCE.txt da quell’archivio insieme al runtime corrispondente.
