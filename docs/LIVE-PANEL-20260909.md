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
live NSFW marks are attached then. Cloud files for Chaturbate become ~15
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
  column. Layout since 3.4.18 (user: "compressed, not readable"): rows with
  NSFW moments get a lane under the session bar
  (`.cr-pulse-track:has(> .nsfw-pulse-layer)`, height `32px + --pin`, `--pin`
  18 px desktop / 20 px phone) so nothing covers REC/ONLINE and phones
  (track `overflow: hidden`, bands hidden 3.4.3–3.4.16) show it. Map-pin
  layout: pins on top with a tip (`::after`) at the group's first moment,
  bands below on a faint rail, coloured by the most explicit part (`cat-*`);
  a second part is a two-colour conic ring (`multi cat2-*`) instead of the
  stacked `nsfw-pin-extra` icon (still used by the file timeline). Motion
  (CSS only, CSP `style-src 'self'`): `enter d0..d8` classes on first
  appearance only (`seenBands`/`seenPins` in `nsfw.js`, the dashboard
  re-renders every 8 s): bands `nsfw-band-draw` (scaleX, expo-out), pins
  `nsfw-pin-pop` (drop with bounce, `animation-fill-mode: backwards` so hover
  scale still works); a band ending within 2 min of the Pulse time in an open
  session is `live` (`nsfw-band-flow` light flow + `nsfw-comet` head);
  hovering/focusing a pin adds `.hot` to its bands and `.focusing` dims the
  rest. `prefers-reduced-motion` disables all of it. QA 2026-09-26 on a local
  instance seeded with the node marks (desktop 1024/1440 px, phone 375 px).
  Rollback: revert 3.4.17 (and 3.4.16); `clear` marks are ignored by older code.
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
