# LiveVault v1.1.2

Remote recorder leggero con dashboard PWA, FastAPI, SQLite, yt-dlp e FFmpeg stream-copy. Monitora sorgenti autorizzate, registra quando diventano live, segmenta, carica su Gofile con fallback Pixeldrain e cancella il locale solo dopo upload verificato.

## TEST GRATIS — GitHub Codespaces

1. `Code` → `Codespaces` → **Create codespace on main**.
2. Attendi che la preparazione automatica termini.
3. Nel terminale: `./scripts/codespaces-run.sh`
4. Inserisci password temporanea e, opzionalmente, token Gofile / API key Pixeldrain.
5. Apri **PORTS** e l'URL HTTPS della porta **8080**. Mantieni la porta `Private`.
6. Aggiungi una sorgente autorizzata dalla dashboard.

Il preset Codespaces usa polling 30 s e segmenti da 10 minuti. I segreti restano nelle variabili del processo e non vengono scritti nella repo.

## VPS definitiva
`chmod +x scripts/*.sh && ./scripts/install.sh && ./scripts/configure-storage.sh`

Architettura: `yt-dlp → FFmpeg stream copy → segmenti → SHA256/ffprobe → Gofile → Pixeldrain fallback → delete local after verified upload`.

Uso esclusivamente per sorgenti che possiedi o che sei autorizzato a registrare.
