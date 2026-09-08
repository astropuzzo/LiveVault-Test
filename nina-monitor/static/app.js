(() => {
  const $ = selector => document.querySelector(selector);
  let pollTimer = null;
  let busy = false;
  let previewFrame = null;
  let previewPending = null;
  let lastSnapshot = null;
  let selectedFrameIndex = null;

  const COLORS = {
    quality: '#8AB4F8', confidence: '#C58AF9', guide: '#81C995', stars: '#FDD663', background: '#F28B82',
    grid: '#383E46', border: '#30363D', text: '#9AA0A6', secondary: '#767D86', rejected: '#FF6E69', warning: '#FDD663', error: '#FF7878'
  };

  function esc(value) {
    return String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
  }
  function text(value, fallback = '—') { const raw = String(value ?? '').trim(); return raw || fallback; }
  function n(value) { const x = Number(value); return Number.isFinite(x) ? x : null; }
  function num(value, digits = 0, suffix = '') { const x = n(value); return x == null ? '—' : `${x.toFixed(digits)}${suffix}`; }
  function signed(value, digits = 1) { const x = n(value); return x == null ? 'N/A' : `${x > 0 ? '+' : ''}${x.toFixed(digits)}%`; }
  function arcsec(value) { const x = n(value); return x == null ? 'N/A' : `${x.toFixed(2)}"`; }
  function when(value, seconds = false) {
    const d = new Date(value || ''); if (!Number.isFinite(d.getTime())) return '—';
    return d.toLocaleTimeString('it-IT', {hour:'2-digit',minute:'2-digit',...(seconds ? {second:'2-digit'} : {})});
  }
  function statusClass(value) { return String(value || '').toLowerCase().replace(/[^a-z]+/g,'-').replace(/^-|-$/g,''); }
  function fileName(frame) { return text(frame?.fileName, frame?.syntheticFile ? `synthetic_frame_${num(frame?.frameIndex,0)}.fits` : `(frame #${num(frame?.frameIndex,0)})`); }
  function qualityLabel(frame) {
    const s = String(frame?.status || '').toUpperCase();
    if (s === 'LEARNING') return 'LEARNING';
    if (s === 'ERROR') return 'UNASSESSED';
    const q = n(frame?.quality); if (q == null) return 'N/A';
    return q >= 90 ? 'EXCELLENT' : q >= 80 ? 'GOOD' : q >= 65 ? 'FAIR' : q >= 50 ? 'POOR' : 'BAD';
  }
  function confidenceLabel(value) { const c = n(value); return c == null ? 'N/A' : c >= 95 ? 'VERY HIGH' : c >= 80 ? 'HIGH' : c >= 65 ? 'MODERATE' : c >= 45 ? 'LOW' : 'VERY LOW'; }
  function trendText(frame) {
    const a = String(frame?.starTrend || '').toUpperCase(), b = String(frame?.backgroundTrend || '').toUpperCase();
    if (a.includes('ABRUPT') || b.includes('ABRUPT')) return 'ABRUPT';
    if (a.includes('GRADUAL') || b.includes('GRADUAL')) return 'GRADUAL';
    if (a.includes('STABLE') || b.includes('STABLE')) return 'STABLE';
    return 'N/A';
  }
  function isAbnormal(frame) { const s = String(frame?.status || '').toUpperCase(); return s.includes('REJECT') || s === 'WARNING' || s === 'ERROR'; }

  async function jsonFetch(url, options = {}) {
    const response = await fetch(url, {cache:'no-store',credentials:'same-origin',...options});
    let payload = {}; try { payload = await response.json(); } catch (_) {}
    if (!response.ok) { const error = new Error(payload.error || `HTTP ${response.status}`); error.status = response.status; error.payload = payload; throw error; }
    return payload;
  }
  function showLogin(message = '') {
    $('#appView').hidden = true; $('#loginView').hidden = false;
    const error = $('#loginError'); error.hidden = !message; error.textContent = message;
    stopPolling(); setTimeout(() => $('#loginPassword')?.focus(),0);
  }
  function showApp() { $('#loginView').hidden = true; $('#appView').hidden = false; startPolling(); }
  function startPolling() { stopPolling(); refresh(true); pollTimer = setInterval(() => refresh(false),1000); }
  function stopPolling() { if (pollTimer) clearInterval(pollTimer); pollTimer = null; }

  function parseReasons(frame) { return String(frame?.reason || '').toUpperCase().split(/[,.·;]/).map(x => x.trim()).filter(Boolean); }
  function classifyEvent(frame) {
    if (String(frame?.status || '').toUpperCase() === 'ERROR') return 'AnalysisDataLoss';
    const reasons = parseReasons(frame); const cause = String(frame?.probableCause || '').toUpperCase();
    let guide = reasons.some(x => x.includes('GUIDE')) || /GUIDE|GUIDING|TRACK|WIND|OSCILL|DRIFT/.test(cause);
    let stars = reasons.some(x => x.includes('STAR_COUNT_DROP')) || /STAR|CLOUD|TRANSP/.test(cause);
    let bright = reasons.some(x => x.includes('BACKGROUND_HIGH'));
    let dark = reasons.some(x => x.includes('BACKGROUND_LOW'));
    if (!bright && !dark && /BACKGROUND|HAZE|SKY/.test(cause)) { const d = n(frame?.backgroundDeltaPercent); if (d != null && d >= 0) bright = true; else dark = true; }
    if (guide && (stars || bright || dark)) return 'MixedConditions';
    if (stars && bright) return 'BrightCloudBackground';
    if (stars) return 'CloudTransparency';
    if (bright || dark) return 'BackgroundHaze';
    if (guide) return 'GuidingDisturbance';
    return 'Unknown';
  }
  function compatibleEvent(a,b) {
    if (a === b || a === 'MixedConditions' || b === 'MixedConditions') return true;
    const image = new Set(['CloudTransparency','BrightCloudBackground','BackgroundHaze']); return image.has(a) && image.has(b);
  }
  function eventTypeText(type) {
    return ({CloudTransparency:'CLOUD / TRANSPARENCY',BrightCloudBackground:'BRIGHT CLOUD / BACKGROUND',BackgroundHaze:'BACKGROUND / HAZE',GuidingDisturbance:'GUIDING DISTURBANCE',MixedConditions:'MIXED CONDITIONS',AnalysisDataLoss:'ANALYSIS DATA LOSS',Unknown:'UNKNOWN'})[type] || 'UNKNOWN';
  }
  function groupEvents(frames) {
    const events = []; let active = null, healthyGap = 0;
    const close = () => { active = null; healthyGap = 0; };
    const addAbnormal = (event, frame) => {
      event.end = frame.timestampUtc; event.lastFrame = frame.frameIndex; event.frameIndices.push(frame.frameIndex);
      const s = String(frame.status || '').toUpperCase(); if (s.includes('REJECT')) event.rejected++; else if (s === 'WARNING') event.warning++; else if (s === 'ERROR') event.errors++;
      const c = n(frame.confidence); if (c != null) event.confidences.push(c); if (text(frame.probableCause,'') !== '') event.causes.push(frame.probableCause);
    };
    const start = (frame,type) => {
      active = {index:events.length+1,type,start:frame.timestampUtc,end:frame.timestampUtc,firstFrame:frame.frameIndex,lastFrame:frame.frameIndex,frameIndices:[],rejected:0,warning:0,errors:0,confidences:[],causes:[]};
      addAbnormal(active,frame); events.push(active); healthyGap = 0;
    };
    for (const frame of frames || []) {
      if (!isAbnormal(frame)) { if (active) { healthyGap++; if (healthyGap >= 2) close(); } continue; }
      const type = classifyEvent(frame);
      if (!active) { start(frame,type); continue; }
      const gapMinutes = Math.max(0,(Date.parse(frame.timestampUtc)-Date.parse(active.end))/60000); const within = Number.isFinite(gapMinutes) && gapMinutes <= 12; const same = compatibleEvent(active.type,type);
      if (within && healthyGap === 0) { if (!same) active.type = 'MixedConditions'; addAbnormal(active,frame); healthyGap = 0; continue; }
      if (within && healthyGap === 1 && same) { addAbnormal(active,frame); healthyGap = 0; continue; }
      close(); start(frame,type);
    }
    return events.map((e,i) => {
      const mean = e.confidences.length ? e.confidences.reduce((a,b)=>a+b,0)/e.confidences.length : 0;
      const causeCounts = new Map(); e.causes.forEach(c => causeCounts.set(c,(causeCounts.get(c)||0)+1));
      const primary = [...causeCounts.entries()].sort((a,b)=>b[1]-a[1] || String(a[0]).localeCompare(String(b[0])))[0]?.[0] || eventTypeText(e.type);
      return {...e,id:`E${String(i+1).padStart(3,'0')}`,meanConfidence:mean,primaryCause:primary,severity:e.errors>0||e.rejected>=3?'CRITICAL':e.rejected>0?'HIGH':'MODERATE'};
    });
  }

  function causeCodes(frame) {
    if (String(frame?.status || '').toUpperCase() === 'ERROR') return '!';
    const r = parseReasons(frame).join(' '), c = String(frame?.probableCause || '').toUpperCase(); let out = '';
    if (/GUIDE|GUIDING|TRACK|WIND|OSCILL|DRIFT/.test(`${r} ${c}`)) out += 'G';
    if (/STAR|CLOUD|TRANSP/.test(`${r} ${c}`)) out += 'S';
    if (/BACKGROUND|HAZE|SKY/.test(`${r} ${c}`)) out += 'B';
    return out || (isAbnormal(frame) ? '?' : '');
  }
  function timelineTooltip(frame, settings) {
    const rows = [`Frame #${num(frame.frameIndex,0)} · ${text(frame.status)} · ${fileName(frame)}`,
      `Quality ${num(frame.quality,0)} / 100 · Confidence ${num(frame.confidence,0,'%')}`,
      `Guide RMS ${arcsec(frame.guideRmsArcsec)} · limit ${num(settings?.maxGuideRms,2,'"')}`];
    const stars = n(frame.stars), sb = n(frame.starBaseline); if (stars != null) rows.push(`Stars ${stars}${sb != null ? ` · baseline ${sb.toFixed(0)} · Δ ${signed(frame.starDeltaPercent)} · reject below -${num(settings?.maxStarLossPercent,1,'%')}` : ' · baseline learning/not ready'}`);
    const bg = n(frame.background), bb = n(frame.backgroundBaseline); if (bg != null) rows.push(`Background ${bg.toFixed(2)}${bb != null ? ` · baseline ${bb.toFixed(2)} · Δ ${signed(frame.backgroundDeltaPercent)} · limits -${num(settings?.maxBackgroundDecreasePercent,1)}/+${num(settings?.maxBackgroundIncreasePercent,1)}%` : ' · baseline learning/not ready'}`);
    rows.push(`Cause: ${text(frame.probableCause)}${text(frame.reason,'—') !== '—' ? ` · ${frame.reason}` : ''}`); return rows.join('\n');
  }
  function seriesSegments(frames,key,min,max,left,top,width,height,cls) {
    let seg = [], out = '';
    const flush = () => { if (seg.length > 1) out += `<polyline class="${cls}" points="${seg.join(' ')}"/>`; seg = []; };
    frames.forEach((f,i) => { const v = n(f?.[key]); if (v == null) { flush(); return; } const x = frames.length===1?left+width/2:left+i*width/(frames.length-1); const y = top+height*(1-(Math.min(max,Math.max(min,v))-min)/Math.max(.000001,max-min)); seg.push(`${x.toFixed(1)},${y.toFixed(1)}`); }); flush(); return out;
  }
  function refLine(left,width,y,cls,label='') { return `<line class="${cls}" x1="${left}" y1="${y.toFixed(1)}" x2="${left+width}" y2="${y.toFixed(1)}"/>${label?`<text class="tl-ref-label" x="${left+width-6}" y="${(y-3).toFixed(1)}" text-anchor="end">${esc(label)}</text>`:''}`; }
  function valY(value,min,max,top,height) { return top+height*(1-(Math.min(max,Math.max(min,value))-min)/Math.max(.000001,max-min)); }

  function renderPluginTimeline(frames, settings) {
    const host = $('#pluginTimeline'); const list = (Array.isArray(frames)?frames:[]).slice(-160);
    const W=1200,H=270,L=185,marker=38,gap=6,plotW=W-L-2,bandH=(H-marker-gap*2-2)/3;
    const top=marker,rms=marker+bandH+gap,img=marker+2*(bandH+gap);
    if (!list.length) { host.innerHTML = '<div class="timeline-empty">Waiting for assessed LIGHT frames</div>'; return; }
    const maxObsRms = Math.max(0,...list.map(f=>n(f.guideRmsArcsec)??0)); const maxGuide = n(settings?.maxGuideRms)??1.5; const rmsMax=Math.max(2,maxObsRms*1.15,maxGuide*1.30);
    const observedAbs=Math.max(0,...list.flatMap(f=>[Math.abs(n(f.starDeltaPercent)??0),Math.abs(n(f.backgroundDeltaPercent)??0)]));
    const starLim=n(settings?.maxStarLossPercent)??35,bgHi=n(settings?.maxBackgroundIncreasePercent)??30,bgLo=n(settings?.maxBackgroundDecreasePercent)??30; const imgMax=Math.min(500,Math.max(50,observedAbs*1.15,Math.max(starLim,bgHi,bgLo)*1.25));
    let svg=`<svg viewBox="0 0 ${W} ${H}" class="plugin-timeline-svg" role="img" aria-label="QSM multichannel timeline">
      <text class="tl-events" x="3" y="11">EVENTS</text><text class="tl-events-help" x="3" y="25">G guide · S sky · B background · ! error</text>`;
    [top,rms,img].forEach(y=>{svg+=`<rect class="tl-band" x="${L}" y="${y.toFixed(1)}" width="${plotW}" height="${bandH.toFixed(1)}"/><line class="tl-grid" x1="${L}" y1="${(y+bandH/2).toFixed(1)}" x2="${L+plotW}" y2="${(y+bandH/2).toFixed(1)}"/>`;});
    svg+=`<circle cx="8" cy="${top+10}" r="3.3" fill="${COLORS.quality}"/><text class="tl-name q" x="17" y="${top+13}">Quality</text><text class="tl-detail" x="59" y="${top+13}">0–100</text>
      <circle cx="8" cy="${top+28}" r="3.3" fill="${COLORS.confidence}"/><text class="tl-name c" x="17" y="${top+31}">Confidence</text><text class="tl-detail" x="76" y="${top+31}">0–100</text>
      <circle cx="8" cy="${rms+12}" r="3.3" fill="${COLORS.guide}"/><text class="tl-name g" x="17" y="${rms+15}">Guide RMS</text><text class="tl-detail" x="76" y="${rms+15}">arcsec · lower is better</text>
      <circle cx="8" cy="${img+10}" r="3.3" fill="${COLORS.stars}"/><text class="tl-name s" x="17" y="${img+13}">Stars Δ</text><text class="tl-detail" x="58" y="${img+13}">% vs baseline · reject &lt; -${starLim.toFixed(1)}%</text>
      <circle cx="8" cy="${img+29}" r="3.3" fill="${COLORS.background}"/><text class="tl-name b" x="17" y="${img+32}">Background Δ</text><text class="tl-detail" x="91" y="${img+32}">% vs baseline · limits -${bgLo.toFixed(1)}/+${bgHi.toFixed(1)}%</text>
      <text class="tl-scale" x="${L+plotW-4}" y="${top+11}" text-anchor="end">100</text><text class="tl-scale" x="${L+plotW-4}" y="${top+bandH-4}" text-anchor="end">0</text>
      <text class="tl-scale" x="${L+5}" y="${rms+11}">0–${rmsMax.toFixed(1)}"</text><text class="tl-scale" x="${L+5}" y="${img+11}">±${imgMax.toFixed(0)}%</text>`;
    svg+=seriesSegments(list,'quality',0,100,L,top,plotW,bandH,'tl-q')+seriesSegments(list,'confidence',0,100,L,top,plotW,bandH,'tl-c')+seriesSegments(list,'guideRmsArcsec',0,rmsMax,L,rms,plotW,bandH,'tl-g')+seriesSegments(list,'starDeltaPercent',-imgMax,imgMax,L,img,plotW,bandH,'tl-s')+seriesSegments(list,'backgroundDeltaPercent',-imgMax,imgMax,L,img,plotW,bandH,'tl-b');
    if (settings?.enableGuideRms !== false && maxGuide>0) svg+=refLine(L,plotW,valY(maxGuide,0,rmsMax,rms,bandH),'tl-ref-guide',`RMS limit ${maxGuide.toFixed(2)}"`);
    svg+=refLine(L,plotW,valY(0,-imgMax,imgMax,img,bandH),'tl-ref-zero','0% rolling baseline');
    if (settings?.enableStarCount !== false && starLim>0) svg+=refLine(L,plotW,valY(-starLim,-imgMax,imgMax,img,bandH),'tl-ref-stars');
    if (settings?.enableBackground !== false) { if(bgHi>0) svg+=refLine(L,plotW,valY(bgHi,-imgMax,imgMax,img,bandH),'tl-ref-bg'); if(bgLo>0) svg+=refLine(L,plotW,valY(-bgLo,-imgMax,imgMax,img,bandH),'tl-ref-bg'); }
    let lastBadgeRight=-Infinity;
    list.forEach((f,i)=>{
      const x=list.length===1?L+plotW/2:L+i*plotW/(list.length-1); const s=String(f.status||'').toUpperCase();
      if (isAbnormal(f)) { const cls=s.includes('REJECT')?'reject':s==='WARNING'?'warning':'error'; svg+=`<line class="tl-status ${cls}" x1="${x.toFixed(1)}" y1="${marker}" x2="${x.toFixed(1)}" y2="${H-1}"/>`; const codes=causeCodes(f), bw=Math.max(20,codes.length*10+10),bx=x-bw/2; if(codes && bx>lastBadgeRight+3){svg+=`<rect class="tl-badge" x="${bx.toFixed(1)}" y="3" width="${bw}" height="16" rx="4"/><text class="tl-badge-text" x="${x.toFixed(1)}" y="14" text-anchor="middle">${esc(codes)}</text>`;lastBadgeRight=bx+bw;}}
      const cell=plotW/list.length; const hitX=Math.max(L,x-cell/2); svg+=`<rect class="timeline-hit" data-frame-id="${esc(f.frameIndex)}" x="${hitX.toFixed(1)}" y="0" width="${Math.max(4,cell).toFixed(1)}" height="${H}" fill="transparent"><title>${esc(timelineTooltip(f,settings))}</title></rect>`;
    });
    svg+='</svg>'; host.innerHTML=svg;
  }

  function renderGuide(guide, settings={}) {
    guide=guide||{}; const series=Array.isArray(guide.series)?guide.series:[];
    $('#guideTotalValue').textContent=arcsec(guide.rmsTotalArcsec); $('#guideRaValue').textContent=arcsec(guide.rmsRaArcsec); $('#guideDecValue').textContent=arcsec(guide.rmsDecArcsec); $('#guideMaxValue').textContent=arcsec(guide.maxExcursionArcsec); $('#guideSamplesValue').textContent=num(guide.samples,0);
    const plot=$('#guidePlot'); if(series.length<2){plot.innerHTML='<div class="plot-empty"><div><b>PHD2 live non sta inviando campioni</b><span>Il pannello resta attivo: comparirà appena N.I.N.A. riceve nuovi GuideEvent.</span></div></div>';return;}
    const vals=series.flatMap(p=>[n(p.raArcsec),n(p.decArcsec)]).filter(v=>v!=null); const lim=n(settings?.excursionThreshold)??2; const maxAbs=Math.max(.5,...vals.map(Math.abs),lim*1.2); const W=1000,H=190;
    const pts=(key)=>series.map((p,i)=>{const v=n(p[key]);if(v==null)return null;const x=series.length===1?W:i/(series.length-1)*W,y=H-(v+maxAbs)/(2*maxAbs)*H;return `${x.toFixed(1)},${y.toFixed(1)}`}).filter(Boolean).join(' ');
    const ly=(v)=>H-(v+maxAbs)/(2*maxAbs)*H; const y0=ly(0),yp=ly(lim),ym=ly(-lim);
    plot.innerHTML=`<div class="guide-legend"><span><i class="legend ra"></i>RA</span><span><i class="legend dec"></i>DEC</span><span>scala ±${maxAbs.toFixed(1)}"</span><span>soglia escursione ±${lim.toFixed(1)}"</span></div><div class="guide-svg-wrap"><div class="guide-y-axis"><span>+${maxAbs.toFixed(1)}"</span><span>0"</span><span>−${maxAbs.toFixed(1)}"</span></div><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><line class="zero-line" x1="0" y1="${y0}" x2="${W}" y2="${y0}"/><line class="guide-limit" x1="0" y1="${yp}" x2="${W}" y2="${yp}"/><line class="guide-limit" x1="0" y1="${ym}" x2="${W}" y2="${ym}"/><polyline class="line-ra" points="${pts('raArcsec')}"/><polyline class="line-dec" points="${pts('decArcsec')}"/></svg><div class="guide-x-axis"><span>−20 s</span><span>adesso</span></div></div>`;
  }

  function renderSessionEvents(events) {
    const host=$('#sessionEventsBody'), rows=(events||[]).slice(-12); if(!rows.length){host.innerHTML='<tr class="empty-row"><td colspan="7">No session events.</td></tr>';return;}
    host.innerHTML=rows.map(e=>`<tr><td>${esc(e.id)}</td><td>${esc(when(e.start))}–${esc(when(e.end))}</td><td>${esc(eventTypeText(e.type))}</td><td>${esc(e.frameIndices.length)}</td><td>${esc(num(e.meanConfidence,0,'%'))}</td><td>${esc(e.severity)}</td><td class="cause">${esc(e.primaryCause)}</td></tr>`).join('');
  }
  function renderRankings(frames) {
    const accepted=(frames||[]).filter(f=>String(f.status||'').toUpperCase()==='ACCEPTED'); const best=[...accepted].sort((a,b)=>(n(b.quality)??-1)-(n(a.quality)??-1)||(n(b.confidence)??-1)-(n(a.confidence)??-1)).slice(0,5); const worst=[...accepted].sort((a,b)=>(n(a.quality)??999)-(n(b.quality)??999)||(n(a.confidence)??999)-(n(b.confidence)??999)).slice(0,5);
    const rows=list=>list.length?list.map(f=>`<tr data-frame-id="${esc(f.frameIndex)}"><td>${esc(num(f.frameIndex,0))}</td><td>${esc(num(f.quality,0))}</td><td>${esc(num(f.confidence,0,'%'))}</td><td class="filename">${esc(fileName(f))}</td></tr>`).join(''):'<tr class="empty-row"><td colspan="4">No accepted frames.</td></tr>';
    $('#bestAcceptedBody').innerHTML=rows(best); $('#worstAcceptedBody').innerHTML=rows(worst);
  }
  function renderFrameInspector(frame) {
    const host=$('#frameInspector'); if(!frame){host.className='inspector-empty';host.innerHTML='Seleziona un frame rejected per vedere file, causa e metriche complete.';return;}
    host.className='inspector'; host.innerHTML=`<div class="inspector-file"><div><span>FILE</span><strong>${esc(fileName(frame))}</strong><small>${esc(text(frame.source))}${frame.sequenceTitle?` · ${esc(frame.sequenceTitle)}`:''}</small></div><span class="pill ${statusClass(frame.status)}">${esc(text(frame.status))}</span></div><div class="inspector-reason"><b>${esc(text(frame.probableCause,'Nessuna causa automatica'))}</b><span>${esc(text(frame.reason))}</span></div><div class="inspector-grid">${[['Quality',num(frame.quality,0)],['Confidence',num(frame.confidence,0,'%')],['Guide RMS',arcsec(frame.guideRmsArcsec)],['Max excursion',arcsec(frame.maxGuideExcursionArcsec)],['Guide pattern',text(frame.guidePattern)],['Stars',num(frame.stars,0)],['Stars baseline',num(frame.starBaseline,0)],['Stars Δ',signed(frame.starDeltaPercent)],['Background',num(frame.background,2)],['BG baseline',num(frame.backgroundBaseline,2)],['BG Δ',signed(frame.backgroundDeltaPercent)],['File action',text(frame.fileDisposition)],['Trend',trendText(frame)],['Target / filter',`${text(frame.target)} · ${text(frame.filter)}`],['Exposure',num(frame.exposureSeconds,1,' s')],['Gain / bin',`G${num(frame.gain,0)} · ${num(frame.binX,0)}×${num(frame.binY,0)}`]].map(([k,v])=>`<div class="inspector-metric"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')}</div>`;
    $('#inspectorHint').textContent=`frame #${num(frame.frameIndex,0)} · ${when(frame.timestampUtc,true)}`;
  }
  function renderTables(frames) {
    const list=Array.isArray(frames)?frames:[]; const rejected=list.filter(f=>String(f.status||'').toUpperCase().includes('REJECT')).slice(-80).reverse();
    $('#rejectedTableBody').innerHTML=rejected.length?rejected.map(f=>`<tr class="${statusClass(f.status)}${Number(f.frameIndex)===selectedFrameIndex?' selected':''}" data-frame-id="${esc(f.frameIndex)}"><td>${esc(num(f.frameIndex,0))}</td><td class="filename">${esc(fileName(f))}</td><td>${esc(text(f.fileDisposition))}</td><td>${esc(num(f.quality,0))}</td><td>${esc(num(f.confidence,0,'%'))}</td><td>${esc(arcsec(f.guideRmsArcsec))}</td><td>${esc(signed(f.starDeltaPercent))}</td><td>${esc(signed(f.backgroundDeltaPercent))}</td><td class="cause">${esc(text(f.probableCause))}</td></tr>`).join(''):'<tr class="empty-row"><td colspan="9">No rejected frames.</td></tr>';
    const history=list.slice(-80); $('#frameHistoryBody').innerHTML=history.length?history.map(f=>`<tr class="${statusClass(f.status)}${Number(f.frameIndex)===selectedFrameIndex?' selected':''}" data-frame-id="${esc(f.frameIndex)}"><td>${esc(num(f.frameIndex,0))}</td><td>${esc(num(f.quality,0))}</td><td>${esc(num(f.confidence,0,'%'))}</td><td>${esc(text(f.status))}</td><td class="filename">${esc(fileName(f))}</td><td>${esc(arcsec(f.guideRmsArcsec))}</td><td>${esc(text(f.guidePattern))}</td><td>${esc(trendText(f))}</td><td>${esc(signed(f.starDeltaPercent))}</td><td>${esc(signed(f.backgroundDeltaPercent))}</td><td class="cause">${esc(text(f.probableCause))}</td></tr>`).join(''):'<tr class="empty-row"><td colspan="11">No QSM frames.</td></tr>';
  }

  function requestPreview(frame, syntheticMode) {
    const frameId=n(frame?.frameIndex); if(previewPending===frameId && frameId!=null)return; previewPending=frameId;
    const image=$('#previewImage'),placeholder=$('#previewPlaceholder'),state=$('#previewState'); state.textContent=syntheticMode?'Cerco l’ultimo LIGHT reale ricevuto da N.I.N.A.…':`Carico preview ${fileName(frame)}…`;
    image.onload=()=>{previewFrame=frameId;previewPending=null;image.hidden=false;placeholder.hidden=true;state.textContent=syntheticMode?'Ultimo LIGHT reale ricevuto da N.I.N.A. · indipendente dal Synthetic Lab':`${fileName(frame)} · ${text(frame.filter,'senza filtro')} · ${num(frame.exposureSeconds,1,' s')}`;};
    image.onerror=()=>{previewPending=null;if(previewFrame==null){image.hidden=true;placeholder.hidden=false;}state.textContent=syntheticMode?'Nessun LIGHT reale disponibile da questa istanza N.I.N.A.':'Preview non ancora pronta · nuovo tentativo automatico';};
    image.src=`api/preview.jpg?t=${Date.now()}`;
  }

  function selectFrame(frameIndex) {
    const frames=Array.isArray(lastSnapshot?.frames)?lastSnapshot.frames:[]; const frame=frames.find(f=>Number(f.frameIndex)===Number(frameIndex)); if(!frame)return; selectedFrameIndex=Number(frame.frameIndex);renderFrameInspector(frame);renderTables(frames);
  }

  function render(payload) {
    const link=$('#linkState'),banner=$('#statusBanner'),empty=$('#emptyState'),dashboard=$('#dashboard');
    link.className=`link-state ${payload.reachable?'online':payload.configured?'warn':''}`; link.querySelector('b').textContent=payload.reachable?`QSM online · ${num(payload.latencyMs,0,' ms')}`:payload.configured?'QSM non raggiungibile':'Da configurare';
    if(!payload.reachable||!payload.snapshot){banner.classList.remove('live');$('#bannerTitle').textContent=payload.reachable?'QSM collegato · nessuna sessione attiva':'N.I.N.A. non disponibile';$('#bannerText').textContent=payload.message||'—';empty.hidden=false;dashboard.hidden=true;$('#emptyTitle').textContent=payload.configured?'PC N.I.N.A. non raggiungibile':'Collegamento N.I.N.A. da configurare';$('#emptyText').textContent=payload.message||'Configura QSM sul PC N.I.N.A. e riavvia il container.';return;}
    empty.hidden=true;dashboard.hidden=false;
    const snapshot=payload.snapshot||{},summary=snapshot.summary||{},frame=snapshot.currentFrame||{},guide=snapshot.guidingLive||{},mode=snapshot.mode||{},settings=snapshot.settings||{},frames=Array.isArray(snapshot.frames)?snapshot.frames:[]; lastSnapshot=snapshot;
    const synthetic=Boolean(mode.syntheticMode),scope=text(mode.monitoringScope,'AdvancedSequencerLights'); $('#modeTag').textContent=synthetic?'SYNTHETIC LAB':'LIVE';$('#modeTag').classList.toggle('synthetic',synthetic);
    banner.classList.toggle('live',Boolean(payload.sessionActive));$('#bannerTitle').textContent=synthetic?`Synthetic Lab${mode.syntheticSessionName?` · ${mode.syntheticSessionName}`:''}`:payload.sessionActive?'Sessione N.I.N.A. attiva':'QSM collegato · nessuna sessione attiva';$('#bannerText').textContent=synthetic?`${text(mode.syntheticStatus,'LAB')} · QSM sintetico + PHD2/preview reali separati`:(payload.message||'—');

    $('#qualityValue').textContent=n(frame.quality)==null?'—':num(frame.quality,0);$('#qualityLabel').textContent=qualityLabel(frame);$('#confidenceValue').textContent=n(frame.confidence)==null?'—':num(frame.confidence,0,'%');$('#confidenceLabel').textContent=confidenceLabel(frame.confidence);
    $('#modeText').textContent=synthetic?'SYNTHETIC LAB':mode.monitorOnly?`MONITOR ONLY · ${scope}`:`ACTIVE REJECT HANDLING · ${scope}`;$('#frameStatusMain').textContent=text(frame.status,'IDLE');$('#probableCauseMain').textContent=text(frame.probableCause,'Waiting for first LIGHT frame');$('#reasonMain').textContent=text(frame.reason);$('#currentFileMain').textContent=fileName(frame);
    $('#guideRmsFrame').textContent=arcsec(frame.guideRmsArcsec);$('#guideExcursionFrame').textContent=arcsec(frame.maxGuideExcursionArcsec);$('#guidePatternFrame').textContent=text(frame.guidePattern,'N/A');$('#guidePatternConfidence').textContent=n(frame.guidePatternConfidence)==null?'N/A':num(frame.guidePatternConfidence,0,'%');$('#starsFrame').textContent=n(frame.stars)==null?'N/A':num(frame.stars,0);$('#starsDeltaValue').textContent=signed(frame.starDeltaPercent);$('#starTrendFrame').textContent=text(frame.starTrend,'N/A');$('#backgroundFrame').textContent=n(frame.background)==null?'N/A':num(frame.background,2);$('#backgroundDeltaValue').textContent=signed(frame.backgroundDeltaPercent);$('#backgroundTrendFrame').textContent=text(frame.backgroundTrend,'N/A');$('#temporalFrame').textContent=trendText(frame);
    const events=groupEvents(frames);$('#eventCountValue').textContent=`${events.length} session events`;
    $('#capturedValue').textContent=num(summary.captured,0);$('#usableValue').textContent=num(summary.usable,0);$('#rejectedValue').textContent=num(summary.rejected,0);$('#acceptanceValue').textContent=num(summary.acceptanceRate,1,'%');$('#sessionQualityValue').textContent=num(summary.sessionQuality,0);$('#sessionConfidenceValue').textContent=num(summary.sessionConfidence,0,'%');
    renderPluginTimeline(frames,settings);renderSessionEvents(events);renderRankings(frames);renderTables(frames);renderGuide(guide,settings);
    if(selectedFrameIndex!=null){renderFrameInspector(frames.find(f=>Number(f.frameIndex)===selectedFrameIndex)||null);} else renderFrameInspector(null);
    requestPreview(frame,synthetic);
  }

  async function refresh(force=false){if(busy||document.hidden)return;busy=true;try{render(await jsonFetch(`api/state${force?'?force=1':''}`));}catch(error){if(error.status===401){showLogin('Sessione scaduta. Accedi di nuovo.');return;}render({configured:true,reachable:false,sessionActive:false,message:`Monitor non disponibile: ${error.message}`,snapshot:null});}finally{busy=false;}}
  document.addEventListener('click',event=>{const row=event.target.closest('[data-frame-id]');if(row?.dataset.frameId)selectFrame(row.dataset.frameId);});
  $('#loginForm').addEventListener('submit',async event=>{event.preventDefault();const password=$('#loginPassword').value,button=event.submitter||event.currentTarget.querySelector('button');button.disabled=true;try{await jsonFetch('api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})});$('#loginPassword').value='';$('#loginError').hidden=true;showApp();}catch(error){const retry=Number(error.payload?.retryAfter);showLogin(Number.isFinite(retry)?`Troppi tentativi. Riprova tra ${retry} s.`:error.message);}finally{button.disabled=false;}});
  $('#logoutButton').addEventListener('click',async()=>{try{await jsonFetch('api/logout',{method:'POST'});}catch(_){}showLogin();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!$('#appView').hidden)refresh(true);});
  (async()=>{try{const session=await jsonFetch('api/session');if(session.authenticated)showApp();else showLogin();}catch(_){showLogin('Il servizio non risponde correttamente.');}})();
})();
