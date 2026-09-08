const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let csrf = '';
let pendingAction = null;
let pendingPayload = {};
let holdTimer = null;
let holdStart = 0;
let historyRange = 3600;
let powerDirty = false;
let activePowerKey = 'balanced';
let activeWifiOn = true;
let refreshBusy = false;
let historyBusy = false;
let signedIn = true;
let actionBusy = false;
let piholeBusy = false;
let selectedDnsDeviceId = '';
let selectedDnsConnection = null;
let latestState = null;
let latestHistory = [];
let mediaUuid = '';
let mediaPath = '';
let mediaSignature = '';
let mediaBusy = false;
let mediaHls = null;
let mediaHlsToken = '';
let mediaPlaybackBase = 0;
let mediaPlaybackDuration = 0;
const VALID_VIEWS = new Set(['dashboard','media','storage','system','nina','pihole','advanced']);
let currentView = VALID_VIEWS.has(location.hash.slice(1)) ? location.hash.slice(1) : 'dashboard';

const powerProfiles = [
  {key:'eco', name:'ECO', glyph:'E', governor:'powersave', mhz:900, description:'Il nodo respira piano: consumi e temperatura ridotti per monitoraggio e servizi leggeri.'},
  {key:'balanced', name:'Bilanciato', glyph:'B', governor:'schedutil', mhz:1200, description:'Il punto dolce: consumi contenuti e accelerazione immediata quando LiveVault ne ha bisogno.'},
  {key:'performance', name:'Performance', glyph:'P', governor:'schedutil', mhz:1500, description:'Risposta rapida con frequenza dinamica completa, ideale per più stream simultanei.'},
  {key:'max', name:'MAX', glyph:'M', governor:'performance', mhz:1500, description:'Massima reattività costante entro i limiti ufficiali del Compute Module 4.'},
];

const actionLabels = {
  eject_nvme: ['Espulsione sicura NVMe', 'Le registrazioni verranno trasferite al buffer interno da 4 GB mentre Docker resta online. Scollega il cavo soltanto dopo il messaggio finale.'],
  attach_nvme: ['Rimonta NVMe', 'Rimonta NVMe, trasferisce e verifica le parti nel buffer, poi riprende la registrazione su NVMe e lo stitching abituale.'],
  restart_livevault: ['Riavvia LiveVault', 'La registrazione corrente verrà chiusa correttamente e il recorder ripartirà.'],
  restart_docker: ['Riavvia Docker', 'Tutti i container, incluso Coolify, saranno indisponibili per alcuni secondi.'],
  backup_now: ['Avvia backup', 'Crea subito una copia consistente del database LiveVault sulla partizione USB SHARE.'],
  restart_pihole: ['Riavvia Pi-hole', 'Il DNS locale sarà indisponibile per alcuni secondi.'],
  power_profile: ['Applica profilo energetico', 'La frequenza CPU e le opzioni di rete verranno aggiornate immediatamente.'],
  media_eject: ['Espelli supporto USB', 'Chiude l’indicizzazione, smonta il supporto e scarica i buffer. Rimuovilo solo dopo il messaggio finale.'],
  media_rescan: ['Aggiorna supporti media', 'Rileva e monta in sola lettura i dispositivi USB rimovibili consentiti.'],
  reboot: ['Riavvia ASIAIR', 'L’intero server verrà riavviato. Il pannello e LiveVault torneranno automaticamente entro circa due minuti.'],
};

function bytes(value) {
  if (value == null || !Number.isFinite(Number(value)) || value < 0) return '—';
  const units = ['B','KB','MB','GB','TB'];
  let size = Number(value), unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit++; }
  return `${size >= 100 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`;
}
function duration(seconds) {
  seconds = Math.max(0, Number(seconds) || 0);
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d ? `${d}g ${h}h` : h ? `${h}h ${m}m` : `${m} min`;
}
function ring(id, value, label) {
  const el = $(id);
  el.style.setProperty('--value', Math.max(0, Math.min(100, value || 0)));
  el.querySelector('span').textContent = label;
}
function serviceCard(name, state, detail, action = '') {
  const good = state === 'active' || state === true;
  const disabled = state === 'inactive' || state === 'not-found' || state === false;
  return `<article class="service-card"><header><strong>${escapeHtml(name)}</strong><span class="service-state ${good ? 'good' : disabled ? '' : 'warn'}">${good ? 'Attivo' : disabled ? 'Inattivo' : escapeHtml(state)}</span></header><small>${escapeHtml(detail)}</small>${action && good ? `<button class="mini-action" data-action="${action}" data-confirm="true">Riavvia</button>` : ''}</article>`;
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}
function dnsPlatformLabel(platform) {
  return ({android:'Android',ios:'iPhone',ipad:'iPad',macos:'Mac',windows:'Windows',generic:'Altro'})[platform] || 'Dispositivo';
}
function dnsDeviceIcon(platform) {
  const paths = {
    android:'M7 5h10l1 3v10a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V8l1-3Zm2-3 1 2h4l1-2 1 .5L15.2 4H8.8L8 2.5 9 2Zm0 6v8h6V8H9Z',
    ios:'M8 2h8a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Zm0 3v14h8V5H8Zm3 14h2v1h-2v-1Z',
    ipad:'M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm0 3v12h14V6H5Zm6 13h2v1h-2v-1Z',
    macos:'M4 4h16v12H4V4Zm2 2v8h12V6H6Zm-4 12h20v2H2v-2Z',
    windows:'M3 4l8-1v8H3V4Zm10-1 8-1v9h-8V3ZM3 13h8v8l-8-1v-7Zm10 0h8v9l-8-1v-8Z',
    generic:'M4 5h16v12H4V5Zm2 2v8h12V7H6Zm3 12h6v2H9v-2Z'
  };
  return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[platform] || paths.generic}"/></svg>`;
}
function renderDnsDevices(devices = [], remote = {}) {
  const list = $('#piholeDeviceList');
  if (!list) return;
  $('#piholeDeviceOnline').textContent = Number(remote.online_device_count || devices.filter(item => item.online).length || 0).toLocaleString('it-IT');
  $('#piholeDeviceEnabled').textContent = Number(remote.enabled_device_count || devices.filter(item => item.enabled).length || 0).toLocaleString('it-IT');
  $('#piholeDeviceTotal').textContent = Number(remote.device_count ?? devices.length).toLocaleString('it-IT');
  if (!devices.length) {
    list.innerHTML = '<div class="dns-device-empty"><strong>Nessun dispositivo remoto</strong><small>Aggiungi il Samsung, un iPhone, iPad o Mac e copia la configurazione una sola volta.</small></div>';
    if (selectedDnsDeviceId) closeDnsDetail();
    return;
  }
  list.innerHTML = devices.map(item => {
    const stateClass = !item.enabled ? 'blocked' : item.online ? 'online' : '';
    const stateText = !item.enabled ? 'Bloccato' : item.online ? 'Online' : 'Offline';
    const last = item.last_seen ? `Ultima attività ${availabilityDate(item.last_seen)}` : 'Mai connesso';
    const host = item.hostname || (remote.dot === 'active' ? 'Hostname in generazione' : 'DoT in configurazione');
    return `<article class="dns-device-row" tabindex="0" role="button" data-dns-device-id="${escapeHtml(item.id)}" aria-label="Gestisci ${escapeHtml(item.name)}">
      <div class="dns-device-id"><span class="dns-device-icon">${dnsDeviceIcon(item.platform)}</span><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(dnsPlatformLabel(item.platform))} · ${escapeHtml(host)}</small></div></div>
      <div class="dns-device-meta"><strong>${escapeHtml(item.last_ip || 'Nessun IP')}</strong><small>${escapeHtml(last)}</small></div>
      <div class="dns-device-state ${stateClass}"><i></i><span>${stateText}</span></div>
      <div class="dns-device-queries"><strong>${Number(item.queries || 0).toLocaleString('it-IT')}</strong><small>query</small></div><span class="dns-device-chevron">›</span>
    </article>`;
  }).join('');
  $$('[data-dns-device-id]').forEach(row => {
    const open = () => openDnsDetail(row.dataset.dnsDeviceId);
    row.addEventListener('click', open);
    row.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
  });
  if (selectedDnsDeviceId) {
    const current = devices.find(item => item.id === selectedDnsDeviceId);
    if (current) renderDnsDetail(current, selectedDnsConnection);
    else closeDnsDetail();
  }
}
function renderPihole(pihole = {}) {
  const installed = Boolean(pihole.installed);
  const dnsOnline = Boolean(pihole.dns_online);
  const ftlActive = Boolean(pihole.ftl_active);
  const blockingOn = pihole.blocking === 'on';
  const blockingOff = pihole.blocking === 'off';
  const stats = pihole.stats || {};
  const versions = pihole.versions || {};
  const lan = pihole.lan || {};
  const remote = pihole.remote || {};
  const installState = $('#piholeInstallState');
  if (installState) { installState.textContent = installed ? 'Installato' : 'Non installato'; installState.className = `status-chip ${installed ? 'good' : 'bad'}`; }
  const title = $('#piholeBlockingTitle'), detail = $('#piholeDnsDetail');
  if (!installed) { title.textContent='Pi-hole non installato'; detail.textContent='DNS e filtering non disponibili.'; }
  else if (!ftlActive) { title.textContent='FTL fermo'; detail.textContent='Il servizio DNS Pi-hole non è attivo.'; }
  else if (!dnsOnline) { title.textContent='DNS non raggiungibile'; detail.textContent='FTL è attivo ma la query locale non riceve risposta.'; }
  else if (blockingOn) { title.textContent='DNS + blocking attivi'; detail.textContent='Il DNS resta online anche quando spegni il filtering.'; }
  else if (blockingOff) { title.textContent='DNS attivo · blocking OFF'; detail.textContent='Risoluzione attiva senza filtraggio; Internet non viene interrotto.'; }
  else { title.textContent='Configurazione incompleta'; detail.textContent='DNS attivo, stato blocking non determinato.'; }
  const toggle=$('#piholeToggle'); if(toggle){toggle.checked=blockingOn;toggle.disabled=piholeBusy||!installed||!dnsOnline;}
  $('#piholeToggleLabel').textContent=blockingOn?'ON':blockingOff?'OFF':'—';
  const count=value=>Number(value||0).toLocaleString('it-IT');
  $('#piholeQueries').textContent=installed?count(stats.queries):'—'; $('#piholeBlocked').textContent=installed?count(stats.blocked):'—';
  $('#piholePercent').textContent=installed?`${Number(stats.percent_blocked||0).toFixed(1)}%`:'—'; $('#piholeClients').textContent=installed?count(stats.active_clients):'—'; $('#piholeGravity').textContent=installed?count(stats.gravity_domains):'—';
  $('#piholeDnsState').textContent=dnsOnline?'Online':installed?'Offline':'Non installato'; $('#piholeFtlState').textContent=ftlActive?'Attivo':installed?(pihole.service||'Inattivo'):'Non installato';
  $('#piholeVersion').textContent=installed?`Core ${versions.core||'—'} · Web ${versions.web||'—'} · FTL ${versions.ftl||'—'}`:'—'; $('#piholeLanDns').textContent=lan.dns||'—';
  const dotText=remote.dot==='active'?'Online':remote.dot==='configured'?'Pronto al TLS':'Non configurato'; const dohText=remote.doh==='active'?'Online':remote.doh==='configured'?'Configurato':'Non configurato';
  $('#piholeDotState').textContent=dotText; $('#piholeDohState').textContent=dohText;
  const tlsText=remote.tls==='active'?'Valido':remote.tls==='expired'?'Scaduto':'Non verificato'; $('#piholeTlsState').textContent=tlsText;
  $('#piholeTlsDetail').textContent=remote.certificate_expires?`Scadenza ${availabilityDate(remote.certificate_expires)}`:'Certificato remoto';
  const remoteHost=remote.base_domain||remote.hostname||''; $('#piholeRemoteHost').textContent=remoteHost||'In configurazione'; $('#piholeRemoteNote').textContent=remoteHost?'Endpoint infrastrutturale. Su Android usa solo l’hostname personale del dispositivo nella sezione Accessi personali.':(remote.reason||'Remote DNS non configurato.');
  const network=remote.network||{}; const networkBanner=$('#piholeNetworkBanner');
  if(networkBanner){
    const state=network.state||'unknown'; const show=state!=='ready'&&state!=='unknown'; networkBanner.hidden=!show;
    if(show){
      $('#piholeNetworkKicker').textContent=state==='permission_required'?'UNA SOLA AUTORIZZAZIONE':'RETE IPV6';
      $('#piholeNetworkTitle').textContent=state==='permission_required'?'Abilita “Modifica impostazioni” sulla iliadbox':state==='routing_pending'?'Delegazione IPv6 in attesa':'Configurazione rete da completare';
      $('#piholeNetworkReason').textContent=network.reason||'OpenAstro completa automaticamente la configurazione appena la rete è pronta.';
      const ns=$('#piholeNetworkState'); ns.textContent=state==='permission_required'?'Una tantum':'In attesa'; ns.className='status-chip warn';
    }
  }
  $('#piholeRemoteIpv6').textContent=remote.ipv6||'—';
  const badge=$('#piholeRemoteBadge'); badge.textContent=remote.reachable?'DNS remoto online':remote.configured?'Configurazione in corso':'Solo LAN'; badge.className=`status-chip ${remote.reachable?'good':''}`;
  const dot=$('#piholeRemoteDot'); dot.className=`dns-live-dot ${remote.reachable?'good':remote.configured?'warn':''}`;
  $('#piholeGateway').textContent=remote.gateway==='active'?'Attivo':remote.gateway==='stale'?'Dati non aggiornati':'Non verificato'; $('#piholeAuthorized').textContent=remote.authorized_query?'OK':'Non verificata'; $('#piholeUnauthorized').textContent=remote.unauthorized_denied?'Negato':'Non verificato'; $('#piholeChecked').textContent=remote.checked_at?availabilityDate(remote.checked_at):'—';
  renderDnsDevices(remote.devices||[],remote);
  const admin=$('#piholeAdminLink'); if(admin){admin.href=installed&&lan.admin_url?lan.admin_url:'#';admin.setAttribute('aria-disabled',installed&&lan.admin_url?'false':'true');}
  ['#piholeRestart','#piholeGravityAction'].forEach(selector=>{const button=$(selector);if(button)button.disabled=piholeBusy||!installed||!ftlActive;});
}
async function postPihole(endpoint, payload = {}) {
  if (piholeBusy) return null;
  piholeBusy=true; if($('#piholeToggle'))$('#piholeToggle').disabled=true; if($('#piholeRestart'))$('#piholeRestart').disabled=true; if($('#piholeGravityAction'))$('#piholeGravityAction').disabled=true;
  try {
    const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(payload),signal:AbortSignal.timeout(300000)});
    if(response.status===401){showLogin();return null;}
    const result=await response.json(); if(!response.ok||!result.ok)throw new Error(result.error||result.message||`HTTP ${response.status}`);
    return result;
  } catch(error){toast(error.message||'Operazione Pi-hole non riuscita.',true);return null;}
  finally{piholeBusy=false;setTimeout(refresh,350);}
}
async function setPiholeState(enabled) {
  const result=await postPihole(enabled?'/api/pihole/blocking/enable':'/api/pihole/blocking/disable'); if(result)toast(enabled?'Filtering Pi-hole attivato.':'Filtering disattivato; DNS ancora attivo.'); return result;
}
function closeDnsDetail(){selectedDnsDeviceId='';selectedDnsConnection=null;const panel=$('#piholeDeviceDetail');if(panel)panel.hidden=true;}
function renderDnsDetail(device, connection = null) {
  const panel=$('#piholeDeviceDetail'); if(!panel)return; panel.hidden=false;
  $('#dnsDeviceDetailPlatform').textContent=dnsPlatformLabel(device.platform).toUpperCase(); $('#dnsDeviceDetailTitle').textContent=device.name;
  const state=$('#dnsDeviceDetailState'); state.textContent=!device.enabled?'Bloccato':device.online?'Online':'Abilitato'; state.className=`status-chip ${!device.enabled?'blocked':device.online?'online':''}`;
  $('#dnsDeviceLastSeen').textContent=device.last_seen?`Ultima attività ${availabilityDate(device.last_seen)}`:'Mai connesso'; $('#dnsDeviceIp').textContent=device.last_ip||'—'; $('#dnsDeviceConnections').textContent=Number(device.active_connections||0).toLocaleString('it-IT'); $('#dnsDeviceQueries').textContent=Number(device.queries||0).toLocaleString('it-IT'); $('#dnsDeviceBlocked').textContent=Number(device.blocked||0).toLocaleString('it-IT');
  $('#dnsDeviceHostname').value=connection?.hostname||device.hostname||''; $('#dnsDeviceDoh').value=connection?.doh_url||'';
  const dotReady=Boolean(connection?.dot_ready); $('#dnsDotHint').textContent=dotReady?'Su Samsung: Impostazioni → Connessioni → Altre impostazioni → DNS privato → Nome host provider. Incolla questo hostname una sola volta.':'DoT è predisposto ma il dominio/TLS pubblico non è ancora verificato; non configurare Android finché questo campo resta vuoto.';
  const apple=$('#dnsAppleProfile'); apple.href=`/api/pihole/apple.mobileconfig?id=${encodeURIComponent(device.id)}`; apple.hidden=!['ios','ipad','macos'].includes(device.platform);
  $('#dnsDeviceToggle').textContent=device.enabled?'Blocca accesso':'Riattiva'; $('#dnsDeviceToggle').dataset.enabled=device.enabled?'1':'0';
  const guides={android:'<strong>Samsung / Android</strong>Quando il campo DoT è disponibile: copia l’hostname, apri DNS privato, scegli “Nome host provider DNS privato” e incollalo. Nessuna app e nessuna autorizzazione ad ogni cambio rete.',ios:'<strong>iPhone</strong>Scarica “Profilo Apple”, aprilo e completa l’installazione in Impostazioni → Generali → VPN e gestione dispositivo. Il profilo usa il tuo accesso DoH personale.',ipad:'<strong>iPad</strong>Scarica “Profilo Apple” e installalo da Impostazioni → Generali → VPN e gestione dispositivo.',macos:'<strong>Mac</strong>Scarica “Profilo Apple” e aprilo dalle impostazioni Profili/Gestione dispositivo.',windows:'<strong>Windows</strong>Usa l’URL DoH personale nei client che accettano un template DoH personalizzato. Il dispositivo può essere revocato dal pannello.',generic:'<strong>Altro dispositivo</strong>Usa DoT se supporta un hostname Private DNS, oppure l’URL DoH personale nei client compatibili.'};
  $('#dnsInstallGuide').innerHTML=guides[device.platform]||guides.generic;
}
async function openDnsDetail(deviceId) {
  const devices=latestState?.pihole?.remote?.devices||[]; const device=devices.find(item=>item.id===deviceId); if(!device)return;
  selectedDnsDeviceId=deviceId; selectedDnsConnection=null; renderDnsDetail(device,null);
  try{const response=await fetch(`/api/pihole/connection?id=${encodeURIComponent(deviceId)}`,{cache:'no-store'});if(response.status===401)return showLogin();const data=await response.json();if(!response.ok||!data.ok)throw new Error(data.error||'Accesso non disponibile.');selectedDnsConnection=data;const current=(latestState?.pihole?.remote?.devices||[]).find(item=>item.id===deviceId)||device;renderDnsDetail(current,data);}catch(error){toast(error.message,true);}
}
async function copyField(selector, message) { const field=$(selector); if(!field?.value)return toast('Valore non ancora disponibile.',true); try{await navigator.clipboard.writeText(field.value);toast(message);}catch{field.select();toast('Seleziona e copia il valore.');} }

