# GPT Harness: autenticazione ChatGPT

Verifica e modifica autorizzata: **2026-09-21**. Fonte corrente per OAuth;
accesso host e vincoli restano in [AI-HANDOFF.md](../AI-HANDOFF.md).

## Runtime e configurazione

- ASIAIR: `gpt-harness.service`, `gpt-harness-root.service` e
  `gpt-harness-tailscale.service` verificati attivi; health locale `status: ok`.
- Sorgente upstream: https://github.com/salman-frs/gpt-harness,
  revisione installata `2637e35d8388eebe65ed920df69dd01b9a443f96`.
  Runtime `/opt/gpt-harness` → `/data/gpt-harness/app`, avvio `dist/main.js`;
  configurazione privata `/etc/gpt-harness/gateway.env`, lavoro `/data/gpt-harness/work`.
- Gateway `127.0.0.1:8787`, Funnel dedicato
  `https://harness.tailf2871c.ts.net`, endpoint/audience `/mcp`.
  Stato Tailscale `/data/gpt-harness/tailscale-node`, socket
  `/run/gpt-harness-tailscale/tailscaled.sock`.
- Auth0: tenant `dev-f45w46gbwf5sgaqd.us.auth0.com`, API **GPT Harness**,
  identifier `https://harness.tailf2871c.ts.net/mcp`, JWT RS256,
  access token massimo 86400 secondi; scope applicativo `gpt-harness.use`.
  Il gateway controlla issuer, audience, scadenza, proprietario e scope.

## Correzione applicata

In Auth0 → Applications → APIs → GPT Harness → Settings,
**Allow Offline Access è abilitato**, salvato e verificato attivo dopo reload.
Prima era disabilitato. Il client DCR ChatGPT usato nei log ha già grant
Authorization Code e Refresh Token abilitati, rotazione refresh attiva,
durata inattiva 15 giorni e massima 30 giorni. Questi valori non sono stati modificati.
Callback esistente: `https://chatgpt.com/connector_platform_oauth_redirect`.

Auth0 richiede sia l'opzione API sia la richiesta `offline_access` per emettere
un refresh token: [documentazione Auth0](https://auth0.com/docs/secure/tokens/refresh-tokens/get-refresh-tokens).
L'abilitazione consente l'emissione nei nuovi flussi idonei; non aggiunge un token
di rinnovo alle connessioni già create. Nessuna modifica al codice, ai segreti,
alla durata access token o ai servizi del nodo; nessun riavvio.

## Evidenze e limite della diagnosi

- Il connettore aveva restituito `oauth_refresh_token_missing`.
- I log Auth0 del 21 settembre mostrano login e scambio codice riusciti.
  Stesso client, utente e audience del precedente scambio riuscito del 16 settembre.
  `scope: null` compare in entrambi: da solo non prova un errore né i claim emessi.
- Health pubblico e locale, metadata OAuth, discovery Auth0 e JWKS raggiungibili
  durante la diagnosi. Orologio del nodo sincronizzato.
- Due richieste MCP del 20 settembre risultavano senza Bearer token, rifiutate
  correttamente con 401. Non provano un malfunzionamento del server.

L'opzione disabilitata spiega la mancata emissione di refresh per flussi che lo
richiedono, ma non è ancora dimostrata come unica causa dell'errore generico di
collegamento ChatGPT. Non è stabilito quando o perché la configurazione sia cambiata.
**Resta da verificare il nuovo collegamento ChatGPT e una chiamata autenticata**;
il rinnovo effettivo richiede inoltre un evento refresh riuscito.

## Verifica e ripristino

1. Ricollegare GPT Harness in ChatGPT e completare il login; non occorre
   reinstallare ripetutamente il plugin.
2. Verificare una chiamata innocua autenticata; confrontare orari UTC degli eventi
   Auth0 e del journal Harness. Non stampare codici OAuth, token o segreti.
3. Se persiste l'errore, controllare il nuovo scambio, la richiesta `offline_access`
   e l'arrivo del Bearer al gateway. Non cambiare audience, owner o scope alla cieca.

Rollback: riportare **Allow Offline Access** a disabilitato nella stessa pagina
API e salvare. Questo non va trattato come revoca dei token già emessi: un'eventuale
revoca va gestita separatamente. Non ripristinare file env o riavviare il server
per annullare questa modifica Auth0. Copia operativa su `/opt/openastro-ops`;
revisione e backup documentale identificati dal relativo `SOURCE.txt`.
