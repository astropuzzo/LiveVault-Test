# Live panel and short reconnect files

Verified 2026-09-09 via existing SSH access. Production image was
`ahul2vdjkyvjiwgzpcrmxzfe:607a151830f290240f4ce84f0a25be4471f96a1b`, healthy.
Authenticated status, sources and six-hour Pulse APIs responded. No capture was
active at inspection; source probes were current. No settings, production media,
database or dirty host checkout were changed. GPT Harness required reauthentication.

## Fixes in source

Branch `codex/live-panel-consistency`, based on production/main `607a151`.
Application paths: `app/static/app.js`, `ui.js`, `pulse-tuning.js`, `pulse-axis.css` (merged into `style.css` in 3.1),
`app/main.py`, `app/main/__init__.py`, `app/workers/size_policy.py`,
`app/workers/__init__.py`. Production runs these paths in the Coolify image;
the host Control Center is unaffected.

- Timeline requests share pending work; late responses cannot overwrite a newer
  range. Failed/malformed replies preserve the last valid data with an explicit
  stale notice. Initial requests follow authenticated boot. Displayed hours and
  timezone remain visible even if a range change fails.
- Public unrecorded gaps remain visible beside private/restricted intervals.
  Current/recent profiles sort first, and hidden rows can be expanded on phones.
  Colored bars use true elapsed width; separate invisible touch targets replace
  the minimum colored widths that exaggerated short intervals over long ranges.
  An unrecorded interval is not described as a creator never having been recorded.
  Date boundaries, unavailable files, loading/empty states and truncated history
  are explicit. The API's existing seven-day/1000-session limits are now ordinary
  source constants instead of runtime bytecode replacement.
- Preview failures leave a visible fallback. Archive covers are labeled as such.
  Timeline thumbnail failures retain playback actions; keyboard/Escape handling
  and narrow-screen controls are improved. Axis labels adapt to available width.
- Production files 344/345 shared a logical session: a 72.35-second, 38,043,248-byte
  reconnect prefix followed by a 2,043,566,742-byte file. Their combined size fit
  below the 2 GiB hard cap but exceeded the 95% target. Batching previously
  published the short prefix alone. Prefixes under two minutes and 10% of target
  can now join the next fragment if total size leaves 2% headroom. The finalized
  output is checked against the hard cap; an oversized join retries conservatively
  with originals preserved. Genuine short sessions and size-limited tails remain
  possible. Existing uploaded files are not rewritten or removed.

## Short captures in Cronologia (checked 2026-09-25, 3.4.12)

Symptom: rows alternating quickly between NON REC and IN ELABORAZIONE. The
Pulse draws every unstitched `recording_fragments` row as IN ELABORAZIONE at
`[mtime - duration, mtime]` and every hole > 12 s as NON REC, so a capture
that stops and restarts every minute looks exactly like this until the
15-minute batch is stitched. Evidence on the node (read-only queries on
`/data/livevault/livevault.db`, Control Center telemetry
`/var/lib/openastro-control/history.sqlite3`, capture ids in
`nsfw_coverage`/`nsfw_marks` part paths):
- Media lost inside stitched files (wall span minus duration) since
  2026-09-15 is < 2% for every source. The worst files all fall on
  2026-09-25 07:06–07:51 UTC, when telemetry shows CPU 91–99% for 42 minutes
  of back-to-back full NSFW scans (3.4.10 spinning helpers, 3 cores, every
  file queued because of the attach bug above): Top Twins lost 1084 of
  2027 s, youandi 171 of 1449 s. Outside that window losses match source-side
  stalls (network intake near 0, e.g. lodemure_ 2026-09-24 21:48) or private
  shows (AliciaBrooks 07:52–08:01, `live_sessions.access_status=private`).
- Top Twins (Chaturbate split LL-HLS, `transport_guard`) restarted its
  capture 26 times in 06:38–07:53 UTC (22 restarts < 3 minutes apart); every
  other session since 2026-09-24 has 1–3 captures. 06:48–07:05 ran at 45–55%
  CPU with steady network, so load alone does not explain that storm. The
  restart reason (`stream_transport_fault`, 35 s without growth) was only in
  memory and cleared by the next start: not recoverable.
- Excluded: storage switch (`storage-state.json` = `nvme` since 2026-09-23),
  buffer gate (`capture_allowed`, 178 GB free, no `.storage-buffer-full`),
  container CPU limits (`cpu.max` = max, `nr_throttled` 0), deploys (one,
  07:39 UTC).

Changes (3.4.12): the CPU fix in the NSFW section (no full scan beside
captures when live analysis is on, one core for every helper next to a
capture), and one container-log line per closed capture from
`capture_end_line()` in `app/workers.py`:
`[recorder] capture chiusa: <source> · <reason> · <seconds> s · <bytes> · exit <code> · <last stderr lines>`
(reason `riavvio: <transport fault>`; since 3.4.14 the stop reason LiveVault
itself set: `fermata manuale` for user actions through `stop_source`,
`cambio storage`, `buffer interno pieno`, `disco in emergenza`,
`buffer oltre limite`, `pausa globale registrazioni`, `arresto servizio`;
then `cambio parte` or `fine stream o errore`; URL query strings are cut).
Before 3.4.14 a user stop was logged as `fine stream o errore` (wasianbby,
2026-09-25 ~10:07 UTC, source removed by the user). Next storm:
`docker logs --since 2h <container> 2>&1 | grep 'capture chiusa'` with the
container from `docker ps --format '{{.Names}}'` (name starts with the
Coolify UUID). Also fixed: `/api/status` could fail with "dictionary changed
size during iteration" (`local_buffer_bytes`/`snapshot` now iterate a copy).
Rollback: revert the 3.4.12 commit and redeploy.