function mean(points, key) {
  const values = points.filter(point => point[key] != null).map(point => Number(point[key])).filter(Number.isFinite);
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}
function linePath(points, key, min, max) {
  const span = Math.max(1, max - min);
  const start = points[0]?.t || 0, range = Math.max(1, (points.at(-1)?.t || 0) - start);
  let previous = null;
  return points.map(point => {
    if (point[key] == null || !Number.isFinite(Number(point[key]))) { previous = null; return ''; }
    const x = (point.t - start) / range * 600;
    const y = 154 - (Number(point[key]) - min) / span * 132;
    const move = previous == null || point.t - previous > Math.max(30, range / 100);
    previous = point.t;
    return `${move ? 'M' : 'L'}${x.toFixed(1)},${Math.max(12, Math.min(160, y)).toFixed(1)}`;
  }).join(' ');
}
function chartMarkup(points, series, min, max) {
  if (points.length < 2) return '<span class="chart-empty">Raccolta dati reali…</span>';
  const grid = [22,55,88,121,154].map(y => `<line x1="0" y1="${y}" x2="600" y2="${y}"/>`).join('');
  const paths = series.map((item, index) => {
    const path = linePath(points, item.key, min, max);
    return `<path class="chart-line ${item.className}" d="${path}"/>`;
  }).join('');
  const time = point => new Date(point.t * 1000).toLocaleTimeString('it-IT', {hour:'2-digit',minute:'2-digit'});
  return `<svg viewBox="0 0 600 170" preserveAspectRatio="none" role="img" aria-label="${series.map(item => item.key).join(', ')}: ${min.toFixed(0)}–${max.toFixed(0)}"><g class="chart-gridlines">${grid}</g>${paths}</svg><div class="chart-axis"><span>${time(points[0])}</span><span>${time(points.at(-1))}</span></div>`;
}
function measuredWatts(power) {
  return power?.measurement === 'measured' && power.watts != null && Number.isFinite(Number(power.watts)) ? Number(power.watts) : null;
}
function availabilityDate(timestamp) {
  if (!timestamp) return '—';
  return new Date(timestamp * 1000).toLocaleString('it-IT', {day:'2-digit',month:'2-digit',year:'2-digit',hour:'2-digit',minute:'2-digit'});
}
function renderAvailability(availability = {}, rangeSeconds = historyRange) {
  const pct = availability.uptime_percent;
  $('#availabilityPercent').textContent = pct == null ? '—' : `${pct.toFixed(pct >= 99 ? 2 : 1)}%`;
  const known = Math.max(0, rangeSeconds - Number(availability.unknown_seconds || 0));
  $('#availabilityKnown').textContent = availability.unknown_seconds > 0 ? `${duration(known)} monitorati · ${duration(availability.unknown_seconds)} non storicizzati` : `${duration(known)} monitorati`;
  $('#downtimeTotal').textContent = duration(availability.downtime_seconds || 0);
  const rows = availability.downtimes || [];
  $('#downtimeCount').textContent = rows.length ? `${rows.length} event${rows.length === 1 ? 'o' : 'i'} offline` : 'nessun evento offline';
  const last = rows.at(-1);
  $('#lastDowntime').textContent = last ? duration(last.seconds || (last.end-last.start)) : '—';
  $('#lastDowntimeWhen').textContent = last ? `${availabilityDate(last.start)} → ${availabilityDate(last.end)}` : 'nessun downtime registrato';
  const now = Date.now()/1000, start = now - rangeSeconds;
  const segments = rows.map(row => {
    const left = Math.max(0, (row.start - start) / rangeSeconds * 100);
    const right = Math.min(100, (row.end - start) / rangeSeconds * 100);
    const width = Math.max(.25, right-left);
    return `<b class="availability-offline" style="left:${left.toFixed(4)}%;width:${width.toFixed(4)}%" title="Offline ${availabilityDate(row.start)} → ${availabilityDate(row.end)} · ${duration(row.seconds||0)}"></b>`;
  }).join('');
  const unknownWidth = Math.max(0, Math.min(100, Number(availability.unknown_seconds || 0) / rangeSeconds * 100));
  $('#availabilityTimeline').innerHTML = `<i class="availability-online"></i>${unknownWidth ? `<b class="availability-unknown" style="left:0;width:${unknownWidth.toFixed(4)}%" title="Periodo precedente all'inizio del monitoraggio"></b>` : ''}${segments}`;
}

