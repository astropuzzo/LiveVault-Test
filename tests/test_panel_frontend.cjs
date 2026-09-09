const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('control-panel/static/app.js', 'utf8');
const context = vm.createContext({});

const liveSource = fs.readFileSync('app/static/app.js', 'utf8');
const pulseSource = fs.readFileSync('app/static/pulse-tuning.js', 'utf8');
function pulseLoader() {
  const pending = [];
  const handlers = {};
  const ctx = vm.createContext({
    localStorage: {getItem: () => null, setItem: () => {}},
    document: {addEventListener: (name, fn) => { handlers[name] = fn; }},
    app: {classList: {contains: () => true}},
    window: {addEventListener: () => {}},
    renderSources: () => {}, hidePulseMediaPreview: () => {},
    requestAnimationFrame: () => {}, timestamp: value => Date.parse(value) || 0,
    controlRoomPulseData: {hours: 6, sessions: []}, lastControlRoomPulseLoad: 0,
    controlRoomPulseError: '', controlRoomPulseLoading: false,
    controlRoomPulseExpanded: false, loadControlRoomPulse: () => {},
    api: url => new Promise((resolve, reject) => pending.push({url, resolve, reject})),
  });
  vm.runInContext(pulseSource, ctx);
  const change = hours => handlers.change({target: {closest: () => ({value: String(hours)})}});
  return {ctx, pending, change};
}
const pulseResponse = hours => ({hours, generated_at:'2026-09-09T09:00:00Z', sessions: []});

test('pulse coalesces concurrent refreshes and does not request before authenticated boot', async () => {
  const {ctx, pending} = pulseLoader();
  assert.equal(pending.length, 0);
  const a = ctx.loadControlRoomPulse();
  const b = ctx.loadControlRoomPulse(true);
  assert.equal(pending.length, 1);
  pending[0].resolve(pulseResponse(6));
  await Promise.all([a, b]);
  assert.equal(ctx.controlRoomPulseLoading, false);
});

test('older range response cannot overwrite newer selection', async () => {
  const {ctx, pending, change} = pulseLoader();
  const old = ctx.loadControlRoomPulse();
  const latest = change(24);
  pending[1].resolve(pulseResponse(24));
  await latest;
  pending[0].resolve(pulseResponse(6));
  await old;
  assert.equal(ctx.controlRoomPulseData.hours, 24);
  assert.equal(ctx.controlRoomPulseError, '');
});

test('failed or malformed pulse response preserves data but exposes stale state and retries', async () => {
  const {ctx, pending} = pulseLoader();
  const first = ctx.loadControlRoomPulse();
  pending[0].resolve(pulseResponse(6));
  await first;
  const original = ctx.controlRoomPulseData;
  for (const failure of ['network', 'malformed']) {
    const next = ctx.loadControlRoomPulse(true);
    if (failure === 'network') pending.at(-1).reject(new Error('offline'));
    else pending.at(-1).resolve({hours: 6});
    await next;
    assert.equal(ctx.controlRoomPulseData, original);
    assert.ok(ctx.controlRoomPulseError);
    assert.equal(ctx.controlRoomPulseLoading, false);
  }
});

function timelineContext(sessions) {
  const ctx = vm.createContext({
    timestamp: value => new Date(value).getTime() || 0,
    esc: value => String(value ?? ''), safeUrl: value => value || '',
    dateText: value => new Date(value).toISOString().slice(0, 16),
    DISPLAY_TIME_ZONE: 'UTC', window: {matchMedia: () => ({matches: true})},
    controlRoomPulseData: {...pulseResponse(6), window_start:'2026-09-09T03:00:00Z', sessions},
    controlRoomPulseError: '', controlRoomPulseLoading: false, controlRoomPulseExpanded: false,
    lastControlRoomPulseLoad: 1,
    controlRoomProfileRows: () => sessions.map(row => ({profile_id:row.profile_id})),
    dashboardProfileMatches: () => true, pulseSessions: () => sessions,
    creatorLinkMarkup: (_id, name) => name,
  });
  vm.runInContext(liveSource.slice(liveSource.indexOf('function pulseTimeLabel('), liveSource.indexOf('function ensurePulseMediaPreview(')), ctx);
  vm.runInContext(liveSource.slice(liveSource.indexOf('function pulseRangeLabel('), liveSource.indexOf('function controlRoomRecentEnded(')), ctx);
  return ctx;
}
const session = (id, extra = {}) => ({profile_id:id, display_name:`profile-${id}`, started_at:'2026-09-09T04:00:00Z', ended_at:'2026-09-09T05:00:00Z', access_intervals:[], ...extra});

