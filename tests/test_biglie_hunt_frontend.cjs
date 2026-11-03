const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('control-panel/static/biglie-hunt.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup() {
  const pending = [], nodes = new Map(), listeners = {}, toasts = [], timers = [];
  function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {textContent:'', hidden:false, disabled:false, className:'', scrollTop:0, scrollHeight:0, clientHeight:0,
      setAttribute(name, value) { this[`@${name}`] = value; }, addEventListener(name, fn) { this[name] = fn; }});
    return nodes.get(selector);
  }
  const document = {hidden:false, querySelector:node, addEventListener:(name, fn) => { listeners[name] = fn; }};
  let logins = 0;
  const ctx = vm.createContext({document, AbortController, AbortSignal, console, Date, URL, csrf:'csrf-1',
    fetch:(url, options = {}) => new Promise((resolve, reject) => pending.push({url, options, resolve, reject})),
    toast:(message, error) => toasts.push([message, Boolean(error)]),
    showLogin:() => { logins++; ctx.OpenAstroBiglie.setAuthenticated(false); },
    setTimeout:(fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearTimeout:() => {}, setInterval:() => 0});
  vm.runInContext(source, ctx);
  return {ctx, node, pending, listeners, document, toasts, timers, logins:() => logins};
}
const reply = (data, status = 200) => ({ok:status === 200, status, json:async () => data});
const searching = {ok:true, installed:true, checked:1762170120, alive:true, state:'searching', round:4, updated:1762170118, machine:null,
  error:null, paused:false, log:['11:40:28 round 3: no room', '11:41:58 round 4: no room'], log_ok:true};

async function shown(data) {
  const s = setup();
  s.ctx.OpenAstroBiglie.setView('apps');
  s.pending[0].resolve(reply(data)); await tick();
  return s;
}

test('the hunt is read only while the App view is on screen', async () => {
  const s = setup();
  await s.ctx.OpenAstroBiglie.refresh(); assert.equal(s.pending.length, 0);
  s.document.hidden = true; s.ctx.OpenAstroBiglie.setView('apps'); assert.equal(s.pending.length, 0);
  s.document.hidden = false; s.listeners.visibilitychange();
  assert.equal(s.pending.length, 1); assert.equal(s.pending[0].url, '/api/biglie/hunt');
  const signal = s.pending[0].options.signal;
  s.ctx.OpenAstroBiglie.setView('media'); assert.equal(signal.aborted, true);
  s.pending[0].resolve(reply(searching)); await tick();
  assert.equal(s.node('#biglieHuntRound').textContent, '');
});

test('a searching hunt shows its round, its log and only the controls that apply', async () => {
  const s = await shown(searching);
  assert.equal(s.node('#biglieHuntChip').textContent, 'In cerca');
  assert.match(s.node('#biglieHuntSummary').textContent, /^Oracle non ha ancora posto/);
  assert.equal(s.node('#biglieHuntProcess').textContent, 'Attivo');
  assert.equal(s.node('#biglieHuntRound').textContent, '4');
  assert.equal(s.node('#biglieHuntMachine').textContent, 'Non ancora');
  assert.equal(s.node('#biglieHuntLog').textContent, '11:40:28 round 3: no room\n11:41:58 round 4: no room');
  assert.equal(s.node('#biglieHuntStart').disabled, true);
  assert.equal(s.node('#biglieHuntStop').disabled, false);
  assert.equal(s.node('#biglieHuntRestart').disabled, false);
  assert.equal(s.node('#biglieHuntPause').textContent, 'Pausa');
  assert.ok(!source.includes('innerHTML'), 'log lines and errors are written as text only');
});

test('the machine, a stop, a crash and a first run each read as such', async () => {
  const s = await shown({...searching, state:'growing', machine:{name:'biglie-regia', ocpus:2, gb:12, ip:'141.144.200.7'}});
  assert.equal(s.node('#biglieHuntMachine').textContent, '2 OCPU · 12 GB');
  assert.equal(s.node('#biglieHuntIp').textContent, '141.144.200.7');
  assert.equal(s.node('#biglieHuntChip').className, 'status-chip good');
  const describe = s.ctx.OpenAstroBiglie.describe;
  assert.deepEqual(JSON.parse(JSON.stringify(describe({...searching, alive:false, state:'stopped'})[0])), ['Ferma', '']);
  assert.deepEqual(JSON.parse(JSON.stringify(describe({...searching, alive:false, state:'searching'})[0])), ['Processo fermo', 'bad']);
  assert.match(describe({...searching, alive:false, state:'searching'})[1], /ultimo stato: in cerca di posto/);
  assert.equal(describe({...searching, alive:false, state:null})[1], 'La ricerca non è mai partita su questo nodo. Premi «Avvia ricerca».');
  assert.equal(describe({...searching, state:'error', error:'ServiceError: NotAuthenticated'})[1], 'Ultimo errore: ServiceError: NotAuthenticated. La ricerca riprova da sola.');
  assert.equal(describe({...searching, paused:true})[0][0], 'Pausa richiesta');
  const stopped = await shown({...searching, alive:false, state:'stopped'});
  assert.equal(stopped.node('#biglieHuntStart').disabled, false);
  assert.equal(stopped.node('#biglieHuntStop').disabled, true);
  assert.equal(stopped.node('#biglieHuntRestart').disabled, true);
});

