(() => {
  const $u = selector => document.querySelector(selector);
  const host = $u('#mediaUploadCard');
  if (!host) return;

  const input = $u('#mediaUploadInput');
  const drop = $u('#mediaDropZone');
  const choose = $u('#mediaUploadChoose');
  const queueHost = $u('#mediaUploadQueue');
  const queueList = $u('#mediaUploadQueueList');
  const queueSummary = $u('#mediaUploadQueueSummary');
  const pathLabel = $u('#mediaUploadPath');
  const route = $u('#mediaUploadRoute');
  const routeChip = $u('#mediaUploadRouteChip');
  const copySmb = $u('#mediaUploadCopySmb');
  const SMB_PATH = '\\\\OPENASTRO\\Media';
  let queue = [];
  let working = false;

  if (typeof actionLabels !== 'undefined') {
    actionLabels.media_rescan = ['Aggiorna supporti media', 'Rileva i dispositivi USB rimovibili consentiti e li prepara per import autenticato.'];
  }

  function routeState() {
    const hostName = location.hostname.toLowerCase();
    const privateIp = /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(hostName);
    const localName = hostName === 'openastro' || hostName.endsWith('.local');
    if (privateIp || localName) {
      routeChip.textContent = 'LAN DIRETTA';
      routeChip.dataset.route = 'lan';
      route.textContent = 'Sei già sul percorso locale. Per film molto grandi SMB resta la via più veloce.';
    } else if (hostName.endsWith('.ts.net')) {
      routeChip.textContent = 'LAN → WEB';
      routeChip.dataset.route = 'auto';
      route.textContent = 'Priorità LAN: usa SMB quando sei sulla stessa rete. Questo uploader HTTPS è il fallback remoto.';
    } else {
      routeChip.textContent = 'LAN → WEB';
      routeChip.dataset.route = 'remote';
      route.textContent = 'Priorità LAN: usa SMB quando disponibile; altrimenti carica qui via HTTPS.';
    }
  }

  function selectedDevice() {
    if (typeof latestState === 'undefined' || !latestState?.media?.devices || typeof mediaUuid === 'undefined') return null;
    return latestState.media.devices.find(device => device.uuid === mediaUuid) || null;
  }

  function syncTarget() {
    const device = selectedDevice();
    const selected = Boolean(typeof mediaUuid !== 'undefined' && mediaUuid && device?.mounted);
    const writable = selected && device?.writable !== false;
    host.classList.toggle('disabled', !writable);
    input.disabled = !writable || working;
    choose.disabled = !writable || working;
    const current = typeof mediaPath !== 'undefined' ? mediaPath : '';
    pathLabel.textContent = selected ? `/${current || ''}` : 'nessun supporto';
    drop.setAttribute('aria-disabled', String(!writable || working));
    if (selected && !writable) pathLabel.textContent += ' · sola lettura';

    const model = $u('#mediaDriveModel');
    if (model && selected) {
      model.textContent = model.textContent.replace(/READ-ONLY|IMPORT RW/g, writable ? 'IMPORT RW' : 'READ-ONLY');
    }
  }

  function rowMarkup(item) {
    const percent = item.total ? Math.max(0, Math.min(100, item.loaded / item.total * 100)) : 0;
    const state = item.state === 'done' ? 'Completato' : item.state === 'error' ? item.error : item.state === 'uploading' ? `${percent.toFixed(0)}%` : 'In coda';
    return `<article class="media-upload-row ${item.state}"><div class="media-upload-file"><strong title="${escapeHtml(item.file.name)}">${escapeHtml(item.file.name)}</strong><small>${bytes(item.file.size)} · ${escapeHtml(state)}</small></div><div class="media-upload-progress"><i style="width:${percent.toFixed(2)}%"></i></div></article>`;
  }

  function renderQueue() {
    queueHost.hidden = queue.length === 0;
    queueList.innerHTML = queue.map(rowMarkup).join('');
    const done = queue.filter(item => item.state === 'done').length;
    const errors = queue.filter(item => item.state === 'error').length;
    const active = queue.find(item => item.state === 'uploading');
    queueSummary.textContent = active ? `Caricamento ${queue.indexOf(active) + 1}/${queue.length}` : errors ? `${done} completati · ${errors} errori` : `${done}/${queue.length} completati`;
  }

  function addFiles(files) {
    const device = selectedDevice();
    if (!mediaUuid || !device?.mounted) return toast('Collega e seleziona prima un supporto Media.', true);
    if (device.writable === false) return toast('Il supporto è ancora in sola lettura. Premi Rileva USB per prepararlo all’import.', true);
    const incoming = [...files].filter(file => file && file.size >= 0);
    if (!incoming.length) return;
    for (const file of incoming) queue.push({file, state:'queued', loaded:0, total:file.size, error:''});
    renderQueue();
    runQueue();
  }

  function uploadOne(item) {
    return new Promise(resolve => {
      const destination = typeof mediaPath !== 'undefined' ? mediaPath : '';
      const params = new URLSearchParams({uuid:mediaUuid, path:destination || '', name:item.file.name});
      const xhr = new XMLHttpRequest();
      item.state = 'uploading'; item.loaded = 0; item.total = item.file.size; renderQueue();
      xhr.open('POST', `/api/media/upload?${params}`);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('X-CSRF-Token', csrf);
      xhr.upload.onprogress = event => {
        if (event.lengthComputable) { item.loaded = event.loaded; item.total = event.total; renderQueue(); }
      };
      xhr.onerror = () => {
        item.state = 'error'; item.error = 'Connessione interrotta'; renderQueue(); resolve(false);
      };
      xhr.onabort = () => {
        item.state = 'error'; item.error = 'Upload annullato'; renderQueue(); resolve(false);
      };
      xhr.onload = () => {
        let result = {};
        try { result = JSON.parse(xhr.responseText || '{}'); } catch (_) {}
        if (xhr.status >= 200 && xhr.status < 300 && result.ok) {
          item.state = 'done'; item.loaded = item.total; item.error = '';
          toast(`${item.file.name} caricato sul Media Center.`);
          resolve(true);
        } else {
          item.state = 'error'; item.error = result.error || `HTTP ${xhr.status}`;
          toast(item.error, true); resolve(false);
        }
        renderQueue();
      };
      xhr.send(item.file);
    });
  }

  async function runQueue() {
    if (working) return;
    working = true; syncTarget();
    try {
      for (const item of queue) {
        if (item.state !== 'queued') continue;
        if (!mediaUuid) { item.state='error'; item.error='Supporto scollegato'; renderQueue(); break; }
        await uploadOne(item);
      }
      if (typeof loadMediaDirectory === 'function' && mediaUuid) await loadMediaDirectory(mediaPath || '');
      if (typeof loadMediaLibrary === 'function' && mediaUuid) await loadMediaLibrary(true);
      if (typeof loadMediaHome === 'function') await loadMediaHome(true);
      if (typeof refresh === 'function') await refresh();
    } finally {
      working = false; syncTarget(); renderQueue();
    }
  }

  choose.addEventListener('click', event => { event.preventDefault(); if (!input.disabled) input.click(); });
  input.addEventListener('change', () => { addFiles(input.files); input.value=''; });
  ['dragenter','dragover'].forEach(name => drop.addEventListener(name, event => {
    event.preventDefault(); if (!input.disabled) drop.classList.add('dragging');
  }));
  ['dragleave','drop'].forEach(name => drop.addEventListener(name, event => {
    event.preventDefault(); drop.classList.remove('dragging');
  }));
  drop.addEventListener('drop', event => { if (!input.disabled) addFiles(event.dataTransfer?.files || []); });
  copySmb.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(SMB_PATH); toast('Percorso SMB copiato: ' + SMB_PATH); }
    catch (_) { toast(SMB_PATH); }
  });

  routeState(); syncTarget(); renderQueue();
  setInterval(syncTarget, 1000);
})();