function renderHistory(points, energy = {}, availability = {}, rangeSeconds = historyRange) {
  latestHistory = points;
  renderAvailability(availability, rangeSeconds);
  const cpuAvg = mean(points, 'cpu'), ramAvg = mean(points, 'ram');
  $('#computeChart').innerHTML = chartMarkup(points, [{key:'cpu',className:'cpu'},{key:'ram',className:'ram'}], 0, 100);
  $('#cpuAverage').textContent = points.some(point => point.cpu != null) ? `CPU media ${cpuAvg.toFixed(0)}%` : 'CPU media —';
  $('#ramAverage').textContent = points.some(point => point.ram != null) ? `RAM media ${ramAvg.toFixed(0)}%` : 'RAM media —';

  const temps = points.filter(point => point.temp != null).map(point => Number(point.temp)).filter(Number.isFinite);
  $('#temperatureChart').innerHTML = chartMarkup(points, [{key:'temp',className:'temp'}], 25, Math.max(80, ...temps));
  $('#tempPeak').textContent = temps.length ? `${Math.max(...temps).toFixed(0)}° picco` : '—';
  $('#tempAverage').textContent = temps.length ? `media ${mean(points, 'temp').toFixed(1)}°` : 'media —';

  const trafficPeak = Math.max(0, ...points.flatMap(point => [Number(point.rx) || 0, Number(point.tx) || 0]));
  $('#networkChart').innerHTML = chartMarkup(points, [{key:'rx',className:'cpu'},{key:'tx',className:'ram'}], 0, Math.max(1, trafficPeak));
  $('#networkPeak').textContent = `${bytes(trafficPeak)}/s picco`;

  const disks = points.filter(point => point.disk != null).map(point => Number(point.disk)).filter(Number.isFinite);
  $('#diskChart').innerHTML = chartMarkup(points, [{key:'disk',className:'disk'}], 0, 100);
  $('#diskTrend').textContent = disks.length ? `${disks.at(-1).toFixed(1)}%` : '—';
  $('#diskDelta').textContent = disks.length > 1 ? `variazione ${(disks.at(-1) - disks[0]) >= 0 ? '+' : ''}${(disks.at(-1) - disks[0]).toFixed(2)}%` : 'variazione —';

  const watts = points.filter(point => point.watts != null).map(point => Number(point.watts)).filter(Number.isFinite);
  const wattMax = Math.max(10, ...watts);
  $('#wattChart').innerHTML = chartMarkup(points, [{key:'watts',className:'watt'}], 0, wattMax);
  $('#wattPeak').textContent = energy.peak_watts != null ? `${energy.peak_watts.toFixed(1)} W picco` : '—';
  $('#wattAverage').textContent = energy.average_watts != null ? `media ${energy.average_watts.toFixed(1)} W` : 'media —';
  const coverage = energy.covered_seconds < 60 ? `${energy.covered_seconds} s` : duration(energy.covered_seconds);
  $('#energyMeasured').textContent = energy.wh != null ? `${energy.wh.toFixed(2)} Wh · ${coverage} coperti` : 'In attesa di campioni misurati';
}
async function refreshHistory() {
  if (historyBusy || document.hidden || !signedIn || currentView !== 'system') return;
  historyBusy = true;
  const requestedRange = historyRange;
  try {
    const response = await fetch(`/api/history?range=${requestedRange}`, {cache:'no-store', signal: AbortSignal.timeout(15000)});
    if (response.status === 401) return showLogin();
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    if (requestedRange === historyRange) renderHistory(result.points || [], result.energy || {}, result.availability || {}, result.range || requestedRange);
    $('#historyStatus').textContent = requestedRange <= 86400 ? `Dettaglio 10 s · downtime reale · ${Intl.DateTimeFormat().resolvedOptions().timeZone}` : `Storico compattato · downtime reale · ${Intl.DateTimeFormat().resolvedOptions().timeZone}`;
  } catch (_) { $('#historyStatus').textContent = 'Storico non aggiornato. Riprova con Aggiorna.'; }
  finally { historyBusy = false; if (requestedRange !== historyRange) refreshHistory(); }
}

function previewPower(index, fromServer = false) {
  const profile = powerProfiles[Number(index)] || powerProfiles[1];
  $('#powerSlider').value = String(powerProfiles.indexOf(profile));
  $('#powerSlider').style.setProperty('--power', `${powerProfiles.indexOf(profile) / 3 * 100}%`);
  $('#profileIndex').textContent = `0${powerProfiles.indexOf(profile) + 1} / 04`;
  $('#profileName').textContent = profile.name;
  $('#profileDescription').textContent = profile.description;
  $('#powerGlyph').textContent = profile.glyph;
  $('#power').dataset.profile = profile.key;
  $('#wifiKeep').disabled = false;
  $('#wifiHint').textContent = $('#wifiKeep').checked ? 'Hotspot locale disponibile; Internet continua via Ethernet.' : 'Wi-Fi spento; pannello pubblico e Funnel continuano via Ethernet.';
  if (!fromServer) powerDirty = true;
}
function renderPower(power) {
  if (!power) return;
  activePowerKey = powerProfiles.some(item => item.key === power.profile) ? power.profile : 'balanced';
  activeWifiOn = Boolean(power.wifi_radio);
  const foundIndex = powerProfiles.findIndex(item => item.key === power.profile);
  const index = foundIndex < 0 ? 1 : foundIndex;
  if (!powerDirty) {
    $('#wifiKeep').checked = power.wifi_policy !== 'off';
    previewPower(index, true);
  }
  $('#currentProfile').textContent = `${(power.profile || 'balanced').toUpperCase()} · ATTIVO`;
  $('#powerGovernor').textContent = power.governor || '—';
  $('#powerFrequency').textContent = `${power.current_mhz || 0} / ${power.max_mhz || 0} MHz`;
  $('#powerWifi').textContent = power.hotspot ? 'OpenAstro-AP' : power.wifi_radio ? 'radio attiva' : 'spento';
  $('#powerHealth').textContent = power.undervoltage_now ? 'tensione bassa' : power.throttled_now ? 'limitato ora' : power.power_event_seen ? 'cali passati' : 'stabile';
  $('#powerHealth').className = power.undervoltage_now || power.throttled_now ? 'bad-text' : power.power_event_seen ? 'warn-text' : '';
  const watts = measuredWatts(power);
  $('#powerWatts').textContent = watts != null ? `${watts.toFixed(1)} W DC` : 'Sensore non disponibile';
  $('#quickWifiTitle').textContent = activeWifiOn ? 'Spegni Wi-Fi' : 'Attiva Wi-Fi';
  $('#quickWifiState').textContent = activeWifiOn ? 'Hotspot attivo' : 'Radio disattivata';
  $('#quickWifi').classList.toggle('is-off', !activeWifiOn);
}

