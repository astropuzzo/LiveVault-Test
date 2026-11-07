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

test('AWAY retains its own label and interval alongside TIP-JAR', () => {
  const ctx = vm.createContext({timestamp: value => Date.parse(value) || 0});
  vm.runInContext(liveSource.slice(liveSource.indexOf('function statusLabel('), liveSource.indexOf('function fallbackProviders(')) +
    liveSource.slice(liveSource.indexOf('function pulseUnavailableIntervals('), liveSource.indexOf('function ensurePulseMediaPreview(')), ctx);
  assert.equal(ctx.statusLabel('away'), 'AWAY');
  assert.equal(ctx.accessStatusLabel('away'), 'AWAY');
  const intervals = ['away','tipjar','live'].map(status => ({status, started_at:'2026-09-30T00:00:00Z',ended_at:'2026-09-30T00:05:00Z'}));
  assert.deepEqual(Array.from(ctx.pulseUnavailableIntervals({access_intervals:intervals}), row=>row.status), ['away','tipjar']);
  assert.equal(ctx.accessStatusLabel('tipjar'), 'TIP-JAR');
});

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

test('whole local playback uses the active media URL even for another linked source', () => {
  const ctx = timelineContext([session(1, {representative_source_id:7, recording_active:true, recordings:[
    {active:true, local_url:'/api/sources/8/capture', started_at:'2026-09-09T04:10:00Z', ended_at:'2026-09-09T04:20:00Z'},
    {kind:'fragment', local_url:'/api/fragments/99/view', started_at:'2026-09-09T04:00:00Z', ended_at:'2026-09-09T04:10:00Z'},
  ]})]);
  ctx.icon = () => '';
  assert.match(ctx.controlRoomPulseMarkup(), /data-local-video="\/api\/sources\/8\/capture"/);
  assert.doesNotMatch(ctx.controlRoomPulseMarkup(), /data-local-video="\/api\/fragments/);
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
test('LiveVault formats sizes, durations and dates the Italian way', () => {
  const ctx = vm.createContext({});
  vm.runInContext(liveSource.slice(liveSource.indexOf('function humanBytes('), liveSource.indexOf('function timestamp(')), ctx);
  vm.runInContext(liveSource.slice(liveSource.indexOf('function shortDate('), liveSource.indexOf('function statNumber(')), ctx);
  assert.equal(ctx.humanBytes(0), '0 B');
  assert.equal(ctx.humanBytes(1536), '1,5 KB');
  assert.equal(ctx.humanBytes(650 * 1024 ** 2), '650 MB');
  assert.equal(ctx.humanBytes(3.6 * 1024 ** 3), '3,6 GB');
  assert.equal(ctx.bytesText(undefined, '12.0 GB'), '12.0 GB');
  assert.equal(ctx.duration(0), '0m');
  assert.equal(ctx.duration(45), '45s');
  assert.equal(ctx.duration(125), '2m 05s');
  assert.equal(ctx.duration(42 * 60 + 30), '42m');
  assert.equal(ctx.duration(2 * 3600 + 40 * 60), '2h 40m');
  assert.equal(ctx.shortDate('2026-09-14'), '14 set');
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

test('archive query syntax filters by creator, status, size, duration and flags', () => {
  const appSource = fs.readFileSync('app/static/app.js', 'utf8');
  const controls = {'#recordingSearch': {value: ''}};
  const ctx = vm.createContext({$: s => controls[s], timestamp: v => Date.parse(v) || 0});
  vm.runInContext(appSource.slice(appSource.indexOf('const ARCHIVE_STATUS_ALIASES'), appSource.indexOf('function recordingMatches(')), ctx);
  const rec = {source_name: 'Zoe Blaze', filename: 'a.mp4', session_id: 's1', upload_status: 'failed', size_bytes: 2 * 1024 ** 3, duration_seconds: 3600, started_at: '2026-09-01T10:00:00Z', local_available: true};
  const match = q => { controls['#recordingSearch'].value = q; return ctx.recordingQueryMatches(rec); };
  assert.equal(match('creator:zoe stato:fallito >1gb durata>30m'), true);
  assert.equal(match('stato:cloud'), false);
  assert.equal(match('<1gb'), false);
  assert.equal(match('durata<30m'), false);
  assert.equal(match('is:locale is:problema dal:2026-08-30 al:2026-09-01'), true);
  assert.equal(match('al:2026-08-31'), false);
  assert.equal(match('blaze a.mp4'), true);
  assert.equal(ctx.parseSizeBytes('1,5g'), 1.5 * 1024 ** 3);
  assert.equal(ctx.parseDurationSeconds('2h'), 7200);
});

function videoLoader(native = true) {
  const pending = [];
  const player = {src: '', pause() {}, load() {}, removeAttribute() { this.src = ''; }, canPlayType: () => native ? 'probably' : ''};
  const title = {textContent: ''};
  const ctx = vm.createContext({
    URL, AbortController, AbortSignal, location: {origin: 'http://local'}, window: {},
    safeUrl: value => value, toast() {}, openModal() {},
    $: selector => selector === '#videoPlayer' ? player : title,
    fetch: (url, options) => new Promise((resolve, reject) => pending.push({url, options, resolve, reject})),
  });
  vm.runInContext(liveSource.slice(liveSource.indexOf('let activeHls = null'), liveSource.indexOf('function statusLabel(')), ctx);
  return {ctx, pending, player, title};
}

test('late playlist cannot replace a newer video or its title', async () => {
  const {ctx, pending, player, title} = videoLoader();
  const old = ctx.playVideo('/api/recordings/1/view', 'old');
  const current = ctx.playVideo('/api/recordings/2/view', 'current');
  assert.equal(pending[0].options.signal.aborted, true);
  pending[1].resolve({ok: true});
  await current;
  pending[0].resolve({ok: true});
  await old;
  assert.equal(player.src, '/api/recordings/2/stream.m3u8');
  assert.equal(title.textContent, 'current');
});

test('closing video cancels pending playback without a late fallback', async () => {
  const {ctx, pending, player} = videoLoader();
  const loading = ctx.playVideo('/api/recordings/1/view', 'old');
  ctx.stopVideo();
  assert.equal(pending[0].options.signal.aborted, true);
  pending[0].reject(new Error('aborted'));
  await loading;
  assert.equal(player.src, '');
});

test('closing while hls.js loads does not attach a stale player', async () => {
  const {ctx, pending, player} = videoLoader(false);
  let libraryResolve;
  let created = 0;
  ctx.loadHlsLibrary = () => new Promise(resolve => { libraryResolve = resolve; });
  const loading = ctx.playVideo('/api/recordings/1/view', 'old');
  pending[0].resolve({ok: true});
  await new Promise(setImmediate);
  ctx.stopVideo();
  class Hls { constructor() { created += 1; } static isSupported() { return true; } }
  libraryResolve(Hls);
  await loading;
  assert.equal(created, 0);
  assert.equal(player.src, '');
});

function statisticsLoader() {
  const pending = [];
  const renders = [];
  const ctx = vm.createContext({
    statisticsBusy: false, statisticsRequest: null, statisticsRequestVersion: 0,
    statisticsDays: 30, statisticsData: null, lastStatisticsLoad: 0,
    profileStatisticsRequestVersion: 0, profileStatisticsDays: 30, profileData: null,
    api: url => new Promise((resolve, reject) => pending.push({url, resolve, reject})),
    renderStatistics: () => renders.push(ctx.statisticsData), renderProfile: () => renders.push(ctx.profileData),
  });
  vm.runInContext(liveSource.slice(liveSource.indexOf('async function loadStatistics('), liveSource.indexOf('// Periodic refreshes must not rebuild')), ctx);
  return {ctx, pending, renders};
}

test('statistics preserves the latest range when responses arrive backwards', async () => {
  const {ctx, pending, renders} = statisticsLoader();
  const old = ctx.loadStatistics(30);
  const current = ctx.loadStatistics(7);
  assert.equal(pending.length, 2);
  pending[1].resolve({days: 7});
  await current;
  pending[0].resolve({days: 30});
  await old;
  assert.equal(ctx.statisticsDays, 7);
  assert.equal(ctx.statisticsData.days, 7);
  assert.equal(renders.length, 1);
  assert.equal(ctx.statisticsBusy, false);
});

test('statistics joins the same pending range and ignores superseded failure', async () => {
  const {ctx, pending} = statisticsLoader();
  const old = ctx.loadStatistics(30);
  const shared = ctx.loadStatistics(30);
  assert.equal(pending.length, 1);
  const current = ctx.loadStatistics(90);
  pending[0].reject(new Error('old request failed'));
  await Promise.all([old, shared]);
  assert.equal(ctx.statisticsBusy, true);
  pending[1].resolve({days: 90});
  await current;
  assert.equal(ctx.statisticsData.days, 90);
});

test('profile statistics cannot attach to another profile or a closed modal', async () => {
  const {ctx, pending, renders} = statisticsLoader();
  const profile = {source: {profile_id: 1}};
  ctx.profileData = profile;
  const loading = ctx.loadProfileStatistics(7);
  const newerProfile = {source: {profile_id: 2}};
  ctx.profileData = newerProfile;
  pending[0].resolve({days: 7});
  await loading;
  assert.equal(newerProfile.activity_statistics, undefined);
  const closing = ctx.loadProfileStatistics(30);
  ctx.profileData = null;
  pending[1].resolve({days: 30});
  await closing;
  assert.equal(renders.length, 0);
});

test('profile statistics ignores an older range response', async () => {
  const {ctx, pending, renders} = statisticsLoader();
  ctx.profileData = {source: {profile_id: 1}};
  const old = ctx.loadProfileStatistics(30);
  const current = ctx.loadProfileStatistics(7);
  pending[1].resolve({days: 7});
  await current;
  pending[0].resolve({days: 30});
  await old;
  assert.equal(ctx.profileData.activity_statistics.days, 7);
  assert.equal(renders.length, 1);
});

function profileLoader() {
  const pending = [];
  const renders = [];
  const controls = {};
  const ctx = vm.createContext({
    sources: [], profileData: null, profileRequestVersion: 0,
    profileStatisticsRequestVersion: 0, profileStatisticsDays: 30,
    $: selector => controls[selector] ||= {classList: {add() {}}, innerHTML: ''},
    $$: () => [], document: {body: {classList: {remove() {}}}},
    api: url => new Promise((resolve, reject) => pending.push({url, resolve, reject})),
    openModal() {}, toast() {}, esc: value => value,
    renderProfile: () => renders.push(ctx.profileData),
  });
  vm.runInContext(liveSource.slice(liveSource.indexOf('function closeModal('), liveSource.indexOf('// Fragmented captures')), ctx);
  vm.runInContext(liveSource.slice(liveSource.indexOf('async function openProfile('), liveSource.indexOf('function renderProfile(')), ctx);
  return {ctx, pending, renders};
}

test('opening a newer profile discards an older response before its statistics request', async () => {
  const {ctx, pending, renders} = profileLoader();
  const old = ctx.openProfile(1);
  const current = ctx.openProfile(2);
  pending[1].resolve({source: {id: 2, profile_id: 2}});
  await new Promise(setImmediate);
  assert.equal(pending[2].url, '/api/library/profiles/2/statistics?days=30');
  pending[2].resolve({days: 30});
  await current;
  pending[0].resolve({source: {id: 1, profile_id: 1}});
  await old;
  assert.equal(ctx.profileData.source.id, 2);
  assert.equal(pending.length, 3);
  assert.equal(renders.length, 1);
});

test('closing a profile while statistics load prevents late modal rendering', async () => {
  const {ctx, pending, renders} = profileLoader();
  const opening = ctx.openProfile(1);
  pending[0].resolve({source: {id: 1, profile_id: 1}});
  await new Promise(setImmediate);
  ctx.closeModal('profileModal');
  pending[1].resolve({days: 30});
  await opening;
  assert.equal(ctx.profileData, null);
  assert.equal(renders.length, 0);
});

test('refresh queues one fresh pass after mutations and retains archive demand', async () => {
  const pending = [];
  const ctx = vm.createContext({
    refresh: options => new Promise(resolve => pending.push({options, resolve})),
    activeView: 'archive', controlRoomPulseData: null,
  });
  vm.runInContext(liveSource.slice(liveSource.indexOf('const refreshV271 = refresh;')), ctx);
  const initial = ctx.refresh();
  let mutationDone = false;
  const mutation = ctx.refresh({includeRecordings: true}).then(() => { mutationDone = true; });
  const shared = ctx.refresh({includeRecordings: false});
  assert.equal(pending.length, 1);
  pending[0].resolve();
  await new Promise(setImmediate);
  assert.equal(pending.length, 2);
  assert.equal(pending[1].options.includeRecordings, true);
  assert.equal(mutationDone, false);
  pending[1].resolve();
  await Promise.all([initial, mutation, shared]);
  assert.equal(mutationDone, true);
  assert.equal(pending.length, 2);
});

test('periodic refresh callers share pending work without prolonging a slow refresh', async () => {
  const pending = [];
  const ctx = vm.createContext({
    refresh: options => new Promise(resolve => pending.push({options, resolve})),
    activeView: 'archive', controlRoomPulseData: null,
  });
  vm.runInContext(liveSource.slice(liveSource.indexOf('const refreshV271 = refresh;')), ctx);
  const initial = ctx.refresh();
  const tick = ctx.refresh();
  pending[0].resolve();
  await Promise.all([initial, tick]);
  assert.equal(pending.length, 1);
});

test('API shows the actionable message in structured busy errors', async () => {
  const ctx = vm.createContext({
    AbortSignal,
    fetch: async () => ({ok: false, status: 409, json: async () => ({detail: {code: 'recording_busy', message: 'File occupato'}})}),
  });
  vm.runInContext(liveSource.slice(liveSource.indexOf('async function api('), liveSource.indexOf('function setBusy(')), ctx);
  await assert.rejects(ctx.api('/api/recordings/1/recover', {method: 'POST'}), /File occupato/);
});
