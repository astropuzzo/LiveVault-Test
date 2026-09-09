# Live panel and short reconnect files

Verified 2026-09-09 via existing SSH access. Production image was
`ahul2vdjkyvjiwgzpcrmxzfe:607a151830f290240f4ce84f0a25be4471f96a1b`, healthy.
Authenticated status, sources and six-hour Pulse APIs responded. No capture was
active at inspection; source probes were current. No settings, production media,
database or dirty host checkout were changed. GPT Harness required reauthentication.

## Fixes in source

Branch `codex/live-panel-consistency`, based on production/main `607a151`.
Application paths: `app/static/app.js`, `ui.js`, `pulse-tuning.js`, `pulse-axis.css`,
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

## Validation and release boundary

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