test('actions send the CSRF token, lock the controls and read the hunt again', async () => {
  const s = await shown({...searching, alive:false, state:'stopped'});
  s.node('#biglieHuntStart').click();
  const post = s.pending[1];
  assert.equal(post.url, '/api/biglie/hunt'); assert.equal(post.options.method, 'POST');
  assert.equal(post.options.headers['X-CSRF-Token'], 'csrf-1'); assert.equal(post.options.body, '{"action":"start"}');
  assert.equal(s.node('#biglieHuntStart').disabled, true); assert.equal(s.node('#biglieHuntPause').disabled, true);
  post.resolve(reply({ok:true, message:'Ricerca avviata.'})); await tick();
  assert.deepEqual(s.toasts.at(-1), ['Ricerca avviata.', false]);
  assert.equal(s.pending.length, 3); assert.equal(s.pending[2].url, '/api/biglie/hunt');
});

test('the pause button follows the pause flag', async () => {
  const s = await shown({...searching, state:'paused', paused:true});
  assert.equal(s.node('#biglieHuntPause').textContent, 'Riprendi');
  s.node('#biglieHuntPause').click();
  assert.equal(s.pending[1].options.body, '{"action":"resume"}');
  s.pending[1].resolve(reply({ok:false, error:'Comando non riuscito.'}, 500)); await tick();
  assert.deepEqual(s.toasts.at(-1), ['Comando non riuscito.', true]);
});

test('the wait between rounds shows the chosen one and sends a new choice', async () => {
  const wait = seconds => `#biglieHuntInterval [data-seconds="${seconds}"]`;
  const s = await shown({...searching, interval:30});
  assert.equal(s.node(wait(30)).className, 'active'); assert.equal(s.node(wait(30))['@aria-pressed'], 'true');
  assert.equal(s.node(wait(15)).className, ''); assert.equal(s.node(wait(15))['@aria-pressed'], 'false');
  assert.equal(s.node(wait(90)).disabled, false);
  s.node(wait(30)).click(); assert.equal(s.pending.length, 1);
  s.node(wait(15)).click();
  assert.equal(s.pending[1].options.body, '{"action":"interval","seconds":15}');
  assert.equal(s.node(wait(60)).disabled, true);
  s.pending[1].resolve(reply({ok:true, message:'Ora la ricerca riprova ogni 15 secondi.'})); await tick();
  assert.deepEqual(s.toasts.at(-1), ['Ora la ricerca riprova ogni 15 secondi.', false]);
  const old = await shown(searching);
  for (const seconds of [15, 30, 60, 90]) assert.equal(old.node(wait(seconds)).disabled, true);
  assert.match(old.node('#biglieHuntIntervalNote').textContent, /va aggiornato/);
});

test('a missing folder disables every control and says why', async () => {
  const s = await shown({ok:true, installed:false, checked:1762170120});
  assert.equal(s.node('#biglieHuntChip').textContent, 'Non installata');
  assert.match(s.node('#biglieHuntSummary').textContent, /\/home\/astro\/biglie-oci/);
  for (const id of ['#biglieHuntPause', '#biglieHuntStop', '#biglieHuntRestart', '#biglieHuntStart']) assert.equal(s.node(id).disabled, true);
});

test('Oracle throttling is an automatic wait, not a panel timeout', async () => {
  const s = await shown({...searching, state:'error', reason:'rate_limited', http_status:429,
    retry_seconds:180, next_retry_at:1762170300, error:'HTTP 429 TooManyRequests'});
  assert.equal(s.node('#biglieHuntChip').textContent, 'In attesa');
  assert.match(s.node('#biglieHuntSummary').textContent, /Oracle chiede di rallentare \(HTTP 429\)/);
  assert.match(s.node('#biglieHuntSummary').textContent, /Prossimo tentativo previsto alle/);
  assert.equal(s.node('#biglieHuntProcess').textContent, 'Attivo');
  const old = s.ctx.OpenAstroBiglie.describe({...searching, state:'error',
    error:'HTTP 429 TooManyRequests: Too many requests for the user. Nuovo tentativo tra 180 s.'});
  assert.equal(old[0][0], 'In attesa');
  assert.match(old[1], /Prossimo tentativo previsto alle/);
});

