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

    if (!statusHints && workerErrors && !$('#errorsPanel').classList.contains('hidden')) {
      $('#errorsPanel').scrollIntoView({behavior: 'smooth', block: 'start'});
      $('#errorsPanel').classList.add('lv-attention-focus');
      setTimeout(() => $('#errorsPanel')?.classList.remove('lv-attention-focus'), 3600);
      return;
    }

    clearArchiveNarrowing();
    ensureAttentionFilters();
    recordings = await api('/api/recordings?limit=2000');
    recordingsLoaded = true;
    lastRecordingLoad = Date.now();
    ensureAttentionFilters();

    const attentionCount = attentionRecordingCount();
    if (attentionCount) {
      $('#recordingStatus').value = 'attention';
      showView('archive');
      renderRecordings();
      flashFirstAttention();
      toast(`${attentionCount} ${attentionCount === 1 ? 'elemento da controllare' : 'elementi da controllare'} evidenziati`, 'bad');
      return;
    }

    if (Number(statusData?.history?.audio_missing || 0)) $('#recordingStatus').value = 'audio_missing';
    else if (Number(statusData?.queue?.integrity_failed || 0)) $('#recordingStatus').value = 'integrity_failed';
    else if (Number(statusData?.queue?.failed || 0)) $('#recordingStatus').value = 'failed';
    else if (Number(statusData?.queue?.waiting_config || 0)) $('#recordingStatus').value = 'waiting_config';
    else $('#recordingStatus').value = 'all';
    showView('archive');
    renderRecordings();
    $('#archive')?.scrollIntoView({behavior: 'smooth', block: 'start'});
  };

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

