(() => {
  const $n = selector => document.querySelector(selector);
  const $$n = selector => [...document.querySelectorAll(selector)];
  if (window.__openastroNinaMonitorLoaded) return;
  window.__openastroNinaMonitorLoaded = true;

  const style = document.createElement('link');
  style.rel = 'stylesheet';
  style.href = '/nina-monitor.css?v=2.0';
  document.head.appendChild(style);

  const sideNav = $n('.side-nav');
  const mobileNav = $n('.mobile-nav');
  const main = $n('.app-main');
  if (!sideNav || !main || typeof window.selectView !== 'function') return;

  const navMarkup = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H4V5Zm2 2v7h12V7H6Zm3 11h6v2H9v-2Zm2-9h2v2h-2V9Z"/></svg><span>NINA</span>';
  const navItem = document.createElement('a');
  navItem.href = '#nina'; navItem.id = 'ninaRoute'; navItem.dataset.route = 'nina'; navItem.innerHTML = navMarkup;
  sideNav.appendChild(navItem);
  const mobileItem = document.createElement('a');
  mobileItem.href = '#nina'; mobileItem.id = 'ninaMobileRoute'; mobileItem.dataset.route = 'nina'; mobileItem.innerHTML = navMarkup;
  if (mobileNav) mobileNav.appendChild(mobileItem);

  const section = document.createElement('section');
  section.className = 'app-view nina-view';
  section.dataset.viewPanel = 'nina';
  section.setAttribute('aria-labelledby', 'view-nina-title');
  section.innerHTML = `
    <div class="nina-head">
      <div><p class="eyebrow">REMOTE SESSION MONITOR</p><h1 id="view-nina-title">NINA</h1><p>Monitor read-only della sessione N.I.N.A.: qualità, guida vera in tempo reale e ultima esposizione, attraverso il tuo OpenAstro.</p></div>
      <div class="nina-link-state" id="ninaLinkState"><i></i><span>Verifica collegamento…</span></div>
    </div>
    <div class="nina-banner" id="ninaBanner"><div><strong id="ninaBannerTitle">In attesa di NINA</strong><small id="ninaBannerText">Il pannello OpenAstro resta operativo anche se il PC di acquisizione è offline.</small></div><i class="nina-pulse"></i></div>
    <div id="ninaEmpty" class="nina-empty" hidden><div><strong id="ninaEmptyTitle">NINA non configurato</strong><p id="ninaEmptyText">Configura il collegamento QSM sul nodo OpenAstro. Il PC NINA non viene esposto direttamente a Internet.</p></div></div>
    <div id="ninaContent" class="nina-grid" hidden>
      <article class="nina-card quality"><span class="kicker">Quality</span><strong class="value" id="ninaQuality">—</strong><small class="detail" id="ninaQualityDetail">Ultimo frame</small></article>
      <article class="nina-card confidence"><span class="kicker">Confidence</span><strong class="value" id="ninaConfidence">—</strong><small class="detail">Affidabilità valutazione QSM</small></article>
      <article class="nina-card guiding"><span class="kicker">Guide RMS · LIVE</span><strong class="value" id="ninaRms">—</strong><small class="detail" id="ninaGuideDetail">In attesa di guide step</small></article>
      <article class="nina-card reject"><span class="kicker">Rejected</span><strong class="value" id="ninaRejected">—</strong><small class="detail" id="ninaAcceptance">—</small></article>

      <article class="nina-card wide"><span class="kicker">Sessione corrente</span><div class="nina-session six">
        <div><span>Target</span><strong id="ninaTarget">—</strong></div><div><span>Filtro</span><strong id="ninaFilter">—</strong></div><div><span>Esposizione</span><strong id="ninaExposure">—</strong></div>
        <div><span>Gain / Bin</span><strong id="ninaGainBin">—</strong></div><div><span>Frame</span><strong id="ninaCaptured">—</strong></div><div><span>Usabili</span><strong id="ninaUsable">—</strong></div>
      </div></article>

      <article class="nina-card wide"><span class="kicker">Segnale immagine</span><div class="nina-session six">
        <div><span>Stelle Δ</span><strong id="ninaStarsDelta">—</strong></div><div><span>Background Δ</span><strong id="ninaBackgroundDelta">—</strong></div><div><span>Stato</span><strong id="ninaFrameStatus">—</strong></div>
        <div><span>Camera</span><strong id="ninaCamera">—</strong></div><div><span>Latenza</span><strong id="ninaLatency">—</strong></div><div><span>Frame QSM</span><strong id="ninaFrameIndex">—</strong></div>
      </div></article>

      <article class="nina-card full">
        <div class="nina-chart-head"><strong>Guida live · ultimi 20 secondi</strong><div class="nina-legend"><span><i style="background:#8ab4f8"></i>RA</span><span><i style="background:#f28b82"></i>DEC</span><span>arcsec · zero al centro</span></div></div>
        <div class="nina-guide-metrics"><span>Total RMS <b id="ninaGuideTotal">—</b></span><span>RA RMS <b id="ninaGuideRa">—</b></span><span>DEC RMS <b id="ninaGuideDec">—</b></span><span>Max <b id="ninaGuideMax">—</b></span><span>Samples <b id="ninaGuideSamples">—</b></span></div>
        <div class="nina-plot guide" id="ninaGuidePlot" role="img" aria-label="Guida live RA e DEC in arcsec"></div>
      </article>

      <article class="nina-card wide">
        <div class="nina-chart-head"><strong>Ultima esposizione</strong><div class="nina-legend"><span>JPEG 1280px · generato in RAM</span></div></div>
        <div class="nina-preview-wrap"><div class="nina-preview-placeholder" id="ninaPreviewPlaceholder"><b>Preview in attesa</b><span>Comparirà al prossimo LIGHT salvato da N.I.N.A.</span></div><img id="ninaPreview" alt="Preview ultima esposizione NINA" hidden></div>
        <div class="nina-preview-meta"><span id="ninaPreviewState">Nessuna preview ricevuta</span><span>FITS originali non trasferiti</span></div>
      </article>

      <article class="nina-card wide">
        <div class="nina-chart-head"><strong>Trend sessione</strong><div class="nina-legend"><span><i style="background:#8ab4f8"></i>Quality</span><span><i style="background:#c58af9"></i>Confidence</span><span><i style="background:#81c995"></i>RMS posa · scala propria</span></div></div>
        <div class="nina-plot" id="ninaPlot" role="img" aria-label="Andamento recente Quality Session Meter"></div>
      </article>

      <article class="nina-card full"><span class="kicker">Ultimi eventi</span><div class="nina-event-list" id="ninaEvents"></div></article>
    </div>`;
  main.appendChild(section);

  let busy = false;
  let previewPending = null;
  let previewLoaded = null;
  const originalSelectView = window.selectView;

  function setHash(view, updateHash) { if (updateHash && location.hash !== `#${view}`) history.pushState(null, '', `#${view}`); }
  function activateNina(updateHash = false) {
    try { currentView = 'nina'; } catch (_) {}
    document.body.dataset.view = 'nina';
    $$n('[data-view-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.viewPanel === 'nina'));
    $$n('[data-route]').forEach(link => { const active = link.dataset.route === 'nina'; link.classList.toggle('active', active); if (active) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current'); });
    const mobileTitle = $n('#mobileViewTitle'); if (mobileTitle) mobileTitle.textContent = 'NINA';
    document.title = 'NINA · OpenAstro'; setHash('nina', updateHash); window.scrollTo({top:0,behavior:'auto'}); refreshNina(true);
  }
  window.selectView = function(view, updateHash = false) {
    if (view === 'nina') return activateNina(updateHash);
    return originalSelectView(view, updateHash);
  };
  [navItem, mobileItem].forEach(item => item?.addEventListener('click', event => { event.preventDefault(); activateNina(true); }));

  function number(value, digits = 0, suffix = '') { const n=Number(value); return Number.isFinite(n)?`${n.toFixed(digits)}${suffix}`:'—'; }
  function signed(value, digits = 1) { const n=Number(value); return Number.isFinite(n)?`${n>0?'+':''}${n.toFixed(digits)}%`:'—'; }
  function text(value, fallback='—') { const raw=String(value??'').trim(); return raw||fallback; }
  function statusClass(value) { return String(value||'').toLowerCase().replace(/[^a-z]+/g,'-'); }
  function polyPoints(items,key,min,max,width,height) {
    const span=Math.max(.0001,max-min); return (items||[]).map((item,index)=>{const value=Number(item?.[key]);if(!Number.isFinite(value))return null;const x=items.length<=1?width:index/(items.length-1)*width;const y=height-(value-min)/span*height;return `${Math.max(0,Math.min(width,x)).toFixed(1)},${Math.max(0,Math.min(height,y)).toFixed(1)}`;}).filter(Boolean).join(' ');
  }
  function grids(w,h) { return [0,.25,.5,.75,1].map(v=>`<line class="nina-gridline" x1="0" y1="${(v*h).toFixed(1)}" x2="${w}" y2="${(v*h).toFixed(1)}"/>`).join(''); }

  function renderGuide(guide) {
    guide=guide||{}; const series=Array.isArray(guide.series)?guide.series:[];
    $n('#ninaGuideTotal').textContent=number(guide.rmsTotalArcsec,2,'"'); $n('#ninaGuideRa').textContent=number(guide.rmsRaArcsec,2,'"'); $n('#ninaGuideDec').textContent=number(guide.rmsDecArcsec,2,'"');
    $n('#ninaGuideMax').textContent=number(guide.maxExcursionArcsec,2,'"'); $n('#ninaGuideSamples').textContent=number(guide.samples,0);
    const age=guide.latestUtc?Math.max(0,(Date.now()-Date.parse(guide.latestUtc))/1000):Infinity; const fresh=guide.hasData&&age<6;
    $n('#ninaRms').textContent=guide.hasData?number(guide.rmsTotalArcsec,2,'"'):'—'; $n('#ninaGuideDetail').textContent=guide.hasData?(fresh?`live · ultimo step ${age.toFixed(1)} s fa`:`dato guida fermo · ${age.toFixed(0)} s fa`):'Nessun guide step negli ultimi 20 s';
    const plot=$n('#ninaGuidePlot'); if(series.length<2){plot.innerHTML='<div class="nina-empty"><div><strong>Guida non disponibile</strong><p>In attesa dei GuideEvent di N.I.N.A.</p></div></div>';return;}
    const vals=series.flatMap(p=>[Number(p.raArcsec),Number(p.decArcsec)]).filter(Number.isFinite); const maxAbs=Math.max(.5,...vals.map(Math.abs))*1.15; const w=1000,h=180;
    const ra=polyPoints(series,'raArcsec',-maxAbs,maxAbs,w,h), dec=polyPoints(series,'decArcsec',-maxAbs,maxAbs,w,h);
    plot.innerHTML=`<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><g>${grids(w,h)}</g><line class="nina-zero" x1="0" y1="${h/2}" x2="${w}" y2="${h/2}"/><polyline class="nina-guide-ra" points="${ra}"/><polyline class="nina-guide-dec" points="${dec}"/></svg>`;
  }

  function renderTrend(frames) {
    const plot=$n('#ninaPlot'), recent=(frames||[]).slice(-80); if(recent.length<2){plot.innerHTML='<div class="nina-empty"><div><strong>Raccolta dati…</strong><p>Servono almeno due frame QSM.</p></div></div>';return;}
    const w=1000,h=200; const quality=polyPoints(recent,'quality',0,100,w,h),confidence=polyPoints(recent,'confidence',0,100,w,h);
    const rmsValues=recent.map(f=>Number(f.guideRmsArcsec)).filter(Number.isFinite),rmsMax=Math.max(2,...rmsValues,2),rms=polyPoints(recent,'guideRmsArcsec',0,rmsMax,w,h);
    plot.innerHTML=`<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><g>${grids(w,h)}</g><polyline class="nina-q" points="${quality}"/><polyline class="nina-c" points="${confidence}"/><polyline class="nina-rms" points="${rms}"/></svg>`;
  }

  function renderEvents(frames) {
    const host=$n('#ninaEvents'),interesting=(frames||[]).filter(frame=>!['ACCEPTED','LEARNING'].includes(String(frame.status||'').toUpperCase())).slice(-8).reverse();
    if(!interesting.length){host.innerHTML='<div class="nina-event"><time>—</time><b>NESSUNO</b><span>Nessun warning/reject/error recente.</span></div>';return;}
    host.innerHTML=interesting.map(frame=>{const when=frame.timestampUtc?new Date(frame.timestampUtc).toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit'}):'—';const status=text(frame.status),reason=text(frame.reason||frame.probableCause,'Nessun dettaglio');return `<div class="nina-event ${statusClass(status)}"><time>${when}</time><b>${escapeHtml(status)}</b><span title="${escapeHtml(reason)}">${escapeHtml(reason)}</span></div>`;}).join('');
  }

  function requestPreview(frame) {
    const frameId=Number(frame?.frameIndex); if(!Number.isFinite(frameId)||frameId<=0||previewLoaded===frameId||previewPending===frameId)return;
    previewPending=frameId; const img=$n('#ninaPreview'),placeholder=$n('#ninaPreviewPlaceholder'),state=$n('#ninaPreviewState'); state.textContent=`Carico preview frame #${frameId}…`;
    img.onload=()=>{previewLoaded=frameId;previewPending=null;img.hidden=false;placeholder.hidden=true;state.textContent=`Preview frame #${frameId} · ${text(frame.filter,'senza filtro')} · ${number(frame.exposureSeconds,1,' s')}`;};
    img.onerror=()=>{previewPending=null;if(previewLoaded===null){img.hidden=true;placeholder.hidden=false;}state.textContent='Preview non ancora pronta · nuovo tentativo automatico';};
    img.src=`/api/nina/preview.jpg?frame=${encodeURIComponent(frameId)}&t=${Date.now()}`;
  }

  function render(payload) {
    const link=$n('#ninaLinkState'),banner=$n('#ninaBanner'),empty=$n('#ninaEmpty'),content=$n('#ninaContent');
    link.className=`nina-link-state ${payload.reachable?'online':payload.configured?'warn':''}`; link.querySelector('span').textContent=payload.reachable?`QSM online · ${number(payload.latencyMs,0,' ms')}`:payload.configured?'QSM non raggiungibile':'Da configurare';
    banner.classList.toggle('live',Boolean(payload.sessionActive)); $n('#ninaBannerTitle').textContent=payload.sessionActive?'Sessione NINA attiva':payload.reachable?'QSM collegato · nessuna sessione attiva':'NINA non disponibile'; $n('#ninaBannerText').textContent=payload.message||'—';
    if(!payload.reachable||!payload.snapshot){empty.hidden=false;content.hidden=true;$n('#ninaEmptyTitle').textContent=payload.configured?'PC NINA non raggiungibile':'Collegamento NINA da configurare';$n('#ninaEmptyText').textContent=payload.message||'Configura QSM sul PC NINA e il proxy OpenAstro.';return;}
    empty.hidden=true;content.hidden=false;const snapshot=payload.snapshot||{},summary=snapshot.summary||{},frame=snapshot.currentFrame||{},guide=snapshot.guidingLive||{}; const quality=Number(frame.quality),confidence=Number(frame.confidence);
    $n('#ninaQuality').textContent=Number.isFinite(quality)?quality.toFixed(0):text(frame.status,'—'); $n('#ninaQualityDetail').textContent=`${text(frame.status)} · ${text(frame.probableCause,'nessuna causa')}`; $n('#ninaConfidence').textContent=Number.isFinite(confidence)?`${confidence.toFixed(0)}%`:'—';
    $n('#ninaRejected').textContent=number(summary.rejected,0); $n('#ninaAcceptance').textContent=`Acceptance ${number(summary.acceptanceRate,1,'%')}`; $n('#ninaTarget').textContent=text(frame.target); $n('#ninaFilter').textContent=text(frame.filter); $n('#ninaExposure').textContent=number(frame.exposureSeconds,1,' s');
    const gain=Number(frame.gain),bx=Number(frame.binX),by=Number(frame.binY); $n('#ninaGainBin').textContent=`${Number.isFinite(gain)&&gain>=0?'G'+gain:'G—'} · ${Number.isFinite(bx)&&Number.isFinite(by)?`${bx}×${by}`:'—'}`; $n('#ninaCaptured').textContent=number(summary.captured,0); $n('#ninaUsable').textContent=number(summary.usable,0);
    $n('#ninaStarsDelta').textContent=signed(frame.starDeltaPercent); $n('#ninaBackgroundDelta').textContent=signed(frame.backgroundDeltaPercent); $n('#ninaFrameStatus').textContent=text(frame.status); $n('#ninaCamera').textContent=text(frame.camera); $n('#ninaLatency').textContent=number(payload.latencyMs,0,' ms'); $n('#ninaFrameIndex').textContent=number(frame.frameIndex,0);
    renderGuide(guide); renderTrend(snapshot.frames||[]); renderEvents(snapshot.frames||[]); requestPreview(frame);
  }

  async function refreshNina(force=false) {
    let active=false; try{active=currentView==='nina';}catch(_){active=location.hash==='#nina';} if(!force&&!active)return;if(busy)return;busy=true;
    try{const response=await fetch(`/api/nina/state${force?'?force=1':''}`,{cache:'no-store',signal:AbortSignal.timeout(5000)});if(response.status===401)return;render(await response.json());}
    catch(error){render({configured:true,reachable:false,sessionActive:false,message:`OpenAstro non riesce a leggere NINA: ${error.message||error}`,snapshot:null});}
    finally{busy=false;}
  }

  window.openastroNinaRefresh=refreshNina;if(location.hash==='#nina')activateNina(false);setInterval(()=>refreshNina(false),1000);
})();