test('a read timeout preserves the last data, disables commands and recovers', async () => {
  const s = await shown(searching);
  const reading = s.ctx.OpenAstroBiglie.refresh();
  assert.equal(s.timers.at(-1).ms, 20000);
  s.timers.at(-1).fn();
  s.pending[1].reject(Object.assign(new Error('aborted'), {name:'AbortError'}));
  await reading;
  assert.equal(s.node('#biglieHuntChip').textContent, 'Lettura in ritardo');
  assert.equal(s.node('#biglieHuntRound').textContent, '4');
  assert.match(s.node('#biglieHuntSummary').textContent, /nessuna risposta in 20 secondi/);
  assert.match(s.node('#biglieHuntSummary').textContent, /non indica un arresto/);
  assert.equal(s.node('#biglieHuntStop').disabled, true);
  assert.match(s.node('#biglieHuntLog').textContent, /round 4/);
  const recovered = s.ctx.OpenAstroBiglie.refresh();
  s.pending[2].resolve(reply({...searching, round:5})); await recovered;
  assert.equal(s.node('#biglieHuntChip').textContent, 'In cerca');
  assert.equal(s.node('#biglieHuntRound').textContent, '5');
  assert.equal(s.node('#biglieHuntStop').disabled, false);
});

test('the service worker shows a pushed notice and opens the panel on tap', async () => {
  const handlers = {}, shown = [], opened = [];
  const self = {addEventListener:(name, fn) => { handlers[name] = fn; }, location:{origin:'https://panel.example'}, skipWaiting:() => {},
    registration:{scope:'https://panel.example/', showNotification:async (title, options) => { shown.push([title, options]); }},
    clients:{matchAll:async () => [], openWindow:async url => { opened.push(url); }, claim:async () => {}}};
  vm.runInContext(fs.readFileSync('control-panel/static/sw.js', 'utf8'), vm.createContext({self, URL}));
  let waited;
  handlers.push({data:{json:() => ({title:'Biglie: trovata la macchina Arm', body:'4 OCPU e 24 GB. IP 203.0.113.24.', url:'/#apps'})}, waitUntil:promise => { waited = promise; }});
  await waited;
  assert.deepEqual(JSON.parse(JSON.stringify(shown)), [['Biglie: trovata la macchina Arm', {body:'4 OCPU e 24 GB. IP 203.0.113.24.', data:{url:'/#apps'}, lang:'it'}]]);
  let closed = false;
  handlers.notificationclick({notification:{close:() => { closed = true; }, data:{url:'/#apps'}}, waitUntil:promise => { waited = promise; }});
  await waited;
  assert.equal(closed, true); assert.deepEqual(opened, ['https://panel.example/#apps']);
});

test('the service worker installs even when one cached file fails', async () => {
  // On the node the fonts folder was unreadable for a month: every install failed and the old worker never showed a notice.
  const handlers = {}, cached = [];
  let waiting = false;
  const self = {addEventListener:(name, fn) => { handlers[name] = fn; }, location:{origin:'https://panel.example'}, skipWaiting:() => { waiting = true; }};
  const cache = {add:async path => { if (path.startsWith('/fonts/')) throw new TypeError('Failed to fetch'); cached.push(path); }};
  vm.runInContext(fs.readFileSync('control-panel/static/sw.js', 'utf8'), vm.createContext({self, URL, caches:{open:async () => cache}}));
  let installed;
  handlers.install({waitUntil:promise => { installed = promise; }});
  await installed;
  assert.equal(waiting, true);
  assert.ok(cached.includes('/biglie-hunt.js') && !cached.some(path => path.startsWith('/fonts/')));
});

test('a 401 returns to the login and clears the log and the address', async () => {
  const s = await shown({...searching, machine:{name:'biglie-regia', ocpus:4, gb:24, ip:'141.144.200.7'}});
  const again = s.ctx.OpenAstroBiglie.refresh();
  s.pending[1].resolve(reply({}, 401)); await again;
  assert.equal(s.logins(), 1);
  assert.equal(s.node('#biglieHuntIp').textContent, '…');
  assert.equal(s.node('#biglieHuntLog').textContent, 'In attesa del registro…');
  assert.equal(s.node('#biglieHuntStart').disabled, true);
  await s.ctx.OpenAstroBiglie.refresh(); assert.equal(s.pending.length, 2);
});
