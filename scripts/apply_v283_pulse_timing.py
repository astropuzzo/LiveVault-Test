from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected text not found in {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: Path, start: str, end: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    start_i = text.find(start)
    if start_i < 0:
        raise SystemExit(f"start marker not found in {path}: {start!r}")
    end_i = text.find(end, start_i)
    if end_i < 0:
        raise SystemExit(f"end marker not found in {path}: {end!r}")
    path.write_text(text[:start_i] + replacement + text[end_i:], encoding="utf-8")


app = ROOT / "app/static/app.js"
css = ROOT / "app/static/enhancements.css"
main = ROOT / "app/main.py"
sw = ROOT / "app/static/sw.js"
version = ROOT / "VERSION"
readme = ROOT / "README.md"
start_doc = ROOT / "START_HERE.md"
changelog = ROOT / "CHANGELOG.md"
test_ui = ROOT / "tests/test_live_pulse_ui.py"
test_v280 = ROOT / "tests/test_v280_live_intelligence.py"
test_version = ROOT / "tests/test_version_consistency.py"

# App-wide display timezone: Frankfurt / Europe-Berlin (DST-aware).
replace_once(
    app,
    "const selectedProfiles = new Set();\n",
    "const selectedProfiles = new Set();\nconst DISPLAY_TIME_ZONE = 'Europe/Berlin';\nconst DISPLAY_TIME_ZONE_LABEL = 'Frankfurt';\n",
)

replace_between(
    app,
    "function dateText(value) {",
    "function dateFull(value) {",
    """function dateText(value) {
  if (!timestamp(value)) return '—';
  return new Intl.DateTimeFormat('it-IT', {
    timeZone: DISPLAY_TIME_ZONE,
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
  }).format(new Date(value));
}

""",
)
replace_between(
    app,
    "function dateFull(value) {",
    "function creatorLinkMarkup",
    """function dateFull(value) {
  if (!timestamp(value)) return '—';
  return new Intl.DateTimeFormat('it-IT', {
    timeZone: DISPLAY_TIME_ZONE,
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'
  }).format(new Date(value));
}

""",
)
replace_once(app, "/* LiveVault Live Intelligence v2.8.2 */", "/* LiveVault Live Intelligence v2.8.3 */")

# Backend: expose the actual recording intervals inside every detected live interval.
replace_between(
    main,
    "                overlapping = []\n",
    "                if interval[\"open\"]:\n",
    """                overlapping: list[tuple[Recording, datetime, datetime]] = []
                for recording in recording_rows:
                    if int(recording.source_id) not in linked_ids:
                        continue
                    rec_start = _pulse_aware(recording.started_at)
                    rec_end = _pulse_aware(recording.finalized_at)
                    if rec_start is None or rec_end is None:
                        continue
                    clipped_start = max(started, rec_start)
                    clipped_end = min(ended, rec_end)
                    if clipped_start < clipped_end:
                        overlapping.append((recording, clipped_start, clipped_end))

                merged_recordings: list[dict] = []
                for _recording, rec_start, rec_end in sorted(overlapping, key=lambda row: row[1]):
                    if merged_recordings and rec_start <= merged_recordings[-1][\"ended\"] + timedelta(seconds=12):
                        merged_recordings[-1][\"ended\"] = max(merged_recordings[-1][\"ended\"], rec_end)
                    else:
                        merged_recordings.append({\"started\": rec_start, \"ended\": rec_end})

                recording_items = [row[0] for row in overlapping]
                live_seconds = max(0.0, (ended - started).total_seconds())
                recorded_seconds = sum(
                    max(0.0, (row[\"ended\"] - row[\"started\"]).total_seconds())
                    for row in merged_recordings
                )
                file_count = len(recording_items)
                uploaded_count = sum(1 for row in recording_items if row.upload_status == \"uploaded\")
                failed_count = sum(1 for row in recording_items if row.upload_status in {\"failed\", \"integrity_failed\"})
                total_bytes = sum(int(row.size_bytes or 0) for row in recording_items)
                coverage = min(100.0, recorded_seconds / live_seconds * 100.0) if live_seconds > 0 else 0.0
""",
)
replace_once(
    main,
    """                    \"recorded_seconds\": round(recorded_seconds, 2),
                    \"coverage_percent\": round(coverage, 1),""",
    """                    \"recorded_seconds\": round(recorded_seconds, 2),
                    \"recording_started_at\": _iso_utc(merged_recordings[0][\"started\"]) if merged_recordings else None,
                    \"recording_ended_at\": _iso_utc(merged_recordings[-1][\"ended\"]) if merged_recordings else None,
                    \"recording_intervals\": [
                        {\"started_at\": _iso_utc(row[\"started\"]), \"ended_at\": _iso_utc(row[\"ended\"])}
                        for row in merged_recordings
                    ],
                    \"coverage_percent\": round(coverage, 1),""",
)

# Frontend Pulse: explicit LIVE/REC timing + Frankfurt timezone + layered geometry.
replace_between(
    app,
    "function pulseTimeLabel(value) {",
    "function controlRoomPulseMarkup() {",
    """function pulseTimeLabel(value, seconds = false) {
  if (!timestamp(value)) return '—';
  return new Intl.DateTimeFormat('it-IT', {
    timeZone: DISPLAY_TIME_ZONE,
    hour: '2-digit', minute: '2-digit', ...(seconds ? {second: '2-digit'} : {})
  }).format(new Date(value));
}

function pulseRecordingIntervals(session) {
  return Array.isArray(session?.recording_intervals) ? session.recording_intervals.filter(row => timestamp(row?.started_at) && timestamp(row?.ended_at)) : [];
}

function pulseRangeLabel(start, end, open = false) {
  if (!timestamp(start)) return '—';
  return `${pulseTimeLabel(start)}–${open ? 'ora' : pulseTimeLabel(end)}`;
}

function pulseSessionTimingMarkup(session) {
  const recs = pulseRecordingIntervals(session);
  const recStart = session.recording_started_at || recs[0]?.started_at;
  const recEnd = session.recording_ended_at || recs[recs.length - 1]?.ended_at;
  return `<span class=\"cr-pulse-times\"><span><b>LIVE</b> ${esc(pulseRangeLabel(session.started_at, session.ended_at, !session.ended_at))}</span><span><b>REC</b> ${recStart ? esc(pulseRangeLabel(recStart, recEnd, false)) : '—'}</span></span>`;
}

""",
)

new_pulse = r'''function controlRoomPulseMarkup() {
  const sessions = pulseSessions();
  const compact = window.matchMedia('(max-width: 620px)').matches;
  const hours = Math.max(1, Number(controlRoomPulseData.hours) || 12);
  const generatedAt = timestamp(controlRoomPulseData.generated_at) || Date.now();
  const expectedWindowStart = generatedAt - hours * 3600000;
  const apiWindowStart = timestamp(controlRoomPulseData.window_start);
  const expectedSpan = hours * 3600000;
  const apiSpan = apiWindowStart ? generatedAt - apiWindowStart : 0;
  const windowStart = apiWindowStart && Math.abs(apiSpan - expectedSpan) <= 15 * 60000
    ? apiWindowStart
    : expectedWindowStart;
  const span = Math.max(1, generatedAt - windowStart);
  const profileOrder = [];
  const byProfile = new Map();
  for (const session of [...sessions].reverse()) {
    const profileId = Number(session.profile_id);
    if (!byProfile.has(profileId)) { byProfile.set(profileId, []); profileOrder.push(profileId); }
    byProfile.get(profileId).push(session);
  }
  const maxProfiles = compact ? 5 : 8;
  const recentProfiles = profileOrder.slice(-maxProfiles).reverse();
  const labelRatios = [0, .25, .5, .75, 1];
  const labels = labelRatios.map((ratio, index) => {
    const value = new Date(windowStart + span * ratio);
    return `<span class="cr-pulse-tick cr-pulse-tick-${index}">${esc(pulseTimeLabel(value))}</span>`;
  }).join('');
  const xFor = value => Math.max(0, Math.min(1000, (value - windowStart) / span * 1000));
  const widthFor = (start, end, minWidth = 5) => Math.max(0, Math.min(1000 - xFor(start), Math.max(minWidth, (end - start) / span * 1000)));
  const rows = recentProfiles.map(profileId => {
    const profileSessions = byProfile.get(profileId) || [];
    const representative = profileSessions[profileSessions.length - 1];
    const graphics = profileSessions.map(session => {
      const start = Math.max(windowStart, timestamp(session.started_at));
      const end = session.ended_at ? Math.min(generatedAt, timestamp(session.ended_at)) : generatedAt;
      if (!start || end <= start) return '';
      const x = xFor(start);
      const liveWidth = widthFor(start, end, compact ? 18 : 5);
      const title = `${session.display_name} · LIVE ${pulseRangeLabel(session.started_at, session.ended_at, !session.ended_at)} · REC ${session.recording_started_at ? pulseRangeLabel(session.recording_started_at, session.recording_ended_at, false) : '—'}`;
      const recs = pulseRecordingIntervals(session).map(rec => {
        const recStart = Math.max(start, timestamp(rec.started_at));
        const recEnd = Math.min(end, timestamp(rec.ended_at));
        if (!recStart || recEnd <= recStart) return '';
        const recX = xFor(recStart);
        const recWidth = widthFor(recStart, recEnd, compact ? 12 : 4);
        return `<rect class="cr-pulse-rec-span" x="${recX.toFixed(3)}" y="4" width="${recWidth.toFixed(3)}" height="8" rx="4" ry="4"></rect>`;
      }).join('');
      const firstRec = pulseRecordingIntervals(session)[0];
      const recMarkerX = firstRec ? xFor(Math.max(start, timestamp(firstRec.started_at))) : null;
      return `<g class="cr-pulse-session" data-profile-link="${session.representative_source_id || 0}"><rect class="cr-pulse-live-span ${session.state === 'live' ? 'current' : ''} ${session.state === 'missed' ? 'missed' : ''}" x="${x.toFixed(3)}" y="2" width="${liveWidth.toFixed(3)}" height="12" rx="6" ry="6"></rect><line class="cr-pulse-live-marker" x1="${x.toFixed(3)}" y1="0" x2="${x.toFixed(3)}" y2="16"></line>${recMarkerX === null ? '' : `<line class="cr-pulse-rec-marker" x1="${recMarkerX.toFixed(3)}" y1="1" x2="${recMarkerX.toFixed(3)}" y2="15"></line>`}${recs}<title>${esc(title)}</title></g>`;
    }).join('');
    return `<div class="cr-pulse-row"><div class="cr-pulse-who">${creatorLinkMarkup(representative.representative_source_id, representative.display_name, 'cr-pulse-name')}${pulseSessionTimingMarkup(representative)}</div><div class="cr-pulse-track"><svg class="cr-pulse-svg" viewBox="0 0 1000 16" preserveAspectRatio="none" role="img" aria-label="Timeline ${esc(representative.display_name)}">${graphics}</svg></div></div>`;
  }).join('');
  const hidden = Math.max(0, profileOrder.length - recentProfiles.length);
  return `<section class="cr-pulse"><div class="cr-pulse-head"><strong>Live Pulse</strong><div class="cr-pulse-head-right"><span class="cr-pulse-legend"><i class="live"></i>LIVE <i class="rec"></i>REC</span><span>${controlRoomPulseData.hours || 12}h · ${DISPLAY_TIME_ZONE_LABEL}${hidden ? ` · +${hidden}` : ''}</span></div></div><div class="cr-pulse-scale"><span></span><div>${labels}</div></div>${rows || '<div class="cr-pulse-empty">—</div>'}</section>`;
}

'''
replace_between(app, "function controlRoomPulseMarkup() {", "function controlRoomRecentEnded", new_pulse)

css_text = css.read_text(encoding="utf-8")
css_text += r'''

/* LiveVault Pulse timing + Frankfurt timezone v2.8.3 */
.cr-pulse-scale,.cr-pulse-row{grid-template-columns:250px minmax(0,1fr);gap:12px}
.cr-pulse-row{min-height:39px}.cr-pulse-who{min-width:0;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:10px}.creator-link.cr-pulse-name{font-size:.72rem;font-weight:780;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cr-pulse-times{display:grid;gap:1px;min-width:116px;font-size:.61rem;line-height:1.25;color:var(--muted);font-variant-numeric:tabular-nums}.cr-pulse-times b{font-size:.55rem;letter-spacing:.04em;color:#aab4ad;margin-right:3px}.cr-pulse-track,.cr-pulse-svg{height:16px}.cr-pulse-track{overflow:hidden;background:rgba(255,255,255,.035)}.cr-pulse-svg{display:block;width:100%;overflow:hidden}.cr-pulse-session{cursor:pointer}.cr-pulse-live-span{fill:#555e59;opacity:.82}.cr-pulse-live-span.current{fill:#b93b46;opacity:1}.cr-pulse-live-span.missed{fill:#8f6b32}.cr-pulse-rec-span{fill:#b8d66f;opacity:.96}.cr-pulse-live-marker{stroke:rgba(255,255,255,.42);stroke-width:1.2;vector-effect:non-scaling-stroke}.cr-pulse-rec-marker{stroke:#d9ef9c;stroke-width:1.5;vector-effect:non-scaling-stroke}.cr-pulse-head-right{display:flex;align-items:center;gap:12px}.cr-pulse-legend{display:flex;align-items:center;gap:5px}.cr-pulse-legend i{display:inline-block;width:12px;height:5px;border-radius:999px}.cr-pulse-legend i.live{background:#666f69}.cr-pulse-legend i.rec{background:#b8d66f}
@media(max-width:760px){.cr-pulse-scale,.cr-pulse-row{grid-template-columns:164px minmax(0,1fr)}.cr-pulse-who{display:block}.cr-pulse-times{margin-top:2px;min-width:0;font-size:.58rem}.cr-pulse-times span{display:block}.cr-pulse-head-right{gap:7px}.cr-pulse-legend{display:none}}
@media(max-width:620px){.cr-pulse-scale,.cr-pulse-row{grid-template-columns:118px minmax(0,1fr);gap:8px}.cr-pulse-row{min-height:42px}.creator-link.cr-pulse-name{font-size:.64rem}.cr-pulse-times{font-size:.54rem}.cr-pulse-times b{font-size:.5rem}.cr-pulse-track,.cr-pulse-svg{height:16px}}
'''
css.write_text(css_text, encoding="utf-8")

# Version bump.
replace_once(main, 'VERSION = "2.8.2"', 'VERSION = "2.8.3"')
replace_once(sw, "livevault-shell-v2.8.2", "livevault-shell-v2.8.3")
version.write_text("2.8.3\n", encoding="utf-8")
replace_once(readme, "# LiveVault v2.8.2", "# LiveVault v2.8.3")
replace_once(start_doc, "# LiveVault v2.8.2 — START HERE", "# LiveVault v2.8.3 — START HERE")
replace_once(test_version, 'assert version == "2.8.2"', 'assert version == "2.8.3"')

changelog_text = changelog.read_text(encoding="utf-8")
entry = """# Changelog

## 2.8.3 — 2026-09-03

- Live Pulse ora mostra gli intervalli **LIVE** e **REC** separatamente, con inizio/fine leggibili per ogni creator.
- L'API Pulse espone gli intervalli reali di registrazione anziché una sola percentuale aggregata.
- Tutti gli orari UI principali usano `Europe/Berlin` (Francoforte), con cambio CET/CEST automatico.
- Timeline resa più leggibile su desktop e mobile senza inline style, mantenendo la CSP stretta.

"""
if not changelog_text.startswith("# Changelog\n"):
    raise SystemExit("unexpected changelog header")
changelog.write_text(entry + changelog_text[len("# Changelog\n\n"):], encoding="utf-8")

test_ui.write_text(r'''from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pulse_function(js: str) -> str:
    start = js.index('function controlRoomPulseMarkup()')
    end = js.index('function controlRoomRecentEnded', start)
    return js[start:end]


def test_live_pulse_is_csp_safe_and_shows_live_and_recording_geometry():
    js = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')
    css = (ROOT / 'app/static/enhancements.css').read_text(encoding='utf-8')
    main = (ROOT / 'app/main.py').read_text(encoding='utf-8')
    pulse = pulse_function(js)

    assert 'style=' not in pulse
    assert 'const labelRatios = [0, .25, .5, .75, 1]' in pulse
    assert 'pulseRecordingIntervals(session)' in pulse
    assert '<rect class="cr-pulse-live-span' in pulse
    assert '<rect class="cr-pulse-rec-span"' in pulse
    assert 'cr-pulse-live-marker' in pulse
    assert 'cr-pulse-rec-marker' in pulse
    assert 'pulseSessionTimingMarkup(representative)' in pulse
    assert 'DISPLAY_TIME_ZONE_LABEL' in pulse
    assert 'viewBox="0 0 1000 16"' in pulse
    assert 'const maxProfiles = compact ? 5 : 8' in pulse

    assert "const DISPLAY_TIME_ZONE = 'Europe/Berlin'" in js
    assert "const DISPLAY_TIME_ZONE_LABEL = 'Frankfurt'" in js
    assert 'timeZone: DISPLAY_TIME_ZONE' in js
    assert 'recording_intervals' in main
    assert 'recording_started_at' in main
    assert 'recording_ended_at' in main
    assert 'LiveVault Pulse timing + Frankfurt timezone v2.8.3' in css
    assert '.cr-pulse-rec-span{fill:' in css

    assert "style-src 'self'" in main
    assert "style-src 'self' 'unsafe-inline'" not in main
''', encoding="utf-8")

v280_text = test_v280.read_text(encoding="utf-8")
v280_text = v280_text.replace(
    "    assert '✓ SALVATA' in js\n",
    "    assert '✓ SALVATA' in js\n    assert 'recording_intervals' in main\n    assert 'recording_started_at' in main\n    assert \"Europe/Berlin\" in js\n",
    1,
)
test_v280.write_text(v280_text, encoding="utf-8")

print("v2.8.3 Pulse timing patch applied")
