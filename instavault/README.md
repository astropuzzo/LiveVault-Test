# InstaVault

Private archive of the Instagram profiles the user picks: posts, carousels and
Reels, plus stories and highlights when an Instagram session is connected. Pure
Python standard library HTTP server, instaloader for Instagram, SQLite, no
frontend framework. Operations, paths and rollback live in
[HOSTING.md](../HOSTING.md#instavault).

## Layout

- `vault/server.py`: HTTP API, static files, media with byte ranges, its own
  login from `auth.json`, signed cookie plus CSRF token.
- `vault/sync.py`: one background worker with a file lease, resumable cursors,
  deduplication by Instagram id, downloads only from Instagram CDN hosts, quota
  and free-space checks before every chunk. Without the media marker it writes
  to the eMMC buffer (2 GiB, stops below 5 GiB free on the eMMC) and drains it onto the NVMe with
  a verified copy when the disk comes back.
- `vault/store.py`: SQLite schema and queries; media paths stay inside `/media`,
  or inside `/data/buffer` while the NVMe is away (`media.location`).
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

## Instagram sign-in

Password login uses instagrapi's mobile flow in a bounded background attempt,
including email/SMS codes and 2FA. Settings polls the authenticated attempt state
and supports cancelling it. Browser cookie imports use authenticated web account
data, not the legacy GraphQL login probe. Rate limiting, rejected sessions,
manual verification and network failures have distinct messages. Passwords and
codes are not persisted; the reusable Instagram session is encrypted. Mobile-authenticated sessions keep the same mobile transport for downloads;
browser sessions keep the web transport. The selected photographer's real login
and first three-photo carousel were verified on 2026-11-02. The full initial
archive continues in bounded, resumable passes.
Rollback and deployment details: [HOSTING.md](../HOSTING.md#instavault).

## Archive layout

The archive opens on one card per profile. Inside a profile, highlights are
circles pinned under the header, as on Instagram; a tap plays the collection
story after story. Posts, Reels and stories are tabs below them.
"Scarica tutto" streams a ZIP of the profile's originals
(`/api/profiles/<id>/download`, optional `collection`), built while it is sent
with no temporary copy; files out of reach (NVMe away) go in `mancanti.txt`.

Posts and Reels keep the newest page of their comments (about 15, one mobile
request per post) in `comments`; `posts.comments_at` marks the read. New posts
get them when archived, older ones twelve per check. A refusal stops comments
for that pass and never the media download. In the viewer a commenter's name
opens a menu: the Instagram profile, and the archived profile or the add form.

Activity shows thumbnails of what each update downloaded (`posts.job_id`).
Deletions are read from what a check fetches anyway, never from extra
requests: a pass over the posts that starts from the newest one marks
`posts.deleted_at` on archived posts and Reels newer than the oldest item it
listed but missing from it; a story still inside its 24 hours that the story
list no longer has is marked the same way. An item that shows up again is
restored. Older archive entries are never checked. Marked items carry a bin
icon and the Cancellati view lists them. A private profile the connected account cannot
see is marked `access='requested'` or `'not_followed'` and skipped without an
error; "Chiedi di seguire" sends the follow request (mobile sessions only).
Instagram errors are named in Italian with Instagram's own words when the
reply carries any; the class and that text are printed to the container log.

A post that two archived profiles both publish (a collab or a repost) is
downloaded once. The first copy owns the files and the `shared` table lists
it under the other profile; both grids mark it as shared. Duplicates archived
before 2 November 2026 were merged the same way at startup; their extra files
stay on disk and are no longer referenced.

Stories keep their tappable areas in `posts.links` (JSON: url, label and the
box as fractions of the image) from link stickers and @mentions. The viewer
places them over the story and lists them beside it. Highlights link to
their stable Instagram address; a plain story's link is hidden after 24 hours,
when Instagram drops it. Highlights use the v1 mobile calls only.
