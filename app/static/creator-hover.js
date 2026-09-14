(() => {
  'use strict';

  const HOVER_DELAY_MS = 250;
  const FOCUS_DELAY_MS = 120;
  const CACHE_TTL_MS = 90_000;
  const fineHover = window.matchMedia?.('(hover: hover) and (pointer: fine)')?.matches ?? true;
  const cache = new Map();
  const pending = new Map();
  let card = null;
  let activeLink = null;
  let openTimer = 0;
  let requestToken = 0;

  function statusText(value) {
    return ({
      recording: 'REC', live: 'LIVE', private: 'PRIVATA', tipjar: 'TIP JAR', restricted: 'LIMITATA',
      error: 'ERRORE', paused: 'PAUSA', archived: 'ARCHIVIATA', offline: 'OFFLINE', unknown: '—'
    })[String(value || '').toLowerCase()] || String(value || '—').toUpperCase();
  }

  function ensureCard() {
    if (card) return card;
    card = document.createElement('aside');
    card.className = 'creator-hover-card';
    card.setAttribute('role', 'tooltip');
    card.setAttribute('aria-hidden', 'true');
    document.body.appendChild(card);
    return card;
  }

  function hideCreatorHover() {
    window.clearTimeout(openTimer);
    openTimer = 0;
    activeLink = null;
    requestToken += 1;
    if (!card) return;
    card.classList.remove('visible');
    card.setAttribute('aria-hidden', 'true');
  }

  function loadingMarkup(name) {
    return `<div class="creator-hover-loading"><div class="creator-hover-loading-head"><strong>${esc(name || 'Creator')}</strong><span>Caricamento…</span></div><div class="creator-hover-loading-grid"><i></i><i></i><i></i></div></div>`;
  }

  function errorMarkup(name) {
    return `<div class="creator-hover-empty"><strong>${esc(name || 'Creator')}</strong><small>Anteprima non disponibile.</small></div>`;
  }

  function recordingMarkup(recording, fallbackImage = '') {
    const image = safeUrl(recording.thumbnail_url || fallbackImage || '');
    const when = recording.finalized_at || recording.started_at;
    const meta = [recording.provider_label || '', dateText(when), duration(recording.duration_seconds || 0)].filter(Boolean).join(' · ');
    return `<article class="creator-hover-video"><div class="creator-hover-video-art ${image ? '' : 'empty'}">${image ? `<img src="${esc(image)}" alt="" loading="eager">` : '<span>LV</span>'}</div><div><strong>${esc(recording.source_name || recording.filename || 'Registrazione')}</strong><small>${esc(meta)}</small></div></article>`;
  }

  function previewMarkup(data) {
    const accounts = (data.accounts || []).map(item => {
      const slug = String(item.slug || '').startsWith('http') ? item.name : `@${item.slug || item.name || ''}`;
      return `${item.provider_label || item.platform || ''} ${slug}`.trim();
    }).filter(Boolean);
    const liveMeta = data.last_seen_live_at ? `ultima live ${ago(data.last_seen_live_at)}` : 'nessuna live recente';
    const recMeta = data.last_recording_at ? `ultima REC ${ago(data.last_recording_at)}` : 'nessuna REC';
    const videos = (data.recent_recordings || []).slice(0, 3);
    const fallback = safeUrl(data.cover_thumbnail_url || '');
    return `<div class="creator-hover-head"><div><strong>${esc(data.display_name || 'Creator')}</strong><small>${esc(accounts.join(' · ') || 'Account non disponibile')}</small></div><span class="creator-hover-status ${esc(String(data.status || 'offline'))}">${esc(statusText(data.status))}</span></div><div class="creator-hover-meta"><span>${esc(liveMeta)}</span><span>${esc(recMeta)}</span></div>${videos.length ? `<div class="creator-hover-videos">${videos.map((recording, index) => recordingMarkup(recording, index === 0 ? fallback : '')).join('')}</div>` : `<div class="creator-hover-empty compact"><small>Nessuna registrazione disponibile per l’anteprima.</small></div>`}`;
  }

  function positionCard(link) {
    const hover = ensureCard();
    const rect = link.getBoundingClientRect();
    const gap = 9;
    const edge = 10;
    hover.style.left = `${edge}px`;
    hover.style.top = `${edge}px`;
    const box = hover.getBoundingClientRect();
    let left = rect.left + Math.min(rect.width * 0.25, 28);
    left = Math.max(edge, Math.min(left, window.innerWidth - box.width - edge));
    let top = rect.bottom + gap;
    if (top + box.height > window.innerHeight - edge) top = rect.top - box.height - gap;
    top = Math.max(edge, Math.min(top, window.innerHeight - box.height - edge));
    hover.style.left = `${Math.round(left)}px`;
    hover.style.top = `${Math.round(top)}px`;
  }

  async function loadPreview(sourceId) {
    const now = Date.now();
    const cached = cache.get(sourceId);
    if (cached && now - cached.at < CACHE_TTL_MS) return cached.data;
    if (pending.has(sourceId)) return pending.get(sourceId);
    const promise = api(`/api/sources/${sourceId}/hover-preview`)
      .then(data => {
        cache.set(sourceId, {at: Date.now(), data});
        return data;
      })
      .finally(() => pending.delete(sourceId));
    pending.set(sourceId, promise);
    return promise;
  }

  async function openCreatorHover(link) {
    if (!link?.isConnected) return;
    const sourceId = Number(link.dataset.profileLink);
    if (!sourceId) return;
    activeLink = link;
    const token = ++requestToken;
    const hover = ensureCard();
    hover.innerHTML = loadingMarkup(link.textContent?.trim() || 'Creator');
    hover.classList.add('visible');
    hover.setAttribute('aria-hidden', 'false');
    positionCard(link);
    try {
      const data = await loadPreview(sourceId);
      if (token !== requestToken || activeLink !== link || !link.isConnected) return;
      hover.innerHTML = previewMarkup(data);
      positionCard(link);
    } catch (_) {
      if (token !== requestToken || activeLink !== link || !link.isConnected) return;
      hover.innerHTML = errorMarkup(link.textContent?.trim() || 'Creator');
      positionCard(link);
    }
  }

  function scheduleCreatorHover(link, delay) {
    window.clearTimeout(openTimer);
    if (activeLink && activeLink !== link) hideCreatorHover();
    openTimer = window.setTimeout(() => openCreatorHover(link), delay);
  }

  document.addEventListener('pointerover', event => {
    if (!fineHover || event.pointerType === 'touch') return;
    const link = event.target.closest?.('[data-profile-link]');
    if (!link || link.contains(event.relatedTarget)) return;
    scheduleCreatorHover(link, HOVER_DELAY_MS);
  });

  document.addEventListener('pointerout', event => {
    if (!fineHover) return;
    const link = event.target.closest?.('[data-profile-link]');
    if (!link || link.contains(event.relatedTarget)) return;
    hideCreatorHover();
  });

  document.addEventListener('focusin', event => {
    if (!fineHover) return;
    const link = event.target.closest?.('[data-profile-link]');
    if (link) scheduleCreatorHover(link, FOCUS_DELAY_MS);
  });

  document.addEventListener('focusout', event => {
    const link = event.target.closest?.('[data-profile-link]');
    if (link) hideCreatorHover();
  });

  document.addEventListener('click', event => {
    if (event.target.closest?.('[data-profile-link]')) hideCreatorHover();
  }, true);

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') hideCreatorHover();
  });

  window.addEventListener('resize', hideCreatorHover);
  window.addEventListener('scroll', hideCreatorHover, true);
})();
