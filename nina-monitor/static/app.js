(() => {
  const $ = selector => document.querySelector(selector);
  let pollTimer = null;
  let busy = false;
  let previewFrame = null;
  let previewPending = null;
  let lastSnapshot = null;
  let selectedFrameIndex = null;

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  }

  function num(value, digits = 0, suffix = '') {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(digits)}${suffix}` : '—';
  }

  function signed(value, digits = 1) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n > 0 ? '+' : ''}${n.toFixed(digits)}%` : '—';
  }

  function text(value, fallback = '—') {
    const raw = String(value ?? '').trim();
    return raw || fallback;
  }

  function when(value, withSeconds = false) {
    if (!value) return '—';
    const d = new Date(value);
    if (!Number.isFinite(d.getTime())) return '—';
    return d.toLocaleTimeString('it-IT', {hour: '2-digit', minute: '2-digit', ...(withSeconds ? {second: '2-digit'} : {})});
  }

  function statusClass(value) { return String(value || '').toLowerCase().replace(/[^a-z]+/g, '-').replace(/^-|-$/g, ''); }
  function fileName(frame) { return text(frame?.fileName, frame?.syntheticFile ? `synthetic_frame_${num(frame.frameIndex, 0)}.fits` : `frame #${num(frame?.frameIndex, 0)}`); }
  function isAttention(frame) {
    const s = String(frame?.status || '').toUpperCase();
    return s.includes('REJECT') || s === 'ERROR' || s === 'WARNING';
  }

  async function jsonFetch(url, options = {}) {
    const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin', ...options});
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) {
      const error = new Error(payload.error || `HTTP ${response.status}`);
      error.status = response.status; error.payload = payload; throw error;
    }
    return payload;
  }

  function showLogin(message = '') {
    $('#appView').hidden = true; $('#loginView').hidden = false;
    const error = $('#loginError'); error.hidden = !message; error.textContent = message;
    stopPolling(); setTimeout(() => $('#loginPassword')?.focus(), 0);
  }
  function showApp() { $('#loginView').hidden = true; $('#appView').hidden = false; startPolling(); }
  function startPolling() { stopPolling(); refresh(true); pollTimer = setInterval(() => refresh(false), 1000); }
  function stopPolling() { if (pollTimer) clearInterval(pollTimer); pollTimer = null; }

  function gridLines(width, height) {
    return [0, .25, .5, .75, 1].map(v => `<line class="gridline" x1="0" y1="${(v * height).toFixed(1)}" x2="${width}" y2="${(v * height).toFixed(1)}"/>`).join('');
  }
  function polyPoints(items, key, min, max, width, height) {
    const span = Math.max(.0001, max - min);
    return (items || []).map((item, index) => {
      const value = Number(item?.[key]); if (!Number.isFinite(value)) return null;
      const x = items.length <= 1 ? width : index / (items.length - 1) * width;
      const y = height - (value - min) / span * height;
      return `${Math.max(0, Math.min(width, x)).toFixed(1)},${Math.max(0, Math.min(height, y)).toFixed(1)}`;
    }).filter(Boolean).join(' ');
  }

  function chartAxisLabels(values, suffix = '') {
    return `<div class="chart-y-labels">${values.map(v => `<span>${esc(v)}${esc(suffix)}</span>`).join('')}</div>`;
  }

  function chartXLabels(items) {
    const list = Array.isArray(items) ? items : [];
    if (!list.length) return '<div class="chart-x-labels"><span>—</span></div>';
    const picks = [...new Set([0, Math.floor((list.length - 1) * .25), Math.floor((list.length - 1) * .5), Math.floor((list.length - 1) * .75), list.length - 1])];
    return `<div class="chart-x-labels">${picks.map(i => { const f = list[i] || {}; return `<span title="${esc(fileName(f))}">#${esc(num(f.frameIndex, 0))}</span>`; }).join('')}</div>`;
  }

  function chartHoverBands(items, width, height) {
    const list = Array.isArray(items) ? items : [];
    if (!list.length) return '';
    const step = width / Math.max(1, list.length - 1);
    return list.map((f, i) => {
      const x = list.length === 1 ? 0 : Math.max(0, i * step - step / 2);
      const w = list.length === 1 ? width : Math.min(width - x, step);
      const details = `${fileName(f)} · ${text(f.status)}\nQ ${num(f.quality,0)} · Conf ${num(f.confidence,0,'%')} · RMS ${num(f.guideRmsArcsec,2,'"')}\nStars Δ ${signed(f.starDeltaPercent)} · BG Δ ${signed(f.backgroundDeltaPercent)}\n${text(f.probableCause || f.reason, 'nessuna causa')}`;
      return `<rect class="chart-hit" x="${x.toFixed(1)}" y="0" width="${Math.max(1,w).toFixed(1)}" height="${height}"><title>${esc(details)}</title></rect>`;
    }).join('');
  }

  function renderGuide(guide, settings = {}) {
    guide = guide || {}; const series = Array.isArray(guide.series) ? guide.series : [];
    $('#guideTotalValue').textContent = num(guide.rmsTotalArcsec, 2, '"');
    $('#guideRaValue').textContent = num(guide.rmsRaArcsec, 2, '"');
    $('#guideDecValue').textContent = num(guide.rmsDecArcsec, 2, '"');
    $('#guideMaxValue').textContent = num(guide.maxExcursionArcsec, 2, '"');
    $('#guideSamplesValue').textContent = num(guide.samples, 0);
    const age = guide.latestUtc ? Math.max(0, (Date.now() - Date.parse(guide.latestUtc)) / 1000) : Infinity;
    const fresh = Boolean(guide.hasData) && age < 6;
    $('#guideValue').textContent = guide.hasData ? num(guide.rmsTotalArcsec, 2, '"') : '—';
    $('#guideDetail').textContent = guide.hasData ? (fresh ? `PHD2 live · ultimo step ${age.toFixed(1)} s fa` : `PHD2 fermo · ultimo step ${age.toFixed(0)} s fa`) : 'Nessun GuideEvent PHD2 negli ultimi 20 s';
    const plot = $('#guidePlot');
    if (series.length < 2) { plot.innerHTML = '<div class="plot-empty"><div><b>PHD2 live non sta inviando campioni</b><span>Il pannello non è stato rimosso: comparirà appena N.I.N.A. riceve nuovi GuideEvent.</span></div></div>'; return; }
    const values = series.flatMap(p => [Number(p.raArcsec), Number(p.decArcsec)]).filter(Number.isFinite);
    const excursion = Number(settings.excursionThreshold);
    const observed = Math.max(.5, ...values.map(Math.abs));
    const maxAbs = Math.max(observed * 1.15, Number.isFinite(excursion) ? excursion * 1.15 : .5);
    const width = 1000, height = 180;
    const yFor = value => height - ((value + maxAbs) / (2 * maxAbs)) * height;
    const limitLines = Number.isFinite(excursion) && excursion > 0 ? `<line class="limit-line" x1="0" y1="${yFor(excursion).toFixed(1)}" x2="${width}" y2="${yFor(excursion).toFixed(1)}"/><line class="limit-line" x1="0" y1="${yFor(-excursion).toFixed(1)}" x2="${width}" y2="${yFor(-excursion).toFixed(1)}"/>` : '';
    const legend = `<div class="chart-legend"><span><i class="legend ra"></i>RA</span><span><i class="legend dec"></i>DEC</span>${Number.isFinite(excursion) ? `<span class="limit-key">Escursione ±${esc(num(excursion,2,'"'))}</span>` : ''}<b>Y = arcsec</b></div>`;
    const body = `<div class="chart-body"><div class="chart-y-title">arcsec</div>${chartAxisLabels([maxAbs.toFixed(1),(maxAbs/2).toFixed(1),'0.0',(-maxAbs/2).toFixed(1),(-maxAbs).toFixed(1)])}<div class="chart-canvas"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="PHD2 RA DEC live"><g>${gridLines(width, height)}</g><line class="zero-line" x1="0" y1="${height / 2}" x2="${width}" y2="${height / 2}"/>${limitLines}<polyline class="line-ra" points="${polyPoints(series, 'raArcsec', -maxAbs, maxAbs, width, height)}"/><polyline class="line-dec" points="${polyPoints(series, 'decArcsec', -maxAbs, maxAbs, width, height)}"/></svg><div class="chart-x-labels"><span>−20 s</span><span>−15 s</span><span>−10 s</span><span>−5 s</span><span>adesso</span></div></div></div>`;
    plot.innerHTML = legend + body;
  }

  function renderTrend(frames, settings = {}) {
    const plot = $('#trendPlot'), recent = (Array.isArray(frames) ? frames : []).slice(-80);
    if (recent.length < 2) { plot.innerHTML = '<div class="plot-empty"><div><b>Raccolta dati…</b><span>Servono almeno due frame QSM.</span></div></div>'; return; }
    const width = 1000, height = 200;
    const rmsValues = recent.map(f => Number(f.guideRmsArcsec)).filter(Number.isFinite);
    const configuredRms = Number(settings.maxGuideRms);
    const rmsMax = Math.max(1, ...rmsValues, Number.isFinite(configuredRms) ? configuredRms * 1.25 : 0);
    const markers = recent.map((f, i) => {
      if (!isAttention(f)) return '';
      const x = recent.length <= 1 ? width : i / (recent.length - 1) * width;
      return `<circle class="trend-marker ${statusClass(f.status)}" cx="${x.toFixed(1)}" cy="${height - 7}" r="5"><title>${esc(fileName(f))} · ${esc(text(f.status))} · ${esc(text(f.probableCause || f.reason))}</title></circle>`;
    }).join('');
    const thresholdY = Number.isFinite(configuredRms) && configuredRms > 0 && configuredRms <= rmsMax ? height - configuredRms / rmsMax * height : null;
    const threshold = thresholdY == null ? '' : `<line class="rms-limit-line" x1="0" y1="${thresholdY.toFixed(1)}" x2="${width}" y2="${thresholdY.toFixed(1)}"><title>Limite Guide RMS ${esc(num(configuredRms,2,'"'))}</title></line>`;
    const legend = `<div class="chart-legend"><span><i class="legend q"></i>Quality <b>0–100</b></span><span><i class="legend c"></i>Confidence <b>0–100%</b></span><span><i class="legend rms"></i>Guide RMS <b>arcsec</b></span>${thresholdY == null ? '' : `<span class="limit-key">Limite RMS ${esc(num(configuredRms,2,'"'))}</span>`}</div>`;
    const leftAxis = chartAxisLabels(['100','75','50','25','0']);
    const rightAxis = chartAxisLabels([rmsMax.toFixed(2),(rmsMax*.75).toFixed(2),(rmsMax*.5).toFixed(2),(rmsMax*.25).toFixed(2),'0.00'], '"');
    const svg = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="Trend Quality Confidence Guide RMS"><g>${gridLines(width, height)}</g>${threshold}<polyline class="line-q" points="${polyPoints(recent, 'quality', 0, 100, width, height)}"/><polyline class="line-c" points="${polyPoints(recent, 'confidence', 0, 100, width, height)}"/><polyline class="line-rms" points="${polyPoints(recent, 'guideRmsArcsec', 0, rmsMax, width, height)}"/>${markers}${chartHoverBands(recent,width,height)}</svg>`;
    plot.innerHTML = `${legend}<div class="chart-dual-axis"><div class="axis-caption left">Q / CONF.</div>${leftAxis}<div class="chart-canvas">${svg}${chartXLabels(recent)}<div class="chart-x-title">FRAME QSM · passa il mouse per file e valori</div></div>${rightAxis}<div class="axis-caption right">RMS</div></div>`;
  }

  function inspectorMetric(label, value, wide = false) { return `<div class="inspector-metric${wide ? ' wide' : ''}"><span>${esc(label)}</span><b>${esc(value)}</b></div>`; }
  function renderFrameInspector(frame) {
    const host = $('#frameInspector');
    if (!frame) { host.className = 'inspector-empty'; host.innerHTML = 'Seleziona un frame dalla cronologia o dalla coda reject per vedere file, causa e metriche complete.'; return; }
    host.className = 'inspector';
    const cause = text(frame.probableCause, 'Nessuna causa automatica');
    const rawReason = text(frame.reason, '—');
    host.innerHTML = `<div class="inspector-file"><div><span>FILE</span><strong title="${esc(fileName(frame))}">${esc(fileName(frame))}</strong><small>${esc(text(frame.source))}${frame.sequenceTitle ? ` · ${esc(frame.sequenceTitle)}` : ''}</small></div><span class="pill ${statusClass(frame.status)}">${esc(text(frame.status))}</span></div>
      <div class="inspector-reason"><b>${esc(cause)}</b><span>${esc(rawReason)}</span></div>
      <div class="inspector-grid">
        ${inspectorMetric('Quality', num(frame.quality, 0))}${inspectorMetric('Confidence', num(frame.confidence, 0, '%'))}${inspectorMetric('Guide RMS', num(frame.guideRmsArcsec, 2, '"'))}${inspectorMetric('Max excursion', num(frame.maxGuideExcursionArcsec, 2, '"'))}
        ${inspectorMetric('Guide pattern', text(frame.guidePattern))}${inspectorMetric('Stars', num(frame.stars, 0))}${inspectorMetric('Stars baseline', num(frame.starBaseline, 0))}${inspectorMetric('Stars Δ', signed(frame.starDeltaPercent))}
        ${inspectorMetric('Background', num(frame.background, 1))}${inspectorMetric('BG baseline', num(frame.backgroundBaseline, 1))}${inspectorMetric('BG Δ', signed(frame.backgroundDeltaPercent))}${inspectorMetric('File action', text(frame.fileDisposition))}
        ${inspectorMetric('Target / filter', `${text(frame.target)} · ${text(frame.filter)}`, true)}${inspectorMetric('Exposure', num(frame.exposureSeconds, 1, ' s'))}${inspectorMetric('Gain / bin', `G${num(frame.gain, 0)} · ${num(frame.binX,0)}×${num(frame.binY,0)}`)}
      </div>`;
    $('#inspectorHint').textContent = `frame #${num(frame.frameIndex, 0)} · ${when(frame.timestampUtc, true)}`;
  }

  function frameRow(frame, attention = false) {
    const idx = Number(frame.frameIndex), selected = idx === selectedFrameIndex ? ' selected' : '';
    const cls = `${statusClass(frame.status)}${selected}`;
    if (attention) return `<tr class="${cls}" data-frame-id="${Number.isFinite(idx) ? idx : ''}"><td>${esc(when(frame.timestampUtc))}</td><td class="filename" title="${esc(fileName(frame))}">${esc(fileName(frame))}</td><td><span class="table-status ${statusClass(frame.status)}">${esc(text(frame.status))}</span></td><td>${esc(num(frame.quality,0))}</td><td>${esc(num(frame.confidence,0,'%'))}</td><td>${esc(num(frame.guideRmsArcsec,2,'"'))}</td><td>${esc(signed(frame.starDeltaPercent))}</td><td>${esc(signed(frame.backgroundDeltaPercent))}</td><td class="cause" title="${esc(text(frame.reason || frame.probableCause))}">${esc(text(frame.probableCause))}</td><td>${esc(text(frame.fileDisposition))}</td></tr>`;
    return `<tr class="${cls}" data-frame-id="${Number.isFinite(idx) ? idx : ''}"><td>${esc(num(idx,0))}</td><td>${esc(when(frame.timestampUtc))}</td><td class="filename" title="${esc(fileName(frame))}">${esc(fileName(frame))}</td><td>${esc(text(frame.target))}</td><td>${esc(text(frame.filter))}</td><td><span class="table-status ${statusClass(frame.status)}">${esc(text(frame.status))}</span></td><td>${esc(num(frame.quality,0))}</td><td>${esc(num(frame.confidence,0,'%'))}</td><td>${esc(num(frame.guideRmsArcsec,2,'"'))}</td><td>${esc(text(frame.guidePattern))}</td><td>${esc(signed(frame.starDeltaPercent))}</td><td>${esc(signed(frame.backgroundDeltaPercent))}</td><td class="cause" title="${esc(text(frame.reason || frame.probableCause))}">${esc(text(frame.probableCause))}</td></tr>`;
  }

  function renderTables(frames) {
    const list = Array.isArray(frames) ? frames : [];
    const attention = list.filter(isAttention).slice(-24).reverse();
    $('#rejectedTableBody').innerHTML = attention.length ? attention.map(f => frameRow(f, true)).join('') : '<tr class="empty-row"><td colspan="10">Nessun reject/warning/error recente.</td></tr>';
    const history = list.slice(-80).reverse();
    $('#frameHistoryBody').innerHTML = history.length ? history.map(f => frameRow(f, false)).join('') : '<tr class="empty-row"><td colspan="13">Nessun frame QSM disponibile.</td></tr>';
  }

  function renderEvents(frames) {
    const host = $('#eventList');
    const interesting = (Array.isArray(frames) ? frames : []).filter(isAttention).slice(-8).reverse();
    if (!interesting.length) { host.innerHTML = '<div class="event"><time>—</time><b>NESSUNO</b><span>Nessun warning/reject/error recente.</span></div>'; return; }
    host.innerHTML = interesting.map(frame => `<div class="event ${statusClass(frame.status)}" data-frame-id="${esc(frame.frameIndex)}"><time>${esc(when(frame.timestampUtc))}</time><b>${esc(text(frame.status))}</b><strong class="event-file">${esc(fileName(frame))}</strong><span title="${esc(text(frame.reason || frame.probableCause))}">${esc(text(frame.probableCause || frame.reason, 'Nessun dettaglio'))}</span></div>`).join('');
  }

  function selectFrame(frameIndex) {
    const frames = Array.isArray(lastSnapshot?.frames) ? lastSnapshot.frames : [];
    const frame = frames.find(f => Number(f.frameIndex) === Number(frameIndex));
    if (!frame) return;
    selectedFrameIndex = Number(frame.frameIndex); renderFrameInspector(frame); renderTables(frames);
  }

  function requestPreview(frame, syntheticMode) {
    const frameId = Number(frame?.frameIndex);
    const now = Date.now();
    const probeKey = syntheticMode ? `real-${Math.floor(now / 5000)}` : (Number.isFinite(frameId) && frameId > 0 ? String(frameId) : 'latest');
    if (previewPending === probeKey || previewFrame === probeKey) return;
    previewPending = probeKey;
    const image = $('#previewImage'), placeholder = $('#previewPlaceholder'), state = $('#previewState');
    state.textContent = syntheticMode ? 'Verifico l’ultimo LIGHT reale ricevuto da N.I.N.A.…' : `Carico preview ${fileName(frame)}…`;
    const probe = new Image();
    probe.onload = () => {
      previewFrame = probeKey; previewPending = null; image.src = probe.src; image.hidden = false; placeholder.hidden = true;
      state.textContent = syntheticMode ? 'Ultimo LIGHT reale ricevuto da N.I.N.A. · indipendente dal Synthetic Lab' : `${fileName(frame)} · ${text(frame.filter, 'senza filtro')} · ${num(frame.exposureSeconds, 1, ' s')}`;
    };
    probe.onerror = () => {
      previewPending = null;
      if (image.hidden || !image.getAttribute('src')) { image.hidden = true; placeholder.hidden = false; }
      state.textContent = syntheticMode ? 'Nessun LIGHT reale disponibile da quando QSM è stato avviato · la preview live resta attiva' : 'Preview non ancora pronta · nuovo tentativo automatico';
    };
    probe.src = `api/preview.jpg?t=${now}${syntheticMode ? '&source=latest-real' : `&frame=${encodeURIComponent(frameId)}`}`;
  }

  function render(payload) {
    const link = $('#linkState'), banner = $('#statusBanner'), empty = $('#emptyState'), dashboard = $('#dashboard');
    link.className = `link-state ${payload.reachable ? 'online' : payload.configured ? 'warn' : ''}`;
    link.querySelector('b').textContent = payload.reachable ? `QSM online · ${num(payload.latencyMs, 0, ' ms')}` : payload.configured ? 'QSM non raggiungibile' : 'Da configurare';

    if (!payload.reachable || !payload.snapshot) {
      banner.classList.remove('live'); $('#bannerTitle').textContent = payload.reachable ? 'QSM collegato · nessuna sessione attiva' : 'NINA non disponibile'; $('#bannerText').textContent = payload.message || '—';
      empty.hidden = false; dashboard.hidden = true; $('#emptyTitle').textContent = payload.configured ? 'PC NINA non raggiungibile' : 'Collegamento NINA da configurare'; $('#emptyText').textContent = payload.message || 'Configura QSM sul PC NINA e riavvia il container.'; return;
    }

    empty.hidden = true; dashboard.hidden = false;
    const snapshot = payload.snapshot || {}, summary = snapshot.summary || {}, frame = snapshot.currentFrame || {}, guide = snapshot.guidingLive || {}, mode = snapshot.mode || {};
    lastSnapshot = snapshot;
    const syntheticMode = Boolean(mode.syntheticMode); dashboard.classList.toggle('synthetic-mode', syntheticMode);
    $('#modeTag').textContent = syntheticMode ? 'SYNTHETIC LAB' : 'LIVE'; $('#modeTag').classList.toggle('synthetic', syntheticMode);
    banner.classList.toggle('live', Boolean(payload.sessionActive));
    $('#bannerTitle').textContent = syntheticMode ? `Synthetic Lab${mode.syntheticSessionName ? ` · ${mode.syntheticSessionName}` : ''}` : payload.sessionActive ? 'Sessione NINA attiva' : 'QSM collegato · nessuna sessione attiva';
    $('#bannerText').textContent = syntheticMode ? `${text(mode.syntheticStatus, 'LAB')} · dati simulati isolati, nessuna camera/file reale` : (payload.message || '—');

    const quality = Number(frame.quality), confidence = Number(frame.confidence);
    $('#qualityValue').textContent = Number.isFinite(quality) ? quality.toFixed(0) : text(frame.status);
    $('#qualityDetail').textContent = `${text(frame.status)} · ${text(frame.probableCause, 'nessuna causa')}`;
    $('#confidenceValue').textContent = Number.isFinite(confidence) ? `${confidence.toFixed(0)}%` : '—';
    $('#rejectedValue').textContent = num(summary.rejected, 0); $('#acceptanceValue').textContent = `Acceptance ${num(summary.acceptanceRate, 1, '%')}`;
    $('#targetValue').textContent = text(frame.target); $('#filterValue').textContent = text(frame.filter); $('#exposureValue').textContent = num(frame.exposureSeconds, 1, ' s');
    const gain = Number(frame.gain), binX = Number(frame.binX), binY = Number(frame.binY);
    $('#gainBinValue').textContent = `${Number.isFinite(gain) && gain >= 0 ? `G${gain}` : 'G—'} · ${Number.isFinite(binX) && Number.isFinite(binY) ? `${binX}×${binY}` : '—'}`;
    $('#capturedValue').textContent = num(summary.captured, 0); $('#usableValue').textContent = num(summary.usable, 0); $('#starsDeltaValue').textContent = signed(frame.starDeltaPercent); $('#backgroundDeltaValue').textContent = signed(frame.backgroundDeltaPercent);
    $('#frameStatusValue').textContent = text(frame.status); $('#cameraValue').textContent = text(frame.camera); $('#latencyValue').textContent = num(payload.latencyMs, 0, ' ms'); $('#frameIndexValue').textContent = num(frame.frameIndex, 0);
    $('#currentFileValue').textContent = fileName(frame); $('#currentFileValue').title = fileName(frame);
    $('#currentFileMeta').textContent = `${text(frame.target)} · ${text(frame.filter)} · ${num(frame.exposureSeconds, 1, ' s')} · frame #${num(frame.frameIndex,0)}`;
    const statusPill = $('#currentStatusPill'); statusPill.textContent = text(frame.status); statusPill.className = `pill ${statusClass(frame.status)}`;
    $('#fileActionPill').textContent = text(frame.fileDisposition, syntheticMode ? 'SYNTHETIC' : 'FILE INVARIATO');

    const frames = Array.isArray(snapshot.frames) ? snapshot.frames : [];
    if (selectedFrameIndex == null || !frames.some(f => Number(f.frameIndex) === selectedFrameIndex)) selectedFrameIndex = Number(frame.frameIndex) || null;
    renderGuide(guide, snapshot.settings || {}); renderTrend(frames, snapshot.settings || {}); renderTables(frames); renderEvents(frames);
    renderFrameInspector(frames.find(f => Number(f.frameIndex) === selectedFrameIndex) || frame);
    requestPreview(frame, syntheticMode);
  }

  async function refresh(force = false) {
    if (busy || document.hidden) return; busy = true;
    try { render(await jsonFetch(`api/state${force ? '?force=1' : ''}`)); }
    catch (error) { if (error.status === 401) { showLogin('Sessione scaduta. Accedi di nuovo.'); return; } render({configured: true, reachable: false, sessionActive: false, message: `Monitor non disponibile: ${error.message}`, snapshot: null}); }
    finally { busy = false; }
  }

  document.addEventListener('click', event => {
    const row = event.target.closest('[data-frame-id]');
    if (row?.dataset.frameId) selectFrame(row.dataset.frameId);
  });

  $('#loginForm').addEventListener('submit', async event => {
    event.preventDefault(); const password = $('#loginPassword').value; const button = event.submitter || event.currentTarget.querySelector('button'); button.disabled = true;
    try { await jsonFetch('api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password})}); $('#loginPassword').value = ''; $('#loginError').hidden = true; showApp(); }
    catch (error) { const retry = Number(error.payload?.retryAfter); showLogin(Number.isFinite(retry) ? `Troppi tentativi. Riprova tra ${retry} s.` : error.message); }
    finally { button.disabled = false; }
  });
  $('#logoutButton').addEventListener('click', async () => { try { await jsonFetch('api/logout', {method: 'POST'}); } catch (_) {} showLogin(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !$('#appView').hidden) refresh(true); });
  (async () => { try { const session = await jsonFetch('api/session'); if (session.authenticated) showApp(); else showLogin(); } catch (_) { showLogin('Il servizio non risponde correttamente.'); } })();
})();
