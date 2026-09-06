const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let csrf = '';
let pendingAction = null;
let pendingPayload = {};
let holdTimer = null;
let holdStart = 0;
let historyRange = 3600;
let powerDirty = false;
let activePowerKey = 'balanced';
let activeWifiOn = true;
let refreshBusy = false;
let historyBusy = false;
let signedIn = true;
let actionBusy = false;
let latestState = null;
let latestHistory = [];
let mediaUuid = '';
let mediaPath = '';
let mediaSignature = '';
let mediaBusy = false;
let mediaHls = null;
let mediaHlsToken = '';
let mediaPlaybackBase = 0;
let mediaPlaybackDuration = 0;
const VALID_VIEWS = new Set(['dashboard','media','storage','system','advanced']);
let currentView = VALID_VIEWS.has(location.hash.slice(1)) ? location.hash.slice(1) : 'dashboard';

const powerProfiles = [
  {key:'eco', name:'ECO', glyph:'E', governor:'powersave', mhz:900, description:'Il nodo respira piano: consumi e temperatura ridotti per monitoraggio e servizi leggeri.'},
  {key:'balanced', name:'Bilanciato', glyph:'B', governor:'schedutil', mhz:1200, description:'Il punto dolce: consumi contenuti e accelerazione immediata quando LiveVault ne ha bisogno.'},
  {key:'performance', name:'Performance', glyph:'P', governor:'schedutil', mhz:1500, description:'Risposta rapida con frequenza dinamica completa, ideale per più stream simultanei.'},
  {key:'max', name:'MAX', glyph:'M', governor:'performance', mhz:1500, description:'Massima reattività costante entro i limiti ufficiali del Compute Module 4.'},
];

const actionLabels = {
  eject_nvme: ['Espulsione sicura NVMe', 'Le registrazioni verranno trasferite al buffer interno da 4 GB mentre Docker resta online. Scollega il cavo soltanto dopo il messaggio finale.'],
  attach_nvme: ['Rimonta NVMe', 'Rimonta NVMe, trasferisce e verifica le parti nel buffer, poi riprende la registrazione su NVMe e lo stitching abituale.'],
  restart_livevault: ['Riavvia LiveVault', 'La registrazione corrente verrà chiusa correttamente e il recorder ripartirà.'],
  restart_docker: ['Riavvia Docker', 'Tutti i container, incluso Coolify, saranno indisponibili per alcuni secondi.'],
  backup_now: ['Avvia backup', 'Crea subito una copia consistente del database LiveVault sulla partizione USB SHARE.'],
  restart_pihole: ['Riavvia Pi-hole', 'Il DNS locale sarà indisponibile per alcuni secondi.'],
  power_profile: ['Applica profilo energetico', 'La frequenza CPU e le opzioni di rete verranno aggiornate immediatamente.'],
  media_eject: ['Espelli supporto USB', 'Chiude l’indicizzazione, smonta il supporto e scarica i buffer. Rimuovilo solo dopo il messaggio finale.'],
  media_rescan: ['Aggiorna supporti media', 'Rileva e monta in sola lettura i dispositivi USB rimovibili consentiti.'],
  reboot: ['Riavvia ASIAIR', 'L’intero server verrà riavviato. Il pannello e LiveVault torneranno automaticamente entro circa due minuti.'],
};

