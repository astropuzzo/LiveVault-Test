/* LiveVault NSFW scan: archive badges, moments dialog, live progress panel, settings state. */
(() => {
  'use strict';

  const icon = (name, cls = 'icon') => `<svg class="${cls}" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
  const STATUS_TEXT = {
    nsfw: 'NSFW', review: 'Da controllare', safe: 'Safe', scanning: 'Analisi', paused: 'In pausa',
    pending: 'Da analizzare', error: 'Errore analisi', skipped: 'Non analizzato', verifying: 'In verifica',
  };
  const CLASS_ICON = {
    FEMALE_BREAST_EXPOSED: 'nsfw-breast', BUTTOCKS_EXPOSED: 'nsfw-butt', ANUS_EXPOSED: 'nsfw-anus',
    FEMALE_GENITALIA_EXPOSED: 'nsfw-vulva', MALE_GENITALIA_EXPOSED: 'nsfw-phallus',
  };
  const MARK_TEXT = {nsfw: 'NSFW confermato', review: 'Da controllare', pending: 'In verifica'};
  // Several parts can be visible together: most explicit first ("A+B" from the server).
  const EXPLICIT = {FEMALE_GENITALIA_EXPOSED: 5, MALE_GENITALIA_EXPOSED: 5, ANUS_EXPOSED: 4, FEMALE_BREAST_EXPOSED: 3, BUTTOCKS_EXPOSED: 2};
  const classParts = cls => [...new Set(String(cls || '').split('+').filter(Boolean))].sort((a, b) => (EXPLICIT[b] || 0) - (EXPLICIT[a] || 0));
  const classText = cls => classParts(cls).map(c => CLASS_TEXT[c] || c).join(' + ');
  const classIcon = cls => CLASS_ICON[classParts(cls)[0]] || 'warning';
  // Pin content: the most explicit icon, the next one stacked as a small badge.
  // Pin colour = most explicit body part, so categories read at a glance.
  const CLASS_CAT = {
    FEMALE_GENITALIA_EXPOSED: 'vulva', MALE_GENITALIA_EXPOSED: 'phallus', ANUS_EXPOSED: 'anus',
    FEMALE_BREAST_EXPOSED: 'breast', BUTTOCKS_EXPOSED: 'butt',
  };
  const catClass = cls => `cat-${CLASS_CAT[classParts(cls)[0]] || 'other'}`;
  const classChips = cls => classParts(cls).map(c => `<span class="nsfw-cat-chip cat-${CLASS_CAT[c] || 'other'}">${icon(CLASS_ICON[c] || 'warning', 'mini-icon')}${esc(CLASS_TEXT[c] || c)}</span>`).join('');
  const pinIcons = cls => {
    const parts = classParts(cls);
    const extra = parts.slice(1).find(c => CLASS_ICON[c] && CLASS_ICON[c] !== CLASS_ICON[parts[0]]);
    return `${icon(classIcon(cls), 'mini-icon')}${extra ? `<i class="nsfw-pin-extra">${icon(CLASS_ICON[extra], 'mini-icon')}</i>` : ''}`;
  };
  const wallClock = time => new Intl.DateTimeFormat('it-IT', {timeZone: DISPLAY_TIME_ZONE, hour: '2-digit', minute: '2-digit', second: '2-digit'}).format(new Date(time));
  const CLASS_TEXT = {
    FEMALE_BREAST_EXPOSED: 'Tette', FEMALE_GENITALIA_EXPOSED: 'Figa',
    MALE_GENITALIA_EXPOSED: 'Cazzo', ANUS_EXPOSED: 'Buco del culo', BUTTOCKS_EXPOSED: 'Culo',
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
    const lines = (recording.nsfw_moments || []).map(m => `${clock(m.start, long)} – ${clock(m.end, long)}  ${STATUS_TEXT[m.label] || m.label}  ${classText(m.class)} ${Math.round(Number(m.score) * 100)}%`);
    return [`${recording.source_name} · ${recording.filename}`, safeUrl(recording.remote_url) || '(non ancora nel cloud)', ...lines].join('\n');
  }

  function eta(seconds) {
    if (seconds == null) return 'stima in corso';
    if (seconds < 90) return `mancano ~${Math.max(1, Math.round(seconds))} s`;
    if (seconds < 5400) return `mancano ~${Math.round(seconds / 60)} min`;
    return `mancano ~${(seconds / 3600).toLocaleString('it-IT', {maximumFractionDigits: 1})} h`;
  }

  // Recordings opened from the Monitor may not be in the loaded archive page.
  const extraRecordings = new Map();
  const findRecording = id => (typeof recordings !== 'undefined' ? recordings : []).find(row => Number(row.id) === Number(id))
    || extraRecordings.get(Number(id));
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
  const LIVE_STATE_TEXT = {
    running: 'Analisi dal vivo attiva', idle: 'Dal vivo: nessuna live in registrazione', disabled: 'Analisi dal vivo spenta',
    storage_switch: 'Dal vivo in pausa: cambio disco in corso', busy: 'Dal vivo in attesa: CPU occupata dalla registrazione',
    models_missing: 'Dal vivo: modello mancante', error: 'Errore analisi dal vivo',
  };

  function liveMarkup(live) {
    if (!live || (live.state === 'disabled' && !live.verify_queue)) return '';
    const tracks = (live.tracks || []).map(t => `<span class="nsfw-live-track ${t.unsupported ? 'off' : ''}" title="${esc(t.unsupported || '')}"><strong>${esc(t.name)}</strong><small>${t.unsupported ? 'formato non analizzabile dal vivo: analisi dopo la chiusura' : `${Number(t.samples || 0).toLocaleString('it-IT')} fotogrammi · ${t.marks} segni${t.lag_seconds != null && t.lag_seconds > 15 ? ` · ${Math.round(t.lag_seconds)} s indietro` : ''}`}</small></span>`).join('');
    const verify = live.verify_queue ? `<span class="nsfw-live-verify">${icon('eye', 'mini-icon')}<span>${live.verify_queue} da verificare${live.verify_state === 'running' ? ' · verifica in corso' : live.verify_state === 'busy' ? ' · in attesa di CPU' : ''}</span></span>` : '';
    return `<div class="nsfw-live"><div class="nsfw-live-head"><span class="nsfw-live-dot ${esc(live.state)}" aria-hidden="true"></span><strong>${esc(LIVE_STATE_TEXT[live.state] || live.state)}</strong>${verify}</div>${tracks ? `<div class="nsfw-live-tracks">${tracks}</div>` : ''}</div>`;
  }

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
    setMarkup(root, `<header class="nsfw-head"><div>${icon('sparkles')}<h2>Analisi NSFW</h2><span class="nsfw-state ${esc(state)}">${esc(STATE_TEXT[state] || state)}</span></div><div class="nsfw-counts">${chip('nsfw', 'NSFW', 'is:nsfw')}${chip('review', 'Da controllare', 'is:controllare')}${chip('safe', 'Safe', 'is:safe')}${queue}</div></header>${liveMarkup(info.live)}${body}`);
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
      <span class="nsfw-cat-chips">${classChips(moment.class)}</span><small>${Math.round(Number(moment.score) * 100)}%</small>
    </article>`;
  }

  // Moments laid out on the whole duration, with an icon for what was seen.
  // HTML (not a stretched SVG) so icons keep their shape; works without the local file.
  function timelineMarkup(recording, moments) {
    const total = Math.max(Number(recording.duration_seconds) || 0, ...moments.map(m => Number(m.end) || 0), 1);
    const long = total >= 3600;
    const ticks = [0, .25, .5, .75, 1].map(r => `<span class="nsfw-tl-tick" data-dynamic-left="${(r * 100).toFixed(2)}">${clock(total * r, long)}</span>`).join('');
    let lastIcon = -99;
    const bands = [];
    const pins = [];
    for (const m of moments) {
      const left = Math.max(0, Math.min(100, Number(m.start) / total * 100));
      const width = Math.max(0.6, Math.min(100 - left, (Number(m.end) - Number(m.start)) / total * 100));
      const label = `${clock(m.start, long)} – ${clock(m.end, long)} · ${classText(m.class)} · ${STATUS_TEXT[m.label] || m.label}`;
      const attrs = `data-nsfw-seek="${recording.id}" data-at="${Number(m.start)}" title="${esc(label)}" aria-label="${esc(label)}"`;
      bands.push(`<button type="button" class="nsfw-tl-band ${esc(m.label)}" data-dynamic-left="${left.toFixed(3)}" data-dynamic-width="${width.toFixed(3)}" ${attrs}></button>`);
      if (left - lastIcon < 3.2) continue;  // keep icons readable on dense timelines
      lastIcon = left;
      pins.push(`<button type="button" class="nsfw-tl-pin ${esc(m.label)} ${catClass(m.class)}" data-dynamic-left="${Math.min(98.6, Math.max(1.4, left)).toFixed(3)}" ${attrs}>${pinIcons(m.class)}</button>`);
    }
    const covered = moments.reduce((sum, m) => sum + Math.max(0, Number(m.end) - Number(m.start)), 0);
    const live = recording.nsfw_source === 'live' ? ` · visto dal vivo${recording.nsfw_live_coverage ? ` (${Math.round(recording.nsfw_live_coverage * 100)}%)` : ''}` : '';
    return `<div class="nsfw-timeline"><div class="nsfw-timeline-head"><strong>Timeline</strong><span>${moments.length} momenti · ${clock(covered, long)} su ${clock(total, long)} (${Math.round(covered / total * 100)}%)${live}</span></div>
      <div class="nsfw-tl" role="group" aria-label="Momenti sulla durata del video"><div class="nsfw-tl-pins">${pins.join('')}</div><div class="nsfw-tl-track">${bands.join('')}</div><div class="nsfw-tl-ticks">${ticks}</div></div></div>`;
  }

  /* ---------- Monitor timeline (Cronologia) ---------- */
  const pulseMarks = new Map();
  const pulseGroups = new Map();
  const RANK = {pending: 0, review: 1, nsfw: 2};
  window.pulseNsfwLayer = (sessions, xFor) => {
    const moments = sessions.flatMap(session => (session.nsfw_moments || []).map(m => ({...m, name: session.display_name})))
      .sort((a, b) => timestamp(a.started_at) - timestamp(b.started_at));
    if (!moments.length) return '';
    if (pulseMarks.size > 3000) { pulseMarks.clear(); pulseGroups.clear(); }
    // Group pins that would overlap at the real on-screen width (phones
    // scroll a wide track; desktop uses the visible one): one pin + count.
    const hours = Math.max(1, Number(controlRoomPulseData?.hours) || 6);
    const perHour = typeof window.pulsePixelsPerHour === 'function' ? window.pulsePixelsPerHour() : 0;
    const trackPx = perHour ? hours * perHour : Math.max(500, window.innerWidth - 420);
    const minGap = (perHour ? 46 : 32) / trackPx * 100;  // pin + count badge never touch the next one
    // Bands closer than a few pixels at this scale read as one stretch: draw
    // them joined instead of as dashes (the moments themselves stay separate).
    const bandJoin = 6 / trackPx * 100;
    const spans = [];
    const groups = [];
    for (const m of moments) {
      const start = timestamp(m.started_at);
      const end = Math.max(start, timestamp(m.ended_at) || start);
      const left = xFor(start) / 10;
      const right = Math.max(left + 0.3, xFor(end) / 10);
      m.key = `${m.started_at}|${m.recording_id || ''}|${m.mark_id || ''}`;
      pulseMarks.set(m.key, m);
      const span = spans[spans.length - 1];
      if (span && left - span.right < bandJoin) {
        span.right = Math.max(span.right, right);
        if (RANK[m.label] > RANK[span.label]) span.label = m.label;
      } else spans.push({left, right, label: m.label});
      const last = groups[groups.length - 1];
      if (last && left - last.left < minGap) last.items.push(m);
      else groups.push({left, items: [m]});
    }
    const bands = spans.map(span => `<i class="nsfw-pulse-band ${esc(span.label)}" data-dynamic-left="${span.left.toFixed(3)}" data-dynamic-width="${Math.min(100 - span.left, span.right - span.left).toFixed(3)}"></i>`);
    const pins = groups.map(group => {
      const {items} = group;
      const top = items.reduce((best, m) => (RANK[m.label] > RANK[best.label] ? m : best), items[0]);
      const cls = classParts(items.map(m => m.class).join('+')).join('+');
      const first = timestamp(items[0].started_at);
      const lastEnd = timestamp(items[items.length - 1].ended_at) || first;
      const title = items.length > 1
        ? `${items.length} momenti · ${wallClock(first)}–${wallClock(lastEnd)} · tocca per l'elenco`
        : `${wallClock(first)} · ${classText(cls) || 'Nudità'} · ${MARK_TEXT[top.label] || top.label}${top.count > 1 ? ` · ${top.count} fotogrammi` : ''}${top.approx ? ' · posizione stimata' : ''}`;
      let attr = `data-nsfw-pulse="${esc(items[0].key)}"`;
      if (items.length > 1) {
        const groupKey = `g:${items.map(m => m.key).join(',')}`;
        pulseGroups.set(groupKey, items);
        attr = `data-nsfw-pulse-group="${esc(groupKey)}"`;
      }
      const left = Math.min(98.6, Math.max(1.4, group.left));
      return `<button type="button" class="nsfw-pulse-mark ${esc(top.label)} ${catClass(cls)}" data-dynamic-left="${left.toFixed(3)}" ${attr} title="${esc(title)}" aria-label="${esc(title)}">${pinIcons(cls)}${items.length > 1 ? `<b class="nsfw-pin-count">${items.length}</b>` : ''}</button>`;
    });
    return `<div class="nsfw-pulse-layer">${bands.join('')}${pins.join('')}</div>`;
  };

  function openPulseGroup(groupKey) {
    const items = pulseGroups.get(groupKey);
    const dialog = $('#nsfwDialog');
    if (!items || !dialog) return;
    dialogRecordingId = null;
    const rows = items.map(m => {
      const image = safeUrl(m.image_url || '');
      const start = timestamp(m.started_at);
      return `<button type="button" class="nsfw-group-row ${esc(m.label)}" data-nsfw-pulse="${esc(m.key)}">${image ? `<img src="${esc(image)}" alt="" loading="lazy">` : `<span class="nsfw-group-icon">${icon(classIcon(m.class))}</span>`}<span><strong>${wallClock(start)}${m.ended_at ? ` – ${wallClock(timestamp(m.ended_at))}` : ''}</strong><span class="nsfw-cat-chips">${classChips(m.class)}</span><small>${esc(MARK_TEXT[m.label] || m.label)}${m.count > 1 ? ` · ${m.count} fotogrammi` : ''}</small></span>${icon('chevron-right', 'mini-icon')}</button>`;
    }).join('');
    setMarkup(dialog, `<div class="nsfw-dialog-head"><div><h2 id="nsfwDialogTitle">${esc(items[0].name || '')}</h2><small>${items.length} momenti ravvicinati</small></div><button type="button" class="icon-button" data-nsfw-close aria-label="Chiudi">${icon('x')}</button></div><div class="nsfw-group-list">${rows}</div>`);
    if (!dialog.open) dialog.showModal();
  }

  async function openPulseMark(key) {
    const mark = pulseMarks.get(key);
    if (!mark) return;
    let recording = mark.recording_id ? findRecording(mark.recording_id) : null;
    if (!recording && mark.recording_id) {
      try {
        recording = await api(`/api/recordings/${mark.recording_id}`);
        extraRecordings.set(Number(recording.id), recording);
      } catch (_error) { recording = null; }
    }
    if (recording) {
      openDialog(recording.id);
      // Jump straight to the clicked moment: frame, time and cloud link.
      const at = Number(mark.file_time);
      const nearest = (recording.nsfw_moments || []).reduce((best, m) => (!best || Math.abs(m.start - at) < Math.abs(best.start - at) ? m : best), null);
      if (nearest) {
        openShot(recording, nearest.start, false);
        const card = document.querySelector(`#nsfwDialog [data-moment-at="${CSS.escape(String(nearest.start))}"]`);
        card?.classList.add('flash');
        setTimeout(() => card?.classList.remove('flash'), 1400);
      }
      return;
    }
    // Still recording: show what was seen and when.
    const dialog = $('#nsfwDialog');
    if (!dialog) return;
    dialogRecordingId = null;
    const image = safeUrl(mark.image_url || '');
    const start = timestamp(mark.started_at);
    const shot = image ? `<img src="${esc(image)}" alt="">` : `<div class="nsfw-noshot">${icon(classIcon(mark.class))}<span>Anteprima non disponibile</span></div>`;
    setMarkup(dialog, `<div class="nsfw-dialog-head"><div><h2 id="nsfwDialogTitle">${esc(mark.name || '')}</h2><small>Live in corso · il momento sarà collegato al file quando la registrazione si chiude</small></div><span class="nsfw-badge ${esc(mark.label === 'pending' ? 'verifying' : mark.label)}">${esc(MARK_TEXT[mark.label] || mark.label)}</span><button type="button" class="icon-button" data-nsfw-close aria-label="Chiudi">${icon('x')}</button></div>
      <div class="nsfw-lightbox">${shot}<div><strong>${wallClock(start)}${mark.ended_at ? ` – ${wallClock(timestamp(mark.ended_at))}` : ''}</strong><span class="nsfw-cat-chips">${classChips(mark.class)}</span><span>${mark.count > 1 ? `${mark.count} fotogrammi` : ''}</span></div></div>`);
    if (!dialog.open) dialog.showModal();
  }

  function openShot(recording, seconds, copy = true) {
    const moment = (recording.nsfw_moments || []).find(m => Number(m.start) === Number(seconds));
    const long = Number(recording.duration_seconds || 0) >= 3600;
    const time = clock(seconds, long);
    if (copy) navigator.clipboard?.writeText(time).catch(() => {});
    const remote = safeUrl(recording.remote_url);
    const image = safeUrl(moment?.image_url || '');
    const box = $('#nsfwDialog .nsfw-lightbox');
    if (!box) return;
    const shot = image ? `<img src="${esc(image)}" alt="">` : `<div class="nsfw-noshot">${icon(classIcon(moment?.class))}<span>Anteprima non disponibile${recording.local_available ? ' · usa "Rianalizza NSFW" per rigenerarla' : ''}</span></div>`;
    const local = recording.view_url ? `<button type="button" class="button secondary compact" data-nsfw-play="${recording.id}" data-at="${Number(seconds)}">${icon('play', 'button-icon')}<span>Guarda da qui</span></button>` : '';
    setMarkup(box, `${shot}<div><strong>${time}${moment ? ` – ${clock(moment.end, long)}` : ''}</strong><span>${copy ? `Tempo copiato: nel player del cloud vai a ${time}.` : `Nel file caricato: vai a ${time}.`}</span></div><div class="nsfw-lightbox-actions">${remote ? `<a class="button primary compact" href="${esc(remote)}" target="_blank" rel="noopener">${icon('external', 'button-icon')}<span>Apri nel cloud</span></a>` : ''}${local}<button type="button" class="button secondary compact" data-nsfw-lightbox-close>${icon('x', 'button-icon')}<span>Chiudi</span></button></div>`);
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
    const pulseGroup = event.target.closest('[data-nsfw-pulse-group]');
    if (pulseGroup) { event.preventDefault(); openPulseGroup(pulseGroup.dataset.nsfwPulseGroup); return; }
    const pulseMark = event.target.closest('[data-nsfw-pulse]');
    if (pulseMark) { event.preventDefault(); openPulseMark(pulseMark.dataset.nsfwPulse); return; }
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

  const baseRenderSourcesNsfw = renderSources;
  renderSources = function renderSourcesWithNsfw(...args) {
    const result = baseRenderSourcesNsfw.apply(this, args);
    const pulse = document.querySelector('.cr-pulse');
    if (pulse) applyDynamicStyles(pulse);
    // After the pulse legend is rebuilt (pulse-tuning, next frame), add the NSFW key.
    if (pulse?.querySelector('.nsfw-pulse-mark')) requestAnimationFrame(() => requestAnimationFrame(() => {
      const legend = document.querySelector('.cr-pulse-legend');
      if (legend && !legend.querySelector('.nsfw-legend')) legend.insertAdjacentHTML('beforeend', `<span class="cr-pulse-legend-item nsfw-legend">${['FEMALE_GENITALIA_EXPOSED', 'MALE_GENITALIA_EXPOSED', 'ANUS_EXPOSED', 'FEMALE_BREAST_EXPOSED', 'BUTTOCKS_EXPOSED'].map(c => `<span class="nsfw-legend-cat cat-${CLASS_CAT[c]}">${icon(CLASS_ICON[c], 'mini-icon')}${esc(CLASS_TEXT[c])}</span>`).join('')}</span>`);
    }));
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
