const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync('control-panel/static/diagnostics.js', 'utf8'), context);
const report = context.OpenAstroDiagnostics.report;
const good = () => ({timestamp:1000, host:{temperature:55,memory:{percent:30}}, livevault:{ok:true,worker:{tasks:{uploader:true}},storage_handoff:{mode:'nvme'}}, storage:{data:{mounted:true,percent:30},share:{mounted:true}}, power:{},services:{docker:'active',tailscale:'active',backup:'active'}});
test('distinguishes historical power events from current critical faults', () => {
  const data = good(); data.power.power_event_seen = true;
  assert.deepEqual(Array.from(report(data,1005).issues, x => x.severity), ['info']);
  data.power.undervoltage_now = true;
  assert.equal(report(data,1005).issues[0].severity,'critical');
  assert.equal(report(data,1005).issues[0].id,'power');
});
test('healthy service response cannot hide dead worker and full buffer', () => {
  const data = good(); data.livevault.worker.tasks.uploader = false;
  data.livevault.storage_handoff = {mode:'buffer',full:true};
  const ids = Array.from(report(data,1005).issues, x => x.id);
  assert.ok(ids.includes('workers')); assert.ok(ids.includes('buffer'));
  assert.equal(report(data,1005).livevault.stopped_workers,1);
});
test('age is recomputed from sampled time, missing metrics stay unknown', () => {
  assert.equal(report(good(),1031).issues[0].id,'stale');
  assert.equal(report({},1000).host.temperature_c,null);
  assert.equal(report({},1000).livevault.ok,null);
  assert.equal(report({},1000).storage.root.mounted,null);
});
test('export allowlist excludes credentials, endpoints, filenames and raw errors', () => {
  const data=good(); data.csrf='private-token'; data.actions=['private-log'];
  data.pihole={token:'private-dns'}; data.host.name='private-host';
  data.storage.data.path='/private-path'; data.livevault.worker.errors={upload:'https://private/?token=secret'};
  data.interfaces=[{url:'private-endpoint'}];
  const encoded=JSON.stringify(report(data,1005));
  for(const secret of ['private-token','private-log','private-dns','private-host','private-path','private-endpoint','https://private']) assert.ok(!encoded.includes(secret));
});