/* LiveVault processing/finalization UX */
(() => {
  function ensureSessionGapField() {
    if ($('#setSessionGap')) return;
    const segment = $('#setSegment')?.closest('label');
    if (!segment) return;
    const field = document.createElement('label');
    field.className = 'field';
    field.innerHTML = '<span>Finestra ricongiungimento (min)</span><input id="setSessionGap" type="number" min="1" max="120" step="1"><small>Attende questo intervallo dopo l’ultimo frammento prima di chiudere la sessione. “Finalizza + upload ora” la salta manualmente.</small>';
    segment.insertAdjacentElement('afterend', field);
  }

  ensureSessionGapField();

  const baseLoadSettingsProcessing = loadSettings;
  loadSettings = async function loadSettingsProcessing() {
    const result = await baseLoadSettingsProcessing();
    ensureSessionGapField();
    if ($('#setSessionGap')) $('#setSessionGap').value = Number(settingsData?.session_stitch_gap_minutes || 20);
    return result;
  };

  $('#settingsForm')?.addEventListener('submit', async () => {
    const value = Math.max(1, Math.min(120, Number($('#setSessionGap')?.value || 20)));
    try {
      await api('/api/session-processing/settings', {
        method: 'PATCH',
        body: JSON.stringify({session_stitch_gap_minutes: value})
      });
      if (settingsData) settingsData.session_stitch_gap_minutes = value;
    } catch (error) {
      const target = $('#settingsError');
      if (target) target.textContent = `Finestra ricongiungimento: ${error.message}`;
      toast(`Finestra ricongiungimento: ${error.message}`, 'bad');
    }
  });

  function ensureProcessingCard() {
    let card = $('#processingNowCard');
    if (card) return card;
    const uploadCard = $('#uploadNowCard');
    if (!uploadCard) return null;
    card = document.createElement('section');
    card.id = 'processingNowCard';
    card.className = 'upload-card panel hidden lv-processing-card';
    card.setAttribute('aria-live', 'polite');
    card.innerHTML = '<div><span class="lv-processing-dot"></span><strong id="processingNowTitle">Elaborazione in corso</strong><small id="processingNowMeta">—</small></div><div class="upload-progress"><div class="meter big"><i id="processingProgressBar"></i></div><span id="processingProgressText">0%</span></div>';
    uploadCard.insertAdjacentElement('afterend', card);
    return card;
  }

  function renderProcessingProgress(status) {
    const card = ensureProcessingCard();
    if (!card) return;
    const current = status?.worker?.processing_current;
    card.classList.toggle('hidden', !current);
    if (!current) return;
    const percent = Math.max(0, Math.min(100, Number(current.percent) || 0));
    $('#processingNowTitle').textContent = `${current.source_name || 'Sessione'} · ${current.stage || 'Elaborazione'}`;
    $('#processingNowMeta').textContent = `${current.parts || 0} ${Number(current.parts) === 1 ? 'parte' : 'parti'} · ${humanBytes(current.processed_bytes || 0)} / ${humanBytes(current.total_bytes || 0)}`;
    $('#processingProgressBar').style.width = `${percent}%`;
    $('#processingProgressText').textContent = `${percent.toFixed(percent % 1 ? 1 : 0)}%`;
    card.dataset.stage = String(current.stage || '').toLowerCase();
  }

  const baseRenderStatusProcessing = renderStatus;
  renderStatus = function renderStatusProcessing(status) {
    const result = baseRenderStatusProcessing(status);
    renderProcessingProgress(status);
    return result;
  };

  function processingForSource(sourceId) {
    const current = statusData?.worker?.processing_current;
    return current && Number(current.source_id) === Number(sourceId) ? current : null;
  }

  function processingCandidateForProfile(profile) {
    const session = profile?.pulse_session;
    if (!session) return false;
    return Number(session.processing_count || 0) > 0
      || (Number(session.file_count || 0) > Number(session.uploaded_count || 0));
  }

  function decorateProcessButtons() {
    const root = $('#sources');
    if (!root) return;
    const profiles = typeof controlRoomProfileRows === 'function' ? controlRoomProfileRows() : [];
    for (const card of root.querySelectorAll('.cr-live-card')) {
      if (card.querySelector('[data-process-now]')) continue;
      const idNode = card.querySelector('[data-id]');
      const sourceId = Number(idNode?.dataset.id || 0);
      if (!sourceId) continue;
      const profile = profiles.find(row => row.rows?.some(source => Number(source.id) === sourceId));
      if (!profile || !processingCandidateForProfile(profile)) continue;
      const actions = card.querySelector('.cr-card-actions');
      if (!actions) continue;
      const button = document.createElement('button');
      button.className = 'btn accent lv-process-now';
      button.type = 'button';
      button.dataset.processNow = String(profile.source?.id || sourceId);
      button.textContent = processingForSource(profile.source?.id || sourceId) ? 'Elaborazione…' : 'Finalizza + upload ora';
      actions.append(button);
    }

    for (const card of root.querySelectorAll('.cr-ended-card')) {
      if (card.querySelector('[data-process-now]')) continue;
      const creator = card.querySelector('[data-profile-link]');
      const sourceId = Number(creator?.dataset.profileLink || 0);
      if (!sourceId) continue;
      const profile = profiles.find(row => row.rows?.some(source => Number(source.id) === sourceId));
      const session = profile?.pulse_session;
      if (!session || !processingCandidateForProfile(profile)) continue;
      const state = card.querySelector('.cr-ended-state');
      if (!state) continue;
      const button = document.createElement('button');
      button.className = 'btn quiet lv-process-now';
      button.type = 'button';
      button.dataset.processNow = String(profile.source?.id || sourceId);
      button.textContent = 'Finalizza ora';
      state.append(button);
    }
  }

  const baseRenderSourcesProcessing = renderSources;
  renderSources = function renderSourcesProcessing() {
    const result = baseRenderSourcesProcessing();
    decorateProcessButtons();
    return result;
  };

  if (typeof controlRoomEndedCard === 'function') {
    const baseEndedCardProcessing = controlRoomEndedCard;
    controlRoomEndedCard = function controlRoomEndedCardProcessing(session) {
      let html = baseEndedCardProcessing(session);
      if (session?.state === 'processing') {
        const sourceId = Number(session.representative_source_id || 0);
        const current = processingForSource(sourceId);
        const label = current ? `ELABORAZIONE ${Math.round(Number(current.percent) || 0)}%` : 'IN ATTESA DI FINALIZZAZIONE';
        html = html.replace('IN RECUPERO', label);
      }
      return html;
    };
  }

  if (typeof controlRoomPulseMarkup === 'function') {
    const basePulseMarkupProcessing = controlRoomPulseMarkup;
    controlRoomPulseMarkup = function controlRoomPulseMarkupProcessing() {
      return String(basePulseMarkupProcessing()).replaceAll('RECUPERO', 'ELABORAZIONE');
    };
  }

  const baseRenderProfileProcessing = renderProfile;
  renderProfile = function renderProfileProcessing() {
    const result = baseRenderProfileProcessing();
    for (const state of $$('#profileContent .local-capture > span')) {
      if (state.textContent === 'PRONTA · CONSOLIDAMENTO') state.textContent = 'PRONTA · IN ATTESA';
      else if (state.textContent === 'RECUPERO DISPONIBILE') state.textContent = 'RIPRISTINO DISPONIBILE';
    }
    return result;
  };

  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-process-now]');
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    const sourceId = Number(button.dataset.processNow || 0);
    if (!sourceId) return;
    setBusy(button, true, 'Avvio…');
    try {
      const result = await api(`/api/sources/${sourceId}/process-now`, {method: 'POST'});
      const bits = [];
      if (result.finalized) bits.push(`${result.finalized} parti finalizzate`);
      if (result.uploads_prioritized) bits.push(`${result.uploads_prioritized} upload prioritizzati`);
      if (result.upload_paused) bits.push('upload globale in pausa');
      toast(bits.length ? bits.join(' · ') : 'Elaborazione richiesta');
      await refresh({includeRecordings: false});
    } catch (error) {
      toast(error.message, 'bad');
    } finally {
      setBusy(button, false);
    }
  }, true);

  renderProcessingProgress(statusData || {});
})();
