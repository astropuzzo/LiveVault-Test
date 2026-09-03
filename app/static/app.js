const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const app = $('#app');
const login = $('#login');

let sources = [];
let recordings = [];
let providers = [];
let statusData = null;
let settingsData = null;
let libraryMeta = {categories: [], collections: [], smart_counts: {}};
let libraryProfiles = [];
let profileData = null;
let statisticsData = null;
let statisticsDays = 30;
let profileStatisticsDays = 30;
let statisticsBusy = false;
let lastStatisticsLoad = 0;
let refreshBusy = false;
let lastRecordingLoad = 0;
let activeView = 'dashboard';
let librarySmart = 'all';
let libraryMode = localStorage.getItem('livevault-library-view') === 'list' ? 'list' : 'grid';
let sourceFilterId = Number(new URLSearchParams(location.search).get('source')) || 0;
const selectedProfiles = new Set();
const DISPLAY_TIME_ZONE = 'Europe/Berlin';
const DISPLAY_TIME_ZONE_LABEL = 'Frankfurt';

function esc(value = '') {
  return String(value).replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[char]);
}

function safeUrl(value = '') {
  value = String(value || '').trim();
  if(!value)return '';
  try {
    const url = new URL(value, location.origin);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch {
    return '';
  }
}

function humanBytes(bytes = 0) {
  let value = Number(bytes) || 0;
  for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
    if (value < 1024 || unit === 'TB') return `${value.toFixed(1)} ${unit}`;
    value /= 1024;
  }
}

function duration(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const rest = value % 60;
  return hours ? `${hours}h ${minutes}m` : `${minutes}:${String(rest).padStart(2, '0')}`;
}

function timestamp(value) {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function ago(value) {
  const time = timestamp(value);
  if (!time) return 'mai';
  const seconds = Math.max(0, (Date.now() - time) / 1000);
  if (seconds < 60) return 'ora';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min fa`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h fa`;
  return `${Math.floor(seconds / 86400)} g fa`;
}

function dateText(value) {
  if (!timestamp(value)) return '—';
  return new Intl.DateTimeFormat('it-IT', {
    timeZone: DISPLAY_TIME_ZONE,
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
  }).format(new Date(value));
}

function dateFull(value) {
  if (!timestamp(value)) return '—';
  return new Intl.DateTimeFormat('it-IT', {
    timeZone: DISPLAY_TIME_ZONE,
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'
  }).format(new Date(value));
}

function creatorLinkMarkup(sourceId, name, extraClass = '') {
  if (!sourceId) return `<span class="${esc(extraClass)}">${esc(name)}</span>`;
  return `<button class="creator-link ${esc(extraClass)}" data-profile-link="${Number(sourceId)}" type="button">${esc(name)}</button>`;
}

function statNumber(value, suffix = '') {
  const number = Number(value) || 0;
  return `${new Intl.NumberFormat('it-IT', {maximumFractionDigits: 1}).format(number)}${suffix}`;
}

function activityChartSvg(rows = []) {
  if (!rows.length || !rows.some(row => Number(row.online_seconds) || Number(row.recorded_seconds))) return '<div class="empty compact">Nessun dato.</div>';
  const width = 820, height = 230, top = 12, bottom = 34, chartHeight = height - top - bottom;
  const maxValue = Math.max(1, ...rows.flatMap(row => [Number(row.online_seconds) || 0, Number(row.recorded_seconds) || 0]));
  const groupWidth = width / rows.length;
  const barWidth = Math.max(.5, Math.min(9, groupWidth * .30));
  const skip = rows.length > 120 ? 30 : rows.length > 60 ? 14 : rows.length > 31 ? 7 : rows.length > 14 ? 4 : 1;
  let bars = '';
  let labels = '';
  rows.forEach((row, index) => {
    const online = Number(row.online_seconds) || 0;
    const recorded = Number(row.recorded_seconds) || 0;
    const center = index * groupWidth + groupWidth / 2;
    const onlineHeight = online / maxValue * chartHeight;
    const recordedHeight = recorded / maxValue * chartHeight;
    bars += `<rect class="chart-bar online" x="${(center - barWidth - .5).toFixed(2)}" y="${(top + chartHeight - onlineHeight).toFixed(2)}" width="${barWidth.toFixed(2)}" height="${onlineHeight.toFixed(2)}"><title>${esc(row.date)} · online ${esc(duration(online))}</title></rect>`;
    bars += `<rect class="chart-bar recorded" x="${(center + .5).toFixed(2)}" y="${(top + chartHeight - recordedHeight).toFixed(2)}" width="${barWidth.toFixed(2)}" height="${recordedHeight.toFixed(2)}"><title>${esc(row.date)} · registrato ${esc(duration(recorded))}</title></rect>`;
    if (index % skip === 0 || index === rows.length - 1) labels += `<text class="chart-label" x="${center.toFixed(2)}" y="${height - 9}" text-anchor="middle">${esc(row.date.slice(5))}</text>`;
  });
  return `<svg class="activity-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Grafico tempo online e registrato"><line class="chart-axis" x1="0" y1="${top + chartHeight}" x2="${width}" y2="${top + chartHeight}"></line>${bars}${labels}</svg>`;
}

function hourlyChartSvg(rows = []) {
  if (!rows.length || !rows.some(row => Number(row.online_seconds))) return '<div class="empty compact">Nessun dato.</div>';
  const width = 820, height = 220, top = 12, bottom = 30, chartHeight = height - top - bottom;
  const maxValue = Math.max(1, ...rows.map(row => Number(row.online_seconds) || 0));
  const groupWidth = width / 24;
  const barWidth = Math.max(5, groupWidth * .56);
  let bars = '';
  rows.forEach((row, index) => {
    const value = Number(row.online_seconds) || 0;
    const barHeight = value / maxValue * chartHeight;
    const x = index * groupWidth + (groupWidth - barWidth) / 2;
    bars += `<rect class="chart-bar online" x="${x.toFixed(2)}" y="${(top + chartHeight - barHeight).toFixed(2)}" width="${barWidth.toFixed(2)}" height="${barHeight.toFixed(2)}"><title>${String(index).padStart(2, '0')}:00 · ${esc(duration(value))}</title></rect>`;
    if (index % 3 === 0) bars += `<text class="chart-label" x="${(index * groupWidth + groupWidth / 2).toFixed(2)}" y="${height - 8}" text-anchor="middle">${String(index).padStart(2, '0')}</text>`;
  });
  return `<svg class="activity-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Grafico distribuzione oraria"><line class="chart-axis" x1="0" y1="${top + chartHeight}" x2="${width}" y2="${top + chartHeight}"></line>${bars}</svg>`;
}

function statisticsSummaryMarkup(data, compact = false) {
  const summary = data?.summary || {};
  const metrics = [
    ['Tempo online', duration(summary.online_seconds || 0), `${summary.days_online || 0} gg`],
    ['Tempo registrato', duration(summary.recorded_seconds || 0), `${summary.recording_sessions || 0} REC`],
    ['Sessioni live', summary.live_sessions || 0, `max ${duration(summary.longest_live_seconds || 0)}`],
    ['Copertura', statNumber(summary.coverage_percent || 0, '%'), ''],
    ['Live ora', summary.online_now || 0, compact ? '' : `${summary.creator_count || 0} creator`],
  ];
  return metrics.map(([label, value, note]) => `<article class="stats-metric panel"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`).join('');
}

function statisticsHistoryNote(data) {
  const summary = data?.summary || {};
  return Number(summary.estimated_online_seconds) > 0 ? 'Storico pre-2.6: stima.' : '';
}

function toast(message, type = 'good') {
  const element = document.createElement('div');
  element.className = `toast ${type}`;
  element.textContent = message;
  $('#toastRegion').append(element);
  setTimeout(() => element.remove(), 4200);
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    credentials: 'same-origin',
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})}
  });
  if (response.status === 401 && url !== '/api/login') {
    app.classList.add('hidden');
    login.classList.remove('hidden');
    throw new Error('auth');
  }
  let data = {};
  try { data = await response.json(); } catch { /* empty response */ }
  if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
  return data;
}

function setBusy(button, busy, label = '') {
  if (!button) return;
  if (busy) {
    if (!button.dataset.oldLabel) button.dataset.oldLabel = button.textContent;
    button.disabled = true;
    if (label) button.textContent = label;
  } else {
    button.disabled = false;
    if (button.dataset.oldLabel) {
      button.textContent = button.dataset.oldLabel;
      delete button.dataset.oldLabel;
    }
  }
}

function openModal(id) {
  const modal = $(`#${id}`);
  if (!modal) return;
  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');
  setTimeout(() => modal.querySelector('input:not([type="hidden"]), select, button')?.focus(), 0);
}

function closeModal(id) {
  const modal = $(`#${id}`);
  if (!modal) return;
  modal.classList.add('hidden');
  if (!$$('.modal:not(.hidden)').length) document.body.classList.remove('modal-open');
  if (id === 'videoModal') {
    $('#videoPlayer').pause();
    $('#videoPlayer').removeAttribute('src');
    $('#videoPlayer').load();
  }
  if (id === 'profileModal') profileData = null;
}

function statusLabel(value) {
  return ({
    recording: 'REC', live: 'LIVE', offline: 'Offline', paused: 'In pausa', archived: 'Archiviata',
    error: 'Errore', unknown: '—', is_upcoming: 'Programmata', post_live: 'Appena terminata',
    was_live: 'Terminata', not_live: 'Non live'
  })[value] || value;
}

function fallbackProviders() {
  return [
    {id: 'auto', label: 'Rileva automaticamente', input_label: 'Username o URL della live', placeholder: 'Username oppure https://...'},
    {id: 'chaturbate', label: 'Chaturbate', input_label: 'Username o URL Chaturbate', placeholder: 'es. rich_roxy'}
  ];
}

function populateProviders() {
  const select = $('#sourcePlatform');
  const selected = select.value || 'auto';
  select.innerHTML = providers.map(provider =>
    `<option value="${esc(provider.id)}">${esc(provider.label)}${provider.support_level === 'beta' ? ' · beta' : ''}</option>`
  ).join('');
  select.value = providers.some(provider => provider.id === selected) ? selected : 'auto';
  updateSourceInputHint();
}

async function loadProviders() {
  try {
    providers = await api('/api/providers');
  } catch (error) {
    if (error.message === 'auth') throw error;
    providers = fallbackProviders();
    toast('Catalogo provider non disponibile: uso modalità compatibile', 'bad');
  }
  populateProviders();
}

function updateSourceInputHint() {
  const provider = providers.find(item => item.id === $('#sourcePlatform').value) || providers[0];
  if (!provider) return;
  $('#sourceInputLabel').textContent = provider.input_label;
  $('#sourceSlug').placeholder = provider.placeholder;
}

function profileGroups() {
  const groups = new Map();
  for (const source of sources) {
    const profileId = Number(source.profile_id || source.id);
    if (!groups.has(profileId)) groups.set(profileId, []);
    groups.get(profileId).push(source);
  }
  return groups;
}

function newestValue(rows, field) {
  return rows.reduce((best, row) => timestamp(row[field]) > timestamp(best) ? row[field] : best, null);
}

function buildLibraryProfiles() {
  libraryProfiles = [...profileGroups()].map(([profileId, rows]) => {
    const representative = rows.find(row => !row.archived) || rows[0];
    const statuses = rows.map(row => row.last_status);
    const live = statuses.some(status => ['recording', 'live'].includes(status));
    const error = rows.some(row => row.last_status === 'error' || String(row.last_error || '').trim());
    const enabled = rows.some(row => row.enabled && !row.archived);
    const status = live ? (statuses.includes('recording') ? 'recording' : 'live') : error ? 'error' : enabled ? 'offline' : 'paused';
    const categories = representative.categories || [];
    const collections = representative.collections || [];
    const lastRecordingAt = newestValue(rows, 'last_recording_at');
    return {
      profile_id: profileId,
      representative_id: representative.id,
      display_name: representative.display_name || representative.name,
      favorite: !!representative.favorite,
      notes: representative.notes || '',
      categories,
      collections,
      sources: rows,
      providers: [...new Set(rows.map(row => row.platform))],
      provider_labels: [...new Set(rows.map(row => row.provider_label || row.platform))],
      cover_thumbnail_url: rows.find(row => row.cover_thumbnail_url)?.cover_thumbnail_url || '',
      recording_count: rows.reduce((sum, row) => sum + Number(row.recording_count || 0), 0),
      session_count: rows.reduce((sum, row) => sum + Number(row.session_count || 0), 0),
      uploaded_count: rows.reduce((sum, row) => sum + Number(row.uploaded_count || 0), 0),
      failed_count: rows.reduce((sum, row) => sum + Number(row.failed_count || 0), 0),
      total_bytes: rows.reduce((sum, row) => sum + Number(row.total_bytes || 0), 0),
      total_duration_seconds: rows.reduce((sum, row) => sum + Number(row.total_duration_seconds || 0), 0),
      last_recording_at: lastRecordingAt,
      last_checked_at: newestValue(rows, 'last_checked_at'),
      last_live_at: newestValue(rows, 'last_live_at'),
      last_seen_live_at: newestValue(rows, 'last_seen_live_at'),
      enabled,
      archived: rows.every(row => row.archived),
      status,
      attention: error || rows.some(row => Number(row.failed_count || 0) > 0)
    };
  }).sort((a, b) => Number(b.favorite) - Number(a.favorite) || a.display_name.localeCompare(b.display_name, 'it'));
}

