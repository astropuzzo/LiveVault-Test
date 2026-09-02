from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise RuntimeError(f"marker not found in {path}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


# Version + release metadata.
replace_once("app/main.py", 'VERSION = "2.7.1"', 'VERSION = "2.8.0"')
write("VERSION", "2.8.0\n")
replace_once("app/static/sw.js", "livevault-shell-v2.7.1", "livevault-shell-v2.8.0")
replace_once("README.md", "# LiveVault v2.7.1", "# LiveVault v2.8.0")
replace_once("START_HERE.md", "# LiveVault v2.7.1 — START HERE", "# LiveVault v2.8.0 — START HERE")
replace_once("tests/test_version_consistency.py", 'assert version == "2.7.1"', 'assert version == "2.8.0"')

changelog = read("CHANGELOG.md")
entry = """## 2.8.0 — 2026-09-02

- **Live Pulse**: timeline operativa delle ultime 12 ore con live correnti, sessioni concluse, copertura REC e sessioni perse.
- Le card della Control Room seguono ora il ciclo della sessione: LIVE/REC durante l'acquisizione, poi terminata, upload e salvata per le sessioni appena concluse.
- **Live DNA** nei profili: impronta settimanale e oraria, durata media, ora di picco e copertura.
- Archivio ridisegnato a gruppi collassabili per **giorno, creator o sessione**, con filtri per periodo, creator, provider, locale/cloud, stato e ordinamento.
- L'Archivio mostra un numero limitato di gruppi alla volta con caricamento progressivo, evitando una lista visivamente infinita.

"""
if "## 2.8.0 —" not in changelog:
    changelog = changelog.replace("# Changelog\n\n", "# Changelog\n\n" + entry, 1)
    write("CHANGELOG.md", changelog)

# Backend pulse endpoint: persistent LiveSession history + recording overlap summaries.
main = read("app/main.py")
pulse_marker = '@app.get("/api/sources/{source_id}/preview")\ndef source_live_preview'
if '@app.get("/api/control-room/pulse")' not in main:
    pulse_code = r'''
def _pulse_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@app.get("/api/control-room/pulse")
def control_room_pulse(request: Request, hours: int = 12):
    require_auth(request)
    hours = max(1, min(int(hours), 48))
    now = utcnow()
    window_start = now - timedelta(hours=hours)
    with db_session() as db:
        source_rows = list(db.scalars(
            select(Source).where(Source.archived.is_(False)).order_by(Source.id)
        ).all())
        source_map = {int(row.id): row for row in source_rows}
        source_ids = list(source_map)
        profile_ids = sorted({int(row.profile_id) for row in source_rows if row.profile_id is not None})
        profiles = {
            int(row.id): row
            for row in db.scalars(select(Profile).where(Profile.id.in_(profile_ids))).all()
        } if profile_ids else {}
        live_rows = list(db.scalars(
            select(LiveSession).where(
                LiveSession.source_id.in_(source_ids),
                LiveSession.started_at <= now,
                or_(LiveSession.ended_at.is_(None), LiveSession.ended_at >= window_start),
            ).order_by(LiveSession.started_at.asc())
        ).all()) if source_ids else []
        recording_rows = list(db.scalars(
            select(Recording).where(
                Recording.source_id.in_(source_ids),
                Recording.finalized_at >= window_start - timedelta(hours=2),
            ).order_by(Recording.started_at.asc())
        ).all()) if source_ids else []

        by_profile: dict[int, list[dict]] = defaultdict(list)
        sources_by_profile: dict[int, list[Source]] = defaultdict(list)
        for source in source_rows:
            if source.profile_id is not None:
                sources_by_profile[int(source.profile_id)].append(source)

        for session in live_rows:
            source = source_map.get(int(session.source_id))
            if not source or source.profile_id is None:
                continue
            started = _pulse_aware(session.started_at)
            ended_real = _pulse_aware(session.ended_at)
            ended = ended_real or now
            if started is None or ended <= window_start or started > now:
                continue
            by_profile[int(source.profile_id)].append({
                "started": max(started, window_start),
                "ended": min(ended, now),
                "open": ended_real is None,
                "origin": str(session.origin or "probe"),
            })

        merged_by_profile: dict[int, list[dict]] = defaultdict(list)
        for profile_id, intervals in by_profile.items():
            for interval in sorted(intervals, key=lambda row: row["started"]):
                merged = merged_by_profile[profile_id]
                if merged and interval["started"] <= merged[-1]["ended"] + timedelta(seconds=75):
                    merged[-1]["ended"] = max(merged[-1]["ended"], interval["ended"])
                    merged[-1]["open"] = bool(merged[-1]["open"] or interval["open"])
                    if interval["origin"] != "recording_backfill":
                        merged[-1]["origin"] = interval["origin"]
                else:
                    merged.append(dict(interval))

        sessions: list[dict] = []
        for profile_id, intervals in merged_by_profile.items():
            profile = profiles.get(profile_id)
            linked = sources_by_profile.get(profile_id, [])
            linked_ids = {int(row.id) for row in linked}
            representative = next((row for row in linked if row.enabled), linked[0] if linked else None)
            for interval in intervals:
                started = interval["started"]
                ended = interval["ended"]
                overlapping = []
                for recording in recording_rows:
                    if int(recording.source_id) not in linked_ids:
                        continue
                    rec_start = _pulse_aware(recording.started_at)
                    rec_end = _pulse_aware(recording.finalized_at)
                    if rec_start is None or rec_end is None:
                        continue
                    if rec_start < ended and rec_end > started:
                        overlapping.append(recording)
                live_seconds = max(0.0, (ended - started).total_seconds())
                recorded_seconds = sum(float(row.duration_seconds or 0.0) for row in overlapping)
                file_count = len(overlapping)
                uploaded_count = sum(1 for row in overlapping if row.upload_status == "uploaded")
                failed_count = sum(1 for row in overlapping if row.upload_status in {"failed", "integrity_failed"})
                total_bytes = sum(int(row.size_bytes or 0) for row in overlapping)
                coverage = min(100.0, recorded_seconds / live_seconds * 100.0) if live_seconds > 0 else 0.0
                if interval["open"]:
                    state = "live"
                elif file_count == 0:
                    state = "missed"
                elif uploaded_count == file_count:
                    state = "saved"
                else:
                    state = "ended"
                sessions.append({
                    "id": f"p{profile_id}-{int(started.timestamp())}",
                    "profile_id": profile_id,
                    "representative_source_id": int(representative.id) if representative else None,
                    "display_name": str(profile.display_name) if profile else (representative.name if representative else f"Profilo {profile_id}"),
                    "started_at": _iso_utc(started),
                    "ended_at": None if interval["open"] else _iso_utc(ended),
                    "duration_seconds": round(live_seconds, 2),
                    "recorded_seconds": round(recorded_seconds, 2),
                    "coverage_percent": round(coverage, 1),
                    "file_count": file_count,
                    "uploaded_count": uploaded_count,
                    "failed_count": failed_count,
                    "total_bytes": total_bytes,
                    "state": state,
                    "origin": interval["origin"],
                })

    sessions.sort(key=lambda row: timestamp := row["started_at"], reverse=True)
    return {
        "hours": hours,
        "window_start": _iso_utc(window_start),
        "generated_at": _iso_utc(now),
        "sessions": sessions[:120],
    }


'''
    # Avoid walrus/lambda portability issue by using direct string sort after insertion.
    pulse_code = pulse_code.replace('sessions.sort(key=lambda row: timestamp := row["started_at"], reverse=True)', 'sessions.sort(key=lambda row: str(row["started_at"]), reverse=True)')
    if pulse_marker not in main:
        raise RuntimeError("pulse endpoint marker not found")
    main = main.replace(pulse_marker, pulse_code + pulse_marker, 1)
    write("app/main.py", main)

# Front-end intelligence layer. It deliberately wraps v2.7 instead of duplicating the original dashboard renderer.
app_js = read("app/static/app.js")
if "/* LiveVault Live Intelligence v2.8.0 */" not in app_js:
    app_js += r'''


/* LiveVault Live Intelligence v2.8.0 */
let controlRoomPulseData = {hours: 12, window_start: null, generated_at: null, sessions: []};
let archiveGroupLimit = 10;

async function loadControlRoomPulse() {
  try {
    controlRoomPulseData = await api('/api/control-room/pulse?hours=12');
  } catch (error) {
    if (error.message !== 'auth') console.warn('Live Pulse:', error.message);
  }
  return controlRoomPulseData;
}

function pulseSessions() {
  return Array.isArray(controlRoomPulseData?.sessions) ? controlRoomPulseData.sessions : [];
}

function pulseCurrentForProfile(profileId) {
  return pulseSessions().find(row => Number(row.profile_id) === Number(profileId) && row.state === 'live') || null;
}

const controlRoomProfileRowsV271 = controlRoomProfileRows;
controlRoomProfileRows = function controlRoomProfileRowsV280() {
  return controlRoomProfileRowsV271().map(profile => ({
    ...profile,
    pulse_session: pulseCurrentForProfile(profile.profile_id),
  }));
};

const controlRoomStatusTextV271 = controlRoomStatusText;
controlRoomStatusText = function controlRoomStatusTextV280(profile) {
  if (!profile.live || profile.blocked) return controlRoomStatusTextV271(profile);
  const pulse = profile.pulse_session;
  const liveSeconds = pulse?.started_at ? Math.max(0, (Date.now() - timestamp(pulse.started_at)) / 1000) : 0;
  const livePart = liveSeconds ? `LIVE ${duration(liveSeconds)}` : 'LIVE';
  if (!profile.recording) return `${livePart} · NON REC`;
  const recSeconds = Number(profile.active?.elapsed_seconds || 0);
  return recSeconds ? `${livePart} · REC ${duration(recSeconds)}` : `${livePart} · REC`;
};

function pulseSessionMeta(session) {
  const bits = [];
  if (session.file_count) bits.push(`${session.file_count} file`);
  if (session.total_bytes) bits.push(humanBytes(session.total_bytes));
  if (session.file_count) bits.push(`${Math.round(Number(session.coverage_percent) || 0)}%`);
  return bits.join(' · ');
}

const controlRoomLiveCardV271 = controlRoomLiveCard;
controlRoomLiveCard = function controlRoomLiveCardV280(profile, wall = false) {
  let html = controlRoomLiveCardV271(profile, wall);
  const session = profile.pulse_session;
  if (!session) return html;
  const meta = pulseSessionMeta(session);
  if (!meta) return html;
  const insert = `<div class="cr-session-meta"><span>${esc(meta)}</span></div>`;
  const position = html.lastIndexOf('</div>\n  </article>');
  return position >= 0 ? html.slice(0, position) + insert + html.slice(position) : html;
};

function pulseBlockClass(session) {
  if (session.state === 'live') return session.file_count || Number(session.recorded_seconds) > 0 ? 'live rec' : 'live';
  if (session.state === 'missed' || Number(session.coverage_percent) < 55) return 'missed';
  if (session.state === 'saved') return 'saved';
  return 'ended';
}

function pulseTimeLabel(value) {
  return new Intl.DateTimeFormat('it-IT', {hour: '2-digit', minute: '2-digit'}).format(new Date(value));
}

function controlRoomPulseMarkup() {
  const sessions = pulseSessions();
  const windowStart = timestamp(controlRoomPulseData.window_start) || (Date.now() - 12 * 3600000);
  const generatedAt = timestamp(controlRoomPulseData.generated_at) || Date.now();
  const span = Math.max(1, generatedAt - windowStart);
  const profileOrder = [];
  const byProfile = new Map();
  for (const session of [...sessions].reverse()) {
    const profileId = Number(session.profile_id);
    if (!byProfile.has(profileId)) { byProfile.set(profileId, []); profileOrder.push(profileId); }
    byProfile.get(profileId).push(session);
  }
  const recentProfiles = profileOrder.slice(-8).reverse();
  const labels = [0, .25, .5, .75, 1].map(ratio => {
    const value = new Date(windowStart + span * ratio);
    return `<span style="left:${ratio * 100}%">${esc(new Intl.DateTimeFormat('it-IT', {hour:'2-digit', minute:'2-digit'}).format(value))}</span>`;
  }).join('');
  const rows = recentProfiles.map(profileId => {
    const profileSessions = byProfile.get(profileId) || [];
    const representative = profileSessions[profileSessions.length - 1];
    const blocks = profileSessions.map(session => {
      const start = Math.max(windowStart, timestamp(session.started_at));
      const end = session.ended_at ? Math.min(generatedAt, timestamp(session.ended_at)) : generatedAt;
      const left = Math.max(0, Math.min(100, (start - windowStart) / span * 100));
      const width = Math.max(.65, Math.min(100 - left, (end - start) / span * 100));
      const title = `${session.display_name} · ${pulseTimeLabel(session.started_at)}${session.ended_at ? `–${pulseTimeLabel(session.ended_at)}` : '–ora'} · ${Math.round(Number(session.coverage_percent) || 0)}% REC`;
      return `<button class="cr-pulse-block ${pulseBlockClass(session)}" style="left:${left.toFixed(3)}%;width:${width.toFixed(3)}%" data-profile-link="${session.representative_source_id || 0}" type="button" title="${esc(title)}" aria-label="${esc(title)}"></button>`;
    }).join('');
    return `<div class="cr-pulse-row"><button class="creator-link cr-pulse-name" data-profile-link="${representative.representative_source_id || 0}" type="button">${esc(representative.display_name)}</button><div class="cr-pulse-track">${blocks}</div></div>`;
  }).join('');
  const hidden = Math.max(0, profileOrder.length - recentProfiles.length);
  return `<section class="cr-pulse"><div class="cr-pulse-head"><strong>Live Pulse</strong><span>${controlRoomPulseData.hours || 12}h${hidden ? ` · +${hidden}` : ''}</span></div><div class="cr-pulse-scale"><span></span><div>${labels}</div></div>${rows || '<div class="cr-pulse-empty">—</div>'}</section>`;
}

function controlRoomRecentEnded(profiles) {
  const liveProfileIds = new Set(profiles.filter(row => row.live).map(row => Number(row.profile_id)));
  const cutoff = Date.now() - 30 * 60 * 1000;
  const latest = new Map();
  for (const session of pulseSessions()) {
    if (!session.ended_at || timestamp(session.ended_at) < cutoff || liveProfileIds.has(Number(session.profile_id))) continue;
    if (!latest.has(Number(session.profile_id))) latest.set(Number(session.profile_id), session);
  }
  return [...latest.values()].sort((a, b) => timestamp(b.ended_at) - timestamp(a.ended_at)).slice(0, 6);
}

function controlRoomEndedCard(session) {
  const source = sources.find(row => Number(row.id) === Number(session.representative_source_id)) || sources.find(row => Number(row.profile_id) === Number(session.profile_id));
  const cover = safeUrl(source?.cover_thumbnail_url || '');
  const saved = session.state === 'saved';
  const state = saved ? '✓ SALVATA' : session.state === 'missed' ? 'NON REC' : session.file_count ? `UPLOAD ${session.uploaded_count}/${session.file_count}` : 'TERMINATA';
  const meta = [duration(session.duration_seconds), session.file_count ? `${session.file_count} file` : '', session.total_bytes ? humanBytes(session.total_bytes) : '', session.file_count ? `${Math.round(Number(session.coverage_percent) || 0)}% REC` : ''].filter(Boolean).join(' · ');
  return `<article class="cr-ended-card ${saved ? 'saved' : ''} ${session.state === 'missed' ? 'missed' : ''}">
    <div class="cr-ended-cover">${cover ? `<img src="${esc(cover)}" alt="">` : `<span>${esc(controlRoomInitials(session.display_name))}</span>`}</div>
    <div class="cr-ended-main"><div>${creatorLinkMarkup(session.representative_source_id, session.display_name, 'cr-ended-name')}<small>${esc(meta)}</small></div><div class="cr-ended-state"><strong>${esc(state)}</strong><span>${esc(ago(session.ended_at))}</span></div></div>
  </article>`;
}

const renderSourcesV271 = renderSources;
renderSources = function renderSourcesV280() {
  renderSourcesV271();
  const root = $('#sources');
  if (!root) return;
  const toolbar = root.querySelector('.cr-toolbar');
  if (toolbar && !root.querySelector('.cr-pulse')) toolbar.insertAdjacentHTML('afterend', controlRoomPulseMarkup());
  const profiles = controlRoomProfileRows();
  const recent = controlRoomRecentEnded(profiles);
  const liveSection = root.querySelector('.cr-live-section');
  if (liveSection && recent.length && !root.querySelector('.cr-recent-section')) {
    liveSection.insertAdjacentHTML('afterend', `<section class="cr-recent-section"><div class="cr-section-head"><h3>Appena terminate</h3><span class="count">${recent.length}</span></div><div class="cr-ended-list">${recent.map(controlRoomEndedCard).join('')}</div></section>`);
  }
};

function liveDnaMarkup(activity) {
  if (!activity) return '';
  const summary = activity.summary || {};
  const daily = activity.daily || [];
  const hourly = activity.hourly || [];
  if (!daily.some(row => Number(row.online_seconds)) && !hourly.some(row => Number(row.online_seconds))) return '';
  const week = Array.from({length: 7}, (_, index) => ({index, seconds: 0}));
  for (const row of daily) {
    const date = new Date(`${row.date}T12:00:00`);
    if (Number.isNaN(date.getTime())) continue;
    const mondayIndex = (date.getDay() + 6) % 7;
    week[mondayIndex].seconds += Number(row.online_seconds) || 0;
  }
  const maxWeek = Math.max(1, ...week.map(row => row.seconds));
  const maxHour = Math.max(1, ...hourly.map(row => Number(row.online_seconds) || 0));
  const peak = hourly.reduce((best, row) => Number(row.online_seconds) > Number(best?.online_seconds || -1) ? row : best, null);
  const average = Number(summary.live_sessions) ? Number(summary.online_seconds || 0) / Number(summary.live_sessions) : 0;
  const dayNames = ['L','M','M','G','V','S','D'];
  const weekBars = week.map(row => `<div class="dna-day"><i style="height:${Math.max(4, row.seconds / maxWeek * 100).toFixed(1)}%"></i><span>${dayNames[row.index]}</span></div>`).join('');
  const hourBars = hourly.map(row => `<i title="${String(row.hour).padStart(2,'0')}:00" style="height:${Math.max(3, (Number(row.online_seconds) || 0) / maxHour * 100).toFixed(1)}%"></i>`).join('');
  return `<section class="profile-section live-dna"><div class="profile-section-head"><h3>Live DNA</h3></div><div class="dna-layout"><div class="dna-week">${weekBars}</div><div class="dna-hours">${hourBars}</div><div class="dna-metrics"><span><strong>${esc(duration(average))}</strong> media</span><span><strong>${peak ? `${String(peak.hour).padStart(2,'0')}:00` : '—'}</strong> picco</span><span><strong>${esc(statNumber(summary.coverage_percent || 0, '%'))}</strong> REC</span></div></div></section>`;
}

const renderProfileV271 = renderProfile;
renderProfile = function renderProfileV280() {
  renderProfileV271();
  if (!profileData?.activity_statistics) return;
  const overview = $('#profileContent .profile-overview');
  if (overview && !$('#profileContent .live-dna')) overview.insertAdjacentHTML('afterend', liveDnaMarkup(profileData.activity_statistics));
};

function ensureArchiveIntelControls() {
  const toolbar = $('#archive .toolbar');
  if (!toolbar || $('#archiveIntelControls')) return;
  const host = document.createElement('div');
  host.id = 'archiveIntelControls';
  host.className = 'archive-intel-controls';
  host.innerHTML = `<select id="archivePeriod" aria-label="Periodo"><option value="all">Tutto</option><option value="today">Oggi</option><option value="7">7 giorni</option><option value="30">30 giorni</option><option value="90">90 giorni</option></select>
    <select id="archiveCreator" aria-label="Creator"><option value="all">Tutte le creator</option></select>
    <select id="archiveProvider" aria-label="Provider"><option value="all">Tutti i provider</option></select>
    <select id="archiveStorage" aria-label="Posizione"><option value="all">Locale + cloud</option><option value="local">Locale</option><option value="cloud">Cloud</option><option value="not-cloud">Non caricate</option></select>
    <select id="archiveGroup" aria-label="Raggruppa"><option value="day">Per giorno</option><option value="creator">Per creator</option><option value="session">Per sessione</option></select>
    <select id="archiveSort" aria-label="Ordine"><option value="newest">Più recenti</option><option value="oldest">Più vecchie</option></select>`;
  toolbar.append(host);
  for (const select of host.querySelectorAll('select')) select.addEventListener('change', () => { archiveGroupLimit = 10; renderRecordings(); });
}

function fillArchiveIntelControls() {
  ensureArchiveIntelControls();
  const creator = $('#archiveCreator');
  const provider = $('#archiveProvider');
  if (!creator || !provider) return;
  const creatorValue = creator.value;
  const providerValue = provider.value;
  const profiles = new Map();
  const providersMap = new Map();
  for (const source of sources) {
    if (!source.archived) profiles.set(Number(source.profile_id || source.id), source.display_name || source.name);
    providersMap.set(source.platform, source.provider_label || source.platform);
  }
  creator.innerHTML = '<option value="all">Tutte le creator</option>' + [...profiles].sort((a,b) => a[1].localeCompare(b[1], 'it')).map(([id,name]) => `<option value="${id}">${esc(name)}</option>`).join('');
  provider.innerHTML = '<option value="all">Tutti i provider</option>' + [...providersMap].sort((a,b) => a[1].localeCompare(b[1], 'it')).map(([id,name]) => `<option value="${esc(id)}">${esc(name)}</option>`).join('');
  creator.value = [...creator.options].some(option => option.value === creatorValue) ? creatorValue : 'all';
  provider.value = [...provider.options].some(option => option.value === providerValue) ? providerValue : 'all';
}

function archiveSourceFor(recording) {
  return sources.find(source => Number(source.id) === Number(recording.source_id)) || null;
}

function archiveIntelMatches(recording) {
  const period = $('#archivePeriod')?.value || 'all';
  const creator = $('#archiveCreator')?.value || 'all';
  const provider = $('#archiveProvider')?.value || 'all';
  const storage = $('#archiveStorage')?.value || 'all';
  const source = archiveSourceFor(recording);
  const started = timestamp(recording.started_at);
  const now = Date.now();
  if (period === 'today') {
    const date = new Date(started);
    const current = new Date();
    if (date.toDateString() !== current.toDateString()) return false;
  } else if (period !== 'all' && started < now - Number(period) * 86400000) return false;
  if (creator !== 'all' && Number(source?.profile_id || source?.id || -1) !== Number(creator)) return false;
  if (provider !== 'all' && source?.platform !== provider) return false;
  if (storage === 'local' && !recording.local_available) return false;
  if (storage === 'cloud' && !recording.remote_url) return false;
  if (storage === 'not-cloud' && recording.remote_url) return false;
  return true;
}

function archiveDayKey(recording) {
  const date = new Date(recording.started_at);
  if (Number.isNaN(date.getTime())) return 'unknown';
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}

function archiveDayLabel(key) {
  if (key === 'unknown') return 'Senza data';
  const date = new Date(`${key}T12:00:00`);
  const now = new Date();
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === now.toDateString()) return 'Oggi';
  if (date.toDateString() === yesterday.toDateString()) return 'Ieri';
  return new Intl.DateTimeFormat('it-IT', {weekday:'long', day:'2-digit', month:'long', year:'numeric'}).format(date);
}

function archiveGroupRows(rows) {
  const mode = $('#archiveGroup')?.value || 'day';
  const sort = $('#archiveSort')?.value || 'newest';
  const groups = new Map();
  for (const recording of rows) {
    const source = archiveSourceFor(recording);
    const profileName = source?.display_name || recording.source_name;
    const profileId = Number(source?.profile_id || source?.id || recording.source_id);
    let key, label;
    if (mode === 'creator') { key = `creator:${profileId}`; label = profileName; }
    else if (mode === 'session') { key = `session:${recording.session_id}`; label = `${profileName} · ${recording.session_id}`; }
    else { const day = archiveDayKey(recording); key = `day:${day}`; label = archiveDayLabel(day); }
    if (!groups.has(key)) groups.set(key, {key, label, rows: []});
    groups.get(key).rows.push(recording);
  }
  const direction = sort === 'oldest' ? 1 : -1;
  const result = [...groups.values()];
  for (const group of result) group.rows.sort((a,b) => direction * (timestamp(a.started_at) - timestamp(b.started_at)));
  result.sort((a,b) => direction * (timestamp(a.rows[0]?.started_at) - timestamp(b.rows[0]?.started_at)));
  return result;
}

function archiveGroupSummary(group) {
  const bytes = group.rows.reduce((sum,row) => sum + Number(row.size_bytes || 0), 0);
  const seconds = group.rows.reduce((sum,row) => sum + Number(row.duration_seconds || 0), 0);
  const cloud = group.rows.filter(row => !!row.remote_url).length;
  return `${group.rows.length} file · ${humanBytes(bytes)} · ${duration(seconds)} · ${cloud}/${group.rows.length} cloud`;
}

const renderRecordingsV271 = renderRecordings;
renderRecordings = function renderRecordingsV280() {
  fillArchiveIntelControls();
  renderRecordingsV271();
  const root = $('#recordings');
  if (!root) return;
  const nodes = new Map();
  for (const card of root.querySelectorAll('.rec-card')) {
    const id = Number(card.querySelector('[data-rec-action][data-id]')?.dataset.id || 0);
    if (id) nodes.set(id, card);
  }
  const visible = recordings.filter(recording => recordingMatches(recording) && archiveIntelMatches(recording));
  if (!visible.length) {
    root.innerHTML = '<div class="empty">Nessun risultato.</div>';
    $('#recordingFooter').textContent = recordings.length ? `${recordings.length} file totali` : '';
    return;
  }
  const groups = archiveGroupRows(visible);
  root.replaceChildren();
  groups.slice(0, archiveGroupLimit).forEach((group, index) => {
    const details = document.createElement('details');
    details.className = 'archive-group';
    details.open = index < 2;
    const summary = document.createElement('summary');
    summary.innerHTML = `<span><strong>${esc(group.label)}</strong><small>${esc(archiveGroupSummary(group))}</small></span><span class="archive-chevron">⌄</span>`;
    details.append(summary);
    const grid = document.createElement('div');
    grid.className = 'recording-grid archive-group-grid';
    for (const recording of group.rows) {
      const node = nodes.get(Number(recording.id));
      if (node) grid.append(node);
    }
    details.append(grid);
    root.append(details);
  });
  if (groups.length > archiveGroupLimit) {
    const more = document.createElement('button');
    more.className = 'btn soft archive-more';
    more.type = 'button';
    more.dataset.archiveMore = '1';
    more.textContent = `Mostra altri ${Math.min(10, groups.length - archiveGroupLimit)}`;
    root.append(more);
  }
  $('#recordingFooter').textContent = `${visible.length} file · ${groups.length} gruppi · ${recordings.length} totali`;
};

for (const control of ['#recordingSearch', '#recordingStatus']) {
  $(control)?.addEventListener(control === '#recordingSearch' ? 'input' : 'change', () => { archiveGroupLimit = 10; renderRecordings(); });
}

$('#recordings')?.addEventListener('click', event => {
  if (!event.target.closest('[data-archive-more]')) return;
  archiveGroupLimit += 10;
  renderRecordings();
});

const refreshV271 = refresh;
refresh = async function refreshV280(options = {}) {
  const pulsePromise = loadControlRoomPulse();
  await refreshV271(options);
  await pulsePromise;
  renderSources();
  if (activeView === 'archive') renderRecordings();
};
'''
    write("app/static/app.js", app_js)

css = read("app/static/enhancements.css")
if "/* LiveVault Live Intelligence v2.8.0 */" not in css:
    css += r'''

/* LiveVault Live Intelligence v2.8.0 */
.cr-pulse{margin:0 0 20px;padding:13px 12px 12px;border:1px solid var(--line);border-radius:14px;background:rgba(255,255,255,.018)}
.cr-pulse-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}.cr-pulse-head strong{font-size:.88rem}.cr-pulse-head span{font-size:.7rem;color:var(--muted)}
.cr-pulse-scale{display:grid;grid-template-columns:116px minmax(0,1fr);gap:9px;height:17px}.cr-pulse-scale>div{position:relative;border-bottom:1px solid rgba(255,255,255,.07)}.cr-pulse-scale>div span{position:absolute;bottom:2px;transform:translateX(-50%);font-size:.58rem;color:var(--muted)}.cr-pulse-scale>div span:first-child{transform:none}.cr-pulse-scale>div span:last-child{transform:translateX(-100%)}
.cr-pulse-row{display:grid;grid-template-columns:116px minmax(0,1fr);gap:9px;align-items:center;min-height:21px}.cr-pulse-name{font-size:.7rem;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cr-pulse-track{height:8px;position:relative;border-radius:999px;background:rgba(255,255,255,.035);overflow:visible}.cr-pulse-track:after{content:'';position:absolute;right:0;top:-3px;width:1px;height:14px;background:rgba(255,255,255,.14)}
.cr-pulse-block{position:absolute;top:0;height:8px;min-width:3px;border:0;border-radius:999px;background:#58615b;cursor:pointer;padding:0;box-shadow:none}.cr-pulse-block.live{background:#dc3847;box-shadow:0 0 12px rgba(220,56,71,.3)}.cr-pulse-block.rec{outline:1px solid rgba(255,255,255,.34);outline-offset:1px}.cr-pulse-block.saved{background:#758b76}.cr-pulse-block.ended{background:#707671}.cr-pulse-block.missed{background:#c18a35}.cr-pulse-empty{text-align:center;color:var(--muted);font-size:.75rem;padding:6px}
.cr-session-meta{margin-top:9px;padding-top:8px;border-top:1px solid rgba(255,255,255,.06);font-size:.7rem;color:var(--muted)}
.cr-recent-section{margin:0 0 20px}.cr-ended-list{display:grid;gap:7px}.cr-ended-card{display:grid;grid-template-columns:58px minmax(0,1fr);gap:10px;align-items:center;padding:7px;border:1px solid var(--line);border-radius:12px;background:rgba(255,255,255,.018)}.cr-ended-card.saved{border-color:rgba(126,159,130,.25)}.cr-ended-card.missed{border-color:rgba(193,138,53,.34)}.cr-ended-cover{width:58px;aspect-ratio:16/10;border-radius:8px;overflow:hidden;background:#0a0d0e;display:grid;place-items:center;color:var(--muted);font-weight:800}.cr-ended-cover img{width:100%;height:100%;object-fit:cover;filter:saturate(.75) brightness(.78)}.cr-ended-main{display:flex;justify-content:space-between;align-items:center;gap:12px;min-width:0}.cr-ended-main>div:first-child{min-width:0}.creator-link.cr-ended-name{font-weight:800}.cr-ended-main small{display:block;margin-top:3px;color:var(--muted);font-size:.68rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.cr-ended-state{text-align:right;white-space:nowrap}.cr-ended-state strong{display:block;font-size:.68rem;letter-spacing:.03em}.cr-ended-state span{display:block;font-size:.64rem;color:var(--muted);margin-top:2px}
.live-dna{overflow:hidden}.dna-layout{display:grid;grid-template-columns:minmax(210px,.8fr) minmax(280px,1.2fr) auto;gap:18px;align-items:end}.dna-week{height:86px;display:grid;grid-template-columns:repeat(7,1fr);gap:5px;align-items:end}.dna-day{height:100%;display:grid;grid-template-rows:1fr 15px;align-items:end;gap:4px}.dna-day i{display:block;min-height:4px;border-radius:5px 5px 2px 2px;background:rgba(144,174,147,.72)}.dna-day span{text-align:center;font-size:.61rem;color:var(--muted)}.dna-hours{height:74px;display:flex;align-items:end;gap:2px;padding-bottom:16px;border-bottom:1px solid rgba(255,255,255,.06)}.dna-hours i{flex:1;min-width:2px;border-radius:2px 2px 0 0;background:rgba(220,56,71,.58)}.dna-metrics{display:grid;gap:7px;min-width:98px}.dna-metrics span{font-size:.66rem;color:var(--muted)}.dna-metrics strong{display:block;font-size:.91rem;color:var(--text)}
.archive-intel-controls{display:flex;gap:8px;flex-wrap:wrap;flex:1 1 100%}.archive-intel-controls select{min-width:125px}.archive-group{border:1px solid var(--line);border-radius:14px;background:rgba(255,255,255,.012);overflow:hidden;margin-bottom:10px}.archive-group>summary{list-style:none;cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:14px;padding:12px 14px;background:rgba(255,255,255,.022)}.archive-group>summary::-webkit-details-marker{display:none}.archive-group>summary>span:first-child{min-width:0}.archive-group>summary strong{display:block;font-size:.9rem;text-transform:none}.archive-group>summary small{display:block;margin-top:3px;font-size:.67rem;color:var(--muted)}.archive-chevron{color:var(--muted);transition:transform .18s ease}.archive-group[open] .archive-chevron{transform:rotate(180deg)}.archive-group-grid{padding:10px}.archive-more{display:block;margin:16px auto 4px}
@media(max-width:900px){.dna-layout{grid-template-columns:1fr}.dna-metrics{grid-template-columns:repeat(3,1fr)}.archive-intel-controls{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));width:100%}.archive-intel-controls select{min-width:0;width:100%}}
@media(max-width:620px){.cr-pulse-scale,.cr-pulse-row{grid-template-columns:78px minmax(0,1fr)}.cr-pulse-name{font-size:.64rem}.cr-ended-main{display:block}.cr-ended-state{text-align:left;margin-top:5px}.archive-intel-controls{grid-template-columns:1fr}.dna-hours{gap:1px}}
'''
    write("app/static/enhancements.css", css)

write("tests/test_v280_live_intelligence.py", r'''from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_live_pulse_endpoint_and_session_evolution_are_wired():
    main = (ROOT / "app/main.py").read_text(encoding="utf-8")
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    assert '@app.get("/api/control-room/pulse")' in main
    assert 'controlRoomPulseMarkup' in js
    assert 'controlRoomRecentEnded' in js
    assert 'Appena terminate' in js
    assert '✓ SALVATA' in js


def test_live_dna_uses_existing_activity_statistics():
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    assert 'function liveDnaMarkup' in js
    assert 'class="profile-section live-dna"' in js
    assert 'dna-week' in js
    assert 'dna-hours' in js


def test_archive_is_grouped_and_filterable():
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "app/static/enhancements.css").read_text(encoding="utf-8")
    for token in ('archivePeriod', 'archiveCreator', 'archiveProvider', 'archiveStorage', 'archiveGroup', 'archiveSort'):
        assert token in js
    assert 'Per giorno' in js
    assert 'Per creator' in js
    assert 'Per sessione' in js
    assert 'archive-group' in css
''')

print("v2.8.0 Live Intelligence patch applied")
