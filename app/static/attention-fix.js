/* LiveVault attention navigation hotfix
 * Makes Dashboard "Controlla errori" open the exact recordings that need attention
 * and makes those records visually unmistakable in the Archive.
 */
(() => {
  const attentionReasons = recording => {
    const reasons = [];
    if (recording.has_video === false) reasons.push('Video assente');
    if (recording.has_audio === false) reasons.push('Audio assente');
    if (recording.integrity_status === 'failed' || recording.upload_status === 'integrity_failed') reasons.push('Integrità fallita');
    if (recording.upload_status === 'failed') reasons.push('Upload fallito');
    if (recording.upload_status === 'waiting_config') reasons.push('Configurazione cloud mancante');
    if (!reasons.length && String(recording.last_error || '').trim()) reasons.push('Errore registrazione');
    return [...new Set(reasons)];
  };

  const needsAttention = recording => attentionReasons(recording).length > 0;

  function ensureAttentionFilters() {
    const select = $('#recordingStatus');
    if (!select) return;
    const values = new Set([...select.options].map(option => option.value));
    if (!values.has('attention')) {
      const option = document.createElement('option');
      option.value = 'attention';
      option.textContent = 'Da controllare';
      select.insertBefore(option, select.options[1] || null);
    }
    if (!values.has('audio_missing')) {
      const option = document.createElement('option');
      option.value = 'audio_missing';
      option.textContent = 'Audio assente';
      const integrity = [...select.options].find(item => item.value === 'integrity_failed');
      if (integrity?.nextSibling) select.insertBefore(option, integrity.nextSibling);
      else select.append(option);
    }
  }

  function cardRecordingId(card) {
    const explicit = Number(card.dataset.recordingId || 0);
    if (explicit) return explicit;
    return Number(card.querySelector('[data-rec-action][data-id]')?.dataset.id || 0);
  }

  function decorateAttentionCards() {
    ensureAttentionFilters();
    const root = $('#recordings');
    if (!root) return;
    const byId = new Map(recordings.map(recording => [Number(recording.id), recording]));
    const filtered = $('#recordingStatus')?.value;
    for (const card of root.querySelectorAll('.rec-card')) {
      const recording = byId.get(cardRecordingId(card));
      if (!recording) continue;
      card.dataset.recordingId = String(recording.id);
      const reasons = attentionReasons(recording);
      card.classList.toggle('lv-attention', reasons.length > 0);
      card.classList.toggle('lv-audio-missing', recording.has_audio === false);
      const old = card.querySelector('.lv-attention-banner');
      if (old) old.remove();
      if (reasons.length) {
        const banner = document.createElement('div');
        banner.className = 'lv-attention-banner';
        banner.innerHTML = `<strong>DA CONTROLLARE</strong><span>${esc(reasons.join(' · '))}</span>`;
        const body = card.querySelector('.rec-body') || card;
        body.prepend(banner);
      }
    }

    const focusMode = filtered === 'attention' || filtered === 'audio_missing';
    if (focusMode) {
      for (const group of root.querySelectorAll('.archive-group')) {
        if (group.querySelector('.rec-card.lv-attention')) group.open = true;
      }
    }
  }

  const baseRecordingMatches = recordingMatches;
  recordingMatches = function recordingMatchesWithAttention(recording) {
    const status = $('#recordingStatus')?.value || 'all';
    if (status !== 'attention' && status !== 'audio_missing') return baseRecordingMatches(recording);
    const query = ($('#recordingSearch')?.value || '').trim().toLocaleLowerCase('it');
    const statusMatch = status === 'attention' ? needsAttention(recording) : recording.has_audio === false;
    return (!sourceFilterId || recording.source_id === sourceFilterId)
      && statusMatch
      && (!query || `${recording.source_name} ${recording.filename} ${recording.session_id}`.toLocaleLowerCase('it').includes(query));
  };

  const baseRenderRecordingsAttention = renderRecordings;
  renderRecordings = function renderRecordingsAttention() {
    const result = baseRenderRecordingsAttention();
    decorateAttentionCards();
    return result;
  };

  function clearArchiveNarrowing() {
    sourceFilterId = 0;
    if ($('#recordingSearch')) $('#recordingSearch').value = '';
    $('#sourceFilterBar')?.classList.add('hidden');
    const url = new URL(location.href);
    url.searchParams.delete('source');
    url.hash = 'archive';
    history.replaceState({}, '', url);
  }

  function attentionRecordingCount() {
    return recordings.filter(needsAttention).length;
  }

  function flashFirstAttention() {
    requestAnimationFrame(() => {
      decorateAttentionCards();
      const first = $('#recordings .rec-card.lv-attention');
      if (!first) {
        $('#archive')?.scrollIntoView({behavior: 'smooth', block: 'start'});
        return;
      }
      const group = first.closest('.archive-group');
      if (group) group.open = true;
      first.classList.remove('lv-attention-focus');
      void first.offsetWidth;
      first.classList.add('lv-attention-focus');
      first.scrollIntoView({behavior: 'smooth', block: 'center'});
      setTimeout(() => first.classList.remove('lv-attention-focus'), 3600);
    });
  }

  openSystemAttention = async function openSystemAttentionTargeted() {
    const workerErrors = Object.keys(statusData?.worker?.errors || {}).length;
    const statusHints = Number(statusData?.queue?.integrity_failed || 0)
      + Number(statusData?.queue?.failed || 0)
      + Number(statusData?.queue?.waiting_config || 0)
      + Number(statusData?.history?.audio_missing || 0);

    // Worker/runtime errors live in their own diagnostic panel. If there are also
    // recording problems, prefer the actionable recording rows and keep the panel
    // available when the user returns to Dashboard.
    if (!statusHints && workerErrors && !$('#errorsPanel').classList.contains('hidden')) {
      $('#errorsPanel').scrollIntoView({behavior: 'smooth', block: 'start'});
      $('#errorsPanel').classList.add('lv-attention-focus');
      setTimeout(() => $('#errorsPanel')?.classList.remove('lv-attention-focus'), 3600);
      return;
    }

    clearArchiveNarrowing();
    ensureAttentionFilters();
    showView('archive');

    // Dashboard normally does not keep the Archive payload loaded. Fetch it before
    // selecting the synthetic attention filter, otherwise the click appears to open
    // an unfiltered list for a moment (the original bug).
    await refresh({includeRecordings: true});
    ensureAttentionFilters();

    const attentionCount = attentionRecordingCount();
    if (attentionCount) {
      $('#recordingStatus').value = 'attention';
      renderRecordings();
      flashFirstAttention();
      toast(`${attentionCount} ${attentionCount === 1 ? 'elemento da controllare' : 'elementi da controllare'} evidenziati`, 'bad');
      return;
    }

    // If the aggregate status was newer than the archive payload, fall back to the
    // most specific filter instead of dumping the user into an unfiltered archive.
    if (Number(statusData?.history?.audio_missing || 0)) $('#recordingStatus').value = 'audio_missing';
    else if (Number(statusData?.queue?.integrity_failed || 0)) $('#recordingStatus').value = 'integrity_failed';
    else if (Number(statusData?.queue?.failed || 0)) $('#recordingStatus').value = 'failed';
    else if (Number(statusData?.queue?.waiting_config || 0)) $('#recordingStatus').value = 'waiting_config';
    else $('#recordingStatus').value = 'all';
    renderRecordings();
    $('#archive')?.scrollIntoView({behavior: 'smooth', block: 'start'});
  };

  // The original overview did not escalate upload failures/configuration blocks when
  // they were the only issue. Keep the Dashboard action consistent with the queue.
  const baseRenderStatusAttention = renderStatus;
  renderStatus = function renderStatusAttention(status) {
    const result = baseRenderStatusAttention(status);
    ensureAttentionFilters();
    const existing = Object.keys(status?.worker?.errors || {}).length
      + Number(status?.queue?.integrity_failed || 0)
      + Number(status?.history?.audio_missing || 0);
    const uploadOnly = Number(status?.queue?.failed || 0) + Number(status?.queue?.waiting_config || 0);
    if (!existing && uploadOnly && status?.disk?.pressure !== 'critical' && !status?.config?.recording_paused) {
      const overview = $('#systemOverview');
      const title = $('#systemOverviewTitle');
      const text = $('#systemOverviewText');
      const action = $('#systemOverviewAction');
      if (overview) overview.dataset.tone = 'warning';
      if (title) title.textContent = `${uploadOnly} ${uploadOnly === 1 ? 'elemento richiede' : 'elementi richiedono'} attenzione`;
      if (text) text.textContent = 'Apri gli elementi interessati: LiveVault li evidenzierà direttamente nell’Archivio.';
      if (action) {
        action.textContent = 'Controlla errori';
        action.dataset.action = 'attention';
        action.disabled = false;
      }
      const health = $('#healthPill');
      if (health) {
        health.className = 'pill warn';
        health.textContent = 'Da controllare';
        health.dataset.action = 'attention';
        health.disabled = false;
      }
    }
    return result;
  };

  ensureAttentionFilters();
  if (activeView === 'archive') {
    renderRecordings();
    decorateAttentionCards();
  }
})();