function profileForId(profileId) {
  return libraryProfiles.find(profile => profile.profile_id === Number(profileId));
}

function populateProfileOptions(selectedProfileId = 0, editing = false) {
  const select = $('#sourceProfile');
  const first = editing ? '<option value="">Mantieni il profilo attuale</option>' : '<option value="">Crea un nuovo profilo</option>';
  select.innerHTML = first + libraryProfiles.map(profile =>
    `<option value="${profile.profile_id}">${esc(profile.display_name)} · ${profile.sources.length} ${profile.sources.length === 1 ? 'account' : 'account'}</option>`
  ).join('');
  if (selectedProfileId) select.value = String(selectedProfileId);
}

function showView(name, updateHash = true) {
  const known = ['dashboard', 'library', 'archive', 'statistics'];
  activeView = known.includes(name) ? name : 'dashboard';
  $$('.view-section').forEach(view => view.classList.toggle('hidden', view.dataset.page !== activeView));
  $$('.nav-link').forEach(link => {
    const current = link.dataset.view === activeView;
    link.classList.toggle('active', current);
    if (current) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
  if (updateHash) {
    const url = new URL(location.href);
    url.hash = activeView;
    history.replaceState({}, '', url);
  }
  if (activeView === 'library') renderLibrary();
  if (activeView === 'statistics') loadStatistics(statisticsDays).catch(error => toast(error.message, 'bad'));
  if (activeView === 'archive') {
    renderRecordings();
    if (Date.now() - lastRecordingLoad > 5000) refresh({includeRecordings: true});
  }
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function dashboardSourceRows() {
  return sources.filter(source => !source.archived);
}

function lastBroadcastChip(source) {
  if (source.last_live_at) {
    const prefix = source.metadata_status === 'restricted' ? 'Ultimo dato provider' : 'Ultima live provider';
    return `<span class="chip">${prefix}: ${esc(dateFull(source.last_live_at))} · ${esc(ago(source.last_live_at))}</span>`;
  }
  if (source.metadata_status === 'unsupported') return '';
  const label = source.metadata_status === 'never' ? 'nessuna live registrata' : 'dato non disponibile';
  return `<span class="chip" title="${esc(source.metadata_error || '')}">${esc(label)}</span>`;
}

function renderSources() {
  const rows = dashboardSourceRows();
  const root = $('#sources');
  $('#sourceCount').textContent = rows.length;
  if (!rows.length) {
    root.innerHTML = '<div class="empty">Nessuna sorgente.</div>';
    return;
  }
  root.innerHTML = rows.map(source => {
    const live = ['recording', 'live'].includes(source.last_status);
    const publicUrl = safeUrl(source.source_url);
    const cloudUrl = safeUrl(source.latest_cloud_url);
    const folderUrl = safeUrl(source.gofile_folder_url);
    const reference = String(source.slug || '').startsWith('http') ? source.slug : `@${source.slug}`;
    const seen = source.last_seen_live_at
      ? `<span class="chip">Rilevata live ${esc(ago(source.last_seen_live_at))}</span>` : '';
    return `<article class="source-card ${live ? 'live' : ''}">
      <div class="source-top">
        <div>${creatorLinkMarkup(source.id, source.display_name || source.name, 'source-name')}<div class="source-slug">${esc(source.provider_label)} · ${esc(reference)}</div></div>
        <span class="source-status ${esc(source.last_status)}">${esc(statusLabel(source.last_status))}</span>
      </div>
      <div class="source-meta"><span class="chip">${esc(source.quality)}</span><span class="chip">Controllata ${esc(ago(source.last_checked_at))}</span>${lastBroadcastChip(source)}${seen}</div>
      <div class="source-stats">
        <div><strong>${source.recording_count || 0}</strong><span>file</span></div>
        <div><strong>${source.session_count || 0}</strong><span>sessioni</span></div>
        <div><strong>${humanBytes(source.total_bytes || 0)}</strong><span>registrati</span></div>
        <div><strong>${duration(source.total_duration_seconds || 0)}</strong><span>durata</span></div>
        <div><strong>${source.uploaded_count || 0}</strong><span>cloud</span></div>
        <div><strong>${source.failed_count || 0}</strong><span>problemi</span></div>
      </div>
      ${source.last_recording_at ? `<div class="source-note">Ultima registrazione ${esc(ago(source.last_recording_at))}</div>` : ''}
      ${source.metadata_error ? `<div class="source-note warning">${esc(source.metadata_error)}</div>` : ''}
      ${source.last_error ? `<div class="rec-error">${esc(source.last_error)}</div>` : ''}
      <div class="source-actions">
        ${publicUrl ? `<a class="btn soft" href="${esc(publicUrl)}" target="_blank" rel="noopener">Sorgente ↗</a>` : ''}
        <button class="btn soft" data-action="check" data-id="${source.id}" type="button">Controlla</button>
        <button class="btn soft" data-action="archive" data-id="${source.id}" type="button">Archivio</button>
        ${folderUrl ? `<a class="btn accent" href="${esc(folderUrl)}" target="_blank" rel="noopener">Cartella Gofile ↗</a>` : source.organize_cloud ? `<button class="btn accent" data-action="organize" data-id="${source.id}" type="button">Crea cartella Gofile</button>` : ''}
        <button class="btn soft" data-action="profile" data-id="${source.id}" type="button">Profilo</button>
        <button class="btn soft" data-action="edit" data-id="${source.id}" type="button">Modifica</button>
        <button class="btn soft" data-action="toggle" data-id="${source.id}" type="button">${source.enabled ? (live ? 'Stop + pausa' : 'Pausa') : 'Riattiva'}</button>
        ${cloudUrl && !folderUrl ? `<a class="btn soft" href="${esc(cloudUrl)}" target="_blank" rel="noopener">Ultimo cloud ↗</a>` : ''}
        <span class="spacer"></span><button class="btn danger" data-action="delete" data-id="${source.id}" type="button">Archivia sorgente</button>
      </div>
    </article>`;
  }).join('');
}

function tagMarkup(items, className = 'library-tag') {
  return (items || []).map(item =>
    `<span class="${className}" style="--tag:${esc(item.color)}">${esc(item.name)}</span>`
  ).join('');
}

function smartMatch(profile) {
  if (librarySmart === 'live') return ['recording', 'live'].includes(profile.status);
  if (librarySmart === 'favorites') return profile.favorite;
  if (librarySmart === 'attention') return profile.attention;
  if (librarySmart === 'paused') return !profile.enabled;
  if (librarySmart === 'uncategorized') return !profile.categories.length;
  if (librarySmart === 'never') return profile.recording_count === 0;
  if (librarySmart === 'recent') return timestamp(profile.last_recording_at) > Date.now() - 7 * 86400000;
  return true;
}

function filteredProfiles() {
  const query = $('#librarySearch').value.trim().toLocaleLowerCase('it');
  const provider = $('#libraryProvider').value;
  const status = $('#libraryStatus').value;
  const category = Number($('#libraryCategory').value) || 0;
  const collection = Number($('#libraryCollection').value) || 0;
  return libraryProfiles.filter(profile => {
    const blob = [profile.display_name, profile.notes, ...profile.sources.flatMap(row => [row.name, row.slug, row.provider_label])].join(' ').toLocaleLowerCase('it');
    return smartMatch(profile)
      && (!query || blob.includes(query))
      && (provider === 'all' || profile.providers.includes(provider))
      && (status === 'all' || (status === 'paused' ? !profile.enabled : status === 'error' ? profile.attention : profile.status === status))
      && (!category || profile.categories.some(item => item.id === category))
      && (!collection || profile.collections.some(item => item.id === collection));
  });
}

function libraryCover(profile) {
  const cover = safeUrl(profile.cover_thumbnail_url);
  if (cover) return `<img src="${esc(cover)}" loading="lazy" alt="Copertina di ${esc(profile.display_name)}">`;
  const initials = profile.display_name.split(/\s+/).slice(0, 2).map(part => part[0] || '').join('').toUpperCase() || 'LV';
  return `<span aria-hidden="true">${esc(initials)}</span>`;
}

function renderLibraryCounts() {
  const counts = {
    all: libraryProfiles.length,
    live: libraryProfiles.filter(profile => ['recording', 'live'].includes(profile.status)).length,
    favorites: libraryProfiles.filter(profile => profile.favorite).length,
    attention: libraryProfiles.filter(profile => profile.attention).length,
    paused: libraryProfiles.filter(profile => !profile.enabled).length,
    uncategorized: libraryProfiles.filter(profile => !profile.categories.length).length,
    never: libraryProfiles.filter(profile => profile.recording_count === 0).length,
    recent: libraryProfiles.filter(profile => timestamp(profile.last_recording_at) > Date.now() - 7 * 86400000).length
  };
  $('#smartCountAll').textContent = counts.all;
  $('#smartCountLive').textContent = counts.live;
  $('#smartCountFavorites').textContent = counts.favorites;
  $('#smartCountAttention').textContent = counts.attention;
  $('#smartCountPaused').textContent = counts.paused;
  $('#smartCountUncategorized').textContent = counts.uncategorized;
  $('#smartCountNever').textContent = counts.never;
  $('#smartCountRecent').textContent = counts.recent;
}

function updateSelectionUi(visible = filteredProfiles()) {
  for (const id of [...selectedProfiles]) if (!profileForId(id)) selectedProfiles.delete(id);
  const selectedVisible = visible.filter(profile => selectedProfiles.has(profile.profile_id)).length;
  const selectAll = $('#selectAllLibrary');
  selectAll.checked = visible.length > 0 && selectedVisible === visible.length;
  selectAll.indeterminate = selectedVisible > 0 && selectedVisible < visible.length;
  $('#bulkCount').textContent = selectedProfiles.size;
  $('#bulkBar').classList.toggle('hidden', selectedProfiles.size === 0);
}

function renderLibrary() {
  renderLibraryCounts();
  const visible = filteredProfiles();
  const root = $('#librarySources');
  root.className = `library-grid ${libraryMode === 'list' ? 'list' : ''}`;
  $('#libraryGridBtn').classList.toggle('active', libraryMode === 'grid');
  $('#libraryListBtn').classList.toggle('active', libraryMode === 'list');
  $('#libraryGridBtn').setAttribute('aria-pressed', String(libraryMode === 'grid'));
  $('#libraryListBtn').setAttribute('aria-pressed', String(libraryMode === 'list'));
  $('#libraryResultsMeta').textContent = `${visible.length} ${visible.length === 1 ? 'profilo' : 'profili'} · ${libraryProfiles.length} totali`;
  if (!visible.length) {
    root.innerHTML = '<div class="empty">Nessun risultato.</div>';
    updateSelectionUi(visible);
    return;
  }
  root.innerHTML = visible.map(profile => {
    const checked = selectedProfiles.has(profile.profile_id);
    const accountLabels = profile.sources.map(source => `${source.provider_label || source.platform} · ${String(source.slug).startsWith('http') ? source.name : `@${source.slug}`}`);
    return `<article class="library-card ${checked ? 'selected' : ''} ${profile.favorite ? 'favorite' : ''}">
      <label class="library-select"><input type="checkbox" data-profile-select="${profile.profile_id}" ${checked ? 'checked' : ''}><span class="sr-only">Seleziona ${esc(profile.display_name)}</span></label>
      <button class="favorite-btn ${profile.favorite ? 'active' : ''}" data-lib-action="favorite" data-id="${profile.representative_id}" type="button" aria-label="${profile.favorite ? 'Rimuovi dai preferiti' : 'Aggiungi ai preferiti'}" aria-pressed="${profile.favorite}">★</button>
      <button class="library-cover" data-lib-action="profile" data-id="${profile.representative_id}" type="button">${libraryCover(profile)}</button>
      <div class="library-card-body">
        <div class="library-card-head"><div><h3>${creatorLinkMarkup(profile.representative_id, profile.display_name)}</h3><p>${accountLabels.map(esc).join(' · ')}</p></div><span class="source-status ${esc(profile.status)}">${esc(statusLabel(profile.status))}</span></div>
        <div class="library-tags">${tagMarkup(profile.categories)}${tagMarkup(profile.collections, 'library-tag collection')}</div>
        <div class="library-stats"><span><strong>${profile.recording_count}</strong> file</span><span><strong>${profile.session_count}</strong> sessioni</span><span><strong>${humanBytes(profile.total_bytes)}</strong></span><span><strong>${profile.uploaded_count}</strong> cloud</span></div>
        <div class="library-recency">REC ${profile.last_recording_at ? esc(ago(profile.last_recording_at)) : '—'} · LIVE ${esc(ago(profile.last_seen_live_at || profile.last_live_at))}</div>
        <div class="library-actions"><button class="btn primary" data-lib-action="profile" data-id="${profile.representative_id}" type="button">Profilo</button><button class="btn soft" data-lib-action="archive" data-id="${profile.representative_id}" type="button">Archivio</button>${profile.archived ? `<button class="btn soft" data-lib-action="restore" data-id="${profile.representative_id}" type="button">Ripristina</button>` : ''}<button class="btn danger" data-lib-action="delete-profile" data-id="${profile.representative_id}" type="button">Elimina definitivamente</button></div>
      </div>
    </article>`;
  }).join('');
  updateSelectionUi(visible);
}

function fillLibraryControls() {
  const currentProvider = $('#libraryProvider').value;
  const platformRows = new Map();
  for (const source of sources) platformRows.set(source.platform, source.provider_label || source.platform);
  $('#libraryProvider').innerHTML = '<option value="all">Tutti i provider</option>' + [...platformRows]
    .sort((a, b) => a[1].localeCompare(b[1], 'it'))
    .map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join('');
  $('#libraryProvider').value = platformRows.has(currentProvider) ? currentProvider : 'all';

  const categoryOptions = (libraryMeta.categories || []).map(item => `<option value="${item.id}">${esc(item.name)}</option>`).join('');
  const collectionOptions = (libraryMeta.collections || []).map(item => `<option value="${item.id}">${esc(item.name)}</option>`).join('');
  const categoryValue = $('#libraryCategory').value;
  const collectionValue = $('#libraryCollection').value;
  $('#libraryCategory').innerHTML = '<option value="all">Tutte le categorie</option>' + categoryOptions;
  $('#libraryCollection').innerHTML = '<option value="all">Tutte le raccolte libreria</option>' + collectionOptions;
  $('#bulkCategory').innerHTML = '<option value="">Aggiungi categoria…</option>' + categoryOptions;
  $('#bulkCollection').innerHTML = '<option value="">Aggiungi a raccolta libreria…</option>' + collectionOptions;
  if ([...$('#libraryCategory').options].some(option => option.value === categoryValue)) $('#libraryCategory').value = categoryValue;
  if ([...$('#libraryCollection').options].some(option => option.value === collectionValue)) $('#libraryCollection').value = collectionValue;
  populateProfileOptions();
  renderTaxonomy();
}

function renderTaxonomy() {
  const categoryRoot = $('#categoryList');
  const collectionRoot = $('#collectionList');
  categoryRoot.innerHTML = (libraryMeta.categories || []).length ? libraryMeta.categories.map(item =>
    `<article class="taxonomy-row"><span class="taxonomy-swatch" style="--tag:${esc(item.color)}"></span><div><strong>${esc(item.name)}</strong><small>${item.profile_count || 0} profili</small></div><button class="btn quiet" data-tax-action="edit-category" data-id="${item.id}" type="button">Modifica</button><button class="btn quiet danger" data-tax-action="delete-category" data-id="${item.id}" type="button">Elimina</button></article>`
  ).join('') : '<div class="empty compact">Nessuna categoria.</div>';
  collectionRoot.innerHTML = (libraryMeta.collections || []).length ? libraryMeta.collections.map(item =>
    `<article class="taxonomy-row collection-row"><span class="taxonomy-swatch" style="--tag:${esc(item.color)}"></span><div><strong>${esc(item.name)}${item.pinned ? ' · in evidenza' : ''}</strong><small>${item.profile_count || 0} profili${item.description ? ` · ${esc(item.description)}` : ''}</small></div><button class="btn quiet" data-tax-action="toggle-collection" data-id="${item.id}" type="button">${item.pinned ? 'Togli evidenza' : 'Evidenzia'}</button><button class="btn quiet" data-tax-action="edit-collection" data-id="${item.id}" type="button">Modifica</button><button class="btn quiet danger" data-tax-action="delete-collection" data-id="${item.id}" type="button">Elimina</button></article>`
  ).join('') : '<div class="empty compact">Nessuna raccolta.</div>';
}

async function loadLibraryMeta() {
  libraryMeta = await api('/api/library/meta');
  fillLibraryControls();
}

async function openProfile(sourceId) {
  const source = sources.find(item => item.id === Number(sourceId));
  $('#profileTitle').textContent = source?.display_name || source?.name || 'Profilo';
  $('#profileProvider').textContent = '';
  $('#profileReference').textContent = source ? `${source.provider_label} · ${source.name}` : '';
  $('#profileContent').innerHTML = '<div class="empty">Caricamento…</div>';
  openModal('profileModal');
  try {
    profileData = await api(`/api/sources/${Number(sourceId)}/profile`);
    try {
      profileData.activity_statistics = await api(`/api/library/profiles/${profileData.source.profile_id}/statistics?days=${profileStatisticsDays}`);
    } catch (error) {
      profileData.activity_statistics = null;
      toast(`Statistiche profilo: ${error.message}`, 'bad');
    }
    renderProfile();
  } catch (error) {
    $('#profileContent').innerHTML = `<div class="error-text">${esc(error.message)}</div>`;
  }
}

function renderProfile() {
  if (!profileData) return;
  const profile = profileData.source;
  const stats = profile.statistics || {};
  const activity = profileData.activity_statistics;
  const cover = safeUrl(profile.cover_thumbnail_url);
  $('#profileTitle').textContent = profile.display_name;
  $('#profileReference').textContent = `${profile.linked_sources.length} account · ${dateText(profile.created_at)}`;
  const categoryChecks = (libraryMeta.categories || []).map(item =>
    `<label class="choice-tag" style="--tag:${esc(item.color)}"><input type="checkbox" data-profile-category="${item.id}" ${profile.categories.some(row => row.id === item.id) ? 'checked' : ''}><span>${esc(item.name)}</span></label>`
  ).join('') || '<span class="muted">Nessuna categoria.</span>';
  const collectionChecks = (libraryMeta.collections || []).map(item =>
    `<label class="choice-tag collection" style="--tag:${esc(item.color)}"><input type="checkbox" data-profile-collection="${item.id}" ${profile.collections.some(row => row.id === item.id) ? 'checked' : ''}><span>${esc(item.name)}</span></label>`
  ).join('') || '<span class="muted">Nessuna raccolta.</span>';
  const linked = profile.linked_sources.map(source => {
    const url = safeUrl(source.source_url);
    return `<article class="linked-source"><div><strong>${esc(source.name)}</strong><small>${esc(source.provider_label)} · ${esc(source.slug)} · ${esc(statusLabel(source.last_status))}</small></div>${url ? `<a class="btn quiet" href="${esc(url)}" target="_blank" rel="noopener">Apri ↗</a>` : ''}<button class="btn quiet" data-profile-action="edit-source" data-id="${source.id}" type="button">Modifica</button><button class="btn quiet" data-profile-action="toggle-source" data-id="${source.id}" type="button">${source.enabled ? 'Pausa' : 'Riattiva'}</button></article>`;
  }).join('');
  const timeline = (profileData.timeline || []).map(event =>
    `<li><time>${esc(dateText(event.at))}</time><span>${esc(event.title)}</span></li>`
  ).join('') || '<li><span>Nessuna attività.</span></li>';
  const recent = (profileData.recent_recordings || []).slice(0, 8).map(recording => {
    const remote = safeUrl(recording.remote_url);
    return `<article class="profile-recording"><div><strong>${esc(recording.filename)}</strong><small>${esc(dateText(recording.started_at))} · ${esc(recording.size_human)} · ${esc(duration(recording.duration_seconds))}</small></div>${recording.local_available ? `<button class="btn quiet" data-profile-action="preview" data-id="${recording.id}" type="button">Vedi</button>` : ''}${remote ? `<a class="btn quiet" href="${esc(remote)}" target="_blank" rel="noopener">Cloud ↗</a>` : ''}</article>`;
  }).join('') || '<div class="empty compact">Nessuna registrazione.</div>';
  $('#profileContent').innerHTML = `<div class="profile-overview">
      <div class="profile-cover ${cover ? '' : 'empty'}">${cover ? `<img src="${esc(cover)}" alt="Copertina di ${esc(profile.display_name)}">` : `<span>${esc(profile.display_name.slice(0, 2).toUpperCase())}</span>`}</div>
      <div class="profile-summary"><button class="favorite-toggle ${profile.favorite ? 'active' : ''}" data-profile-action="favorite" data-id="${profile.id}" type="button" aria-pressed="${profile.favorite}">★ ${profile.favorite ? 'Preferita' : 'Preferiti'}</button><div class="profile-metrics"><span><strong>${stats.recording_count || 0}</strong> file</span><span><strong>${stats.session_count || 0}</strong> sessioni</span><span><strong>${humanBytes(stats.total_bytes || 0)}</strong> registrati</span><span><strong>${duration(stats.total_duration_seconds || 0)}</strong> durata</span><span><strong>${stats.uploaded_count || 0}</strong> cloud</span><span class="${stats.failed_count ? 'danger-text' : ''}"><strong>${stats.failed_count || 0}</strong> problemi</span></div></div>
    </div>
    <section class="profile-section profile-statistics"><div class="profile-section-head"><div><h3>Statistiche</h3></div><label class="stats-range compact"><span>Periodo</span><select id="profileStatisticsRange"><option value="7" ${profileStatisticsDays === 7 ? 'selected' : ''}>7g</option><option value="30" ${profileStatisticsDays === 30 ? 'selected' : ''}>30g</option><option value="90" ${profileStatisticsDays === 90 ? 'selected' : ''}>90g</option><option value="365" ${profileStatisticsDays === 365 ? 'selected' : ''}>365g</option></select></label></div>${activity ? `<div class="stats-summary-grid compact">${statisticsSummaryMarkup(activity, true)}</div><div class="stats-chart-grid profile"><div class="stats-mini-chart"><div class="chart-title">Online vs registrato</div>${activityChartSvg(activity.daily)}</div><div class="stats-mini-chart"><div class="chart-title">Orari più frequenti</div>${hourlyChartSvg(activity.hourly)}</div></div>${statisticsHistoryNote(activity) ? `<p class="stats-note">${esc(statisticsHistoryNote(activity))}</p>` : ''}` : '<div class="empty compact">Statistiche non disponibili.</div>'}</section>
    <section class="profile-section"><div class="profile-section-head"><h3>Identità e note</h3></div><label class="field"><span>Nome profilo</span><input id="profileDisplayName" maxlength="120" value="${esc(profile.display_name)}"></label><label class="field"><span>Note private</span><textarea id="profileNotes" maxlength="20000" rows="4" placeholder="Note, preferenze, riferimenti…">${esc(profile.notes)}</textarea></label></section>
    <section class="profile-section"><div class="profile-section-head"><h3>Categorie</h3><button class="btn quiet" data-profile-action="manage-taxonomy" type="button">Gestisci</button></div><div class="choice-grid">${categoryChecks}</div></section>
    <section class="profile-section"><div class="profile-section-head"><h3>Raccolte libreria</h3></div><div class="choice-grid">${collectionChecks}</div></section>
    <section class="profile-section"><div class="profile-section-head"><h3>Account e provider</h3><button class="btn soft" data-profile-action="add-source" data-id="${profile.profile_id}" type="button">Collega nuova sorgente</button></div><div class="linked-list">${linked}</div></section>
    <section class="profile-section"><div class="profile-section-head"><h3>Registrazioni recenti</h3><button class="btn quiet" data-profile-action="archive" data-id="${profile.id}" type="button">Apri archivio</button></div><div class="profile-recordings">${recent}</div></section>
    <section class="profile-section"><div class="profile-section-head"><h3>Timeline</h3></div><ol class="timeline">${timeline}</ol></section>
    <div class="profile-save"><button class="btn danger" data-profile-action="delete-profile" data-id="${profile.profile_id}" type="button">Elimina creator definitivamente</button><span id="profileSaveError" class="error-text"></span><button class="btn primary" data-profile-action="save" data-id="${profile.id}" type="button">Salva profilo</button></div>`;
}

async function saveProfile(button) {
  if (!profileData) return;
  const sourceId = profileData.source.id;
  const body = {
    display_name: $('#profileDisplayName').value.trim(),
    notes: $('#profileNotes').value.trim(),
    category_ids: $$('[data-profile-category]:checked').map(input => Number(input.dataset.profileCategory)),
    collection_ids: $$('[data-profile-collection]:checked').map(input => Number(input.dataset.profileCollection))
  };
  setBusy(button, true, 'Salvataggio…');
  try {
    await api(`/api/sources/${sourceId}/library`, {method: 'PATCH', body: JSON.stringify(body)});
    toast('Profilo aggiornato');
    await refresh({includeRecordings: false});
    profileData = await api(`/api/sources/${sourceId}/profile`);
    profileData.activity_statistics = await api(`/api/library/profiles/${profileData.source.profile_id}/statistics?days=${profileStatisticsDays}`);
    renderProfile();
  } catch (error) {
    const target = $('#profileSaveError');
    if (target) target.textContent = error.message;
  } finally {
    setBusy(button, false);
  }
}

function uploadLabel(value) {
  return ({
    pending: 'In coda', uploading: 'Upload', uploaded: 'Caricato', failed: 'Fallito',
    waiting_config: 'Config mancante', integrity_failed: 'Integrità fallita', converting: 'Conversione MP4',
    missing: 'Mancante', deleting: 'Eliminazione', discarded: 'Locale eliminato'
  })[value] || value;
}

function recordingMatches(recording) {
  const query = $('#recordingSearch').value.trim().toLocaleLowerCase('it');
  const status = $('#recordingStatus').value;
  return (!sourceFilterId || recording.source_id === sourceFilterId)
    && (status === 'all' || recording.upload_status === status)
    && (!query || `${recording.source_name} ${recording.filename} ${recording.session_id}`.toLocaleLowerCase('it').includes(query));
}

function recordingStreamMarkup(recording) {
  const video = recording.has_video === true
    ? `<span class="stream-ok">✓ Video${recording.video_codec ? ` · ${esc(recording.video_codec)}` : ''}</span>`
    : recording.has_video === false ? '<span class="stream-bad">✕ Video assente</span>' : '<span class="stream-unknown">? Video non verificato</span>';
  const audio = recording.has_audio === true
    ? `<span class="stream-ok">✓ Audio${recording.audio_codec ? ` · ${esc(recording.audio_codec)}` : ''}</span>`
    : recording.has_audio === false ? '<span class="stream-bad">✕ Audio assente</span>' : '<span class="stream-unknown">? Audio non verificato</span>';
  return video + audio;
}

function renderRecordings() {
  const visible = recordings.filter(recordingMatches);
  const root = $('#recordings');
  if (!visible.length) {
    root.innerHTML = '<div class="empty">Nessun risultato.</div>';
    $('#recordingFooter').textContent = recordings.length ? `${recordings.length} file totali` : '';
    return;
  }
  root.innerHTML = visible.map(recording => {
    const remote = safeUrl(recording.remote_url);
    const collection = safeUrl(recording.collection_url);
    const thumbnail = recording.thumbnail_available && safeUrl(recording.thumbnail_url)
      ? `<img src="${esc(safeUrl(recording.thumbnail_url))}" loading="lazy" alt="Miniatura ${esc(recording.filename)}">` : '';
    const error = recording.integrity_error || recording.last_error || '';
    const recordingSource = sources.find(source => source.id === recording.source_id);
    const creatorName = recordingSource?.display_name || recording.source_name;
    return `<article class="rec-card">
      <button class="thumb ${thumbnail ? '' : 'empty'}" data-rec-action="preview" data-id="${recording.id}" type="button" aria-label="Anteprima ${esc(recording.filename)}">${thumbnail || '<span>LV</span>'}${recording.local_available ? '<span class="play-badge">▶ Anteprima</span>' : ''}</button>
      <div class="rec-body">
        <div class="rec-title">${creatorLinkMarkup(recordingSource?.id || 0, creatorName)}</div><div class="rec-file">${esc(recording.filename)}</div><div class="rec-date">${esc(dateText(recording.started_at))} · ${esc(recording.session_id)}</div>
        <div class="rec-meta"><span class="chip">${esc(recording.size_human)}</span><span class="chip">${esc(duration(recording.duration_seconds))}</span><span class="chip">${esc((recording.container_format || '').toUpperCase())}</span>${recordingStreamMarkup(recording)}<span class="integrity ${esc(recording.integrity_status)}">${recording.integrity_status === 'passed' ? '✓ Integro' : recording.integrity_status === 'failed' || recording.integrity_status === 'integrity_failed' ? '✕ Fallita' : `… ${esc(recording.integrity_status)}`}</span><span class="upload-status ${esc(recording.upload_status)}">${esc(uploadLabel(recording.upload_status))}${recording.upload_provider ? ` · ${esc(recording.upload_provider)}` : ''}</span></div>
        ${error ? `<div class="rec-error" title="${esc(error)}">${esc(error)}</div>` : ''}
        <div class="rec-actions">
          ${recording.local_available ? `<button class="btn soft" data-rec-action="preview" data-id="${recording.id}" type="button">Vedi</button><a class="btn soft" href="/api/recordings/${recording.id}/download">Scarica</a>` : ''}
          ${remote ? `<a class="btn soft" href="${esc(remote)}" target="_blank" rel="noopener">Apri ${esc(recording.upload_provider || 'cloud')} ↗</a><button class="btn soft" data-rec-action="copy-cloud" data-id="${recording.id}" type="button">Copia link</button>` : ''}
          ${collection ? `<a class="btn soft" href="${esc(collection)}">Archivio camera</a>` : ''}
          ${recording.local_available && recording.integrity_status === 'passed' ? `<button class="btn accent" data-rec-action="upload-now" data-id="${recording.id}" type="button">Upload ora</button>` : ''}
          ${recording.local_available ? `<button class="btn soft" data-rec-action="integrity" data-id="${recording.id}" type="button">Ricontrolla</button>` : ''}
          ${recording.local_available && recording.container_format !== 'mp4' ? `<button class="btn soft" data-rec-action="convert" data-id="${recording.id}" type="button">Converti MP4</button>` : ''}
          ${recording.local_available ? `<button class="btn danger" data-rec-action="delete-local" data-id="${recording.id}" type="button">Elimina locale</button>` : ''}
          <button class="btn danger" data-rec-action="delete-record" data-id="${recording.id}" type="button">Elimina voce</button>
        </div>
      </div>
    </article>`;
  }).join('');
  $('#recordingFooter').textContent = `${visible.length} visualizzate · ${recordings.length} totali`;
}

function setSourceFilter(id) {
  sourceFilterId = Number(id) || 0;
  const selected = sources.find(source => source.id === sourceFilterId);
  $('#recordingSearch').value = '';
  $('#sourceFilterBar').classList.toggle('hidden', !selected);
  $('#sourceFilterNote').textContent = selected ? `${selected.display_name || selected.name}` : '';
  const url = new URL(location.href);
  if (sourceFilterId) url.searchParams.set('source', String(sourceFilterId)); else url.searchParams.delete('source');
  url.hash = 'archive';
  history.replaceState({}, '', url);
  showView('archive', false);
  renderRecordings();
}

function showSource(source = null, requestedProfileId = 0) {
  const editing = !!source;
  $('#sourceId').value = source?.id || '';
  $('#sourceName').value = source?.name || '';
  $('#sourcePlatform').value = source?.platform || 'auto';
  $('#sourcePlatform').disabled = editing;
  $('#sourceSlug').value = source?.slug || '';
  $('#sourceQuality').value = source?.quality || 'best';
  $('#sourceOrganizeCloud').checked = source ? !!source.organize_cloud : true;
  $('#sourceGofileFolder').value = source?.gofile_folder_id || '';
  $('#sourceGofileFolderUrl').value = source?.gofile_folder_url || '';
  $('#sourceConsent').checked = !!source?.consent_confirmed;
  $('#sourceModalTitle').textContent = editing ? 'Modifica sorgente' : 'Aggiungi sorgente';
  $('#sourceError').textContent = '';
  $('#sourceTestResult').textContent = '';
  $('#sourceTestResult').className = '';
  populateProfileOptions(requestedProfileId || source?.profile_id || 0, editing);
  updateSourceInputHint();
  openModal('sourceModal');
}

async function loadSettings() {
  const response = await api('/api/settings');
  settingsData = response.settings;
  const settings = settingsData;
  $('#setSegment').value = settings.segment_minutes;
  $('#setSegmentMax').value = settings.segment_max_gb;
  $('#setContainer').value = settings.container_format;
  $('#setPoll').value = settings.poll_seconds;
  $('#setProbe').value = settings.max_probe_concurrency;
  $('#setIntegrity').value = settings.integrity_mode;
  $('#setThumbs').checked = settings.generate_thumbnails;
  $('#setBuffer').value = settings.buffer_max_gb;
  $('#setHardStop').checked = settings.buffer_hard_stop;
  $('#setMinFree').value = settings.min_free_gb;
  $('#setCriticalFree').value = settings.critical_free_gb;
  $('#setEmergencyFree').value = settings.emergency_free_gb;
  $('#setDeleteAfter').checked = settings.delete_after_upload;
  $('#setPrimary').value = settings.primary_uploader;
  $('#setFallback').value = settings.fallback_uploader;
  $('#setRetry').value = settings.upload_retry_seconds;
  $('#setAttempts').value = settings.max_upload_attempts;
  $('#setGofileFolder').value = settings.gofile_folder_id || '';
  $('#setGofileRegion').value = settings.gofile_region || 'auto';
  $('#setGofileToken').value = '';
  $('#setPixeldrainKey').value = '';
  $('#clearGofile').checked = false;
  $('#clearPixeldrain').checked = false;
  $('#gofileHint').textContent = settings.gofile_configured ? `Token salvato ${settings.gofile_token_hint}` : 'Nessun token salvato';
  $('#pixeldrainHint').textContent = settings.pixeldrain_configured ? `Key salvata ${settings.pixeldrain_key_hint}` : 'Nessuna key salvata';
  $('#gofileState').textContent = settings.gofile_configured ? 'Configurato' : 'Non configurato';
  $('#gofileState').className = `provider-state ${settings.gofile_configured ? 'ok' : ''}`;
  $('#pixeldrainState').textContent = settings.pixeldrain_configured ? 'Configurato' : 'Non configurato';
  $('#pixeldrainState').className = `provider-state ${settings.pixeldrain_configured ? 'ok' : ''}`;
  $('#settingsError').textContent = '';
}

async function testProvider(provider, button, state) {
  setBusy(button, true, 'Test…');
  state.textContent = 'Test in corso…';
  state.className = 'provider-state';
  try {
    const response = await api(`/api/settings/test/${provider}`, {method: 'POST'});
    state.textContent = response.message + (response.username ? ` · ${response.username}` : '');
    state.className = 'provider-state ok';
    toast(response.message);
  } catch (error) {
    state.textContent = error.message;
    state.className = 'provider-state bad';
    toast(error.message, 'bad');
  } finally {
    setBusy(button, false);
  }
}

async function saveSecretBeforeTest(provider) {
  const gofile = provider === 'gofile';
  const input = $(gofile ? '#setGofileToken' : '#setPixeldrainKey');
  const value = input.value.trim();
  if (!value) return;
  await api('/api/settings', {
    method: 'PATCH',
    body: JSON.stringify(gofile ? {gofile_token: value} : {pixeldrain_api_key: value})
  });
  input.value = '';
  await loadSettings();
}

function renderStatus(status) {
  statusData = status;
  $('#versionLabel').textContent = `v${status.config.version}`;
  const active = status.worker.active || [];
  const history = status.history || {};
  $('#activeCount').textContent = active.length;
  $('#activeNames').innerHTML = active.length ? active.map(item => {
    const source = sources.find(row => row.id === Number(item.source_id));
    return creatorLinkMarkup(item.source_id, source?.display_name || item.source_name);
  }).join(' · ') : 'Nessuna';
  $('#queueCount').textContent = status.queue.pending;
  $('#queueNote').textContent = status.queue.integrity_failed ? `${status.queue.integrity_failed} integrità fallita` : status.queue.failed ? `${status.queue.failed} falliti` : status.queue.pending ? 'file in attesa' : 'Coda vuota';
  $('#historyCount').textContent = history.recordings || 0;
  $('#historyNote').textContent = `${history.sessions || 0} sessioni · ${history.today || 0} oggi`;
  $('#cloudCount').textContent = history.uploaded || 0;
  $('#cloudNote').textContent = `${history.uploaded_human || '0 B'} verificati`;
  $('#bufferValue').textContent = status.queue.local_human;
  $('#bufferNote').textContent = status.config.buffer_max_gb ? `${status.queue.buffer_percent}% di ${status.queue.buffer_max_human}` : 'Nessun limite';
  $('#bufferBar').style.width = `${Math.min(100, status.queue.buffer_percent || 0)}%`;
  $('#bufferBar').className = status.queue.buffer_percent > 95 ? 'bad' : status.queue.buffer_percent > 80 ? 'warn' : '';
  $('#freeSpace').textContent = status.disk.free_human;
  $('#diskUsage').textContent = `${status.disk.used_human} / ${status.disk.total_human} usati`;
  const diskUsed = status.disk.total ? Math.min(100, status.disk.used / status.disk.total * 100) : 0;
  $('#diskBar').style.width = `${diskUsed}%`;
  $('#diskBar').className = status.disk.pressure === 'critical' ? 'bad' : status.disk.pressure === 'warning' ? 'warn' : '';
  $('#providerRoute').textContent = `${status.config.primary_uploader} → ${status.config.fallback_uploader}`;
  $('#recordingControlState').textContent = status.config.recording_paused ? 'In pausa' : 'Attive';
  $('#uploadControlState').textContent = status.config.upload_paused ? 'In pausa' : 'Attivo';
  $('#pauseRecordingsBtn').textContent = status.config.recording_paused ? 'Riprendi registrazioni' : 'Pausa registrazioni';
  $('#pauseUploadsBtn').textContent = status.config.upload_paused ? 'Riprendi upload' : 'Pausa upload';

  const current = status.worker.upload_current;
  $('#uploadNowCard').classList.toggle('hidden', !current);
  if (current) {
    $('#uploadNowTitle').textContent = `${current.provider || 'Cloud'} · ${current.source_name}`;
    $('#uploadNowMeta').textContent = `${current.filename} · ${humanBytes(current.sent_bytes || 0)} / ${humanBytes(current.size_bytes || 0)}`;
    $('#uploadProgressBar').style.width = `${current.percent || 0}%`;
    $('#uploadProgressText').textContent = `${current.percent || 0}%`;
  }

  const errors = status.worker.errors || {};
  const errorCount = Object.keys(errors).length;
  $('#errorsPanel').classList.toggle('hidden', errorCount === 0);
  $('#diagnosticCount').textContent = errorCount;
  $('#errors').textContent = Object.entries(errors).map(([name, message]) => `${name}\n${message}`).join('\n\n');
  const health = $('#healthPill');
  if (status.disk.pressure === 'critical') {
    health.className = 'pill bad'; health.textContent = 'Disco critico';
  } else if (errorCount || status.queue.integrity_failed || history.audio_missing) {
    health.className = 'pill warn'; health.textContent = 'Da controllare';
  } else {
    health.className = 'pill good'; health.textContent = 'Online';
  }
}

function renderLivePauseAlert() {
  const root = $('#livePauseAlert');
  if (!root) return;
  const grouped = new Map();
  for (const source of sources.filter(row => row.recording_blocked_by_pause && !row.archived)) {
    const profileId = Number(source.profile_id || source.id);
    if (!grouped.has(profileId)) grouped.set(profileId, source);
  }
  const rows = [...grouped.values()];
  root.classList.toggle('hidden', rows.length === 0);
  if (!rows.length) { root.innerHTML = ''; return; }
  const globallyPaused = !!statusData?.config?.recording_paused;
  const title = rows.length === 1 ? 'LIVE · NON REC' : `${rows.length} LIVE · NON REC`;
  root.innerHTML = `<div class="live-pause-head"><span class="live-pause-pulse" aria-hidden="true"></span><div><strong>${esc(title)}</strong><small>${globallyPaused ? 'PAUSA GLOBALE' : 'IN PAUSA'}</small></div></div><div class="live-pause-creators">${rows.slice(0, 5).map(source => creatorLinkMarkup(source.id, source.display_name || source.name)).join('')}${rows.length > 5 ? `<span class="live-pause-more">+${rows.length - 5}</span>` : ''}</div>${globallyPaused ? '<button class="btn primary live-pause-resume" data-alert-resume type="button">Riprendi REC</button>' : ''}`;
}

function renderStatistics() {
  if (!statisticsData) return;
  $('#statisticsSummary').innerHTML = statisticsSummaryMarkup(statisticsData);
  $('#statisticsDailyChart').innerHTML = activityChartSvg(statisticsData.daily);
  $('#statisticsHourlyChart').innerHTML = hourlyChartSvg(statisticsData.hourly);
  const rows = statisticsData.top_creators || [];
  $('#statisticsLeaderboard').innerHTML = rows.length ? rows.map((row, index) => `<article class="leader-row"><span class="leader-rank">${index + 1}</span><div class="leader-name">${creatorLinkMarkup(row.representative_source_id, row.display_name)}${row.online_now ? '<span class="leader-live">LIVE</span>' : ''}</div><div><strong>${esc(duration(row.online_seconds))}</strong><small>online · ${row.days_online} giorni</small></div><div><strong>${esc(duration(row.recorded_seconds))}</strong><small>registrato</small></div><div><strong>${esc(statNumber(row.coverage_percent, '%'))}</strong><small>copertura</small></div></article>`).join('') : '<div class="empty">Nessun dato.</div>';
  $('#statisticsNote').textContent = statisticsHistoryNote(statisticsData);
  $('#statisticsRange').value = String(statisticsDays);
}

async function loadStatistics(days = statisticsDays) {
  if (statisticsBusy) return;
  statisticsBusy = true;
  statisticsDays = Math.max(1, Math.min(365, Number(days) || 30));
  try {
    statisticsData = await api(`/api/statistics?days=${statisticsDays}`);
    lastStatisticsLoad = Date.now();
    renderStatistics();
  } finally {
    statisticsBusy = false;
  }
}

async function loadProfileStatistics(days) {
  if (!profileData) return;
  profileStatisticsDays = Math.max(1, Math.min(365, Number(days) || 30));
  profileData.activity_statistics = await api(`/api/library/profiles/${profileData.source.profile_id}/statistics?days=${profileStatisticsDays}`);
  renderProfile();
}

async function refresh({includeRecordings = false} = {}) {
  if (refreshBusy) return;
  refreshBusy = true;
  try {
    const shouldLoadRecordings = includeRecordings || !recordings.length || Date.now() - lastRecordingLoad > 30000;
    const calls = [api('/api/status'), api('/api/sources'), api('/api/library/meta')];
    if (shouldLoadRecordings) calls.push(api('/api/recordings?limit=1000'));
    const [status, sourceRows, meta, recordingRows] = await Promise.all(calls);
    statusData = status;
    sources = sourceRows;
    libraryMeta = meta;
    if (recordingRows) {
      recordings = recordingRows;
      lastRecordingLoad = Date.now();
    }
    buildLibraryProfiles();
    fillLibraryControls();
    renderStatus(status);
    renderSources();
    renderLibrary();
    renderRecordings();
    renderLivePauseAlert();
    if (activeView === 'statistics' && Date.now() - lastStatisticsLoad > 30000) loadStatistics(statisticsDays).catch(() => {});
    $('#lastRefresh').textContent = `Aggiornato ${new Intl.DateTimeFormat('it-IT', {hour: '2-digit', minute: '2-digit', second: '2-digit'}).format(new Date())}`;
  } catch (error) {
    if (error.message !== 'auth') {
      $('#lastRefresh').textContent = `Errore: ${error.message}`;
      toast(`Aggiornamento fallito: ${error.message}`, 'bad');
    }
  } finally {
    refreshBusy = false;
  }
}

async function bulkAction(action, button) {
  const sourceIds = [...selectedProfiles].map(profileId => profileForId(profileId)?.representative_id).filter(Boolean);
  if (!sourceIds.length) return;
  const body = {source_ids: sourceIds, action};
  if (action === 'add_category') {
    body.category_id = Number($('#bulkCategory').value);
    if (!body.category_id) return toast('Scegli una categoria', 'bad');
  }
  if (action === 'add_collection') {
    body.collection_id = Number($('#bulkCollection').value);
    if (!body.collection_id) return toast('Scegli una raccolta', 'bad');
  }
  setBusy(button, true, 'Applico…');
  try {
    const response = await api('/api/sources/bulk', {method: 'POST', body: JSON.stringify(body)});
    selectedProfiles.clear();
    toast(`${response.updated} ${response.updated === 1 ? 'profilo aggiornato' : 'profili aggiornati'}`);
    await refresh({includeRecordings: false});
  } catch (error) {
    toast(error.message, 'bad');
  } finally {
    setBusy(button, false);
  }
}

document.addEventListener('click', event => {
  const profileLink = event.target.closest('[data-profile-link]');
  if (profileLink) {
    event.preventDefault();
    openProfile(Number(profileLink.dataset.profileLink));
    return;
  }
  const resumeAlert = event.target.closest('[data-alert-resume]');
  if (resumeAlert) {
    event.preventDefault();
    setBusy(resumeAlert, true, 'Riprendo…');
    api('/api/control/recordings', {method: 'POST', body: JSON.stringify({paused: false, stop_active: false})})
      .then(() => { toast('Registrazioni riattivate'); return refresh({includeRecordings: false}); })
      .catch(error => toast(error.message, 'bad'))
      .finally(() => setBusy(resumeAlert, false));
    return;
  }
  const close = event.target.closest('[data-close-modal]');
  if (close) closeModal(`${close.dataset.closeModal}Modal`);
  const navigation = event.target.closest('[data-view]');
  if (navigation) {
    event.preventDefault();
    showView(navigation.dataset.view);
  }
});

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  const open = $$('.modal:not(.hidden)').pop();
  if (open) closeModal(open.id);
});

window.addEventListener('hashchange', () => showView(location.hash.slice(1) || 'dashboard', false));

$('#loginForm').addEventListener('submit', async event => {
  event.preventDefault();
  $('#loginError').textContent = '';
  try {
    await api('/api/login', {method: 'POST', body: JSON.stringify({password: $('#password').value})});
    $('#password').value = '';
    login.classList.add('hidden');
    app.classList.remove('hidden');
    await loadProviders();
    await refresh({includeRecordings: true});
    showView(location.hash.slice(1) || (sourceFilterId ? 'archive' : 'dashboard'), false);
  } catch (error) {
    $('#loginError').textContent = error.message === 'auth' ? 'Accesso non valido' : error.message;
  }
});

$('#logoutBtn').addEventListener('click', async () => {
  await api('/api/logout', {method: 'POST'}).catch(() => {});
  app.classList.add('hidden');
  login.classList.remove('hidden');
});

$('#showAddBtn').addEventListener('click', () => showSource());
$('#libraryAddBtn').addEventListener('click', () => showSource());
$('#statisticsRange').addEventListener('change', event => loadStatistics(Number(event.target.value)).catch(error => toast(error.message, 'bad')));

$('#settingsBtn').addEventListener('click', async () => {
  try { await loadSettings(); openModal('settingsModal'); } catch (error) { toast(error.message, 'bad'); }
});

$('#refreshBtn').addEventListener('click', async event => {
  setBusy(event.currentTarget, true, 'Controllo…');
  try {
    await api('/api/sources/check-now', {method: 'POST'});
    toast('Controllo sorgenti richiesto');
    setTimeout(() => refresh({includeRecordings: false}), 900);
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(event.currentTarget, false); }
});

$('#pauseRecordingsBtn').addEventListener('click', async event => {
  const paused = !!statusData?.config?.recording_paused;
  setBusy(event.currentTarget, true);
  try {
    await api('/api/control/recordings', {method: 'POST', body: JSON.stringify({paused: !paused, stop_active: true})});
    toast(!paused ? 'Registrazioni messe in pausa' : 'Registrazioni riattivate');
    await refresh({includeRecordings: true});
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(event.currentTarget, false); }
});

$('#pauseUploadsBtn').addEventListener('click', async event => {
  const paused = !!statusData?.config?.upload_paused;
  setBusy(event.currentTarget, true);
  try {
    await api('/api/control/uploads', {method: 'POST', body: JSON.stringify({paused: !paused, stop_active: false})});
    toast(!paused ? 'Upload in pausa' : 'Upload riattivati');
    await refresh({includeRecordings: false});
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(event.currentTarget, false); }
});

$('#runUploadsBtn').addEventListener('click', async event => {
  setBusy(event.currentTarget, true, 'Avvio…');
  try {
    const response = await api('/api/uploads/run-now', {method: 'POST'});
    toast(`Coda upload avviata${response.changed ? ` · ${response.changed} retry` : ''}`);
    await refresh({includeRecordings: true});
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(event.currentTarget, false); }
});

$('#retryAllBtn').addEventListener('click', async event => {
  setBusy(event.currentTarget, true, 'Riprovo…');
  try {
    const response = await api('/api/recordings/retry-failed', {method: 'POST'});
    toast(`${response.changed} file rimessi in coda`);
    await refresh({includeRecordings: true});
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(event.currentTarget, false); }
});

$('#cleanupBtn').addEventListener('click', async event => {
  if (!confirm('Eliminare solo le copie locali già caricate e verificate? Cloud e miniature restano.')) return;
  setBusy(event.currentTarget, true, 'Pulizia…');
  try {
    const response = await api('/api/recordings/cleanup-uploaded', {method: 'POST'});
    toast(`Liberati ${response.freed_human}${response.errors?.length ? ` · ${response.errors.length} errori` : ''}`, response.errors?.length ? 'bad' : 'good');
    await refresh({includeRecordings: true});
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(event.currentTarget, false); }
});

$('#purgeLocalBtn').addEventListener('click', async event => {
  const selected = sources.find(source => source.id === sourceFilterId);
  const target = selected ? ` della sorgente ${selected.name}` : ' di tutte le sorgenti';
  if (!confirm(`Eliminare definitivamente tutti i video locali${target}, anche quelli non caricati? Le voci archivio, miniature e copie cloud restano.`)) return;
  setBusy(event.currentTarget, true, 'Pulizia…');
  try {
    const response = await api('/api/recordings/cleanup-local', {
      method: 'POST',
      body: JSON.stringify({scope: 'all', source_id: sourceFilterId || null, include_orphans: !sourceFilterId, delete_thumbnails: false, confirm: true})
    });
    toast(`Rimossi ${response.removed} file · liberati ${response.freed_human}${response.skipped_active ? ` · ${response.skipped_active} live saltati` : ''}`, response.errors?.length ? 'bad' : 'good');
    await refresh({includeRecordings: true});
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(event.currentTarget, false); }
});

$('#sourcePlatform').addEventListener('change', () => {
  updateSourceInputHint();
  $('#sourceTestResult').textContent = '';
});
$('#sourceSlug').addEventListener('input', () => { $('#sourceTestResult').textContent = ''; });
$('#sourceQuality').addEventListener('change', () => { $('#sourceTestResult').textContent = ''; });

$('#testSourceBtn').addEventListener('click', async event => {
  const value = $('#sourceSlug').value.trim();
  const output = $('#sourceTestResult');
  if (!value) { output.textContent = 'Inserisci username o URL'; output.className = 'bad'; return; }
  setBusy(event.currentTarget, true, 'Test…');
  output.textContent = 'Controllo…';
  output.className = '';
  try {
    const response = await api('/api/sources/inspect', {
      method: 'POST',
      body: JSON.stringify({platform: $('#sourcePlatform').value, slug: value, quality: $('#sourceQuality').value})
    });
    if (response.live && response.has_audio && response.has_video) {
      output.textContent = `${response.provider_label} · LIVE · audio + video verificati`;
      output.className = 'good';
    } else if (response.live) {
      output.textContent = `${response.provider_label} · LIVE · ${response.error || 'tracce incomplete'}`;
      output.className = 'bad';
    } else if (response.status === 'error') {
      output.textContent = `${response.provider_label} · ${response.error || 'controllo fallito'}`;
      output.className = 'bad';
    } else {
      output.textContent = `${response.provider_label} · ${statusLabel(response.status)}`;
      output.className = 'warn';
    }
  } catch (error) { output.textContent = error.message; output.className = 'bad'; }
  finally { setBusy(event.currentTarget, false); }
});

$('#sourceForm').addEventListener('submit', async event => {
  event.preventDefault();
  const submit = event.currentTarget.querySelector('button[type="submit"]');
  const id = Number($('#sourceId').value) || 0;
  const body = {
    name: $('#sourceName').value.trim(),
    platform: $('#sourcePlatform').value,
    slug: $('#sourceSlug').value.trim(),
    quality: $('#sourceQuality').value,
    organize_cloud: $('#sourceOrganizeCloud').checked,
    gofile_folder_id: $('#sourceGofileFolder').value.trim(),
    gofile_folder_url: $('#sourceGofileFolderUrl').value.trim(),
    consent_confirmed: $('#sourceConsent').checked
  };
  const chosenProfile = Number($('#sourceProfile').value) || 0;
  if (chosenProfile) body.profile_id = chosenProfile;
  setBusy(submit, true, 'Salvataggio…');
  try {
    await api(id ? `/api/sources/${id}` : '/api/sources', {method: id ? 'PATCH' : 'POST', body: JSON.stringify(body)});
    closeModal('sourceModal');
    toast(id ? 'Sorgente aggiornata' : 'Sorgente aggiunta');
    await refresh({includeRecordings: false});
  } catch (error) { $('#sourceError').textContent = error.message; }
  finally { setBusy(submit, false); }
});

$('#sources').addEventListener('click', async event => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const id = Number(button.dataset.id);
  const source = sources.find(item => item.id === id);
  if (!source) return;
  const action = button.dataset.action;
  if (action === 'edit') return showSource(source);
  if (action === 'profile') return openProfile(id);
  if (action === 'archive') return setSourceFilter(id);
  setBusy(button, true);
  try {
    if (action === 'check') {
      await api(`/api/sources/${id}/check-now`, {method: 'POST'});
      toast(`${source.name}: controllo completato`);
    } else if (action === 'organize') {
      const response = await api(`/api/sources/${id}/cloud-folder`, {method: 'POST'});
      toast(response.warning || `Cartella pronta${response.moved ? ` · ${response.moved} file spostati` : ''}`, response.warning ? 'bad' : 'good');
    } else if (action === 'toggle') {
      await api(`/api/sources/${id}`, {method: 'PATCH', body: JSON.stringify({enabled: !source.enabled})});
      toast(source.enabled ? 'Sorgente in pausa' : 'Sorgente riattivata');
    } else if (action === 'delete') {
      if (!confirm(`Archiviare ${source.name}? Recorder e controlli si fermano; profilo, file, storico e cloud restano.`)) return;
      await api(`/api/sources/${id}`, {method: 'DELETE'});
      toast('Sorgente archiviata; puoi ripristinarla dalla Libreria');
    }
    await refresh({includeRecordings: false});
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(button, false); }
});