function bytes(value) {
  if (value == null || !Number.isFinite(Number(value)) || value < 0) return '—';
  const units = ['B','KB','MB','GB','TB'];
  let size = Number(value), unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit++; }
  return `${size >= 100 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`;
}
function duration(seconds) {
  seconds = Math.max(0, Number(seconds) || 0);
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d ? `${d}g ${h}h` : h ? `${h}h ${m}m` : `${m} min`;
}
function ring(id, value, label) {
  const el = $(id);
  el.style.setProperty('--value', Math.max(0, Math.min(100, value || 0)));
  el.querySelector('span').textContent = label;
}
function serviceCard(name, state, detail, action = '') {
  const good = state === 'active' || state === true;
  const disabled = state === 'inactive' || state === 'not-found' || state === false;
  return `<article class="service-card"><header><strong>${escapeHtml(name)}</strong><span class="service-state ${good ? 'good' : disabled ? '' : 'warn'}">${good ? 'Attivo' : disabled ? 'Inattivo' : escapeHtml(state)}</span></header><small>${escapeHtml(detail)}</small>${action && good ? `<button class="mini-action" data-action="${action}" data-confirm="true">Riavvia</button>` : ''}</article>`;
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

function mean(points, key) {
  const values = points.filter(point => point[key] != null).map(point => Number(point[key])).filter(Number.isFinite);
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}
function linePath(points, key, min, max) {
  const span = Math.max(1, max - min);
  const start = points[0]?.t || 0, range = Math.max(1, (points.at(-1)?.t || 0) - start);
  let previous = null;
  return points.map(point => {
    if (point[key] == null || !Number.isFinite(Number(point[key]))) { previous = null; return ''; }
    const x = (point.t - start) / range * 600;
    const y = 154 - (Number(point[key]) - min) / span * 132;
    const move = previous == null || point.t - previous > Math.max(30, range / 100);
    previous = point.t;
    return `${move ? 'M' : 'L'}${x.toFixed(1)},${Math.max(12, Math.min(160, y)).toFixed(1)}`;
  }).join(' ');
}
function chartMarkup(points, series, min, max) {
  if (points.length < 2) return '<span class="chart-empty">Raccolta dati reali…</span>';
  const grid = [22,55,88,121,154].map(y => `<line x1="0" y1="${y}" x2="600" y2="${y}"/>`).join('');
  const paths = series.map((item, index) => {
    const path = linePath(points, item.key, min, max);
    return `<path class="chart-line ${item.className}" d="${path}"/>`;
  }).join('');
  const time = point => new Date(point.t * 1000).toLocaleTimeString('it-IT', {hour:'2-digit',minute:'2-digit'});
  return `<svg viewBox="0 0 600 170" preserveAspectRatio="none" role="img" aria-label="${series.map(item => item.key).join(', ')}: ${min.toFixed(0)}–${max.toFixed(0)}"><g class="chart-gridlines">${grid}</g>${paths}</svg><div class="chart-axis"><span>${time(points[0])}</span><span>${time(points.at(-1))}</span></div>`;
}
function measuredWatts(power) {
  return power?.measurement === 'measured' && power.watts != null && Number.isFinite(Number(power.watts)) ? Number(power.watts) : null;
}
function availabilityDate(timestamp) {
  if (!timestamp) return '—';
  return new Date(timestamp * 1000).toLocaleString('it-IT', {day:'2-digit',month:'2-digit',year:'2-digit',hour:'2-digit',minute:'2-digit'});
}
function renderAvailability(availability = {}, rangeSeconds = historyRange) {
  const pct = availability.uptime_percent;
  $('#availabilityPercent').textContent = pct == null ? '—' : `${pct.toFixed(pct >= 99 ? 2 : 1)}%`;
  const known = Math.max(0, rangeSeconds - Number(availability.unknown_seconds || 0));
  $('#availabilityKnown').textContent = availability.unknown_seconds > 0 ? `${duration(known)} monitorati · ${duration(availability.unknown_seconds)} non storicizzati` : `${duration(known)} monitorati`;
  $('#downtimeTotal').textContent = duration(availability.downtime_seconds || 0);
  const rows = availability.downtimes || [];
  $('#downtimeCount').textContent = rows.length ? `${rows.length} event${rows.length === 1 ? 'o' : 'i'} offline` : 'nessun evento offline';
  const last = rows.at(-1);
  $('#lastDowntime').textContent = last ? duration(last.seconds || (last.end-last.start)) : '—';
  $('#lastDowntimeWhen').textContent = last ? `${availabilityDate(last.start)} → ${availabilityDate(last.end)}` : 'nessun downtime registrato';
  const now = Date.now()/1000, start = now - rangeSeconds;
  const segments = rows.map(row => {
    const left = Math.max(0, (row.start - start) / rangeSeconds * 100);
    const right = Math.min(100, (row.end - start) / rangeSeconds * 100);
    const width = Math.max(.25, right-left);
    return `<b class="availability-offline" style="left:${left.toFixed(4)}%;width:${width.toFixed(4)}%" title="Offline ${availabilityDate(row.start)} → ${availabilityDate(row.end)} · ${duration(row.seconds||0)}"></b>`;
  }).join('');
  const unknownWidth = Math.max(0, Math.min(100, Number(availability.unknown_seconds || 0) / rangeSeconds * 100));
  $('#availabilityTimeline').innerHTML = `<i class="availability-online"></i>${unknownWidth ? `<b class="availability-unknown" style="left:0;width:${unknownWidth.toFixed(4)}%" title="Periodo precedente all'inizio del monitoraggio"></b>` : ''}${segments}`;
}

function renderHistory(points, energy = {}, availability = {}, rangeSeconds = historyRange) {
  latestHistory = points;
  renderAvailability(availability, rangeSeconds);
  const cpuAvg = mean(points, 'cpu'), ramAvg = mean(points, 'ram');
  $('#computeChart').innerHTML = chartMarkup(points, [{key:'cpu',className:'cpu'},{key:'ram',className:'ram'}], 0, 100);
  $('#cpuAverage').textContent = points.some(point => point.cpu != null) ? `CPU media ${cpuAvg.toFixed(0)}%` : 'CPU media —';
  $('#ramAverage').textContent = points.some(point => point.ram != null) ? `RAM media ${ramAvg.toFixed(0)}%` : 'RAM media —';

  const temps = points.filter(point => point.temp != null).map(point => Number(point.temp)).filter(Number.isFinite);
  $('#temperatureChart').innerHTML = chartMarkup(points, [{key:'temp',className:'temp'}], 25, Math.max(80, ...temps));
  $('#tempPeak').textContent = temps.length ? `${Math.max(...temps).toFixed(0)}° picco` : '—';
  $('#tempAverage').textContent = temps.length ? `media ${mean(points, 'temp').toFixed(1)}°` : 'media —';

  const trafficPeak = Math.max(0, ...points.flatMap(point => [Number(point.rx) || 0, Number(point.tx) || 0]));
  $('#networkChart').innerHTML = chartMarkup(points, [{key:'rx',className:'cpu'},{key:'tx',className:'ram'}], 0, Math.max(1, trafficPeak));
  $('#networkPeak').textContent = `${bytes(trafficPeak)}/s picco`;

  const disks = points.filter(point => point.disk != null).map(point => Number(point.disk)).filter(Number.isFinite);
  $('#diskChart').innerHTML = chartMarkup(points, [{key:'disk',className:'disk'}], 0, 100);
  $('#diskTrend').textContent = disks.length ? `${disks.at(-1).toFixed(1)}%` : '—';
  $('#diskDelta').textContent = disks.length > 1 ? `variazione ${(disks.at(-1) - disks[0]) >= 0 ? '+' : ''}${(disks.at(-1) - disks[0]).toFixed(2)}%` : 'variazione —';

  const watts = points.filter(point => point.watts != null).map(point => Number(point.watts)).filter(Number.isFinite);
  const wattMax = Math.max(10, ...watts);
  $('#wattChart').innerHTML = chartMarkup(points, [{key:'watts',className:'watt'}], 0, wattMax);
  $('#wattPeak').textContent = energy.peak_watts != null ? `${energy.peak_watts.toFixed(1)} W picco` : '—';
  $('#wattAverage').textContent = energy.average_watts != null ? `media ${energy.average_watts.toFixed(1)} W` : 'media —';
  const coverage = energy.covered_seconds < 60 ? `${energy.covered_seconds} s` : duration(energy.covered_seconds);
  $('#energyMeasured').textContent = energy.wh != null ? `${energy.wh.toFixed(2)} Wh · ${coverage} coperti` : 'In attesa di campioni misurati';
}
async function refreshHistory() {
  if (historyBusy || document.hidden || !signedIn || currentView !== 'system') return;
  historyBusy = true;
  const requestedRange = historyRange;
  try {
    const response = await fetch(`/api/history?range=${requestedRange}`, {cache:'no-store', signal: AbortSignal.timeout(15000)});
    if (response.status === 401) return showLogin();
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    if (requestedRange === historyRange) renderHistory(result.points || [], result.energy || {}, result.availability || {}, result.range || requestedRange);
    $('#historyStatus').textContent = requestedRange <= 86400 ? `Dettaglio 10 s · downtime reale · ${Intl.DateTimeFormat().resolvedOptions().timeZone}` : `Storico compattato · downtime reale · ${Intl.DateTimeFormat().resolvedOptions().timeZone}`;
  } catch (_) { $('#historyStatus').textContent = 'Storico non aggiornato. Riprova con Aggiorna.'; }
  finally { historyBusy = false; if (requestedRange !== historyRange) refreshHistory(); }
}

function previewPower(index, fromServer = false) {
  const profile = powerProfiles[Number(index)] || powerProfiles[1];
  $('#powerSlider').value = String(powerProfiles.indexOf(profile));
  $('#powerSlider').style.setProperty('--power', `${powerProfiles.indexOf(profile) / 3 * 100}%`);
  $('#profileIndex').textContent = `0${powerProfiles.indexOf(profile) + 1} / 04`;
  $('#profileName').textContent = profile.name;
  $('#profileDescription').textContent = profile.description;
  $('#powerGlyph').textContent = profile.glyph;
  $('#power').dataset.profile = profile.key;
  $('#wifiKeep').disabled = false;
  $('#wifiHint').textContent = $('#wifiKeep').checked ? 'Hotspot locale disponibile; Internet continua via Ethernet.' : 'Wi-Fi spento; pannello pubblico e Funnel continuano via Ethernet.';
  if (!fromServer) powerDirty = true;
}
function renderPower(power) {
  if (!power) return;
  activePowerKey = powerProfiles.some(item => item.key === power.profile) ? power.profile : 'balanced';
  activeWifiOn = Boolean(power.wifi_radio);
  const foundIndex = powerProfiles.findIndex(item => item.key === power.profile);
  const index = foundIndex < 0 ? 1 : foundIndex;
  if (!powerDirty) {
    $('#wifiKeep').checked = power.wifi_policy !== 'off';
    previewPower(index, true);
  }
  $('#currentProfile').textContent = `${(power.profile || 'balanced').toUpperCase()} · ATTIVO`;
  $('#powerGovernor').textContent = power.governor || '—';
  $('#powerFrequency').textContent = `${power.current_mhz || 0} / ${power.max_mhz || 0} MHz`;
  $('#powerWifi').textContent = power.hotspot ? 'OpenAstro-AP' : power.wifi_radio ? 'radio attiva' : 'spento';
  $('#powerHealth').textContent = power.undervoltage_now ? 'tensione bassa' : power.throttled_now ? 'limitato ora' : power.power_event_seen ? 'cali passati' : 'stabile';
  $('#powerHealth').className = power.undervoltage_now || power.throttled_now ? 'bad-text' : power.power_event_seen ? 'warn-text' : '';
  const watts = measuredWatts(power);
  $('#powerWatts').textContent = watts != null ? `${watts.toFixed(1)} W DC` : 'Sensore non disponibile';
  $('#quickWifiTitle').textContent = activeWifiOn ? 'Spegni Wi-Fi' : 'Attiva Wi-Fi';
  $('#quickWifiState').textContent = activeWifiOn ? 'Hotspot attivo' : 'Radio disattivata';
  $('#quickWifi').classList.toggle('is-off', !activeWifiOn);
}

function mediaUrl(path, download = false, uuid = mediaUuid) {
  const query = new URLSearchParams({uuid, path});
  if (download) query.set('download', '1');
  return `/api/media/file?${query}`;
}
function mediaThumbUrl(path, uuid = mediaUuid) {
  return `/api/media/thumbnail?${new URLSearchParams({uuid, path})}`;
}
function mediaProbeUrl(path, uuid = mediaUuid) {
  return `/api/media/probe?${new URLSearchParams({uuid, path})}`;
}
function mediaPlanUrl(path, uuid = mediaUuid) { return `/api/media/play-plan?${new URLSearchParams({uuid,path})}`; }
function mediaSubtitleUrl(path, uuid = mediaUuid) { return `/api/media/subtitle?${new URLSearchParams({uuid,path})}`; }
async function stopMediaHls() {
  if (mediaHls) { try { mediaHls.destroy(); } catch (_) {} mediaHls=null; }
  const token=mediaHlsToken; mediaHlsToken='';
  if (token) { try { await fetch('/api/media/hls/stop',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({token})}); } catch (_) {} }
}
function addMediaSubtitleTracks(video, tracks, uuid) {
  (tracks||[]).forEach((track,index)=>{ const node=document.createElement('track'); node.kind='subtitles'; node.label=track.label||`Subtitle ${index+1}`; node.srclang=(track.label||'sub').slice(0,5).toLowerCase(); node.src=mediaSubtitleUrl(track.path,uuid); if(index===0)node.default=false; video.appendChild(node); });
}
let mediaProfile = {continue:[], recently_played:[], favorites:[], active_streams:[], stats:{known_files:0,known_bytes:0}};
let mediaFavoriteKeys = new Set();
let mediaHomeLoadedAt = 0;
function isMediaFavorite(path, uuid = mediaUuid) { return mediaFavoriteKeys.has(`${uuid}:${path}`); }
async function toggleMediaFavorite(path, name = '', uuid = mediaUuid) {
  if (!uuid || !path) return false;
  const key = `${uuid}:${path}`, favorite = !mediaFavoriteKeys.has(key);
  try {
    const response = await fetch('/api/media/favorite', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({uuid,path,favorite}), signal:AbortSignal.timeout(10000)});
    if (response.status === 401) { showLogin(); return !favorite; }
    const result = await response.json(); if (!response.ok || !result.ok) throw new Error(result.error || 'Errore preferiti');
    if (favorite) mediaFavoriteKeys.add(key); else mediaFavoriteKeys.delete(key);
    await loadMediaHome(true); renderMediaFiles();
    return favorite;
  } catch (error) { toast(error.message || 'Preferito non salvato.', true); return !favorite; }
}
function mediaResumePosition(uuid, path) {
  const rows = [...(mediaProfile.continue||[]), ...(mediaProfile.recently_played||[])];
  const row = rows.find(item => item.uuid === uuid && item.path === path);
  return Number(row?.position || 0);
}
async function saveMediaProgress(media, force = false) {
  if (!media || !mediaPlayerUuid || !mediaPlayerPath || !Number.isFinite(media.currentTime)) return;
  const position=mediaPlaybackBase + media.currentTime; const duration=mediaPlaybackDuration || media.duration || 0;
  if (!duration || (!force && position < 2)) return;
  try { await fetch('/api/media/progress',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({uuid:mediaPlayerUuid,path:mediaPlayerPath,position,duration}),signal:AbortSignal.timeout(8000)}); } catch (_) {}
}
function mediaProfileCard(item, kind='history') {
  const available = item.available !== false;
  const thumb = available && ['video','image'].includes(item.category) ? `<img loading="lazy" src="${escapeHtml(mediaThumbUrl(item.path,item.uuid))}" alt="">` : `<span>${available ? mediaCategoryLabel(item.category) : 'OFFLINE'}</span>`;
  const progress = Number(item.duration) > 0 ? Math.max(0,Math.min(100,Number(item.position||0)/Number(item.duration)*100)) : 0;
  return `<button class="media-profile-card ${available?'':'offline'}" data-profile-play="${escapeHtml(item.path)}" data-profile-uuid="${escapeHtml(item.uuid)}" data-profile-name="${escapeHtml(item.name)}" data-profile-category="${escapeHtml(item.category)}" ${available?'':'data-offline="1"'}><div class="media-profile-art">${thumb}${progress>0?`<i style="width:${progress.toFixed(1)}%"></i>`:''}</div><strong>${escapeHtml(item.name)}</strong><small>${available ? (progress>0 ? `${Math.round(progress)}% · ${mediaDuration(item.position)} / ${mediaDuration(item.duration)}` : mediaCategoryLabel(item.category)) : 'Supporto non collegato'}</small></button>`;
}
function bindMediaProfileCards() {
  $$('[data-profile-play]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.offline) return toast('Questo contenuto è ricordato, ma il supporto USB non è collegato.', true);
    mediaUuid = button.dataset.profileUuid; openMediaPlayer(button.dataset.profilePlay,button.dataset.profileName,button.dataset.profileCategory,button.dataset.profileUuid);
  }));
}
function renderMediaHome(profile = {}) {
  mediaProfile = profile;
  mediaFavoriteKeys = new Set((profile.favorites||[]).map(item => `${item.uuid}:${item.path}`));
  $('#mediaKnownFiles').textContent = profile.stats?.known_files || 0; $('#mediaKnownBytes').textContent = `${bytes(profile.stats?.known_bytes || 0)} indicizzati`;
  $('#mediaContinueCount').textContent = (profile.continue||[]).length; $('#mediaFavoriteCount').textContent = (profile.favorites||[]).length;
  $('#mediaStreamCount').textContent = (profile.active_streams||[]).length; $('#mediaStreamDetail').textContent = (profile.active_streams||[]).length ? 'riproduzione in corso' : 'nessuna riproduzione';
  const sections = [['#mediaContinueWrap','#mediaContinue',profile.continue||[],'continue'],['#mediaFavoritesWrap','#mediaFavorites',profile.favorites||[],'favorite'],['#mediaPlayedWrap','#mediaPlayed',profile.recently_played||[],'played']];
  sections.forEach(([wrap,host,rows,kind]) => { $(wrap).hidden=!rows.length; $(host).innerHTML=rows.map(item=>mediaProfileCard(item,kind)).join(''); });
  const streams=profile.active_streams||[]; $('#mediaStreamsWrap').hidden=!streams.length;
  $('#mediaStreams').innerHTML=streams.map(stream=>`<article><div><strong>${escapeHtml(stream.name||stream.path)}</strong><small>${escapeHtml(stream.client||'client')} · ${mediaDuration(stream.seconds||0)}</small></div><span>${bytes(stream.bytes_sent||0)}</span></article>`).join('');
  bindMediaProfileCards(); renderMediaFiles();
}
async function loadMediaHome(force=false) {
  if (!signedIn || (currentView !== 'media' && !force)) return;
  const now=Date.now(); if (!force && now-mediaHomeLoadedAt<10000) return; mediaHomeLoadedAt=now;
  try {
    const response=await fetch('/api/media/home',{cache:'no-store',signal:AbortSignal.timeout(12000)}); if(response.status===401)return showLogin();
    const result=await response.json(); if(response.ok&&result.ok) renderMediaHome(result);
  } catch (_) {}
}
function mediaCategoryLabel(category) { return ({video:'VIDEO',audio:'AUDIO',image:'FOTO',document:'DOC',archive:'ARCHIVIO',other:'FILE',dir:'CARTELLA'})[category] || 'FILE'; }
function mediaDate(timestamp) { return timestamp ? new Date(timestamp * 1000).toLocaleDateString('it-IT',{day:'2-digit',month:'short',year:'numeric'}) : '—'; }
function mediaDuration(value) {
  const seconds = Math.max(0, Number(value) || 0), h = Math.floor(seconds/3600), m = Math.floor((seconds%3600)/60), s = Math.floor(seconds%60);
  return h ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : `${m}:${String(s).padStart(2,'0')}`;
}
let mediaItems = [];
let mediaLibrary = null;
let mediaFilter = 'all';
let mediaView = 'grid';
let mediaSearchText = '';
let mediaSort = 'name';
let mediaPlayerPath = '';
let mediaPlayerName = '';
let mediaPlayerUuid = '';
let mediaResumeTimer = null;

