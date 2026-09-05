/* Navigation and discovery shared by the LiveVault workspace. */
(() => {
  const dialog = $('#commandDialog');
  const input = $('#commandSearch');
  const results = $('#commandResults');
  const commands = [
    {label:'Panoramica', note:'Live e registrazioni', run:() => showView('dashboard')},
    {label:'Libreria', note:'Profili, categorie e raccolte', run:() => showView('library')},
    {label:'Archivio', note:'Video locali e copie cloud', run:() => showView('archive')},
    {label:'Statistiche', note:'Attività e copertura', run:() => showView('statistics')},
    {label:'Impostazioni', note:'Registrazione e cloud', run:() => $('#settingsBtn').click()},
    {label:'Aggiungi sorgente', note:'Collega un nuovo account', run:() => $('#showAddBtn').click()}
  ];
  let matches = [];
  function renderCommands() {
    const query = input.value.trim().toLocaleLowerCase('it');
    matches = [...commands, ...libraryProfiles.map(profile => ({
      label:profile.display_name, note:profile.sources.map(source => source.platform).join(' · '),
      run:() => openProfile(profile.representative_id)
    }))].filter(item => `${item.label} ${item.note}`.toLocaleLowerCase('it').includes(query)).slice(0,16);
    results.innerHTML = matches.length ? matches.map((item,index) => `<button type="button" data-command="${index}"><span><strong>${esc(item.label)}</strong><small>${esc(item.note)}</small></span><span aria-hidden="true">↗</span></button>`).join('') : '<p class="empty">Nessun risultato. Prova un altro nome.</p>';
  }
  function openCommands() { if (app.classList.contains('hidden')) return; input.value = ''; renderCommands(); dialog.showModal(); input.focus(); }
  $('#commandButton').addEventListener('click', openCommands);
  $('#closeCommands').addEventListener('click', () => dialog.close());
  input.addEventListener('input', renderCommands);
  results.addEventListener('click', event => {
    const button = event.target.closest('[data-command]');
    if (!button) return;
    const command = matches[Number(button.dataset.command)];
    dialog.close(); command?.run();
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown') { event.preventDefault(); results.querySelector('button')?.focus(); }
    if (event.key === 'Enter' && matches.length) { event.preventDefault(); dialog.close(); matches[0].run(); }
  });
  results.addEventListener('keydown', event => {
    if (!['ArrowDown','ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    const buttons = [...results.querySelectorAll('button')];
    const index = buttons.indexOf(document.activeElement);
    buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus();
  });
  document.addEventListener('keydown', event => {
    const editing = event.target.closest('input,textarea,select,[contenteditable="true"]');
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' || event.key === '/' && !editing && !$('.modal:not(.hidden)')) {
      event.preventDefault(); if (!dialog.open) openCommands();
    }
  });
  $('#dashboardSearch').addEventListener('input', () => renderSources());
  $('#dashboardStatus').addEventListener('change', () => renderSources());
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-archive-shortcut]');
    if (!button) return;
    sourceFilterId = 0;
    $('#recordingSearch').value = '';
    for (const id of ['archivePeriod','archiveCreator','archiveProvider']) if ($(`#${id}`)) $(`#${id}`).value = 'all';
    $('#recordingStatus').value = button.dataset.archiveShortcut;
    showView('archive');
  });
  $('#exportArchive').addEventListener('click', () => {
    const rows = recordings.filter(recording => recordingMatches(recording) && archiveIntelMatches(recording));
    if (!rows.length) return toast('Nessun file nella selezione.', 'bad');
    const cell = value => {
      let text = String(value ?? '');
      if (/^[=+@\-\t\r]/.test(text)) text = `'${text}`;
      return `"${text.replace(/"/g, '""')}"`;
    };
    const keys = ['id','source_name','filename','started_at','duration_seconds','size_bytes','upload_status','integrity_status','remote_url'];
    const csv = [keys.join(','), ...rows.map(row => keys.map(key => cell(row[key])).join(','))].join('\r\n');
    const url = URL.createObjectURL(new Blob(['\uFEFF',csv], {type:'text/csv;charset=utf-8'}));
    const link = document.createElement('a'); link.href = url; link.download = 'livevault-archivio.csv'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`${rows.length} file esportati dalla selezione caricata.`);
  });
  $('#loadOlderRecordings').addEventListener('click', async event => {
    const button = event.currentTarget;
    setBusy(button, true, 'Caricamento…');
    try {
      const rows = await api(`/api/recordings?limit=1000&offset=${recordings.length}`);
      const ids = new Set(recordings.map(row => row.id));
      recordings.push(...rows.filter(row => !ids.has(row.id)));
      archiveLoadedLimit += 1000;
      renderRecordings();
      if (rows.length < 1000) button.hidden = true;
    } catch (error) { toast(error.message, 'bad'); }
    finally { setBusy(button, false); }
  });
})();