for (const control of ['#librarySearch', '#libraryProvider', '#libraryStatus', '#libraryCategory', '#libraryCollection']) {
  $(control).addEventListener(control === '#librarySearch' ? 'input' : 'change', renderLibrary);
}

$('.library-sidebar').addEventListener('click', event => {
  const button = event.target.closest('[data-smart]');
  if (!button) return;
  librarySmart = button.dataset.smart;
  $$('.smart-link').forEach(item => item.classList.toggle('active', item === button));
  renderLibrary();
});

$('#libraryGridBtn').addEventListener('click', () => {
  libraryMode = 'grid'; localStorage.setItem('livevault-library-view', libraryMode); renderLibrary();
});
$('#libraryListBtn').addEventListener('click', () => {
  libraryMode = 'list'; localStorage.setItem('livevault-library-view', libraryMode); renderLibrary();
});

$('#selectAllLibrary').addEventListener('change', event => {
  for (const profile of filteredProfiles()) {
    if (event.target.checked) selectedProfiles.add(profile.profile_id); else selectedProfiles.delete(profile.profile_id);
  }
  renderLibrary();
});

$('#clearLibrarySelection').addEventListener('click', () => { selectedProfiles.clear(); renderLibrary(); });

$('#librarySources').addEventListener('change', event => {
  const checkbox = event.target.closest('[data-profile-select]');
  if (!checkbox) return;
  const id = Number(checkbox.dataset.profileSelect);
  if (checkbox.checked) selectedProfiles.add(id); else selectedProfiles.delete(id);
  renderLibrary();
});

