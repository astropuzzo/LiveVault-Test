/* LiveVault 3.2 features: live forecasts, notifications, saved archive filters. */
(() => {
  'use strict';

  const icon = name => `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
  const storage = {
    get(key, fallback) { try { const value = localStorage.getItem(key); return value === null ? fallback : JSON.parse(value); } catch (_error) { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_error) { /* private mode */ } },
  };

  /* ---------- Upcoming live forecasts ---------- */
  let predictionData = null;
  let predictionLoadedAt = 0;

  function whenLabel(iso) {
    const time = timestamp(iso);
    if (!time) return '—';
    const minutes = Math.round((time - Date.now()) / 60000);
    const clock = new Intl.DateTimeFormat('it-IT', {timeZone: DISPLAY_TIME_ZONE, weekday: minutes > 20 * 60 ? 'short' : undefined, hour: '2-digit', minute: '2-digit'}).format(new Date(time));
    if (minutes <= 60) return `entro un'ora · ${clock}`;
    if (minutes < 24 * 60) return `tra ${Math.round(minutes / 60)} h · ${clock}`;
    return clock;
  }

  function renderForecast() {
    const root = $('#forecastPanel');
    if (!root) return;
    const upcoming = predictionData?.upcoming || [];
    if (!upcoming.length) {
      root.classList.add('hidden');
      return;
    }
    root.classList.remove('hidden');
    root.innerHTML = `<header class="surface-header section-head"><div><h2>Prossime live previste</h2><span class="count">${upcoming.length}</span></div><small>Modello orario con dati recenti più pesanti · aggiornato ${esc(ago(predictionData.generated_at))}</small></header>
      <div class="forecast-list">${upcoming.map(row => {
        const pct = Math.round(Number(row.next_start_probability) * 100);
        return `<article class="forecast-row" data-confidence="${esc(row.confidence)}">
          <div class="forecast-who">${creatorLinkMarkup(row.representative_source_id, row.display_name)}<small>${esc(whenLabel(row.next_start_at))}${row.typical_minutes ? ` · di solito ${esc(duration(row.typical_minutes * 60))}` : ''}</small></div>
          <div class="forecast-meter" role="img" aria-label="Probabilità ${pct}%"><i data-dynamic-width="${pct}"></i></div>
          <strong>${pct}%</strong><span class="forecast-confidence">${esc(row.confidence)}</span>
        </article>`;
      }).join('')}</div>`;
    applyDynamicStyles(root);
  }

  async function loadPredictions(force = false) {
    if (!force && predictionData && Date.now() - predictionLoadedAt < 5 * 60000) return renderForecast();
    try {
      predictionData = await api('/api/predictions');
      predictionLoadedAt = Date.now();
      renderForecast();
    } catch (_error) { /* forecasts are optional */ }
  }

  /* ---------- Notifications ---------- */
  const NOTIFY_KEY = 'livevault-notifications';
  let notifyEnabled = storage.get(NOTIFY_KEY, false);
  let previous = null;

  function snapshotState() {
    const byId = {};
    for (const source of sources || []) byId[source.id] = {status: source.last_status, name: source.display_name || source.name, blocked: !!source.recording_blocked_by_pause};
    return {
      sources: byId,
      failed: Number(statusData?.queue?.failed || 0) + Number(statusData?.queue?.integrity_failed || 0),
      disk: statusData?.disk?.pressure || 'ok',
      stale: $('#connectionState')?.dataset.state === 'stale',
    };
  }

  function announce(title, body, tone = 'good') {
    toast(`${title}${body ? ` · ${body}` : ''}`, tone);
    if (!notifyEnabled || !('Notification' in window) || Notification.permission !== 'granted') return;
    const options = {body, icon: '/static/icon.svg', badge: '/static/icon.svg', tag: `${title}-${body}`};
    navigator.serviceWorker?.getRegistration().then(reg => reg ? reg.showNotification(title, options) : new Notification(title, options))
      .catch(() => { try { new Notification(title, options); } catch (_error) { /* unsupported */ } });
  }

  function diffAndNotify() {
    const current = snapshotState();
    if (previous) {
      for (const [id, now] of Object.entries(current.sources)) {
        const before = previous.sources[id];
        if (!before || before.status === now.status) continue;
        const wasLive = ['live', 'recording', 'private', 'tipjar'].includes(before.status);
        if (now.status === 'recording' && before.status !== 'recording') announce('REC avviata', now.name);
        else if (now.status === 'live' && !wasLive) announce(now.blocked ? 'Live non registrata' : 'Live iniziata', now.name, now.blocked ? 'bad' : 'good');
        else if (wasLive && ['offline', 'was_live', 'post_live'].includes(now.status)) announce('Live terminata', now.name);
      }
      if (current.failed > previous.failed) announce('Upload o integrità falliti', `${current.failed - previous.failed} nuovi`, 'bad');
      if (current.disk !== previous.disk && ['warning', 'critical'].includes(current.disk)) announce(current.disk === 'critical' ? 'Disco critico' : 'Spazio disco basso', '', 'bad');
      if (current.stale && !previous.stale) announce('Connessione al server persa', 'nuovo tentativo automatico', 'bad');
    }
    previous = current;
  }
  window.notifyStateChange = () => {};

  function renderNotifyButton() {
    const button = $('#notifyBtn');
    if (!button) return;
    const on = notifyEnabled && 'Notification' in window && Notification.permission === 'granted';
    button.classList.toggle('active', on);
    button.setAttribute('aria-pressed', String(on));
    button.setAttribute('aria-label', on ? 'Notifiche attive' : 'Attiva notifiche');
    button.dataset.tooltip = on ? 'Notifiche attive' : 'Attiva notifiche';
    button.innerHTML = icon(on ? 'bell' : 'bell-off');
  }

  async function toggleNotifications() {
    if (!('Notification' in window)) return toast('Questo browser non supporta le notifiche', 'bad');
    if (notifyEnabled && Notification.permission === 'granted') {
      notifyEnabled = false;
    } else {
      const permission = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission();
      notifyEnabled = permission === 'granted';
      if (!notifyEnabled) toast('Permesso notifiche negato dal browser', 'bad');
    }
    storage.set(NOTIFY_KEY, notifyEnabled);
    renderNotifyButton();
    if (notifyEnabled) toast('Notifiche attive: live, REC, errori e disco');
  }

  /* ---------- Saved archive filters ---------- */
  const FILTERS_KEY = 'livevault-archive-filters';
  let savedFilters = storage.get(FILTERS_KEY, []);

  function currentFilter() {
    return {query: $('#recordingSearch')?.value || '', status: $('#recordingStatus')?.value || 'all'};
  }

  function renderSavedFilters() {
    const host = $('.archive-hooks');
    if (!host) return;
    let bar = $('#savedFilters');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'savedFilters';
      bar.className = 'saved-filters';
      host.append(bar);
    }
    const active = currentFilter();
    bar.innerHTML = savedFilters.map((filter, index) => {
      const on = filter.query === active.query && filter.status === active.status;
      return `<span class="saved-filter ${on ? 'active' : ''}"><button type="button" data-filter-apply="${index}">${esc(filter.name)}</button><button type="button" class="saved-filter-remove" data-filter-remove="${index}" aria-label="Rimuovi filtro ${esc(filter.name)}">×</button></span>`;
    }).join('') + `<button class="button secondary compact" type="button" data-filter-save>${icon('plus')}<span>Salva filtro</span></button>`;
  }

  async function saveCurrentFilter() {
    const filter = currentFilter();
    if (!filter.query.trim() && filter.status === 'all') return toast('Imposta prima una ricerca o uno stato', 'bad');
    const name = await uiPrompt('Nome del filtro', filter.query.trim() || filter.status, {title: 'Salva filtro archivio'});
    if (!name) return;
    savedFilters = [...savedFilters.filter(item => item.name !== name), {name: name.slice(0, 40), ...filter}].slice(-12);
    storage.set(FILTERS_KEY, savedFilters);
    renderSavedFilters();
  }

  function bindFilterBar() {
    document.addEventListener('click', event => {
      const apply = event.target.closest('[data-filter-apply]');
      const remove = event.target.closest('[data-filter-remove]');
      if (event.target.closest('[data-filter-save]')) return void saveCurrentFilter();
      if (apply) {
        const filter = savedFilters[Number(apply.dataset.filterApply)];
        if (!filter) return;
        $('#recordingSearch').value = filter.query;
        $('#recordingStatus').value = filter.status;
        renderRecordings();
        renderSavedFilters();
      }
      if (remove) {
        savedFilters.splice(Number(remove.dataset.filterRemove), 1);
        storage.set(FILTERS_KEY, savedFilters);
        renderSavedFilters();
      }
    });
    $('#recordingSearch')?.addEventListener('input', () => renderSavedFilters());
    $('#recordingStatus')?.addEventListener('change', () => renderSavedFilters());
  }

  /* ---------- Wiring ---------- */
  const baseRefreshFeatures = refresh;
  refresh = async function refreshWithFeatures(options = {}) {
    const result = await baseRefreshFeatures(options);
    diffAndNotify();
    if (activeView === 'dashboard') loadPredictions();
    return result;
  };

  const baseShowViewFeatures = showView;
  showView = function showViewWithFeatures(name, updateHash = true) {
    const result = baseShowViewFeatures(name, updateHash);
    if (activeView === 'dashboard') loadPredictions();
    if (activeView === 'archive') renderSavedFilters();
    return result;
  };

  $('#notifyBtn')?.addEventListener('click', toggleNotifications);
  bindFilterBar();
  renderNotifyButton();
})();
