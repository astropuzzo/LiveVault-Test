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
let latestState = null;
let latestHistory = [];

const powerProfiles = [
  {key:'eco', name:'ECO', glyph:'E', governor:'powersave', mhz:900, description:'Il nodo respira piano: consumi e temperatura ridotti per monitoraggio e servizi leggeri.'},
  {key:'balanced', name:'Bilanciato', glyph:'B', governor:'schedutil', mhz:1200, description:'Il punto dolce: consumi contenuti e accelerazione immediata quando LiveVault ne ha bisogno.'},
  {key:'performance', name:'Performance', glyph:'P', governor:'schedutil', mhz:1500, description:'Risposta rapida con frequenza dinamica completa, ideale per più stream simultanei.'},
  {key:'max', name:'MAX', glyph:'M', governor:'performance', mhz:1500, description:'Massima reattività costante entro i limiti ufficiali del Compute Module 4.'},
];

const actionLabels = {
  eject_nvme: ['Espulsione sicura NVMe', 'Le registrazioni verranno chiuse, Docker sarà arrestato e le due partizioni saranno smontate. Scollega il cavo soltanto dopo il messaggio finale.'],
  attach_nvme: ['Rimonta NVMe', 'Forza il mount delle partizioni e riavvia Docker, Coolify, LiveVault e i backup.'],
  restart_livevault: ['Riavvia LiveVault', 'La registrazione corrente verrà chiusa correttamente e il recorder ripartirà.'],
  restart_docker: ['Riavvia Docker', 'Tutti i container, incluso Coolify, saranno indisponibili per alcuni secondi.'],
  backup_now: ['Avvia backup', 'Crea subito una copia consistente del database LiveVault sulla partizione USB SHARE.'],
  restart_pihole: ['Riavvia Pi-hole', 'Il DNS locale sarà indisponibile per alcuni secondi.'],
  power_profile: ['Applica profilo energetico', 'La frequenza CPU e le opzioni di rete verranno aggiornate immediatamente.'],
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
function renderHistory(points, energy = {}) {
  latestHistory = points;
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
  $('#energyMeasured').textContent = energy.wh != null ? `${energy.wh.toFixed(2)} Wh · ${duration(energy.covered_seconds)} coperti` : 'In attesa di campioni misurati';
}
async function refreshHistory() {
  if (historyBusy || document.hidden || !signedIn) return;
  historyBusy = true;
  const requestedRange = historyRange;
  try {
    const response = await fetch(`/api/history?range=${requestedRange}`, {cache:'no-store', signal: AbortSignal.timeout(15000)});
    if (response.status === 401) return showLogin();
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    if (requestedRange === historyRange) renderHistory(result.points || [], result.energy || {});
    $('#historyStatus').textContent = `Campioni ogni 10 s · ${Intl.DateTimeFormat().resolvedOptions().timeZone}`;
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

function render(data) {
  latestState = data;
  document.body.classList.remove('stale');
  csrf = data.csrf || csrf;
  const host = data.host;
  const storage = data.storage;
  const lv = data.livevault || {};
  const online = lv.ok === true;
  $('#connectionText').textContent = online ? 'Sistema operativo' : 'LiveVault non disponibile';
  $('.connection').className = `connection ${online ? 'online' : 'offline'}`;
  $('#heroTitle').textContent = online ? 'Il tuo nodo, in diretta.' : storage.data.mounted ? 'LiveVault non risponde.' : 'NVMe scollegato.';
  $('#heroSubtitle').textContent = online ? `LiveVault ${lv.version || ''} è operativo e raggiungibile.` : storage.data.mounted ? 'Il server è online, ma LiveVault non risponde.' : 'Il pannello resta attivo. Ricollega il disco per ripristinare i servizi.';
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

  const mounted = storage.data.mounted && storage.share.mounted;
  $('#storageChip').textContent = mounted ? 'Montato' : storage.data_present ? 'Rilevato · non montato' : 'Scollegato';
  $('#storageChip').className = `status-chip ${mounted ? 'good' : 'bad'}`;
  $('#dataUsage').textContent = storage.data.mounted ? `${bytes(storage.data.used)} / ${bytes(storage.data.total)}` : 'OFFLINE';
  $('#shareUsage').textContent = storage.share.mounted ? `${bytes(storage.share.used)} / ${bytes(storage.share.total)}` : 'OFFLINE';
  $('#dataBar').style.width = `${storage.data.percent || 0}%`;
  $('#shareBar').style.width = `${storage.share.percent || 0}%`;
  $('#storageNote').textContent = mounted ? 'Disco operativo. Prima di rimuoverlo usa sempre Espelli NVMe.' : storage.data_present ? 'Disco presente ma non montato: premi Rimonta.' : 'Puoi ricollegare l’NVMe: il ripristino sarà automatico.';
  $$('[data-action="eject_nvme"]').forEach(button => { button.disabled = !storage.data.mounted; });
  $('.nav-eject span').textContent = storage.data.mounted ? 'Espelli NVMe' : 'NVMe scollegato';
  $('#quickNvmeTitle').textContent = storage.data.mounted ? 'Espelli NVMe' : 'NVMe scollegato';
  $('#quickNvmeState').textContent = storage.data.mounted ? 'Smontaggio sicuro' : 'Ricollega il supporto';
  $('[data-action="attach_nvme"]').disabled = mounted;
  $('[data-action="backup_now"]').disabled = !storage.share.mounted || !storage.data.mounted;

  const services = [
    ['LiveVault', online, online ? `${lv.worker?.active_recorders ?? '—'} recorder attivi` : 'non raggiungibile', 'restart_livevault'],
    ['Docker', data.services.docker, data.services.docker, 'restart_docker'],
    ['Tailscale', data.services.tailscale, 'rete privata e HTTPS'],
    ['Backup', data.services.backup, data.services.backup],
    ['Pi-hole', data.services.pihole, data.services.pihole === 'active' ? 'DNS attivo' : 'non installato', 'restart_pihole'],
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
  $('.topbar').inert = true;
  $('.bottom-nav').inert = true;
  $('#loginGate').hidden = false;
  document.body.classList.add('locked');
}
function hideLogin() {
  signedIn = true;
  $('main').inert = false;
  $('.topbar').inert = false;
  $('.bottom-nav').inert = false;
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
    await refreshHistory();
  } catch (error) { $('#loginError').textContent = error.message; }
  submit.disabled = false;
});
$('#logoutButton').addEventListener('click', async () => { await fetch('/api/logout', {method:'POST'}); showLogin(); });
const sections = [...document.querySelectorAll('main > section[id]')];
const navObserver = new IntersectionObserver(entries => entries.forEach(entry => {
  if (entry.isIntersecting) $$('.bottom-nav a').forEach(link => link.classList.toggle('active', link.hash === `#${entry.target.id}`));
}), {rootMargin:'-25% 0px -65%'});
sections.forEach(section => navObserver.observe(section));
document.addEventListener('visibilitychange', () => { if (document.hidden) stopHold(); else { refresh(); refreshHistory(); } });
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
refresh();
refreshHistory();
setInterval(refresh, 5000);
setInterval(refreshHistory, 30000);
