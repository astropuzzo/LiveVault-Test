const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('control-panel/static/apps.js','utf8');
const appSource = fs.readFileSync('control-panel/static/app.js','utf8');
const tick = () => new Promise(resolve=>setImmediate(resolve));

function setup() {
  const pending=[], nodes=new Map(), listeners={};
  function node(selector) {
    if(!nodes.has(selector))nodes.set(selector,{textContent:'',innerHTML:'',hidden:false,disabled:false,value:'',href:'',className:'',
      setAttribute(){},addEventListener(name,fn){this[name]=fn;}});
    return nodes.get(selector);
  }
  const document={hidden:false,querySelector:node,addEventListener:(name,fn)=>listeners[name]=fn};
  let loginCalls=0;
  const ctx=vm.createContext({document,location:{hostname:'remote.example'},AbortController,console,
    fetch:(url,options)=>new Promise((resolve,reject)=>pending.push({url,options,resolve,reject})),
    showLogin:()=>{loginCalls++;ctx.OpenAstroApps.setAuthenticated(false);},setTimeout,clearTimeout,URL,Blob,Date});
  vm.runInContext(source,ctx);
  return {ctx,nodes,node,pending,listeners,document,loginCalls:()=>loginCalls};
}
function sample() {
  return {ok:true,generated_at:'2026-10-08T10:00:00Z',apps:[{id:'bonsai-sensei',name:'Bonsai Sensei',available:true,status:'preparing',billing_enabled:false,
    version:'1.0.0',generated_at:'2026-10-08T10:00:00Z',purchases:{total:3,active:1,revoked:1,pending_purchase:1,processing:0,permanent:2,consumable:1},
    users:{purchasing_accounts:2},daily_utc:[{date:'2026-10-08',purchases:3,revocations:1}],catalog:[{id:'garden_pass',kind:'permanent'}],missing_configuration:[],
    links:{coolify_public:'https://console.example/app',coolify_local:'http://local.example/app',google_play:'https://play.google.com/console/'}}]};
}
function response(data=sample(),status=200) {return {ok:status===200,status,json:async()=>data};}

test('app requests are lazy and suspend when the page or view is hidden',async()=>{
  const s=setup();await s.ctx.OpenAstroApps.refresh();assert.equal(s.pending.length,0);
  s.document.hidden=true;s.ctx.OpenAstroApps.setView('apps');assert.equal(s.pending.length,0);
  s.document.hidden=false;s.listeners.visibilitychange();assert.equal(s.pending.length,1);
  const signal=s.pending[0].options.signal;
  s.ctx.OpenAstroApps.setView('media');assert.equal(signal.aborted,true);
  s.pending[0].resolve(response());await tick();
  assert.equal(s.node('#appsTotal').textContent,'');
  s.ctx.OpenAstroApps.setView('apps');assert.equal(s.pending.length,2);
  s.pending[1].resolve(response());await tick();assert.equal(s.node('#appsTotal').textContent,'3');
});

test('refresh bursts share one request and server 401 enters the existing login flow',async()=>{
  const s=setup();s.ctx.OpenAstroApps.setView('apps');
  await s.ctx.OpenAstroApps.refresh();await s.ctx.OpenAstroApps.refresh();assert.equal(s.pending.length,1);
  s.pending[0].resolve(response());await tick();assert.equal(s.node('#appsAccounts').textContent,'2');
  const next=s.ctx.OpenAstroApps.refresh();assert.equal(s.pending.length,2);
  s.pending[1].resolve(response({},401));await next;
  assert.equal(s.loginCalls(),1);assert.equal(s.node('#appsTotal').textContent,'—');
  assert.equal(s.node('#appsDailyRows').innerHTML,'');assert.equal(s.node('#appsExport').disabled,true);
  await s.ctx.OpenAstroApps.refresh();assert.equal(s.pending.length,2);
});