$('#librarySources').addEventListener('click', async event => {
  const button = event.target.closest('[data-lib-action]');
  if (!button) return;
  const sourceId = Number(button.dataset.id);
  const source = sources.find(item => item.id === sourceId);
  if (!source) return;
  const action = button.dataset.libAction;
  if (action === 'profile') return openProfile(sourceId);
  if (action === 'archive') return setSourceFilter(sourceId);
  setBusy(button, true);
  try {
    if (action === 'favorite') {
      await api(`/api/sources/${sourceId}/library`, {method: 'PATCH', body: JSON.stringify({favorite: !source.favorite})});
      toast(source.favorite ? 'Rimossa dai preferiti' : 'Aggiunta ai preferiti');
    } else if (action === 'restore') {
      await api(`/api/sources/${sourceId}`, {method: 'PATCH', body: JSON.stringify({enabled: true})});
      toast('Sorgente ripristinata');
    } else if (action === 'delete-profile') {
      const profile = profileForId(source.profile_id);
      const name = profile?.display_name || source.display_name || source.name;
      if (!confirm(`Eliminare definitivamente la creator ${name}? Verranno rimossi il profilo e tutte le sorgenti collegate. Le registrazioni già salvate e i file locali/cloud RESTANO nell'Archivio. Questa operazione non può essere annullata.`)) return;
      const response = await api(`/api/library/profiles/${source.profile_id}`, {method: 'DELETE'});
      selectedProfiles.delete(Number(source.profile_id));
      toast(`Creator eliminata definitivamente${response.preserved_recordings ? ` · ${response.preserved_recordings} registrazioni conservate` : ''}`);
    }
    await refresh({includeRecordings: false});
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(button, false); }
});

