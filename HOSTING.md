# Hosting di LiveVault su TierHive

Configurazione attiva dal 1 settembre 2026.

## Indirizzi web

- LiveVault: <https://livevault.streamingcam.duckdns.org>
- Pannello hosting CapRover: <https://captain.streamingcam.duckdns.org>

Entrambi gli indirizzi sono pubblici e funzionano da PC, Android e tablet. L'accesso alle funzioni resta protetto dalle rispettive password.

## Architettura

- VPS Debian 12 su TierHive: 2 vCPU, 2 GB RAM, 30 GB NVMe;
- CapRover gestisce applicazioni, log, variabili, riavvii e deploy;
- TierHive HAProxy espone soltanto i domini web e termina HTTPS;
- DuckDNS fornisce il dominio gratuito `streamingcam.duckdns.org`;
- `/opt/livevault/data` è montato in `/data` nel container e conserva database e impostazioni tra deploy e riavvii;
- uno swap file da 1 GB assorbe i picchi temporanei delle build.

## Deploy automatico da GitHub

L'app `livevault` segue il branch `main` di `astropuzzo/LiveVault-Test`.

1. Un push su `main` avvia il webhook GitHub.
2. CapRover scarica il repository e legge `captain-definition`.
3. Costruisce una nuova immagine dal `Dockerfile`.
4. Sostituisce il container soltanto dopo la build.
5. In caso di problema, la versione precedente resta disponibile nella cronologia del pannello.

I log della build sono in **Apps > livevault > Deployment > View Build Logs**. I log dell'app sono in **Apps > livevault > Logs**.

## Aggiungere un altro progetto

1. Nel pannello CapRover apri **Apps** e premi **Create A New App**.
2. In **Deployment**, inserisci repository e branch GitHub.
3. Aggiungi al repository un `captain-definition` che punti al Dockerfile.
4. In **App Configs**, inserisci variabili e segreti senza salvarli nel repository.
5. Se il progetto scrive dati, crea un percorso persistente prima del primo deploy.
6. In **HTTP Settings**, imposta la porta usata dal container.
7. In TierHive **HAProxy**, aggiungi un dominio DuckDNS, convalidalo, collega `10.5.138.11` alla porta 80 e abilita SSL.
8. Copia il webhook mostrato da CapRover nelle impostazioni GitHub del repository.

Il VPS aggiornato può ospitare LiveVault e alcuni servizi piccoli. I 2 GB di RAM permettono monitoraggio e due vCPU, ma è comunque prudente eseguire una build pesante alla volta e tenere i database grandi su servizi separati.

Il disco del sistema è già stato esteso a 30 GB; la partizione ext4 vede l'intera capacità. Con il costo corrente di circa 1,95 token/mese il preventivo è circa 23,40 token/anno, esclusi eventuali consumi o variazioni del provider.

## Dati e backup

Un timer di sistema crea ogni giorno alle 03:15 UTC un archivio in `/var/backups/live-platform/` e conserva gli ultimi sette giorni. Il backup include la configurazione CapRover e una copia coerente del database SQLite; il buffer video temporaneo resta escluso per non saturare il disco.

Prima di modifiche importanti, conserva fuori dal VPS almeno un backup recente e i segreti dell'app. Il provider non sostituisce una copia esterna.

## Sicurezza operativa

- SSH accetta soltanto la chiave autorizzata; login con password disattivato.
- Non pubblicare token, password o chiavi nel repository.
- Mantieni aggiornati Debian, Docker, CapRover e le dipendenze dell'app.
- Lascia attivo HTTPS e usa password diverse per pannello e LiveVault.
- Conserva i dati soltanto su sorgenti che possiedi o sei autorizzato a registrare.

Documentazione: [CapRover](https://caprover.com/docs/get-started), [deploy da Git](https://caprover.com/docs/deployment-methods.html), [monitoraggio](https://caprover.com/docs/resource-monitoring.html), [DuckDNS](https://www.duckdns.org/spec.jsp).
