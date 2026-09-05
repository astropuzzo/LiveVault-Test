const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('control-panel/static/app.js', 'utf8');
const context = vm.createContext({});
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
