/* LiveVault 3.0 — product UI renderer.
 * This layer replaces presentation only. API calls and operational contracts stay in app.js/operations.js.
 */
(() => {
  'use strict';

  const icon = (name, className = 'icon') => `<svg class="${className}" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
  window.liveVaultIcon = icon;

  const actionButton = (name, label, attrs = '', tone = '') =>
    `<button class="icon-button ${tone}" type="button" aria-label="${esc(label)}" data-tooltip="${esc(label)}" ${attrs}>${icon(name)}</button>`;
  const actionLink = (name, label, href, attrs = '') =>
    `<a class="icon-button" aria-label="${esc(label)}" data-tooltip="${esc(label)}" href="${esc(href)}" ${attrs}>${icon(name)}</a>`;

  setBusy = function setBusyProduct(button, busy, label = '') {
    if (!button) return;
    if (busy) {
      if (!button.dataset.oldHtml) button.dataset.oldHtml = button.innerHTML;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      if (label) button.textContent = label;
    } else {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      if (button.dataset.oldHtml) {
        button.innerHTML = button.dataset.oldHtml;
        delete button.dataset.oldHtml;
      }
    }
  };

  const viewMeta = {
    dashboard: ['Monitor', 'Stato live e registrazioni'],
    library: ['Libreria', 'Creator e sorgenti'],
    archive: ['Archivio', 'Registrazioni'],
    statistics: ['Analisi', 'Attività e copertura'],
  };

  const baseShowView = showView;
  showView = function showViewProduct(name, updateHash = true) {
    const result = baseShowView(name, updateHash);
    const [title, context] = viewMeta[activeView] || viewMeta.dashboard;
    const titleNode = $('#appViewTitle');
    const contextNode = $('#appContext');
    if (titleNode) titleNode.textContent = title;
    if (contextNode) contextNode.textContent = context;
    document.title = `${title} · LiveVault`;
    return result;
  };

  function healthReasonCount(status) {
    return Object.keys(status?.worker?.errors || {}).length
      + Number(status?.queue?.integrity_failed || 0)
      + Number(status?.queue?.failed || 0)
      + Number(status?.queue?.waiting_config || 0)
      + Number(status?.history?.audio_missing || 0);
  }

  const baseRenderStatus = renderStatus;
  renderStatus = function renderStatusProduct(status) {
    const result = baseRenderStatus(status);
    const health = $('#healthPill');
    if (health) {
      const hidden = health.classList.contains('hidden');
      const action = health.dataset.action || 'none';
      const count = healthReasonCount(status);
      health.className = `rail-icon-button health-button status-alert${hidden ? ' hidden' : ''}`;
      health.dataset.action = action;
      health.innerHTML = icon(action === 'settings' ? 'hard-drive' : 'warning') + (count && action === 'attention' ? `<span class="health-count">${count}</span>` : '');
      health.setAttribute('data-tooltip', action === 'settings' ? 'Spazio disco critico' : count ? `${count} elementi da controllare` : 'Stato sistema');
    }
    return result;
  };

  function durationScale(seconds) {
    const value = Number(seconds) || 0;
    if (value >= 3600) return `${(value / 3600).toFixed(value >= 36000 ? 0 : 1)} h`;
    if (value >= 60) return `${Math.round(value / 60)} min`;
    return `${Math.round(value)} s`;
  }

  activityChartSvg = function activityChartSvgProduct(rows = []) {
    if (!rows.length || !rows.some(row => Number(row.online_seconds) || Number(row.recorded_seconds))) return '<div class="empty compact">Nessun dato nel periodo.</div>';
    const W = 900, H = 272, L = 54, R = 12, T = 14, B = 34;
    const cw = W - L - R, ch = H - T - B;
    const maxRaw = Math.max(1, ...rows.flatMap(row => [Number(row.online_seconds) || 0, Number(row.recorded_seconds) || 0]));
    const stepBase = maxRaw / 4;
    const magnitude = 10 ** Math.floor(Math.log10(Math.max(1, stepBase)));
    const niceStep = [1, 2, 5, 10].map(v => v * magnitude).find(v => v >= stepBase) || magnitude * 10;
    const maxValue = niceStep * 4;
    const grid = Array.from({length: 5}, (_, i) => {
      const value = maxValue - i * niceStep;
      const y = T + i * (ch / 4);
      return `<line class="chart-grid-line" x1="${L}" x2="${W-R}" y1="${y}" y2="${y}"></line><text class="chart-y-label" x="${L-8}" y="${y+4}" text-anchor="end">${esc(durationScale(value))}</text>`;
    }).join('');
    const groupWidth = cw / rows.length;
    const barWidth = Math.max(1, Math.min(8, groupWidth * .28));
    const labelEvery = rows.length > 120 ? 30 : rows.length > 60 ? 14 : rows.length > 31 ? 7 : rows.length > 14 ? 4 : 1;
    let bars = '', labels = '';
    rows.forEach((row, index) => {
      const center = L + index * groupWidth + groupWidth / 2;
      const online = Number(row.online_seconds) || 0;
      const recorded = Number(row.recorded_seconds) || 0;
      const oh = online / maxValue * ch, rh = recorded / maxValue * ch;
      bars += `<rect class="chart-bar online" x="${(center-barWidth-.8).toFixed(2)}" y="${(T+ch-oh).toFixed(2)}" width="${barWidth.toFixed(2)}" height="${oh.toFixed(2)}"><title>${esc(row.date)} · Online ${esc(duration(online))}</title></rect>`;
      bars += `<rect class="chart-bar recorded" x="${(center+.8).toFixed(2)}" y="${(T+ch-rh).toFixed(2)}" width="${barWidth.toFixed(2)}" height="${rh.toFixed(2)}"><title>${esc(row.date)} · Registrato ${esc(duration(recorded))}</title></rect>`;
      if (index % labelEvery === 0 || index === rows.length - 1) labels += `<text class="chart-label" x="${center.toFixed(2)}" y="${H-10}" text-anchor="middle">${esc(row.date.slice(5))}</text>`;
    });
    return `<svg class="activity-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Tempo online e registrato per giorno">${grid}${bars}${labels}</svg>`;
  };

  hourlyChartSvg = function hourlyChartSvgProduct(rows = []) {
    if (!rows.length || !rows.some(row => Number(row.online_seconds))) return '<div class="empty compact">Nessun dato nel periodo.</div>';
    const W = 900, H = 272, L = 54, R = 12, T = 14, B = 34;
    const cw = W - L - R, ch = H - T - B;
    const maxRaw = Math.max(1, ...rows.map(row => Number(row.online_seconds) || 0));
    const stepBase = maxRaw / 4;
    const magnitude = 10 ** Math.floor(Math.log10(Math.max(1, stepBase)));
    const niceStep = [1,2,5,10].map(v => v*magnitude).find(v => v >= stepBase) || magnitude*10;
    const maxValue = niceStep * 4;
    const grid = Array.from({length:5}, (_, i) => {
      const value = maxValue - i*niceStep, y=T+i*(ch/4);
      return `<line class="chart-grid-line" x1="${L}" x2="${W-R}" y1="${y}" y2="${y}"></line><text class="chart-y-label" x="${L-8}" y="${y+4}" text-anchor="end">${esc(durationScale(value))}</text>`;
    }).join('');
    const groupWidth = cw / 24, barWidth = groupWidth * .54;
    const bars = rows.map((row, index) => {
      const value = Number(row.online_seconds) || 0, bh=value/maxValue*ch;
      const x=L+index*groupWidth+(groupWidth-barWidth)/2;
      const label = index % 3 === 0 ? `<text class="chart-label" x="${(L+index*groupWidth+groupWidth/2).toFixed(2)}" y="${H-10}" text-anchor="middle">${String(index).padStart(2,'0')}</text>` : '';
      return `<rect class="chart-bar online" x="${x.toFixed(2)}" y="${(T+ch-bh).toFixed(2)}" width="${barWidth.toFixed(2)}" height="${bh.toFixed(2)}"><title>${String(index).padStart(2,'0')}:00 · ${esc(duration(value))}</title></rect>${label}`;
    }).join('');
    return `<svg class="activity-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Tempo online per fascia oraria">${grid}${bars}</svg>`;
  };

  recordingStreamMarkup = function recordingStreamMarkupProduct(recording) {
    const item = (ok, name, codec) => `<span class="stream-state ${ok === true ? 'stream-ok' : ok === false ? 'stream-bad' : 'stream-unknown'}">${icon(ok === true ? 'check' : ok === false ? 'circle-off' : 'info', 'mini-icon')}<span>${esc(name)}${codec ? ` · ${esc(codec)}` : ''}</span></span>`;
    return item(recording.has_video, 'Video', recording.video_codec) + item(recording.has_audio, 'Audio', recording.audio_codec);
  };

  function sourceMoreMenu(profile) {
    const source = profile.source;
    const publicUrl = safeUrl(source.source_url);
    const folderUrl = safeUrl(source.gofile_folder_url);
    const cloudUrl = safeUrl(source.latest_cloud_url);
    return `<details class="row-more"><summary class="icon-button" aria-label="Altre azioni" data-tooltip="Altre azioni">${icon('more')}</summary><div class="row-menu">
      <button type="button" data-action="check" data-id="${source.id}">${icon('refresh')}<span>Controlla ora</span></button>
      <button type="button" data-action="edit" data-id="${source.id}">${icon('edit')}<span>Modifica sorgente</span></button>
      <button type="button" data-action="toggle" data-id="${source.id}">${icon(source.enabled ? 'pause' : 'play')}<span>${source.enabled ? 'Metti in pausa' : 'Riattiva'}</span></button>
      ${source.organize_cloud && !folderUrl ? `<button type="button" data-action="organize" data-id="${source.id}">${icon('folder')}<span>Crea cartella Gofile</span></button>` : ''}
      ${folderUrl ? `<a href="${esc(folderUrl)}" target="_blank" rel="noopener">${icon('cloud')}<span>Cartella Gofile</span></a>` : ''}
      ${cloudUrl && !folderUrl ? `<a href="${esc(cloudUrl)}" target="_blank" rel="noopener">${icon('cloud')}<span>Ultima copia cloud</span></a>` : ''}
      ${publicUrl ? `<a href="${esc(publicUrl)}" target="_blank" rel="noopener">${icon('external')}<span>Apri sorgente</span></a>` : ''}
      <button class="danger-menu" type="button" data-action="delete" data-id="${source.id}">${icon('trash')}<span>Archivia sorgente</span></button>
    </div></details>`;
  }

  controlRoomPreviewMarkup = function controlRoomPreviewMarkupProduct(profile, wall = false) {
    const source = profile.preview_source || profile.source;
    const updated = source?.preview_updated_at;
    const previewEnabled = !document.hidden && activeView === 'dashboard' && profile.recording;
    const previewUrl = previewEnabled && source?.preview_url ? `${source.preview_url}?v=${timestamp(updated) || 0}` : '';
    const cover = safeUrl(source?.cover_thumbnail_url || '');
    const unavailableLabel = {private:'Privata',tipjar:'Tip-jar',restricted:'Limitata'}[source?.pause_reason] || '';
    const state = profile.recording ? 'REC' : profile.live ? 'LIVE' : 'OFFLINE';
    const visual = previewUrl ? `<img data-live-preview src="${esc(previewUrl)}" alt="Preview live di ${esc(profile.display_name)}" loading="lazy" decoding="async">`
      : cover ? `<img class="cr-preview-cover" src="${esc(cover)}" alt="Copertina di ${esc(profile.display_name)}" loading="lazy" decoding="async">`
      : `<div class="cr-preview-placeholder"><span>${esc(controlRoomInitials(profile.display_name))}</span></div>`;
    return `<div class="cr-preview ${profile.blocked && !profile.unavailable ? 'attention' : ''} ${wall ? 'wall' : ''}">${visual}<div class="cr-preview-state"><span class="state-dot ${profile.recording ? 'recording' : profile.live ? 'live' : ''}"></span><strong>${state}</strong>${unavailableLabel ? `<span>${esc(unavailableLabel)}</span>` : profile.blocked ? '<span>Non registrata</span>' : ''}</div>${updated ? `<time>${esc(ago(updated))}</time>` : ''}</div>`;
  };

  function processButton(profile) {
    const session = profile?.pulse_session;
    const needs = session && (Number(session.processing_count || 0) > 0 || Number(session.file_count || 0) > Number(session.uploaded_count || 0));
    if (!needs) return '';
    const current = statusData?.worker?.processing_current;
    const busy = current && Number(current.source_id) === Number(profile.source?.id);
    return `<button class="btn compact ${busy ? '' : 'secondary'}" data-process-now="${profile.source.id}" type="button" ${busy ? 'disabled' : ''}>${icon('upload','button-icon')}<span>${busy ? 'Elaborazione' : 'Finalizza'}</span></button>`;
  }

  controlRoomLiveCard = function controlRoomLiveCardProduct(profile, wall = false) {
    const source = profile.source;
    const publicUrl = safeUrl(source.source_url);
    const captureSourceId = Number(profile.active?.source_id || 0);
    const actions = wall ? '' : `<div class="cr-card-actions">
      ${actionButton('star', profile.focus ? 'Rimuovi dal Focus' : 'Aggiungi al Focus', `data-focus-toggle="${source.id}" aria-pressed="${profile.focus}"`, profile.focus ? 'selected' : '')}
      ${actionButton('users','Apri profilo',`data-action="profile" data-id="${source.id}"`)}
      ${actionButton('archive','Apri archivio',`data-action="archive" data-id="${source.id}"`)}
      ${captureSourceId ? actionButton('play','Riproduci registrazione locale',`data-local-video="/api/sources/${captureSourceId}/capture" data-local-title="${esc(`${profile.display_name} · REC locale`)}"`) : ''}
      ${publicUrl ? actionLink('external','Apri sorgente',publicUrl,'target="_blank" rel="noopener"') : ''}
      ${profile.blocked && source.pause_reason === 'global' ? `<button class="btn primary compact" data-cr-resume-global type="button">${icon('record','button-icon')}<span>Riprendi REC</span></button>` : profile.blocked && source.pause_reason === 'source' ? `<button class="btn primary compact" data-action="toggle" data-id="${source.id}" type="button">${icon('record','button-icon')}<span>Avvia REC</span></button>` : ''}
      ${processButton(profile)}
      ${sourceMoreMenu(profile)}
    </div>`;
    const warning = profile.last_error ? `<div class="inline-alert">${icon('warning','mini-icon')}<span>${esc(profile.last_error)}</span></div>` : '';
    const providers = profile.providers.join(' · ');
    const accounts = profile.rows.length > 1 ? ` · ${profile.rows.length} account` : '';
    return `<article class="cr-live-card ${profile.blocked && !profile.unavailable ? 'blocked' : ''} ${profile.focus ? 'focus' : ''}">
      ${controlRoomPreviewMarkup(profile, wall)}
      <div class="cr-live-body"><div class="cr-live-head"><div>${creatorLinkMarkup(source.id, profile.display_name, 'cr-live-name')}<div class="cr-live-provider">${esc(providers + accounts)}</div></div><strong class="cr-live-state">${esc(controlRoomStatusText(profile))}</strong></div>${warning}${actions}</div>
    </article>`;
  };

  controlRoomCompactRow = function controlRoomCompactRowProduct(profile, focus = false) {
    const source = profile.source;
    const status = profile.last_error ? 'Errore' : source.enabled ? `Offline · ${ago(profile.last_seen_live_at)}` : 'In pausa';
    return `<article class="cr-compact-row ${focus ? 'focus' : ''}">
      ${actionButton('star', profile.focus ? 'Rimuovi dal Focus' : 'Aggiungi al Focus', `data-focus-toggle="${source.id}" aria-pressed="${profile.focus}"`, profile.focus ? 'selected' : '')}
      <div class="cr-compact-main">${creatorLinkMarkup(source.id, profile.display_name, 'cr-compact-name')}<span>${esc(profile.providers.join(' · '))}</span></div>
      <span class="cr-compact-status ${profile.last_error ? 'danger-text' : ''}">${esc(status)}</span>
      ${actionButton('refresh','Controlla ora',`data-action="check" data-id="${source.id}"`)}
      ${actionButton('users','Apri profilo',`data-action="profile" data-id="${source.id}"`)}
      ${sourceMoreMenu(profile)}
    </article>`;
  };

  controlRoomEndedCard = function controlRoomEndedCardProduct(session) {
    const source = sources.find(row => Number(row.id) === Number(session.representative_source_id)) || sources.find(row => Number(row.profile_id) === Number(session.profile_id));
    const cover = safeUrl(source?.cover_thumbnail_url || '');
    const saved = session.state === 'saved', processing = session.state === 'processing';
    const state = saved ? 'Salvata' : processing ? 'Elaborazione' : session.state === 'missed' ? 'Non registrata' : session.file_count ? `Upload ${session.uploaded_count}/${session.file_count}` : 'Terminata';
    const meta = [duration(session.duration_seconds), session.file_count ? `${session.file_count} file` : '', session.total_bytes ? humanBytes(session.total_bytes) : '', session.file_count ? `${Math.round(Number(session.coverage_percent)||0)}% REC` : ''].filter(Boolean).join(' · ');
    const local = (session.recordings || []).find(item => item.local_url);
    return `<article class="cr-ended-card ${saved ? 'saved' : ''} ${processing ? 'processing' : ''} ${session.state === 'missed' ? 'missed' : ''}"><div class="cr-ended-cover">${cover ? `<img src="${esc(cover)}" alt="">` : `<span>${esc(controlRoomInitials(session.display_name))}</span>`}</div><div class="cr-ended-main"><div>${creatorLinkMarkup(session.representative_source_id,session.display_name,'cr-ended-name')}<small>${esc(meta)}</small></div><div class="cr-ended-state"><strong>${esc(state)}</strong><span>${esc(ago(session.ended_at))}</span>${local ? actionButton('play','Anteprima locale',`data-local-video="${esc(local.local_url)}" data-local-title="${esc(`${session.display_name} · copia locale`)}"`) : ''}</div></div></article>`;
  };

  renderSources = function renderSourcesProduct() {
    const root = $('#sources');
    if (!root) return;
    const profiles = controlRoomProfileRows().filter(dashboardProfileMatches);
    const live = profiles.filter(p => p.live).sort((a,b) => controlRoomPriority(b)-controlRoomPriority(a) || a.display_name.localeCompare(b.display_name,'it'));
    const offlineFocus = profiles.filter(p => !p.live && p.focus).sort((a,b) => timestamp(b.last_seen_live_at)-timestamp(a.last_seen_live_at));
    const offline = profiles.filter(p => !p.live && !p.focus).sort((a,b) => Number(!!b.last_error)-Number(!!a.last_error) || timestamp(b.last_seen_live_at)-timestamp(a.last_seen_live_at));
    const blocked = live.filter(p => p.blocked && !p.unavailable).length;
    $('#sourceCount').textContent = profiles.length;
    if (!profiles.length) {
      root.innerHTML = `<div class="empty">${sources.length ? 'Nessuna sorgente corrisponde ai filtri.' : 'Nessuna sorgente configurata.'}</div>`;
      renderControlRoomWall([]);
      return;
    }
    const pulse = typeof controlRoomPulseMarkup === 'function' ? controlRoomPulseMarkup() : '';
    const recent = typeof controlRoomRecentEnded === 'function' ? controlRoomRecentEnded(profiles) : [];
    root.innerHTML = `<div class="monitor-summary"><div><span class="state-dot ${live.length ? 'live' : ''}"></span><strong>${live.length} live</strong><span>${blocked ? `${blocked} da controllare` : live.length ? 'Copertura REC attiva' : 'Nessuna live'}</span></div><button class="btn secondary compact" data-live-wall type="button" ${live.length ? '' : 'disabled'}>${icon('grid','button-icon')}<span>Live wall</span></button></div>
      ${pulse}
      <section class="monitor-section"><header><h3>Live</h3><span>${live.length}</span></header>${live.length ? `<div class="cr-live-grid">${live.map(p => controlRoomLiveCard(p)).join('')}</div>` : '<div class="empty compact">Nessuna creator live.</div>'}</section>
      ${recent.length ? `<section class="monitor-section"><header><h3>Appena terminate</h3><span>${recent.length}</span></header><div class="cr-ended-list">${recent.map(controlRoomEndedCard).join('')}</div></section>` : ''}
      ${offlineFocus.length ? `<section class="monitor-section"><header><h3>Focus</h3><span>${offlineFocus.length}</span></header><div class="cr-compact-list">${offlineFocus.map(p => controlRoomCompactRow(p,true)).join('')}</div></section>` : ''}
      <details id="controlRoomOffline" class="cr-offline" ${controlRoomOfflineOpen ? 'open' : ''}><summary><span>Altre creator</span><span>${offline.length}</span>${icon('chevron-down','mini-icon')}</summary><div class="cr-compact-list">${offline.length ? offline.map(p => controlRoomCompactRow(p)).join('') : '<div class="empty compact">Nessuna.</div>'}</div></details>`;
    renderControlRoomWall(profiles);
  };

  function categoryTags(profile) {
    const items = [...(profile.categories || []), ...(profile.collections || [])];
    return items.slice(0,3).map(item => `<span class="library-tag" style="--tag:${esc(item.color)}">${esc(item.name)}</span>`).join('') + (items.length > 3 ? `<span class="library-tag muted-tag">+${items.length-3}</span>` : '');
  }

  renderLibrary = function renderLibraryProduct() {
    renderLibraryCounts();
    const visible = filteredProfiles();
    const root = $('#librarySources');
    root.className = `library-grid ${libraryMode === 'grid' ? 'grid' : 'list'}`;
    $('#libraryGridBtn').classList.toggle('active',libraryMode==='grid');
    $('#libraryListBtn').classList.toggle('active',libraryMode==='list');
    $('#libraryGridBtn').setAttribute('aria-pressed',String(libraryMode==='grid'));
    $('#libraryListBtn').setAttribute('aria-pressed',String(libraryMode==='list'));
    $('#libraryResultsMeta').textContent = `${visible.length} di ${libraryProfiles.length}`;
    if (!visible.length) { root.innerHTML='<div class="empty">Nessun risultato.</div>'; updateSelectionUi(visible); return; }
    root.innerHTML = visible.map(profile => {
      const checked=selectedProfiles.has(profile.profile_id), cover=safeUrl(profile.cover_thumbnail_url);
      const statusText=statusLabel(profile.status);
      return `<article class="library-card ${checked?'selected':''} ${profile.favorite?'favorite':''}">
        <label class="library-select"><input type="checkbox" data-profile-select="${profile.profile_id}" ${checked?'checked':''}><span class="sr-only">Seleziona ${esc(profile.display_name)}</span></label>
        <button class="favorite-btn ${profile.favorite?'active':''}" data-lib-action="favorite" data-id="${profile.representative_id}" type="button" aria-label="${profile.favorite?'Rimuovi dai preferiti':'Aggiungi ai preferiti'}" data-tooltip="Preferiti">${icon('star')}</button>
        <button class="library-cover" data-lib-action="profile" data-id="${profile.representative_id}" type="button">${cover?`<img src="${esc(cover)}" alt="" loading="lazy">`:`<span>${esc(controlRoomInitials(profile.display_name))}</span>`}</button>
        <div class="library-card-body"><div class="library-card-head"><div><h3>${creatorLinkMarkup(profile.representative_id,profile.display_name)}</h3><p>${esc(profile.provider_labels.join(' · '))}</p></div><span class="source-status ${esc(profile.status)}">${esc(statusText)}</span></div>
        <div class="library-tags">${categoryTags(profile)}</div>
        <div class="library-stats"><span><strong>${profile.recording_count}</strong> file</span><span><strong>${esc(humanBytes(profile.total_bytes))}</strong></span><span><strong>${esc(duration(profile.total_duration_seconds))}</strong></span><span><strong>${profile.uploaded_count}</strong> cloud</span></div>
        <div class="library-recency">Ultima REC ${profile.last_recording_at?esc(ago(profile.last_recording_at)):'mai'}</div>
        <div class="library-actions">${actionButton('users','Apri profilo',`data-lib-action="profile" data-id="${profile.representative_id}"`)}${actionButton('archive','Apri archivio',`data-lib-action="archive" data-id="${profile.representative_id}"`)}${profile.archived?actionButton('rotate-ccw','Ripristina',`data-lib-action="restore" data-id="${profile.representative_id}"`):''}${actionButton('trash','Elimina creator',`data-lib-action="delete-profile" data-id="${profile.representative_id}"`,'danger')}</div></div>
      </article>`;
    }).join('');
    updateSelectionUi(visible);
  };

  function attentionReasonsProduct(recording) {
    const reasons=[];
    if(recording.has_video===false) reasons.push('Video assente');
    if(recording.has_audio===false) reasons.push('Audio assente');
    if(recording.integrity_status==='failed'||recording.upload_status==='integrity_failed') reasons.push('Integrità fallita');
    if(recording.upload_status==='failed') reasons.push('Upload fallito');
    if(recording.upload_status==='waiting_config') reasons.push('Configurazione cloud mancante');
    if(!reasons.length&&String(recording.last_error||'').trim()) reasons.push('Errore registrazione');
    return [...new Set(reasons)];
  }

  function archiveRecordRow(recording) {
    const source=archiveSourceFor(recording), creator=source?.display_name||recording.source_name;
    const remote=safeUrl(recording.remote_url), collection=safeUrl(recording.collection_url), preview=safeUrl(recording.thumbnail_url);
    const reasons=attentionReasonsProduct(recording);
    const primary = remote ? actionLink('external','Apri cloud',remote,'target="_blank" rel="noopener"') : recording.local_available ? actionButton('play','Riproduci',`data-rec-action="preview" data-id="${recording.id}"`) : '';
    return `<article class="rec-card ${reasons.length?'lv-attention':''}" data-recording-id="${recording.id}">
      <button class="archive-thumb ${preview?'':'empty'}" type="button" data-rec-action="preview" data-id="${recording.id}" aria-label="Anteprima ${esc(recording.filename)}" ${recording.local_available?'':'disabled'}>${preview?`<img src="${esc(preview)}" alt="" loading="lazy">`:icon('play')}</button>
      <div class="archive-identity"><strong>${creatorLinkMarkup(source?.id||0,creator)}</strong><span title="${esc(recording.filename)}">${esc(recording.filename)}</span><small>${esc(dateText(recording.started_at))}</small></div>
      <div class="archive-health">${reasons.length?`<span class="attention-label">${icon('warning','mini-icon')}<span>${esc(reasons.join(' · '))}</span></span>`:recordingStreamMarkup(recording)}<span class="integrity ${esc(recording.integrity_status)}">${recording.integrity_status==='passed'?'Integro':esc(recording.integrity_status||'—')}</span></div>
      <div class="archive-number"><strong>${esc(duration(recording.duration_seconds))}</strong><span>${esc(recording.size_human)}</span></div>
      <div class="archive-upload"><span class="upload-status ${esc(recording.upload_status)}">${esc(uploadLabel(recording.upload_status))}</span><small>${esc(recording.upload_provider||'')}</small></div>
      <div class="rec-actions">${primary}${remote?actionButton('copy','Copia link',`data-rec-action="copy-cloud" data-id="${recording.id}"`):''}<details class="row-more"><summary class="icon-button" aria-label="Altre azioni" data-tooltip="Altre azioni">${icon('more')}</summary><div class="row-menu align-right">${recording.local_available?`<button data-rec-action="preview" data-id="${recording.id}" type="button">${icon('play')}<span>Anteprima locale</span></button><a href="/api/recordings/${recording.id}/download">${icon('download')}<span>Scarica</span></a>`:''}${collection?`<a href="${esc(collection)}">${icon('archive')}<span>Archivio camera</span></a>`:''}${recording.local_available&&recording.integrity_status==='passed'?`<button data-rec-action="upload-now" data-id="${recording.id}" type="button">${icon('cloud-upload')}<span>Upload ora</span></button>`:''}${recording.local_available?`<button data-rec-action="integrity" data-id="${recording.id}" type="button">${icon('check')}<span>Ricontrolla integrità</span></button>`:''}${recording.local_available&&recording.container_format!=='mp4'?`<button data-rec-action="convert" data-id="${recording.id}" type="button">${icon('refresh')}<span>Converti MP4</span></button>`:''}${recording.local_available?`<button class="danger-menu" data-rec-action="delete-local" data-id="${recording.id}" type="button">${icon('trash')}<span>Elimina copia locale</span></button>`:''}<button class="danger-menu" data-rec-action="delete-record" data-id="${recording.id}" type="button">${icon('trash')}<span>Elimina voce archivio</span></button></div></details></div>
    </article>`;
  }

  renderRecordings = function renderRecordingsProduct() {
    fillArchiveIntelControls();
    const toggle=$('.archive-filter-toggle');
    if(toggle&&!toggle.dataset.productDecorated){toggle.dataset.productDecorated='1';toggle.innerHTML=`${icon('filter','button-icon')}<span>Filtri</span>`;}
    const visible=recordings.filter(r=>recordingMatches(r)&&archiveIntelMatches(r));
    const root=$('#recordings');
    if(!visible.length){root.innerHTML='<div class="empty">Nessuna registrazione nei filtri.</div>';$('#recordingFooter').textContent=recordings.length?`${recordings.length} file caricati`:'';return;}
    const groups=archiveGroupRows(visible);
    root.innerHTML=groups.slice(0,archiveGroupLimit).map((group,index)=>`<details class="archive-group" ${index<3?'open':''}><summary><span><strong>${esc(group.label)}</strong><small>${esc(archiveGroupSummary(group))}</small></span>${icon('chevron-down','mini-icon')}</summary><div class="archive-table-head"><span>Registrazione</span><span>Integrità</span><span>Durata / dimensione</span><span>Cloud</span><span>Azioni</span></div><div class="archive-group-grid">${group.rows.map(archiveRecordRow).join('')}</div></details>`).join('');
    if(groups.length>archiveGroupLimit)root.insertAdjacentHTML('beforeend',`<button class="btn secondary archive-more" type="button" data-archive-more="1">Mostra altri ${Math.min(10,groups.length-archiveGroupLimit)}</button>`);
    const total=Number(statusData?.history?.recordings??recordings.length);
    $('#recordingFooter').textContent=`${visible.length} nei filtri · ${groups.length} gruppi · ${recordings.length} caricati`;
    $('#loadOlderRecordings').hidden=recordings.length<archiveLoadedLimit||(total>0&&total<=recordings.length);
  };

  const baseRenderProfile = renderProfile;
  renderProfile = function renderProfileProduct() {
    const result=baseRenderProfile();
    if(!profileData)return result;
    $$('#profileContent .favorite-toggle').forEach(button=>{
      const isFocus=button.hasAttribute('data-profile-focus');
      const active=button.getAttribute('aria-pressed')==='true';
      button.innerHTML=`${icon('star','button-icon')}<span>${isFocus?(active?'In Focus':'Aggiungi al Focus'):(active?'Preferita':'Preferiti')}</span>`;
    });
    $$('#profileContent a, #profileContent button').forEach(node=>{
      if(node.children.length)return;
      const text=node.textContent.trim();
      if(text.endsWith('\u2197')){node.textContent=text.replace(/\s*\u2197$/,'');node.insertAdjacentHTML('afterbegin',icon('external','button-icon'));}
      else if(text.startsWith('\u25b6')){node.textContent=text.replace(/^\u25b6\s*/, '');node.insertAdjacentHTML('afterbegin',icon('play','button-icon'));}
    });
    $$('#profileContent .local-capture > span').forEach(node=>{node.textContent=node.textContent.replace(/^\u25cf\s*/,'');});
    return result;
  };

  const baseRenderStatistics = renderStatistics;
  renderStatistics = function renderStatisticsProduct() {
    const result=baseRenderStatistics();
    $$('#statisticsLeaderboard .leader-live').forEach(node=>node.textContent='LIVE');
    return result;
  };

  const baseRefreshProduct = refresh;
  refresh = async function refreshProduct(options = {}) {
    await baseRefreshProduct(options);
    if(activeView==='dashboard')renderSources();
    if(activeView==='library')renderLibrary();
    if(activeView==='archive')renderRecordings();
    if(activeView==='statistics')renderStatistics();
  };

  function bindProductControls() {
    document.addEventListener('click', event=>{
      const settings=event.target.closest('[data-open-settings]');
      if(settings){event.preventDefault();$('#settingsBtn')?.click();}
      const logout=event.target.closest('[data-product-logout]');
      if(logout){event.preventDefault();$('#logoutBtn')?.click();}
    });
  }

  bindProductControls();
  if(!localStorage.getItem('livevault-library-view')){libraryMode='list';localStorage.setItem('livevault-library-view','list');}
  const initial=viewMeta[activeView]||viewMeta.dashboard;
  if($('#appViewTitle'))$('#appViewTitle').textContent=initial[0];
  if($('#appContext'))$('#appContext').textContent=initial[1];
})();
