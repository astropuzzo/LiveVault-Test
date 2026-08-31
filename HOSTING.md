# Hosting gratuito per LiveVault

Verificato il 1 settembre 2026.

LiveVault non e una normale web app stateless: registra 24/7 con FFmpeg, mantiene SQLite e un buffer video locale, quindi richiede un processo sempre attivo, Docker, storage persistente e molto traffico in uscita verso Gofile/Pixeldrain.

## Scelta consigliata: Oracle Cloud Always Free

La VM `VM.Standard.A1.Flex` e l'unica offerta gratuita corrente che soddisfa bene questi requisiti:

- fino a 2 OCPU Arm e 12 GB RAM Always Free;
- 200 GB totali di Block Volume Always Free (boot disk incluso);
- 10 TB/mese di traffico dati in uscita;
- Ubuntu e IP pubblico supportati;
- Docker, Python e FFmpeg usati da LiveVault sono compatibili con `linux/arm64`.

Limiti importanti:

- la capacita A1 puo non essere subito disponibile nella home region;
- Oracle puo recuperare istanze Always Free considerate inattive;
- gratuito non significa SLA o disponibilita garantita: mantieni backup del database e conserva `APP_SECRET`.

Documentazione ufficiale: [Oracle Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).

## Configurazione VM consigliata

Nel pannello Oracle Cloud crea una Compute Instance con:

- image: Ubuntu 24.04 LTS;
- shape: `VM.Standard.A1.Flex`, 2 OCPU, 12 GB RAM;
- boot volume: 100 GB;
- public IPv4: attivo;
- chiave SSH: genera o carica la tua chiave pubblica;
- regola ingress: solo TCP 22 inizialmente.

La configurazione lascia margine sufficiente per il buffer predefinito da 12 GB. Non aprire pubblicamente la porta 8080: dopo l'installazione usa Tailscale, oppure abilita HTTPS con Caddy e apri soltanto 80/443.

## Installazione

Collegati in SSH alla VM e avvia:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/astropuzzo/LiveVault-Test.git
cd LiveVault-Test
chmod +x scripts/*.sh
./scripts/install.sh
```

Lo script chiede una password pannello di almeno 10 caratteri, genera `APP_SECRET`, costruisce il container e avvia LiveVault.

Per accesso privato HTTPS consigliato:

```bash
./scripts/enable-tailscale.sh
```

Installa Tailscale anche sul telefono/PC e apri l'URL mostrato dallo script. In alternativa, per un endpoint pubblico:

1. autorizza TCP 80 e 443 sia nella Security List/NSG Oracle sia nel firewall della VM;
2. esegui `./scripts/enable-https.sh`;
3. usa l'URL `sslip.io` mostrato dallo script.

## Dopo il primo accesso

1. Apri **Settings**.
2. Inserisci il token Gofile e premi **Test connessione**.
3. Inserisci anche una API key Pixeldrain come fallback e testala.
4. Mantieni `Delete after upload` attivo solo dopo che entrambi i test sono verdi.
5. Aggiungi esclusivamente sorgenti che possiedi o che sei autorizzato a registrare.

Controlli server:

```bash
./scripts/status.sh
./scripts/backup.sh
```

Aggiornamento dal repository:

```bash
./scripts/update.sh
```

## Perche non gli altri free tier

| Provider | Motivo per cui non e adatto al servizio 24/7 |
|---|---|
| GitHub Codespaces | Quota personale limitata; ambiente di sviluppo, non host permanente. |
| Google Compute Engine Free Tier | 1 e2-micro e 30 GB disco sono stretti; 1 GB/mese di egress gratuito non basta per upload video continui. |
| Render Free | Va in sleep e perde filesystem/SQLite a ogni restart o deploy; persistent disk solo a pagamento. |
| Koyeb Free | 0.1 vCPU, 512 MB RAM, 2 GB SSD, scale-to-zero e nessun volume persistente. |
| Fly.io | Offre un trial breve, non un free tier permanente. |

Fonti ufficiali: [GitHub Codespaces](https://github.com/features/codespaces), [Google Compute Engine](https://cloud.google.com/products/compute), [Render Free](https://render.com/docs/free), [Koyeb Instances](https://www.koyeb.com/docs/reference/instances), [Fly.io Free Trial](https://fly.io/docs/about/free-trial/).
