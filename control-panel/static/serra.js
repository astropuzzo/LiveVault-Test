(() => {
  'use strict';
  const q = id => document.getElementById(id);
  let authenticated = false, view = '', csrf = '', data = null, timer = null, request = null, busy = false, loadedHours = null, loadedVersion = null;
  let liveEnabled = true, liveVisible = false, playerVisible = false, resumePosition = 0, resumeHours = null;
  let previewRequest = null, pendingSeek = 0;
  let diagnosticTimer = null, diagnosticRequest = null, diagnosticCount = 0;
  let bufferTimer = null, nativeBuffering = false;
  let loadedRotation = null, resumeRotation = null, cleanupPlan = null;
  const playbackEvents = [];
  const diagnosePlayback = new URLSearchParams(globalThis.location?.search || '').get('serraDebug') === '1';
  const archiveRotation = () => Number(q('serraArchive').value || data?.settings.rotation_degrees || 0);
  const selectedArchive = () => {
    if (!data?.orientations) return data;
    const rows = data.orientations.filter(item=>item.rotation===archiveRotation());
    if (Number(q('serraWindow').value)>720) return rows.find(item=>item.tier==='daylight') || {blocks:0,frames:0,tier:'daylight'};
    return (Number(q('serraWindow').value)>72 ? rows.find(item=>item.tier==='history') : null) || rows.find(item=>item.tier==='recent') || {blocks:0};
  };
  async function reportPlayback() {
    diagnosticTimer = null;
    if (!diagnosePlayback || diagnosticCount >= 8 || !authenticated || !csrf || view !== 'serra' || document.hidden || !playerVisible || loadedHours === null) return;
    const video = q('serraVideo'), quality = video.getVideoPlaybackQuality?.();
    const report = {build:'20261104-7', user_agent:navigator.userAgent.slice(0,256),
      position:video.currentTime, duration:Number.isFinite(video.duration) ? video.duration : null,
      paused:video.paused, ready:video.readyState, network:video.networkState,
      visible:playerVisible, hidden:document.hidden, width:video.videoWidth, height:video.videoHeight,
      buffered:Array.from({length:Math.min(video.buffered.length,4)},(_,i)=>[video.buffered.start(i),video.buffered.end(i)]),
      decoded_frames:quality?.totalVideoFrames ?? video.mozDecodedFrames ?? null,
      dropped_frames:quality?.droppedVideoFrames ?? null, error_code:video.error?.code || 0,
      error_message:(video.error?.message || '').slice(0,256), events:playbackEvents.slice(-12)};
    diagnosticCount++;
    const active = new AbortController(); diagnosticRequest = active;
    const timeout = setTimeout(() => active.abort(),5000);
    try {
      await fetch('/api/serra/action',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
        body:JSON.stringify({action:'playback-report',report}),signal:active.signal});
    } catch (_) { /* Diagnostics must never interrupt playback. */ }
    finally {
      clearTimeout(timeout);
      if (diagnosticRequest === active) {
        diagnosticRequest = null;
        if (diagnosticCount < 8) diagnosticTimer = setTimeout(reportPlayback,3000);
      }
    }
  }
  const when = seconds => seconds ? new Date(seconds * 1000).toLocaleString('it-IT', {timeZone:'Europe/Rome'}) : 'In attesa';
  const bytes = n => n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GB` : `${(n / 1024 ** 2).toFixed(1)} MB`;
  function message(text, error = false) {
    q('serraMessage').textContent = text;
    q('serraMessage').classList.toggle('bad', error);
  }
  function controls() {
    for (const id of ['serraInterval','serraQuota','serraRotation','serraPause','serraCapture','serraFlush','serraCleanupPreview']) q(id).disabled = busy || !csrf || !data;
    q('serraCleanupDelete').disabled = busy || !csrf || !cleanupPlan?.frames;
    q('serraDownload').setAttribute('aria-disabled', !selectedArchive()?.blocks ? 'true' : 'false');
  }
  function checkNativeBuffer() {
    clearTimeout(bufferTimer); bufferTimer = null;
    if (!nativeBuffering || !authenticated || view !== 'serra' || document.hidden || !playerVisible || loadedHours === null) return;
    const video = q('serraVideo'), position = video.currentTime || 0;
    const remaining = Number.isFinite(video.duration) ? Math.max(0,video.duration-position) : Infinity;
    const required = Math.min(remaining,30,Math.max(8,6 * Number(q('serraSpeed').value)));
    let available = 0;
    for (let i=0;i<video.buffered.length;i++) {
      if (video.buffered.start(i) <= position+.05 && video.buffered.end(i) > position) available = Math.max(available,video.buffered.end(i)-position);
    }
    if (video.readyState >= 2 && Number.isFinite(required) && available+.05 >= required) {
      nativeBuffering = false;
      q('serraBufferStatus').textContent = '';
      playbackEvents.push({event:'buffer-ready-v8',position,at:Date.now()});
      video.play().catch(error => {
        if (error.name === 'NotAllowedError') q('serraBufferStatus').textContent = 'Video pronto: tocca Play.';
      });
      return;
    }
    const percent = Number.isFinite(required) && required > 0 ? Math.min(99,Math.floor(100*available/required)) : 0;
    q('serraBufferStatus').textContent = `Preparazione video · ${percent}%`;
    bufferTimer = setTimeout(checkNativeBuffer,500);
  }
  function prepareNativeBuffer() {
    if (loadedHours === null || !authenticated || !playerVisible || document.hidden) return;
    nativeBuffering = true;
    const video = q('serraVideo');
    video.preload = 'auto';
    video.pause();
    playbackEvents.push({event:'buffer-start-v8',position:video.currentTime || 0,at:Date.now()});
    if (playbackEvents.length > 12) playbackEvents.shift();
    checkNativeBuffer();
  }
  function stopPlayer() {
    clearTimeout(bufferTimer); bufferTimer = null; nativeBuffering = false;
    q('serraBufferStatus').textContent = '';
    clearTimeout(diagnosticTimer); diagnosticTimer = null;
    diagnosticRequest?.abort(); diagnosticRequest = null;
    previewRequest?.abort(); previewRequest = null;
    if (loadedHours !== null) { resumePosition = q('serraVideo').currentTime || 0; resumeHours = loadedHours; resumeRotation = loadedRotation; }
    loadedHours = null; loadedVersion = null; loadedRotation = null;
    const video = q('serraVideo');
    video.preload = 'none';
    video.pause(); video.removeAttribute('src'); video.load();
  }
  function stop() {
    clearTimeout(timer); timer = null;
    request?.abort(); request = null;
    q('serraLive').removeAttribute('src');
    stopPlayer();
  }
  function syncLive() {
    const watch = authenticated && view === 'serra' && !document.hidden && liveEnabled && liveVisible;
    const image = q('serraLive');
    if (watch && !image.getAttribute('src')) image.src = `/api/serra/live?t=${Date.now()}`;
    if (!watch) image.removeAttribute('src');
    q('serraRetry').textContent = liveEnabled ? 'Ferma diretta' : 'Avvia diretta';
    q('serraLiveStatus').textContent = watch ? 'Diretta locale · 3 fotogrammi al secondo' : 'Diretta sospesa · la raccolta delle foto continua';
    image.closest('.serra-frame').classList.toggle('is-paused', !watch);
  }
  function loadPlayer(force = false) {
    const hours = q('serraWindow').value, rotation = archiveRotation(), archive = selectedArchive();
    const version = `${archive?.newest || data?.last_compact}:${data?.last_cleanup || 0}:${['history','daylight'].includes(archive?.tier) ? data?.last_history_maintenance || 0 : 0}`;
    q('serraDownload').href = `/api/serra/export.mp4?hours=${hours}&rotation=${rotation}`;
    if (!force && loadedHours === hours && loadedRotation === rotation && q('serraVideo').paused) return;
    if (!archive?.blocks || !authenticated || view !== 'serra' || document.hidden || !playerVisible || (!force && loadedHours === hours && loadedRotation === rotation && loadedVersion === version)) return;
    const previousPosition = !force ? (loadedHours === hours && loadedRotation === rotation ? q('serraVideo').currentTime : resumeHours === hours && resumeRotation === rotation ? resumePosition : 0) : 0;
    stopPlayer(); loadedHours = hours; loadedRotation = rotation; loadedVersion = version;
    const video = q('serraVideo'), url = `/api/serra/playlist.m3u8?hours=${hours}&rotation=${rotation}`;
    video.muted = true; video.loop = true;
    // The complete index exposes duration and seek ranges before playback.
    // All video bytes are read directly from the existing archive chunks.
    const active = new AbortController(); previewRequest = active;
    const timeout = setTimeout(() => active.abort(), 20000);
    fetch(`/api/serra/preview-info?hours=${hours}&rotation=${rotation}`, {cache:'no-store', signal:active.signal})
      .then(async response => {
        const info = await response.json();
        if (!response.ok) throw new Error(info.error || 'Anteprima non disponibile.');
        if (previewRequest !== active || !authenticated || view !== 'serra' || document.hidden || !playerVisible) return;
        pendingSeek = previousPosition;
        nativeBuffering = true;
        video.preload = 'auto';
        video.src = info.url;
        video.load();
        if (diagnosePlayback && diagnosticCount < 8) diagnosticTimer = setTimeout(reportPlayback,3000);
        prepareNativeBuffer();
      })
      .catch(error => { if (previewRequest === active && error.name !== 'AbortError') { loadedHours = null; message(error.message,true); } })
      .finally(() => { clearTimeout(timeout); if (previewRequest === active) previewRequest = null; });
  }

  function render(reload = true) {
    const settings = data.settings;
    const hasError = Boolean(data.last_error || data.encode_error || data.history_error);
    q('serraState').textContent = settings.enabled ? 'Acquisizione attiva' : 'Acquisizione in pausa';
    q('serraState').className = `status-chip ${settings.enabled && !hasError ? 'good' : 'warn'}`;
    q('serraPause').textContent = settings.enabled ? 'Metti in pausa' : 'Riprendi acquisizione';
    for (const [id, value] of [['serraInterval',settings.interval_seconds],['serraQuota',settings.quota_gb],['serraRotation',settings.rotation_degrees || 0]]) {
      if (document.activeElement !== q(id)) q(id).value = String(value);
    }
    q('serraResolution').textContent = `${data.width} × ${data.height} · risoluzione nativa`;
    const rotation = settings.rotation_degrees || 0;
    q('serraLive').style.setProperty('--serra-rotation',`${rotation}deg`);
    q('serraLive').style.setProperty('--serra-rotation-scale',[90,270].includes(rotation) ? '0.5625' : '1');
    const prior = q('serraArchive').value;
    const previous = [...new Set((data.orientations || []).map(item=>item.rotation))].filter(angle=>angle!==rotation).map(angle=>({rotation:angle}));
    q('serraArchive').innerHTML = `<option value="">Attuali · ${rotation}°</option>` + previous.map(item=>`<option value="${item.rotation}">Precedenti · ${item.rotation}°</option>`).join('');
    q('serraArchive').value = previous.some(item=>String(item.rotation)===prior) ? prior : '';
    q('serraArchiveLabel').hidden = !previous.length;
    q('serraLast').textContent = when(data.last_capture);
    q('serraStorage').textContent = data.storage_online ? `${bytes(data.used_bytes)} / ${settings.quota_gb} GB` : 'NVMe scollegato';
    q('serraStorage').classList.toggle('warn', !data.storage_online);
    q('serraSpace').textContent = data.storage_online ? `${bytes(data.free_bytes)} liberi sul disco` : 'La raccolta riparte quando ricolleghi il disco';
    q('serraCount').textContent = `${data.frames.toLocaleString('it-IT')} fotogrammi nell’archivio`;
    q('serraPending').textContent = data.encoding ? 'Compressione di un piccolo blocco in corso' : `${data.pending_frames} scatti in attesa di compressione`;
    const archive = selectedArchive();
    q('serraCoverage').textContent = archive?.oldest ? `${when(archive.oldest)} → ${when(archive.newest)}` : 'Il primo video arriva dopo almeno due scatti';
    q('serraSampling').textContent = archive?.tier==='daylight' ? `Solo foto delle 10:00, 14:00 e 18:00, ora italiana · ${archive.frames || 0} fotogrammi disponibili. Gli orari mancanti non vengono sostituiti da foto notturne.` : archive?.tier==='history' ? 'Storico lungo: una foto all’ora nei primi 30 giorni; poi alle 10:00, 14:00 e 18:00, ora italiana. Conservazione fino a un anno entro il limite di spazio.' : 'Dettaglio recente: usa tutti gli scatti dell’intervallo scelto. Lo storico lungo si raccoglie automaticamente.';
    q('serraTimelapseEmpty').textContent = archive?.tier==='daylight' ? 'In attesa di una foto delle 10:00, 14:00 o 18:00. Gli scatti vengono aggiunti al video durante la giornata.' : 'Il timelapse sarà disponibile dopo i primi scatti compressi.';
    q('serraTimelapseEmpty').hidden = Boolean(archive?.blocks);
    q('serraVideo').hidden = !archive?.blocks;
    if (!archive?.blocks && loadedHours !== null) stopPlayer();
    q('serraUpdated').textContent = data.last_compact ? `Ultimo blocco: ${when(data.last_compact)}` : 'In preparazione';
    message(data.last_error || data.encode_error || data.history_error || 'Raccolta continua. Al limite di spazio vengono sostituiti prima i dettagli più vecchi, conservando lo storico diradato.', hasError);
    controls(); if (reload) loadPlayer();
  }
  async function refresh() {
    if (!authenticated || view !== 'serra' || document.hidden) return;
    request?.abort(); const active = new AbortController(); request = active;
    const timeout = setTimeout(() => active.abort(), 12000);
    try {
      const response = await fetch('/api/serra/status', {cache:'no-store', signal:active.signal});
      if (response.status === 401) { authenticated = false; data = null; stop(); return; }
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Stato della serra non disponibile.');
      if (request !== active) return;
      data = result; render();
      syncLive();
    } catch (error) {
      if (request === active) message(error.name === 'AbortError' ? 'Aggiornamento in ritardo: nuovo tentativo automatico.' : error.message, true);
    } finally {
      clearTimeout(timeout);
      if (request === active) { request = null; clearTimeout(timer); timer = setTimeout(refresh, 10000); }
    }
  }
  async function action(payload, onError) {
    if (busy || !csrf) return;
    busy = true; controls(); message('Operazione in corso…');
    try {
      const response = await fetch('/api/serra/action', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify(payload), signal:AbortSignal.timeout(20000)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Operazione non riuscita.');
      await refresh();
      return result;
    } catch (error) { message(error.message, true); onError?.(error.message); }
    finally { busy = false; controls(); }
  }
  function save() {
    action({action:'settings', enabled:data.settings.enabled, interval_seconds:Number(q('serraInterval').value), quota_gb:Number(q('serraQuota').value), rotation_degrees:Number(q('serraRotation').value)});
  }
  function resetCleanup() {
    cleanupPlan = null;
    const custom = q('serraCleanupPeriod').value === 'custom';
    q('serraCleanupFrom').disabled = !custom; q('serraCleanupTo').disabled = !custom;
    cleanupMessage('Nessun file selezionato.');
    controls();
  }
  function cleanupMessage(text, error = false) {
    q('serraCleanupResult').textContent = text;
    q('serraCleanupResult').classList.toggle('bad',error);
  }
  async function cleanup(execute) {
    if (busy) return;
    if (!csrf) return cleanupMessage('Sessione scaduta: accedi di nuovo prima di calcolare la pulizia.',true);
    let payload;
    if (execute) {
      if (!cleanupPlan?.frames || !globalThis.confirm(`Eliminare definitivamente ${cleanupPlan.frames} fotogrammi (${bytes(cleanupPlan.bytes)}) da ${cleanupPlan.from_ts ? when(cleanupPlan.from_ts) : 'inizio archivio'} a ${when(cleanupPlan.to_ts)}?`)) return;
      payload = {action:'cleanup-delete',...cleanupPlan};
    } else if (q('serraCleanupPeriod').value !== 'custom') {
      payload = {action:'cleanup-preview',older_than_hours:Number(q('serraCleanupPeriod').value)};
    } else {
      const start = q('serraCleanupFrom').value ? new Date(q('serraCleanupFrom').value).getTime()/1000 : 0;
      const end = new Date(q('serraCleanupTo').value).getTime()/1000;
      if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end) return cleanupMessage('Scegli un intervallo da/a valido.',true);
      payload = {action:'cleanup-preview',from_ts:start,to_ts:end};
    }
    if (!execute) payload.include_history = q('serraCleanupHistory').checked;
    cleanupMessage(execute ? 'Pulizia in corso…' : 'Calcolo dei file e dello spazio…');
    const result = await action(payload, error => cleanupMessage(error,true));
    if (!result) { cleanupPlan = null; controls(); return; }
    cleanupPlan = execute ? null : result;
    cleanupMessage(execute ? `Eliminati ${result.frames} fotogrammi · ${bytes(result.bytes)} liberati.` : result.frames ? `${result.frames} fotogrammi · ${bytes(result.bytes)} da liberare. Nessun file è stato eliminato.` : 'Calcolo completato: nessun file eliminabile nel periodo scelto. I blocchi che attraversano i limiti del periodo e gli scatti in compressione sono protetti.');
    if (execute) { stopPlayer(); await refresh(); }
    controls();
  }
  function sync() {
    stop();
    syncLive();
    if (authenticated && view === 'serra' && !document.hidden) refresh();
  }
  globalThis.OpenAstroSerra = {
    setView(value) { if (view !== value) { view = value; sync(); } },
    setAuthenticated(value) {
      const next = Boolean(value);
      // The main panel confirms the same session after every telemetry update.
      // Only a real login/logout transition should reset the media lifecycle.
      if (authenticated === next) return;
      authenticated = next;
      sync();
      if (!authenticated) { csrf = ''; data = null; resumePosition = 0; resumeHours = null; resumeRotation = null; cleanupPlan = null; }
    },
    update(value) { csrf = value?.csrf || csrf; controls(); },
  };
  if (!q('serraLive')) return;
  if (diagnosePlayback) for (const event of ['loadstart','emptied','play','playing','pause','waiting','stalled','error','suspend','ended']) {
    q('serraVideo').addEventListener(event, () => {
      playbackEvents.push({event,position:q('serraVideo').currentTime,at:Date.now()});
      if (playbackEvents.length > 12) playbackEvents.shift();
    });
  }
  q('serraPause').addEventListener('click', () => action({action:'settings', enabled:!data.settings.enabled, interval_seconds:data.settings.interval_seconds, quota_gb:data.settings.quota_gb,rotation_degrees:data.settings.rotation_degrees || 0}));
  q('serraRotation').addEventListener('change',save);
  q('serraArchive').addEventListener('change',()=>{render(false);loadPlayer(true);});
  q('serraCleanupHistory').addEventListener('change',resetCleanup);
  q('serraCleanupPeriod').addEventListener('change',resetCleanup);
  q('serraCleanupFrom').addEventListener('change',resetCleanup);
  q('serraCleanupTo').addEventListener('change',resetCleanup);
  q('serraCleanupPreview').addEventListener('click',()=>cleanup(false));
  q('serraCleanupDelete').addEventListener('click',()=>cleanup(true));
  q('serraInterval').addEventListener('change', save);
  q('serraQuota').addEventListener('change', save);
  q('serraCapture').addEventListener('click', () => action({action:'capture'}));
  q('serraFlush').addEventListener('click', () => action({action:'flush'}));
  q('serraRetry').addEventListener('click', () => { liveEnabled = !liveEnabled; syncLive(); });
  q('serraWindow').addEventListener('change', () => {render(false);loadPlayer(true);});
  q('serraReloadVideo').addEventListener('click', () => loadPlayer(true));
  q('serraSpeed').addEventListener('change', () => { q('serraVideo').playbackRate = Number(q('serraSpeed').value); });
  q('serraLatest').addEventListener('click', () => {
    const video = q('serraVideo');
    if (video.seekable.length) video.currentTime = Math.max(0, video.seekable.end(video.seekable.length - 1) - 1);
    video.play().catch(() => {});
  });
  q('serraDownload').addEventListener('click', event => { if (!selectedArchive()?.blocks) event.preventDefault(); });
  q('serraLive').addEventListener('error', () => {
    if (authenticated && view === 'serra') { q('serraLive').removeAttribute('src'); message('Diretta interrotta. Riconnessione automatica tra pochi secondi.', true); }
  });
  q('serraVideo').addEventListener('loadedmetadata', () => {
    const video = q('serraVideo');
    video.playbackRate = Number(q('serraSpeed').value);
    if (pendingSeek > 0 && Number.isFinite(video.duration)) video.currentTime = Math.min(pendingSeek,Math.max(0,video.duration-.01));
    pendingSeek = 0;
    checkNativeBuffer();
  });
  for (const event of ['progress','loadeddata','canplay','canplaythrough','seeked']) q('serraVideo').addEventListener(event,checkNativeBuffer);
  q('serraVideo').addEventListener('waiting', () => {
    if (!q('serraVideo').paused) prepareNativeBuffer();
  });
  q('serraVideo').addEventListener('play', () => {
    if (nativeBuffering) { q('serraVideo').pause(); checkNativeBuffer(); return; }
  });
  if ('IntersectionObserver' in globalThis) {
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (entry.target === q('serraLive')) { liveVisible = entry.isIntersecting; syncLive(); }
        if (entry.target === q('serraVideo')) {
          playerVisible = entry.isIntersecting;
          if (playerVisible) loadPlayer(); else stopPlayer();
        }
      }
    }, {threshold:0.01});
    observer.observe(q('serraLive')); observer.observe(q('serraVideo'));
  } else { liveVisible = true; playerVisible = true; }
  document.addEventListener('visibilitychange', sync);
})();