test('logout erases counters and prevents a late authenticated response restoring them',async()=>{
  const s=setup();s.ctx.OpenAstroApps.setView('apps');
  s.ctx.OpenAstroApps.setAuthenticated(false);
  s.pending[0].resolve(response());await tick();
  assert.equal(s.node('#appsAccounts').textContent,'—');assert.equal(s.node('#appsExport').disabled,true);
  s.ctx.OpenAstroApps.setAuthenticated(true);assert.equal(s.pending.length,2);
  s.pending[1].resolve(response());await tick();assert.equal(s.node('#appsActive').textContent,'1');
});

test('an old 401 cannot log out a newly authenticated session',async()=>{
  const s=setup();s.ctx.OpenAstroApps.setView('apps');
  s.ctx.OpenAstroApps.setAuthenticated(false);s.ctx.OpenAstroApps.setAuthenticated(true);
  assert.equal(s.pending.length,2);
  s.pending[0].resolve(response({},401));await tick();assert.equal(s.loginCalls(),0);
  s.pending[1].resolve(response());await tick();assert.equal(s.node('#appsTotal').textContent,'3');
});

test('service unavailable is unknown, while an empty ledger shows real zero',async()=>{
  const s=setup();s.ctx.OpenAstroApps.setView('apps');
  const unavailable=sample();Object.assign(unavailable.apps[0],{available:false,purchases:null,users:{purchasing_accounts:null},daily_utc:[]});
  s.pending[0].resolve(response(unavailable,503));await tick();
  assert.equal(s.node('#appsTotal').textContent,'—');assert.equal(s.node('#appsExport').disabled,true);
  const next=s.ctx.OpenAstroApps.refresh();const empty=sample();
  empty.apps[0].purchases.total=0;empty.apps[0].users.purchasing_accounts=0;empty.apps[0].daily_utc=[{date:'2026-10-08',purchases:0,revocations:0}];
  s.pending[1].resolve(response(empty));await next;
  assert.equal(s.node('#appsTotal').textContent,'0');assert.equal(s.node('#appsAccounts').textContent,'0');
  assert.match(s.node('#appsChartSummary').textContent,/0 ricevute create/);
  assert.match(s.node('#appsDailyRows').innerHTML,/<td>0<\/td>/);
});

test('aggregate export drops raw private metadata, link targets and account identifiers',()=>{
  const s=setup(), raw=sample();
  raw.csrf='private-csrf';raw.apps[0].owner='private-owner';raw.apps[0].token='private-token';raw.apps[0].error='private-error';
  raw.apps[0].users.ids=['private-id'];raw.apps[0].users.registered=123;
  const report=s.ctx.OpenAstroApps.exportReport(raw),encoded=JSON.stringify(report);
  for(const privateValue of ['private-csrf','private-owner','private-token','private-error','private-id','console.example','local.example'])assert.ok(!encoded.includes(privateValue));
  assert.equal(report.apps[0].users.registered,null);assert.equal(report.apps[0].users.purchasing_accounts,2);
  assert.equal(report.apps[0].revenue.available,false);assert.equal(report.apps[0].ads.available,false);
});

test('existing navigation updates route title and calls the app lifecycle hook',()=>{
  const events=[], panels=['dashboard','media','apps'].map(view=>({dataset:{viewPanel:view},classList:{toggle:(name,value)=>events.push([view,value])}}));
  const ctx=vm.createContext({VALID_VIEWS:new Set(['dashboard','media','apps']),currentView:'dashboard',document:{body:{dataset:{}}},
    location:{hash:''},history:{pushState:()=>{}},window:{scrollTo:()=>{}},
    $:()=>({textContent:''}),$$:selector=>selector==='[data-view-panel]'?panels:[],
    OpenAstroApps:{setView:view=>events.push(['lifecycle',view])},refreshHistory(){},refreshMonthlyEnergy(){},loadMediaHome(){},mediaUuid:''});
  vm.runInContext(appSource.slice(appSource.indexOf('function selectView('),appSource.indexOf('function setHeroBar(')),ctx);
  ctx.selectView('apps');assert.equal(ctx.document.body.dataset.view,'apps');
  assert.equal(ctx.document.title,'App e ricavi · OpenAstro');
  assert.ok(events.some(([name,value])=>name==='lifecycle'&&value==='apps'));
  assert.ok(events.some(([name,value])=>name==='apps'&&value===true));
});
