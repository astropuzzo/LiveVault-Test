(() => {
  let statusBusy = false;
  let searchBusy = false;
  let lastSearch = [];
  let lastQuery = '';
  let lastGoodStatus = null;

  const q = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  const fmtBytes = value => {
    value = Number(value);
    if (!Number.isFinite(value) || value < 0) return '—';
    const units=['B','KB','MB','GB','TB']; let n=value,u=0;
    while(n>=1024&&u<units.length-1){n/=1024;u++;}
    return `${n>=100?n.toFixed(0):n.toFixed(1)} ${units[u]}`;
  };
  const fmtRate = value => `${fmtBytes(value)}/s`;
  const fmtEta = value => {
    if (value == null || !Number.isFinite(Number(value))) return '—';
    let s=Math.max(0,Number(value)); const d=Math.floor(s/86400); s%=86400; const h=Math.floor(s/3600); const m=Math.floor((s%3600)/60);
    return d?`${d}g ${h}h`:h?`${h}h ${m}m`:`${m} min`;
  };

  async function api(url, options={}) {
    const response = await fetch(url,{cache:'no-store',...options});
    if(response.status===401){ if(typeof showLogin==='function')showLogin(); throw new Error('Sessione scaduta.'); }
    let data={}; try{data=await response.json();}catch(_){}
    if(!response.ok||data.ok===false)throw new Error(data.error||`HTTP ${response.status}`);
    return data;
  }

  function renderStatus(data){
    const chip=q('#torrentClientChip');
    if(chip){chip.textContent=data.stale?'Transmission riconnessione…':(data.available?'Transmission online':'Transmission offline');chip.className=`torrent-chip ${data.stale?'':(data.available?'good':'bad')}`;}
    q('#torrentProviderChip').textContent=(data.provider||'1337x').toUpperCase();
    const s=data.stats||{};
    q('#torrentRateDown').textContent=fmtRate(s.rate_down||0);
    q('#torrentRateUp').textContent=fmtRate(s.rate_up||0);
    q('#torrentActive').textContent=String(s.active||0);
    q('#torrentCount').textContent=String(s.count||0);
    q('#torrentRateDownSub').textContent=s.active?'download live':'nessun download';
    q('#torrentRateUpSub').textContent=(s.rate_up||0)>0?'upload peer':'upload inattivo';
    q('#torrentActiveSub').textContent=`${s.paused||0} in pausa`;
    q('#torrentCountSub').textContent='job nel client';
    q('#torrentStatusError').hidden=!data.error;
    q('#torrentStatusError').textContent=data.error||'';

    const jobs=data.torrents||[];
    q('#torrentQueueCount').textContent=`${jobs.length} ${jobs.length===1?'torrent':'torrent'}`;
    q('#torrentQueue').innerHTML=jobs.length?jobs.map(job=>{
      const progress=Number(job.size)>0?Number(job.percent||0):Number(job.metadata_percent||0);
      const paused=Number(job.status)===0;
      const state=job.error?`Errore · ${job.error_text||'torrent'}`:(Number(job.size)===0&&job.metadata_percent<100?`Metadata ${job.metadata_percent.toFixed(0)}%`:job.status_label);
      return `<article class="torrent-job">
        <div class="torrent-job-top"><div><strong title="${esc(job.name)}">${esc(job.name||'Recupero metadata…')}</strong><small>${fmtBytes(job.downloaded)} / ${fmtBytes(job.size)} · ${Number(job.percent||0).toFixed(1)}%</small></div><span class="torrent-job-state">${esc(state)}</span></div>
        <div class="torrent-progress" style="--progress:${Math.max(0,Math.min(100,progress))}%"><i></i></div>
        <div class="torrent-job-meta"><div class="torrent-job-metrics"><span>↓ <b>${fmtRate(job.rate_down)}</b></span><span>↑ <b>${fmtRate(job.rate_up)}</b></span><span>ETA <b>${fmtEta(job.eta)}</b></span><span>Peer <b>${job.peers}</b></span><span>Ratio <b>${Number(job.ratio||0).toFixed(2)}</b></span></div><div class="torrent-job-actions"><button data-torrent-action="${paused?'resume':'pause'}" data-torrent-id="${job.id}">${paused?'Riprendi':'Pausa'}</button><button class="danger" data-torrent-action="remove" data-torrent-id="${job.id}">Annulla</button></div></div>
      </article>`;
    }).join(''):'<div class="torrent-empty">Nessun torrent in coda.</div>';
    q('#torrentQueue').querySelectorAll('[data-torrent-action]').forEach(button=>button.addEventListener('click',()=>torrentAction(button)));

    const imports=data.recent_imports||[];
    q('#torrentImportsWrap').hidden=!imports.length;
    q('#torrentImports').innerHTML=imports.map(item=>`<div class="torrent-import-row"><div><strong title="${esc(item.name)}">${esc(item.name)}</strong><small>${esc(item.path||'Downloads')} · ${fmtBytes(item.size||0)}</small></div><span>IMPORTATO</span></div>`).join('');
  }

  async function refreshStatus(force=false){
    if(statusBusy||document.hidden||document.body.dataset.view!=='media')return;
    statusBusy=true;
    try{
      const data=await api('/api/torrents/status');
      if(data.available){
        lastGoodStatus=data;
        renderStatus(data);
      }else if(lastGoodStatus){
        renderStatus({...lastGoodStatus,available:false,stale:true,error:data.error||'Transmission temporaneamente non disponibile · visualizzato ultimo stato noto.'});
      }else{
        renderStatus(data);
      }
    }catch(error){
      if(lastGoodStatus){
        renderStatus({...lastGoodStatus,available:false,stale:true,error:`${error.message} · visualizzato ultimo stato noto.`});
      }else{
        renderStatus({available:false,provider:'1337x',stats:{},torrents:[],recent_imports:[],error:error.message});
      }
    }finally{statusBusy=false;}
  }

  function searchSizeKey(value){
    const m=String(value||'').trim().match(/([0-9]+(?:[.,][0-9]+)?)\s*(B|KB|MB|GB|TB)/i);
    if(!m)return 0;
    const units={B:1,KB:1024,MB:1024**2,GB:1024**3,TB:1024**4};
    return Number(m[1].replace(',','.'))*(units[m[2].toUpperCase()]||1);
  }
  function searchTimeKey(value){
    const raw=String(value||'').trim().toLowerCase();
    const rel=raw.match(/^([0-9]+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks|mo|month|months|y|yr|yrs|year|years)(?:\s+ago)?$/);
    if(rel){
      const unit=rel[2];
      const mult=unit.startsWith('s')?1:unit==='m'||unit.startsWith('min')?60:unit==='h'||unit.startsWith('hr')||unit.startsWith('hour')?3600:unit==='d'||unit.startsWith('day')?86400:unit==='w'||unit.startsWith('week')?604800:unit==='mo'||unit.startsWith('month')?2592000:31536000;
      return Date.now()-Number(rel[1])*mult*1000;
    }
    const clock=raw.match(/^(today|y-day|yesterday)[, ]+([0-2]?\d):([0-5]\d)$/);
    if(clock){
      const date=new Date();
      if(clock[1]!=='today')date.setDate(date.getDate()-1);
      date.setHours(Number(clock[2]),Number(clock[3]),0,0);
      return date.getTime();
    }
    const cleaned=raw.replace(/(\d+)(st|nd|rd|th)/g,'$1').replace(/'/g,'20');
    const parsed=Date.parse(cleaned);
    return Number.isFinite(parsed)?parsed:0;
  }
  function orderedSearch(){
    const sort=q('#torrentSearchSort')?.value||'seeders';
    const order=q('#torrentSearchOrder')?.value==='asc'?1:-1;
    const key=item=>sort==='time'?searchTimeKey(item.age):sort==='size'?searchSizeKey(item.size):sort==='leechers'?Number(item.leechers||0):Number(item.seeders||0);
    return lastSearch.map((item,index)=>({item,index})).sort((a,b)=>{
      const delta=key(a.item)-key(b.item);
      return delta?delta*order:String(a.item.name||'').localeCompare(String(b.item.name||''),'it');
    });
  }
  function renderSearch(results,remember=true){
    if(remember)lastSearch=results||[];
    const rows=orderedSearch();
    const host=q('#torrentResults');
    q('#torrentResultsWrap').hidden=false;
    q('#torrentResultsCount').textContent=`${lastSearch.length} risultati`;
    host.innerHTML=rows.length?rows.map(({item,index})=>`<article class="torrent-result">
      <div class="torrent-result-main"><div class="torrent-result-name"><strong title="${esc(item.name)}">${esc(item.name)}</strong><small>${esc(item.age||'')} ${item.uploader?'· '+esc(item.uploader):''}</small></div><button class="primary" data-torrent-result="${index}">Scarica</button></div>
      <div class="torrent-result-metrics"><span class="seed">Seed <b>${Number(item.seeders||0)}</b></span><span class="leech">Leech <b>${Number(item.leechers||0)}</b></span><span>Size <b>${esc(item.size||'—')}</b></span><span>Fonte <b>1337x</b></span></div>
    </article>`).join(''):'<div class="torrent-empty">Nessun risultato trovato.</div>';
    host.querySelectorAll('[data-torrent-result]').forEach(button=>button.addEventListener('click',()=>addSearchResult(Number(button.dataset.torrentResult),button)));
  }

  async function runSearch(){
    const input=q('#torrentSearchInput'); const query=input.value.trim();
    if(query.length<2)return typeof toast==='function'&&toast('Inserisci almeno 2 caratteri.',true);
    if(searchBusy)return;
    searchBusy=true; lastQuery=query;
    q('#torrentSearchButton').disabled=true; q('#torrentSearchButton').textContent='Ricerca…';
    q('#torrentResultsWrap').hidden=false; q('#torrentResults').innerHTML='<div class="torrent-empty">Ricerca su 1337x…</div>';
    const challengeHint=setTimeout(()=>{
      if(searchBusy)q('#torrentResults').innerHTML='<div class="torrent-empty">Superamento protezione 1337x… La prima ricerca può richiedere 30–45 secondi; le successive saranno più rapide.</div>';
    },4000);
    try{
      const params=new URLSearchParams({provider:q('#torrentProvider').value||'1337x',q:query,page:'1',sort:q('#torrentSearchSort').value||'seeders',order:q('#torrentSearchOrder').value||'desc'});
      const data=await api('/api/torrents/search?'+params);
      renderSearch(data.results||[]);
    }catch(error){
      q('#torrentResults').innerHTML=`<div class="torrent-error">${esc(error.message)}</div>`;
      q('#torrentResultsCount').textContent='errore provider';
    }finally{
      clearTimeout(challengeHint);
      searchBusy=false;q('#torrentSearchButton').disabled=false;q('#torrentSearchButton').textContent='Cerca';
    }
  }

  async function addSearchResult(index,button){
    const item=lastSearch[index]; if(!item)return;
    button.disabled=true;button.textContent='Aggiungo…';
    try{
      await api('/api/torrents/add',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({detail_url:item.detail_url})});
      if(typeof toast==='function')toast(`${item.name} aggiunto ai download.`);
      await refreshStatus(true);
    }catch(error){if(typeof toast==='function')toast(error.message,true);}
    finally{button.disabled=false;button.textContent='Scarica';}
  }

  async function addMagnet(){
    const input=q('#torrentMagnet'); const magnet=input.value.trim(); if(!magnet)return;
    const button=q('#torrentMagnetAdd');button.disabled=true;
    try{
      await api('/api/torrents/add',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({magnet})});
      input.value=''; if(typeof toast==='function')toast('Magnet aggiunto.');
      await refreshStatus(true);
    }catch(error){if(typeof toast==='function')toast(error.message,true);}
    finally{button.disabled=false;}
  }

  async function addTorrentFile(file){
    if(!file)return;
    if(file.size>5*1024*1024)return typeof toast==='function'&&toast('File .torrent troppo grande.',true);
    try{
      await api('/api/torrents/add-file',{method:'POST',headers:{'Content-Type':'application/x-bittorrent','X-CSRF-Token':csrf},body:file});
      if(typeof toast==='function')toast(`${file.name} aggiunto.`);
      await refreshStatus(true);
    }catch(error){if(typeof toast==='function')toast(error.message,true);}
  }

  async function torrentAction(button){
    const id=Number(button.dataset.torrentId),action=button.dataset.torrentAction;
    if(!id)return;
    if(action==='remove'&&!window.confirm('Annullare il torrent e cancellare i dati parziali dalla staging?'))return;
    button.disabled=true;
    try{
      await api('/api/torrents/action',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({id,action})});
      await refreshStatus(true);
    }catch(error){if(typeof toast==='function')toast(error.message,true);}
    finally{button.disabled=false;}
  }

  const searchButton=q('#torrentSearchButton');
  if(!searchButton)return;
  searchButton.addEventListener('click',runSearch);
  q('#torrentSearchInput').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();runSearch();}});
  q('#torrentSearchSort').addEventListener('change',()=>{if(lastSearch.length)renderSearch(lastSearch,false);});
  q('#torrentSearchOrder').addEventListener('change',()=>{if(lastSearch.length)renderSearch(lastSearch,false);});
  q('#torrentMagnetAdd').addEventListener('click',addMagnet);
  q('#torrentMagnet').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();addMagnet();}});
  q('#torrentFile').addEventListener('change',event=>{const file=event.target.files?.[0];event.target.value='';addTorrentFile(file);});
  q('#torrentRefresh').addEventListener('click',()=>refreshStatus(true));
  refreshStatus(true);
  setInterval(refreshStatus,2000);
})();