# OpenAstro 3.0.0 verification

Release date: 2026-09-05. Covers LiveVault and OpenAstro Control.

## Scope

- Rebuilt both responsive interfaces with shared visual direction, local assets,
  clearer navigation, real telemetry, source search, keyboard command search,
  archive pagination and CSV exports.
- Preserved recording, processing, upload, recovery and authentication behavior.
  No database migration or credential change is required.
- Consolidated LiveVault styles, reduced unnecessary polling, compressed text
  responses, and kept recording byte-range responses uncompressed.
- Versioned the previously separate control panel. Fixed shared probe caching,
  chart gaps, stale state, invalid request handling, serialized actions and
  cancelled confirmations. Energy values remain explicitly estimated.
- Removed the obsolete CapRover deployment job. Deployment uses the current
  Coolify application, as documented in HOSTING.md.

## Checks performed before release

- Windows: 173 Python tests passed, eight skipped because of platform or missing
  media tools. Linux CI installs FFmpeg and runs the full suite before deployment.
- Five JavaScript regression tests passed, including source filtering, chart
  gaps, timestamp positions and action recovery after session expiry.
- Python compilation, JavaScript syntax and Git whitespace checks passed.
- Browser checks with real authenticated data through a local read-only preview:
  dashboard, library, archive, statistics, settings, profile discovery, keyboard
  search, panel charts and restart confirmation cancellation.
- Phone-width checks found no horizontal overflow in either app. Archive filters
  collapse on phones. Missing measurements show unavailable states rather than
  invented readings.

## Rollback and practical limits

- Database backup: `/share/livevault-backups/livevault-20260905-063949.db`.
- Panel rollback copy:
  `/home/astro/deploy-backups/openastro-control-before-v3-20260905.tgz`.
- Previous LiveVault commit: `f62fb8c7ef567c2dd2791a468682144b1fbf81ac`.
- Database backups do not contain video media. Retain the existing cloud copies.
- Hardware shutdown, disk ejection and Docker restart were not exercised during
  UI testing. These interrupt the active node; request validation and confirmation
  behavior were checked without executing those operations.
- External providers can still make a source private or unavailable. The UI must
  report that condition honestly. Archive search/export covers loaded records;
  the interface offers loading older records and states that scope explicitly.
- This release is a verified improvement to the existing product, not a claim
  that every possible defect or provider-side failure has been eliminated.