First cause seen with the new line (2026-09-25 09:48 UTC, tinnydoll,
Chaturbate): `fine stream o errore · 748 s · exit 0` with
`HTTP error 403 Forbidden` / `Failed to reload playlist`: the signed HLS URL
expired after ~12 minutes, ffmpeg ended cleanly and the poller started a new
capture at once (a new part and a short hole each time).

Segment length: `segment_minutes` was 120, so a Chaturbate part only closed at
the end of a capture or when the watcher's size check (`safe_stop_bytes`,
~1.9 GB, about an hour at 1080p) restarted ffmpeg, which leaves a hole. Set to
15 on 2026-09-25 09:50 UTC (user request, `PATCH /api/settings`, captures
started afterwards): the segment muxer cuts at a keyframe inside the same
process (no reconnect, no hole), `_watch_session` indexes every closed part,
`_stitch_group_ready` publishes each ~15-minute part as a recording, and its
live NSFW marks are attached then. That made one cloud file per 15 minutes
(143 files for 24 sessions by 2026-09-27); since 3.4.21 the parts of one
recording wait until they fill a file (`size_policy.fragments_fill_a_file`:
the stitch target, ~1.95 GB at `segment_max_gb` 2, or `MERGED_FILE_MAX_SECONDS`
= 2 h minus 60 s of slack for segment jitter) or the session goes quiet;
`bounded_fragment_batch` takes at most 2 h + 60 s of parts, the rest starts the
next file, and `nsfw_attach_stitched` moves every part's marks by the running
concat offsets (minute 10 of the fourth part = minute 55 of the file). While
the capture runs, closed parts of its logical session are `queued` in the
Pulse (red, "REGISTRATO · IN ATTESA DI UNIONE"), not IN ELABORAZIONE. Cloud files for Chaturbate become ~15
minutes long (Stripchat already was). First result: recording 1109 (tinnydoll,
901 s, 10:09–10:24 UTC) closed `nsfw` with `nsfw_source=live`, coverage 0.997,
no full scan. Side effect found at once and fixed in 3.4.15: the stall guard
summed only the parts still on disk, the stitched part was deleted while the
capture ran and 35 s later a healthy Chaturbate capture was restarted
(`riavvio: nessun dato HLS scritto · 1003 s · 48.8 MB`, 10:26 UTC).
`capture_bytes_written()` in `app/workers.py` now keeps a monotonic per-part
total and `capture_output_files()` tolerates a part deleted while listing.
Rollback: Settings → Registrazione → Segmento = 120 (and/or revert 3.4.15).

## Interface 3.1 (2026-09-24)

Source only, LiveVault 3.1.0: `app/static/style.css` (the only stylesheet),
`app/static/fonts/` (Mona Sans, SIL OFL, licence alongside), `app.js`, `ui.js`,
`pulse-tuning.js`, `workspace.js`, `index.html`, `sw.js`, `manifest.webmanifest`.
Runtime is the same LiveVault Coolify image; no API, database, storage or
setting change. The Control Center and NINA Monitor are untouched.

- Styling: `style.css` replaces the former `style.css` + `creator-hover.css` +
  runtime-injected `ui-fixes.css`, `dashboard-tuning.css`, `pulse-axis.css`,
  `mobile-fixes.css`. The merge was first verified style-identical (computed
  styles of every element in all four views at 1440/900/390 px), then rewritten
  as one token-based design system. `pulse-tuning.js` is a normal deferred
  script instead of being injected after `load`.
- Font: Mona Sans served from `/static/fonts/` (CSP allows only same origin).
  Proportional figures are used because its tabular zero is slashed.
- Behaviour: native `confirm()`/`prompt()` replaced by an in-app `<dialog>`
  (`lvDialog` in `app.js`); row menus close on outside click, Escape or after an
  action and only one stays open; periodic refreshes skip identical markup and
  defer view renders while a row menu is open; archive groups keep their
  open/closed state; the library "delete creator" action moved into the menu.
- Formatting: sizes, percentages, durations, relative times and chart dates use
  Italian formatting on the client (`humanBytes`, `bytesText`, `duration`, `ago`,
  `shortDate`); the API `*_human` strings are only fallbacks.
- Fixed: section title stuck on "Monitor", invisible hourly Live DNA bars,
  `.empty` padding deforming empty thumbnails/avatars, chart labels scaled to
  3–6 px, overlapping Analisi summary text, library status hidden under the star.

Verified only locally on synthetic data: Chromium at 1440, 900 and 390 px (touch),
automated overlap/overflow checks, scripted menu/dialog/refresh interaction
checks, full pytest and Node suites. Not checked against production data or on a
real phone. Asset versions and SW cache are superseded by 3.2 below.
Rollback: revert the 3.1.0 commits and redeploy LiveVault; nothing persistent
changes, and the SW cache name changes again on rollback deploys.

## Interface 3.2 glass and features (2026-09-24)

Source only, LiveVault 3.2.0. Same Coolify image and database schema; no new
dependency on the host (hls.js 1.7.2 is vendored in `app/static/vendor/`).

- Glass visual layer (`style.css`, section "Glass layer"): aurora background,
  frosted panels with `backdrop-filter`, fallbacks for browsers without blur and
  for `prefers-reduced-transparency`; animation stops with reduced motion.
- Seekable playback: `app/mp4_index.py` reads only box headers of fragmented
  MP4 captures and serves HLS byte-range playlists at
  `/api/recordings/{id}/stream.m3u8`, `/api/fragments/{id}/stream.m3u8`,
  `/api/sources/{id}/capture.m3u8` (409 when the file is not fragmented; the
  player then falls back to the direct file). CSP gains `media-src 'self' blob:`.
  Il candidato audit del 2026-09-29 aggiunge `app/live_capture_playlist.py` per
  la rotazione della capture: playlist scorrevole, sequenze monotone e URL dei
  byte legati alla singola parte. I file VOD conservano l'indice precedente;
  verifiche e limiti nella sezione audit qui sotto.
