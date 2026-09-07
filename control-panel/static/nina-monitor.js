(() => {
  const $n = selector => document.querySelector(selector);
  const $$n = selector => [...document.querySelectorAll(selector)];
  if (window.__openastroNinaMonitorLoaded) return;
  window.__openastroNinaMonitorLoaded = true;

  const style = document.createElement('link');
  style.rel = 'stylesheet';
  style.href = '/nina-monitor.css?v=1.0';
  document.head.appendChild(style);

  const nav = $n('.side-nav');
  const main = $n('.app-main');
  if (!nav || !main || typeof window.selectView !== 'function') return;

  const navItem = document.createElement('a');
  navItem.href = '#nina';
  navItem.id = 'ninaRoute';
  navItem.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H4V5Zm2 2v7h12V7H6Zm3 11h6v2H9v-2Zm2-9h2v2h-2V9Z"/></svg><span>NINA</span>';
  nav.appendChild(navItem);

  const section = document.createElement('section');
  section.className = 'app-view nina-view';
  section.dataset.viewPanel = 'nina';
  section.setAttribute('aria-labelledby', 'view-nina-title');
  section.innerHTML = `
    <div class="nina-head">
      <div>
        <p class="eyebrow">REMOTE SESSION MONITOR</p>
        <h1 id="view-nina-title">NINA</h1>
        <p>Controllo read-only della sessione: Quality Session Meter, stato acquisizione e telemetria utile fuori casa.</p>
      </div>
      <div class="nina-link-state" id="ninaLinkState"><i></i><span>Verifica collegamento…</span></div>
    </div>
    <div class="nina-banner" id="ninaBanner"><div><strong id="ninaBannerTitle">In attesa di NINA</strong><small id="ninaBannerText">Il pannello resta operativo anche quando il PC di acquisizione è offline.</small></div><i class="nina-pulse"></i></div>
    <div id="ninaEmpty" class="nina-empty" hidden><div><strong id="ninaEmptyTitle">NINA non configurato</strong><p id="ninaEmptyText">Configura il collegamento QSM sul nodo OpenAstro. Nessun dato viene esposto direttamente a Internet.</p></div></div>
    <div id="ninaContent" class="nina-grid" hidden>
      <article class="nina-card quality"><span class="kicker">Quality</span><strong class="value" id="ninaQuality">—</strong><small class="detail" id="ninaQualityDetail">Ultimo frame</small></article>
      <article class="nina-card confidence"><span class="kicker">Confidence</span><strong class="value" id="ninaConfidence">—</strong><small class="detail" id="ninaConfidenceDetail">Affidabilità valutazione</small></article>
      <article class="nina-card guiding"><span class="kicker">Guide RMS</span><strong class="value" id="ninaRms">—</strong><small class="detail">RMS integrato dell'ultimo frame · live guider separato in arrivo</small></article>
      <article class="nina-card reject"><span class="kicker">Rejected</span><strong class="value" id="ninaRejected">—</strong><small class="detail" id="ninaAcceptance">—</small></article>

      <article class="nina-card wide">
        <span class="kicker">Sessione corrente</span>
        <div class="nina-session">
          <div><span>Target</span><strong id="ninaTarget">—</strong></div>
          <div><span>Filtro</span><strong id="ninaFilter">—</strong></div>
          <div><span>Frame</span><strong id="ninaCaptured">—</strong></div>
          <div><span>Usabili</span><strong id="ninaUsable">—</strong></div>
        </div>
      </article>

      <article class="nina-card wide">
        <span class="kicker">Segnale immagine</span>
        <div class="nina-session">
          <div><span>Stelle Δ</span><strong id="ninaStarsDelta">—</strong></div>
          <div><span>Background Δ</span><strong id="ninaBackgroundDelta">—</strong></div>
          <div><span>Stato</span><strong id="ninaFrameStatus">—</strong></div>
          <div><span>Latenza</span><strong id="ninaLatency">—</strong></div>
        </div>
      </article>

      <article class="nina-card full">
        <div class="nina-chart-head"><strong>Quality / Confidence / RMS</strong><div class="nina-legend"><span><i style="background:#8ab4f8"></i>Quality</span><span><i style="background:#c58af9"></i>Confidence</span><span><i style="background:#81c995"></i>RMS normalizzato</span></div></div>
        <div class="nina-plot" id="ninaPlot" role="img" aria-label="Andamento recente Quality Session Meter"></div>
      </article>

      <article class="nina-card full">
        <span class="kicker">Ultimi eventi</span>
        <div class="nina-event-list" id="ninaEvents"></div>
        <div class="nina-next">
          <article><b>Guida realmente live</b><small>Canale PHD2 dedicato, separato dall'RMS integrato per frame. Non viene simulato con dati QSM.</small></article>
          <article><b>Preview ultima esposizione</b><small>Canale immagine leggero JPEG/WebP, separato dai FITS originali e senza dipendere dall'NVMe del server.</small></article>
        </div>
      </article>
    </div>`;
  main.appendChild(section);

  let busy = false;
  let lastPayload = null;
  const originalSelectView = window.selectView;

  function setHash(view, updateHash) {
    if (!updateHash || location.hash === `#${view}`) return;
    history.pushState(null, '', `#${view}`);
  }

  function activateNina(updateHash = false) {
    try { currentView = 'nina'; } catch (_) {}
    document.body.dataset.view = 'nina';
    $$n('[data-view-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.viewPanel === 'nina'));
    $$n('[data-route]').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
    navItem.classList.add('active'); navItem.setAttribute('aria-current', 'page');
    const mobileTitle = $n('#mobileViewTitle'); if (mobileTitle) mobileTitle.textContent = 'NINA';
    document.title = 'NINA · OpenAstro';
    setHash('nina', updateHash);
    window.scrollTo({top:0, behavior:'auto'});
    refreshNina(true);
  }

  window.selectView = function(view, updateHash = false) {
    if (view === 'nina') return activateNina(updateHash);
    navItem.classList.remove('active'); navItem.removeAttribute('aria-current');
    return originalSelectView(view, updateHash);
  };

  navItem.addEventListener('click', event => { event.preventDefault(); activateNina(true); });

  function number(value, digits = 0, suffix = '') {
    const n = Number(value); return Number.isFinite(n) ? `${n.toFixed(digits)}${suffix}` : '—';
  }
  function signed(value, digits = 1) {
    const n = Number(value); return Number.isFinite(n) ? `${n > 0 ? '+' : ''}${n.toFixed(digits)}%` : '—';
  }
  function text(value, fallback = '—') { const raw = String(value ?? '').trim(); return raw || fallback; }
  function statusClass(value) { return String(value || '').toLowerCase().replace(/[^a-z]+/g, '-'); }

  function linePoints(frames, key, min, max, width, height) {
    const span = Math.max(.0001, max - min);
    return frames.map((frame, index) => {
      const value = Number(frame?.[key]); if (!Number.isFinite(value)) return null;
      const x = frames.length <= 1 ? width : index / (frames.length - 1) * width;
      const y = height - (value - min) / span * height;
      return `${Math.max(0,Math.min(width,x)).toFixed(1)},${Math.max(0,Math.min(height,y)).toFixed(1)}`;
    }).filter(Boolean).join(' ');
  }

  function renderPlot(frames) {
    const plot = $n('#ninaPlot');
    const recent = (frames || []).slice(-80);
    if (recent.length < 2) { plot.innerHTML = '<div class="nina-empty"><div><strong>Raccolta dati…</strong><p>Servono almeno due frame QSM per disegnare l’andamento.</p></div></div>'; return; }
    const w = 1000, h = 210, top = 10, bottom = 200;
    const quality = linePoints(recent, 'quality', 0, 100, w, 150);
    const confidence = linePoints(recent, 'confidence', 0, 100, w, 150);
    const rmsValues = recent.map(f => Number(f.guideRmsArcsec)).filter(Number.isFinite);
    const rmsMax = Math.max(2, ...rmsValues, 2);
    const rms = linePoints(recent, 'guideRmsArcsec', 0, rmsMax, w, 150);
    const grids = [0, .25, .5, .75, 1].map(v => `<line class="nina-gridline" x1="0" y1="${top + v*(bottom-top)}" x2="${w}" y2="${top + v*(bottom-top)}"/>`).join('');
    plot.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><g>${grids}</g><polyline class="nina-q" points="${quality}" transform="translate(0,20)"/><polyline class="nina-c" points="${confidence}" transform="translate(0,20)"/><polyline class="nina-rms" points="${rms}" transform="translate(0,20)"/></svg>`;
  }

  function renderEvents(frames) {
    const host = $n('#ninaEvents');
    const interesting = (frames || []).filter(frame => !['ACCEPTED','LEARNING'].includes(String(frame.status || '').toUpperCase())).slice(-8).reverse();
    if (!interesting.length) { host.innerHTML = '<div class="nina-event"><time>—</time><b>NESSUNO</b><span>Nessun warning/reject/error recente.</span></div>'; return; }
    host.innerHTML = interesting.map(frame => {
      const when = frame.timestampUtc ? new Date(frame.timestampUtc).toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit'}) : '—';
      const status = text(frame.status);
      const reason = text(frame.reason || frame.probableCause, 'Nessun dettaglio');
      return `<div class="nina-event ${statusClass(status)}"><time>${when}</time><b>${status}</b><span title="${escapeHtml(reason)}">${escapeHtml(reason)}</span></div>`;
    }).join('');
  }

  function render(payload) {
    lastPayload = payload;
    const link = $n('#ninaLinkState'), banner = $n('#ninaBanner');
    const empty = $n('#ninaEmpty'), content = $n('#ninaContent');
    link.className = `nina-link-state ${payload.reachable ? 'online' : payload.configured ? 'warn' : ''}`;
    link.querySelector('span').textContent = payload.reachable ? `QSM online · ${number(payload.latencyMs,0,' ms')}` : payload.configured ? 'QSM non raggiungibile' : 'Da configurare';
    banner.classList.toggle('live', Boolean(payload.sessionActive));
    $n('#ninaBannerTitle').textContent = payload.sessionActive ? 'Sessione NINA attiva' : payload.reachable ? 'QSM collegato · nessuna sessione attiva' : 'NINA non disponibile';
    $n('#ninaBannerText').textContent = payload.message || '—';

    if (!payload.reachable || !payload.snapshot) {
      empty.hidden = false; content.hidden = true;
      $n('#ninaEmptyTitle').textContent = payload.configured ? 'PC NINA non raggiungibile' : 'Collegamento NINA da configurare';
      $n('#ninaEmptyText').textContent = payload.message || 'Configura QSM sul PC NINA e il proxy OpenAstro.';
      return;
    }

    empty.hidden = true; content.hidden = false;
    const snapshot = payload.snapshot || {};
    const summary = snapshot.summary || {};
    const frame = snapshot.currentFrame || {};
    const quality = Number(frame.quality), confidence = Number(frame.confidence);
    $n('#ninaQuality').textContent = Number.isFinite(quality) ? quality.toFixed(0) : text(frame.status, '—');
    $n('#ninaQualityDetail').textContent = `${text(frame.status)} · ${text(frame.probableCause, 'nessuna causa')}`;
    $n('#ninaConfidence').textContent = Number.isFinite(confidence) ? `${confidence.toFixed(0)}%` : '—';
    $n('#ninaRms').textContent = number(frame.guideRmsArcsec, 2, '"');
    $n('#ninaRejected').textContent = number(summary.rejected, 0);
    $n('#ninaAcceptance').textContent = `Acceptance ${number(summary.acceptanceRate,1,'%')}`;
    $n('#ninaTarget').textContent = text(frame.target);
    $n('#ninaFilter').textContent = text(frame.filter);
    $n('#ninaCaptured').textContent = number(summary.captured,0);
    $n('#ninaUsable').textContent = number(summary.usable,0);
    $n('#ninaStarsDelta').textContent = signed(frame.starDeltaPercent);
    $n('#ninaBackgroundDelta').textContent = signed(frame.backgroundDeltaPercent);
    $n('#ninaFrameStatus').textContent = text(frame.status);
    $n('#ninaLatency').textContent = number(payload.latencyMs,0,' ms');
    renderPlot(snapshot.frames || []);
    renderEvents(snapshot.frames || []);
  }

  async function refreshNina(force = false) {
    let active = false;
    try { active = currentView === 'nina'; } catch (_) { active = location.hash === '#nina'; }
    if (!force && !active) return;
    if (busy) return;
    busy = true;
    try {
      const response = await fetch(`/api/nina/state${force ? '?force=1' : ''}`, {cache:'no-store', signal:AbortSignal.timeout(5000)});
      if (response.status === 401) return;
      const payload = await response.json();
      render(payload);
    } catch (error) {
      render({configured:true,reachable:false,sessionActive:false,message:`OpenAstro non riesce a leggere NINA: ${error.message || error}`,snapshot:null});
    } finally { busy = false; }
  }

  window.openastroNinaRefresh = refreshNina;
  if (location.hash === '#nina') activateNina(false);
  setInterval(() => refreshNina(false), 2000);
})();