function mediaUrl(path, download = false, uuid = mediaUuid) {
  const query = new URLSearchParams({uuid, path});
  if (download) query.set('download', '1');
  return `/api/media/file?${query}`;
}
function mediaThumbUrl(path, uuid = mediaUuid) {
  return `/api/media/thumbnail?${new URLSearchParams({uuid, path})}`;
}
function mediaProbeUrl(path, uuid = mediaUuid) {
  return `/api/media/probe?${new URLSearchParams({uuid, path})}`;
}
function mediaPlanUrl(path, uuid = mediaUuid, audioStream = null) { const q=new URLSearchParams({uuid,path}); if(audioStream!==null&&audioStream!==undefined&&audioStream!=='')q.set('audio_stream',audioStream); return `/api/media/play-plan?${q}`; }
function mediaSubtitleUrl(track, uuid = mediaUuid) { const q=new URLSearchParams({uuid,path:track.path}); if(track.kind==='embedded'&&track.stream_index!==undefined)q.set('stream',track.stream_index); return `/api/media/subtitle?${q}`; }
function mediaSubtitleKey(track) { return track.kind==='embedded' ? `embedded:${track.stream_index}` : `sidecar:${track.path}`; }
function mediaVttSeconds(value) {
  const match=String(value||'').trim().match(/^(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})$/);
  if(!match)return NaN; return (Number(match[1]||0)*3600)+(Number(match[2])*60)+Number(match[3])+(Number(match[4])/1000);
}
function mediaSubtitleText(value) {
  const stripped=String(value||'').replace(/<[^>]*>/g,''); const node=document.createElement('textarea'); node.innerHTML=stripped; return node.value.trim();
}
function parseMediaWebVtt(value) {
  const blocks=String(value||'').replace(/\r/g,'').split(/\n{2,}/), cues=[];
  for(const block of blocks){const lines=block.split('\n').map(line=>line.trimEnd());const timingIndex=lines.findIndex(line=>line.includes('-->'));if(timingIndex<0)continue;const timing=lines[timingIndex].split(/\s+-->\s+/);if(timing.length<2)continue;const start=mediaVttSeconds(timing[0]);const end=mediaVttSeconds(timing[1].trim().split(/\s+/)[0]);if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)continue;const text=mediaSubtitleText(lines.slice(timingIndex+1).join('\n'));if(text)cues.push({start,end,text});}
  cues.sort((a,b)=>a.start-b.start||a.end-b.end); return cues;
}
function mediaSubtitleCueAt(cues,time) {
  if(!cues?.length||!Number.isFinite(time))return null; let lo=0,hi=cues.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(cues[mid].start<=time)lo=mid+1;else hi=mid;}
  for(let i=Math.min(cues.length-1,lo-1);i>=0&&i>=lo-8;i--){if(cues[i].start<=time&&time<cues[i].end)return cues[i];}
  return null;
}
function createMediaSubtitleController(video,shell,tracks,uuid,getGlobalTime) {
  const overlay=shell?.querySelector('#mediaSubtitleOverlay'); let cues=[],requestId=0,lastText='';
  const render=()=>{if(!overlay)return;const cue=mediaSubtitleCueAt(cues,Number(getGlobalTime?.()));const text=cue?.text||'';if(text===lastText)return;lastText=text;overlay.textContent=text;overlay.hidden=!text;overlay.setAttribute('aria-hidden',text?'false':'true');if(cue)overlay.dataset.cueStart=String(cue.start);else delete overlay.dataset.cueStart;};
  const clear=()=>{lastText='';if(overlay){overlay.textContent='';overlay.hidden=true;overlay.setAttribute('aria-hidden','true');delete overlay.dataset.cueStart;}};
  ['timeupdate','seeking','seeked','loadedmetadata','play'].forEach(name=>video?.addEventListener(name,render));
  return {
    async set(key){const id=++requestId;cues=[];clear();if(!key||key==='off')return 0;const track=(tracks||[]).find(item=>mediaSubtitleKey(item)===key);if(!track)throw new Error('Traccia sottotitoli non trovata.');const response=await fetch(mediaSubtitleUrl(track,uuid),{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!response.ok)throw new Error(`Sottotitoli non disponibili (HTTP ${response.status}).`);const parsed=parseMediaWebVtt(await response.text());if(id!==requestId)return 0;if(!parsed.length)throw new Error('Traccia sottotitoli vuota.');cues=parsed;render();return cues.length;},
    update:render, clear,
    destroy(){requestId++;['timeupdate','seeking','seeked','loadedmetadata','play'].forEach(name=>video?.removeEventListener(name,render));cues=[];clear();}
  };
}
async function stopMediaHls() {
  if (mediaHls) { try { mediaHls.destroy(); } catch (_) {} mediaHls=null; }
  const token=mediaHlsToken; mediaHlsToken='';
  if (token) { try { await fetch('/api/media/hls/stop',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({token})}); } catch (_) {} }
}
function addMediaSubtitleTracks(video, tracks, uuid, selected='off') {
  if(selected==='off')return; const track=(tracks||[]).find(item=>mediaSubtitleKey(item)===selected); if(!track)return; const node=document.createElement('track'); node.kind='subtitles'; node.label=track.label||'Sottotitoli'; node.srclang=(track.language||'sub').slice(0,8).toLowerCase(); node.src=mediaSubtitleUrl(track,uuid); node.dataset.trackKey=selected; video.appendChild(node); try{node.track.mode='showing';}catch(_){} node.addEventListener('load',()=>{try{node.track.mode='showing';}catch(_){}});
}
function setMediaSubtitle(video,key,tracks,uuid){ [...video.querySelectorAll('track[data-track-key]')].forEach(node=>node.remove()); addMediaSubtitleTracks(video,tracks,uuid,key); }
function mediaAudioLabel(track){ const details=[String(track.codec||'').toUpperCase(),track.channel_layout||((track.channels||0)>2?`${track.channels}ch`:'')].filter(Boolean).join(' · '); return details?`${track.label} — ${details}`:track.label; }
function mediaTransportIcon(kind){
  const icons={
    play:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>',
    pause:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h4v14H7zm6 0h4v14h-4z"/></svg>',
    volume:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9H4zm11.5 3a4.5 4.5 0 0 0-2-3.74v7.48A4.5 4.5 0 0 0 15.5 12zm0-8v2.1a7 7 0 0 1 0 11.8V20a9 9 0 0 0 0-16z"/></svg>',
    muted:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9H4zm11.3.9 1.6 1.6 1.6-1.6 1.4 1.4-1.6 1.6 1.6 1.6-1.4 1.4-1.6-1.6-1.6 1.6-1.4-1.4 1.6-1.6-1.6-1.6z"/></svg>',
    fullscreen:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 9V5h4V3H3v6h2zm10-6v2h4v4h2V3h-6zM5 15H3v6h6v-2H5v-4zm14 4h-4v2h6v-6h-2v4z"/></svg>'
  }; return icons[kind]||'';
}
function mediaHlsPlayerMarkup(){
  return `<div class="media-video-shell"><video playsinline preload="metadata"></video><div class="media-subtitle-overlay" id="mediaSubtitleOverlay" aria-hidden="true" hidden></div><div class="media-video-controls" id="mediaPlayerControls"><button type="button" id="mediaPlayerPlayPause" class="media-transport-button" aria-label="Riproduci">${mediaTransportIcon('play')}</button><div class="media-player-seek" id="mediaPlayerSeek"><input id="mediaPlayerSeekInput" type="range" min="0" max="0" step="1" value="0" aria-label="Posizione nel film"></div><strong class="media-player-clock"><b id="mediaPlayerSeekCurrent">0:00</b><span>/</span><b id="mediaPlayerSeekDuration">0:00</b></strong><button type="button" id="mediaPlayerMute" class="media-transport-button" aria-label="Disattiva audio">${mediaTransportIcon('volume')}</button><button type="button" id="mediaPlayerFullscreen" class="media-transport-button" aria-label="Schermo intero">${mediaTransportIcon('fullscreen')}</button></div></div>`;
}
function renderMediaFullSeek(media, duration, initialPosition, onSeek){
  const host=$('#mediaPlayerSeek'), input=$('#mediaPlayerSeekInput'), current=$('#mediaPlayerSeekCurrent'), total=$('#mediaPlayerSeekDuration');
  const play=$('#mediaPlayerPlayPause'), mute=$('#mediaPlayerMute'), fullscreen=$('#mediaPlayerFullscreen'), shell=media?.closest('.media-video-shell');
  const max=Math.max(0,Math.floor(Number(duration)||0)), initial=Math.max(0,Math.min(max,Number(initialPosition)||0));
  if(!media||!host||!input||!current||!total||max<=0)return{update:()=>{},destroy:()=>{}};
  host.classList.remove('seeking'); input.max=String(max); input.value=String(Math.floor(initial)); current.textContent=mediaDuration(initial); total.textContent=mediaDuration(max);
  let dragging=false,busy=false,pseudoFullscreen=false,orientationLocked=false;
  const syncPlay=()=>{if(!play)return;const paused=media.paused;play.innerHTML=mediaTransportIcon(paused?'play':'pause');play.setAttribute('aria-label',paused?'Riproduci':'Pausa');};
  const syncMute=()=>{if(!mute)return;mute.innerHTML=mediaTransportIcon(media.muted?'muted':'volume');mute.setAttribute('aria-label',media.muted?'Riattiva audio':'Disattiva audio');};
  const preview=()=>{dragging=true;current.textContent=mediaDuration(Number(input.value)||0);};
  const commit=async()=>{const target=Math.max(0,Math.min(max,Number(input.value)||0));dragging=false;if(busy)return;busy=true;host.classList.add('seeking');input.disabled=true;try{await onSeek(target);}catch(error){toast(error.message||'Seek non riuscito.',true);}finally{busy=false;host.classList.remove('seeking');input.disabled=false;}};
  const togglePlay=()=>media.paused?media.play().catch(()=>{}):media.pause();
  const toggleMute=()=>{media.muted=!media.muted;syncMute();};
  const fullElement=()=>document.fullscreenElement||document.webkitFullscreenElement||null;
  const unlockOrientation=()=>{if(!orientationLocked)return;try{screen.orientation?.unlock?.();}catch(_){}orientationLocked=false;};
  const lockLandscape=async()=>{if(!matchMedia('(max-width: 1000px)').matches||!screen.orientation?.lock)return;try{await screen.orientation.lock('landscape');orientationLocked=true;}catch(_){}};
  const leaveFullscreen=async()=>{
    try{
      if(pseudoFullscreen){shell?.classList.remove('media-video-shell--pseudo-fullscreen');document.documentElement.classList.remove('media-player-pseudo-fullscreen');pseudoFullscreen=false;}
      else if(document.exitFullscreen)await document.exitFullscreen();
      else if(document.webkitExitFullscreen)document.webkitExitFullscreen();
    }catch(_){}finally{unlockOrientation();}
  };
  const enterFullscreen=async()=>{
    const target=shell||media;
    try{
      if(target.requestFullscreen)await target.requestFullscreen({navigationUI:'hide'});
      else if(target.webkitRequestFullscreen)target.webkitRequestFullscreen();
      else {target.classList.add('media-video-shell--pseudo-fullscreen');document.documentElement.classList.add('media-player-pseudo-fullscreen');pseudoFullscreen=true;}
      await lockLandscape();
    }catch(error){toast('Schermo intero non disponibile in questo browser.',true);}
  };
  const toggleFullscreen=()=>{if(fullElement()||pseudoFullscreen)leaveFullscreen();else enterFullscreen();};
  const onFullscreenChange=()=>{if(!fullElement()&&!pseudoFullscreen)unlockOrientation();};
  input.addEventListener('input',preview); input.addEventListener('change',commit); play?.addEventListener('click',togglePlay); mute?.addEventListener('click',toggleMute); fullscreen?.addEventListener('click',toggleFullscreen); media.addEventListener('play',syncPlay);media.addEventListener('pause',syncPlay);media.addEventListener('volumechange',syncMute);document.addEventListener('fullscreenchange',onFullscreenChange);document.addEventListener('webkitfullscreenchange',onFullscreenChange);syncPlay();syncMute();
  return{update(position){if(dragging||busy)return;const value=Math.max(0,Math.min(max,Number(position)||0));input.value=String(Math.floor(value));current.textContent=mediaDuration(value);},destroy(){input.removeEventListener('input',preview);input.removeEventListener('change',commit);play?.removeEventListener('click',togglePlay);mute?.removeEventListener('click',toggleMute);fullscreen?.removeEventListener('click',toggleFullscreen);media.removeEventListener('play',syncPlay);media.removeEventListener('pause',syncPlay);media.removeEventListener('volumechange',syncMute);document.removeEventListener('fullscreenchange',onFullscreenChange);document.removeEventListener('webkitfullscreenchange',onFullscreenChange);if(pseudoFullscreen){shell?.classList.remove('media-video-shell--pseudo-fullscreen');document.documentElement.classList.remove('media-player-pseudo-fullscreen');}unlockOrientation();}};
}
function renderMediaTrackControls(plan, selectedAudio, selectedSubtitle, onAudio, onSubtitle){
  const host=$('#mediaPlayerTracks'), audios=plan.audio_tracks||[], subtitles=plan.subtitles||[]; const parts=[];
  if(audios.length>1)parts.push(`<label class="media-player-track"><span>LINGUA / AUDIO</span><select id="mediaPlayerAudio">${audios.map(track=>`<option value="${track.stream_index}" ${Number(track.stream_index)===Number(selectedAudio)?'selected':''}>${escapeHtml(mediaAudioLabel(track))}</option>`).join('')}</select></label>`);
  if(subtitles.length)parts.push(`<label class="media-player-track"><span>SOTTOTITOLI</span><select id="mediaPlayerSubtitle"><option value="off" ${selectedSubtitle==='off'?'selected':''}>Disattivati</option>${subtitles.map(track=>{const key=mediaSubtitleKey(track);return `<option value="${escapeHtml(key)}" ${key===selectedSubtitle?'selected':''}>${escapeHtml(track.label||'Sottotitoli')}</option>`}).join('')}</select></label>`);
  host.innerHTML=parts.join(''); host.hidden=!parts.length;
  $('#mediaPlayerAudio')?.addEventListener('change',e=>onAudio(Number(e.target.value))); $('#mediaPlayerSubtitle')?.addEventListener('change',e=>onSubtitle(e.target.value));
}
let mediaProfile = {continue:[], recently_played:[], favorites:[], active_streams:[], stats:{known_files:0,known_bytes:0}};
let mediaFavoriteKeys = new Set();
let mediaHomeLoadedAt = 0;
function isMediaFavorite(path, uuid = mediaUuid) { return mediaFavoriteKeys.has(`${uuid}:${path}`); }
async function toggleMediaFavorite(path, name = '', uuid = mediaUuid) {
  if (!uuid || !path) return false;
  const key = `${uuid}:${path}`, favorite = !mediaFavoriteKeys.has(key);
  try {
    const response = await fetch('/api/media/favorite', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({uuid,path,favorite}), signal:AbortSignal.timeout(10000)});
    if (response.status === 401) { showLogin(); return !favorite; }
    const result = await response.json(); if (!response.ok || !result.ok) throw new Error(result.error || 'Errore preferiti');
    if (favorite) mediaFavoriteKeys.add(key); else mediaFavoriteKeys.delete(key);
    await loadMediaHome(true); renderMediaFiles();
    return favorite;
  } catch (error) { toast(error.message || 'Preferito non salvato.', true); return !favorite; }
}
function mediaResumePosition(uuid, path) {
  const rows = [...(mediaProfile.continue||[]), ...(mediaProfile.recently_played||[])];
  const row = rows.find(item => item.uuid === uuid && item.path === path);
  return Number(row?.position || 0);
}
async function saveMediaProgress(media, force = false) {
  if (!media || !mediaPlayerUuid || !mediaPlayerPath || !Number.isFinite(media.currentTime)) return;
  const position=mediaPlaybackBase + media.currentTime; const duration=mediaPlaybackDuration || media.duration || 0;
  if (!duration || (!force && position < 2)) return;
  try { await fetch('/api/media/progress',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({uuid:mediaPlayerUuid,path:mediaPlayerPath,position,duration}),signal:AbortSignal.timeout(8000)}); } catch (_) {}
}
function mediaProfileCard(item, kind='history') {
  const available = item.available !== false;
  const thumb = available && ['video','image'].includes(item.category) ? `<img loading="lazy" src="${escapeHtml(mediaThumbUrl(item.path,item.uuid))}" alt="">` : `<span>${available ? mediaCategoryLabel(item.category) : 'OFFLINE'}</span>`;
  const progress = Number(item.duration) > 0 ? Math.max(0,Math.min(100,Number(item.position||0)/Number(item.duration)*100)) : 0;
  return `<button class="media-profile-card ${available?'':'offline'}" data-profile-play="${escapeHtml(item.path)}" data-profile-uuid="${escapeHtml(item.uuid)}" data-profile-name="${escapeHtml(item.name)}" data-profile-category="${escapeHtml(item.category)}" ${available?'':'data-offline="1"'}><div class="media-profile-art">${thumb}${progress>0?`<i style="width:${progress.toFixed(1)}%"></i>`:''}</div><strong>${escapeHtml(item.name)}</strong><small>${available ? (progress>0 ? `${Math.round(progress)}% · ${mediaDuration(item.position)} / ${mediaDuration(item.duration)}` : mediaCategoryLabel(item.category)) : 'Supporto non collegato'}</small></button>`;
}
function bindMediaProfileCards() {
  $$('[data-profile-play]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.offline) return toast('Questo contenuto è ricordato, ma il supporto USB non è collegato.', true);
    mediaUuid = button.dataset.profileUuid; openMediaPlayer(button.dataset.profilePlay,button.dataset.profileName,button.dataset.profileCategory,button.dataset.profileUuid);
  }));
}
function renderMediaHome(profile = {}) {
  mediaProfile = profile;
  mediaFavoriteKeys = new Set((profile.favorites||[]).map(item => `${item.uuid}:${item.path}`));
  $('#mediaKnownFiles').textContent = profile.stats?.known_files || 0; $('#mediaKnownBytes').textContent = `${bytes(profile.stats?.known_bytes || 0)} indicizzati`;
  $('#mediaContinueCount').textContent = (profile.continue||[]).length; $('#mediaFavoriteCount').textContent = (profile.favorites||[]).length;
  $('#mediaStreamCount').textContent = (profile.active_streams||[]).length; $('#mediaStreamDetail').textContent = (profile.active_streams||[]).length ? 'riproduzione in corso' : 'nessuna riproduzione';
  const sections = [['#mediaContinueWrap','#mediaContinue',profile.continue||[],'continue'],['#mediaFavoritesWrap','#mediaFavorites',profile.favorites||[],'favorite'],['#mediaPlayedWrap','#mediaPlayed',profile.recently_played||[],'played']];
  sections.forEach(([wrap,host,rows,kind]) => { $(wrap).hidden=!rows.length; $(host).innerHTML=rows.map(item=>mediaProfileCard(item,kind)).join(''); });
  const streams=profile.active_streams||[]; $('#mediaStreamsWrap').hidden=!streams.length;
  $('#mediaStreams').innerHTML=streams.map(stream=>`<article><div><strong>${escapeHtml(stream.name||stream.path)}</strong><small>${escapeHtml(stream.client||'client')} · ${mediaDuration(stream.seconds||0)}</small></div><span>${bytes(stream.bytes_sent||0)}</span></article>`).join('');
  bindMediaProfileCards(); renderMediaFiles();
}
async function loadMediaHome(force=false) {
  if (!signedIn || (currentView !== 'media' && !force)) return;
  const now=Date.now(); if (!force && now-mediaHomeLoadedAt<10000) return; mediaHomeLoadedAt=now;
  try {
    const response=await fetch('/api/media/home',{cache:'no-store',signal:AbortSignal.timeout(12000)}); if(response.status===401)return showLogin();
    const result=await response.json(); if(response.ok&&result.ok) renderMediaHome(result);
  } catch (_) {}
}
function mediaCategoryLabel(category) { return ({video:'VIDEO',audio:'AUDIO',image:'FOTO',document:'DOC',archive:'ARCHIVIO',other:'FILE',dir:'CARTELLA'})[category] || 'FILE'; }
function mediaDate(timestamp) { return timestamp ? new Date(timestamp * 1000).toLocaleDateString('it-IT',{day:'2-digit',month:'short',year:'numeric'}) : '—'; }
function mediaDuration(value) {
  const seconds = Math.max(0, Number(value) || 0), h = Math.floor(seconds/3600), m = Math.floor((seconds%3600)/60), s = Math.floor(seconds%60);
  return h ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : `${m}:${String(s).padStart(2,'0')}`;
}
let mediaItems = [];
let mediaLibrary = null;
let mediaFilter = 'all';
let mediaView = 'grid';
let mediaSearchText = '';
let mediaSort = 'name';
let mediaPlayerPath = '';
let mediaPlayerName = '';
let mediaPlayerUuid = '';
let mediaResumeTimer = null;
let mediaSubtitleController = null;

