(() => {
  'use strict';
  const q = id => document.getElementById(id);
  let authenticated = false, view = '', csrf = '', data = null, timer = null, request = null, busy = false, hls = null, loadedHours = null;
  const when = seconds => seconds ? new Date(seconds * 1000).toLocaleString('it-IT', {timeZone:'Europe/Rome'}) : 'In attesa';
  const bytes = n => n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GB` : `${(n / 1024 ** 2).toFixed(1)} MB`;
  function message(text, error = false) {
    q('serraMessage').textContent = text;
    q('serraMessage').classList.toggle('bad', error);
  }
  function controls() {
    for (const id of ['serraInterval','serraQuota','serraPause','serraCapture','serraFlush']) q(id).disabled = busy || !csrf || !data;
    q('serraDownload').setAttribute('aria-disabled', !data?.blocks ? 'true' : 'false');
  }
  function stopPlayer() {
    hls?.destroy(); hls = null; loadedHours = null;
    const video = q('serraVideo');
    video.pause(); video.removeAttribute('src'); video.load();
  }
  function stop() {
    clearTimeout(timer); timer = null;
    request?.abort(); request = null;
    q('serraLive').removeAttribute('src');
    stopPlayer();
  }
  function loadPlayer(force = false) {
    const hours = q('serraWindow').value;
    q('serraDownload').href = `/api/serra/export.mp4?hours=${hours}`;
    if (!data?.blocks || !authenticated || view !== 'serra' || document.hidden || (!force && loadedHours === hours)) return;
    stopPlayer(); loadedHours = hours;
    const video = q('serraVideo'), url = `/api/serra/playlist.m3u8?hours=${hours}`;
    video.muted = true;
    if (globalThis.Hls?.isSupported()) {
      hls = new Hls({startPosition:0, maxBufferLength:30, maxMaxBufferLength:120, liveSyncDurationCount:3, backBufferLength:7200});
      hls.loadSource(url); hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => { video.playbackRate = Number(q('serraSpeed').value); video.play().catch(() => {}); });
      hls.on(Hls.Events.ERROR, (_event, detail) => {
        if (detail.fatal) {
          message('Timelapse in aggiornamento. Premi Ricarica video per riprovare.', true);
          hls?.destroy(); hls = null; loadedHours = null;
        }
      });
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url; video.play().catch(() => {});
    } else {
      message('Il browser non supporta questa anteprima. Puoi scaricare il file MP4.', true);
    }
  }
  function render() {
    const settings = data.settings;
    const hasError = Boolean(data.last_error || data.encode_error);
    q('serraState').textContent = settings.enabled ? 'Acquisizione attiva' : 'Acquisizione in pausa';
    q('serraState').className = `status-chip ${settings.enabled && !hasError ? 'good' : 'warn'}`;
    q('serraPause').textContent = settings.enabled ? 'Metti in pausa' : 'Riprendi acquisizione';
    for (const [id, value] of [['serraInterval',settings.interval_seconds],['serraQuota',settings.quota_gb]]) {
      if (document.activeElement !== q(id)) q(id).value = String(value);
    }
    q('serraResolution').textContent = `${data.width} × ${data.height} · risoluzione nativa`;
    q('serraLast').textContent = when(data.last_capture);
    q('serraStorage').textContent = data.storage_online ? `${bytes(data.used_bytes)} / ${settings.quota_gb} GB` : 'NVMe scollegato';
    q('serraStorage').classList.toggle('warn', !data.storage_online);
    q('serraSpace').textContent = data.storage_online ? `${bytes(data.free_bytes)} liberi sul disco` : 'La raccolta riparte quando ricolleghi il disco';
    q('serraCount').textContent = `${data.frames.toLocaleString('it-IT')} fotogrammi nell’archivio`;
    q('serraPending').textContent = data.encoding ? 'Compressione di un piccolo blocco in corso' : `${data.pending_frames} scatti in attesa di compressione`;
    q('serraCoverage').textContent = data.oldest ? `${when(data.oldest)} → ${when(data.newest)}` : 'Il primo video arriva dopo almeno due scatti';
    q('serraTimelapseEmpty').hidden = Boolean(data.blocks);
    q('serraVideo').hidden = !data.blocks;
    q('serraUpdated').textContent = data.last_compact ? `Ultimo blocco: ${when(data.last_compact)}` : 'In preparazione';
    message(data.last_error || data.encode_error || 'Raccolta continua. Al limite di spazio vengono sostituiti i blocchi più vecchi.', hasError);
    controls(); loadPlayer();
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
      if (!q('serraLive').getAttribute('src')) q('serraLive').src = `/api/serra/live?t=${Date.now()}`;
    } catch (error) {
      if (request === active) message(error.name === 'AbortError' ? 'Aggiornamento in ritardo: nuovo tentativo automatico.' : error.message, true);
    } finally {
      clearTimeout(timeout);
      if (request === active) { request = null; clearTimeout(timer); timer = setTimeout(refresh, 10000); }
    }
  }
  async function action(payload) {
    if (busy || !csrf) return;
    busy = true; controls(); message('Operazione in corso…');
    try {
      const response = await fetch('/api/serra/action', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify(payload), signal:AbortSignal.timeout(20000)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Operazione non riuscita.');
      await refresh();
    } catch (error) { message(error.message, true); }
    finally { busy = false; controls(); }
  }
  function save() {
    action({action:'settings', enabled:data.settings.enabled, interval_seconds:Number(q('serraInterval').value), quota_gb:Number(q('serraQuota').value)});
  }
  function sync() {
    stop();
    if (authenticated && view === 'serra' && !document.hidden) refresh();
  }
  globalThis.OpenAstroSerra = {
    setView(value) { if (view !== value) { view = value; sync(); } },
    setAuthenticated(value) { authenticated = Boolean(value); if (!authenticated) { csrf = ''; data = null; } sync(); },
    update(value) { csrf = value?.csrf || csrf; controls(); },
  };
  if (!q('serraLive')) return;
  q('serraPause').addEventListener('click', () => action({action:'settings', enabled:!data.settings.enabled, interval_seconds:data.settings.interval_seconds, quota_gb:data.settings.quota_gb}));
  q('serraInterval').addEventListener('change', save);
  q('serraQuota').addEventListener('change', save);
  q('serraCapture').addEventListener('click', () => action({action:'capture'}));
  q('serraFlush').addEventListener('click', () => action({action:'flush'}));
  q('serraRetry').addEventListener('click', sync);
  q('serraWindow').addEventListener('change', () => loadPlayer(true));
  q('serraReloadVideo').addEventListener('click', () => loadPlayer(true));
  q('serraSpeed').addEventListener('change', () => { q('serraVideo').playbackRate = Number(q('serraSpeed').value); });
  q('serraLatest').addEventListener('click', () => {
    const video = q('serraVideo');
    if (video.seekable.length) video.currentTime = Math.max(0, video.seekable.end(video.seekable.length - 1) - 1);
    video.play().catch(() => {});
  });
  q('serraDownload').addEventListener('click', event => { if (!data?.blocks) event.preventDefault(); });
  q('serraLive').addEventListener('error', () => {
    if (authenticated && view === 'serra') { q('serraLive').removeAttribute('src'); message('Diretta interrotta. Riconnessione automatica tra pochi secondi.', true); }
  });
  document.addEventListener('visibilitychange', sync);
})();
