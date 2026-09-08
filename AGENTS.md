# Istruzioni obbligatorie per le IA
Prima di modificare progetto o server, leggere [AI-HANDOFF.md](AI-HANDOFF.md),
fonte di verità corrente, e solo i documenti collegati rilevanti.

- Verificare GitHub e stato reale prima di fidarsi del checkout locale.
- Preservare modifiche non committate, registrazioni, DB, segreti e rollback.
- Usare accessi SSH/GPT Harness esistenti senza stampare o committare credenziali.
- Storage tramite UUID e namespace host; mai basarsi sui nomi sdX.
- Interventi mirati; niente reboot, restart Docker o prune indiscriminati.
- Ogni modifica a runtime, storage, servizi, dipendenze, accessi o deploy DEVE
  aggiornare il documento operativo pertinente nello stesso commit.
- Aggiornare fatti e procedure in posizione, senza appendere copie contraddittorie.
  Includere data verifica, percorso sorgente/runtime, rollback e limiti.
- CI richiede aggiornamenti documentali, ma l'IA è responsabile della loro accuratezza.
- Test mirati e CI obbligatoria. QA visivo per cambiamenti visivi, non per backend.
- Concludere con modifiche, evidenze, rollback e punti ancora aperti.