function mediaFilteredItems() {
  let items = [...mediaItems];
  const query = mediaSearchText.trim().toLocaleLowerCase('it-IT');
  if (query) items = items.filter(item => item.name.toLocaleLowerCase('it-IT').includes(query));
  if (mediaFilter === 'favorite') items = items.filter(item => item.type === 'file' && isMediaFavorite(item.path));
  else if (mediaFilter !== 'all') items = items.filter(item => item.type === 'dir' || item.category === mediaFilter);
  if (mediaSort === 'recent') items.sort((a,b) => (a.type !== b.type ? (a.type === 'dir' ? -1 : 1) : (b.modified||0)-(a.modified||0)));
  else if (mediaSort === 'size') items.sort((a,b) => (a.type !== b.type ? (a.type === 'dir' ? -1 : 1) : (b.size||0)-(a.size||0)));
  else items.sort((a,b) => (a.type !== b.type ? (a.type === 'dir' ? -1 : 1) : a.name.localeCompare(b.name, 'it', {numeric:true})));
  return items;
}
function mediaCard(item) {
  if (item.type === 'dir') return `<button class="media-card media-dir" data-media-path="${escapeHtml(item.path)}"><div class="media-art folder-art"><span>DIR</span></div><div class="media-card-copy"><strong>${escapeHtml(item.name)}</strong><small>Cartella</small></div></button>`;
  const favorite = isMediaFavorite(item.path);
  const art = item.thumbnail ? `<div class="media-art has-thumb"><img loading="lazy" src="${escapeHtml(mediaThumbUrl(item.path))}" alt=""><span>${mediaCategoryLabel(item.category)}</span></div>` : `<div class="media-art type-art ${escapeHtml(item.category)}"><span>${mediaCategoryLabel(item.category)}</span></div>`;
  return `<article class="media-card" data-category="${escapeHtml(item.category)}">${art}<div class="media-card-copy"><strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong><small>${bytes(item.size)} · ${mediaDate(item.modified)}</small></div><div class="media-card-actions">${item.streamable ? `<button data-media-play="${escapeHtml(item.path)}" data-media-name="${escapeHtml(item.name)}" data-media-category="${escapeHtml(item.category)}">Riproduci</button>` : `<a href="${escapeHtml(mediaUrl(item.path))}" target="_blank" rel="noopener">Apri</a>`}<a href="${escapeHtml(mediaUrl(item.path,true))}">Download</a><button class="media-fav ${favorite?'active':''}" data-media-fav="${escapeHtml(item.path)}" data-media-name="${escapeHtml(item.name)}" title="Preferito">${favorite?'★':'☆'}</button></div></article>`;
}
function renderMediaFiles() {
  const host = $('#mediaFiles');
  if (!mediaUuid) { host.className = `media-files-v2 ${mediaView}`; host.innerHTML = '<div class="media-empty"><strong>Nessun supporto</strong><small>Collega un’unità USB per iniziare.</small></div>'; return; }
  const items = mediaFilteredItems();
  host.className = `media-files-v2 ${mediaView}`;
  host.innerHTML = items.length ? items.map(mediaCard).join('') : '<div class="media-empty"><strong>Nessun risultato</strong><small>Prova a cambiare ricerca o filtro.</small></div>';
  $$('.media-dir').forEach(button => button.addEventListener('click', () => loadMediaDirectory(button.dataset.mediaPath)));
  $$('[data-media-play]').forEach(button => button.addEventListener('click', () => openMediaPlayer(button.dataset.mediaPlay, button.dataset.mediaName, button.dataset.mediaCategory)));
  $$('[data-media-fav]').forEach(button => button.addEventListener('click', () => toggleMediaFavorite(button.dataset.mediaFav, button.dataset.mediaName)));
}
function renderMediaLibrary(library) {
  mediaLibrary = library;
  const counts = library?.counts || {}, sizes = library?.bytes || {};
  $('#mediaStatVideo').textContent = counts.video || 0; $('#mediaStatVideoSize').textContent = bytes(sizes.video || 0);
  $('#mediaStatAudio').textContent = counts.audio || 0; $('#mediaStatAudioSize').textContent = bytes(sizes.audio || 0);
  $('#mediaStatImage').textContent = counts.image || 0; $('#mediaStatImageSize').textContent = bytes(sizes.image || 0);
  $('#mediaStatTotal').textContent = library?.total_files || 0; $('#mediaStatTotalSize').textContent = bytes(library?.total_bytes || 0);
  const recent = library?.recent || [];
  $('#mediaRecentWrap').hidden = !recent.length;
  $('#mediaRecentCount').textContent = recent.length ? `${recent.length} elementi` : '';
  $('#mediaRecent').innerHTML = recent.slice(0,12).map(item => `<button class="media-recent-card" data-media-recent="${escapeHtml(item.path)}" data-media-name="${escapeHtml(item.name)}" data-media-category="${escapeHtml(item.category)}">${item.thumbnail ? `<img loading="lazy" src="${escapeHtml(mediaThumbUrl(item.path))}" alt="">` : `<span>${mediaCategoryLabel(item.category)}</span>`}<strong>${escapeHtml(item.name)}</strong><small>${mediaDate(item.modified)}</small></button>`).join('');
  $$('.media-recent-card').forEach(button => button.addEventListener('click', () => openMediaPlayer(button.dataset.mediaRecent, button.dataset.mediaName, button.dataset.mediaCategory)));
}
async function loadMediaLibrary(force = false) {
  if (!mediaUuid) return;
  try {
    const query = new URLSearchParams({uuid:mediaUuid}); if (force) query.set('force','1');
    const response = await fetch(`/api/media/library?${query}`, {cache:'no-store', signal:AbortSignal.timeout(30000)});
    if (response.status === 401) return showLogin();
    const result = await response.json();
    if (response.ok && result.ok) renderMediaLibrary(result);
  } catch (_) {}
}
async function loadMediaDirectory(path = mediaPath) {
  if (!mediaUuid || mediaBusy) return;
  mediaBusy = true;
  $('#mediaFiles').innerHTML = '<div class="media-empty"><strong>Caricamento…</strong><small>Lettura del supporto USB.</small></div>';
  try {
    const query = new URLSearchParams({uuid:mediaUuid, path:path || ''});
    const response = await fetch(`/api/media/list?${query}`, {cache:'no-store', signal:AbortSignal.timeout(15000)});
    if (response.status === 401) return showLogin();
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
    mediaPath = result.path || ''; mediaItems = result.items || [];
    $('#mediaDriveLabel').textContent = result.label || 'USB'; $('#mediaPath').textContent = `/${mediaPath}`;
    $('#mediaBack').disabled = !mediaPath; $('#mediaBack').dataset.parent = result.parent || '';
    renderMediaFiles();
  } catch (error) { $('#mediaFiles').innerHTML = `<div class="media-empty"><strong>Supporto non leggibile</strong><small>${escapeHtml(error.message || '')}</small></div>`; }
  finally { mediaBusy = false; }
}
function closeMediaPlayer() {
  const stage=$('#mediaPlayerStage'), media=stage.querySelector('video,audio'); if(media) saveMediaProgress(media,true);
  if(mediaResumeTimer)clearInterval(mediaResumeTimer); mediaResumeTimer=null; stopMediaHls(); stage.innerHTML='';
  mediaPlaybackBase=0; mediaPlaybackDuration=0; if($('#mediaPlayerDialog').open)$('#mediaPlayerDialog').close(); setTimeout(()=>loadMediaHome(true),500);
}
async function openMediaPlayer(path,name,category,uuid=mediaUuid){
  if(!uuid||!path)return; const device=(latestState?.media?.devices||[]).find(item=>item.uuid===uuid); if(!device?.mounted)return toast('Il supporto che contiene questo file non è collegato.',true);
  await stopMediaHls(); mediaPlayerUuid=uuid; mediaPlayerPath=path; mediaPlayerName=name||path.split('/').at(-1); mediaPlaybackBase=0; mediaPlaybackDuration=0;
  $('#mediaPlayerTitle').textContent=mediaPlayerName; $('#mediaPlayerType').textContent=mediaCategoryLabel(category); $('#mediaPlayerPlan').textContent='ANALISI'; $('#mediaPlayerNote').textContent='Analisi compatibilità codec e carico del nodo…';
  $('#mediaPlayerDownload').href=mediaUrl(path,true,uuid); $('#mediaPlayerMeta').innerHTML='<span>Analisi file…</span>'; $('#mediaPlayerFavorite').textContent=isMediaFavorite(path,uuid)?'★ Preferito':'☆ Preferito';
  if(!$('#mediaPlayerDialog').open)$('#mediaPlayerDialog').showModal(); const stage=$('#mediaPlayerStage'); stage.innerHTML='<div class="media-playback-wait"><strong>Preparazione playback…</strong><small>OpenAstro sta scegliendo il percorso più efficiente.</small></div>';
  let plan={mode:'direct',available:true,subtitles:[],duration:0,reason:'Direct Play'};
  try{const r=await fetch(mediaPlanUrl(path,uuid),{cache:'no-store',signal:AbortSignal.timeout(12000)});const x=await r.json();if(r.ok&&x.ok)plan=x;}catch(_){}
  mediaPlaybackDuration=Number(plan.duration||0); const resume=mediaResumePosition(uuid,path); const directUrl=mediaUrl(path,false,uuid); let media=null;
  const bindProgress=()=>{ if(!media)return; media.addEventListener('pause',()=>saveMediaProgress(media,true)); media.addEventListener('ended',()=>saveMediaProgress(media,true)); mediaResumeTimer=setInterval(()=>{if(!media.paused)saveMediaProgress(media);},5000); };
  const originalVideo=()=>{ stage.innerHTML='<video controls playsinline preload="metadata"></video>'; media=stage.querySelector('video'); media.src=directUrl; addMediaSubtitleTracks(media,plan.subtitles,uuid); media.addEventListener('loadedmetadata',()=>{if(resume>5&&resume<media.duration-10)media.currentTime=resume;},{once:true}); mediaPlaybackBase=0; if(!mediaPlaybackDuration)mediaPlaybackDuration=media.duration||0; bindProgress(); };
  if(category==='image'){stage.innerHTML=`<img src="${escapeHtml(directUrl)}" alt="${escapeHtml(mediaPlayerName)}">`;$('#mediaPlayerPlan').textContent='DIRECT';$('#mediaPlayerNote').textContent='Immagine servita direttamente dal supporto USB.';}
  else if(category==='audio'){stage.innerHTML='<audio controls preload="metadata"></audio>';media=stage.querySelector('audio');media.src=directUrl;media.addEventListener('loadedmetadata',()=>{if(resume>5&&resume<media.duration-10)media.currentTime=resume;},{once:true});bindProgress();$('#mediaPlayerPlan').textContent='DIRECT';$('#mediaPlayerNote').textContent=plan.reason||'Audio Direct Play.';}
  else if(plan.mode==='direct'){originalVideo();$('#mediaPlayerPlan').textContent='DIRECT PLAY';$('#mediaPlayerNote').textContent=plan.reason||'Nessuna conversione: il file passa direttamente dalla USB al browser.';}
  else if(plan.available&&String(plan.mode).startsWith('hls_')){
    $('#mediaPlayerPlan').textContent=plan.mode==='hls_copy'?'REMUX HLS':plan.mode==='hls_audio'?'AUDIO → AAC':'TRANSCODE HLS'; $('#mediaPlayerNote').textContent=`${plan.reason}. Finestra seek fallback: circa ${Math.round((plan.hls_window_seconds||360)/60)} min.`;
    try{const r=await fetch('/api/media/hls/start',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({uuid,path,position:resume}),signal:AbortSignal.timeout(15000)});const h=await r.json();if(!r.ok||!h.ok)throw new Error(h.error||'HLS non disponibile');mediaHlsToken=h.token||'';mediaPlaybackBase=Number(h.offset||0);mediaPlaybackDuration=Number(h.plan?.duration||plan.duration||0);stage.innerHTML='<video controls playsinline preload="metadata"></video>';media=stage.querySelector('video');addMediaSubtitleTracks(media,plan.subtitles,uuid);if(window.Hls&&Hls.isSupported()){mediaHls=new Hls({maxBufferLength:60,backBufferLength:90,enableWorker:true});mediaHls.loadSource(h.manifest);mediaHls.attachMedia(media);}else if(media.canPlayType('application/vnd.apple.mpegurl')){media.src=h.manifest;}else throw new Error('Questo browser non supporta HLS/MSE');bindProgress();}catch(error){stage.innerHTML=`<div class="media-playback-blocked"><strong>Fallback HLS non disponibile</strong><small>${escapeHtml(error.message||'Errore HLS')}</small><button id="mediaTryOriginal" class="button secondary">Prova comunque il file originale</button></div>`;$('#mediaTryOriginal').addEventListener('click',originalVideo);}
  }else{stage.innerHTML=`<div class="media-playback-blocked"><strong>Transcode protetto</strong><small>${escapeHtml(plan.reason||'Playback non disponibile')}</small><button id="mediaTryOriginal" class="button secondary">Prova Direct Play</button></div>`;$('#mediaTryOriginal').addEventListener('click',originalVideo);$('#mediaPlayerPlan').textContent='PROTECTED';$('#mediaPlayerNote').textContent=plan.reason||'OpenAstro evita di sottrarre risorse a LiveVault.';}
  try{const response=await fetch(mediaProbeUrl(path,uuid),{cache:'no-store',signal:AbortSignal.timeout(12000)});const data=await response.json();const probe=data.probe||{},format=probe.format||{},streams=probe.streams||[];const video=streams.find(x=>x.codec_type==='video'),audio=streams.find(x=>x.codec_type==='audio');const bits=[];if(format.duration)bits.push(mediaDuration(format.duration));if(video?.width)bits.push(`${video.width}×${video.height}`);if(video?.codec_name)bits.push(video.codec_name.toUpperCase());if(audio?.codec_name)bits.push(audio.codec_name.toUpperCase());if(format.bit_rate)bits.push(`${(Number(format.bit_rate)/1e6).toFixed(1)} Mb/s`);bits.push(bytes(data.size));$('#mediaPlayerMeta').innerHTML=bits.map(value=>`<span>${escapeHtml(value)}</span>`).join('');}catch(_){$('#mediaPlayerMeta').innerHTML=`<span>${bytes(mediaItems.find(i=>i.path===path)?.size||0)}</span>`;}
}
function renderMedia(media = {}) {
  const devices = media.devices || [], mounted = devices.filter(device => device.mounted);
  $('#mediaChip').textContent = mounted.length ? `${mounted.length} support${mounted.length === 1 ? 'o' : 'i'} online` : 'Nessun supporto';
  $('#mediaChip').className = `status-chip ${mounted.length ? 'good' : ''}`;
  $('#mediaSmbPath').textContent = media.smb_path || '\\\\OPENASTRO\\Media'; $('#mediaDlnaName').textContent = media.dlna_name || 'OpenAstro Media';
  $('#mediaSmbState').textContent = media.smb === 'active' ? 'ON' : 'OFF'; $('#mediaSmbState').className = media.smb === 'active' ? 'good' : 'bad';
  $('#mediaDlnaState').textContent = media.dlna === 'active' ? 'ON' : 'OFF'; $('#mediaDlnaState').className = media.dlna === 'active' ? 'good' : 'bad';
  $('#mediaDeviceList').innerHTML = devices.length ? devices.map(device => {
    const usage = device.usage, usedPct = usage?.total ? usage.used/usage.total*100 : 0;
    return `<div class="media-device ${device.uuid === mediaUuid ? 'selected' : ''} ${device.mounted?'':'offline'}"><button class="media-select" data-media-uuid="${escapeHtml(device.uuid)}" ${device.mounted?'':'disabled'}><strong>${escapeHtml(device.label || 'USB')}</strong><small>${escapeHtml(device.model || '')}</small><span>${usage ? `${bytes(usage.free)} liberi · ${escapeHtml(device.fstype.toUpperCase())}` : `${escapeHtml(device.fstype.toUpperCase())} · ricordato / offline`}</span><div class="mini-capacity"><i style="width:${usedPct.toFixed(1)}%"></i></div></button>${device.mounted ? `<button class="media-eject" data-media-eject="${escapeHtml(device.uuid)}" title="Espelli in sicurezza">EJECT</button>` : '<span class="media-offline-chip">OFFLINE</span>'}</div>`;
  }).join('') : '<div class="media-empty side"><strong>Nessuna USB</strong><small>Collega un supporto rimovibile.</small></div>';
  $$('.media-select').forEach(button => button.addEventListener('click', () => { mediaUuid = button.dataset.mediaUuid; mediaPath=''; mediaSignature=''; mediaLibrary=null; renderMedia(media); loadMediaDirectory(''); loadMediaLibrary(); }));
  $$('.media-eject').forEach(button => button.addEventListener('click', event => { event.stopPropagation(); openConfirm('media_eject',{uuid:button.dataset.mediaEject}); }));
  if (mediaUuid && !mounted.some(device => device.uuid === mediaUuid)) { mediaUuid=''; mediaPath=''; mediaItems=[]; mediaLibrary=null; }
  if (!mediaUuid && mounted.length) { mediaUuid=mounted[0].uuid; mediaPath=''; }
  const selected = mounted.find(device => device.uuid === mediaUuid);
  if (selected) {
    $('#mediaDriveModel').textContent = `${selected.model || 'USB STORAGE'} · ${String(selected.fstype||'').toUpperCase()} · READ-ONLY`;
    $('#mediaDriveName').textContent = selected.label || 'Media USB'; const usage=selected.usage;
    $('#mediaDriveMeta').textContent = `${bytes(usage?.used||0)} usati su ${bytes(usage?.total||selected.size||0)}`; $('#mediaDriveFree').textContent = bytes(usage?.free||0); $('#mediaDriveBar').style.width = `${usage?.total ? usage.used/usage.total*100 : 0}%`;
  } else { $('#mediaDriveModel').textContent='NESSUN SUPPORTO'; $('#mediaDriveName').textContent='Media USB'; $('#mediaDriveMeta').textContent='Inserisci un dispositivo USB rimovibile.'; $('#mediaDriveFree').textContent='—'; $('#mediaDriveBar').style.width='0%'; }
  const signature = mounted.map(device => `${device.uuid}:${device.usage?.used||0}`).join('|');
  if (currentView === 'media' && mediaUuid && signature !== mediaSignature) { mediaSignature=signature; loadMediaDirectory(mediaPath); loadMediaLibrary(); }
  if (!mounted.length) { mediaItems=[]; renderMediaFiles(); renderMediaLibrary(null); $('#mediaRecentWrap').hidden=true; }
}