$('#bulkBar').addEventListener('click', event => {
  const button = event.target.closest('[data-bulk]');
  if (button) bulkAction(button.dataset.bulk, button);
});

$('#manageLibraryBtn').addEventListener('click', () => {
  $('#libraryTaxonomy').classList.remove('hidden');
  $('#libraryTaxonomy').scrollIntoView({behavior: 'smooth', block: 'start'});
});
$('#closeLibraryManager').addEventListener('click', () => $('#libraryTaxonomy').classList.add('hidden'));

$('#categoryCreateForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  setBusy(button, true, 'Creo…');
  try {
    await api('/api/library/categories', {method: 'POST', body: JSON.stringify({name: $('#newCategoryName').value.trim(), color: $('#newCategoryColor').value})});
    event.currentTarget.reset();
    $('#newCategoryColor').value = '#8fa88a';
    await refresh({includeRecordings: false});
    toast('Categoria creata');
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(button, false); }
});

$('#collectionCreateForm').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  setBusy(button, true, 'Creo…');
  try {
    await api('/api/library/collections', {method: 'POST', body: JSON.stringify({name: $('#newCollectionName').value.trim(), description: $('#newCollectionDescription').value.trim(), color: $('#newCollectionColor').value, pinned: $('#newCollectionPinned').checked})});
    event.currentTarget.reset();
    $('#newCollectionColor').value = '#8da7c2';
    await refresh({includeRecordings: false});
    toast('Raccolta creata');
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(button, false); }
});