- 17 new providers in `app/source_providers.py` (TikTok, CamModels, SOOP, CHZZK,
  Bigo, Picarto, TwitCasting, DLive, VK Live, Huya, Douyu, YouNow, Showroom,
  17LIVE, MixChannel, Rumble, Niconico); capture still goes through yt-dlp/
  streamlink, so per-site success depends on those extractors.
- Real-time: `GET /api/events` (SSE, excluded from compression) emits `change`
  when a state fingerprint changes (checked every 2 s, stream closed after
  300 s, client reconnects); polling drops to 30 s while connected.
- Forecasts: `app/predictions.py` + `GET /api/predictions` (5-minute cache):
  168 hour-of-week bins over 90 days, 21-day half-life, neighbour smoothing and
  Bayesian shrinkage toward base rates; shown in "Prossime live previste".
- `features.js`: browser notifications (opt-in, stored in localStorage
  `livevault-notifications`), saved archive filters (`livevault-archive-filters`),
  forecast panel. Archive search accepts `creator:`, `stato:`, `>1gb`,
  `durata>30m`, `dal:/al:YYYY-MM-DD`, `oggi/ieri/settimana/mese`, `is:locale|cloud|problema`.

Verified locally on synthetic data only (Chromium 1440/390 px, HLS seek on a
real ffmpeg fMP4, SSE change event, full pytest/Node suites). Asset versions
`?v=3.2.0-glass1`, SW cache `livevault-shell-v3.2.0-glass1`.
Rollback: revert the 3.2.0 commits and redeploy; no persistent data changes
(only two optional localStorage keys in browsers).

## NSFW moment scan 3.3 (2026-09-24)

Source only, LiveVault 3.3.0, disabled by default (`nsfw_enabled=false`).
Code: `app/nsfw_scan.py` (scanner, separate process), `app/nsfw_worker.py`
(queue mixed into `WorkerManager`, task `nsfw-scan`), `app/static/nsfw.js`,
settings in `app/settings_store.py` (`nsfw_*`), columns `recordings.nsfw_*`
(added by `_migrate_recordings`, rows already deleted locally become `skipped`).
New image dependencies: `onnxruntime`, `numpy` (tests also use `onnx`).

- Models are not in the image. Copy NudeNet 3.4 `320n.onnx` and `640m.onnx` to
  host `/data/livevault/models/` (container `/data/models/`, eMMC, never the
  NVMe so an eject is not blocked). Missing models → state `models_missing`,
  nothing is scanned and deletions are not held.
- Pipeline: ffmpeg decodes keyframes only, one every `nsfw_step_seconds` of
  real pts (capture gaps are skipped, timestamps stay those of the file);
  small model on every frame, large model only on suspects (±1 frame) and,
  inside a confirmed stretch, once every 60 s (a fully explicit video costs
  minutes, not hours; hard cap 400 large-model frames per file, beyond that
  hits stay "da controllare"). Only the large model can mark NSFW.
- Timestamps refer to the same file that is uploaded (`recordings.local_path`
  → Gofile/Pixeldrain); `nsfw_file_sig` (size-mtime) makes a converted or
  repaired file rescan automatically. Preview JPEGs in `/data/nsfw/` are written
  by the scanner from the frame already in memory (3.3.1; the 3.3.0 re-seek
  exceeded 60 s on fragmented captures past ~40 min), so they survive the
  local file deletion.
- Runs at `nice 19` + `ionice -c3`, `nsfw_threads` cores (default 1); with
  `nsfw_only_when_idle` it pauses while any recorder is active. Storage
  quiesce (NVMe eject) kills the child at once and saves the position; the
  scan resumes from there. With `nsfw_hold_delete` an uploaded file is kept
  locally until scanned, at most `nsfw_max_hold_hours` (24) and never under
  disk pressure.
- API: `GET /api/nsfw` (state, live progress, counts, model presence),
  `POST /api/recordings/{id}/nsfw` (`rescan|skip|mark_safe|mark_nsfw`),
  `GET /api/nsfw/images/{id}-{t}.jpg`. Archive search `is:nsfw`, `is:safe`,
  `is:controllare`, `is:daanalizzare`; CSV export includes the moments.

Measured on the node (2026-09-24, benchmark venv in `/opt/nsfw-bench`, one
core, nice 19): small model 214–278 ms/frame, large model ~4.9 s/frame, peak
RSS 363 MB with both; 1 h 46 min capture scanned in 4 min. The leggings false
positive of the small model alone was rejected by the large one. Not yet run
inside the container or on a video with confirmed nudity. Asset versions
asset versions: see the 3.4 section below.
Rollback: set `nsfw_enabled=false` (instant, no restart) or revert the 3.3.0
commits and redeploy; the extra columns are ignored by older code, the
`/data/nsfw` previews and `/data/models` files can be deleted by hand.

## Live NSFW analysis 3.4 (2026-09-24)

Since 3.4.5 a frame reports every hot class scoring at least 0.3 and half of
the best one (`class_scores` in `app/nsfw_scan.py`), stored as "A+B" most
explicit first (`EXPLICITNESS`); moments and Monitor clusters keep the union.
Since 3.4.7 (verified 2026-09-24, QA server desktop 1440 px and phone 390 px)
pins are coloured by the most explicit part (`CLASS_CAT`/`catClass` in
`app/static/nsfw.js`, `.cat-*` rules at the end of `app/static/style.css`), the
Cronologia legend lists each category and dialogs show every part as chips
(`classChips`). 3.4.7 renames the categories (`CLASS_TEXT`) and redraws the
`nsfw-*` symbols in `app/static/icons.svg` (new `nsfw-anus`); 3.4.8 fixes the sprite XML (a malformed symbol hides every
icon; `tests/test_icons_sprite.py` guards it); 3.4.9 restyles the same symbols. Frontend only; rollback = revert the commit.