function selectView(view, updateHash = false) {
  view = VALID_VIEWS.has(view) ? view : 'dashboard';
  currentView = view;
  document.body.dataset.view = view;
  $$('[data-view-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.viewPanel === view));
  $$('[data-route]').forEach(link => link.classList.toggle('active', link.dataset.route === view));
  if (updateHash && location.hash !== `#${view}`) history.pushState(null, '', `#${view}`);
  window.scrollTo({top:0, behavior:'auto'});
  if (view === 'system') refreshHistory();
  if (view === 'media') {
    loadMediaHome(true);
    if (mediaUuid) { loadMediaDirectory(mediaPath); loadMediaLibrary(); }
  }
}
function dashboardStatusCard(id, state, detail, tone = '') {
  const strong = $(`#${id}`), card = strong?.closest('article');
  if (!strong || !card) return;
  strong.textContent = state;
  const small = card.querySelector('small'); if (small) small.textContent = detail;
  card.classList.remove('attention','bad'); if (tone) card.classList.add(tone);
}

function render(data) {
  latestState = data;
  document.body.classList.remove('stale');
  csrf = data.csrf || csrf;
  const host = data.host;
  const storage = data.storage;
  const lv = data.livevault || {};
  const online = lv.ok === true;
  const handoffMode = lv.storage_handoff?.mode || (storage.data.mounted ? 'nvme' : 'buffer');
  const mediaOnline = (data.media?.devices || []).filter(item => item.mounted);
  const wattsNow = measuredWatts(data.power);
  dashboardStatusCard('dashLiveState', online ? 'Online' : 'Problema', online ? `${lv.worker?.active_recorders ?? 0} recorder attivi` : 'LiveVault non raggiungibile', online ? '' : 'bad');
  dashboardStatusCard('dashStorageMode', handoffMode === 'buffer' ? 'Buffer 4 GB' : 'NVMe', handoffMode === 'buffer' ? `${bytes(storage.buffer?.used || 0)} / ${bytes(storage.buffer?.total || 0)}` : `${bytes(storage.data?.free || 0)} liberi`, handoffMode === 'buffer' ? 'attention' : '');
  dashboardStatusCard('dashMediaState', mediaOnline.length ? `${mediaOnline.length} USB online` : 'Pronto', mediaOnline.length ? mediaOnline.map(item => item.label || 'USB').join(' · ') : 'Nessun supporto collegato');
  dashboardStatusCard('dashPowerState', wattsNow != null ? `${wattsNow.toFixed(1)} W` : '—', wattsNow != null ? `${data.power.input_volts?.toFixed?.(2) || '—'} V · ${data.power.input_amps?.toFixed?.(3) || '—'} A` : 'Sensore ASIAIR non disponibile', wattsNow == null ? 'attention' : '');
  const mobileConnection = $('#mobileConnectionText'); if (mobileConnection) mobileConnection.textContent = online ? 'Online' : 'Attenzione';
  $('#connectionText').textContent = online ? 'Sistema operativo' : 'LiveVault non disponibile';
  $('.connection').className = `connection ${online ? 'online' : 'offline'}`;
  $('#heroTitle').textContent = online ? 'Il tuo nodo, in diretta.' : storage.data.mounted ? 'LiveVault non risponde.' : 'NVMe scollegato.';
  $('#heroSubtitle').textContent = online ? `LiveVault ${lv.version || ''} è operativo e raggiungibile.` : storage.data.mounted ? 'Il server è online, ma LiveVault non risponde.' : 'Il pannello resta attivo. Ricollega il disco per ripristinare i servizi.';
  $('#hostName').textContent = host.name;
  $('#uptime').textContent = `UP ${duration(host.uptime)}`;
  $('#lastUpdate').textContent = new Date(data.timestamp * 1000).toLocaleTimeString('it-IT', {hour:'2-digit',minute:'2-digit',second:'2-digit'});

  ring('#cpuRing', host.cpu_percent, `${host.cpu_percent.toFixed(0)}%`);
  $('#loadValue').textContent = `Load ${host.load[0]}`;
  $('#coreValue').textContent = `${host.cpu_count} core`;
  ring('#ramRing', host.memory.percent, `${host.memory.percent.toFixed(0)}%`);
  $('#ramUsed').textContent = bytes(host.memory.used);
  $('#ramTotal').textContent = `di ${bytes(host.memory.total)}`;
  const temp = host.temperature ?? 0;
  ring('#tempRing', Math.min(100, temp), host.temperature == null ? '—' : `${temp.toFixed(0)}°`);
  $('#tempState').textContent = host.temperature == null ? 'Sensore non disponibile' : temp > 75 ? 'Caldo' : temp > 62 ? 'Attenzione' : 'Nella norma';
  ring('#diskRing', storage.data.percent, storage.data.mounted ? `${storage.data.percent.toFixed(0)}%` : 'OFF');
  $('#diskFree').textContent = storage.data.mounted ? `${bytes(storage.data.free)} liberi` : 'Scollegato';
  $('#diskTotal').textContent = storage.data.mounted ? `di ${bytes(storage.data.total)}` : 'SERVER assente';
  const network = host.network || {};
  ring('#networkRing', Math.min(100, ((network.rx_rate || 0) + (network.tx_rate || 0)) / 125000), 'NET');
  $('#networkDown').textContent = `↓ ${bytes(network.rx_rate || 0)}/s`;
  $('#networkUp').textContent = `↑ ${bytes(network.tx_rate || 0)}/s`;
  const watts = measuredWatts(data.power);
  ring('#wattRing', watts == null ? 0 : Math.min(100, watts), watts != null ? `${watts.toFixed(1)}W` : '—');
  $('#wattValue').textContent = watts != null ? `${watts.toFixed(1)} watt DC` : 'Non disponibile';
  $('#wattDaily').textContent = watts != null ? `${data.power.input_volts.toFixed(2)} V · ${data.power.input_amps.toFixed(3)} A · ingresso ASIAIR` : 'Sensore ASIAIR non raggiungibile';
  $('#wattValue').title = 'Potenza DC dai sensori ASIAIR, inclusi i carichi collegati. Conversione del driver INDI; non calibrata con wattmetro esterno.';
  renderPower(data.power);
  renderMedia(data.media || {});
  if (currentView === 'media') loadMediaHome();

  const mounted = storage.data.mounted && storage.share.mounted;
  $('#storageChip').textContent = mounted ? 'Montato' : storage.data_present ? 'Rilevato · non montato' : 'Scollegato';
  $('#storageChip').className = `status-chip ${mounted ? 'good' : 'bad'}`;
  $('#dataUsage').textContent = storage.data.mounted ? `${bytes(storage.data.used)} / ${bytes(storage.data.total)}` : 'OFFLINE';
  $('#shareUsage').textContent = storage.share.mounted ? `${bytes(storage.share.used)} / ${bytes(storage.share.total)}` : 'OFFLINE';
  $('#dataBar').style.width = `${storage.data.percent || 0}%`;
  $('#shareBar').style.width = `${storage.share.percent || 0}%`;
  $('#storageNote').textContent = mounted ? 'Disco operativo. Prima di rimuoverlo usa sempre Espelli NVMe.' : storage.data_present ? 'Disco presente ma non montato: premi Rimonta.' : 'Puoi ricollegare l’NVMe: il ripristino sarà automatico.';
  $$('[data-action="eject_nvme"]').forEach(button => { button.disabled = !storage.data.mounted; });
  const navEject = $('.nav-eject span'); if (navEject) navEject.textContent = storage.data.mounted ? 'Espelli NVMe' : 'NVMe scollegato';
  $('#quickNvmeTitle').textContent = storage.data.mounted ? 'Espelli NVMe' : 'NVMe scollegato';
  $('#quickNvmeState').textContent = storage.data.mounted ? 'Passa al buffer interno' : `Buffer ${bytes(storage.buffer?.used || 0)} / ${bytes(storage.buffer?.total || 0)}`;
  $('[data-action="attach_nvme"]').disabled = mounted;
  $('[data-action="backup_now"]').disabled = !storage.share.mounted || !storage.data.mounted;

  const services = [
    ['LiveVault', online, online ? `${lv.worker?.active_recorders ?? '—'} recorder attivi` : 'non raggiungibile', 'restart_livevault'],
    ['Docker', data.services.docker, data.services.docker, 'restart_docker'],
    ['Tailscale', data.services.tailscale, 'rete privata e HTTPS'],
    ['Backup', data.services.backup, data.services.backup],
    ['Pi-hole', data.services.pihole, data.services.pihole === 'active' ? 'DNS attivo' : 'non installato', 'restart_pihole'],
  ];
  $('#serviceGrid').innerHTML = services.map(item => serviceCard(...item)).join('');
  $('#serviceCount').textContent = `${services.filter(item => item[1] === true || item[1] === 'active').length}/${services.length} attivi`;

  $('#interfaceGrid').innerHTML = data.interfaces.map(item => item.available ? `<a class="quick-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener"><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.detail)}</small></div><span>↗</span></a>` : `<div class="quick-link disabled"><div><strong>${escapeHtml(item.name)}</strong><small>Non disponibile</small></div></div>`).join('');
  $('#containerCount').textContent = `${data.containers.length} totali`;
  $('#containerList').innerHTML = data.containers.length ? data.containers.map(item => `<div class="container-row"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.image)}</small><span class="container-state ${item.state === 'running' ? '' : 'bad'}">${escapeHtml(item.status)}</span></div>`).join('') : '<p class="muted">Docker non disponibile.</p>';
  $('#actionLog').innerHTML = data.actions.length ? data.actions.map(line => {
    const [when = '', action = '', result = ''] = line.split('|').map(x => x.trim());
    return `<div class="log-row"><span>${escapeHtml(when)}</span><strong>${escapeHtml(action)}</strong><span>${escapeHtml(result)}</span></div>`;
  }).join('') : '<p class="muted">Nessuna azione manuale registrata.</p>';
  bindActionButtons();
}

async function refresh() {
  if (refreshBusy || document.hidden || !signedIn) return;
  refreshBusy = true;
  try {
    const response = await fetch('/api/state', {cache: 'no-store', signal: AbortSignal.timeout(20000)});
    if (response.status === 401) return showLogin();
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    hideLogin();
    render(await response.json());
  } catch (error) {
    document.body.classList.add('stale');
    $('.connection').className = 'connection offline';
    $('#connectionText').textContent = latestState ? 'Dati non aggiornati' : 'Connessione non disponibile';
  } finally { refreshBusy = false; }
}
function showLogin() {
  signedIn = false;
  csrf = '';
  stopHold();
  if ($('#confirmDialog').open) $('#confirmDialog').close();
  $('main').inert = true;
  const sidebar = $('.sidebar'); if (sidebar) sidebar.inert = true;
  const mobileNav = $('.mobile-nav'); if (mobileNav) mobileNav.inert = true;
  $('#loginGate').hidden = false;
  document.body.classList.add('locked');
}
function hideLogin() {
  signedIn = true;
  $('main').inert = false;
  const sidebar = $('.sidebar'); if (sidebar) sidebar.inert = false;
  const mobileNav = $('.mobile-nav'); if (mobileNav) mobileNav.inert = false;
  $('#loginGate').hidden = true;
  document.body.classList.remove('locked');
  $('#loginError').textContent = '';
}
function toast(message, error = false) {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast show ${error ? 'error' : ''}`;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.className = 'toast', 4500);
}
function openConfirm(action, payload = {}) {
  if (actionBusy) return toast('Un’operazione è già in corso.');
  stopHold();
  pendingAction = action;
  pendingPayload = payload;
  const [title, text] = actionLabels[action] || ['Conferma azione', action];
  $('#dialogTitle').textContent = title;
  $('#dialogText').textContent = text;
  $('#holdAction').style.setProperty('--hold', '0%');
  $('#confirmDialog').showModal();
}
async function executeAction(action, payload = {}) {
  if (actionBusy || !action) return;
  actionBusy = true;
  toast('Azione in corso…');
  try {
    const response = await fetch('/api/action', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({action, ...payload}), signal:AbortSignal.timeout(180000)});
    if (response.status === 401) return showLogin();
    const result = await response.json();
    toast(result.message || result.error || (result.ok ? 'Operazione completata.' : 'Operazione fallita.'), !result.ok);
    if (result.ok) powerDirty = false;
  } catch (error) {
    toast('Connessione interrotta: esito non confermato. Verifica lo stato prima di riprovare.', true);
  } finally {
    actionBusy = false;
    setTimeout(refresh, 2500);
  }
}
function bindActionButtons() {
  $$('[data-action]').forEach(button => {
    if (button.dataset.bound) return;
    button.dataset.bound = '1';
    button.addEventListener('click', () => {
      const action = button.dataset.action;
      if (!actionLabels[action]) return toast('Azione non riconosciuta.', true);
      if (button.dataset.confirm) openConfirm(action); else executeAction(action);
    });
  });
}
function startHold(event) {
  if (holdTimer || !pendingAction || !$('#confirmDialog').open) return;
  event.preventDefault();
  holdStart = performance.now();
  const tick = () => {
    const percent = Math.min(100, (performance.now() - holdStart) / 20);
    $('#holdAction').style.setProperty('--hold', `${percent}%`);
    if (percent >= 100) {
      clearInterval(holdTimer); holdTimer = null;
      const action = pendingAction; pendingAction = null;
      const payload = pendingPayload; pendingPayload = {};
      $('#confirmDialog').close();
      executeAction(action, payload);
    }
  };
  holdTimer = setInterval(tick, 35); tick();
}
function stopHold() {
  if (holdTimer) clearInterval(holdTimer);
  holdTimer = null;
  $('#holdAction').style.setProperty('--hold', '0%');
}

$$('[data-media-filter]').forEach(button => button.addEventListener('click', () => {
  mediaFilter = button.dataset.mediaFilter || 'all'; $$('[data-media-filter]').forEach(item => item.classList.toggle('active', item.dataset.mediaFilter === mediaFilter)); renderMediaFiles();
}));
$('#mediaSearch').addEventListener('input', event => { mediaSearchText = event.target.value || ''; renderMediaFiles(); });
$('#mediaSort').addEventListener('change', event => { mediaSort = event.target.value || 'name'; renderMediaFiles(); });
$('#mediaGridView').addEventListener('click', () => { mediaView='grid'; $('#mediaGridView').classList.add('active'); $('#mediaListView').classList.remove('active'); renderMediaFiles(); });
$('#mediaListView').addEventListener('click', () => { mediaView='list'; $('#mediaListView').classList.add('active'); $('#mediaGridView').classList.remove('active'); renderMediaFiles(); });
$('#mediaLibraryRefresh').addEventListener('click', () => loadMediaLibrary(true));
$('#mediaPlayerClose').addEventListener('click', closeMediaPlayer);
$('#mediaPlayerDialog').addEventListener('close', () => { const media=$('#mediaPlayerStage').querySelector('video,audio'); if(media) saveMediaProgress(media,true); if(mediaResumeTimer) clearInterval(mediaResumeTimer); mediaResumeTimer=null; stopMediaHls(); mediaPlaybackBase=0; mediaPlaybackDuration=0; $('#mediaPlayerStage').innerHTML=''; setTimeout(()=>loadMediaHome(true),500); });
$('#mediaPlayerFavorite').addEventListener('click', async () => { const state=await toggleMediaFavorite(mediaPlayerPath,mediaPlayerName,mediaPlayerUuid); $('#mediaPlayerFavorite').textContent=state?'★ Preferito':'☆ Preferito'; });
$('#mediaBack').addEventListener('click', () => loadMediaDirectory($('#mediaBack').dataset.parent || ''));
$('#mediaCredentials').addEventListener('click', async () => {
  try {
    const response = await fetch('/api/media/credentials', {cache:'no-store'});
    if (response.status === 401) return showLogin();
    const result = await response.json();
    const text = result.password ? `Utente: ${result.username} · Password: ${result.password}` : `Utente: ${result.username} · password non disponibile`;
    $('#mediaCredentialsText').textContent = text;
    if (result.password && navigator.clipboard?.writeText) navigator.clipboard.writeText(`${result.username}
${result.password}`).catch(() => {});
  } catch (_) { toast('Credenziali SMB non disponibili.', true); }
});
$('#cancelAction').addEventListener('click', () => $('#confirmDialog').close());
$('#confirmDialog').addEventListener('close', () => { stopHold(); pendingAction = null; pendingPayload = {}; });
$('#confirmDialog').addEventListener('cancel', stopHold);
window.addEventListener('blur', stopHold);
$('#holdAction').addEventListener('keydown', event => { if ([' ', 'Enter'].includes(event.key) && !event.repeat) startHold(event); });
$('#holdAction').addEventListener('keyup', stopHold);
$('#holdAction').addEventListener('pointerdown', startHold);
$('#holdAction').addEventListener('pointerup', stopHold);
$('#holdAction').addEventListener('pointerleave', stopHold);
$('#holdAction').addEventListener('pointercancel', stopHold);
$('#powerSlider').addEventListener('input', event => previewPower(event.target.value));
$('#wifiKeep').addEventListener('change', () => {
  powerDirty = true;
  $('#wifiHint').textContent = $('#wifiKeep').checked ? 'Hotspot locale disponibile; Internet continua via Ethernet.' : 'Wi-Fi spento; pannello pubblico e Funnel continuano via Ethernet.';
});
$('#quickWifi').addEventListener('click', () => {
  const targetWifi = activeWifiOn ? 'off' : 'on';
  actionLabels.power_profile = [activeWifiOn ? 'Spegni hotspot Wi-Fi' : 'Attiva hotspot Wi-Fi', `Il profilo CPU ${activePowerKey.toUpperCase()} resterà invariato. ${activeWifiOn ? 'Il pannello pubblico continuerà a funzionare tramite Ethernet.' : 'La rete locale OpenAstro-AP verrà riattivata.'}`];
  openConfirm('power_profile', {profile:activePowerKey, wifi:targetWifi});
});
$('#applyPower').addEventListener('click', () => {
  const profile = powerProfiles[Number($('#powerSlider').value)] || powerProfiles[1];
  const wifi = $('#wifiKeep').checked ? 'on' : 'off';
  actionLabels.power_profile = [`Attiva ${profile.name}`, `${profile.description} CPU massima ${profile.mhz} MHz. Hotspot Wi-Fi: ${wifi === 'on' ? 'attivo' : 'spento'}.`];
  openConfirm('power_profile', {profile:profile.key, wifi});
});
$$('[data-range]').forEach(button => button.addEventListener('click', () => {
  historyRange = Number(button.dataset.range);
  $$('[data-range]').forEach(item => { item.classList.toggle('active', item === button); item.setAttribute('aria-pressed', String(item === button)); });
  refreshHistory();
}));
$('#loginForm').addEventListener('submit', async event => {
  event.preventDefault();
  $('#loginError').textContent = '';
  const submit = event.currentTarget.querySelector('button');
  submit.disabled = true;
  try {
    const response = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:$('#loginUsername').value, password:$('#loginPassword').value})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Accesso non riuscito.');
    $('#loginPassword').value = '';
    hideLogin();
    await refresh();
    if (currentView === 'system') await refreshHistory();
    if (currentView === 'media') await loadMediaHome(true);
  } catch (error) { $('#loginError').textContent = error.message; }
  submit.disabled = false;
});
$('#logoutButton').addEventListener('click', async () => { await fetch('/api/logout', {method:'POST'}); showLogin(); });
$$('[data-route]').forEach(link => link.addEventListener('click', event => { event.preventDefault(); selectView(link.dataset.route, true); }));
window.addEventListener('hashchange', () => selectView(location.hash.slice(1) || 'dashboard'));
document.addEventListener('visibilitychange', () => { if (document.hidden) stopHold(); else { refresh(); if (currentView === 'system') refreshHistory(); if (currentView === 'media') loadMediaHome(true); } });
$('#refreshAll').addEventListener('click', () => { refresh(); refreshHistory(); });
$('#exportMetrics').addEventListener('click', () => {
  if (!latestHistory.length) return toast('Nessun campione da esportare.', true);
  const keys = ['t', 'cpu', 'ram', 'temp', 'disk', 'rx', 'tx', 'watts', 'power_measurement', 'input_volts', 'input_amps', 'estimated_watts'];
  const csv = ['timestamp_utc,cpu_percent,ram_percent,temperature_c,disk_percent,download_bytes_s,upload_bytes_s,measured_dc_watts,power_measurement,input_volts,input_amps,estimated_watts', ...latestHistory.map(row => keys.map(key => key === 't' ? new Date(row.t * 1000).toISOString() : row[key] ?? '').join(','))].join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = 'openastro-telemetria.csv'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
bindActionButtons();
selectView(currentView);
refresh();
if (currentView === 'system') refreshHistory();
if (currentView === 'media') loadMediaHome(true);
setInterval(refresh, 5000);
setInterval(() => { if (currentView === 'system') refreshHistory(); }, 30000);
setInterval(() => { if (currentView === 'media') loadMediaHome(); }, 10000);
