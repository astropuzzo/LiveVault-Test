# OpenAstro Control

PWA mobile-first per monitorare e controllare il server ASIAIR/Debian. Il servizio
vive sulla memoria interna, resta accessibile senza NVMe e viene pubblicato solo
in HTTPS tramite Tailscale Funnel, con autenticazione applicativa.

## Funzioni

- CPU, memoria, temperatura, uptime, rete e dischi in tempo reale
- grafici persistenti CPU, RAM, temperatura, rete e spazio disco (1h/6h/24h)
- watt istantanei e andamento energetico stimati; il CM4 non integra un sensore di corrente
- profili energetici ECO, Bilanciato, Performance e MAX senza overclock
- hotspot Wi-Fi attivabile o disattivabile con qualsiasi profilo, protetto dalla verifica Ethernet
- stato Docker, Tailscale, backup, LiveVault e Pi-hole
- elenco container e link rapidi alle interfacce
- rimozione sicura e riattivazione NVMe
- backup immediato, riavvio LiveVault/Docker/Pi-hole e reboot controllato
- installazione PWA dalla schermata Home
- esportazione CSV dello storico, con timestamp UTC e valori mancanti conservati
- campionamento condiviso fra client, polling sospeso nelle schede nascoste
- interfaccia coerente con LiveVault, navigazione da desktop e telefono

URL previsto: `https://openastro.tailf2871c.ts.net:8443/`

## Installazione sul telefono

1. Apri l'URL qui sopra da qualunque rete in Chrome (Android) o Safari
   (iPhone/iPad) ed effettua l'accesso.
2. Android: menu e **Installa app**. iPhone/iPad: **Condividi** e
   **Aggiungi alla schermata Home**.

La porta 8443 è pubblicata in HTTPS tramite Tailscale Funnel, ma non richiede
Tailscale sul telefono. Stato e comandi sono protetti da login, cookie sicuro,
CSRF e limitazione dei tentativi. Le credenziali sono nel file locale
del precedente workspace di installazione (`outputs/OpenAstro-Control-access.txt`)
e non vengono conservate in chiaro sul server o nel repository.

## Percorsi sul server

- applicazione: `/opt/openastro-control`
- servizio: `openastro-control.service`
- azioni privilegiate: `/usr/local/sbin/openastro-action`
- registro azioni: `/var/log/openastro-control-actions.log`
