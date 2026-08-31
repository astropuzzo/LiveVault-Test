# Test gratuito di LiveVault con GitHub Codespaces

Questo percorso serve esclusivamente a collaudare LiveVault prima di comprare una VPS.
Non è pensato come hosting 24/7: GitHub Codespaces si arresta dopo inattività e usa una quota mensile di compute.

## Cosa puoi testare

- dashboard e login;
- aggiunta/modifica sorgenti;
- rilevamento LIVE;
- avvio/stop FFmpeg;
- segmentazione (10 minuti nel preset test);
- upload Gofile;
- fallback Pixeldrain;
- verifica upload e cancellazione locale;
- retry e storico registrazioni.

## Avvio

1. Apri il repository in GitHub.
2. `Code` -> `Codespaces` -> `Create codespace on main`.
3. Attendi il completamento di `postCreateCommand`.
4. Nel terminale esegui:

```bash
./scripts/codespaces-run.sh
```

5. Scegli una password temporanea per LiveVault.
6. Incolla, se vuoi testare gli upload, token Gofile e API key Pixeldrain. Puoi lasciare vuota una delle due.
7. Apri la scheda **PORTS** e clicca sull'URL della porta `8080`.
8. Mantieni la porta **Private**: non serve renderla pubblica.
9. Accedi a LiveVault e aggiungi una sorgente di prova autorizzata.

## Arresto

Nel terminale premi `CTRL+C`, poi dal menu Codespaces scegli **Stop codespace** per non consumare quota gratuita inutilmente.

## Dati

I file di test e SQLite restano nella cartella `data/` del codespace finché il codespace esiste. Non committare `data/` né credenziali.

## Nota sui segreti

`codespaces-run.sh` tiene password e token solo nelle variabili d'ambiente del processo di test; non li scrive nel repository.
