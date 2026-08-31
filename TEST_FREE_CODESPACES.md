# Test LiveVault v2 gratis con GitHub Codespaces

Questa modalita serve per provare la build corrente della repository prima del deploy su VPS.
Non e' pensata come hosting 24/7.

## Avvio manuale

1. GitHub: **Code -> Codespaces -> Create codespace on main**.
2. Attendi che `postCreateCommand` completi la preparazione.
3. Nel terminale esegui:

   ```bash
   ./scripts/codespaces-run.sh
   ```

4. Scegli una password temporanea di almeno 10 caratteri.
5. Apri la scheda **PORTS** e la porta **8080**. L'URL deve restare **Private**.
6. Accedi a LiveVault.
7. Le API key Gofile/Pixeldrain si configurano e si testano ora direttamente da **Settings** nella web app.

## Smoke test automatico

Per verificare backend, login e Settings API senza configurare provider:

```bash
./scripts/codespaces-smoke.sh
```

Il test avvia una istanza temporanea, controlla `/healthz`, effettua login, legge e modifica Settings, quindi chiude l'istanza.

## Cosa viene verificato in GitHub Actions

La workflow CI esegue due job:

- `test`: unit/integration tests, Python compile, JavaScript syntax e shell syntax;
- `codespaces-smoke`: usa gli stessi script Codespaces (`codespaces-prepare.sh` + `codespaces-smoke.sh`) per assicurarsi che l'ambiente di prova possa realmente avviare LiveVault v2.

Una release non va considerata pronta se uno dei due job e' rosso.
