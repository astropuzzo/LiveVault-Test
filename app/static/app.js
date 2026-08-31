'use strict';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const login = $('#login');
const app = $('#app');
let sourceCache = [];
let recordingCache = [];
let statusCache = null;
let refreshRunning = false;
let refreshQueued = false;

async function api(path, opts = {}) {
  const options = {...opts};
  const headers = {...(opts.headers || {})};
  if (opts.body && !(opts.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  options.headers = headers;
  const response = await fetch(path, options);
  if (response.status === 401) {
    showLogin();
    throw new Error('auth');
  }
  let data = null;
  try { data = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(data?.detail || `HTTP ${response.status}`);
  return data;
}

function showLogin() {
  app.classList.add('hidden');
  login.classList.remove('hidden');
  setTimeout(() => $('#password')?.focus(), 50);
}
function showApp() {
  login.classList.add('hidden');
  app.classList.remove('hidden');
}
function esc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}
function safeUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : '';
  } catch (_) { return ''; }
}
function humanBytes(n) {
  let v = Number(n || 0);
  const units = ['B','KB','MB','GB','TB'];
  for (const unit of units) {
    if (v < 1024 || unit === 'TB') return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${unit}`;
    v /= 1024;
  }
  return `${v.toFixed(1)} TB`;
}
function duration(seconds, compact = false) {
  const s = Math.max(0, Number(seconds || 0));
  if (!s) return '—';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  if (compact) return h ? `${h}h ${m}m` : m ? `${m}m` : `${sec}s`;
  return h ? `${h}h ${m}m` : `${m} min`;
}
function localDate(value) {
  if (!value) return '—';
  try { return new Intl.DateTimeFormat('it-IT', {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}).format(new Date(value)); }
  catch (_) { return value; }
}
function ago(value) {
  if (!value) return 'mai';
  const sec = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (sec < 60) return 'ora';
  if (sec < 3600) return `${Math.floor(sec/60)} min fa`;
  if (sec < 86400) return `${Math.floor(sec/3600)} h fa`;
  return `${Math.floor(sec/86400)} g fa`;
}
function statusLabel(status) {
  return ({recording:'REC',live:'LIVE',offline:'Offline',error:'Errore',paused:'In pausa',unknown:'In attesa'})[status] || status;
}
function uploadLabel(status) {
  return ({uploaded:'Caricato',uploading:'Upload',pending:'In attesa',failed:'Fallito',waiting_config:'Configura cloud',missing:'Mancante'})[status] || status;
}
function toast(message, type = 'good') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  $('#toastRegion').append(el);
  setTimeout(() => el.remove(), 3600);
}
function setBusy(button, busy, label) {
  if (!button) return;
  if (busy) {
    button.dataset.oldText = button.textContent;
    button.disabled = true;
    if (label) button.textContent = label;
  } else {
    button.disabled = false;
    if (button.dataset.oldText) button.textContent = button.dataset.oldText;
  }
}

async function boot() {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
  try {
    await api('/api/me');
    showApp();
    await refresh();
  } catch (e) {
    if (e.message !== 'auth') showLogin();
  }
}

$('#loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  $('#loginError').textContent = '';
  const button = e.currentTarget.querySelector('button[type="submit"]');
  setBusy(button, true, 'Accesso…');
  try {
    await api('/api/login', {method:'POST', body:JSON.stringify({password:$('#password').value})});
    $('#password').value = '';
    showApp();
    await refresh();
  } catch (err) {
    if (err.message !== 'auth') $('#loginError').textContent = err.message;
  } finally { setBusy(button, false); }
});

$('#logoutBtn').addEventListener('click', async () => {
  await api('/api/logout', {method:'POST'}).catch(() => {});
  showLogin();
});

function openSourceModal(source = null) {
  $('#sourceId').value = source?.id || '';
  $('#sourceName').value = source?.name || '';
  $('#sourceSlug').value = source?.slug || '';
  $('#sourceQuality').value = source?.quality || 'best';
  $('#sourceConsent').checked = source ? Boolean(source.consent_confirmed) : false;
  $('#modalTitle').textContent = source ? 'Modifica sorgente' : 'Aggiungi sorgente';
  $('#saveSourceBtn').textContent = source ? 'Salva modifiche' : 'Salva sorgente';
  $('#sourceError').textContent = '';
  $('#sourceModal').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  setTimeout(() => $('#sourceName').focus(), 50);
}
function closeSourceModal() {
  $('#sourceModal').classList.add('hidden');
  document.body.style.overflow = '';
}

$('#showAddBtn').addEventListener('click', () => openSourceModal());
$('#mobileAddBtn').addEventListener('click', () => openSourceModal());
$('#closeModalBtn').addEventListener('click', closeSourceModal);
$('#cancelModalBtn').addEventListener('click', closeSourceModal);
$('#sourceModal').addEventListener('click', e => { if (e.target.dataset.closeModal) closeSourceModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#sourceModal').classList.contains('hidden')) closeSourceModal(); });

$('#sourceForm').addEventListener('submit', async e => {
  e.preventDefault();
  const id = $('#sourceId').value;
  const body = {
    name: $('#sourceName').value.trim(),
    slug: $('#sourceSlug').value.trim(),
    quality: $('#sourceQuality').value,
    consent_confirmed: $('#sourceConsent').checked,
  };
  $('#sourceError').textContent = '';
  if (!body.consent_confirmed) {
    $('#sourceError').textContent = 'Devi confermare l’autorizzazione per attivare la sorgente.';
    return;
  }
  const button = $('#saveSourceBtn');
  setBusy(button, true, 'Salvataggio…');
  try {
    if (id) await api(`/api/sources/${id}`, {method:'PATCH', body:JSON.stringify(body)});
    else await api('/api/sources', {method:'POST', body:JSON.stringify({...body, platform:'chaturbate'})});
    closeSourceModal();
    toast(id ? 'Sorgente aggiornata' : 'Sorgente aggiunta');
    await refresh();
  } catch (err) {
    $('#sourceError').textContent = err.message;
  } finally { setBusy(button, false); }
});

async function checkNow(button = null) {
  setBusy(button, true, '…');
  try {
    await api('/api/sources/check-now', {method:'POST'});
    toast('Controllo live richiesto');
    await new Promise(r => setTimeout(r, 600));
    await refresh();
  } catch (err) {
    if (err.message !== 'auth') toast(err.message, 'bad');
  } finally { setBusy(button, false); }
}
$('#refreshBtn').addEventListener('click', e => checkNow(e.currentTarget));
$('#mobileRefreshBtn').addEventListener('click', e => checkNow(e.currentTarget));
$('#mobileTopBtn').addEventListener('click', () => window.scrollTo({top:0, behavior:'smooth'}));

$('#sources').addEventListener('click', async e => {
  const button = e.target.closest('[data-action]');
  if (!button) return;
  const id = Number(button.dataset.id);
  const source = sourceCache.find(s => s.id === id);
  if (!source) return;
  const action = button.dataset.action;
  try {
    if (action === 'edit') return openSourceModal(source);
    if (action === 'toggle') {
      setBusy(button, true, source.enabled ? 'Stop…' : 'Avvio…');
      await api(`/api/sources/${id}`, {method:'PATCH', body:JSON.stringify({enabled:!source.enabled})});
      toast(source.enabled ? 'Sorgente messa in pausa' : 'Sorgente riattivata');
      await refresh();
    }
    if (action === 'delete') {
      if (!confirm(`Rimuovere “${source.name}”? Lo storico delle registrazioni resterà disponibile.`)) return;
      setBusy(button, true, '…');
      await api(`/api/sources/${id}`, {method:'DELETE'});
      toast('Sorgente rimossa');
      await refresh();
    }
  } catch (err) {
    if (err.message !== 'auth') toast(err.message, 'bad');
    setBusy(button, false);
  }
});

$('#recordings').addEventListener('click', async e => {
  const button = e.target.closest('[data-rec-action]');
  if (!button) return;
  const id = Number(button.dataset.id);
  try {
    if (button.dataset.recAction === 'retry') {
      setBusy(button, true, '…');
      await api(`/api/recordings/${id}/retry`, {method:'POST'});
      toast('Upload rimesso in coda');
      await refresh();
    }
    if (button.dataset.recAction === 'delete-local') {
      if (!confirm('Eliminare la copia locale? La copia cloud verificata resterà disponibile.')) return;
      setBusy(button, true, '…');
      await api(`/api/recordings/${id}/local`, {method:'DELETE'});
      toast('Copia locale eliminata');
      await refresh();
    }
  } catch (err) {
    if (err.message !== 'auth') toast(err.message, 'bad');
    setBusy(button, false);
  }
});

$('#retryAllBtn').addEventListener('click', async e => {
  setBusy(e.currentTarget, true, 'Riprovo…');
  try {
    const result = await api('/api/recordings/retry-failed', {method:'POST'});
    toast(`${result.changed} file rimessi in coda`);
    await refresh();
  } catch (err) { toast(err.message, 'bad'); }
  finally { setBusy(e.currentTarget, false); }
});

$('#cleanupBtn').addEventListener('click', async e => {
  if (!confirm('Eliminare dal server tutte le copie locali che risultano già caricate e verificate?')) return;
  setBusy(e.currentTarget, true, 'Pulizia…');
  try {
    const result = await api('/api/recordings/cleanup-uploaded', {method:'POST'});
    toast(`Liberati ${result.freed_human} · ${result.removed} file`);
    await refresh();
  } catch (err) { toast(err.message, 'bad'); }
  finally { setBusy(e.currentTarget, false); }
});

$('#recordingSearch').addEventListener('input', renderRecordings);
$('#recordingStatus').addEventListener('change', renderRecordings);
$('#diagnosticToggle').addEventListener('click', () => $('#errorsPanel').classList.toggle('collapsed'));

function renderSources() {
  const el = $('#sources');
  $('#sourceCount').textContent = sourceCache.length;
  if (!sourceCache.length) {
    el.innerHTML = '<div class="empty-state"><div class="empty-icon">◎</div><strong>Nessuna sorgente</strong><span>Aggiungi la prima sorgente autorizzata per iniziare.</span></div>';
    return;
  }
  el.innerHTML = sourceCache.map(s => {
    const live = ['recording','live'].includes(s.last_status);
    const checked = s.last_checked_at ? `Controllata ${ago(s.last_checked_at)}` : 'Non ancora controllata';
    const lastLive = s.last_live_at ? `Ultima live ${ago(s.last_live_at)}` : 'Nessuna live registrata';
    return `<article class="source-card ${live ? 'live-card' : ''}">
      <div class="source-top">
        <div class="source-title-wrap"><div class="source-name">${esc(s.name)}</div><div class="source-slug">@${esc(s.slug)}</div></div>
        <span class="source-status ${esc(s.last_status)}"><span class="dot"></span>${esc(statusLabel(s.last_status))}</span>
      </div>
      <div class="source-meta"><span class="meta-chip">${esc(s.quality === 'best' ? 'Best' : s.quality)}</span><span class="meta-chip">${esc(checked)}</span><span class="meta-chip">${esc(lastLive)}</span></div>
      <div class="source-actions">
        <button class="btn btn-soft" type="button" data-action="edit" data-id="${s.id}">Modifica</button>
        <button class="btn btn-soft" type="button" data-action="toggle" data-id="${s.id}">${s.enabled ? (live ? 'Stop & pausa' : 'Pausa') : 'Riattiva'}</button>
        <span class="spacer"></span>
        <button class="btn btn-danger" type="button" data-action="delete" data-id="${s.id}">Rimuovi</button>
      </div>
    </article>`;
  }).join('');
}

function renderRecordings() {
  const el = $('#recordings');
  const query = $('#recordingSearch').value.trim().toLowerCase();
  const status = $('#recordingStatus').value;
  const filtered = recordingCache.filter(r => {
    if (status !== 'all' && r.upload_status !== status) return false;
    if (query && !`${r.source_name} ${r.filename} ${r.session_id || ''}`.toLowerCase().includes(query)) return false;
    return true;
  });
  const failedCount = recordingCache.filter(r => ['failed','waiting_config'].includes(r.upload_status) && r.local_available).length;
  $('#retryAllBtn').classList.toggle('hidden', failedCount === 0);
  if (!filtered.length) {
    el.innerHTML = `<div class="empty-state"><div class="empty-icon">▱</div><strong>${recordingCache.length ? 'Nessun risultato' : 'Archivio vuoto'}</strong><span>${recordingCache.length ? 'Prova a cambiare ricerca o filtro.' : 'Le registrazioni completate compariranno qui.'}</span></div>`;
    $('#recordingFooter').classList.add('hidden');
    return;
  }
  el.innerHTML = filtered.map(r => {
    const remoteUrl = safeUrl(r.remote_url);
    const remote = remoteUrl ? `<a class="btn btn-soft" href="${esc(remoteUrl)}" target="_blank" rel="noopener noreferrer">Cloud</a>` : '';
    const local = r.local_available ? `<a class="btn btn-soft" href="/api/recordings/${r.id}/download">Scarica</a>` : '';
    const retry = ['failed','waiting_config'].includes(r.upload_status) && r.local_available ? `<button class="btn btn-soft" type="button" data-rec-action="retry" data-id="${r.id}">Riprova</button>` : '';
    const del = r.upload_status === 'uploaded' && r.local_available ? `<button class="btn btn-danger" type="button" data-rec-action="delete-local" data-id="${r.id}">Libera</button>` : '';
    return `<article class="rec-row">
      <div class="rec-primary"><div class="rec-name">${esc(r.source_name)} · ${esc(r.filename)}</div><div class="rec-sub"><span>${esc(localDate(r.finalized_at))}</span><span>•</span><span>${r.local_available ? 'locale + cloud' : (r.remote_url ? 'solo cloud' : 'solo indice')}</span></div></div>
      <div class="rec-size"><span class="rec-cell-label">Dimensione</span><span class="rec-value">${esc(r.size_human)}</span></div>
      <div class="rec-duration"><span class="rec-cell-label">Durata</span><span class="rec-value">${esc(duration(r.duration_seconds))}</span></div>
      <div class="rec-provider"><span class="upload-pill ${esc(r.upload_status)}">${esc(uploadLabel(r.upload_status))}${r.upload_provider ? ` · ${esc(r.upload_provider)}` : ''}</span>${r.last_error ? `<div class="rec-error" title="${esc(r.last_error)}">${esc(r.last_error.slice(0,120))}</div>` : ''}</div>
      <div class="rec-actions">${local}${remote}${retry}${del}</div>
    </article>`;
  }).join('');
  const footer = $('#recordingFooter');
  footer.textContent = `${filtered.length} visualizzate su ${recordingCache.length} registrazioni caricate`;
  footer.classList.remove('hidden');
}

function renderStatus(status) {
  statusCache = status;
  const active = status.worker.active || [];
  $('#versionLabel').textContent = `REMOTE RECORDER · v${status.config.version || '—'}`;
  $('#activeCount').textContent = active.length;
  $('#activeNames').textContent = active.length ? active.map(x => x.source_name).join(', ') : 'Nessuna sorgente live';
  $('#queueCount').textContent = status.queue.pending;
  $('#queueNote').textContent = status.queue.failed ? `${status.queue.failed} con errore` : status.queue.pending ? 'segmenti da trasferire' : 'Nessun file in attesa';
  $('#freeSpace').textContent = status.disk.free_human;
  $('#diskUsage').textContent = `${status.disk.used_human} usati / ${status.disk.total_human}`;
  $('#localBuffer').textContent = `Buffer: ${status.queue.local_human}`;
  const usedPct = status.disk.total ? Math.min(100, Math.max(0, status.disk.used / status.disk.total * 100)) : 0;
  $('#diskBar').style.width = `${usedPct}%`;
  $('#diskBar').className = `progress-fill ${status.disk.pressure}`;
  const badge = $('#diskBadge');
  badge.className = `status-chip ${status.disk.pressure}`;
  badge.textContent = ({ok:'OK',warning:'Attenzione',critical:'Critico'})[status.disk.pressure] || status.disk.pressure;
  $('#uploadConfig').textContent = `${status.config.primary_uploader} → ${status.config.fallback_uploader}`;

  const current = status.worker.upload_current;
  if (current) {
    $('#uploadState').textContent = 'In upload';
    $('#uploadDetail').textContent = `${current.provider || 'cloud'} · ${current.filename}`;
  } else {
    $('#uploadState').textContent = status.queue.pending ? 'In coda' : 'Pronto';
    $('#uploadDetail').textContent = status.queue.pending ? `${status.queue.pending} segmenti in attesa` : 'Nessun trasferimento attivo';
  }

  const activeStrip = $('#activeStrip');
  if (active.length) {
    const first = active[0];
    activeStrip.classList.remove('hidden');
    $('#activeStripTitle').textContent = active.length === 1 ? `${first.source_name} · registrazione in corso` : `${active.length} registrazioni in corso`;
    $('#activeStripMeta').textContent = active.length === 1 ? `REC ${duration(first.elapsed_seconds, true)} · segmenti da ${status.config.segment_minutes} min` : active.map(x => x.source_name).join(' · ');
    $('#activeStripSize').textContent = active.length === 1 ? humanBytes(first.local_bytes) : `${active.length} stream`;
  } else activeStrip.classList.add('hidden');

  const errorCount = Object.keys(status.worker.errors || {}).length;
  const health = $('#healthPill');
  health.className = `health-pill ${status.disk.pressure === 'critical' || errorCount ? (status.disk.pressure === 'critical' ? 'bad' : 'warning') : ''}`;
  health.querySelector('span:last-child').textContent = status.disk.pressure === 'critical' ? 'Storage critico' : errorCount ? 'Da controllare' : 'Online';

  const errors = status.worker.errors || {};
  if (errorCount) {
    $('#errorsPanel').classList.remove('hidden');
    $('#diagnosticCount').textContent = errorCount;
    $('#errors').textContent = Object.entries(errors).map(([k,v]) => `${k}\n${v}`).join('\n\n');
  } else {
    $('#errorsPanel').classList.add('hidden');
  }
}

async function refresh() {
  if (refreshRunning) { refreshQueued = true; return; }
  refreshRunning = true;
  try {
    const [status, sources, recordings] = await Promise.all([
      api('/api/status'), api('/api/sources'), api('/api/recordings?limit=500')
    ]);
    sourceCache = sources;
    recordingCache = recordings;
    renderStatus(status);
    renderSources();
    renderRecordings();
    $('#lastRefresh').textContent = `Aggiornato ${new Intl.DateTimeFormat('it-IT',{hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date())}`;
  } catch (e) {
    if (e.message !== 'auth') {
      console.error(e);
      $('#lastRefresh').textContent = `Errore aggiornamento · ${e.message}`;
    }
  } finally {
    refreshRunning = false;
    if (refreshQueued) { refreshQueued = false; setTimeout(refresh, 50); }
  }
}

boot();
setInterval(() => { if (!app.classList.contains('hidden') && !document.hidden) refresh(); }, 8000);
document.addEventListener('visibilitychange', () => { if (!document.hidden && !app.classList.contains('hidden')) refresh(); });