Source only, LiveVault 3.4.0; active when `nsfw_enabled` and `nsfw_live_enabled`
(default on) and the models of the 3.3 section are present.
Code: `app/nsfw_live_worker.py` (tasks `nsfw-live`, `nsfw-verify` in
`WorkerManager`), `app/nsfw_live.py` (persistent helper processes),
`GrowingIndex` in `app/mp4_index.py`, tables `nsfw_marks` and `nsfw_coverage`
(created by `create_all`), columns `recordings.nsfw_source` and
`recordings.nsfw_live_coverage`, hooks in `_stitch_fragment_group` (the
production one is the override in `app/workers/__init__.py`, wrapped by
`size_policy` and `app/main/__init__.py`; the legacy copy in `app/workers.py`)
and `_index_file` (`app/workers.py`), both through `nsfw_attach_stitched` /
`nsfw_attach_parts`, `nsfw_moments` per session in
`GET /api/control-room/pulse`, `live` block in `GET /api/nsfw`.

- Sampling: round-robin over active captures, `nsfw_live_fps` frames/s in
  total (default 0.5), one keyframe every `nsfw_step_seconds` of each capture.
  Only the fragments appended since the previous pass are parsed; one fragment
  (init + moof/mdat) is piped to ffmpeg and the small model in a `nice 19` /
  `ionice -c3` helper that stays loaded (~100 MB) and, like the verifier, runs
  on one core whatever "Core CPU" says (3.4.12). `ionice -c3` has no effect on
  the node NVMe (`sdb` uses `mq-deadline`, checked 2026-09-25). Since 3.4.13
  the sampler ignores the load: only the verifier waits while the 1-minute load
  average exceeds `nsfw_live_max_load` (default 3.0 on 4 cores; 3.1 on the
  node). Measured 2026-09-25 on 3.4.12: the node load counts USB I/O (1.6–2
  with nothing recording) and the stitch of a 1.9 GB part plus a capture restart
  pushed it to 5; the sampler paused, wasianbby's recording 1106 ended at 73%
  coverage and was queued for a full scan. With two captures the sampler uses
  ~15–30% of one core at nice 19. Only
  fragmented `.mp4` captures are sampled live; other formats fall back to the
  full scan after the session. When sampling falls more than 60 s behind it
  jumps to the newest fragment and the gap is left to the full scan.
- Suspects (`nsfw_candidate`) are stored with part path, part time and wall
  clock; a cropped preview and a 640x640 copy are written to `/data/nsfw/`
  (internal drive). Since 3.4.1 every suspect keeps its preview until
  verified; kept marks then prune previews of later frames of the same part
  within 30 s (`_prune_preview`), so a moment keeps a picture even when its
  first frame is rejected. Pulse clicks on closed recordings load them via
  `GET /api/recordings/{id}`.
- Phones (<= 620 px, 3.4.2): `pulse-tuning.js` wraps scale and rows in a
  horizontal scroller (170 px/h up to 24 h, 60 px/h up to 3 days, 28 px/h
  beyond), names sticky, scroll kept across refreshes; pins closer than 26 px
  at the real width are grouped with a count (`openPulseGroup` in `nsfw.js`). The verifier runs the large model on that copy (helper
  closed after 2 idle minutes, ~300 MB while active), inheriting a
  confirmation for 60 s inside a confirmed stretch; copies are deleted after
  verification, previews of rejected frames too.
- Storage handoff: `quiesce` pauses sampling (each fragment read is counted
  as a storage job, so the detach waits at most one read); `buffer` and
  `nvme` both sample (the capture path is the same, on the internal buffer or
  on the NVMe). Verification never reads recordings and runs in every mode.
  The full-file scan (3.3) also runs on buffer files now; files left on the
  detached NVMe stay queued.
- Mapping: at stitching, part offsets are the container durations in concat
  order (same as the concat demuxer), so `file_time = offset + part_time`.
  Each live sample credits the time since the previous one up to
  `MOMENT_GAP_FACTOR` (2.5) x step, the tolerance that already merges hits
  into one moment (3.4.12; before 1.5 x step, so four captures sharing 0.5
  fps could never reach 85%).
  Coverage >= 85% of the stitched duration → `nsfw_source=live`, status
  `verifying` until every mark is verified, then safe/review/nsfw without a
  full scan; lower coverage keeps `pending` for the full scan. Manual verdicts
  are never overwritten.
- Decision rule since 3.4.16 (user request 2026-09-26: the small model is
  usually right and re-checks were far too many): small-model score >=
  `nsfw_threshold` is final (`judge`, live mark `confirmed` at once); only
  [`nsfw_candidate`, `nsfw_threshold`) goes to the 640m (`needs_verify`, live
  mark `pending`), which confirms or discards; candidate >= threshold means no
  re-check at all. No neighbour re-checks, no new `review` verdicts (old ones
  stay). Inside an NSFW stretch uncertain frames continue it without the 640m.
