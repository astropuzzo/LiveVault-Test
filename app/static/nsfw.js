/* LiveVault NSFW scan: archive badges, moments dialog, live progress panel, settings state. */
(() => {
  'use strict';

  const icon = (name, cls = 'icon') => `<svg class="${cls}" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
  const STATUS_TEXT = {
    nsfw: 'NSFW', review: 'Da controllare', safe: 'Safe', scanning: 'Analisi', paused: 'In pausa',
    pending: 'Da analizzare', error: 'Errore analisi', skipped: 'Non analizzato',
  };
  const CLASS_TEXT = {
    FEMALE_BREAST_EXPOSED: 'Seno scoperto', FEMALE_GENITALIA_EXPOSED: 'Genitali femminili',
    MALE_GENITALIA_EXPOSED: 'Genitali maschili', ANUS_EXPOSED: 'Ano', BUTTOCKS_EXPOSED: 'Glutei scoperti',
  };
  const STATE_TEXT = {
    running: 'Analisi in corso', idle: 'Coda vuota', disabled: 'Disattivata', models_missing: 'Modelli mancanti',
    waiting_idle: 'In pausa: registrazione attiva', waiting_storage: 'In pausa: NVMe non disponibile',
  };
  let info = null;
  let timer = null;
  let lastRunningId = null;
  let dialogRecordingId = null;

  // Same h:mm:ss the Gofile/Pixeldrain/VLC players show for the uploaded file.
  function clock(seconds, long = false) {
    const value = Math.max(0, Math.round(Number(seconds) || 0));
    const h = Math.floor(value / 3600), m = Math.floor(value % 3600 / 60), s = value % 60;
    return h || long ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
  }

  function momentsText(recording) {
    const long = Number(recording.duration_seconds || 0) >= 3600;
    const lines = (recording.nsfw_moments || []).map(m => `${clock(m.start, long)} – ${clock(m.end, long)}  ${STATUS_TEXT[m.label] || m.label}  ${CLASS_TEXT[m.class] || m.class || ''} ${Math.round(Number(m.score) * 100)}%`);
    return [`${recording.source_name} · ${recording.filename}`, safeUrl(recording.remote_url) || '(non ancora nel cloud)', ...lines].join('\n');
  }

  function eta(seconds) {
    if (seconds == null) return 'stima in corso';
    if (seconds < 90) return `mancano ~${Math.max(1, Math.round(seconds))} s`;
    if (seconds < 5400) return `mancano ~${Math.round(seconds / 60)} min`;
    return `mancano ~${(seconds / 3600).toLocaleString('it-IT', {maximumFractionDigits: 1})} h`;
  }

  const findRecording = id => (typeof recordings !== 'undefined' ? recordings : []).find(row => Number(row.id) === Number(id));
  const liveFor = id => (info?.current && Number(info.current.recording_id) === Number(id) ? info.current : null);

  function badgeText(recording) {
    const live = liveFor(recording.id);
    const status = live ? 'scanning' : (recording.nsfw_status || 'pending');
    const moments = (recording.nsfw_moments || []).length;
    if (status === 'scanning') {
      const pct = Math.round(Number(live?.progress ?? recording.nsfw_progress ?? 0) * 100);
      return {status, text: `Analisi ${pct}%`};
    }
    if (status === 'paused') return {status, text: `In pausa ${Math.round(Number(recording.nsfw_progress || 0) * 100)}%`};
    if ((status === 'nsfw' || status === 'review') && moments) return {status, text: `${STATUS_TEXT[status]} · ${moments}`};
    return {status, text: STATUS_TEXT[status] || status};
  }

  window.nsfwBadgeMarkup = recording => {
    const {status, text} = badgeText(recording);
    // With the feature off, never-analysed files stay quiet.
    if (!info?.enabled && ['pending', 'skipped'].includes(status)) return '';
    const title = status === 'error' ? recording.nsfw_error : `Analisi NSFW: ${text}`;
    return `<button type="button" class="nsfw-badge ${esc(status)}" data-nsfw-open="${recording.id}" title="${esc(title || '')}">${status === 'scanning' ? '<span class="nsfw-spinner" aria-hidden="true"></span>' : ''}<span>${esc(text)}</span></button>`;
  };

  window.nsfwMenuMarkup = recording => {
    const status = recording.nsfw_status || 'pending';
    const items = [];
    if ((recording.nsfw_moments || []).length) items.push(`<button type="button" data-nsfw-open="${recording.id}">${icon('eye')}<span>Momenti NSFW</span></button>`);
    if (recording.local_available && status !== 'scanning') items.push(`<button type="button" data-nsfw-action="rescan" data-id="${recording.id}">${icon('sparkles')}<span>${['pending', 'paused'].includes(status) ? 'Analizza ora' : 'Rianalizza NSFW'}</span></button>`);
    if (status !== 'safe') items.push(`<button type="button" data-nsfw-action="mark_safe" data-id="${recording.id}">${icon('check')}<span>Segna come safe</span></button>`);
    if (status !== 'nsfw') items.push(`<button type="button" data-nsfw-action="mark_nsfw" data-id="${recording.id}">${icon('warning')}<span>Segna come NSFW</span></button>`);
    if (['pending', 'paused', 'scanning'].includes(status)) items.push(`<button type="button" data-nsfw-action="skip" data-id="${recording.id}">${icon('circle-off')}<span>${status === 'scanning' ? 'Annulla analisi' : 'Escludi dall\'analisi'}</span></button>`);
    return items.join('');
  };

  /* ---------- Progress panel ---------- */
  function renderPanel() {
    const root = $('#nsfwPanel');
    if (!root) return;
    const counts = info?.counts || {};
    const flagged = Number(counts.nsfw || 0) + Number(counts.review || 0);
    if (!info || (!info.enabled && !flagged)) {
      root.classList.add('hidden');
      return;
    }
    root.classList.remove('hidden');
    const current = info.current;
    const state = current ? 'running' : info.state;
    const chip = (key, label, query) => `<button type="button" class="nsfw-count ${key}" data-nsfw-filter="${query}"><strong>${Number(counts[key] || 0).toLocaleString('it-IT')}</strong><span>${label}</span></button>`;
    const queue = `<button type="button" class="nsfw-count queue" data-nsfw-filter="is:daanalizzare"><strong>${Number(info.queue || 0).toLocaleString('it-IT')}</strong><span>In coda</span></button>`;
    let body = '';
    if (current) {
      const pct = Math.min(100, Math.round(Number(current.progress || 0) * 1000) / 10);
      const speed = current.speed ? `${Number(current.speed).toLocaleString('it-IT', {maximumFractionDigits: 0})}× tempo reale` : '';
      body = `<div class="nsfw-job">
        <div class="nsfw-job-head"><div><strong>${esc(current.name || '')}</strong><small title="${esc(current.filename || '')}">${esc(current.filename || '')}</small></div><button type="button" class="button secondary compact" data-nsfw-action="skip" data-id="${current.recording_id}">${icon('x', 'button-icon')}<span>Annulla</span></button></div>
        <div class="nsfw-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="Avanzamento analisi"><i data-dynamic-width="${pct}"></i></div>
        <div class="nsfw-job-meta"><span><strong>${pct.toLocaleString('it-IT')}%</strong> · ${clock(current.t)} / ${clock(current.duration)}</span><span>${Number(current.frames || 0).toLocaleString('it-IT')} fotogrammi${current.verified ? ` · ${current.verified} verificati` : ''}</span><span>${Number(current.found || 0)} momenti trovati</span>${speed ? `<span>${speed}</span>` : ''}<span>${eta(current.eta_seconds)}</span></div>
      </div>`;
    } else if (info.state === 'models_missing') {
      body = `<p class="nsfw-note bad">${esc(info.detail || 'Modelli non trovati')}. Copia <code>320n.onnx</code> e <code>640m.onnx</code> in <code>/data/livevault/models</code> sul nodo.</p>`;
    } else if (info.state === 'waiting_idle') {
      body = '<p class="nsfw-note">Riprende da dove si era fermata quando nessuna live è in registrazione.</p>';
    } else if (info.state === 'waiting_storage') {
      body = '<p class="nsfw-note">NVMe scollegato: l\'analisi riprende quando torna disponibile.</p>';
    }
    setMarkup(root, `<header class="nsfw-head"><div>${icon('sparkles')}<h2>Analisi NSFW</h2><span class="nsfw-state ${esc(state)}">${esc(STATE_TEXT[state] || state)}</span></div><div class="nsfw-counts">${chip('nsfw', 'NSFW', 'is:nsfw')}${chip('review', 'Da controllare', 'is:controllare')}${chip('safe', 'Safe', 'is:safe')}${queue}</div></header>${body}`);
    applyDynamicStyles(root);
  }

  function updateBadgesInPlace() {
    $$('.nsfw-badge[data-nsfw-open]').forEach(node => {
      const recording = findRecording(node.dataset.nsfwOpen);
      if (!recording) return;
      const markup = window.nsfwBadgeMarkup(recording);
      if (markup && node.outerHTML !== markup) node.outerHTML = markup;
    });
  }

  async function poll() {
    clearTimeout(timer);
    try {
      info = await api('/api/nsfw');
      const runningId = info.current?.recording_id ?? null;
      if (lastRunningId && lastRunningId !== runningId) {
        // A job just ended: reload the archive so the verdict appears.
        await refresh();
      }
      lastRunningId = runningId;
      renderPanel();
      updateBadgesInPlace();
      if (dialogRecordingId && liveFor(dialogRecordingId)) renderDialog();
    } catch (_error) { /* optional feature */ }
    const watching = activeView === 'archive' || dialogRecordingId;
    timer = setTimeout(poll, info?.current && watching ? 2000 : watching ? 5000 : 30000);
  }

  /* ---------- Moments dialog ---------- */
  function momentCard(recording, moment) {
    const image = safeUrl(moment.image_url || '');
    const long = Number(recording.duration_seconds || 0) >= 3600;
    const action = recording.local_available ? `Guarda da ${clock(moment.start, long)}` : `Ingrandisci ${clock(moment.start, long)}`;
    return `<article class="nsfw-moment ${esc(moment.label)}" data-moment-at="${Number(moment.start)}">
      <button type="button" class="nsfw-shot" data-nsfw-play="${recording.id}" data-at="${Number(moment.start)}" aria-label="${action}" title="${action}">${image ? `<img src="${esc(image)}" alt="" loading="lazy">` : icon('play')}</button>
      <div><strong class="nsfw-time">${clock(moment.start, long)} – ${clock(moment.end, long)}</strong><span class="nsfw-label ${esc(moment.label)}">${esc(STATUS_TEXT[moment.label] || moment.label)}</span></div>
      <small>${esc(CLASS_TEXT[moment.class] || moment.class || '')} · ${Math.round(Number(moment.score) * 100)}%</small>
    </article>`;
  }

  // Moments laid out on the whole duration; works without the local file.
  function timelineMarkup(recording, moments) {
    const total = Math.max(Number(recording.duration_seconds) || 0, ...moments.map(m => Number(m.end) || 0), 1);
    const long = total >= 3600;
    const ticks = [0, .25, .5, .75, 1].map(r => `<text x="${(r * 1000).toFixed(1)}" y="46" text-anchor="${r === 0 ? 'start' : r === 1 ? 'end' : 'middle'}">${clock(total * r, long)}</text>`).join('');
    const bars = moments.map(m => {
      const x = Math.max(0, Number(m.start) / total * 1000);
      const w = Math.max(4, (Number(m.end) - Number(m.start)) / total * 1000);
      return `<rect class="nsfw-tl-bar ${esc(m.label)}" x="${x.toFixed(1)}" y="6" width="${Math.min(w, 1000 - x).toFixed(1)}" height="22" rx="4" data-nsfw-seek="${recording.id}" data-at="${Number(m.start)}" tabindex="0" role="button"><title>${clock(m.start, long)} – ${clock(m.end, long)} · ${esc(STATUS_TEXT[m.label] || m.label)}</title></rect>`;
    }).join('');
    const covered = moments.reduce((sum, m) => sum + Math.max(0, Number(m.end) - Number(m.start)), 0);
    return `<div class="nsfw-timeline"><div class="nsfw-timeline-head"><strong>Timeline</strong><span>${moments.length} momenti · ${clock(covered, long)} su ${clock(total, long)} (${Math.round(covered / total * 100)}%)</span></div>
      <svg viewBox="0 0 1000 52" preserveAspectRatio="none" role="img" aria-label="Momenti sulla durata del video"><rect class="nsfw-tl-track" x="0" y="6" width="1000" height="22" rx="6"></rect>${bars}${ticks}</svg></div>`;
  }

  function openShot(recording, seconds) {
    const moment = (recording.nsfw_moments || []).find(m => Number(m.start) === Number(seconds));
    const long = Number(recording.duration_seconds || 0) >= 3600;
    const time = clock(seconds, long);
    navigator.clipboard?.writeText(time).catch(() => {});
    const remote = safeUrl(recording.remote_url);
    const image = safeUrl(moment?.image_url || '');
    const box = $('#nsfwDialog .nsfw-lightbox');
    if (!box) return;
    setMarkup(box, `${image ? `<img src="${esc(image)}" alt="">` : ''}<div><strong>${time}${moment ? ` – ${clock(moment.end, long)}` : ''}</strong><span>Tempo copiato: nel player del cloud vai a ${time}.</span></div><div class="nsfw-lightbox-actions">${remote ? `<a class="button primary compact" href="${esc(remote)}" target="_blank" rel="noopener">${icon('external', 'button-icon')}<span>Apri nel cloud</span></a>` : ''}<button type="button" class="button secondary compact" data-nsfw-lightbox-close>${icon('x', 'button-icon')}<span>Chiudi</span></button></div>`);
    box.hidden = false;
  }

  function renderDialog() {
    const dialog = $('#nsfwDialog');
    const recording = findRecording(dialogRecordingId);
    if (!dialog || !recording) return;
    const {status, text} = badgeText(recording);
    const live = liveFor(recording.id);
    const moments = recording.nsfw_moments || [];
    const pct = Math.round(Number(live?.progress ?? recording.nsfw_progress ?? 0) * 100);
    const progress = live || status === 'paused'
      ? `<div class="nsfw-progress" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><i data-dynamic-width="${pct}"></i></div><p class="nsfw-note">${live ? `${clock(live.t)} / ${clock(live.duration)} · ${eta(live.eta_seconds)}` : `Fermata a ${clock(Number(recording.nsfw_progress || 0) * Number(recording.duration_seconds || 0))}: riprende da lì`}</p>` : '';
    const empty = status === 'safe' ? 'Nessun momento con nudità trovato.'
      : ['pending', 'paused', 'scanning'].includes(status) ? 'I momenti compaiono qui durante l\'analisi.'
      : status === 'error' ? `Errore: ${recording.nsfw_error || 'sconosciuto'}` : 'Nessun momento registrato.';
    const remote = safeUrl(recording.remote_url);
    const provider = {gofile: 'Gofile', pixeldrain: 'Pixeldrain'}[recording.upload_provider] || 'cloud';
    const cloud = remote
      ? `<p class="nsfw-cloud">${icon('cloud', 'mini-icon')}<span>I tempi valgono per questo file anche su ${provider}: stesso file, stessa timeline.</span><a class="button secondary compact" href="${esc(remote)}" target="_blank" rel="noopener">${icon('external', 'button-icon')}<span>Apri su ${provider}</span></a></p>`
      : '<p class="nsfw-cloud">' + icon('cloud', 'mini-icon') + '<span>Non ancora nel cloud: dopo l\'upload i tempi varranno per il file caricato.</span></p>';
    const menu = window.nsfwMenuMarkup(recording).replace(/<button type="button" data-nsfw-open[^]*?<\/button>/, '')
      .replaceAll('<button type="button"', '<button type="button" class="button secondary compact"');
    const copy = moments.length ? `<button type="button" class="button secondary compact" data-nsfw-copy="${recording.id}">${icon('copy', 'button-icon')}<span>Copia elenco</span></button>` : '';
    setMarkup(dialog, `<div class="nsfw-dialog-head"><div><h2 id="nsfwDialogTitle">${esc(recording.source_name)}</h2><small>${esc(recording.filename)} · ${esc(dateText(recording.started_at))}</small></div><span class="nsfw-badge ${esc(status)}">${esc(text)}</span><button type="button" class="icon-button" data-nsfw-close aria-label="Chiudi">${icon('x')}</button></div>
      ${progress}
      ${moments.length ? timelineMarkup(recording, moments) : ''}
      <div class="nsfw-lightbox" hidden></div>
      ${moments.length ? `<div class="nsfw-moments">${moments.map(moment => momentCard(recording, moment)).join('')}</div>` : `<p class="nsfw-empty">${esc(empty)}</p>`}
      ${recording.nsfw_error && status !== 'error' ? `<p class="nsfw-note">${esc(recording.nsfw_error)}</p>` : ''}
      ${cloud}
      <footer class="nsfw-dialog-actions">${copy}${menu}</footer>`);
    applyDynamicStyles(dialog);
  }

  function openDialog(id) {
    const dialog = $('#nsfwDialog');
    if (!dialog || !findRecording(id)) return;
    dialogRecordingId = Number(id);
    renderDialog();
    if (!dialog.open) dialog.showModal();
    poll();
  }

  async function playAt(id, seconds) {
    const recording = findRecording(id);
    if (!recording) return;
    // Local copy already deleted after upload: show the frame and hand over the time for the cloud player.
    if (!recording.view_url) return openShot(recording, seconds);
    $('#nsfwDialog')?.close();
    await playVideo(recording.view_url, `${recording.source_name} · ${clock(seconds)}`);
    const player = $('#videoPlayer');
    const seek = () => { try { player.currentTime = Number(seconds); } catch (_error) { /* not seekable yet */ } };
    if (player.readyState >= 1) seek(); else player.addEventListener('loadedmetadata', seek, {once: true});
  }

  async function runAction(action, id, button) {
    if (action === 'rescan' && findRecording(id)?.nsfw_status === 'safe') {
      if (!await uiConfirm('Rianalizzare questo video? L\'esito attuale verrà sostituito.', {title: 'Rianalizza NSFW'})) return;
    }
    button?.setAttribute('disabled', '');
    try {
      const updated = await api(`/api/recordings/${id}/nsfw`, {method: 'POST', body: JSON.stringify({action})});
      const index = recordings.findIndex(row => Number(row.id) === Number(id));
      if (index >= 0) recordings[index] = updated;
      toast({rescan: 'Messo in coda per l\'analisi', skip: 'Analisi annullata', mark_safe: 'Segnato come safe', mark_nsfw: 'Segnato come NSFW'}[action]);
      renderRecordings();
      if (dialogRecordingId) renderDialog();
      poll();
    } catch (error) {
      toast(error.message, 'bad');
    } finally {
      button?.removeAttribute('disabled');
    }
  }

  /* ---------- Settings: model state ---------- */
  async function renderModelState() {
    const node = $('#nsfwModelState');
    if (!node) return;
    try {
      const data = await api('/api/nsfw');
      info = data;
      const model = (entry, label) => entry.present
        ? `<span class="ok">${icon('check', 'mini-icon')}${label}: ${esc(entry.path.split('/').pop())} (${esc(humanBytes(entry.size_bytes))})</span>`
        : entry.path ? `<span class="bad">${icon('warning', 'mini-icon')}${label}: manca ${esc(entry.path)}</span>` : `<span>${label}: non impostato</span>`;
      setMarkup(node, `${model(data.models.nsfw_fast_model, 'Veloce')} ${model(data.models.nsfw_verify_model, 'Verifica')}`);
    } catch (error) {
      node.textContent = `Stato modelli non disponibile: ${error.message}`;
    }
  }

  document.addEventListener('click', event => {
    const open = event.target.closest('[data-nsfw-open]');
    const action = event.target.closest('[data-nsfw-action]');
    const play = event.target.closest('[data-nsfw-play]');
    const filter = event.target.closest('[data-nsfw-filter]');
    if (open && !action) { event.preventDefault(); openDialog(open.dataset.nsfwOpen); return; }
    if (action) { event.preventDefault(); runAction(action.dataset.nsfwAction, action.dataset.id, action); return; }
    if (play) { event.preventDefault(); playAt(play.dataset.nsfwPlay, play.dataset.at); return; }
    const seekBar = event.target.closest('[data-nsfw-seek]');
    if (seekBar) {
      const card = document.querySelector(`#nsfwDialog [data-moment-at="${CSS.escape(seekBar.dataset.at)}"]`);
      card?.scrollIntoView({behavior: 'smooth', block: 'center'});
      card?.classList.add('flash');
      setTimeout(() => card?.classList.remove('flash'), 1200);
      playAt(seekBar.dataset.nsfwSeek, seekBar.dataset.at);
      return;
    }
    if (event.target.closest('[data-nsfw-lightbox-close]')) { const box = $('#nsfwDialog .nsfw-lightbox'); if (box) box.hidden = true; return; }
    if (filter) {
      const search = $('#recordingSearch');
      search.value = search.value.trim() === filter.dataset.nsfwFilter ? '' : filter.dataset.nsfwFilter;
      renderRecordings();
      return;
    }
    const copy = event.target.closest('[data-nsfw-copy]');
    if (copy) {
      const recording = findRecording(copy.dataset.nsfwCopy);
      navigator.clipboard?.writeText(momentsText(recording)).then(() => toast('Elenco momenti copiato'), () => toast('Copia non riuscita', 'bad'));
      return;
    }
    if (event.target.closest('[data-nsfw-close]')) $('#nsfwDialog')?.close();
    if (event.target.closest('#settingsBtn, [data-open-settings]')) setTimeout(renderModelState, 50);
  });
  $('#nsfwDialog')?.addEventListener('close', () => { dialogRecordingId = null; });
  $('#nsfwDialog')?.addEventListener('click', event => { if (event.target === event.currentTarget) event.currentTarget.close(); });

  const baseShowViewNsfw = showView;
  showView = function showViewWithNsfw(name, updateHash = true) {
    const result = baseShowViewNsfw(name, updateHash);
    if (activeView === 'archive') poll();
    return result;
  };

  // Start after the first authenticated refresh (a 401 would show the login).
  let started = false;
  const baseRefreshNsfw = refresh;
  refresh = async function refreshWithNsfw(options = {}) {
    const result = await baseRefreshNsfw(options);
    if (!started) { started = true; poll(); }
    else if (info) updateBadgesInPlace();
    return result;
  };
})();
