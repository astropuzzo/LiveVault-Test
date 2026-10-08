(() => {
  'use strict';
  const q = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const counts = ['total','active','pending_purchase','processing','revoked','permanent','consumable'];
  const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const fmt = value => count(value) === null ? '—' : value.toLocaleString('it-IT');
  const configLabels = {
    BILLING_ENABLED:'Attivazione della verifica acquisti', PLAY_PACKAGE_NAME:'App su Google Play',
    FIREBASE_PROJECT_ID:'Progetto Firebase', GOOGLE_APPLICATION_CREDENTIALS:'Accesso server a Google Play',
    ACCOUNT_HMAC_KEY:'Protezione degli account', TOKEN_ENCRYPTION_KEY:'Protezione delle ricevute',
    CATALOG:'Catalogo prodotti', RTDN_CONFIGURATION:'Notifiche Google Play',
    GOOGLE_CREDENTIALS_OR_ENCRYPTION:'Accesso Google e protezione delle ricevute', REFUND_RECONCILIATION:'Controllo dei rimborsi',
  };
  let view = 'dashboard', authenticated = true, snapshot = null, selectedId = '';
  let controller = null, generation = 0;

  // Explicit export allowlist: no URLs, account identifiers, purchase tokens or errors.
  function exportReport(data) {
    return {
      generated_at: data?.generated_at ?? null,
      period: {days:30, timezone:'UTC'},
      apps: (data?.apps || []).map(app => ({
        id: app.id, name: app.name, available: app.available === true,
        status: ['ready','preparing'].includes(app.status) ? app.status : 'unavailable',
        source: 'sqlite_purchase_ledger', count_unit: 'receipt_rows',
        sampled_at: app.generated_at ?? null,
        purchases: app.available && app.purchases ? Object.fromEntries(counts.map(key => [key,count(app.purchases[key])])) : null,
        users: {purchasing_accounts: app.available ? count(app.users?.purchasing_accounts) : null, registered:null, active:null},
        daily_utc: app.available ? (app.daily_utc || []).map(row => ({date:row.date,purchases:count(row.purchases),revocations:count(row.revocations)})) : [],
        revenue:{available:false,reason:'google_financial_reports_not_configured'},
        ads:{available:false,reason:'admob_not_configured'},
      })),
    };
  }

  function setText(selector, value) { const node=q(selector); if(node)node.textContent=value; }
  function selectedApp() { return snapshot?.apps?.find(app => app.id===selectedId) || snapshot?.apps?.[0]; }
  function clearPrivate() {
    snapshot=null; selectedId='';
    for(const selector of ['#appsTotal','#appsActive','#appsRevoked','#appsAccounts','#appsPending','#appsProcessing','#appsPermanent','#appsConsumable'])setText(selector,'—');
    setText('#appsSample','In attesa del servizio');
    setText('#appsStatus','Da verificare');
    if(q('#appsStatus'))q('#appsStatus').className='status-chip';
    setText('#appsChartSummary','I dati saranno disponibili dopo l’accesso al servizio.');
    setText('#appsCatalog','In attesa del catalogo');
    if(q('#appsChart'))q('#appsChart').innerHTML='';
    if(q('#appsDailyRows'))q('#appsDailyRows').innerHTML='';
    if(q('#appsSetup'))q('#appsSetup').hidden=true;
    if(q('#appsExport'))q('#appsExport').disabled=true;
    if(q('#appsSelector'))q('#appsSelector').innerHTML='<option value="">Seleziona app</option>';
  }

  function chart(rows) {
    if(!rows.length){
      setText('#appsChartSummary','Attività non disponibile: il servizio non risponde.');
      q('#appsChart').innerHTML='<div class="apps-chart-empty">Aggiorna per riprovare.</div>';
      q('#appsDailyRows').innerHTML=''; return;
    }
    const purchases=rows.reduce((sum,row)=>sum+row.purchases,0), revocations=rows.reduce((sum,row)=>sum+row.revocations,0);
    setText('#appsChartSummary',`${fmt(purchases)} ricevute create · ${fmt(revocations)} revoche negli ultimi 30 giorni`);
    const width=720,height=200,left=32,bottom=166,top=16,span=(width-left-12)/rows.length;
    const max=Math.max(2,Math.ceil(Math.max(...rows.flatMap(row=>[row.purchases,row.revocations]))/2)*2);
    const points=rows.map((row,index)=>{
      const x=left+index*span+3, bw=Math.max(2,span*.32);
      const p=(bottom-top)*row.purchases/max, r=(bottom-top)*row.revocations/max;
      return `<g><title>${esc(row.date)}: ${row.purchases} ricevute create, ${row.revocations} revoche</title><rect x="${x}" y="${bottom-p}" width="${bw}" height="${p}" rx="2" class="apps-bar-purchases"/><rect x="${x+bw+2}" y="${bottom-r}" width="${bw}" height="${r}" rx="2" class="apps-bar-revocations"/></g>`;
    }).join('');
    const ticks=[0,1,2].map(i=>{
      const y=bottom-(bottom-top)*i/2;
      return `<line x1="${left}" x2="${width-8}" y1="${y}" y2="${y}"/><text x="${left-9}" y="${y+4}" text-anchor="end">${Math.round(max*i/2)}</text>`;
    }).join('');
    const first=rows[0].date.slice(5).split('-').reverse().join('/'),last=rows.at(-1).date.slice(5).split('-').reverse().join('/');
    q('#appsChart').innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="appsChartSvgTitle appsChartSvgDesc"><title id="appsChartSvgTitle">Attività degli ultimi 30 giorni</title><desc id="appsChartSvgDesc">${purchases} ricevute create e ${revocations} revoche. Tutti i valori giornalieri sono nella tabella seguente.</desc><g class="apps-chart-grid">${ticks}</g>${points}<g class="apps-chart-dates"><text x="${left}" y="194">${first}</text><text x="${width-10}" y="194" text-anchor="end">${last}</text></g></svg>`;
    q('#appsDailyRows').innerHTML=rows.map(row=>`<tr><th scope="row">${esc(row.date)}</th><td>${fmt(row.purchases)}</td><td>${fmt(row.revocations)}</td></tr>`).join('');
  }

  function render() {
    const app=selectedApp(); if(!app)return;
    setText('#appsName',app.name);
    const available=app.available===true;
    setText('#appsStatus',available ? (app.status==='ready'?'Verifica acquisti pronta':'In preparazione') : 'Servizio non disponibile');
    q('#appsStatus').className=`status-chip ${available?(app.status==='ready'?'good':'warn'):'bad'}`;
    setText('#appsServiceNote',available ? (app.billing_enabled?'Verifica server delle ricevute abilitata.':'Verifica server delle ricevute ancora da attivare.') : 'Il riepilogo non è raggiungibile. I contatori restano non disponibili.');
    const p=available?app.purchases:null;
    for(const [selector,key] of [['#appsTotal','total'],['#appsActive','active'],['#appsRevoked','revoked'],['#appsPending','pending_purchase'],['#appsProcessing','processing'],['#appsPermanent','permanent'],['#appsConsumable','consumable']])setText(selector,fmt(p?.[key]));
    setText('#appsAccounts',fmt(available?app.users?.purchasing_accounts:null));
    const sampled=app.generated_at?new Date(app.generated_at):null;
    setText('#appsSample',sampled&&!Number.isNaN(sampled.getTime())?`Registro acquisti · aggiornato ${sampled.toLocaleString('it-IT',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})} · UTC nei dati giornalieri`:'Registro acquisti · dati non disponibili');
    setText('#appsVersion',available&&app.version?`Servizio ${app.version}`:'Servizio da verificare');
    const missing=(app.missing_configuration||[]).filter(key=>configLabels[key]);
    q('#appsSetup').hidden=!available||!missing.length;
    q('#appsSetupList').innerHTML=missing.map(key=>`<li>${esc(configLabels[key])}</li>`).join('');
    const catalog=app.catalog||[];
    q('#appsCatalog').innerHTML=!available?'Catalogo non disponibile':catalog.length?catalog.map(item=>`<span class="apps-product"><code>${esc(item.id)}</code><small>${item.kind==='permanent'?'Permanente':'Consumabile'}</small></span>`).join(''):'Nessun prodotto configurato.';
    chart(available?app.daily_utc||[]:[]);
    const links=app.links||{};
    // Links come from the server registry and are never included in exports.
    const local=['192.168.1.27','127.0.0.1','localhost'].includes(location.hostname);
    if(links.coolify_public)q('#appsCoolify').href=local?links.coolify_local:links.coolify_public;
    if(links.google_play)q('#appsPlay').href=links.google_play;
    q('#appsExport').disabled=!snapshot?.apps?.some(item=>item.available);
  }

  function stopRequest() { generation++; controller?.abort(); controller=null; if(q('#appsRefresh'))q('#appsRefresh').disabled=false; if(q('#appsWorkspace'))q('#appsWorkspace').setAttribute('aria-busy','false'); }
  function canLoad() { return authenticated&&view==='apps'&&!document.hidden; }
  async function refresh() {
    if(!canLoad()||controller)return;
    const requestGeneration=++generation;
    controller=new AbortController(); const activeController=controller;
    let timedOut=false;
    const timeout=setTimeout(()=>{timedOut=true;activeController.abort();},8000);
    q('#appsRefresh').disabled=true;
    q('#appsWorkspace').setAttribute('aria-busy','true');
    setText('#appsLoadState','Aggiornamento del riepilogo…');
    try {
      const response=await fetch('/api/apps/summary',{cache:'no-store',signal:activeController.signal});
      if(response.status===401){ if(typeof showLogin==='function')showLogin(); else setAuthenticated(false); return; }
      const data=await response.json();
      if(requestGeneration!==generation||!canLoad())return;
      if(!Array.isArray(data.apps)||!data.apps.length)throw new Error('Unavailable');
      snapshot=data;
      if(!data.apps.some(app=>app.id===selectedId))selectedId=data.apps[0].id;
      q('#appsSelector').innerHTML=data.apps.map(app=>`<option value="${esc(app.id)}">${esc(app.name)}</option>`).join('');
      q('#appsSelector').value=selectedId;
      render();
      setText('#appsLoadState',response.ok?'Dati aggiornati dal servizio.':'Servizio temporaneamente non disponibile. Riprova con Aggiorna.');
    } catch(error) {
      if(requestGeneration!==generation||(error.name==='AbortError'&&!timedOut))return;
      clearPrivate();
      setText('#appsStatus','Servizio non disponibile');
      q('#appsStatus').className='status-chip bad';
      setText('#appsServiceNote','I dati non sono disponibili. Aggiorna per riprovare.');
      setText('#appsLoadState','Connessione al riepilogo non disponibile. Riprova con Aggiorna.');
      setText('#appsChartSummary','Attività non disponibile.');
    } finally {
      clearTimeout(timeout);
      if(requestGeneration===generation){controller=null;q('#appsRefresh').disabled=false;q('#appsWorkspace').setAttribute('aria-busy','false');}
    }
  }
  function setView(nextView) { view=nextView; if(!canLoad())stopRequest(); else refresh(); }
  function setAuthenticated(value) { authenticated=Boolean(value); if(!authenticated){stopRequest();clearPrivate();setText('#appsLoadState','Effettua l’accesso per consultare le app.');}else if(canLoad())refresh(); }
  globalThis.OpenAstroApps={setView,setAuthenticated,refresh,exportReport};
  if(typeof document==='undefined'||!q('#appsWorkspace'))return;
  q('#appsRefresh').addEventListener('click',refresh);
  q('#appsSelector').addEventListener('change',event=>{selectedId=event.target.value;render();});
  q('#appsExport').addEventListener('click',()=>{
    if(!authenticated||!snapshot||q('#appsExport').disabled)return;
    const report=exportReport(snapshot);
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download=`openastro-apps-${new Date().toISOString().slice(0,10)}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopRequest();else if(canLoad())refresh();});
  // No background app polling: refresh on entry, restored visibility or explicit action.
})();