function mediaFilteredItems() {
  let items = [...mediaItems];
  const query = mediaSearchText.trim().toLocaleLowerCase('it-IT');
  if (query) items = items.filter(item => item.name.toLocaleLowerCase('it-IT').includes(query));
  if (mediaFilter === 'favorite') items = items.filter(item => item.type === 'file' && isMediaFavorite(item.path));
  else if (mediaFilter !== 'all') items = items.filter(item => item.type === 'dir' || item.category === mediaFilter);
  if (mediaSort === 'recent') items.sort((a,b) => (a.type !== b.type ? (a.type === 'dir' ? -1 : 1) : (b.modified||0)-(a.modified||0)));
  else if (mediaSort === 'size') items.sort((a,b) => (a.type !== b.type ? (a.type === 'dir' ? -1 : 1) : (b.size||0)-(a.size||0)));
  else items.sort((a,b) => (a.type !== b.type ? (a.type === 'dir' ? -1 : 1) : a.name.localeCompare(b.name, 'it', {numeric:true})));
  return items;
}
function mediaCard(item) {
  if (item.type === 'dir') return `<button class="media-card media-dir" data-media-path="${escapeHtml(item.path)}"><div class="media-art folder-art"><span>DIR</span></div><div class="media-card-copy"><strong>${escapeHtml(item.name)}</strong><small>Cartella</small></div></button>`;
  const favorite = isMediaFavorite(item.path);
  const art = item.thumbnail ? `<div class="media-art has-thumb"><img loading="lazy" src="${escapeHtml(mediaThumbUrl(item.path))}" alt=""><span>${mediaCategoryLabel(item.category)}</span></div>` : `<div class="media-art type-art ${escapeHtml(item.category)}"><span>${mediaCategoryLabel(item.category)}</span></div>`;
  return `<article class="media-card" data-category="${escapeHtml(item.category)}">${art}<div class="media-card-copy"><strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong><small>${bytes(item.size)} · ${mediaDate(item.modified)}</small></div><div class="media-card-actions">${item.streamable ? `<button data-media-play="${escapeHtml(item.path)}" data-media-name="${escapeHtml(item.name)}" data-media-category="${escapeHtml(item.category)}">Riproduci</button>` : `<a href="${escapeHtml(mediaUrl(item.path))}" target="_blank" rel="noopener">Apri</a>`}<a href="${escapeHtml(mediaUrl(item.path,true))}">Download</a><button class="media-fav ${favorite?'active':''}" data-media-fav="${escapeHtml(item.path)}" data-media-name="${escapeHtml(item.name)}" title="Preferito">${favorite?'★':'☆'}</button></div></article>`;
}
function renderMediaFiles() {
  const host = $('#mediaFiles');
  if (!mediaUuid) { host.className = `media-files-v2 ${mediaView}`; host.innerHTML = '<div class="media-empty"><strong>Nessun supporto</strong><small>Collega un’unità USB per iniziare.</small></div>'; return; }
  const items = mediaFilteredItems();
  host.className = `media-files-v2 ${mediaView}`;
  host.innerHTML = items.length ? items.map(mediaCard).join('') : '<div class="media-empty"><strong>Nessun risultato</strong><small>Prova a cambiare ricerca o filtro.</small></div>';
  $$('.media-dir').forEach(button => button.addEventListener('click', () => loadMediaDirectory(button.dataset.mediaPath)));
  $$('[data-media-play]').forEach(button => button.addEventListener('click', () => openMediaPlayer(button.dataset.mediaPlay, button.dataset.mediaName, button.dataset.mediaCategory)));
  $$('[data-media-fav]').forEach(button => button.addEventListener('click', () => toggleMediaFavorite(button.dataset.mediaFav, button.dataset.mediaName)));
}
function renderMediaLibrary(library) {
  mediaLibrary = library;
  const counts = library?.counts || {}, sizes = library?.bytes || {};
  $('#mediaStatVideo').textContent = counts.video || 0; $('#mediaStatVideoSize').textContent = bytes(sizes.video || 0);
  $('#mediaStatAudio').textContent = counts.audio || 0; $('#mediaStatAudioSize').textContent = bytes(sizes.audio || 0);
  $('#mediaStatImage').textContent = counts.image || 0; $('#mediaStatImageSize').textContent = bytes(sizes.image || 0);
  $('#mediaStatTotal').textContent = library?.total_files || 0; $('#mediaStatTotalSize').textContent = bytes(library?.total_bytes || 0);
  const recent = library?.recent || [];
  $('#mediaRecentWrap').hidden = !recent.length;
  $('#mediaRecentCount').textContent = recent.length ? `${recent.length} elementi` : '';
  $('#mediaRecent').innerHTML = recent.slice(0,12).map(item => `<button class="media-recent-card" data-media-recent="${escapeHtml(item.path)}" data-media-name="${escapeHtml(item.name)}" data-media-category="${escapeHtml(item.category)}">${item.thumbnail ? `<img loading="lazy" src="${escapeHtml(mediaThumbUrl(item.path))}" alt="">` : `<span>${mediaCategoryLabel(item.category)}</span>`}<strong>${escapeHtml(item.name)}</strong><small>${mediaDate(item.modified)}</small></button>`).join('');
  $$('.media-recent-card').forEach(button => button.addEventListener('click', () => openMediaPlayer(button.dataset.mediaRecent, button.dataset.mediaName, button.dataset.mediaCategory)));
}
async function loadMediaLibrary(force = false) {
  if (!mediaUuid) return;
  try {
    const query = new URLSearchParams({uuid:mediaUuid}); if (force) query.set('force','1');
    const response = await fetch(`/api/media/library?${query}`, {cache:'no-store', signal:AbortSignal.timeout(30000)});
    if (response.status === 401) return showLogin();
    const result = await response.json();
    if (response.ok && result.ok) renderMediaLibrary(result);
  } catch (_) {}
}
async function loadMediaDirectory(path = mediaPath) {
  if (!mediaUuid || mediaBusy) return;
  mediaBusy = true;
  $('#mediaFiles').innerHTML = '<div class="media-empty"><strong>Caricamento…</strong><small>Lettura del supporto USB.</small></div>';
  try {
    const query = new URLSearchParams({uuid:mediaUuid, path:path || ''});
    const response = await fetch(`/api/media/list?${query}`, {cache:'no-store', signal:AbortSignal.timeout(15000)});
    if (response.status === 401) return showLogin();
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
    mediaPath = result.path || ''; mediaItems = result.items || [];
    $('#mediaDriveLabel').textContent = result.label || 'USB'; $('#mediaPath').textContent = `/${mediaPath}`;
    $('#mediaBack').disabled = !mediaPath; $('#mediaBack').dataset.parent = result.parent || '';
    renderMediaFiles();
  } catch (error) { $('#mediaFiles').innerHTML = `<div class="media-empty"><strong>Supporto non leggibile</strong><small>${escapeHtml(error.message || '')}</small></div>`; }
  finally { mediaBusy = false; }
}
function closeMediaPlayer() {
  const stage=$('#mediaPlayerStage'), media=stage.querySelector('video,audio'); if(media) saveMediaProgress(media,true);
  if(mediaResumeTimer)clearInterval(mediaResumeTimer); mediaResumeTimer=null; mediaSubtitleController?.destroy?.(); mediaSubtitleController=null; stopMediaHls(); stage.innerHTML='';
  mediaPlaybackBase=0; mediaPlaybackDuration=0; if($('#mediaPlayerDialog').open)$('#mediaPlayerDialog').close(); setTimeout(()=>loadMediaHome(true),500);
}
async function openMediaPlayer(path,name,category,uuid=mediaUuid,options={}){
  if(!uuid||!path)return; const device=(latestState?.media?.devices||[]).find(item=>item.uuid===uuid); if(!device?.mounted)return toast('Il supporto che contiene questo file non è collegato.',true);
  const requestedAudio=options.audioStream??null; let selectedSubtitle=options.subtitleKey||'off';
  await stopMediaHls(); mediaSubtitleController?.destroy?.(); mediaSubtitleController=null; mediaPlayerUuid=uuid; mediaPlayerPath=path; mediaPlayerName=name||path.split('/').at(-1); mediaPlaybackBase=0; mediaPlaybackDuration=0;
  $('#mediaPlayerTitle').textContent=mediaPlayerName; $('#mediaPlayerType').textContent=mediaCategoryLabel(category); $('#mediaPlayerPlan').textContent='ANALISI'; $('#mediaPlayerNote').textContent='Analisi compatibilità codec e carico del nodo…'; $('#mediaPlayerTracks').hidden=true; $('#mediaPlayerTracks').innerHTML='';
  $('#mediaPlayerDownload').href=mediaUrl(path,true,uuid); $('#mediaPlayerMeta').innerHTML='<span>Analisi file…</span>'; $('#mediaPlayerFavorite').textContent=isMediaFavorite(path,uuid)?'★ Preferito':'☆ Preferito';
  if(!$('#mediaPlayerDialog').open)$('#mediaPlayerDialog').showModal(); const stage=$('#mediaPlayerStage'); stage.innerHTML='<div class="media-playback-wait"><strong>Preparazione playback…</strong><small>OpenAstro sta scegliendo il percorso più efficiente.</small></div>';
  let plan={mode:'direct',available:true,audio_tracks:[],selected_audio_stream:null,subtitles:[],duration:0,reason:'Direct Play'};
  try{const r=await fetch(mediaPlanUrl(path,uuid,requestedAudio),{cache:'no-store',signal:AbortSignal.timeout(12000)});const x=await r.json();if(r.ok&&x.ok)plan=x;}catch(_){}
  let selectedAudio=plan.selected_audio_stream??requestedAudio; mediaPlaybackDuration=Number(plan.duration||0); const resume=Number.isFinite(Number(options.position))?Number(options.position):mediaResumePosition(uuid,path); const directUrl=mediaUrl(path,false,uuid); let media=null;
  const logicalPosition=()=>mediaPlaybackBase+(Number(media?.currentTime)||0);
  let fullSeek={update:()=>{},destroy:()=>{}};
  const bindProgress=()=>{ if(!media)return; if(mediaResumeTimer)clearInterval(mediaResumeTimer); media.addEventListener('pause',()=>saveMediaProgress(media,true)); media.addEventListener('ended',()=>saveMediaProgress(media,true)); mediaResumeTimer=setInterval(()=>{const pos=logicalPosition();fullSeek.update(pos);if(!media.paused)saveMediaProgress(media);},500); };
  const attachHls=manifest=>{if(window.Hls&&Hls.isSupported()){mediaHls=new Hls({maxBufferLength:60,backBufferLength:90,enableWorker:true});mediaHls.loadSource(manifest);mediaHls.attachMedia(media);}else if(media.canPlayType('application/vnd.apple.mpegurl')){media.src=manifest;}else throw new Error('Questo browser non supporta HLS/MSE');};
  const startHlsAt=async(position,audio=selectedAudio)=>{mediaSubtitleController?.clear?.();const oldToken=mediaHlsToken,oldHls=mediaHls;if(oldHls){try{oldHls.destroy();}catch(_){}}mediaHls=null;const payload={uuid,path,position};if(audio!==null&&audio!==undefined)payload.audio_stream=audio;const r=await fetch('/api/media/hls/start',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(payload),signal:AbortSignal.timeout(15000)});const h=await r.json();if(!r.ok||!h.ok){mediaHlsToken=oldToken;throw new Error(h.error||'HLS non disponibile');}mediaHlsToken=h.token||'';mediaPlaybackBase=Number(h.offset||0);mediaPlaybackDuration=Number(h.plan?.duration||plan.duration||0);selectedAudio=h.plan?.selected_audio_stream??audio;plan=h.plan||plan;attachHls(h.manifest);return h;};
  const localSeek=position=>{if(!media||!media.seekable)return false;const local=position-mediaPlaybackBase;if(local<0)return false;for(let i=0;i<media.seekable.length;i++){const start=media.seekable.start(i),end=media.seekable.end(i);if(local>=start-.25&&local<=end+.25){media.currentTime=Math.max(start,Math.min(end,local));fullSeek.update(position);return true;}}return false;};
  const restartAt=async position=>{if(localSeek(position))return;await startHlsAt(position,selectedAudio);fullSeek.update(position);};
  const bindFullSeek=()=>{fullSeek.destroy();fullSeek=renderMediaFullSeek(media,mediaPlaybackDuration,logicalPosition()||resume,restartAt);};
  const bindTrackControls=()=>renderMediaTrackControls(plan,selectedAudio,selectedSubtitle,async nextAudio=>{const pos=logicalPosition();await startHlsAt(pos,nextAudio);fullSeek.update(pos);},async key=>{selectedSubtitle=key;if(!mediaSubtitleController)return;try{await mediaSubtitleController.set(key);}catch(error){toast(error.message||'Sottotitoli non disponibili.',true);}});
  const originalVideo=()=>{ stage.innerHTML='<video controls playsinline preload="metadata"></video>'; media=stage.querySelector('video'); media.src=directUrl; addMediaSubtitleTracks(media,plan.subtitles,uuid,selectedSubtitle); media.addEventListener('loadedmetadata',()=>{if(resume>5&&resume<media.duration-10)media.currentTime=resume;},{once:true}); mediaPlaybackBase=0; if(!mediaPlaybackDuration)mediaPlaybackDuration=media.duration||0; bindProgress(); bindTrackControls(); };
  if(category==='image'){stage.innerHTML=`<img src="${escapeHtml(directUrl)}" alt="${escapeHtml(mediaPlayerName)}">`;$('#mediaPlayerPlan').textContent='DIRECT';$('#mediaPlayerNote').textContent='Immagine servita direttamente dal supporto USB.';}
  else if(category==='audio'){stage.innerHTML='<audio controls preload="metadata"></audio>';media=stage.querySelector('audio');media.src=directUrl;media.addEventListener('loadedmetadata',()=>{if(resume>5&&resume<media.duration-10)media.currentTime=resume;},{once:true});bindProgress();$('#mediaPlayerPlan').textContent='DIRECT';$('#mediaPlayerNote').textContent=plan.reason||'Audio Direct Play.';}
  else if(plan.mode==='direct'){originalVideo();$('#mediaPlayerPlan').textContent='DIRECT PLAY';$('#mediaPlayerNote').textContent=plan.reason||'Nessuna conversione: il file passa direttamente dalla USB al browser.';}
  else if(plan.available&&String(plan.mode).startsWith('hls_')){
    $('#mediaPlayerPlan').textContent=plan.mode==='hls_copy'?'REMUX HLS':plan.mode==='hls_audio'?'AUDIO → AAC':'TRANSCODE HLS'; $('#mediaPlayerNote').textContent=`${plan.reason}. Seek rapido sull'intera durata; il video resta in copia quando compatibile.`;
    try{stage.innerHTML=mediaHlsPlayerMarkup();media=stage.querySelector('video');mediaSubtitleController=createMediaSubtitleController(media,media.closest('.media-video-shell'),plan.subtitles,uuid,logicalPosition);await startHlsAt(resume,selectedAudio);if(selectedSubtitle!=='off')await mediaSubtitleController.set(selectedSubtitle);bindProgress();bindTrackControls();bindFullSeek();}catch(error){mediaSubtitleController?.destroy?.();mediaSubtitleController=null;stage.innerHTML=`<div class="media-playback-blocked"><strong>Fallback HLS non disponibile</strong><small>${escapeHtml(error.message||'Errore HLS')}</small><button id="mediaTryOriginal" class="button secondary">Prova comunque il file originale</button></div>`;$('#mediaTryOriginal').addEventListener('click',originalVideo);}
  }else{stage.innerHTML=`<div class="media-playback-blocked"><strong>Transcode protetto</strong><small>${escapeHtml(plan.reason||'Playback non disponibile')}</small><button id="mediaTryOriginal" class="button secondary">Prova Direct Play</button></div>`;$('#mediaTryOriginal').addEventListener('click',originalVideo);$('#mediaPlayerPlan').textContent='PROTECTED';$('#mediaPlayerNote').textContent=plan.reason||'OpenAstro evita di sottrarre risorse a LiveVault.';}
  try{const response=await fetch(mediaProbeUrl(path,uuid),{cache:'no-store',signal:AbortSignal.timeout(12000)});const data=await response.json();const probe=data.probe||{},format=probe.format||{},streams=probe.streams||[];const video=streams.find(x=>x.codec_type==='video'),audio=streams.find(x=>x.index===selectedAudio)||streams.find(x=>x.codec_type==='audio');const bits=[];if(format.duration)bits.push(mediaDuration(format.duration));if(video?.width)bits.push(`${video.width}×${video.height}`);if(video?.codec_name)bits.push(video.codec_name.toUpperCase());if(audio?.codec_name)bits.push(audio.codec_name.toUpperCase());if(format.bit_rate)bits.push(`${(Number(format.bit_rate)/1e6).toFixed(1)} Mb/s`);bits.push(bytes(data.size));$('#mediaPlayerMeta').innerHTML=bits.map(value=>`<span>${escapeHtml(value)}</span>`).join('');}catch(_){$('#mediaPlayerMeta').innerHTML=`<span>${bytes(mediaItems.find(i=>i.path===path)?.size||0)}</span>`;}
}
function renderMedia(media = {}) {
  const devices = media.devices || [], mounted = devices.filter(device => device.mounted);
  $('#mediaChip').textContent = mounted.length ? `${mounted.length} support${mounted.length === 1 ? 'o' : 'i'} online` : 'Nessun supporto';
  $('#mediaChip').className = `status-chip ${mounted.length ? 'good' : ''}`;
  $('#mediaSmbPath').textContent = media.smb_path || '\\\\OPENASTRO\\Media'; $('#mediaDlnaName').textContent = media.dlna_name || 'OpenAstro Media';
  $('#mediaSmbState').textContent = media.smb === 'active' ? 'ON' : 'OFF'; $('#mediaSmbState').className = media.smb === 'active' ? 'good' : 'bad';
  $('#mediaDlnaState').textContent = media.dlna === 'active' ? 'ON' : 'OFF'; $('#mediaDlnaState').className = media.dlna === 'active' ? 'good' : 'bad';
  $('#mediaDeviceList').innerHTML = devices.length ? devices.map(device => {
    const usage = device.usage, usedPct = usage?.total ? usage.used/usage.total*100 : 0;
    return `<div class="media-device ${device.uuid === mediaUuid ? 'selected' : ''} ${device.mounted?'':'offline'}"><button class="media-select" data-media-uuid="${escapeHtml(device.uuid)}" ${device.mounted?'':'disabled'}><strong>${escapeHtml(device.label || 'USB')}</strong><small>${escapeHtml(device.model || '')}</small><span>${usage ? `${bytes(usage.free)} liberi · ${escapeHtml(device.fstype.toUpperCase())}` : `${escapeHtml(device.fstype.toUpperCase())} · ricordato / offline`}</span><div class="mini-capacity"><i style="width:${usedPct.toFixed(1)}%"></i></div></button>${device.mounted ? `<button class="media-eject" data-media-eject="${escapeHtml(device.uuid)}" title="Espelli in sicurezza">EJECT</button>` : '<span class="media-offline-chip">OFFLINE</span>'}</div>`;
  }).join('') : '<div class="media-empty side"><strong>Nessuna USB</strong><small>Collega un supporto rimovibile.</small></div>';
  $$('.media-select').forEach(button => button.addEventListener('click', () => { mediaUuid = button.dataset.mediaUuid; mediaPath=''; mediaSignature=''; mediaLibrary=null; renderMedia(media); loadMediaDirectory(''); loadMediaLibrary(); }));
  $$('.media-eject').forEach(button => button.addEventListener('click', event => { event.stopPropagation(); openConfirm('media_eject',{uuid:button.dataset.mediaEject}); }));
  if (mediaUuid && !mounted.some(device => device.uuid === mediaUuid)) { mediaUuid=''; mediaPath=''; mediaItems=[]; mediaLibrary=null; }
  if (!mediaUuid && mounted.length) { mediaUuid=mounted[0].uuid; mediaPath=''; }
  const selected = mounted.find(device => device.uuid === mediaUuid);
  if (selected) {
    $('#mediaDriveModel').textContent = `${selected.model || 'USB STORAGE'} · ${String(selected.fstype||'').toUpperCase()} · READ-ONLY`;
    $('#mediaDriveName').textContent = selected.label || 'Media USB'; const usage=selected.usage;
    $('#mediaDriveMeta').textContent = `${bytes(usage?.used||0)} usati su ${bytes(usage?.total||selected.size||0)}`; $('#mediaDriveFree').textContent = bytes(usage?.free||0); $('#mediaDriveBar').style.width = `${usage?.total ? usage.used/usage.total*100 : 0}%`;
  } else { $('#mediaDriveModel').textContent='NESSUN SUPPORTO'; $('#mediaDriveName').textContent='Media USB'; $('#mediaDriveMeta').textContent='Inserisci un dispositivo USB rimovibile.'; $('#mediaDriveFree').textContent='—'; $('#mediaDriveBar').style.width='0%'; }
  const signature = mounted.map(device => `${device.uuid}:${device.usage?.used||0}`).join('|');
  if (currentView === 'media' && mediaUuid && signature !== mediaSignature) { mediaSignature=signature; loadMediaDirectory(mediaPath); loadMediaLibrary(); }
  if (!mounted.length) { mediaItems=[]; renderMediaFiles(); renderMediaLibrary(null); $('#mediaRecentWrap').hidden=true; }
}

