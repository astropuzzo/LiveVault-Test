# InstaVault

Private archive of the Instagram profiles the user picks: posts, carousels and
Reels, plus stories and highlights when an Instagram session is connected. Pure
Python standard library HTTP server, instaloader for Instagram, SQLite, no
frontend framework. Operations, paths and rollback live in
[HOSTING.md](../HOSTING.md#instavault).

## Layout

- `vault/server.py`: HTTP API, static files, media with byte ranges, login shared
  with the OpenAstro Control credentials, signed cookie plus CSRF token.
- `vault/sync.py`: one background worker with a file lease, resumable cursors,
  deduplication by Instagram id, downloads only from Instagram CDN hosts, quota
  and free-space checks before every chunk.
- `vault/store.py`: SQLite schema and queries; media paths stay inside `/media`.
- `vault/security.py`: password check, session cookies, Fernet encryption of
  the Instagram session.
- `static/`: the interface (`index.html`, `app.css`, `app.js`, `icon.svg`).
  The CSP allows only same-origin scripts and styles: no inline styles, no CDN.

## Local preview

```bash
pip install -r requirements.txt pytest
PYTHONPATH=. pytest -q tests
INSTAVAULT_DATA=/tmp/iv/data INSTAVAULT_MEDIA=/tmp/iv/media INSTAVAULT_SECRETS=/tmp/iv/secrets \
INSTAVAULT_COOKIE_SECURE=false HOST=127.0.0.1 PORT=18098 python -m vault.server
```

The media folder needs a `.instavault-volume` file containing `instavault-v1`,
and the secrets folder an `auth.json` (`username`, `salt`, `hash` =
sha256 of `salt:password`) plus a `secret` file of at least 32 random bytes.
Set `automatic` to false in the `settings` table when previewing with seeded
data, so the worker does not contact Instagram.
