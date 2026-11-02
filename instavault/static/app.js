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
  const every = (minutes, title = false) => {
    const text = minutes === 60 ? 'ogni ora' : minutes % 60 ? `ogni ${minutes} minuti` : `ogni ${minutes / 60} ore`;
    return title ? text[0].toUpperCase() + text.slice(1) : text;
  };
  const stateNames = {queued: 'In coda', running: 'In corso', syncing: 'In corso', done: 'Completato', ready: 'Aggiornato', error: 'Da verificare', cancelled: 'Interrotto', cancel_requested: 'In arresto', paused: 'In pausa', private: 'Privato'};
  const KINDS = {post: ['post', 'post'], reel: ['Reel', 'Reel'], story: ['storia', 'storie'], highlight: ['contenuto in evidenza', 'contenuti in evidenza']};
  const state = {authenticated: false, csrf: '', view: 'archive', summary: null, profile: '', kind: 'post', collection: '', igChanging: false, query: '', order: 'newest', cursor: null, posts: [], loadSeq: 0, loading: false, post: null, slide: 0, pendingCode: false, lastCount: null};
  let toastTimer, searchTimer, refreshBusy = false, returnFocus = null;
  let loginAttemptId = null, loginPollGeneration = 0;

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
    ++loginPollGeneration; loginAttemptId = null;
    state.authenticated = false; state.csrf = ''; state.summary = null; state.posts = []; state.lastCount = null;
    if ($('#postDialog').open) $('#postDialog').close();
    if ($('#profileDialog').open) $('#profileDialog').close();
    ['mediaGrid', 'profileCards', 'collections', 'profilesList', 'jobsList', 'profileHeader', 'postAuthor', 'postCaption', 'postMetadata'].forEach(id => $('#' + id).replaceChildren());
    ['igPassword', 'igCode', 'cookieText', 'loginPassword'].forEach(id => { $('#' + id).value = ''; });
    state.pendingCode = false; $('#twoFactorBox').hidden = true;
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
    // Files still in the buffer with the NVMe back mean a transfer is running or a copy conflicted.
    const left = storage.ready && storage.buffer_files ? ` · ${number(storage.buffer_files)} file ancora nel buffer interno` : '';
    const note = storage.ready ? `Limite ${bytes(storage.quota_bytes)} · ${bytes(storage.free_bytes)} liberi sul disco${left}`
      : `NVMe scollegato · buffer ${bytes(storage.buffer_bytes)} di ${bytes(storage.buffer_limit_bytes)}`;
    $$('[data-storage-used]').forEach(element => { element.textContent = used; });
    $$('[data-storage-sentence]').forEach(element => { element.textContent = `${used} usati`; });
    $$('[data-storage-note]').forEach(element => { element.textContent = note; });
    $$('[data-storage-meter]').forEach(element => { element.value = storage.quota_bytes ? Math.min(100, (storage.used_bytes / storage.quota_bytes) * 100) : 0; });
  }
  function renderNotice(summary) {
    const {profiles = [], storage = {}, instagram = {}, worker = {}} = summary; const notice = $('#notice');
    const failing = profiles.find(p => p.enabled && p.error && !p.access);
    if (storage.buffer_full) notice.innerHTML = 'NVMe scollegato e buffer interno pieno: i download riprendono quando ricolleghi l’NVMe.';
    else if (!storage.ready) notice.innerHTML = `NVMe scollegato: i nuovi download vanno nel buffer interno (${bytes(storage.buffer_bytes)} di ${bytes(storage.buffer_limit_bytes)}). Quando ricolleghi l’NVMe passano sul disco e torna visibile tutto l’archivio.`;
    else if (worker.draining) notice.innerHTML = 'Sposto sull’NVMe i download salvati nel buffer interno.';
    else if (!instagram.configured && profiles.length) notice.innerHTML = 'Instagram chiede l’accesso per scaricare quasi tutti i profili. <a href="#settings" data-view="settings">Collega l’account</a>';
    else if (failing) notice.innerHTML = `<b>@${esc(failing.username)}</b>: ${esc(failing.error)} <button class="link" type="button" data-profile="${failing.id}">Apri il profilo</button>`;
    else { notice.hidden = true; return; }
    notice.hidden = false;
  }
  function renderSummary() {
    const summary = state.summary; if (!summary) return;
    const {profiles = [], jobs = [], storage = {}, instagram = {}, settings = {}, counts = {}, worker = {}} = summary;
    $('#navCount').textContent = counts.posts ? number(counts.posts) : '';
    $('#activityDot').hidden = !jobs.length;
    renderStorage(storage);
    const running = jobs.some(j => j.state === 'running');
    if (storage.buffer_full) setSyncStatus('Buffer pieno', 'off');
    else if (!storage.ready) setSyncStatus(running ? 'Download nel buffer' : 'NVMe scollegato', running ? 'live' : 'wait');
    else if (worker.draining) setSyncStatus('Trasferimento su NVMe', 'live');
    else if (running) setSyncStatus('Download in corso', 'live');
    else if (jobs.length) setSyncStatus(`${number(jobs.length)} in coda`, 'wait');
    else if (!settings.automatic) setSyncStatus('Automatico in pausa', 'off');
    else setSyncStatus('Aggiornato', 'ok');
    renderNotice(summary);
    $('#instagramState').textContent = instagram.configured ? `Collegato come @${instagram.username}` : 'Non collegato';
    // Once connected the sign-in form steps aside; it comes back only to switch account.
    const connected = instagram.configured && !state.igChanging && !state.pendingCode && !loginAttemptId;
    $('#igConnected').hidden = !connected; $('#igConnect').hidden = connected;
    $('#keepInstagram').hidden = !(instagram.configured && state.igChanging);
    if (!$('#settingsForm').contains(document.activeElement)) { $('#automatic').checked = !!settings.automatic; $('#quotaGb').value = settings.quota_gb ?? 60; $('#reserveGb').value = settings.min_free_gb ?? 15; }
    renderArchive(); renderProfiles();
  }
  async function refreshSummary(initial = false) {
    if (refreshBusy || !state.authenticated) return;
    refreshBusy = true;
    try {
      const summary = await api('/api/summary');
      if (!state.authenticated) return;
      const count = Number(summary.counts?.posts || 0);
      // The grid depends on the disk too: offline it lists only what the buffer holds.
      const mode = summary.storage?.mode;
      const changed = (state.lastCount !== null && state.lastCount !== count) || (state.lastMode !== undefined && state.lastMode !== mode);
      state.summary = summary; state.lastCount = count; state.lastMode = mode; renderSummary();
      if (state.view === 'activity') await loadJobs();
      if (!initial && changed && ['archive', 'favorites'].includes(state.view)) await loadPosts(true);
    } finally { refreshBusy = false; }
  }

  function profileLine(p) {
    if (p.access === 'requested') return 'Privato · richiesta di seguire in attesa';
    if (p.access === 'not_followed') return 'Privato · da seguire per archiviarlo';
    if (p.status === 'syncing') return 'Download in corso';
    if (!p.enabled) return 'In pausa';
    return `${plural(p.post_count, 'contenuto', 'contenuti')} · aggiornato ${moment(p.last_sync)}`;
  }
  function renderCards(profiles) {
    $('#profileCards').innerHTML = profiles.map(p => `<button class="profile-card" type="button" data-profile="${p.id}" aria-label="Apri @${esc(p.username)}">
      <span class="card-cover">${p.cover ? `<img src="${BASE}/media/${Number(p.cover)}/thumb" alt="" loading="lazy" decoding="async">` : p.access ? icon('lock') : ''}</span>
      <span class="card-body">${avatar(p, 'medium')}<span class="card-text"><strong>@${esc(p.username)}</strong><span class="${p.error && !p.access ? 'card-error' : ''}">${esc(p.error && !p.access ? 'Da verificare' : profileLine(p))}</span></span></span>
    </button>`).join('') + (profiles.length ? '' : `<button class="profile-card add" type="button" data-action="add-profile"><span class="card-cover">${icon('plus')}</span><span class="card-body"><span class="card-text"><strong>Aggiungi un profilo</strong><span>Username o link Instagram</span></span></span></button>`);
    bindImageFallback($('#profileCards'));
  }
  function renderArchive() {
    const summary = state.summary || {}; const profiles = summary.profiles || []; const counts = summary.counts || {};
    const profile = state.view === 'archive' ? profileById(state.profile) : null;
    const favorites = state.view === 'favorites'; const landing = state.view === 'archive' && !profile;
    // A private profile with nothing archived yet shows only its note, not a row of zeros.
    const locked = profile && profile.access && !Number(profile.post_count);
    $('#archiveHeader').hidden = !!profile; $('#profileCards').hidden = !landing; $('#profilePage').hidden = !profile; $('#browse').hidden = landing || locked;
    $('#kindTabs').hidden = favorites; $('#collections').hidden = !profile || state.kind !== 'highlight';
    $('#syncAll').hidden = favorites || !profiles.length;
    $('#archiveTitle').textContent = favorites ? 'Salvati' : 'Archivio';
    $('#archiveMeta').textContent = favorites ? 'I contenuti che hai segnato col cuore, da tutti i profili' : profiles.length ? `${plural(counts.posts, 'contenuto', 'contenuti')} da ${plural(profiles.length, 'profilo', 'profili')}` : 'Aggiungi un profilo Instagram: InstaVault ne conserva post, Reel e storie.';
    if (landing) renderCards(profiles);
    if (!profile) { $('#profileHeader').replaceChildren(); $('#accessNote').hidden = true; return; }
    $('#profileHeader').innerHTML = `${avatar(profile, 'large')}<div class="profile-body">
      <button class="link back" type="button" data-clear-profile>${icon('left')}Tutti i profili</button>
      <div class="profile-top"><h1>${esc(profile.username)}</h1><div class="page-actions"><button class="button" type="button" data-sync-profile="${profile.id}">${icon('refresh')}Aggiorna</button><button class="button" type="button" data-edit-profile="${profile.id}">Modifica</button></div></div>
      <p class="profile-counts"><span><b>${number(profile.count_post)}</b> post</span><span><b>${number(profile.count_reel)}</b> Reel</span><span><b>${number(profile.count_story)}</b> storie</span><span><b>${number(profile.count_highlight)}</b> in evidenza</span></p>
      ${profile.full_name ? `<p class="profile-name">${esc(profile.full_name)}</p>` : ''}
      ${profile.biography ? `<p class="profile-bio">${esc(profile.biography)}</p>` : ''}
      ${profile.error && !profile.access ? `<p class="profile-error">${esc(profile.error)}</p>` : ''}
      <p class="stamp">${esc(profile.status === 'syncing' ? 'download in corso' : 'aggiornato ' + moment(profile.last_sync))} · ${esc(every(profile.interval_minutes))}</p>
    </div>`;
    bindImageFallback($('#profileHeader'));
    const note = $('#accessNote');
    note.hidden = !profile.access;
    if (profile.access === 'requested') note.innerHTML = `${icon('lock')}<p><b>Profilo privato.</b> La richiesta di seguirlo è in attesa. InstaVault controlla ${esc(every(profile.interval_minutes))} e scarica tutto appena viene accettata.</p>`;
    if (profile.access === 'not_followed') note.innerHTML = `${icon('lock')}<p><b>Profilo privato.</b> L’account Instagram collegato non lo segue, quindi Instagram non mostra i suoi contenuti.</p><button class="button primary small" type="button" data-follow="${profile.id}">Chiedi di seguire</button>`;
    $$('#kindTabs [data-kind]').forEach(button => { button.querySelector('.count').textContent = number(profile['count_' + button.dataset.kind]); });
  }
  async function loadCollections() {
    const box = $('#collections'); const id = state.profile;
    const result = await api(`/api/profiles/${Number(id)}/collections`);
    if (id !== state.profile || state.kind !== 'highlight') return;
    const name = value => value.replace(/^In evidenza · /, '') || 'Senza titolo';
    box.innerHTML = result.items.length ? `<button class="collection${state.collection ? '' : ' selected'}" type="button" data-collection="" aria-pressed="${!state.collection}"><span class="avatar ring">${icon('grid')}</span><span>Tutte</span></button>`
      + result.items.map(c => `<button class="collection${state.collection === c.name ? ' selected' : ''}" type="button" data-collection="${esc(c.name)}" aria-pressed="${state.collection === c.name}" aria-label="${esc(name(c.name))}, ${plural(c.count, 'contenuto', 'contenuti')}"><span class="avatar ring">${c.cover ? `<img src="${BASE}/media/${Number(c.cover)}/thumb" alt="" loading="lazy">` : ''}</span><span>${esc(name(c.name))}</span></button>`).join('') : '';
    bindImageFallback(box);
  }
  function renderProfiles() {
    const profiles = state.summary?.profiles || [];
    $('#profilesList').innerHTML = profiles.length ? profiles.map(p => `<article class="row">
      ${avatar(p, 'medium')}
      <div class="row-main">
        <h2><button class="link name" type="button" data-profile="${p.id}">@${esc(p.username)}</button></h2>
        ${p.full_name ? `<p>${esc(p.full_name)}</p>` : ''}
        ${p.error ? `<p class="row-error">${esc(p.error)}</p>` : ''}
        <p class="stamp">${number(p.post_count)} contenuti · ${every(p.interval_minutes)} · aggiornato ${esc(moment(p.last_sync))}</p>
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
    if (state.summary) renderSummary(); else renderArchive();
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
    // Open on the first kind that has something to show, like Instagram opens on the grid.
    state.kind = ['post', 'reel', 'story', 'highlight'].find(kind => Number(profile?.['count_' + kind])) || 'post';
    state.collection = ''; pressKind();
    if (state.kind === 'highlight') loadCollections().catch(report);
    if (location.hash !== '#archive') history.pushState(null, '', '#archive');
    renderSummary(); loadPosts(true).catch(report); window.scrollTo({top: 0, behavior: 'instant'});
  }
  function pressKind() { $$('#kindTabs [data-kind]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.kind === state.kind))); }
  function clearProfile() { state.profile = ''; document.title = 'Archivio · InstaVault'; renderSummary(); loadPosts(true).catch(report); }

  function postTile(post) {
    const media = post.media?.[0]; if (!media) return '';
    const kind = post.kind === 'reel' || media.kind === 'video' ? 'reel' : post.media.length > 1 ? 'stack' : post.collection ? 'ring' : post.kind === 'story' ? 'story' : '';
    const label = post.kind === 'reel' ? 'Reel' : post.collection ? 'In evidenza' : post.kind === 'story' ? 'Storia' : post.media.length > 1 ? 'Carosello' : 'Post';
    const owner = state.profile ? '' : `@${esc(post.username)} · `;
    const width = media.width || 480; const height = media.height || 600;
    const here = profileById(state.profile)?.username || post.username;
    const shared = (post.profiles || []).filter(name => name !== here);
    return `<button class="tile" type="button" data-post="${post.id}" aria-label="${esc(label)} di @${esc(post.username)}, ${esc(day(post.published_at))}">
      <img src="${BASE}/media/${media.id}/thumb" alt="" width="${width}" height="${height}" loading="lazy" decoding="async">
      ${kind ? `<span class="tile-kind">${icon(kind)}</span>` : ''}
      ${post.favorite ? `<span class="tile-saved">${icon('heart')}</span>` : ''}
      ${shared.length ? `<span class="tile-shared" title="Condiviso con ${esc(shared.map(name => '@' + name).join(', '))}">${icon('shared')}</span>` : ''}
      <span class="tile-hover">${owner}${esc(day(post.published_at))}</span>
    </button>`;
  }
  function showEmpty(title, text, action = null) {
    $('#emptyState').hidden = false; $('#emptyTitle').textContent = title; $('#emptyText').textContent = text;
    const button = $('#emptyAction'); button.hidden = !action;
    if (action) { button.textContent = action.label; button.dataset.action = action.action || ''; button.dataset.view = action.view || ''; button.dataset.editProfile = action.edit || ''; }
  }
  function renderEmpty() {
    if (state.posts.length) { $('#emptyState').hidden = true; return; }
    const summary = state.summary || {}; const profiles = summary.profiles || [];
    if (summary.storage && !summary.storage.ready && profiles.length) return showEmpty('Archivio sull’NVMe scollegato', 'Qui compaiono solo i contenuti scaricati nel buffer interno. Il resto torna visibile quando ricolleghi l’NVMe.');
    if (state.view === 'favorites') return showEmpty('Nessun contenuto salvato', 'Apri un contenuto e tocca il cuore per ritrovarlo qui.');
    if (state.query) return showEmpty('Nessun risultato', 'Nessun contenuto corrisponde a questa ricerca.');
    const profile = profileById(state.profile);
    if (profile) {
      const [, many] = KINDS[state.kind] || KINDS.post;
      const setting = {post: 'posts', reel: 'reels', story: 'stories', highlight: 'highlights'}[state.kind];
      if (profile.access) return showEmpty('Ancora niente da mostrare', 'Il profilo è privato: i contenuti arrivano quando l’account collegato potrà vederli.');
      if (!profile[setting]) return showEmpty(`Nessun ${many === 'post' ? 'post' : many} archiviato`, `Questo profilo non archivia ${many}. Puoi attivarli da Modifica.`, {label: 'Modifica', edit: profile.id});
      if (state.kind === 'story') return showEmpty('Nessuna storia archiviata', 'Le storie restano su Instagram 24 ore: InstaVault salva quelle pubblicate da quando segue il profilo.');
      return showEmpty(`Nessun ${many === 'post' ? 'post' : many} archiviato`, profile.status === 'syncing' ? 'Il download è in corso: i contenuti compaiono qui appena arrivano.' : 'Compaiono qui al prossimo aggiornamento.');
    }
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
    if (state.profile) params.set('kind', state.kind);
    if (state.profile && state.kind === 'highlight' && state.collection) params.set('collection', state.collection);
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
    const select = $('#pInterval'); const value = profile?.interval_minutes || 10;
    $$('option[data-custom]', select).forEach(option => option.remove());
    if (![...select.options].some(option => Number(option.value) === Number(value))) { const option = new Option(every(value, true), value); option.dataset.custom = '1'; select.add(option); }
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
    // A collab or repost shows once and names every archived profile that has it.
    const others = (result.profiles || []).filter(name => name !== result.username);
    $('#postShared').hidden = !others.length;
    $('#postShared').innerHTML = others.length ? `${icon('shared')}<span>Condiviso con ${others.map(name => {
      const profile = (state.summary?.profiles || []).find(item => item.username === name);
      return profile ? `<button class="link" type="button" data-profile="${Number(profile.id)}">@${esc(name)}</button>` : `@${esc(name)}`;
    }).join(', ')}</span>` : '';
    const links = (result.links || []).filter(link => /^https?:\/\//.test(link.url));
    $('#postLinks').hidden = !links.length;
    $('#postLinks').innerHTML = links.map(link => `<li><a href="${esc(link.url)}" target="_blank" rel="noopener noreferrer">${icon('link')}<span>${esc(link.label || link.url)}</span></a></li>`).join('');
    // A plain story's Instagram address dies after a day; highlights keep theirs.
    const external = String(result.permalink || '');
    const expired = result.kind === 'story' && !result.collection && Date.now() / 1000 - result.published_at > 86400;
    $('#openInstagram').hidden = expired;
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
    placeStoryLinks(item);
    const many = post.media.length > 1;
    $('#previousMedia').hidden = !many || state.slide === 0; $('#nextMedia').hidden = !many || state.slide === post.media.length - 1;
    $('#carouselCount').hidden = !many; $('#carouselCount').textContent = `${state.slide + 1} / ${post.media.length}`;
    $('#downloadOriginal').href = `${BASE}/media/${media.id}?download=1`;
  }
  // Tappable areas over the story, where Instagram drew the sticker or mention.
  function placeStoryLinks(item) {
    const layer = $('#storyLinks'); const links = (state.post?.links || []).filter(link => link.w > 0 && link.h > 0 && /^https?:\/\//.test(link.url));
    layer.hidden = true; layer.replaceChildren();
    if (!links.length) return;
    const place = () => {
      const natural = [item.naturalWidth || item.videoWidth, item.naturalHeight || item.videoHeight];
      const stage = $('#viewerMedia').getBoundingClientRect();
      if (!natural[0] || !natural[1] || !stage.width) return;
      const scale = Math.min(stage.width / natural[0], stage.height / natural[1]);
      const width = natural[0] * scale, height = natural[1] * scale;
      Object.assign(layer.style, {left: `${(stage.width - width) / 2}px`, top: `${(stage.height - height) / 2}px`, width: `${width}px`, height: `${height}px`});
      layer.innerHTML = links.map(link => `<a href="${esc(link.url)}" target="_blank" rel="noopener noreferrer" aria-label="${esc(link.label || link.url)}"
        style="left:${(link.x - link.w / 2) * 100}%;top:${(link.y - link.h / 2) * 100}%;width:${link.w * 100}%;height:${link.h * 100}%"></a>`).join('');
      layer.hidden = false;
    };
    if (item.complete || item.readyState >= 1) place();
    item.addEventListener(item.tagName === 'VIDEO' ? 'loadedmetadata' : 'load', place, {once: true});
    storyLinkResize = place;
  }
  let storyLinkResize = null;
  window.addEventListener('resize', () => { if ($('#postDialog').open && storyLinkResize) storyLinkResize(); });
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
    if (data.follow) { busy(target, async () => { try { const result = await api(`/api/profiles/${Number(data.follow)}/follow`, {method: 'POST', body: '{}'}); toast(result.access ? 'Richiesta inviata: InstaVault scarica appena viene accettata' : 'Ora lo segui: download in coda'); await refreshSummary(); } catch (error) { report(error); } }); return; }
    if (Object.hasOwn(data, 'collection')) { state.collection = data.collection; loadCollections().catch(report); loadPosts(true).catch(report); return; }
    if (Object.hasOwn(data, 'kind')) {
      state.kind = data.kind; state.collection = ''; pressKind(); renderArchive();
      if (state.kind === 'highlight') loadCollections().catch(report);
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
    const body = {username: $('#profileUsername').value.trim(), interval_minutes: Number($('#pInterval').value), posts: $('#pPosts').checked, reels: $('#pReels').checked, stories: $('#pStories').checked, highlights: $('#pHighlights').checked};
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
  const codeSentTo = {
    email: 'Instagram ha mandato un codice alla tua email. Scrivilo qui sotto entro 5 minuti.',
    sms: 'Instagram ha mandato un codice via SMS. Scrivilo qui sotto entro 5 minuti.',
    two_factor: 'Scrivi il codice dell’autenticazione a due fattori.',
  };
  function resetInstagramLogin() {
    state.pendingCode = false; $('#twoFactorBox').hidden = true;
    $('#loginProgress').hidden = true; $('#igCode').value = '';
  }
  async function awaitInstagramLogin(result) {
    const generation = ++loginPollGeneration;
    loginAttemptId = result.attempt_id || loginAttemptId;
    while (result.state === 'running') {
      $('#loginProgress').hidden = false;
      $('#loginProgressText').textContent = 'Connessione a Instagram in corso…';
      await new Promise(resolve => setTimeout(resolve, 1500));
      if (generation !== loginPollGeneration || !state.authenticated) return {state: 'cancelled'};
      result = await api('/api/instagram/login/status?attempt_id=' + encodeURIComponent(loginAttemptId));
    }
    $('#loginProgress').hidden = true;
    if (result.state === 'error') throw new Error(result.error);
    return result;
  }
  async function cancelInstagramLogin() {
    ++loginPollGeneration;
    const id = loginAttemptId; loginAttemptId = null;
    resetInstagramLogin(); $('#igError').textContent = '';
    if (id) { try { const result = await api('/api/instagram/login/cancel', {method: 'POST', body: JSON.stringify({attempt_id: id})}); if (result.already_connected) { toast('L’account è già stato collegato'); await refreshSummary(); } } catch (error) { report(error); } }
    $('#igPassword').focus();
  }
  $('#instagramLogin').addEventListener('submit', event => {
    event.preventDefault(); $('#igError').textContent = '';
    const body = state.pendingCode ? {code: $('#igCode').value.trim(), attempt_id: loginAttemptId} : {username: $('#igUsername').value.trim(), password: $('#igPassword').value};
    $('#igPassword').value = ''; $('#igCode').value = '';
    busy(event.submitter, async () => {
      try {
        let result = await api('/api/instagram/login', {method: 'POST', body: JSON.stringify(body)});
        result = await awaitInstagramLogin(result);
        if (result.state === 'cancelled') return;
        if (result.code_required) {
          state.pendingCode = true; $('#twoFactorBox').hidden = false;
          $('#igError').textContent = codeSentTo[result.channel] || codeSentTo.email; $('#igCode').focus();
        } else if (result.configured) {
          loginAttemptId = null; state.igChanging = false; resetInstagramLogin(); toast('Account Instagram collegato'); await refreshSummary();
        }
      } catch (error) {
        resetInstagramLogin(); $('#igError').textContent = error.message;
      } finally { body.password = ''; body.code = ''; }
    });
  });
  $('#cancelInstagramLogin').addEventListener('click', () => cancelInstagramLogin());
  $('#changeInstagram').addEventListener('click', () => { state.igChanging = true; renderSummary(); $('#igUsername').focus(); });
  $('#keepInstagram').addEventListener('click', () => { state.igChanging = false; $('#igError').textContent = ''; renderSummary(); });
  $('#resetTwoFactor').addEventListener('click', () => cancelInstagramLogin());
  $('#cookieImport').addEventListener('submit', event => {
    event.preventDefault();
    const body = {cookies: $('#cookieText').value}; $('#cookieText').value = '';
    busy(event.submitter, async () => { try { await api('/api/instagram/cookies', {method: 'POST', body: JSON.stringify(body)}); toast('Account collegato, sessione cifrata sul server'); $('#cookieImport').closest('details').open = false; state.igChanging = false; await refreshSummary(); } catch (error) { report(error); } finally { body.cookies = ''; } });
  });
  $('#disconnectInstagram').addEventListener('click', event => busy(event.currentTarget, async () => {
    try { await api('/api/instagram/disconnect', {method: 'POST', body: '{}'}); state.pendingCode = false; $('#twoFactorBox').hidden = true; ['igUsername', 'igPassword', 'igCode'].forEach(id => { $('#' + id).value = ''; }); toast('Account scollegato, sessione rimossa dal server'); await refreshSummary(); } catch (error) { report(error); }
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