async function taxonomyAction(action, id) {
  if (action === 'edit-category') {
    const item = libraryMeta.categories.find(row => row.id === id);
    const name = prompt('Nome categoria', item?.name || '');
    if (name === null) return;
    await api(`/api/library/categories/${id}`, {method: 'PATCH', body: JSON.stringify({name, color: item.color})});
  } else if (action === 'delete-category') {
    const item = libraryMeta.categories.find(row => row.id === id);
    if (!confirm(`Eliminare la categoria “${item?.name || ''}”? Profili e registrazioni non verranno eliminati.`)) return;
    await api(`/api/library/categories/${id}`, {method: 'DELETE'});
  } else if (action === 'edit-collection') {
    const item = libraryMeta.collections.find(row => row.id === id);
    const name = prompt('Nome raccolta', item?.name || '');
    if (name === null) return;
    const description = prompt('Descrizione', item?.description || '');
    if (description === null) return;
    await api(`/api/library/collections/${id}`, {method: 'PATCH', body: JSON.stringify({name, description})});
  } else if (action === 'toggle-collection') {
    const item = libraryMeta.collections.find(row => row.id === id);
    await api(`/api/library/collections/${id}`, {method: 'PATCH', body: JSON.stringify({pinned: !item.pinned})});
  } else if (action === 'delete-collection') {
    const item = libraryMeta.collections.find(row => row.id === id);
    if (!confirm(`Eliminare la raccolta “${item?.name || ''}”? File, profili e cartelle cloud non verranno eliminati.`)) return;
    await api(`/api/library/collections/${id}`, {method: 'DELETE'});
  }
  await refresh({includeRecordings: false});
  toast('Libreria aggiornata');
}

$('#libraryTaxonomy').addEventListener('click', async event => {
  const button = event.target.closest('[data-tax-action]');
  if (!button) return;
  setBusy(button, true);
  try { await taxonomyAction(button.dataset.taxAction, Number(button.dataset.id)); }
  catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(button, false); }
});

$('#profileContent').addEventListener('change', event => {
  if (event.target.id === 'profileStatisticsRange') {
    loadProfileStatistics(Number(event.target.value)).catch(error => toast(error.message, 'bad'));
  }
});

