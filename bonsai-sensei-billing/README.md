# Bonsai Sensei — servizio acquisti

Preparazione verificata il **2026-10-08**. Servizio indipendente dal gioco e
dalle altre applicazioni OpenAstro. La configurazione distribuita ha
`BILLING_ENABLED=false`, catalogo `[]` e nessuna credenziale Google:
**non vende, non accetta ricevute e non assegna contenuti**.
`/healthz` risponde 200, `/readyz` risponde 503. Questo è lo stato atteso
finché Bonsai Sensei non è registrato nella Play Console.

## Avvio e container

Python 3.13, FastAPI, SQLite WAL con transazioni `FULL`, un solo processo
Uvicorn e un worker interno. Immagine eseguita come UID/GID `10001:10001`,
base `python:3.13.16-slim-bookworm`, verificata il 2026-10-08 rispetto
alle [immagini ufficiali Python](https://hub.docker.com/_/python),
porta `8095`. Il Dockerfile nella presente cartella è il punto di build;
Coolify distribuisce l'applicazione separata tramite `docker-compose.yml`
per applicare esattamente mount, filesystem in sola lettura, privilegi e
limiti di risorse. La distribuzione e l'ingresso pubblico sono descritti
in `../HOSTING.md`.

I mount devono restare separati dai dati LiveVault, Control e NINA:

| Percorso nel container | Uso | Accesso |
|---|---|---|
| `/data/bonsai-sensei` | Database, WAL e ledger acquisti | Scrittura, UID 10001 |
| `/config/catalog.json` | Prodotti autorizzati | Sola lettura |
| `/run/secrets/play-service-account.json` | Service account Google Play | Sola lettura |
| `/run/secrets/account-hmac.key` | Chiave base64 HMAC, almeno 32 byte casuali | Sola lettura |
| `/run/secrets/token-encryption.key` | Chiave Fernet per i purchase token | Sola lettura |
| `/run/secrets/admin-stats.key` | Chiave base64, almeno 32 byte casuali, per le sole statistiche interne | Sola lettura |

Non inserire segreti nell'immagine o nel repository. Le due chiavi sono
diverse e persistenti: cambiarle senza una migrazione rompe l'associazione
degli account o la decifratura degli acquisti pendenti. `.env.example`
contiene solo percorsi e valori vuoti. È possibile usare le variabili
`ACCOUNT_HMAC_KEY` e `TOKEN_ENCRYPTION_KEY` se non sono definiti i rispettivi
percorsi `*_FILE`; i file hanno precedenza.

Per verificare localmente il modulo:

```text
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m service
```

Senza container impostare `DATABASE_PATH` su una cartella separata e
`CATALOG_PATH` sul `catalog.json` incluso. Non occorrono credenziali per
avviare lo stato di preparazione. Non esiste una modalità di autenticazione
fittizia nel processo distribuito; i sostituti di Google sono iniettati solo
nei test.

## Configurazione da completare dopo la registrazione Google

1. Definire il package definitivo nella Play Console e impostare
   `PLAY_PACKAGE_NAME`. Nessun identificativo è stato inventato.
2. Abilitare Android Publisher API; autorizzare un service account soltanto
   per l'app Bonsai Sensei e con i permessi necessari a lettura acquisti,
   conferma/consumo e lettura acquisti annullati. Montare il JSON privato.
3. Creare Firebase Authentication e impostare `FIREBASE_PROJECT_ID`.
   Il futuro client deve inviare un **Firebase ID token** in `Authorization:
   Bearer …`. Non bastano un UID dichiarato, una chiave statica dell'app
   o un token OAuth di un'altra applicazione. La verifica controlla firma,
   scadenza, audience, issuer, sub e tempi di emissione/autenticazione.
   La revoca della sessione Firebase prima della scadenza del token non è
   verificata con Admin SDK: per ban immediati servirà una policy aggiuntiva.
4. Generare in modo privato le due chiavi, conservarle e includerle nel
   backup cifrato. Nessuna chiave va comunicata in chat.
5. Popolare il catalogo con gli identificativi realmente creati in Play:
   ogni elemento deve contenere soltanto `id` e `kind`, dove `kind` è
   `permanent` o `consumable`. Prezzi e quantità di valuta di gioco non
   appartengono a questo catalogo e non sono stati definiti.
6. Impostare `BILLING_ENABLED=true` e riavviare soltanto questo servizio.
   La prima sincronizzazione degli acquisti annullati deve riuscire prima
   che `/readyz` diventi 200 e prima di qualsiasi assegnazione.
7. Eseguire un acquisto di prova Play reale, pagamento pendente, ripristino,
   doppia richiesta, perdita di connessione, rimborso e recupero dopo
   riavvio. Queste prove reali restano aperte: i test locali usano risposte
   controllate e non dimostrano che permessi e prodotti Google siano attivi.

### Verifica delle dipendenze — 2026-10-08

Audit mirato delle dipendenze runtime con `pip-audit` 2.10.1: la
configurazione iniziale aveva segnalazioni note in cryptography, requests
e Starlette. Applicate soltanto le correzioni necessarie: cryptography
`50.0.0`, requests `2.33.0`, Starlette `1.3.1`; FastAPI `0.135.0`
permette quella versione di Starlette, mentre la versione iniziale ne
impediva l'uso. Uvicorn e google-auth sono rimasti invariati.
Il nuovo audit di `requirements.txt` copre 25 dipendenze risolte e
non segnala vulnerabilità note; **41 test passati** dopo gli aggiornamenti
nell'ambiente locale isolato. Le prove del container ARM64/Python 3.13
restano documentate dal deploy e dalla CI.

## Contratto API per la futura integrazione Godot

| Metodo e percorso | Risposta e significato |
|---|---|
| `GET /healthz` | Nome, versione, stato effettivo `billing_enabled`; 200 indica processo sano |
| `GET /readyz` | 200 solo se configurazione e riconciliazione rimborsi sono pronte; altrimenti 503 |
| `GET /v1/account` | `obfuscated_account_id` HMAC del Firebase UID, stabile e lungo 64 caratteri |
| `POST /v1/purchases/verify` | Corpo con `purchase_token` e `product_id`; risposta con `receipt_id`, `product_id`, `state`, `granted` |
| `GET /v1/entitlements` | Prodotti permanenti attivi e registro dei consumabili dell'account autenticato |
| `POST /v1/notifications/google-play` | Push Pub/Sub autenticato, disabilitato senza configurazione RTDN completa |
| `GET /internal/admin/summary` | Aggregati riservati alla console OpenAstro, con chiave amministrativa separata |

Prima di aprire il pagamento, il client deve recuperare `/v1/account` e
passare quel valore a `BillingFlowParams.Builder.setObfuscatedAccountId`.
Il server rifiuta acquisti senza quella associazione. Il corpo di verifica
non accetta `user_id` o campi extra. Il token viene inviato nel corpo HTTPS,
mai nell'URL. Il server richiede un solo prodotto, quantità uno, catalogo
autorizzato; no abbonamenti, no rental, no preorder, no multi-quantity.

Stati: `pending_purchase` (pagamento pendente), `processing` (verificato,
conferma o consumo ancora pendente), `active` (contenuto assegnabile),
`revoked` (annullato definitivamente). **Solo `granted=true`/`active` permette
uno sblocco.** La ricevuta di un consumabile identifica un evento immutabile;
il gioco dovrà applicarla una sola volta e gestire gli storni. Questo modulo
non aggiunge monete, non modifica salvataggi e non contiene regole di gioco.
Il ledger dei consumabili include anche stati pendenti/revocati per rendere
esplicito il risultato; il client non deve trattare ogni riga come un premio.

### Statistiche riservate della console OpenAstro — 2026-10-08

`GET /internal/admin/summary` richiede `Authorization: Bearer …` con la
chiave esatta caricata da `ADMIN_STATS_KEY_FILE`, il cui percorso predefinito
è `/run/secrets/admin-stats.key`. La chiave deve essere base64 valida con
almeno 32 byte casuali. Il confronto è costante nel tempo; chiave assente,
debole o errata produce 401 anche quando il billing è disabilitato.
La chiave rimane sul server/pannello e non deve arrivare al browser.
Un file mancante non impedisce l'avvio dello stato di preparazione.

Il servizio apre una connessione SQLite separata in modalità `mode=ro`
e legge un solo snapshot: non cambia ricevute, premi o code. La risposta
comprende nome/versione, stato `preparing` o `ready`, configurazioni ancora
mancanti, catalogo con soli identificativo e tipo, conteggi delle righe
per stato/tipo e una serie di 30 giorni UTC. `generated_at` indica l'istante
UTC della lettura; `source=sqlite_purchase_ledger` e
`count_unit=receipt_rows` identificano l'origine dei numeri.

`purchases.total` e `counted_total` sono il numero effettivo di ricevute
registrate, incluse quelle pendenti/revocate: **non sono vendite pagate**.
`users.purchasing_accounts` conta gli owner distinti nel ledger e va
presentato come **Account con ricevute**. Il numero di tutti gli utenti
registrati o attivi non è disponibile; i campi `registered` e `active`
valgono `null`. Gli ID account, purchase token e receipt ID non sono
restituiti. `daily_utc` contiene `date`, `purchases` (ricevute create) e
`revocations` (revoche registrate), con giorni vuoti a zero.

Ricavi e pubblicità restano `available=false`: mancano report finanziari
Google e configurazione AdMob. Il servizio non moltiplica acquisti per
prezzi presunti e non inventa incassi netti. Dopo questa aggiunta il totale
locale è **51 test passati**, comprese autorizzazione, statistiche nello
stato disabilitato, aggregati reali, finestre UTC e assenza di identificativi.

## Sicurezza delle transazioni e recupero

Il server interroga `purchases.productsv2.getproductpurchasev2` di Google
per il package configurato, controlla account e prodotto, poi registra
**nella stessa transazione SQLite** una sola ricevuta e il lavoro di
conferma/consumo. La chiave unica è SHA-256 del purchase token. Il token
originale è cifrato con Fernet per poter ritentare anche quando l'app è
chiusa. Una lease nel database impedisce finalizzazioni concorrenti.

Il contenuto rimane pendente fino alla conferma Google. L'intento di
finalizzazione viene salvato prima della chiamata: se la risposta si perde,
il worker verifica se Google ha già confermato/consumato e completa la
stessa ricevuta. I ritentativi hanno ritardo progressivo fino a un'ora.
Un consumabile già consumato, mai visto nel ledger, viene rifiutato:
non è possibile ricostruirlo in sicurezza da un database perso.

Il worker, indipendente dal client, legge ogni cinque minuti tutti i risultati
paginati di `purchases.voidedpurchases.list`, con sovrapposizione temporale;
i rimborsi e chargeback creano tombstone persistenti anche per token ignoti.
Una ricevuta revocata non viene riattivata da richieste o notifiche vecchie.
Gli acquisti attivi vengono inoltre ricontrollati periodicamente.
Se la sincronizzazione rimborsi non riesce per oltre 15 minuti, le nuove
verifiche sono bloccate con 503. Un'interruzione oltre 29 giorni richiede
audit manuale del ledger perché Google espone una finestra di 30 giorni;
non si deve cancellare il checkpoint per aggirare il blocco.

RTDN è facoltativo, il polling resta necessario. Per abilitarlo servono
`RTDN_AUDIENCE` (audience esatta configurata in Pub/Sub),
`RTDN_SERVICE_ACCOUNT_EMAIL` e `RTDN_SUBSCRIPTION` (nome completo
`projects/…/subscriptions/…`). La firma Google, issuer, audience, email
e `email_verified` vengono verificati. Il `messageId` viene accodato
durabilmente una sola volta prima della risposta 200. Il payload non
assegna premi direttamente; il worker ricontrolla Google e non deduce
il proprietario di un token ignoto. Abbonamenti non supportati.
Una notifica autenticata di rimborso totale per un acquisto una tantum
registra subito una tombstone nello stesso commit dell'accodamento;
il worker interroga comunque Google e il polling riconcilia tutti i casi.
Il tipo di evento rimane nel database. Una notifica di semplice acquisto
non assegna contenuti senza la verifica Google.

## Operazioni e limiti

Un solo container/replica e un solo worker; SQLite deve restare su disco
locale, non su una share di rete. Backup coerente con l'API SQLite backup
o container fermo, includendo ledger e chiavi in un archivio cifrato.
Non copiare il solo `.sqlite3` mentre il WAL è attivo. I mount Google e
chiavi possono rimanere assenti nello stato disabilitato.

Uvicorn non registra gli accessi; gli errori pubblici non riportano token,
corpi Google o stringhe di eccezione HTTP. Limite corpo 32 KiB; documentazione
Swagger/OpenAPI runtime disabilitata. Configurare anche al proxy limiti di
richieste e connessioni prima dell'apertura commerciale. Questa preparazione
non include rate limiting distribuito, notifiche all'operatore, backup
automatici, integrazione del gioco o verifica reale con Google.

Rollback: fermare soltanto l'applicazione Bonsai Sensei, oppure impostare
`BILLING_ENABLED=false` e distribuire l'immagine precedente. Conservare
database, chiavi e checkpoint: il rollback non deve perdere le ricevute
già registrate. Nessuna migrazione coinvolge le altre applicazioni.

## Fonti ufficiali consultate il 2026-10-08

- [Verifica ProductPurchaseV2](https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.productsv2/getproductpurchasev2)
- [Campi, quantità, stato, account e consumo](https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.productsv2)
- [Conferma acquisti](https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.products/acknowledge)
- [Consumo acquisti](https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.products/consume)
- [Rimborsi, paginazione e finestra di 30 giorni](https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.voidedpurchases/list)
- [Firebase ID token](https://firebase.google.com/docs/auth/admin/verify-id-tokens)
- [Autenticazione Pub/Sub push](https://cloud.google.com/pubsub/docs/authenticate-push-subscriptions)
- [Tipi di notifica e rimborsi totali](https://developer.android.com/google/play/billing/rtdn-reference)