function selectView(view, updateHash = false) {
  view = VALID_VIEWS.has(view) ? view : 'dashboard';
  const labels = {dashboard:'Dashboard',media:'Media',storage:'Storage',system:'Sistema',nina:'NINA',pihole:'Pi-hole',advanced:'Avanzate'};
  currentView = view;
  document.body.dataset.view = view;
  $$('[data-view-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.viewPanel === view));
  $$('[data-route]').forEach(link => {
    const active = link.dataset.route === view;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  });
  const mobileTitle = $('#mobileViewTitle'); if (mobileTitle) mobileTitle.textContent = labels[view] || 'OpenAstro';
  document.title = `${labels[view] || 'Control'} · OpenAstro`;
  if (updateHash && location.hash !== `#${view}`) history.pushState(null, '', `#${view}`);
  window.scrollTo({top:0, behavior:'auto'});
  if (view === 'system') refreshHistory();
  if (view === 'media') {
    loadMediaHome(true);
    if (mediaUuid) { loadMediaDirectory(mediaPath); loadMediaLibrary(); }
  }
}
function setHeroBar(id, textId, value, label, warn = false) {
  const el=$(id), text=$(textId); if(!el)return;
  const level=Math.max(6,Math.min(100,Number(value)||0));
  el.style.setProperty('--level',`${level}%`);
  el.classList.toggle('warn',Boolean(warn));
  if(text) text.textContent=label;
}

function dashboardStatusCard(id, state, detail, tone = '') {
  const strong = $(`#${id}`), card = strong?.closest('article');
  if (!strong || !card) return;
  strong.textContent = state;
  const small = card.querySelector('small'); if (small) small.textContent = detail;
  card.classList.remove('attention','bad'); if (tone) card.classList.add(tone);
}

function render(data) {
  latestState = data;
  document.body.classList.remove('stale','is-loading');
  csrf = data.csrf || csrf;
  const host = data.host;
  const storage = data.storage;
  const lv = data.livevault || {};
  const online = lv.ok === true;
  const handoffMode = lv.storage_handoff?.mode || (storage.data.mounted ? 'nvme' : 'buffer');
  const mediaOnline = (data.media?.devices || []).filter(item => item.mounted);
  const wattsNow = measuredWatts(data.power);
  setHeroBar('#heroCpuBar','#heroCpuText',host.cpu_percent,`${host.cpu_percent.toFixed(0)}%`,host.cpu_percent>=85);
  setHeroBar('#heroRamBar','#heroRamText',host.memory.percent,`${host.memory.percent.toFixed(0)}%`,host.memory.percent>=88);
  const tempLevel=host.temperature==null?0:Math.max(0,Math.min(100,(host.temperature-25)/55*100));
  setHeroBar('#heroTempBar','#heroTempText',tempLevel,host.temperature==null?'—':`${host.temperature.toFixed(0)}°`,host.temperature>=72);
  const diskLevel=storage.data?.mounted ? Number(storage.data.percent||0) : 0;
  setHeroBar('#heroDiskBar','#heroDiskText',diskLevel,storage.data?.mounted?`${diskLevel.toFixed(0)}%`:'OFF',diskLevel>=90);
  const storageArt=$('#dashStorageArt'); if(storageArt) storageArt.style.setProperty('--usage',`${diskLevel}%`);
  const powerLevel=Math.min(100,Math.max(5,(wattsNow||0)/15*100));
  const powerArt=$('#dashPowerArt'); if(powerArt) powerArt.style.setProperty('--level',`${powerLevel}%`);
  const liveRings=$('#liveRings'); if(liveRings){
    liveRings.style.setProperty('--cpu',`${Math.max(2,Math.min(100,host.cpu_percent||0))}%`);
    liveRings.style.setProperty('--ram',`${Math.max(2,Math.min(100,host.memory.percent||0))}%`);
    liveRings.style.setProperty('--thermal',`${Math.max(2,Math.min(100,tempLevel))}%`);
  }
  const recorderCount=Number(lv.worker?.active_recorders||0);
  const nodeCard=$('#overview'); if(nodeCard) nodeCard.classList.toggle('is-alert',!online || handoffMode === 'buffer' || !storage.data?.mounted);
  const uptimeHero=$('#heroUptimeValue'); if(uptimeHero) uptimeHero.textContent=duration(host.uptime);
  const recorderPill=$('#heroRecorderPill'); if(recorderPill){ recorderPill.textContent=`${recorderCount} REC`; recorderPill.classList.toggle('active',recorderCount>0); }
  dashboardStatusCard('dashLiveState', online ? 'Online' : 'Problema', online ? `${recorderCount} recorder attiv${recorderCount===1?'o':'i'}` : 'LiveVault non raggiungibile', online ? '' : 'bad');
  const storageState=handoffMode === 'buffer' ? 'Buffer' : (storage.data?.mounted ? bytes(storage.data.free || 0) : 'Offline');
  const storageDetail=handoffMode === 'buffer' ? `${bytes(storage.buffer?.used || 0)} usati su ${bytes(storage.buffer?.total || 0)}` : (storage.data?.mounted ? `liberi · NVMe ${diskLevel.toFixed(0)}% usato` : 'NVMe non montato');
  dashboardStatusCard('dashStorageMode', storageState, storageDetail, handoffMode === 'buffer' ? 'attention' : (!storage.data?.mounted ? 'bad' : ''));
  dashboardStatusCard('dashMediaState', mediaOnline.length ? `${mediaOnline.length} USB online` : 'Pronto', mediaOnline.length ? mediaOnline.map(item => item.label || 'USB').join(' · ') : 'Collega un supporto USB');
  dashboardStatusCard('dashPowerState', wattsNow != null ? `${wattsNow.toFixed(1)} W` : '—', wattsNow != null ? `${data.power.input_volts?.toFixed?.(2) || '—'} V · ${data.power.input_amps?.toFixed?.(3) || '—'} A` : 'Sensore ASIAIR non disponibile', wattsNow == null ? 'attention' : '');
  const mobileConnection = $('#mobileConnectionText'); if (mobileConnection) mobileConnection.textContent = online ? 'Online' : 'Attenzione';
  $('#connectionText').textContent = online ? 'Sistema operativo' : 'LiveVault non disponibile';
  $('.connection').className = `connection ${online ? 'online' : 'offline'}`;
  $('#heroTitle').textContent = online ? 'Operativo' : storage.data.mounted ? 'Attenzione' : 'Storage offline';
  $('#heroSubtitle').textContent = online ? `LiveVault ${lv.version || ''} · ${handoffMode === 'buffer' ? 'buffer interno' : 'NVMe'} · ${host.name}` : storage.data.mounted ? 'Il nodo è online, ma LiveVault non risponde.' : 'Il pannello resta attivo sul buffer interno.';
  $('#hostName').textContent = host.name;
  $('#uptime').textContent = `UP ${duration(host.uptime)}`;
  $('#lastUpdate').textContent = new Date(data.timestamp * 1000).toLocaleTimeString('it-IT', {hour:'2-digit',minute:'2-digit',second:'2-digit'});

  ring('#cpuRing', host.cpu_percent, `${host.cpu_percent.toFixed(0)}%`);
  $('#loadValue').textContent = `Load ${host.load[0]}`;
  $('#coreValue').textContent = `${host.cpu_count} core`;
  ring('#ramRing', host.memory.percent, `${host.memory.percent.toFixed(0)}%`);
  $('#ramUsed').textContent = bytes(host.memory.used);
  $('#ramTotal').textContent = `di ${bytes(host.memory.total)}`;
  const temp = host.temperature ?? 0;
  ring('#tempRing', Math.min(100, temp), host.temperature == null ? '—' : `${temp.toFixed(0)}°`);
  $('#tempState').textContent = host.temperature == null ? 'Sensore non disponibile' : temp > 75 ? 'Caldo' : temp > 62 ? 'Attenzione' : 'Nella norma';
  ring('#diskRing', storage.data.percent, storage.data.mounted ? `${storage.data.percent.toFixed(0)}%` : 'OFF');
  $('#diskFree').textContent = storage.data.mounted ? `${bytes(storage.data.free)} liberi` : 'Scollegato';
  $('#diskTotal').textContent = storage.data.mounted ? `di ${bytes(storage.data.total)}` : 'SERVER assente';
  const network = host.network || {};
  ring('#networkRing', Math.min(100, ((network.rx_rate || 0) + (network.tx_rate || 0)) / 125000), 'NET');
  $('#networkDown').textContent = `↓ ${bytes(network.rx_rate || 0)}/s`;
  $('#networkUp').textContent = `↑ ${bytes(network.tx_rate || 0)}/s`;
  const watts = measuredWatts(data.power);
  ring('#wattRing', watts == null ? 0 : Math.min(100, watts), watts != null ? `${watts.toFixed(1)}W` : '—');
  $('#wattValue').textContent = watts != null ? `${watts.toFixed(1)} watt DC` : 'Non disponibile';
  $('#wattDaily').textContent = watts != null ? `${data.power.input_volts.toFixed(2)} V · ${data.power.input_amps.toFixed(3)} A · ingresso ASIAIR` : 'Sensore ASIAIR non raggiungibile';
  $('#wattValue').title = 'Potenza DC dai sensori ASIAIR, inclusi i carichi collegati. Conversione del driver INDI; non calibrata con wattmetro esterno.';
  renderPower(data.power);
  renderMedia(data.media || {});
  renderPihole(data.pihole || {});
  if (currentView === 'media') loadMediaHome();

  const mounted = storage.data.mounted && storage.share.mounted;
  $('#storageChip').textContent = mounted ? 'Montato' : storage.data_present ? 'Rilevato · non montato' : 'Scollegato';
  $('#storageChip').className = `status-chip ${mounted ? 'good' : 'bad'}`;
  $('#dataUsage').textContent = storage.data.mounted ? `${bytes(storage.data.used)} / ${bytes(storage.data.total)}` : 'OFFLINE';
  $('#shareUsage').textContent = storage.share.mounted ? `${bytes(storage.share.used)} / ${bytes(storage.share.total)}` : 'OFFLINE';
  $('#dataBar').style.width = `${storage.data.percent || 0}%`;
  $('#shareBar').style.width = `${storage.share.percent || 0}%`;
  $('#storageNote').textContent = mounted ? 'Disco operativo. Prima di rimuoverlo usa sempre Espelli NVMe.' : storage.data_present ? 'Disco presente ma non montato: premi Rimonta.' : 'Puoi ricollegare l’NVMe: il ripristino sarà automatico.';
  $$('[data-action="eject_nvme"]').forEach(button => { button.disabled = !storage.data.mounted; });
  const navEject = $('.nav-eject span'); if (navEject) navEject.textContent = storage.data.mounted ? 'Espelli NVMe' : 'NVMe scollegato';
  const quickNvmeAction = $('#quickNvmeAction');
  if (quickNvmeAction) {
    const ejecting = Boolean(storage.data.mounted);
    quickNvmeAction.dataset.action = ejecting ? 'eject_nvme' : 'attach_nvme';
    quickNvmeAction.dataset.confirm = ejecting ? 'Tieni premuto per espellere in sicurezza' : 'Tieni premuto per rimontare il disco';
    quickNvmeAction.disabled = ejecting ? false : !storage.data_present;
    $('#quickNvmeTitle').textContent = ejecting ? 'Espelli' : 'Rimonta';
    $('#quickNvmeState').textContent = ejecting ? 'NVMe' : (storage.data_present ? 'NVMe' : 'Assente');
  }
  $$('[data-action="attach_nvme"]').forEach(button => { if (button !== quickNvmeAction) button.disabled = mounted || !storage.data_present; });
  $$('[data-action="backup_now"]').forEach(button => { button.disabled = !storage.share.mounted || !storage.data.mounted; });

  const services = [
    ['LiveVault', online, online ? `${lv.worker?.active_recorders ?? '—'} recorder attivi` : 'non raggiungibile', 'restart_livevault'],
    ['Docker', data.services.docker, data.services.docker, 'restart_docker'],
    ['Tailscale', data.services.tailscale, 'rete privata e HTTPS'],
    ['Backup', data.services.backup, data.services.backup],
  ];
  $('#serviceGrid').innerHTML = services.map(item => serviceCard(...item)).join('');
  $('#serviceCount').textContent = `${services.filter(item => item[1] === true || item[1] === 'active').length}/${services.length} attivi`;

  $('#interfaceGrid').innerHTML = data.interfaces.map(item => item.available ? `<a class="quick-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener"><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.detail)}</small></div><span>↗</span></a>` : `<div class="quick-link disabled"><div><strong>${escapeHtml(item.name)}</strong><small>Non disponibile</small></div></div>`).join('');
  $('#containerCount').textContent = `${data.containers.length} totali`;
  $('#containerList').innerHTML = data.containers.length ? data.containers.map(item => `<div class="container-row"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.image)}</small><span class="container-state ${item.state === 'running' ? '' : 'bad'}">${escapeHtml(item.status)}</span></div>`).join('') : '<p class="muted">Docker non disponibile.</p>';
  $('#actionLog').innerHTML = data.actions.length ? data.actions.map(line => {
    const [when = '', action = '', result = ''] = line.split('|').map(x => x.trim());
    return `<div class="log-row"><span>${escapeHtml(when)}</span><strong>${escapeHtml(action)}</strong><span>${escapeHtml(result)}</span></div>`;
  }).join('') : '<p class="muted">Nessuna azione manuale registrata.</p>';
  bindActionButtons();
}

async function refresh() {
  if (refreshBusy || document.hidden || !signedIn) return;
  refreshBusy = true;
  try {
    const response = await fetch('/api/state', {cache: 'no-store', signal: AbortSignal.timeout(20000)});
    if (response.status === 401) return showLogin();
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    hideLogin();
    render(await response.json());
  } catch (error) {
    document.body.classList.add('stale');
    $('.connection').className = 'connection offline';
    $('#connectionText').textContent = latestState ? 'Dati non aggiornati' : 'Connessione non disponibile';
  } finally { refreshBusy = false; }
}
function showLogin() {
  signedIn = false;
  csrf = '';
  stopHold();
  if ($('#confirmDialog').open) $('#confirmDialog').close();
  $('main').inert = true;
  const sidebar = $('.sidebar'); if (sidebar) sidebar.inert = true;
  const mobileNav = $('.mobile-nav'); if (mobileNav) mobileNav.inert = true;
  $('#loginGate').hidden = false;
  document.body.classList.add('locked');
}
function hideLogin() {
  signedIn = true;
  $('main').inert = false;
  const sidebar = $('.sidebar'); if (sidebar) sidebar.inert = false;
  const mobileNav = $('.mobile-nav'); if (mobileNav) mobileNav.inert = false;
  $('#loginGate').hidden = true;
  document.body.classList.remove('locked');
  $('#loginError').textContent = '';
}
function toast(message, error = false) {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast show ${error ? 'error' : ''}`;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.className = 'toast', 4500);
}
function openConfirm(action, payload = {}) {
  if (actionBusy) return toast('Un’operazione è già in corso.');
  stopHold();
  pendingAction = action;
  pendingPayload = payload;
  const [title, text] = actionLabels[action] || ['Conferma azione', action];
  $('#dialogTitle').textContent = title;
  $('#dialogText').textContent = text;
  $('#holdAction').style.setProperty('--hold', '0%');
  $('#confirmDialog').showModal();
}
async function executeAction(action, payload = {}) {
  if (actionBusy || !action) return;
  actionBusy = true;
  toast('Azione in corso…');
  try {
    const response = await fetch('/api/action', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({action, ...payload}), signal:AbortSignal.timeout(180000)});
    if (response.status === 401) return showLogin();
    const result = await response.json();
    toast(result.message || result.error || (result.ok ? 'Operazione completata.' : 'Operazione fallita.'), !result.ok);
    if (result.ok) powerDirty = false;
  } catch (error) {
    toast('Connessione interrotta: esito non confermato. Verifica lo stato prima di riprovare.', true);
  } finally {
    actionBusy = false;
    setTimeout(refresh, 2500);
  }
}
function bindActionButtons() {
  $$('[data-action]').forEach(button => {
    if (button.dataset.bound) return;
    button.dataset.bound = '1';
    button.addEventListener('click', () => {
      const action = button.dataset.action;
      if (!actionLabels[action]) return toast('Azione non riconosciuta.', true);
      if (button.dataset.confirm) openConfirm(action); else executeAction(action);
    });
  });
}
function startHold(event) {
  if (holdTimer || !pendingAction || !$('#confirmDialog').open) return;
  event.preventDefault();
  holdStart = performance.now();
  const tick = () => {
    const percent = Math.min(100, (performance.now() - holdStart) / 20);
    $('#holdAction').style.setProperty('--hold', `${percent}%`);
    if (percent >= 100) {
      clearInterval(holdTimer); holdTimer = null;
      const action = pendingAction; pendingAction = null;
      const payload = pendingPayload; pendingPayload = {};
      $('#confirmDialog').close();
      executeAction(action, payload);
    }
  };
  holdTimer = setInterval(tick, 35); tick();
}
function stopHold() {
  if (holdTimer) clearInterval(holdTimer);
  holdTimer = null;
  $('#holdAction').style.setProperty('--hold', '0%');
}

$$('[data-media-filter]').forEach(button => button.addEventListener('click', () => {
  mediaFilter = button.dataset.mediaFilter || 'all'; $$('[data-media-filter]').forEach(item => item.classList.toggle('active', item.dataset.mediaFilter === mediaFilter)); renderMediaFiles();
}));
$('#mediaSearch').addEventListener('input', event => { mediaSearchText = event.target.value || ''; renderMediaFiles(); });
$('#mediaSort').addEventListener('change', event => { mediaSort = event.target.value || 'name'; renderMediaFiles(); });
$('#mediaGridView').addEventListener('click', () => { mediaView='grid'; $('#mediaGridView').classList.add('active'); $('#mediaListView').classList.remove('active'); renderMediaFiles(); });
$('#mediaListView').addEventListener('click', () => { mediaView='list'; $('#mediaListView').classList.add('active'); $('#mediaGridView').classList.remove('active'); renderMediaFiles(); });
$('#mediaLibraryRefresh').addEventListener('click', () => loadMediaLibrary(true));
$('#mediaPlayerClose').addEventListener('click', closeMediaPlayer);
$('#mediaPlayerDialog').addEventListener('close', () => { const media=$('#mediaPlayerStage').querySelector('video,audio'); if(media) saveMediaProgress(media,true); if(mediaResumeTimer) clearInterval(mediaResumeTimer); mediaResumeTimer=null; mediaSubtitleController?.destroy?.(); mediaSubtitleController=null; stopMediaHls(); mediaPlaybackBase=0; mediaPlaybackDuration=0; $('#mediaPlayerStage').innerHTML=''; setTimeout(()=>loadMediaHome(true),500); });
$('#mediaPlayerFavorite').addEventListener('click', async () => { const state=await toggleMediaFavorite(mediaPlayerPath,mediaPlayerName,mediaPlayerUuid); $('#mediaPlayerFavorite').textContent=state?'★ Preferito':'☆ Preferito'; });
$('#mediaBack').addEventListener('click', () => loadMediaDirectory($('#mediaBack').dataset.parent || ''));
$('#mediaCredentials').addEventListener('click', async () => {
  try {
    const response = await fetch('/api/media/credentials', {cache:'no-store'});
    if (response.status === 401) return showLogin();
    const result = await response.json();
    const text = result.password ? `Utente: ${result.username} · Password: ${result.password}` : `Utente: ${result.username} · password non disponibile`;
    $('#mediaCredentialsText').textContent = text;
    if (result.password && navigator.clipboard?.writeText) navigator.clipboard.writeText(`${result.username}
${result.password}`).catch(() => {});
  } catch (_) { toast('Credenziali SMB non disponibili.', true); }
});
$('#piholeRefresh').addEventListener('click', refresh);
$('#piholeAddDevice').addEventListener('click', () => { $('#piholeDeviceSetup').hidden=false; $('#piholeDeviceName').focus(); });
$('#piholeSetupClose').addEventListener('click', () => { $('#piholeDeviceSetup').hidden=true; });
$('#piholeDetailClose').addEventListener('click', closeDnsDetail);
$('#piholeDeviceForm').addEventListener('submit', async event => { event.preventDefault(); const name=$('#piholeDeviceName').value.trim(); const platform=$('#piholeDevicePlatform').value; if(!name)return; const result=await postPihole('/api/pihole/devices/create',{name,platform}); if(!result)return; $('#piholeDeviceName').value=''; $('#piholeDeviceSetup').hidden=true; toast('Accesso dispositivo generato.'); await refresh(); if(result.device?.id)openDnsDetail(result.device.id); });
$('#dnsCopyHostname').addEventListener('click', () => copyField('#dnsDeviceHostname','Hostname DNS privato copiato.'));
$('#dnsCopyDoh').addEventListener('click', () => copyField('#dnsDeviceDoh','URL DoH copiato.'));
$('#dnsDeviceToggle').addEventListener('click', async () => { if(!selectedDnsDeviceId)return; const enabled=$('#dnsDeviceToggle').dataset.enabled==='1'; const result=await postPihole(enabled?'/api/pihole/devices/disable':'/api/pihole/devices/enable',{id:selectedDnsDeviceId}); if(result)toast(enabled?'Dispositivo bloccato.':'Dispositivo riattivato.'); });
$('#dnsDeviceRegenerate').addEventListener('click', async () => { if(!selectedDnsDeviceId||!window.confirm('Rigenerare l’accesso? La configurazione precedente smetterà subito di funzionare.'))return; const result=await postPihole('/api/pihole/devices/regenerate',{id:selectedDnsDeviceId}); if(result){selectedDnsConnection=result.connection||null;toast('Accesso rigenerato. Riconfigura solo questo dispositivo.');await refresh();openDnsDetail(selectedDnsDeviceId);} });
$('#dnsDeviceDelete').addEventListener('click', async () => { if(!selectedDnsDeviceId||!window.confirm('Eliminare questo dispositivo e revocarne definitivamente l’accesso?'))return; const id=selectedDnsDeviceId; const result=await postPihole('/api/pihole/devices/delete',{id}); if(result){closeDnsDetail();toast('Dispositivo eliminato.');} });
$('#piholeToggle').addEventListener('change', event => setPiholeState(Boolean(event.target.checked)));
$$('[data-pihole-endpoint]').forEach(button => button.addEventListener('click', async () => { const result=await postPihole(button.dataset.piholeEndpoint); if(result)toast(result.message||'Operazione completata.'); }));
$('#cancelAction').addEventListener('click', () => $('#confirmDialog').close());
$('#confirmDialog').addEventListener('close', () => { stopHold(); pendingAction = null; pendingPayload = {}; });
$('#confirmDialog').addEventListener('cancel', stopHold);
window.addEventListener('blur', stopHold);
$('#holdAction').addEventListener('keydown', event => { if ([' ', 'Enter'].includes(event.key) && !event.repeat) startHold(event); });
$('#holdAction').addEventListener('keyup', stopHold);
$('#holdAction').addEventListener('pointerdown', startHold);
$('#holdAction').addEventListener('pointerup', stopHold);
$('#holdAction').addEventListener('pointerleave', stopHold);
$('#holdAction').addEventListener('pointercancel', stopHold);
$('#powerSlider').addEventListener('input', event => previewPower(event.target.value));
$('#wifiKeep').addEventListener('change', () => {
  powerDirty = true;
  $('#wifiHint').textContent = $('#wifiKeep').checked ? 'Hotspot locale disponibile; Internet continua via Ethernet.' : 'Wi-Fi spento; pannello pubblico e Funnel continuano via Ethernet.';
});
$('#quickWifi').addEventListener('click', () => {
  const targetWifi = activeWifiOn ? 'off' : 'on';
  actionLabels.power_profile = [activeWifiOn ? 'Spegni hotspot Wi-Fi' : 'Attiva hotspot Wi-Fi', `Il profilo CPU ${activePowerKey.toUpperCase()} resterà invariato. ${activeWifiOn ? 'Il pannello pubblico continuerà a funzionare tramite Ethernet.' : 'La rete locale OpenAstro-AP verrà riattivata.'}`];
  openConfirm('power_profile', {profile:activePowerKey, wifi:targetWifi});
});
$('#applyPower').addEventListener('click', () => {
  const profile = powerProfiles[Number($('#powerSlider').value)] || powerProfiles[1];
  const wifi = $('#wifiKeep').checked ? 'on' : 'off';
  actionLabels.power_profile = [`Attiva ${profile.name}`, `${profile.description} CPU massima ${profile.mhz} MHz. Hotspot Wi-Fi: ${wifi === 'on' ? 'attivo' : 'spento'}.`];
  openConfirm('power_profile', {profile:profile.key, wifi});
});
$$('[data-range]').forEach(button => button.addEventListener('click', () => {
  historyRange = Number(button.dataset.range);
  $$('[data-range]').forEach(item => { item.classList.toggle('active', item === button); item.setAttribute('aria-pressed', String(item === button)); });
  refreshHistory();
}));
$('#loginForm').addEventListener('submit', async event => {
  event.preventDefault();
  $('#loginError').textContent = '';
  const submit = event.currentTarget.querySelector('button');
  submit.disabled = true;
  try {
    const response = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:$('#loginUsername').value, password:$('#loginPassword').value})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Accesso non riuscito.');
    $('#loginPassword').value = '';
    hideLogin();
    await refresh();
    if (currentView === 'system') await refreshHistory();
    if (currentView === 'media') await loadMediaHome(true);
  } catch (error) { $('#loginError').textContent = error.message; }
  submit.disabled = false;
});
$('#logoutButton').addEventListener('click', async () => { await fetch('/api/logout', {method:'POST'}); showLogin(); });
const resourceToggle=$('#resourceToggle');
if(resourceToggle) resourceToggle.addEventListener('click',()=>{ const details=$('#resourceDetails'); const open=details.classList.toggle('open'); resourceToggle.setAttribute('aria-expanded',String(open)); });
const mediaTechToggle=$('#mediaTechToggle');
if(mediaTechToggle) mediaTechToggle.addEventListener('click',()=>{ const media=$('#media'); const open=media.classList.toggle('media-tech-open'); mediaTechToggle.textContent=open?'Chiudi dettagli':'Dettagli'; mediaTechToggle.setAttribute('aria-expanded',String(open)); });
$$('[data-system-tab]').forEach(button=>button.addEventListener('click',()=>{
  const tab=button.dataset.systemTab; const grid=$('.system-grid'); if(!grid)return;
  grid.classList.toggle('system-tab-power',tab==='power'); grid.classList.toggle('system-tab-telemetry',tab==='telemetry');
  $$('[data-system-tab]').forEach(item=>item.classList.toggle('active',item===button));
  if(tab==='telemetry') refreshHistory();
}));
$$('[data-route]').forEach(link => link.addEventListener('click', event => { event.preventDefault(); selectView(link.dataset.route, true); }));
window.addEventListener('hashchange', () => selectView(location.hash.slice(1) || 'dashboard'));
document.addEventListener('visibilitychange', () => { if (document.hidden) stopHold(); else { refresh(); if (currentView === 'system') refreshHistory(); if (currentView === 'media') loadMediaHome(true); } });
$('#refreshAll').addEventListener('click', () => { refresh(); refreshHistory(); });
$('#exportMetrics').addEventListener('click', () => {
  if (!latestHistory.length) return toast('Nessun campione da esportare.', true);
  const keys = ['t', 'cpu', 'ram', 'temp', 'disk', 'rx', 'tx', 'watts', 'power_measurement', 'input_volts', 'input_amps', 'estimated_watts'];
  const csv = ['timestamp_utc,cpu_percent,ram_percent,temperature_c,disk_percent,download_bytes_s,upload_bytes_s,measured_dc_watts,power_measurement,input_volts,input_amps,estimated_watts', ...latestHistory.map(row => keys.map(key => key === 't' ? new Date(row.t * 1000).toISOString() : row[key] ?? '').join(','))].join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = 'openastro-telemetria.csv'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
bindActionButtons();
selectView(currentView);
refresh();
if (currentView === 'system') refreshHistory();
if (currentView === 'media') loadMediaHome(true);
setInterval(refresh, 5000);
setInterval(() => { if (currentView === 'system') refreshHistory(); }, 30000);
setInterval(() => { if (currentView === 'media') loadMediaHome(); }, 10000);