$('#profileContent').addEventListener('click', async event => {
  const button = event.target.closest('[data-profile-action]');
  if (!button || !profileData) return;
  const action = button.dataset.profileAction;
  if (action === 'save') return saveProfile(button);
  if (action === 'manage-taxonomy') {
    closeModal('profileModal');
    showView('library');
    $('#libraryTaxonomy').classList.remove('hidden');
    $('#libraryTaxonomy').scrollIntoView({behavior: 'smooth'});
    return;
  }
  if (action === 'add-source') {
    const profileId = profileData.source.profile_id;
    closeModal('profileModal');
    return showSource(null, profileId);
  }
  if (action === 'archive') {
    const sourceId = profileData.source.id;
    closeModal('profileModal');
    return setSourceFilter(sourceId);
  }
  if (action === 'delete-profile') {
    const profile = profileData.source;
    if (!confirm(`Eliminare definitivamente la creator ${profile.display_name}? Verranno rimossi il profilo e tutte le sorgenti collegate. Le registrazioni già salvate e i file locali/cloud RESTANO nell'Archivio. Questa operazione non può essere annullata.`)) return;
    setBusy(button, true, 'Eliminazione…');
    try {
      const response = await api(`/api/library/profiles/${profile.profile_id}`, {method: 'DELETE'});
      selectedProfiles.delete(Number(profile.profile_id));
      closeModal('profileModal');
      profileData = null;
      toast(`Creator eliminata definitivamente${response.preserved_recordings ? ` · ${response.preserved_recordings} registrazioni conservate` : ''}`);
      await refresh({includeRecordings: false});
    } catch (error) {
      toast(error.message, 'bad');
      setBusy(button, false);
    }
    return;
  }
  if (action === 'edit-source') {
    const source = sources.find(item => item.id === Number(button.dataset.id));
    closeModal('profileModal');
    return showSource(source);
  }
  if (action === 'preview') {
    const recording = (profileData.recent_recordings || []).find(item => item.id === Number(button.dataset.id));
    if (!recording?.local_available) return toast('Anteprima locale non disponibile', 'bad');
    $('#videoTitle').textContent = `${recording.source_name} · ${recording.filename}`;
    $('#videoPlayer').src = recording.view_url;
    return openModal('videoModal');
  }
  setBusy(button, true);
  try {
    if (action === 'favorite') {
      const source = profileData.source;
      await api(`/api/sources/${source.id}/library`, {method: 'PATCH', body: JSON.stringify({favorite: !source.favorite})});
      toast(source.favorite ? 'Rimossa dai preferiti' : 'Aggiunta ai preferiti');
    } else if (action === 'toggle-source') {
      const source = sources.find(item => item.id === Number(button.dataset.id));
      await api(`/api/sources/${source.id}`, {method: 'PATCH', body: JSON.stringify({enabled: !source.enabled})});
      toast(source.enabled ? 'Sorgente in pausa' : 'Sorgente riattivata');
    }
    const sourceId = profileData.source.id;
    await refresh({includeRecordings: false});
    profileData = await api(`/api/sources/${sourceId}/profile`);
    profileData.activity_statistics = await api(`/api/library/profiles/${profileData.source.profile_id}/statistics?days=${profileStatisticsDays}`);
    renderProfile();
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(button, false); }
});

$('#recordingSearch').addEventListener('input', renderRecordings);
$('#recordingStatus').addEventListener('change', renderRecordings);
$('#clearSourceFilter').addEventListener('click', () => {
  sourceFilterId = 0;
  const url = new URL(location.href);
  url.searchParams.delete('source');
  url.hash = 'archive';
  history.replaceState({}, '', url);
  $('#sourceFilterBar').classList.add('hidden');
  renderRecordings();
});

$('#recordings').addEventListener('click', async event => {
  const button = event.target.closest('[data-rec-action]');
  if (!button) return;
  const id = Number(button.dataset.id);
  const recording = recordings.find(item => item.id === id);
  if (!recording) return;
  const action = button.dataset.recAction;
  if (action === 'preview') {
    if (!recording.local_available) return toast('Anteprima non disponibile: copia locale rimossa', 'bad');
    $('#videoTitle').textContent = `${recording.source_name} · ${recording.filename}`;
    $('#videoPlayer').src = recording.view_url;
    return openModal('videoModal');
  }
  if (action === 'copy-cloud') {
    const remote = safeUrl(recording.remote_url);
    if (!remote) return toast('Link cloud non disponibile', 'bad');
    try { await navigator.clipboard.writeText(remote); toast('Link cloud copiato'); }
    catch { toast('Impossibile copiare il link', 'bad'); }
    return;
  }
  setBusy(button, true);
  try {
    if (action === 'upload-now') {
      await api(`/api/recordings/${id}/upload-now`, {method: 'POST'});
      toast('File messo in testa alla coda');
    } else if (action === 'integrity') {
      const response = await api(`/api/recordings/${id}/integrity`, {method: 'POST'});
      toast(response.ok ? 'Integrità, audio e video confermati' : `Controllo fallito: ${response.error}`, response.ok ? 'good' : 'bad');
    } else if (action === 'convert') {
      if (!confirm('Convertire questo file in MP4 senza ricodifica? Il nuovo MP4 tornerà in coda upload.')) return;
      await api(`/api/recordings/${id}/convert-mp4`, {method: 'POST'});
      toast('Conversione MP4 completata');
    } else if (action === 'delete-local') {
      const uploaded = recording.upload_status === 'uploaded';
      const warning = uploaded ? 'Eliminare la copia locale? Cloud e miniatura resteranno.' : 'Questo file non risulta caricato: eliminarlo significa perdere il video locale. Continuare?';
      if (!confirm(warning)) return;
      const response = await api(`/api/recordings/${id}/local?force=${uploaded ? 'false' : 'true'}`, {method: 'DELETE'});
      toast(`File locale eliminato · ${response.freed_human} liberati`);
    } else if (action === 'delete-record') {
      if (!confirm('Eliminare voce archivio, copia locale e miniatura? Il file cloud non verrà cancellato.')) return;
      const response = await api(`/api/recordings/${id}?delete_file=true&delete_thumbnail=true`, {method: 'DELETE'});
      toast(`Registrazione eliminata${response.freed ? ` · ${response.freed_human} liberati` : ''}`);
    }
    await refresh({includeRecordings: true});
  } catch (error) { toast(error.message, 'bad'); }
  finally { setBusy(button, false); }
});

$('#settingsForm').addEventListener('submit', async event => {
  event.preventDefault();
  const submit = event.currentTarget.querySelector('button[type="submit"]');
  const body = {
    segment_minutes: Number($('#setSegment').value), segment_max_gb: Number($('#setSegmentMax').value),
    container_format: $('#setContainer').value, poll_seconds: Number($('#setPoll').value),
    max_probe_concurrency: Number($('#setProbe').value), integrity_mode: $('#setIntegrity').value,
    generate_thumbnails: $('#setThumbs').checked, buffer_max_gb: Number($('#setBuffer').value),
    buffer_hard_stop: $('#setHardStop').checked, min_free_gb: Number($('#setMinFree').value),
    critical_free_gb: Number($('#setCriticalFree').value), emergency_free_gb: Number($('#setEmergencyFree').value),
    delete_after_upload: $('#setDeleteAfter').checked, primary_uploader: $('#setPrimary').value,
    fallback_uploader: $('#setFallback').value, upload_retry_seconds: Number($('#setRetry').value),
    max_upload_attempts: Number($('#setAttempts').value), gofile_folder_id: $('#setGofileFolder').value.trim(),
    gofile_region: $('#setGofileRegion').value, clear_gofile_token: $('#clearGofile').checked,
    clear_pixeldrain_api_key: $('#clearPixeldrain').checked
  };
  if ($('#setGofileToken').value.trim()) body.gofile_token = $('#setGofileToken').value.trim();
  if ($('#setPixeldrainKey').value.trim()) body.pixeldrain_api_key = $('#setPixeldrainKey').value.trim();
  setBusy(submit, true, 'Salvataggio…');
  try {
    await api('/api/settings', {method: 'PATCH', body: JSON.stringify(body)});
    toast('Impostazioni salvate');
    await loadSettings();
    await refresh({includeRecordings: false});
  } catch (error) { $('#settingsError').textContent = error.message; }
  finally { setBusy(submit, false); }
});

$('#testGofileBtn').addEventListener('click', async () => {
  const button = $('#testGofileBtn');
  try { await saveSecretBeforeTest('gofile'); await testProvider('gofile', button, $('#gofileState')); }
  catch (error) { toast(error.message, 'bad'); setBusy(button, false); }
});
$('#testPixeldrainBtn').addEventListener('click', async () => {
  const button = $('#testPixeldrainBtn');
  try { await saveSecretBeforeTest('pixeldrain'); await testProvider('pixeldrain', button, $('#pixeldrainState')); }
  catch (error) { toast(error.message, 'bad'); setBusy(button, false); }
});

async function boot() {
  try {
    await api('/api/me');
    login.classList.add('hidden');
    app.classList.remove('hidden');
    await loadProviders();
    await refresh({includeRecordings: true});
    showView(location.hash.slice(1) || (sourceFilterId ? 'archive' : 'dashboard'), false);
    if (sourceFilterId) setSourceFilter(sourceFilterId);
  } catch (error) {
    if (error.message === 'auth') login.classList.remove('hidden');
    else { login.classList.remove('hidden'); $('#loginError').textContent = error.message; }
  }
}

boot();
setInterval(() => {
  if (!document.hidden && !app.classList.contains('hidden')) refresh({includeRecordings: activeView === 'archive'});
}, 8000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && !app.classList.contains('hidden')) refresh({includeRecordings: activeView === 'archive'});
});
if ('serviceWorker' in navigator) window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));


/* LiveVault Control Room v2.7.1 */
let controlRoomOfflineOpen = localStorage.getItem('livevault-control-room-offline-open') === '1';
let controlRoomWallOpen = false;

function controlRoomProfileRows() {
  const grouped = new Map();
  for (const source of sources.filter(row => !row.archived)) {
    const profileId = Number(source.profile_id || source.id);
    if (!grouped.has(profileId)) grouped.set(profileId, []);
    grouped.get(profileId).push(source);
  }
  const activeMap = new Map((statusData?.worker?.active || []).map(item => [Number(item.source_id), item]));
  return [...grouped.entries()].map(([profileId, rows]) => {
    const recordingRows = rows.filter(row => row.last_status === 'recording');
    const liveRows = rows.filter(row => row.detected_live || ['recording', 'live'].includes(row.last_status));
    const blockedRows = liveRows.filter(row => row.recording_blocked_by_pause || row.last_status !== 'recording');
    const previewSource = recordingRows.find(row => row.preview_url) || recordingRows[0] || liveRows[0] || rows[0];
    const actionSource = blockedRows[0] || recordingRows[0] || liveRows[0] || rows.find(row => row.enabled) || rows[0];
    const newest = field => rows.reduce((best, row) => timestamp(row[field]) > timestamp(best) ? row[field] : best, null);
    return {
      profile_id: profileId,
      source: actionSource,
      preview_source: previewSource,
      rows,
      display_name: actionSource.display_name || actionSource.name,
      focus: rows.some(row => !!row.focus),
      favorite: rows.some(row => !!row.favorite),
      live: liveRows.length > 0,
      recording: recordingRows.length > 0,
      blocked: blockedRows.length > 0,
      blocked_count: blockedRows.length,
      live_count: liveRows.length,
      recording_count: recordingRows.length,
      active: recordingRows.map(row => activeMap.get(Number(row.id))).find(Boolean) || null,
      last_seen_live_at: newest('last_seen_live_at'),
      last_checked_at: newest('last_checked_at'),
      last_error: rows.map(row => String(row.last_error || '').trim()).find(Boolean) || '',
      providers: [...new Set(rows.map(row => row.provider_label || row.platform))],
    };
  });
}

function controlRoomPriority(profile) {
  if (profile.blocked) return 500;
  if (profile.live && profile.focus) return 440;
  if (profile.recording) return 420;
  if (profile.live) return 400;
  if (profile.focus) return 250;
  if (profile.last_error) return 200;
  return 100;
}

function controlRoomInitials(name) {
  return String(name || 'LV').split(/\s+/).slice(0, 2).map(part => part[0] || '').join('').toUpperCase() || 'LV';
}

function controlRoomPreviewMarkup(profile, wall = false) {
  const source = profile.preview_source || profile.source;
  const updated = source?.preview_updated_at;
  const previewUrl = source?.preview_url ? `${source.preview_url}?v=${timestamp(updated) || 0}` : '';
  const cover = safeUrl(source?.cover_thumbnail_url || '');
  const recordingLabel = profile.recording ? 'REC' : profile.live ? 'LIVE' : 'OFFLINE';
  const alertLabel = profile.blocked ? 'NON REGISTRATA' : '';
  const freshness = updated ? ago(updated) : '';
  return `<div class="cr-preview ${profile.blocked ? 'attention' : ''} ${wall ? 'wall' : ''}">
    ${previewUrl ? `<img data-live-preview src="${esc(previewUrl)}" alt="Preview live di ${esc(profile.display_name)}">` : cover ? `<img class="cr-preview-cover" src="${esc(cover)}" alt="Copertina di ${esc(profile.display_name)}">` : `<div class="cr-preview-placeholder"><span>${esc(controlRoomInitials(profile.display_name))}</span></div>`}
    <div class="cr-preview-shade"></div>
    <div class="cr-preview-badges"><span class="cr-live-badge">● ${esc(recordingLabel)}</span>${alertLabel ? `<span class="cr-alert-badge">${esc(alertLabel)}</span>` : ''}${profile.focus ? '<span class="cr-focus-badge">★ FOCUS</span>' : ''}</div>
    ${freshness ? `<span class="cr-preview-age">${esc(freshness)}</span>` : ''}
  </div>`;
}