- Bands (`stretches` in `app/nsfw_scan.py`, `cluster_marks`), since 3.4.16:
  a band lasts while checked frames stay NSFW; since 3.4.17 it ends at the
  first clean frame only when no NSFW frame follows within
  `BAND_HOLD_SECONDS` (60 s): single clean samples inside an explicit stretch
  (pose, framing) had split it into dashes. Replayed on the node marks of
  2026-09-26: tinnydoll 102 → 17 bands, mollybabyx 51 → 6 (30 s gave 37 and
  9). With nothing checked for longer than the sampling-gap limit (60 s for
  scans, whose hits only keep the first clean frame after a moment;
  `LIVE_MAX_GAP_SECONDS` = 90 live) a band ends one step after its last NSFW
  frame. The full scan stores that first clean frame in `nsfw_hits` (label
  ""); the live sampler writes one `clear` mark per NSFW→clean transition
  (`LiveTrack.last_nsfw`), and `rejected` marks also end bands. The Pulse
  query reads `clear`/`rejected` too. Drawing (`pulseNsfwLayer` in
  `app/static/nsfw.js`): bands less than 6 px apart at the current scale are
  drawn joined. Phone legend: `.nsfw-legend` no longer wraps into a 117 px
  column. Layout since 3.4.20 (user request: "the recording bar becomes the
  symbols"; 3.4.18 pins + lane and the 3.4.19 strip under the bar were both
  rejected as unreadable): tracks are 22 px again. `pulseNsfwLayer` in
  `app/static/nsfw.js` draws, above the bar and the hour grid, one
  `.nsfw-bar-run` button per stretch and recording state, 22 px tall,
  holding a row of 16 px symbols (`.nsfw-glyph g-vulva|phallus|anus|breast|
  butt|other`, data-URI SVG masks with a 1.1 aspect ratio, allowed by CSP
  `img-src data:`, painted in the colour of the recording underneath:
  `s-rec` red = recording or on disk, `s-cloud` green, `s-processing` blue;
  same precedence as the bar in app.js: processing > cloud > red; outside
  every file the nearest file's colour). Moments closer than `JOIN_PX` = 6 px
  make one stretch; a stretch is at least `GLYPH_PX` = 24 px (one symbol,
  centred on the moment) and stretches that then overlap are joined. The
  stretch is split into slices of `GLYPH_PITCH` = 20.6 px: each symbol shows
  the part seen longest in its slice (a moment counts as its most explicit
  part; ties go to the more explicit; a slice between moments takes the
  nearest), so zooming out keeps "Tette, Cazzo, Tette" instead of turning a
  whole stretch into its most explicit part (seen on shawtywiththedick at
  24 h). A piece of another state narrower than one symbol is merged,
  narrowest first, into its wider neighbour, so the colour covering most of
  the stretch wins. The glyphs come from the icons in `icons.svg` (shading
  kept as alpha: full, 45%, cut) via `scripts/nsfw-glyphs.py`; a test checks
  the stylesheet matches the generator. Click/tap: the moments of that stretch within 8 px
  of the pointer, else the nearest one (one → moment, several → list);
  keyboard Enter → all moments of the stretch. Mouse hover: `.nsfw-scrub`
  hairline and `.nsfw-scrub-tip` (fixed, above the track) with time and
  parts. The rest of the REC bar keeps its preview and link. Legend: the NSFW
  key comes from `window.pulseLegendExtra`, called by pulse-tuning every time
  it rebuilds the legend (before, a resize dropped it, e.g. on phones).
  Motion: `animateNewPulseItems` adds `enter` after rendering (never in the
  markup) and stores when each piece first appeared; a piece rebuilt
  mid-entrance gets a negative `animation-delay` and resumes (the Pulse is
  often re-rendered ~20 ms after the first render, which cut 3.4.19's
  entrances). First display of a row: one left-to-right sweep (delay by tenth
  of the track); a stretch ending within 2 min of the Pulse time in an open
  session gets a `nsfw-ping` dot at its end; `prefers-reduced-motion`
  disables all of it. QA 2026-09-27 with Playwright (system Chrome, 2x) on a
  local instance with the Pulse tables exported from the node (profiles,
  sources, live_sessions, recordings, fragments, nsfw_marks of the last 26 h;
  cloud URLs replaced by a placeholder, no settings, secrets or media; one
  file made local and one fragment added to see red and blue; a live stretch
  injected client-side): desktop 1440 px 6/12/24 h, hover, click, keyboard,
  mid-entrance frame, live ping, phone 390 px with tap. The export was
  deleted after the QA.
  Rollback: revert 3.4.17 (and 3.4.16); `clear` marks are ignored by older code.
- Partial live coverage (3.4.22, user report on tinnydoll 2026-09-27): joined
  1 h files covered 64-83% stayed `pending` with no moments although their
  live marks were attached with the right `file_time`, because
  `nsfw_attach_parts` closed a file as `live` only at `LIVE_COVERAGE_OK`
  (0.85) and the full scan only runs with no capture. Two causes, two fixes:
  the coverage credit per sample gap was 2.5 x step = 10 s while a few lives
  sharing `nsfw_live_fps` on the CM4 are sampled every ~12 s (average 0.77
  since 2026-09-25), now `COVER_GAP_SECONDS` = 30 (bands hold 60 s, so a
  sample every 30 s cannot miss one); and a file with at least one NSFW live
  mark is closed as `live` whatever its coverage. Below 0.85 it
  `needs_full_scan` (`app/nsfw_worker.py`): `nsfw_hold_blocks_delete` keeps
  the local copy (up to `nsfw_max_hold_hours`), `_next_nsfw_job` picks it
  after the pending files (status back to `pending`, live moments kept until
  the scan replaces them with `nsfw_source=scan`), and the dialog says the
  moments come from N% of the file. No NSFW mark and partial coverage: still
  `pending` (calling it safe would be a guess). `nsfw_publish_live_left_pending`
  (run once when the verifier loop starts) closes files left `pending`, or
  `skipped` because the local copy went, that already carry NSFW live marks;
  user exclusions/cancellations are left alone.
  3.4.23 (user decision: what was analysed live is not analysed again):
  `needs_full_scan`, its queue tier in `_next_nsfw_job` and its hold were
  removed; a file with NSFW live marks is final whatever its coverage, one
  with none is `safe` from `LIVE_COVERAGE_OK` = 0.5 (the dialog says "visto
  dal vivo sul N% del file"), below it stays `pending` for a full scan that
  only happens while a local copy exists. `nsfw_hold_delete` was set to false
  on the node, so local copies go right after the verified upload.
- Stripchat is sampled on the raw `<stem>.capture.mp4` but indexed/stitched
  as the remuxed `<stem>.mp4` (`_remux` in `app/stripchat_capture.py`,
  `-start_at_zero`, same timeline); `live_aliases()` (3.4.10) matches both
  names and `_run_nsfw_job` re-tries the match before a queued full scan.
- Real cause of "every recording is fully re-scanned" (found on the node
  2026-09-25, fixed in 3.4.12): the production `_stitch_fragment_group`
  override in `app/workers/__init__.py` never called the attach hook (only the
  legacy copy did), so from 3.4.0 to 3.4.11 all 1669 live marks kept
  `recording_id` NULL, every recording had `nsfw_live_coverage` 0 and
  `nsfw_source=scan`, and `nsfw_coverage` rows were never consumed. 3.4.10
  alone could not help. The override now measures part durations before the
  concat and calls `nsfw_attach_stitched` right after inserting the
  recording, before any await (the full-scan queue cannot grab it first).
  `tests/test_v3412_nsfw_live_stitch.py` runs that production path.
  Recordings stitched before 3.4.12 keep their scan results; their live marks
  and stale `nsfw_coverage` rows stay orphaned (harmless, not backfilled).
  3.4.14: the 3.4.10 re-match in `_run_nsfw_job` only runs while
  `nsfw_live_coverage` is 0 (it matched the stitched name, found nothing and
  reset recording 1106 from 0.732 to 0 on 2026-09-25; that value was not
  restored). Full scans also wait `STARTUP_GRACE_SECONDS` (90 s) after a
  container start when they would yield to captures: right after the 3.4.13
  deploy a scan started before the poller had resumed tinnydoll and was paused
  seconds later.
- CPU next to captures. 3.4.11 (no onnxruntime spinning, NSFW ffmpeg
  `-threads 1`, `helper_env()` with OMP/OpenBLAS/MKL = 1) was live on the node
  on 2026-09-25 but the scan helper still took 120–207% (runtime setting
  `nsfw_threads=3`) + 25–70% ffmpeg, load 5–6, with
  `nsfw_only_when_idle=false`. Every file was queued (bug above), the scan
  load kept the 1-minute load over `nsfw_live_max_load`, the sampler stopped
  (15% and 12% coverage on the two parts measured) and the next file was
  queued again. From 3.4.12 (`_nsfw_waits_for_recorders`, `_nsfw_threads` in
  `app/nsfw_worker.py`): with live analysis on, the full scan waits while any
  capture is active (UI "In pausa: registrazione attiva") and resumes from its
  checkpoint afterwards; without live analysis "only when idle" still decides,
  and a scan beside a capture runs on one core (a 3-core scan is stopped and
  restarted on one core when a capture starts). "Core CPU" applies only to
  full scans with nothing recording. Check with `top -o %CPU` while
  recording: no `app.nsfw_scan`, `app.nsfw_live` helpers <= ~100%.
  Rollback: revert the 3.4.12 commit and redeploy (no schema change); the
  settings are untouched.

Verified locally: unit/integration tests with a real growing fragmented MP4
(quiesce pause, buffer continuation, verification during a switch, mapping
onto a two-part stitch), and a simulated live in the panel. Not yet run on
the node with real captures and the NudeNet models. Asset versions
`?v=3.4.7`, SW cache `livevault-shell-v3.4.7`.
Rollback: untick "Analizza durante la registrazione" (instant), or revert the
3.4.0 commit and redeploy; the two new tables and columns are ignored by older
code; `/data/nsfw/live-*.jpg` and `/data/nsfw/verify/` can be deleted by hand.

## Gofile link per recording (2026-09-24)

Gofile has no share page for a single file: the upload response's
`downloadPage` is the containing folder, so every recording link opened the
creator/day folder. From 3.3.0, when `gofile_subfolder_per_video` is enabled (off by default since
3.3.1: the first deploy created subfolders named after the internal capture
file, `…partNNN.capture`, next to the loose files of the same day), the
uploader creates a public subfolder named after the uploaded file inside the day
folder (`WorkerManager._gofile_file_folder`, `app/workers.py`) and uploads
into it: `recordings.remote_url` is that subfolder (only this video),
`remote_parent_url` stays the day folder, `remote_folder_id` stores the
subfolder id. The same subfolder is reused on retries; if creating it fails
the file goes to the day folder as before (error under
`last_errors["gofile-file-folder:<id>"]`). "Crea cartella Gofile" moves the
subfolders, not the files. Files uploaded before this change keep the day
folder link (not migrated). Pixeldrain already links each file.
Subfolders already created stay on Gofile; move or delete them by hand.
Rollback: untick the option in Settings → Gofile (no restart).


Targeted Python/Node regression tests cover request races, malformed/stale replies,
timeline gaps, mobile expansion, missing media, date boundaries, short-prefix
batching and maximum-size enforcement. Linux/Python 3.13 CI passed on `b8f3033`
(GitHub run `34335064487`), including the container build/smoke test. Final touch
focus follow-up preserves the playback button while moving focus into its card
and makes hidden preview controls inert; all 15 Node tests pass locally.
Run CI on the final branch tip before promotion, as in HOSTING.md.
Browser attachment was unavailable during
initial inspection; a live preview cannot be sampled without an active capture.

These changes are not a production deployment. Merge only after green CI and
review. Rollback is the preceding Coolify image `607a151`; no migration is added.
Do not reset the dirty checkout at `/mnt/livevault-nvme/gpt-harness/work/LiveVault-Test`.

## Audit player e richieste asincrone (candidato 2026-09-29)

Sorgenti: `app/static/app.js`, `app/live_capture_playlist.py`, `app/main.py`,
`app/mp4_index.py`, `app/workers.py`, `app/storage_response.py`.
Stati AWAY e claim scanner: `app/source_providers.py`,
`app/stripchat_state.py`, `app/nsfw_worker.py`; verifica sorgente 2026-09-30.
Baseline del clone audit: GitHub `main` `5fddda9`.
Le verifiche riportate qui sono locali sul candidato; questa sezione non
attesta il deploy o la continuità di una capture sul nodo.

- Il player assegna una generazione a ogni apertura/chiusura e annulla la
  richiesta playlist precedente. Una risposta tardiva o il caricamento
  ritardato di hls.js non riapre un player chiuso né sostituisce il video
  successivo. Anche gli errori HLS restano legati alla propria istanza.
- I refresh periodici/SSE condividono la richiesta in corso. Le richieste
  esplicite dopo un'azione accodano una sola nuova passata, conservando
  `includeRecordings=true` se almeno un chiamante lo richiede; i chiamanti
  attendono anche quella passata. I tick periodici non prolungano una
  richiesta lenta o fallita con una coda permanente.
- Le statistiche accettano subito l'ultimo periodo selezionato; richieste
  identiche condividono il lavoro e risposte/errori superati non ripristinano
  un periodo precedente. Il profilo carica i dati in uno snapshot locale;
  apertura di un altro profilo o chiusura della finestra invalidano le
  risposte precedenti, comprese le statistiche e i reload dopo salvataggio.
- La capture usa gruppi su keyframe da 2 s sia mentre una parte cresce sia
  dopo la chiusura. Prima del fix, 12 s di file pubblicavano cinque gruppi
  live da 2 s che alla rotazione diventavano due gruppi da 6 s mantenendo
  `MEDIA-SEQUENCE:0`; gli stessi numeri di segmento indicavano altri byte.
  Ora i gruppi già annunciati conservano range, durata e URL. Il target HLS
  è fisso per URI di playlist (almeno 3 s). Se un GOP successivo supera il
  target, la route redirige alla nuova `capture.m3u8?epoch=<id>` con un
  target adeguato, mantenendo range e posizioni già pubblicati; i loader
  HLS seguono il redirect senza una risposta 409 permanente. Gli indici
  live e finiti invalidano anche per device/inode cambiati, compresa la
  sostituzione con un file più grande o con dimensione/mtime identici.
- Ogni URL `capture?part=<token>` è autenticato e lega source, directory di
  sessione, percorso risolto e identità device/inode. La route ricontrolla
  la radice delle registrazioni e passa l'identità registrata a
  `StorageFileResponse`, che la verifica con `fstat` sul descrittore
  effettivamente aperto e serve i byte dallo stesso handle. Quindi
  range HTTP e cancellazione durante il cambio storage restano attivi.
  Un token sconosciuto, di un'altra source, per un file sostituito/eliminato
  o per un percorso esterno restituisce 404; non legge la nuova parte attiva.
  L'identità resta fissata anche durante indicizzazione e pubblicazione:
  una sostituzione atomica fra le due fasi non lega range vecchi al nuovo file.
- Remux/stitch/upload continuano a rimuovere le copie previste. La playlist
  non dichiara `EVENT`: quando sparisce un prefisso avanza `MEDIA-SEQUENCE`
  e `DISCONTINUITY-SEQUENCE`, conservando il significato dei segmenti
  rimasti. La timeline disponibile copre le sole parti ancora riproducibili;
  per Stripchat il remux finale non frammentato non aggiunge storia HLS.
  Nessun video viene trattenuto per il player. Le cache sono in memoria:
  massimo 16 sessioni, 256 parti ricordate per sessione e 2048 token;
  deploy/evizione richiedono la riapertura del player, senza effetti sui dati.
- Upload immediato e retry rispondono 409 `recording_busy` mentre il file
  viene caricato, convertito o eliminato. Recupero, conversione, verifica
  integrità e cancellazioni condividono l'esclusione per registrazione;
  anche le pulizie in blocco la rispettano. L'integrità reclama
  temporaneamente il file e ripristina lo stato precedente su eccezione.
  Le transizioni API, uploader e repair usano compare-and-set, così una
  lettura precedente non sovrascrive un claim successivo. La cancellazione
  locale reclama `deleting` prima dell'unlink anche per una copia già
  caricata e ripristina `uploaded` solo dopo la rimozione dei byte; il link
  cloud resta disponibile. Cancellare la sola voce senza il file usa lo
  stesso claim e non può rimuovere la riga di un upload appena iniziato.
  La rimozione automatica dopo upload e il worker miniature usano claim
  condizionali: un recupero o una cancellazione vincenti impediscono
  l'avvio di un nuovo lettore sul file. Un errore di unlink rilascia il claim.
  La coda NSFW esclude `converting`/`deleting`; lo scanner reclama `scanning`
  con compare-and-set prima di aprire il processo helper. Conversione,
  recupero, verifica integrità e cancellazione rispettano quel claim.
  Un errore/annullamento nell'avvio o nella lettura dell'helper rilascia
  l'analisi in `paused`, conservando progressi e file.
  All'avvio, con storage online, un `deleting` interrotto viene riconciliato
  senza cancellare altri byte: `uploaded` con ricevuta cloud esistente,
  altrimenti `discarded`, con `local_deleted` basato sulla presenza reale.
  Con storage offline resta occupato fino a un avvio con storage online,
  per non confondere un disco scollegato con un file eliminato.
  Il repair espone
  l'ownership in `_repairing_recordings`: un `converting` realmente attivo
  resta occupato, uno lasciato da un processo interrotto resta recuperabile.
  Il frontend mostra il messaggio del 409 strutturato. Il registro delle
  operazioni manuali è locale al singolo processo uvicorn del Dockerfile;
  più processi API richiederebbero un lease condiviso per distinguere un
  vecchio `converting` da un'operazione manuale su un altro processo.

Test locali Python 3.13 su Windows con FFmpeg 7.1: 83 passati nel gruppo
player/concorrenza/indici/storage, worker, receipt, libreria e contratti UI;
test Node del frontend: 29 passati. Regressioni in
`tests/test_live_capture_rotation.py` (rotazione, eliminazione, range HTTP,
vincoli dei token, percorso esterno e limiti cache),
`tests/test_panel_frontend.cjs` (risposte invertite, chiusura, cambio profilo,
refresh dopo mutazione) e aggiornamento del contratto in
`tests/test_v3424_performance.py`; le operazioni concorrenti e il recovery
del `converting` abbandonato sono verificati in
`tests/test_recording_action_concurrency.py` (claim prima dell'unlink,
CAS contro upload/recupero/miniature, rimozione della sola voce e rilascio
su errori), insieme ai test SQLite in `tests/test_thumbnail_queue.py`.
La cronologia verifica anche il passaggio live → away → tipjar → live,
con intervalli distinti, in `tests/test_worker_monitoring.py`.
Verifica aggiuntiva del 2026-09-30: 52 test passati nel gruppo
`test_recording_action_concurrency.py`, `test_thumbnail_queue.py` e
`test_worker_monitoring.py`, inclusi i claim di cancellazione e l'avvio
con operazioni interrotte; questo gruppo non richiede FFmpeg.
La CI Linux e la prova browser con capture
reale devono ancora confermare il candidato.
Validazione finale locale 30 settembre: 512 test passati, 11 saltati nel gruppo
che esclude cinque file di test dipendenti da host/mount Linux; controllo versione
3.4.31 incluso. Successivo gruppo scanner/claim/versione: 49 passati, un test
ONNX saltato; copre sia file reclamato dopo la scelta della coda sia helper
non avviabile e protezione dei byte durante l'analisi. La CI esegue tutti i file.
AWAY resta distinto da TIP-JAR nei nuovi intervalli e nella UI; gli intervalli
storici non sono riclassificati. Entrambi sospendono la capture fino al ritorno
pubblico. QA delle etichette su fixture esplicita a 1440×1000 e 412×915;
la segnalazione ivyquinette e i limiti sono nel verbale audit.
Rollback: revert del commit audit e redeploy dell'immagine precedente;
nessuna migrazione, modifica alle impostazioni o riscrittura di media/DB.

## Playlist della capture live 3.4.29 (verifica locale 2026-09-29)

Sorgente: `app/mp4_index.py` (`fragment_starts_with_keyframe`, `GrowingIndex`,
`LivePlaylistIndex`, `build_index`) e `app/main.py` (`stream_active_capture`).
Runtime verificato: checkout Windows del candidato e container Coolify CM4
`a6fbbbc` healthy dopo il merge; sulla capture reale resta il difetto `sidx`
descritto nella sezione 3.4.30.
La release 3.4.29 aggiorna anche la versione del service worker e i parametri
di cache degli asset in `app/static/index.html`.

La capture Stripchat osservata aveva frammenti di circa 0,5 s, ma solo circa un quarto
iniziava con un keyframe. La playlist 3.4.28 esponeva ogni frammento come segmento
indipendente. Il nuovo indice live legge in modo incrementale solo i box completi
aggiunti al file e pubblica gruppi chiusi di almeno 2 s, ciascuno avviato su
keyframe. Anche l'indice dei file finiti raggruppa senza iniziare un segmento a
metà GOP. La parte finale aperta non viene annunciata; la durata disponibile può
restare indietro fino al keyframe successivo.

Test locale: MP4 generato con ffmpeg a 24 fps, frammenti da 0,5 s e keyframe ogni
2 s. Ventiquattro frammenti danno sei keyframe e cinque segmenti live chiusi da
2 s; il test dell'endpoint conferma che tutti i byte range partono sui keyframe.
Un file da 120 s ha 240 frammenti, 60 keyframe e 59 segmenti live chiusi; su
questo PC l'aggiornamento senza nuovi byte ha richiesto circa 0,08 ms. Passati
27 test mirati, inclusi crescita per append, prefisso invariato della playlist
e ricreazione di un file più corto. La capture AliciaBrooks precedente non era
più presente in `/data/livevault/recordings` al momento della nuova verifica.
CI Linux/Python 3.13 sul commit `1da3721`: 468 test passati e controllo della
documentazione operativa verde (run PR `36542182223`).
La misura sul nodo e il seek nel browser richiedono il follow-up 3.4.30.

Limite: se i keyframe non coincidono con l'inizio di alcun frammento successivo,
la parte live non produce nuovi segmenti HLS; il player può ricadere sulla
riproduzione diretta. Rollback: revert del commit 3.4.29 e redeploy 3.4.28;
nessuna migrazione di database, file o impostazioni.

## Box sidx fra frammenti live 3.4.30 (verifica 2026-09-29)

Sorgente: `app/mp4_index.py` (`build_index`, `LivePlaylistIndex.snapshot`),
`tests/test_v3424_performance.py`. Runtime controllato: container Coolify 3.4.29
`a6fbbbc` sul CM4, capture attiva AliciaBrooks `part001.capture.mp4` nella
directory `/data/recordings/AliciaBrooks/AliciaBrooks_2026-09-29_03-28-59/`.
La prima lettura aveva 192 frammenti da 0,5 s, 48 keyframe e 191 segmenti live:
il fix 3.4.29 non era efficace per quel file. Fra due coppie `moof`/`mdat` ci
sono due box `sidx` da 52 byte; il divario di 104 byte attivava il taglio prima
del controllo keyframe. La 3.4.30 estende il byte range attraverso
quei box e taglia solo quando arriva il prossimo keyframe dopo almeno 2 s.

Test locale su MP4 audio/video prodotto con `-frag_duration 500000` e
`-movflags empty_moov+default_base_moof+dash`: due `sidx` tra frammenti,
segmenti avviati su keyframe e decodifica ffmpeg di un segmento centrale.
Passati 83 test mirati su Windows nell'intervento completo 3.4.30. PR #40 e CI
`main` run `36597522780` verdi; immagine `0242aa3` healthy sul nodo. Sulla
capture attiva mollybabyx il 2026-09-29: 73 frammenti, 24 segmenti live,
tutti con keyframe iniziale e nessuno sotto 2 s. Non era attiva una capture
Stripchat durante la verifica: seek nel browser su una nuova live Stripchat
ancora da osservare. Rollback: immagine 3.4.29 `a6fbbbc`, nessuna migrazione.
