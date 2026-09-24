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

Source only, LiveVault 3.4.0; active when `nsfw_enabled` and `nsfw_live_enabled`
(default on) and the models of the 3.3 section are present.
Code: `app/nsfw_live_worker.py` (tasks `nsfw-live`, `nsfw-verify` in
`WorkerManager`), `app/nsfw_live.py` (persistent helper processes),
`GrowingIndex` in `app/mp4_index.py`, tables `nsfw_marks` and `nsfw_coverage`
(created by `create_all`), columns `recordings.nsfw_source` and
`recordings.nsfw_live_coverage`, hooks in `_stitch_fragment_group` and
`_index_file` (`app/workers.py`), `nsfw_moments` per session in
`GET /api/control-room/pulse`, `live` block in `GET /api/nsfw`.

- Sampling: round-robin over active captures, `nsfw_live_fps` frames/s in
  total (default 0.5), one keyframe every `nsfw_step_seconds` of each capture.
  Only the fragments appended since the previous pass are parsed; one fragment
  (init + moof/mdat) is piped to ffmpeg and the small model in a `nice 19` /
  `ionice -c3` helper that stays loaded (~100 MB). It waits while the 1-minute
  load average exceeds `nsfw_live_max_load` (default 3.0 on 4 cores). Only
  fragmented `.mp4` captures are sampled live; other formats fall back to the
  full scan after the session. When sampling falls more than 60 s behind it
  jumps to the newest fragment and the gap is left to the full scan.
- Suspects (`nsfw_candidate`) are stored with part path, part time and wall
  clock; a cropped preview and a 640x640 copy are written to `/data/nsfw/`
  (internal drive). Since 3.4.1 every suspect keeps its preview until
  verified; kept marks then prune previews of later frames of the same part
  within 30 s (`_prune_preview`), so a moment keeps a picture even when its
  first frame is rejected. Pulse clicks on closed recordings load them via
  `GET /api/recordings/{id}`. The verifier runs the large model on that copy (helper
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
  Coverage >= 85% of the stitched duration → `nsfw_source=live`, status
  `verifying` until every mark is verified, then safe/review/nsfw without a
  full scan; lower coverage keeps `pending` for the full scan. Manual verdicts
  are never overwritten.

Verified locally: unit/integration tests with a real growing fragmented MP4
(quiesce pause, buffer continuation, verification during a switch, mapping
onto a two-part stitch), and a simulated live in the panel. Not yet run on
the node with real captures and the NudeNet models. Asset versions
`?v=3.4.1-live2`, SW cache `livevault-shell-v3.4.1-live2`.
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
