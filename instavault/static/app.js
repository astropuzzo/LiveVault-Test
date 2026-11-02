(() => {
  'use strict';
  const BASE = location.pathname === '/instavault' || location.pathname.startsWith('/instavault/') ? '/instavault' : '';
  const THEME_KEY = 'instavault-theme';
  const VIEWS = ['archive', 'favorites', 'profiles', 'activity', 'settings'];
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const number = value => new Intl.NumberFormat('it-IT').format(Number(value) || 0);
  const day = timestamp => timestamp ? new Intl.DateTimeFormat('it-IT', {day: 'numeric', month: 'short', year: 'numeric'}).format(new Date(timestamp * 1000)) : '';
  const moment = timestamp => timestamp ? new Intl.DateTimeFormat('it-IT', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}).format(new Date(timestamp * 1000)) : 'mai';
  const bytes = value => { const size = Number(value) || 0; const fmt = n => n.toLocaleString('it-IT', {maximumFractionDigits: 1}); return size >= 1024 ** 3 ? `${fmt(size / 1024 ** 3)} GB` : size >= 1024 ** 2 ? `${fmt(size / 1024 ** 2)} MB` : `${Math.round(size / 1024)} KB`; };
  const plural = (count, one, many) => `${number(count)} ${Number(count) === 1 ? one : many}`;
  const stateNames = {queued: 'In coda', running: 'In corso', syncing: 'In corso', done: 'Completato', ready: 'Aggiornato', error: 'Da verificare', cancelled: 'Interrotto', cancel_requested: 'In arresto', paused: 'In pausa'};
  const state = {authenticated: false, csrf: '', view: 'archive', summary: null, profile: '', kind: '', query: '', order: 'newest', cursor: null, posts: [], loadSeq: 0, loading: false, post: null, slide: 0, pending2fa: false, lastCount: null};
  let toastTimer, searchTimer, refreshBusy = false, returnFocus = null;

  function applyTheme(value) {
    if (value === 'light' || value === 'dark') document.documentElement.dataset.theme = value;
    else delete document.documentElement.dataset.theme;
    $('#themeSelect').value = value === 'light' || value === 'dark' ? value : '';
  }
  try { applyTheme(localStorage.getItem(THEME_KEY)); } catch { applyTheme(''); }

  function toast(message) {
    const element = $('#toast'); element.textContent = message; element.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { element.hidden = true; }, 5000);
  }
  function showLogin() {
    state.authenticated = false; state.csrf = ''; state.summary = null; state.posts = []; state.lastCount = null;
    if ($('#postDialog').open) $('#postDialog').close();
    if ($('#profileDialog').open) $('#profileDialog').close();
    ['mediaGrid', 'tray', 'profilesList', 'jobsList', 'profileHeader', 'postAuthor', 'postCaption', 'postMetadata'].forEach(id => $('#' + id).replaceChildren());
    ['igPassword', 'igCode', 'cookieText', 'loginPassword'].forEach(id => { $('#' + id).value = ''; });
    state.pending2fa = false; $('#twoFactorBox').hidden = true;
    $('#appShell').hidden = true; $('#loginGate').hidden = false; document.title = 'Accedi · InstaVault';
  }
  async function api(path, options = {}) {
    const headers = {'Accept': 'application/json', ...options.headers};
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (options.method && options.method !== 'GET' && state.csrf) headers['X-CSRF-Token'] = state.csrf;
    const response = await fetch(BASE + path, {...options, headers, credentials: 'same-origin', cache: 'no-store'});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401 && path !== '/api/login') showLogin();
      const error = new Error(payload.error || 'Operazione non riuscita. Riprova tra poco.'); error.status = response.status; throw error;
    }
    return payload;
  }
  async function busy(button, fn) {
    if (!button || button.disabled) return;
    button.disabled = true; button.setAttribute('aria-busy', 'true');
    try { return await fn(); } finally { button.disabled = false; button.removeAttribute('aria-busy'); }
  }
  function avatar(profile, size = '') {
    const username = profile.username || ''; const initial = username.replace(/[^a-z0-9]/ig, '').slice(0, 1).toUpperCase() || '?';
    const id = Number(profile.profile_id || profile.id);
    return `<span class="avatar ${size}" aria-hidden="true">${esc(initial)}${profile.avatar ? `<img src="${BASE}/api/profiles/${id}/avatar" alt="" loading="lazy" decoding="async">` : ''}</span>`;
  }
  function bindImageFallback(root) {
    $$('img', root).forEach(image => image.addEventListener('error', () => {
      if (image.closest('.avatar')) image.remove();
      else image.closest('.tile')?.classList.add('broken');
    }, {once: true}));
  }
  function profileById(id) { return state.summary?.profiles.find(p => String(p.id) === String(id)); }
  function statusTag(status) { return `<span class="tag ${esc(status)}">${esc(stateNames[status] || 'In attesa')}</span>`; }

  function setSyncStatus(text, mode) {
    $$('[data-sync-status]').forEach(element => { element.dataset.mode = mode; element.lastElementChild.textContent = text; });
  }
  function renderStorage(storage) {
    const used = bytes(storage.used_bytes);
    const note = storage.ready ? `Limite ${bytes(storage.quota_bytes)} · ${bytes(storage.free_bytes)} liberi sul disco` : 'NVMe non collegato · download in pausa';
    $$('[data-storage-used]').forEach(element => { element.textContent = used; });
    $$('[data-storage-sentence]').forEach(element => { element.textContent = `${used} usati`; });
    $$('[data-storage-note]').forEach(element => { element.textContent = note; });
    $$('[data-storage-meter]').forEach(element => { element.value = storage.quota_bytes ? Math.min(100, (storage.used_bytes / storage.quota_bytes) * 100) : 0; });
  }
  function renderNotice(summary) {
    const {profiles = [], storage = {}, instagram = {}} = summary; const notice = $('#notice');
    const failing = profiles.find(p => p.enabled && p.error);
    if (!storage.ready) notice.innerHTML = 'NVMe non collegato: i download sono in pausa e riprendono quando torna disponibile.';
    else if (!instagram.configured && profiles.length) notice.innerHTML = 'Instagram chiede l’accesso per scaricare quasi tutti i profili. <a href="#settings" data-view="settings">Collega l’account</a>';
    else if (failing) notice.innerHTML = `<b>@${esc(failing.username)}</b>: ${esc(failing.error)} <a href="#settings" data-view="settings">Impostazioni</a>`;
    else { notice.hidden = true; return; }
    notice.hidden = false;
  }
  function renderSummary() {
    const summary = state.summary; if (!summary) return;
    const {profiles = [], jobs = [], storage = {}, instagram = {}, settings = {}, counts = {}} = summary;
    $('#navCount').textContent = counts.posts ? number(counts.posts) : '';
    $('#activityDot').hidden = !jobs.length;
    renderStorage(storage);
    const running = jobs.some(j => j.state === 'running');
    if (!storage.ready) setSyncStatus('NVMe scollegato', 'off');
    else if (running) setSyncStatus('Download in corso', 'live');
    else if (jobs.length) setSyncStatus(`${number(jobs.length)} in coda`, 'wait');
    else if (!settings.automatic) setSyncStatus('Automatico in pausa', 'off');
    else setSyncStatus('Aggiornato', 'ok');
    renderNotice(summary);
    $('#instagramState').textContent = instagram.configured ? `Collegato come @${instagram.username}` : 'Non collegato';
    $('#disconnectInstagram').hidden = !instagram.configured;
    if (!$('#instagramLogin').contains(document.activeElement) && instagram.configured && !$('#igUsername').value) $('#igUsername').value = instagram.username;
    if (!$('#settingsForm').contains(document.activeElement)) { $('#automatic').checked = !!settings.automatic; $('#quotaGb').value = settings.quota_gb ?? 60; $('#reserveGb').value = settings.min_free_gb ?? 15; }
    $('#tray').innerHTML = profiles.map(p => `<button class="tray-item${String(p.id) === String(state.profile) ? ' selected' : ''}${p.status === 'syncing' ? ' live' : ''}" type="button" data-profile="${p.id}" aria-label="Apri @${esc(p.username)}">${avatar(p, 'ring')}<span>${esc(p.username)}</span></button>`).join('')
      + '<button class="tray-item add" type="button" data-action="add-profile">' + `<span class="avatar ring" aria-hidden="true">${icon('plus')}</span><span>Aggiungi</span></button>`;
    bindImageFallback($('#tray'));
    renderHeader(); renderProfiles();
  }
  async function refreshSummary(initial = false) {
    if (refreshBusy || !state.authenticated) return;
    refreshBusy = true;
    try {
      const summary = await api('/api/summary');
      if (!state.authenticated) return;
      const count = Number(summary.counts?.posts || 0); const changed = state.lastCount !== null && state.lastCount !== count;
      state.summary = summary; state.lastCount = count; renderSummary();
      if (state.view === 'activity') await loadJobs();
      if (!initial && changed && ['archive', 'favorites'].includes(state.view)) await loadPosts(true);
    } finally { refreshBusy = false; }
  }

  function renderHeader() {
    const summary = state.summary || {}; const profiles = summary.profiles || []; const counts = summary.counts || {};
    const profile = state.view === 'archive' ? profileById(state.profile) : null;
    const favorites = state.view === 'favorites';
    $('#archiveHeader').hidden = !!profile; $('#profileHeader').hidden = !profile; $('#tray').hidden = favorites || !profiles.length && !profile;
    $('#syncAll').hidden = favorites;
    $('#archiveTitle').textContent = favorites ? 'Salvati' : 'Archivio';
    $('#archiveMeta').textContent = favorites ? 'I contenuti che hai segnato col cuore' : profiles.length ? `${plural(counts.posts, 'contenuto', 'contenuti')} · ${plural(profiles.length, 'profilo', 'profili')}` : '';
    if (!profile) { $('#profileHeader').replaceChildren(); return; }
    $('#profileHeader').innerHTML = `${avatar(profile, 'large')}<div class="profile-body">
      <div class="profile-top"><h1>${esc(profile.username)}</h1><div class="page-actions"><button class="button" type="button" data-sync-profile="${profile.id}">${icon('refresh')}Aggiorna</button><button class="button" type="button" data-edit-profile="${profile.id}">Modifica</button></div></div>
      <p class="profile-counts"><span><b>${number(profile.post_count)}</b> contenuti</span><span><b>${number(profile.media_count)}</b> file</span></p>
      ${profile.full_name ? `<p class="profile-name">${esc(profile.full_name)}</p>` : ''}
      ${profile.biography ? `<p class="profile-bio">${esc(profile.biography)}</p>` : ''}
      <p class="stamp">aggiornato ${esc(moment(profile.last_sync))}</p>
      <button class="link back" type="button" data-clear-profile>${icon('left')}Tutti i profili</button>
    </div>`;
    bindImageFallback($('#profileHeader'));
  }
  function renderProfiles() {
    const profiles = state.summary?.profiles || [];
    $('#profilesList').innerHTML = profiles.length ? profiles.map(p => `<article class="row">
      ${avatar(p, 'medium')}
      <div class="row-main">
        <h2><button class="link name" type="button" data-profile="${p.id}">@${esc(p.username)}</button></h2>
        ${p.full_name ? `<p>${esc(p.full_name)}</p>` : ''}
        ${p.error ? `<p class="row-error">${esc(p.error)}</p>` : ''}
        <p class="stamp">${number(p.post_count)} contenuti · ogni ${p.interval_hours === 1 ? 'ora' : p.interval_hours + ' ore'} · aggiornato ${esc(moment(p.last_sync))}</p>
      </div>
      <div class="row-side">
        ${statusTag(p.enabled ? p.status : 'paused')}
        <div class="row-actions">
          <button class="button small" type="button" data-sync-profile="${p.id}">Aggiorna</button>
          <button class="button small" type="button" data-edit-profile="${p.id}">Modifica</button>
          <button class="button small" type="button" data-toggle-profile="${p.id}">${p.enabled ? 'Metti in pausa' : 'Riprendi'}</button>
        </div>
      </div>
    </article>`).join('') : '<div class="empty"><h2>Nessun profilo</h2><p>Aggiungi il primo profilo Instagram da archiviare.</p><button class="button primary" type="button" data-action="add-profile">Aggiungi profilo</button></div>';
    bindImageFallback($('#profilesList'));
  }
  function setView(view, replace = false) {
    if (!VIEWS.includes(view)) view = 'archive';
    if (view !== 'archive') state.profile = '';
    state.view = view;
    $$('.nav-item').forEach(item => { if (item.dataset.view === view) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current'); });
    $('#archiveView').hidden = !['archive', 'favorites'].includes(view); $('#profilesView').hidden = view !== 'profiles'; $('#activityView').hidden = view !== 'activity'; $('#settingsView').hidden = view !== 'settings';
    const names = {archive: 'Archivio', favorites: 'Salvati', profiles: 'Profili', activity: 'Attività', settings: 'Impostazioni'};
    document.title = `${names[view]} · InstaVault`;
    if (state.summary) renderSummary(); else renderHeader();
    if (state.authenticated) {
      if (['archive', 'favorites'].includes(view)) loadPosts(true).catch(report);
      if (view === 'activity') loadJobs().catch(report);
    }
    const hash = '#' + view;
    if (location.hash !== hash) history[replace ? 'replaceState' : 'pushState'](null, '', hash);
    window.scrollTo({top: 0, behavior: 'instant'});
  }
  function selectProfile(id) {
    state.profile = String(id); state.view = 'archive';
    $$('.nav-item').forEach(item => { if (item.dataset.view === 'archive') item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current'); });
    $('#archiveView').hidden = false; ['profilesView', 'activityView', 'settingsView'].forEach(id => { $('#' + id).hidden = true; });
    const profile = profileById(id); document.title = `@${profile?.username || ''} · InstaVault`;
    if (location.hash !== '#archive') history.pushState(null, '', '#archive');
    renderSummary(); loadPosts(true).catch(report); window.scrollTo({top: 0, behavior: 'instant'});
  }
  function clearProfile() { state.profile = ''; document.title = 'Archivio · InstaVault'; renderSummary(); loadPosts(true).catch(report); }

  function postTile(post) {
    const media = post.media?.[0]; if (!media) return '';
    const kind = post.kind === 'reel' || media.kind === 'video' ? 'reel' : post.media.length > 1 ? 'stack' : post.kind === 'story' ? 'story' : '';
    const label = post.kind === 'reel' ? 'Reel' : post.kind === 'story' ? 'Storia' : post.media.length > 1 ? 'Carosello' : 'Post';
    const owner = state.profile ? '' : `@${esc(post.username)} · `;
    const width = media.width || 480; const height = media.height || 600;
    return `<button class="tile" type="button" data-post="${post.id}" aria-label="${esc(label)} di @${esc(post.username)}, ${esc(day(post.published_at))}">
      <img src="${BASE}/media/${media.id}/thumb" alt="" width="${width}" height="${height}" loading="lazy" decoding="async">
      ${kind ? `<span class="tile-kind">${icon(kind)}</span>` : ''}
      ${post.favorite ? `<span class="tile-saved">${icon('heart')}</span>` : ''}
      <span class="tile-hover">${owner}${esc(day(post.published_at))}</span>
    </button>`;
  }
  function showEmpty(title, text, action = null) {
    $('#emptyState').hidden = false; $('#emptyTitle').textContent = title; $('#emptyText').textContent = text;
    const button = $('#emptyAction'); button.hidden = !action;
    if (action) { button.textContent = action.label; button.dataset.action = action.action || ''; button.dataset.view = action.view || ''; }
  }
  function renderEmpty() {
    if (state.posts.length) { $('#emptyState').hidden = true; return; }
    const summary = state.summary || {}; const profiles = summary.profiles || [];
    if (state.view === 'favorites') return showEmpty('Nessun contenuto salvato', 'Apri un contenuto e tocca il cuore per ritrovarlo qui.');
    if (state.query || state.kind) return showEmpty('Nessun risultato', 'Nessun contenuto corrisponde a questi filtri.');
    if (!profiles.length) return showEmpty('Nessun profilo da archiviare', 'Aggiungi un profilo Instagram: InstaVault scarica post, Reel e storie e li conserva sul server.', {label: 'Aggiungi profilo', action: 'add-profile'});
    if (summary.jobs?.length) return showEmpty('Download in coda', 'I primi contenuti compaiono qui appena scaricati.');
    if (!summary.instagram?.configured) return showEmpty('Ancora nessun contenuto', 'Instagram chiede l’accesso prima di mostrare i post. Collega l’account in Impostazioni.', {label: 'Collega l’account', view: 'settings'});
    showEmpty('Ancora nessun contenuto', 'Avvia un aggiornamento: i contenuti scaricati compaiono qui.');
  }
  async function loadPosts(reset = false) {
    if (!state.authenticated || !['archive', 'favorites'].includes(state.view)) return;
    if (state.loading && !reset) return;
    const seq = ++state.loadSeq; state.loading = true;
    const grid = $('#mediaGrid');
    if (reset) {
      state.cursor = null; state.posts = []; $('#loadMore').hidden = true; $('#emptyState').hidden = true;
      grid.setAttribute('aria-busy', 'true'); grid.innerHTML = '<span class="tile placeholder"></span>'.repeat(6); $('#gridStatus').textContent = 'Caricamento dei contenuti';
    }
    const params = new URLSearchParams();
    if (state.profile) params.set('profile', state.profile);
    if (state.kind) params.set('kind', state.kind);
    if (state.query) params.set('q', state.query);
    if (state.view === 'favorites') params.set('favorite', '1');
    params.set('order', state.order);
    if (!reset && state.cursor) params.set('cursor', state.cursor);
    try {
      const result = await api('/api/posts?' + params);
      if (seq !== state.loadSeq || !state.authenticated) return;
      state.cursor = result.next; state.posts = reset ? result.items : [...state.posts, ...result.items];
      if (reset) grid.innerHTML = result.items.map(postTile).join('');
      else grid.insertAdjacentHTML('beforeend', result.items.map(postTile).join(''));
      bindImageFallback(grid); $('#loadMore').hidden = !state.cursor; renderEmpty();
      $('#gridStatus').textContent = state.posts.length ? `${plural(state.posts.length, 'contenuto mostrato', 'contenuti mostrati')}` : '';
    } catch (error) {
      if (seq === state.loadSeq && reset) { grid.replaceChildren(); showEmpty('Archivio non raggiungibile', error.message); }
      throw error;
    } finally { if (seq === state.loadSeq) { state.loading = false; grid.removeAttribute('aria-busy'); } }
  }
  async function loadJobs() {
    const list = $('#jobsList');
    if (!list.children.length) list.innerHTML = '<p class="loading-line">Caricamento dell’attività</p>';
    const result = await api('/api/jobs');
    list.innerHTML = result.items.length ? result.items.map(job => `<article class="row">
      <span class="row-icon${job.state === 'running' ? ' live' : ''}" aria-hidden="true">${icon(job.state === 'done' ? 'download' : 'refresh')}</span>
      <div class="row-main">
        <h2>@${esc(job.username)}</h2>
        <p${job.error ? ' class="row-error"' : ''}>${esc(job.error || job.message || (job.state === 'queued' ? 'In attesa del suo turno' : 'Controllo dei nuovi contenuti'))}</p>
        <p class="stamp">${esc(moment(job.started_at || job.created_at))} · ${plural(job.downloaded, 'nuovo', 'nuovi')} · ${number(job.seen)} controllati</p>
      </div>
      <div class="row-side">${statusTag(job.state)}${['queued', 'running'].includes(job.state) ? `<button class="button small" type="button" data-cancel-job="${job.id}">Interrompi</button>` : ''}</div>
    </article>`).join('') : '<div class="empty"><h2>Nessun aggiornamento</h2><p>Gli aggiornamenti dei profili compaiono qui, uno alla volta.</p></div>';
  }

  function openProfile(profile = null) {
    returnFocus = document.activeElement; $('#profileError').textContent = ''; $('#profileId').value = profile?.id || '';
    $('#profileDialogTitle').textContent = profile ? `Modifica @${profile.username}` : 'Aggiungi un profilo';
    $('#profileUsername').value = profile?.username || ''; $('#profileUsername').readOnly = !!profile;
    $('#profileSubmit').textContent = profile ? 'Salva modifiche' : 'Aggiungi profilo';
    ['posts', 'reels', 'stories', 'highlights'].forEach(key => { $('#p' + key[0].toUpperCase() + key.slice(1)).checked = profile ? !!profile[key] : key !== 'highlights'; });
    const select = $('#pInterval'); const value = profile?.interval_hours || 6;
    $$('option[data-custom]', select).forEach(option => option.remove());
    if (![...select.options].some(option => Number(option.value) === Number(value))) { const option = new Option(`Ogni ${value} ore`, value); option.dataset.custom = '1'; select.add(option); }
    select.value = String(value); $('#profileDialog').showModal();
  }
  function cleanupMedia() { $$('video', $('#viewerMedia')).forEach(video => { video.pause(); video.removeAttribute('src'); video.load(); }); $('#viewerMedia').replaceChildren(); }
  async function openPost(id) {
    returnFocus = document.activeElement;
    const result = await api('/api/posts/' + Number(id)); state.post = result; state.slide = 0;
    $('#postAuthor').innerHTML = `${avatar(result, 'small')}<div><button class="link name" type="button" data-profile="${Number(result.profile_id)}">${esc(result.username)}</button>${result.full_name ? `<small>${esc(result.full_name)}</small>` : ''}</div>`;
    bindImageFallback($('#postAuthor'));
    $('#postCaption').textContent = result.caption || result.collection || '';
    $('#postCaption').hidden = !(result.caption || result.collection);
    $('#postMetadata').textContent = `pubblicato ${day(result.published_at)} · archiviato ${day(result.archived_at)}`;
    const external = String(result.permalink || '');
    $('#openInstagram').href = /^https:\/\/www\.instagram\.com\//.test(external) ? external : 'https://www.instagram.com/';
    updateFavorite(); renderSlide(); $('#postDialog').showModal(); document.body.classList.add('dialog-open');
  }
  function updateFavorite() {
    const saved = !!state.post?.favorite; const button = $('#favoritePost');
    button.setAttribute('aria-pressed', String(saved)); button.setAttribute('aria-label', saved ? 'Rimuovi dai salvati' : 'Aggiungi ai salvati');
  }
  function renderSlide() {
    cleanupMedia(); const post = state.post; if (!post) return;
    const media = post.media[state.slide];
    if (!media) { $('#viewerMedia').innerHTML = '<p class="viewer-missing">Contenuto non disponibile</p>'; return; }
    const item = document.createElement(media.kind === 'video' ? 'video' : 'img');
    item.src = `${BASE}/media/${media.id}`;
    if (media.width && media.height) { item.width = media.width; item.height = media.height; }
    if (media.kind === 'video') { item.controls = true; item.playsInline = true; item.preload = 'metadata'; item.poster = `${BASE}/media/${media.id}/thumb`; }
    else { item.alt = post.caption?.slice(0, 150) || `Foto di @${post.username}`; item.decoding = 'async'; }
    item.addEventListener('error', () => { $('#viewerMedia').innerHTML = '<p class="viewer-missing">File non disponibile. Controlla che l’NVMe sia collegato.</p>'; }, {once: true});
    $('#viewerMedia').append(item);
    const many = post.media.length > 1;
    $('#previousMedia').hidden = !many || state.slide === 0; $('#nextMedia').hidden = !many || state.slide === post.media.length - 1;
    $('#carouselCount').hidden = !many; $('#carouselCount').textContent = `${state.slide + 1} / ${post.media.length}`;
    $('#downloadOriginal').href = `${BASE}/media/${media.id}?download=1`;
  }
  function moveSlide(delta) {
    if (!state.post) return; const next = state.slide + delta;
    if (next < 0 || next >= state.post.media.length) return;
    state.slide = next; renderSlide();
    // An arrow hides at either end of a carousel; keep keyboard focus inside the viewer.
    if (document.activeElement?.hidden) (delta > 0 ? $('#previousMedia') : $('#nextMedia')).focus();
  }
  function report(error) { if (error.status !== 401) toast(error.message || 'Operazione non riuscita.'); }
  async function start() {
    const me = await api('/api/me'); state.csrf = me.csrf; state.authenticated = true; $('#loginGate').hidden = true; $('#appShell').hidden = false;
    await refreshSummary(true); setView(location.hash.slice(1) || 'archive', true);
  }
  async function logout(button) {
    await busy(button, async () => { try { await api('/api/logout', {method: 'POST', body: '{}'}); showLogin(); } catch (error) { report(error); } });
  }

  $('#vaultLogin').addEventListener('submit', event => {
    event.preventDefault(); $('#loginError').textContent = '';
    const body = {username: $('#loginUsername').value, password: $('#loginPassword').value}; $('#loginPassword').value = '';
    busy(event.submitter, async () => { try { await api('/api/login', {method: 'POST', body: JSON.stringify(body)}); await start(); } catch (error) { $('#loginError').textContent = error.message; } finally { body.password = ''; } });
  });
  document.addEventListener('click', event => {
    const target = event.target.closest('button, a'); if (!target) return;
    const data = target.dataset;
    if (data.close) { $('#' + data.close)?.close(); return; }
    if (data.action === 'logout') { logout(target); return; }
    if (data.action === 'add-profile') { openProfile(); return; }
    if (data.view) { event.preventDefault(); if ($('#postDialog').open) $('#postDialog').close(); if (data.view === 'archive') state.profile = ''; setView(data.view); return; }
    if (data.profile) { if ($('#postDialog').open) $('#postDialog').close(); selectProfile(data.profile); return; }
    if (Object.hasOwn(data, 'clearProfile')) { clearProfile(); return; }
    if (data.post) { openPost(data.post).catch(report); return; }
    if (data.editProfile) { openProfile(profileById(data.editProfile)); return; }
    if (data.syncProfile) { busy(target, async () => { try { await api(`/api/profiles/${Number(data.syncProfile)}/sync`, {method: 'POST', body: '{}'}); toast('Aggiornamento in coda'); await refreshSummary(); } catch (error) { report(error); } }); return; }
    if (data.toggleProfile) { busy(target, async () => { try { const p = profileById(data.toggleProfile); await api('/api/profiles/' + p.id, {method: 'PATCH', body: JSON.stringify({enabled: !p.enabled})}); toast(p.enabled ? 'Profilo in pausa, i contenuti restano' : 'Aggiornamenti riattivati'); await refreshSummary(); } catch (error) { report(error); } }); return; }
    if (data.cancelJob) { busy(target, async () => { try { await api(`/api/jobs/${Number(data.cancelJob)}/cancel`, {method: 'POST', body: '{}'}); toast('Interruzione richiesta'); await refreshSummary(); await loadJobs(); } catch (error) { report(error); } }); return; }
    if (Object.hasOwn(data, 'kind')) {
      state.kind = data.kind; $$('[data-kind]').forEach(b => b.setAttribute('aria-pressed', String(b === target)));
      loadPosts(true).catch(report);
    }
  });
  $('#search').addEventListener('input', event => { clearTimeout(searchTimer); const value = event.target.value; searchTimer = setTimeout(() => { state.query = value.trim(); loadPosts(true).catch(report); }, 250); });
  $('#sortOrder').addEventListener('change', event => { state.order = event.target.value; loadPosts(true).catch(report); });
  $('#loadMore').addEventListener('click', event => busy(event.currentTarget, () => loadPosts().catch(report)));
  $('#syncAll').addEventListener('click', event => busy(event.currentTarget, async () => {
    try { const result = await api('/api/sync', {method: 'POST', body: '{}'}); toast(result.queued ? `Aggiornamento in coda per ${plural(result.queued, 'profilo', 'profili')}` : 'Nessun profilo attivo da aggiornare'); await refreshSummary(); } catch (error) { report(error); }
  }));
  $('#profileForm').addEventListener('submit', event => {
    event.preventDefault(); $('#profileError').textContent = '';
    const ident = $('#profileId').value;
    const body = {username: $('#profileUsername').value.trim(), interval_hours: Number($('#pInterval').value), posts: $('#pPosts').checked, reels: $('#pReels').checked, stories: $('#pStories').checked, highlights: $('#pHighlights').checked};
    if (!body.posts && !body.reels && !body.stories && !body.highlights) { $('#profileError').textContent = 'Scegli almeno un tipo di contenuto.'; return; }
    busy(event.submitter, async () => {
      try { await api('/api/profiles' + (ident ? '/' + ident : ''), {method: ident ? 'PATCH' : 'POST', body: JSON.stringify(body)}); $('#profileDialog').close(); toast(ident ? 'Modifiche salvate' : 'Profilo aggiunto, primo download in coda'); await refreshSummary(); if (!ident && ['archive', 'favorites'].includes(state.view)) renderEmpty(); }
      catch (error) { $('#profileError').textContent = error.message; }
    });
  });
  $('#settingsForm').addEventListener('submit', event => {
    event.preventDefault();
    const body = {automatic: $('#automatic').checked, quota_gb: Number($('#quotaGb').value), min_free_gb: Number($('#reserveGb').value)};
    busy(event.submitter, async () => { try { await api('/api/settings', {method: 'POST', body: JSON.stringify(body)}); toast('Impostazioni salvate'); await refreshSummary(); } catch (error) { report(error); } });
  });
  $('#themeSelect').addEventListener('change', event => { const value = event.target.value; applyTheme(value); try { if (value) localStorage.setItem(THEME_KEY, value); else localStorage.removeItem(THEME_KEY); } catch { /* the choice still applies to this visit */ } });
  $('#instagramLogin').addEventListener('submit', event => {
    event.preventDefault(); $('#igError').textContent = '';
    const body = state.pending2fa ? {code: $('#igCode').value.trim()} : {username: $('#igUsername').value.trim(), password: $('#igPassword').value};
    $('#igPassword').value = ''; $('#igCode').value = '';
    busy(event.submitter, async () => {
      try {
        const result = await api('/api/instagram/login', {method: 'POST', body: JSON.stringify(body)});
        if (result.two_factor) { state.pending2fa = true; $('#twoFactorBox').hidden = false; $('#igError').textContent = 'Inserisci il codice dell’autenticazione a due fattori.'; $('#igCode').focus(); }
        else { state.pending2fa = false; $('#twoFactorBox').hidden = true; toast('Account Instagram collegato'); await refreshSummary(); }
      } catch (error) { $('#igError').textContent = error.message; } finally { body.password = ''; body.code = ''; }
    });
  });
  $('#resetTwoFactor').addEventListener('click', () => { state.pending2fa = false; $('#twoFactorBox').hidden = true; $('#igCode').value = ''; $('#igError').textContent = ''; $('#igPassword').focus(); });
  $('#cookieImport').addEventListener('submit', event => {
    event.preventDefault();
    const body = {username: $('#cookieUsername').value.trim(), cookies: $('#cookieText').value}; $('#cookieText').value = '';
    busy(event.submitter, async () => { try { await api('/api/instagram/cookies', {method: 'POST', body: JSON.stringify(body)}); toast('Sessione importata e cifrata sul server'); $('#cookieImport').closest('details').open = false; await refreshSummary(); } catch (error) { report(error); } finally { body.cookies = ''; } });
  });
  $('#disconnectInstagram').addEventListener('click', event => busy(event.currentTarget, async () => {
    try { await api('/api/instagram/disconnect', {method: 'POST', body: '{}'}); state.pending2fa = false; $('#twoFactorBox').hidden = true; ['igUsername', 'igPassword', 'igCode'].forEach(id => { $('#' + id).value = ''; }); toast('Account scollegato, sessione rimossa dal server'); await refreshSummary(); } catch (error) { report(error); }
  }));
  $('#closePost').addEventListener('click', () => $('#postDialog').close());
  $('#previousMedia').addEventListener('click', () => moveSlide(-1));
  $('#nextMedia').addEventListener('click', () => moveSlide(1));
  $('#favoritePost').addEventListener('click', event => busy(event.currentTarget, async () => {
    if (!state.post) return;
    try {
      const favorite = !state.post.favorite; const id = state.post.id;
      await api(`/api/posts/${id}/favorite`, {method: 'POST', body: JSON.stringify({favorite})});
      state.post.favorite = favorite; updateFavorite();
      const cached = state.posts.find(p => p.id === id); if (cached) cached.favorite = favorite;
      if (state.view === 'favorites' && !favorite) state.posts = state.posts.filter(p => p.id !== id);
      $('#mediaGrid').innerHTML = state.posts.map(postTile).join(''); bindImageFallback($('#mediaGrid')); renderEmpty();
    } catch (error) { report(error); }
  }));
  $('#postDialog').addEventListener('close', () => { cleanupMedia(); state.post = null; document.body.classList.remove('dialog-open'); returnFocus?.focus?.(); });
  $('#profileDialog').addEventListener('close', () => { returnFocus?.focus?.(); });
  ['postDialog', 'profileDialog'].forEach(id => { const dialog = $('#' + id); dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); }); });
  document.addEventListener('keydown', event => {
    if (!$('#postDialog').open || /INPUT|TEXTAREA|SELECT|VIDEO/.test(event.target.tagName)) return;
    if (event.key === 'ArrowLeft') { event.preventDefault(); moveSlide(-1); }
    if (event.key === 'ArrowRight') { event.preventDefault(); moveSlide(1); }
  });
  let touchStart = null;
  $('#viewerMedia').addEventListener('touchstart', event => { if (!event.target.closest('video')) touchStart = {x: event.changedTouches[0].clientX, y: event.changedTouches[0].clientY}; }, {passive: true});
  $('#viewerMedia').addEventListener('touchend', event => { if (!touchStart) return; const dx = event.changedTouches[0].clientX - touchStart.x; const dy = event.changedTouches[0].clientY - touchStart.y; if (Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy) * 1.5) moveSlide(dx < 0 ? 1 : -1); touchStart = null; }, {passive: true});
  window.addEventListener('popstate', () => { if (state.authenticated) setView(location.hash.slice(1) || 'archive', true); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && state.authenticated) refreshSummary().catch(report); });
  setInterval(() => { if (!document.hidden && state.authenticated && !$('#profileDialog').open) refreshSummary().catch(error => { if (error.status === 401) showLogin(); }); }, 15000);
  start().catch(error => { showLogin(); if (error.status !== 401) $('#loginError').textContent = 'Server non raggiungibile. Riprova tra poco.'; });
})();