test('timeline marks unrecorded public gaps beside private intervals', () => {
  const ctx = timelineContext([session(1, {access_intervals:[{status:'private', started_at:'2026-09-09T04:20:00Z', ended_at:'2026-09-09T04:40:00Z'}]})]);
  const html = ctx.controlRoomPulseMarkup();
  assert.equal((html.match(/class="cr-pulse-missed-span"/g) || []).length, 2);
});

test('mobile timeline prioritizes ongoing profiles and can expand all hidden rows', () => {
  const rows = Array.from({length:7}, (_, i) => session(i));
  rows.push(session(99, {started_at:'2026-09-09T03:00:00Z', ended_at:null, state:'live'}));
  const ctx = timelineContext(rows);
  let html = ctx.controlRoomPulseMarkup();
  assert.match(html, /profile-99/);
  assert.match(html, /Mostra altri 3 profili/);
  ctx.controlRoomPulseExpanded = true;
  html = ctx.controlRoomPulseMarkup();
  assert.equal((html.match(/class="cr-pulse-row"/g) || []).length, 8);
});

test('recording media order, missing storage and cross-day ranges remain truthful', () => {
  const ctx = timelineContext([session(1, {recordings:[{started_at:'2026-09-09T04:10:00Z', ended_at:'2026-09-09T04:20:00Z', upload_provider:'gofile'}]})]);
  assert.match(ctx.controlRoomPulseMarkup(), /FILE NON DISPONIBILE/);
  assert.match(ctx.pulseRangeLabel('2026-09-08T23:00:00Z', '2026-09-09T01:00:00Z'), /2026-09-08.*2026-09-09/);
  const files = ctx.pulseRecordingFiles({recordings:[
    {started_at:'2026-09-09T05:00:00Z', ended_at:'2026-09-09T06:00:00Z'},
    {started_at:'2026-09-09T03:00:00Z', ended_at:'2026-09-09T04:00:00Z'},
    {started_at:'2026-09-09T05:00:00Z', ended_at:'2026-09-09T04:00:00Z'},
  ]});
  assert.equal(files.length, 2);
  assert.equal(files[0].started_at, '2026-09-09T03:00:00Z');
});

