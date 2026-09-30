# UI OpenAstro 3.5.1 — verifica e rollout

Verifica **2026-09-30**. Candidato `codex/ui-future-20260930`; runtime corrente
3.5.0 `103abbd349cb0f5fee6e062e41def90c412d6e0d`, LiveVault/NINA healthy e
Control attivo. CI del runtime [36718431842](https://github.com/astropuzzo/LiveVault-Test/actions/runs/36718431842)
verde. Il primo design è stato respinto dall’utente; questa iterazione segue
la scelta esplicita di evolvere l’originale con vetro, blu cosmico e motion.

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

Test locali: 48 Python mirati passati, 81 JavaScript passati (18 motion e
annullamento immediato hold inclusi). Le suite verificano async/race, preview,
player, qualità QSM, auth e contratti visuali. Sintassi/CI Linux completa
obbligatorie prima di main. Il vecchio run 583 Python/69 JavaScript appartiene
al runtime 3.5.0, non certifica questo candidato.

Prova JPEG reale NINA vincolata alla disponibilità PC/QSM. Placeholder e
stato offline non simulano una preview reale; resta un limite esplicito.

## Deploy e rollback

LiveVault Coolify watch `app/**`; NINA watch `nina-monitor/**`, main dopo CI
verde. Non riattivare il webhook manuale GitHub disabilitato `674326069`.
Control: sette asset allowlist aggiornati atomicamente e verificati SHA-256
in `/opt/openastro-control/static`, senza restart. Prima di scrivere,
verificare gli asset correnti rispetto al runtime 3.5.0 per preservare
modifiche impreviste. Sessioni, configurazioni, DB e registrazioni conservati.

Backup privato originale 3.4.31 già presente e da non sovrascrivere:
`/var/backups/openastro/20260930-ui-future/control-before.tar.gz` (0600).
Manifest asset installati: `manifest-installed.json` nella stessa directory
privata. Immagini 3.4.31 `:fc023fd8908d41fc897c78b73a65f97695ab26e0`
conservate per LiveVault/NINA; anche 3.5.0 `:103abbd...` è un rollback intermedio.
Ripristinare gli asset Control dal backup e redeploy immagini corrispondenti,
poi reload browser. Cache PWA `openastro-control-v3.5.1` / `livevault-shell-v3.5.1`;
nessuna migrazione dati da annullare.

`/opt/openastro-ops` è ancora all’allineamento precedente 3.4.31. Allineare
insieme al rollout e aggiornare SOURCE.txt con sorgente/runtime verificati;
il documento OAuth privato estraneo a Git deve restare intatto.
