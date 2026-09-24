/* LiveVault workspace navigation, command search and archive export. */
(() => {
  'use strict';

  const svg = name => `<svg class="mini-icon" aria-hidden="true"><use href="/static/icons.svg#${name}"></use></svg>`;
  const dialog = $('#commandDialog');
  const input = $('#commandSearch');
  const results = $('#commandResults');
  const commands = [
    {label:'Monitor', note:'Live e registrazioni in corso', icon:'monitor', run:()=>showView('dashboard')},
    {label:'Libreria', note:'Creator, sorgenti e categorie', icon:'users', run:()=>showView('library')},
    {label:'Archivio', note:'Registrazioni locali e cloud', icon:'archive', run:()=>showView('archive')},
    {label:'Analisi', note:'Attività, copertura e dati', icon:'chart', run:()=>showView('statistics')},
    {label:'Impostazioni', note:'Registrazione, storage e cloud', icon:'settings', run:()=>$('#settingsBtn').click()},
    {label:'Aggiungi sorgente', note:'Configura una nuova sorgente', icon:'plus', run:()=>$('#showAddBtn').click()},
  ];
  let matches=[];

  function renderCommands(){
    const query=input.value.trim().toLocaleLowerCase('it');
    const profiles=(libraryProfiles||[]).map(profile=>({label:profile.display_name,note:profile.sources.map(source=>source.provider_label||source.platform).join(' · '),icon:'users',run:()=>openProfile(profile.representative_id)}));
    matches=[...commands,...profiles].filter(item=>`${item.label} ${item.note}`.toLocaleLowerCase('it').includes(query)).slice(0,18);
    results.innerHTML=matches.length?matches.map((item,index)=>`<button type="button" data-command="${index}">${svg(item.icon)}<span><strong>${esc(item.label)}</strong><small>${esc(item.note)}</small></span>${svg('chevron-right')}</button>`).join(''):'<p class="empty compact">Nessun risultato.</p>';
  }
  function openCommands(){
    if(app.classList.contains('hidden'))return;
    input.value='';renderCommands();dialog.showModal();input.focus();
  }
  $('#commandButton').addEventListener('click',openCommands);
  $('#closeCommands').addEventListener('click',()=>dialog.close());
  input.addEventListener('input',renderCommands);
  results.addEventListener('click',event=>{const button=event.target.closest('[data-command]');if(!button)return;const command=matches[Number(button.dataset.command)];dialog.close();command?.run();});
  input.addEventListener('keydown',event=>{if(event.key==='ArrowDown'){event.preventDefault();results.querySelector('button')?.focus();}if(event.key==='Enter'&&matches.length){event.preventDefault();dialog.close();matches[0].run();}});
  results.addEventListener('keydown',event=>{if(!['ArrowDown','ArrowUp'].includes(event.key))return;event.preventDefault();const buttons=[...results.querySelectorAll('button')];const index=buttons.indexOf(document.activeElement);buttons[(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();});
  document.addEventListener('keydown',event=>{
    const editing=event.target.closest('input,textarea,select,[contenteditable="true"]');
    const shortcut=(event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k';
    const slash=event.key==='/'&&!editing&&!$('.modal:not(.hidden)');
    if(shortcut||slash){event.preventDefault();if(!dialog.open)openCommands();}
  });

  $('#dashboardSearch').addEventListener('input',()=>renderSources());
  $('#dashboardStatus').addEventListener('change',()=>renderSources());
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-archive-shortcut]');
    if(!button)return;
    sourceFilterId=0;
    $('#recordingSearch').value='';
    for(const id of ['archivePeriod','archiveCreator','archiveProvider'])if($(`#${id}`))$(`#${id}`).value='all';
    $('#recordingStatus').value=button.dataset.archiveShortcut;
    showView('archive');
  });

  $('#exportArchive').addEventListener('click',()=>{
    const rows=recordings.filter(recording=>recordingMatches(recording)&&archiveIntelMatches(recording));
    if(!rows.length)return toast('Nessun file nella selezione.','bad');
    const cell=value=>{let text=String(value??'');if(/^[=+@\-\t\r]/.test(text))text=`'${text}`;return `"${text.replace(/"/g,'""')}"`;};
    const keys=['id','source_name','filename','started_at','duration_seconds','size_bytes','upload_status','integrity_status','remote_url','nsfw_status','nsfw_moments'];
    // Moments as "h:mm:ss-h:mm:ss label" so they can be looked up in the cloud copy of the same file.
    const hms=s=>{s=Math.max(0,Math.round(Number(s)||0));return `${Math.floor(s/3600)}:${String(Math.floor(s%3600/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;};
    const value=(row,key)=>key==='nsfw_moments'?(row.nsfw_moments||[]).map(m=>`${hms(m.start)}-${hms(m.end)} ${m.label}`).join(' | '):row[key];
    const csv=[keys.join(','),...rows.map(row=>keys.map(key=>cell(value(row,key))).join(','))].join('\r\n');
    const url=URL.createObjectURL(new Blob(['\uFEFF',csv],{type:'text/csv;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download='livevault-archivio.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    toast(`${rows.length} file esportati.`);
  });

  $('#loadOlderRecordings').addEventListener('click',async event=>{
    const button=event.currentTarget;setBusy(button,true,'Caricamento');
    try{
      const rows=await api(`/api/recordings?limit=1000&offset=${recordings.length}`);
      const ids=new Set(recordings.map(row=>row.id));recordings.push(...rows.filter(row=>!ids.has(row.id)));archiveLoadedLimit+=1000;renderRecordings();if(rows.length<1000)button.hidden=true;
    }catch(error){toast(error.message,'bad');}finally{setBusy(button,false);}
  });
})();