test('short recording uses exact timeline width with a separate touch target', () => {
  const ctx = timelineContext([session(1, {recordings:[{started_at:'2026-09-09T04:10:00Z', ended_at:'2026-09-09T04:11:00Z'}]})]);
  const html = ctx.controlRoomPulseMarkup();
  assert.match(html, /class="cr-pulse-rec-span[^>]+width="2\.778"/);
  assert.match(html, /class="cr-pulse-hit"[^>]+width="24\.000"/);
});

test('active product preview labels archive covers and always retains fallback markup', () => {
  const source = fs.readFileSync('app/static/ui.js', 'utf8');
  const ctx = vm.createContext({
    document:{hidden:false}, activeView:'dashboard',
    timestamp: value => Date.parse(value) || 0, safeUrl:value=>value||'',
    esc:value=>String(value||''), ago:()=> '1 min fa', controlRoomInitials:()=> 'AB',
  });
  vm.runInContext(source.slice(source.indexOf('  controlRoomPreviewMarkup = '), source.indexOf('  function processButton(')), ctx);
  const sourceRow = {preview_updated_at:'2026-09-09T09:00:00Z',cover_thumbnail_url:'/cover.jpg'};
  assert.match(ctx.controlRoomPreviewMarkup({source:sourceRow}), /Copertina archivio/);
  const html = ctx.controlRoomPreviewMarkup({source:{...sourceRow,preview_url:'/preview'}, recording:true});
  assert.match(html, /data-live-preview/);
  assert.match(html, /cr-preview-placeholder/);
});

test('moving focus into the touch preview does not dismiss its playback action', () => {
  let handler;
  let hidden = 0;
  const ctx = vm.createContext({document:{addEventListener: (_name, fn) => {handler=fn;}}, hidePulseMediaPreview:()=>{hidden++;}});
  const start = liveSource.indexOf("document.addEventListener('focusout', event => {");
  const end = liveSource.indexOf("document.addEventListener('click'", start);
  vm.runInContext(liveSource.slice(start, end), ctx);
  handler({target:{closest:()=>true}, relatedTarget:{closest:()=>true}});
  assert.equal(hidden, 0);
  handler({target:{closest:()=>true}, relatedTarget:null});
  assert.equal(hidden, 1);
});
vm.runInContext(source.slice(source.indexOf('function mean('), source.indexOf('function chartMarkup(')), context);
test('missing telemetry is excluded from averages', () => {
  assert.equal(context.mean([{temp: null}, {temp: 50}, {temp: 60}], 'temp'), 55);
});
test('power presentation never substitutes an estimate for a sensor', () => {
  const power = vm.createContext({});
  vm.runInContext(source.slice(source.indexOf('function measuredWatts('), source.indexOf('function renderHistory(')), power);
  assert.equal(power.measuredWatts({measurement:'measured',watts:0}), 0);
  assert.equal(power.measuredWatts({measurement:'measured',watts:8.1}), 8.1);
  assert.equal(power.measuredWatts({measurement:'estimated',watts:6}), null);
  assert.equal(power.measuredWatts({measurement:'unavailable',estimated_watts:6}), null);
});
test('chart preserves gaps rather than drawing missing samples as zero', () => {
  const result = context.linePath([{t:0,temp:50},{t:10,temp:null},{t:20,temp:60}], 'temp', 0, 100);
  assert.equal((result.match(/M/g) || []).length, 2);
  assert.equal((result.match(/L/g) || []).length, 0);
});
test('chart positions samples by timestamp', () => {
  const result = context.linePath([{t:0,cpu:10},{t:5,cpu:20},{t:20,cpu:30}], 'cpu', 0, 100);
  assert.match(result, /L150\.0,/);
});

test('expired action session releases the busy state for reauthentication', async () => {
  let calls = 0;
  let signIns = 0;
  const actionContext = vm.createContext({
    AbortSignal,
    fetch: async () => { calls++; return {status:401}; },
    showLogin: () => { signIns++; },
    toast: () => {}, refresh: () => {}, setTimeout: () => {}
  });
  vm.runInContext('let actionBusy = false; let csrf = "test"; let powerDirty = false;' +
    source.slice(source.indexOf('async function executeAction('), source.indexOf('function bindActionButtons(')), actionContext);
  await actionContext.executeAction('backup_now');
  await actionContext.executeAction('backup_now');
  assert.equal(calls, 2);
  assert.equal(signIns, 2);
});

test('dashboard search uses grouped source rows and respects status filters', () => {
  const appSource = fs.readFileSync('app/static/app.js', 'utf8');
  const controls = {'#dashboardSearch': {value:'alias'}, '#dashboardStatus': {value:'all'}};
  const dashboard = vm.createContext({$: selector => controls[selector]});
  vm.runInContext(appSource.slice(appSource.indexOf('function dashboardProfileMatches('), appSource.indexOf('renderSources = function renderSourcesControlRoom(')), dashboard);
  const profile = {display_name:'Creator', rows:[{name:'alias'}], live:false};
  assert.equal(dashboard.dashboardProfileMatches(profile), true);
  controls['#dashboardSearch'].value = 'unknown';
  assert.equal(dashboard.dashboardProfileMatches(profile), false);
  controls['#dashboardSearch'].value = '';
  controls['#dashboardStatus'].value = 'live';
  assert.equal(dashboard.dashboardProfileMatches(profile), false);
  assert.equal(dashboard.dashboardProfileMatches({...profile, live:true}), true);
});
