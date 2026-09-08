(() => {
  const $ = selector => document.querySelector(selector);
  let pollTimer = null;
  let busy = false;
  let previewFrame = null;
  let previewPending = null;

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
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

  function statusClass(value) {
    return String(value || '').toLowerCase().replace(/[^a-z]+/g, '-');
  }

  async function jsonFetch(url, options = {}) {
    const response = await fetch(url, {
      cache: 'no-store',
      credentials: 'same-origin',
      ...options,
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) {
      const error = new Error(payload.error || `HTTP ${response.status}`);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function showLogin(message = '') {
    $('#appView').hidden = true;
    $('#loginView').hidden = false;
    const error = $('#loginError');
    error.hidden = !message;
    error.textContent = message;
    stopPolling();
    setTimeout(() => $('#loginPassword')?.focus(), 0);
  }

  function showApp() {
    $('#loginView').hidden = true;
    $('#appView').hidden = false;
    startPolling();
  }

  function startPolling() {
    stopPolling();
    refresh(true);
    pollTimer = setInterval(() => refresh(false), 1000);
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  function gridLines(width, height) {
    return [0, .25, .5, .75, 1]
      .map(v => `<line class="gridline" x1="0" y1="${(v * height).toFixed(1)}" x2="${width}" y2="${(v * height).toFixed(1)}"/>`)
      .join('');
  }

  function polyPoints(items, key, min, max, width, height) {
    const span = Math.max(.0001, max - min);
    return (items || []).map((item, index) => {
      const value = Number(item?.[key]);
      if (!Number.isFinite(value)) return null;
      const x = items.length <= 1 ? width : index / (items.length - 1) * width;
      const y = height - (value - min) / span * height;
      return `${Math.max(0, Math.min(width, x)).toFixed(1)},${Math.max(0, Math.min(height, y)).toFixed(1)}`;
    }).filter(Boolean).join(' ');
  }

  function renderGuide(guide) {
    guide = guide || {};
    const series = Array.isArray(guide.series) ? guide.series : [];
    $('#guideTotalValue').textContent = num(guide.rmsTotalArcsec, 2, '"');
    $('#guideRaValue').textContent = num(guide.rmsRaArcsec, 2, '"');
    $('#guideDecValue').textContent = num(guide.rmsDecArcsec, 2, '"');
    $('#guideMaxValue').textContent = num(guide.maxExcursionArcsec, 2, '"');
    $('#guideSamplesValue').textContent = num(guide.samples, 0);

    const age = guide.latestUtc ? Math.max(0, (Date.now() - Date.parse(guide.latestUtc)) / 1000) : Infinity;
    const fresh = Boolean(guide.hasData) && age < 6;
    $('#guideValue').textContent = guide.hasData ? num(guide.rmsTotalArcsec, 2, '"') : '—';
    $('#guideDetail').textContent = guide.hasData
      ? fresh ? `live · ultimo step ${age.toFixed(1)} s fa` : `dato guida fermo · ${age.toFixed(0)} s fa`
      : 'Nessun guide step negli ultimi 20 s';

    const plot = $('#guidePlot');
    if (series.length < 2) {
      plot.innerHTML = '<div class="plot-empty"><div><b>Guida non disponibile</b><span>In attesa dei GuideEvent di N.I.N.A.</span></div></div>';
      return;
    }

    const values = series.flatMap(p => [Number(p.raArcsec), Number(p.decArcsec)]).filter(Number.isFinite);
    const maxAbs = Math.max(.5, ...values.map(Math.abs)) * 1.15;
    const width = 1000, height = 180;
    const ra = polyPoints(series, 'raArcsec', -maxAbs, maxAbs, width, height);
    const dec = polyPoints(series, 'decArcsec', -maxAbs, maxAbs, width, height);
    plot.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true"><g>${gridLines(width, height)}</g><line class="zero-line" x1="0" y1="${height / 2}" x2="${width}" y2="${height / 2}"/><polyline class="line-ra" points="${ra}"/><polyline class="line-dec" points="${dec}"/></svg>`;
  }

  function renderTrend(frames) {
    const plot = $('#trendPlot');
    const recent = (Array.isArray(frames) ? frames : []).slice(-80);
    if (recent.length < 2) {
      plot.innerHTML = '<div class="plot-empty"><div><b>Raccolta dati…</b><span>Servono almeno due frame QSM.</span></div></div>';
      return;
    }

    const width = 1000, height = 200;
    const quality = polyPoints(recent, 'quality', 0, 100, width, height);
    const confidence = polyPoints(recent, 'confidence', 0, 100, width, height);
    const rmsValues = recent.map(f => Number(f.guideRmsArcsec)).filter(Number.isFinite);
    const rmsMax = Math.max(2, ...rmsValues, 2);
    const rms = polyPoints(recent, 'guideRmsArcsec', 0, rmsMax, width, height);
    plot.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true"><g>${gridLines(width, height)}</g><polyline class="line-q" points="${quality}"/><polyline class="line-c" points="${confidence}"/><polyline class="line-rms" points="${rms}"/></svg>`;
  }

  function renderEvents(frames) {
    const host = $('#eventList');
    const interesting = (Array.isArray(frames) ? frames : [])
      .filter(frame => !['ACCEPTED', 'LEARNING'].includes(String(frame.status || '').toUpperCase()))
      .slice(-8)
      .reverse();

    if (!interesting.length) {
      host.innerHTML = '<div class="event"><time>—</time><b>NESSUNO</b><span>Nessun warning/reject/error recente.</span></div>';
      return;
    }

    host.innerHTML = interesting.map(frame => {
      const when = frame.timestampUtc
        ? new Date(frame.timestampUtc).toLocaleTimeString('it-IT', {hour: '2-digit', minute: '2-digit'})
        : '—';
      const status = text(frame.status);
      const reason = text(frame.reason || frame.probableCause, 'Nessun dettaglio');
      return `<div class="event ${statusClass(status)}"><time>${esc(when)}</time><b>${esc(status)}</b><span title="${esc(reason)}">${esc(reason)}</span></div>`;
    }).join('');
  }

  function requestPreview(frame) {
    const frameId = Number(frame?.frameIndex);
    if (!Number.isFinite(frameId) || frameId <= 0 || previewFrame === frameId || previewPending === frameId) return;

    previewPending = frameId;
    const image = $('#previewImage');
    const placeholder = $('#previewPlaceholder');
    const state = $('#previewState');
    state.textContent = `Carico preview frame #${frameId}…`;

    image.onload = () => {
      previewFrame = frameId;
      previewPending = null;
      image.hidden = false;
      placeholder.hidden = true;
      state.textContent = `Preview frame #${frameId} · ${text(frame.filter, 'senza filtro')} · ${num(frame.exposureSeconds, 1, ' s')}`;
    };
    image.onerror = () => {
      previewPending = null;
      if (previewFrame === null) {
        image.hidden = true;
        placeholder.hidden = false;
      }
      state.textContent = 'Preview non ancora pronta · nuovo tentativo automatico';
    };
    image.src = `/api/preview.jpg?frame=${encodeURIComponent(frameId)}&t=${Date.now()}`;
  }

  function render(payload) {
    const link = $('#linkState');
    const banner = $('#statusBanner');
    const empty = $('#emptyState');
    const dashboard = $('#dashboard');

    link.className = `link-state ${payload.reachable ? 'online' : payload.configured ? 'warn' : ''}`;
    link.querySelector('b').textContent = payload.reachable
      ? `QSM online · ${num(payload.latencyMs, 0, ' ms')}`
      : payload.configured ? 'QSM non raggiungibile' : 'Da configurare';

    banner.classList.toggle('live', Boolean(payload.sessionActive));
    $('#bannerTitle').textContent = payload.sessionActive
      ? 'Sessione NINA attiva'
      : payload.reachable ? 'QSM collegato · nessuna sessione attiva' : 'NINA non disponibile';
    $('#bannerText').textContent = payload.message || '—';

    if (!payload.reachable || !payload.snapshot) {
      empty.hidden = false;
      dashboard.hidden = true;
      $('#emptyTitle').textContent = payload.configured ? 'PC NINA non raggiungibile' : 'Collegamento NINA da configurare';
      $('#emptyText').textContent = payload.message || 'Configura QSM sul PC NINA e riavvia il container.';
      return;
    }

    empty.hidden = true;
    dashboard.hidden = false;

    const snapshot = payload.snapshot || {};
    const summary = snapshot.summary || {};
    const frame = snapshot.currentFrame || {};
    const guide = snapshot.guidingLive || {};
    const quality = Number(frame.quality);
    const confidence = Number(frame.confidence);

    $('#qualityValue').textContent = Number.isFinite(quality) ? quality.toFixed(0) : text(frame.status);
    $('#qualityDetail').textContent = `${text(frame.status)} · ${text(frame.probableCause, 'nessuna causa')}`;
    $('#confidenceValue').textContent = Number.isFinite(confidence) ? `${confidence.toFixed(0)}%` : '—';
    $('#rejectedValue').textContent = num(summary.rejected, 0);
    $('#acceptanceValue').textContent = `Acceptance ${num(summary.acceptanceRate, 1, '%')}`;
    $('#targetValue').textContent = text(frame.target);
    $('#filterValue').textContent = text(frame.filter);
    $('#exposureValue').textContent = num(frame.exposureSeconds, 1, ' s');

    const gain = Number(frame.gain), binX = Number(frame.binX), binY = Number(frame.binY);
    $('#gainBinValue').textContent = `${Number.isFinite(gain) && gain >= 0 ? `G${gain}` : 'G—'} · ${Number.isFinite(binX) && Number.isFinite(binY) ? `${binX}×${binY}` : '—'}`;
    $('#capturedValue').textContent = num(summary.captured, 0);
    $('#usableValue').textContent = num(summary.usable, 0);
    $('#starsDeltaValue').textContent = signed(frame.starDeltaPercent);
    $('#backgroundDeltaValue').textContent = signed(frame.backgroundDeltaPercent);
    $('#frameStatusValue').textContent = text(frame.status);
    $('#cameraValue').textContent = text(frame.camera);
    $('#latencyValue').textContent = num(payload.latencyMs, 0, ' ms');
    $('#frameIndexValue').textContent = num(frame.frameIndex, 0);

    renderGuide(guide);
    renderTrend(snapshot.frames || []);
    renderEvents(snapshot.frames || []);
    requestPreview(frame);
  }

  async function refresh(force = false) {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const payload = await jsonFetch(`/api/state${force ? '?force=1' : ''}`);
      render(payload);
    } catch (error) {
      if (error.status === 401) {
        showLogin('Sessione scaduta. Accedi di nuovo.');
        return;
      }
      render({configured: true, reachable: false, sessionActive: false, message: `Monitor non disponibile: ${error.message}`, snapshot: null});
    } finally {
      busy = false;
    }
  }

  $('#loginForm').addEventListener('submit', async event => {
    event.preventDefault();
    const password = $('#loginPassword').value;
    const button = event.submitter || event.currentTarget.querySelector('button');
    button.disabled = true;
    try {
      await jsonFetch('/api/login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({password}),
      });
      $('#loginPassword').value = '';
      $('#loginError').hidden = true;
      showApp();
    } catch (error) {
      const retry = Number(error.payload?.retryAfter);
      showLogin(Number.isFinite(retry) ? `Troppi tentativi. Riprova tra ${retry} s.` : error.message);
    } finally {
      button.disabled = false;
    }
  });

  $('#logoutButton').addEventListener('click', async () => {
    try { await jsonFetch('/api/logout', {method: 'POST'}); } catch (_) {}
    showLogin();
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !$('#appView').hidden) refresh(true);
  });

  (async () => {
    try {
      const session = await jsonFetch('/api/session');
      if (session.authenticated) showApp(); else showLogin();
    } catch (_) {
      showLogin('Il servizio non risponde correttamente.');
    }
  })();
})();