function controlRoomStatusText(profile) {
  if (profile.blocked) {
    const source = profile.source;
    if (source.pause_reason === 'global') return 'LIVE · PAUSA GLOBALE';
    if (source.pause_reason === 'source') return 'LIVE · IN PAUSA';
    return 'LIVE · NON REC';
  }
  if (profile.recording) {
    const active = profile.active;
    return active ? `REC ${duration(active.elapsed_seconds)} · ${humanBytes(active.local_bytes || 0)}` : 'REC';
  }
  return profile.live ? 'LIVE' : `Offline · ${ago(profile.last_seen_live_at)}`;
}

function controlRoomLiveCard(profile, wall = false) {
  const source = profile.source;
  const publicUrl = safeUrl(source.source_url);
  const multi = profile.rows.length > 1 ? `<span class="cr-account-count">${profile.rows.length} account</span>` : '';
  const controls = wall ? '' : `<div class="cr-card-actions">
      <button class="btn ${profile.focus ? 'accent' : 'soft'}" data-focus-toggle="${source.id}" type="button" aria-pressed="${profile.focus}">★ Focus</button>
      <button class="btn soft" data-action="profile" data-id="${source.id}" type="button">Profilo</button>
      ${profile.blocked && source.pause_reason === 'global' ? '<button class="btn primary" data-cr-resume-global type="button">Riprendi REC</button>' : profile.blocked && source.pause_reason === 'source' ? `<button class="btn primary" data-action="toggle" data-id="${source.id}" type="button">Avvia REC</button>` : ''}
      ${publicUrl ? `<a class="btn soft" href="${esc(publicUrl)}" target="_blank" rel="noopener">Sorgente ↗</a>` : ''}
    </div>`;
  return `<article class="cr-live-card ${profile.blocked ? 'blocked' : ''} ${profile.focus ? 'focus' : ''}">
    ${controlRoomPreviewMarkup(profile, wall)}
    <div class="cr-live-body">
      <div class="cr-live-head"><div>${creatorLinkMarkup(source.id, profile.display_name, 'cr-live-name')}<div class="cr-live-provider">${esc(profile.providers.join(' · '))} ${multi}</div></div><strong class="cr-live-state">${esc(controlRoomStatusText(profile))}</strong></div>
      ${profile.last_error ? `<div class="cr-card-warning">${esc(profile.last_error)}</div>` : ''}
      ${controls}
    </div>
  </article>`;
}

function controlRoomCompactRow(profile, focus = false) {
  const source = profile.source;
  return `<article class="cr-compact-row ${focus ? 'focus' : ''}">
    <button class="cr-compact-focus ${profile.focus ? 'active' : ''}" data-focus-toggle="${source.id}" type="button" aria-label="${profile.focus ? 'Togli dal Focus' : 'Metti in Focus'}" aria-pressed="${profile.focus}">★</button>
    <div class="cr-compact-main">${creatorLinkMarkup(source.id, profile.display_name, 'cr-compact-name')}<span>${esc(profile.providers.join(' · '))}</span></div>
    <span class="cr-compact-status">${profile.last_error ? '⚠ Errore' : profile.source.enabled ? `Offline · ${ago(profile.last_seen_live_at)}` : 'In pausa'}</span>
    <button class="btn quiet" data-action="check" data-id="${source.id}" type="button">Controlla</button>
  </article>`;
}

function ensureControlRoomWall() {
  let wall = $('#controlRoomWall');
  if (wall) return wall;
  wall = document.createElement('section');
  wall.id = 'controlRoomWall';
  wall.className = 'cr-wall hidden';
  wall.setAttribute('aria-label', 'Live Wall');
  wall.innerHTML = `<header class="cr-wall-header"><div><h2>Live Wall</h2><span id="crWallCount">0 live</span></div><button class="btn soft" data-live-wall-close type="button">Chiudi</button></header><div id="crWallGrid" class="cr-wall-grid"></div>`;
  document.body.append(wall);
  return wall;
}

function renderControlRoomWall(profiles = controlRoomProfileRows()) {
  const wall = ensureControlRoomWall();
  wall.classList.toggle('hidden', !controlRoomWallOpen);
  document.body.classList.toggle('cr-wall-open', controlRoomWallOpen);
  if (!controlRoomWallOpen) return;
  const live = profiles.filter(profile => profile.live).sort((a, b) => controlRoomPriority(b) - controlRoomPriority(a) || a.display_name.localeCompare(b.display_name, 'it'));
  $('#crWallCount').textContent = `${live.length} ${live.length === 1 ? 'live' : 'live'}`;
  const grid = $('#crWallGrid');
  grid.className = `cr-wall-grid ${live.length >= 7 ? 'dense' : ''}`;
  grid.innerHTML = live.length ? live.map(profile => controlRoomLiveCard(profile, true)).join('') : '<div class="cr-wall-empty">Nessuna live.</div>';
}

const baseRenderSourcesV26 = renderSources;
renderSources = function renderSourcesControlRoom() {
  const root = $('#sources');
  if (!root) return baseRenderSourcesV26();
  const profiles = controlRoomProfileRows();
  const live = profiles.filter(profile => profile.live).sort((a, b) => controlRoomPriority(b) - controlRoomPriority(a) || timestamp(b.last_seen_live_at) - timestamp(a.last_seen_live_at) || a.display_name.localeCompare(b.display_name, 'it'));
  const offlineFocus = profiles.filter(profile => !profile.live && profile.focus).sort((a, b) => timestamp(b.last_seen_live_at) - timestamp(a.last_seen_live_at) || a.display_name.localeCompare(b.display_name, 'it'));
  const offline = profiles.filter(profile => !profile.live && !profile.focus).sort((a, b) => Number(!!b.last_error) - Number(!!a.last_error) || timestamp(b.last_seen_live_at) - timestamp(a.last_seen_live_at) || a.display_name.localeCompare(b.display_name, 'it'));
  const recCount = live.filter(profile => profile.recording).length;
  const blockedCount = live.filter(profile => profile.blocked).length;
  $('#sourceCount').textContent = profiles.length;
  const panelHead = root.closest('.section')?.querySelector('.section-head');
  if (panelHead) {
    const title = panelHead.querySelector('h2');
    const note = panelHead.querySelector('p');
    if (title) title.textContent = 'Control Room';
    if (note) note.remove();
  }
  if (!profiles.length) {
    root.innerHTML = '<div class="empty">Nessuna sorgente attiva. Quelle archiviate restano disponibili nella Libreria.</div>';
    renderControlRoomWall([]);
    return;
  }
  root.innerHTML = `<div class="cr-toolbar">
      <div class="cr-now-summary"><strong>${live.length} LIVE</strong><span>${recCount} REC</span><span class="${blockedCount ? 'danger-text' : ''}">${blockedCount} NON REC</span></div>
      <button class="btn accent" data-live-wall type="button" ${live.length ? '' : 'disabled'}>▦ Live Wall</button>
    </div>
    <section class="cr-live-section">
      <div class="cr-section-head"><div><h3>Live</h3></div>${blockedCount ? `<span class="cr-attention-count">⚠ ${blockedCount} non registrata${blockedCount === 1 ? '' : 'e'}</span>` : ''}</div>
      ${live.length ? `<div class="cr-live-grid">${live.map(profile => controlRoomLiveCard(profile)).join('')}</div>` : '<div class="cr-live-empty">Nessuna live.</div>'}
    </section>
    ${offlineFocus.length ? `<section class="cr-focus-section"><div class="cr-section-head"><div><h3>Focus</h3></div><span class="count">${offlineFocus.length}</span></div><div class="cr-compact-list">${offlineFocus.map(profile => controlRoomCompactRow(profile, true)).join('')}</div></section>` : ''}
    <details id="controlRoomOffline" class="cr-offline" ${controlRoomOfflineOpen ? 'open' : ''}>
      <summary><span><strong>Altre creator</strong></span><span class="count">${offline.length}</span></summary>
      <div class="cr-compact-list">${offline.length ? offline.map(profile => controlRoomCompactRow(profile)).join('') : '<div class="empty compact">Vuoto.</div>'}</div>
    </details>`;
  renderControlRoomWall(profiles);
};

const baseRenderProfileV26 = renderProfile;
renderProfile = function renderProfileControlRoom() {
  baseRenderProfileV26();
  if (!profileData) return;
  const profile = profileData.source;
  const summary = $('#profileContent .profile-summary');
  if (!summary || summary.querySelector('[data-profile-focus]')) return;
  const button = document.createElement('button');
  button.className = `favorite-toggle ${profile.focus ? 'active' : ''}`;
  button.type = 'button';
  button.dataset.profileFocus = String(profile.id);
  button.setAttribute('aria-pressed', String(!!profile.focus));
  button.textContent = `★ ${profile.focus ? 'In Focus' : 'Metti in Focus'}`;
  summary.prepend(button);
};

document.addEventListener('click', async event => {
  const focusButton = event.target.closest('[data-focus-toggle]');
  if (focusButton) {
    event.preventDefault();
    const sourceId = Number(focusButton.dataset.focusToggle);
    const source = sources.find(row => row.id === sourceId);
    if (!source) return;
    setBusy(focusButton, true, '…');
    try {
      await api(`/api/sources/${sourceId}/library`, {method: 'PATCH', body: JSON.stringify({focus: !source.focus})});
      toast(source.focus ? 'Rimossa dal Focus' : 'Creator fissata nel Focus');
      await refresh({includeRecordings: false});
    } catch (error) { toast(error.message, 'bad'); }
    finally { setBusy(focusButton, false); }
    return;
  }
  const profileFocus = event.target.closest('[data-profile-focus]');
  if (profileFocus && profileData) {
    event.preventDefault();
    setBusy(profileFocus, true, '…');
    try {
      const sourceId = profileData.source.id;
      const next = !profileData.source.focus;
      await api(`/api/sources/${sourceId}/library`, {method: 'PATCH', body: JSON.stringify({focus: next})});
      profileData.source.focus = next;
      for (const source of sources.filter(row => Number(row.profile_id) === Number(profileData.source.profile_id))) source.focus = next;
      renderProfile();
      renderSources();
      toast(next ? 'Creator fissata nel Focus' : 'Rimossa dal Focus');
    } catch (error) { toast(error.message, 'bad'); }
    return;
  }
  if (event.target.closest('[data-live-wall]')) {
    event.preventDefault();
    controlRoomWallOpen = true;
    renderControlRoomWall();
    return;
  }
  if (event.target.closest('[data-live-wall-close]')) {
    event.preventDefault();
    controlRoomWallOpen = false;
    renderControlRoomWall();
    return;
  }
  const resume = event.target.closest('[data-cr-resume-global]');
  if (resume) {
    event.preventDefault();
    setBusy(resume, true, 'Riprendo…');
    try {
      await api('/api/control/recordings', {method: 'POST', body: JSON.stringify({paused: false, stop_active: false})});
      toast('Registrazioni riattivate');
      await refresh({includeRecordings: false});
    } catch (error) { toast(error.message, 'bad'); }
    finally { setBusy(resume, false); }
  }
}, true);

document.addEventListener('toggle', event => {
  if (event.target?.id !== 'controlRoomOffline') return;
  controlRoomOfflineOpen = !!event.target.open;
  localStorage.setItem('livevault-control-room-offline-open', controlRoomOfflineOpen ? '1' : '0');
}, true);

document.addEventListener('error', event => {
  const image = event.target;
  if (!(image instanceof HTMLImageElement) || !image.matches('[data-live-preview]')) return;
  image.classList.add('hidden');
  image.closest('.cr-preview')?.classList.add('preview-missing');
}, true);

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && controlRoomWallOpen) {
    controlRoomWallOpen = false;
    renderControlRoomWall();
  }
});



/* LiveVault Live Intelligence v2.8.3 */
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

function pulseTimeLabel(value, seconds = false) {
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
  return `<span class="cr-pulse-times"><span><b>LIVE</b> ${esc(pulseRangeLabel(session.started_at, session.ended_at, !session.ended_at))}</span><span><b>REC</b> ${recStart ? esc(pulseRangeLabel(recStart, recEnd, false)) : '—'}</span></span>`;
}

function controlRoomPulseMarkup() {
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
