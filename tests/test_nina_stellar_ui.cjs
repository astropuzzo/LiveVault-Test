const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const js=fs.readFileSync(path.join(__dirname,'../nina-monitor/static/app.js'),'utf8');
const body=js.slice(js.indexOf('function evidenceMarkup('),js.indexOf('  function renderStellar('));
const context=vm.createContext({});vm.runInContext(body,context);
const render=f=>context.evidenceMarkup(f,'en');
const frame={frameIndex:6,status:'REJECTED',imageEvidenceAvailable:true,imageEvidenceHasRescueMargin:false,starEccentricity:.58,starEccentricityLimit:.60,starRescueEccentricityLimit:.55};
test('borderline rejection is not presented as recovered or proven damage',()=>{
 const html=render(frame);assert.match(html,/insufficient rescue margin/);assert.doesNotMatch(html,/class="stellar-result rescued"/);assert.doesNotMatch(html,/Star-shape limit exceeded/);
});
test('a cleared guide flag cannot override an independent final rejection',()=>{
 const html=render({...frame,guideFalsePositive:true,imageEvidenceHasRescueMargin:true,reason:'BACKGROUND_HIGH'});
 assert.doesNotMatch(html,/class="stellar-result rescued"/);assert.match(html,/Guiding or signal limit still exceeded/);
});
test('usable recovered frame and unavailable measurements are distinct',()=>{
 assert.match(render({...frame,status:'WARNING',guideFalsePositive:true}),/Usable frame/);
 assert.match(render({status:'REJECTED',imageEvidenceAttempted:true}),/Stellar check inconclusive/);
 const html=render({...frame,starRescueEccentricityLimit:undefined});assert.match(html,/Applied limits unavailable/);
});
test('file and decision text are escaped and proof URLs are constrained',()=>{
 const html=render({...frame,decisionSummary:'<script>attack()</script>',fileName:'<img src=x>',starProofPng:'" onerror="attack()'});
 assert.doesNotMatch(html,/<script>|<img/);assert.match(html,/&lt;script&gt;/);
});

test('photometric rescue respects the final verdict and extended proof is constrained',()=>{
 const recovered=render({...frame,status:'WARNING',starCountFalsePositive:true,extendedStarProofPng:'YWJj',relativeStellarFlux:.91,matchedStars:40,verifiedStarRegions:5});
 assert.match(recovered,/class="stellar-result rescued"/);assert.match(recovered,/Extended profile/);assert.match(recovered,/91%/);assert.match(recovered,/Matched stellar signal within recovery limits/);
 const rejected=render({...frame,starCountFalsePositive:true,extendedStarProofPng:'\" onerror=\"attack()'});
 assert.doesNotMatch(rejected,/class="stellar-result rescued"/);assert.doesNotMatch(rejected,/<img/);
});

test('stellar signal rejection remains visible after a guide flag is cleared',()=>{
 const html=render({...frame,guideFalsePositive:true,reason:'SKY_SIGNAL_LOSS',imageEvidenceHasRescueMargin:true});
 assert.match(html,/Sudden loss of stellar signal and star detections/);assert.doesNotMatch(html,/class="stellar-result rescued"/);
 assert.match(render({...frame,reason:'STELLAR_FLUX_LOSS'}),/Measured stellar signal loss exceeds the limit/);
});


test('photometry remains visible without claiming verified stellar shapes',()=>{
 const html=render({status:'WARNING',imageEvidenceAttempted:true,imageEvidenceAvailable:false,stellarPhotometryAvailable:true,relativeStellarFlux:.91,matchedStars:58,stellarReferenceFrames:8,stellarReferenceAgeMinutes:180});
 assert.match(html,/91%/);assert.match(html,/180 min/);assert.match(html,/Shape not verified/);
});
test('signal rejection does not require verified stellar shape in the inspector',()=>{
 const html=render({status:'REJECTED',imageEvidenceAttempted:true,imageEvidenceAvailable:false,stellarPhotometryAvailable:true,relativeStellarFlux:.5,reason:'STELLAR_FLUX_LOSS'});
 assert.match(html,/Measured stellar signal loss exceeds the limit/);assert.match(html,/50%/);assert.doesNotMatch(html,/class="stellar-result rescued"/);
});

async function monitorHarness(fetchImpl) {
 const elements=new Map(),timeouts=new Map(),requests=[],revoked=[];let timerId=0,urlId=0;
 const clock={now:Date.parse('2026-09-29T12:05:30Z')};
 class Element {
  constructor(){this.hidden=false;this.textContent='';this.html='';this.writes=0;this.value='';this.listeners={};this.classList={toggle(){},remove(){}};}
  set innerHTML(value){this.html=value;this.writes++;}get innerHTML(){return this.html;}
  addEventListener(type,listener){this.listeners[type]=listener;}
  querySelector(selector){return this.children?.[selector]||(this.children??={},this.children[selector]=new Element());}
  removeAttribute(name){if(name==='src')this.src='';}
  insertAdjacentHTML(_,value){this.innerHTML+=value;}
  focus(){}
 }
 const element=selector=>{if(!elements.has(selector))elements.set(selector,new Element());return elements.get(selector);};
 class MonitorDate extends Date {static now(){return clock.now;}}
 const defaultResponse=(url)=>url==='api/session'?{ok:true,status:200,json:async()=>({authenticated:false})}:{ok:false,status:404,headers:{get(){return null;}}};
 const runtime=vm.createContext({document:{hidden:false,querySelector:element,addEventListener(){},createElement:()=>new Element()},Date:MonitorDate,AbortController,URL:{createObjectURL:()=>`blob:test-${++urlId}`,revokeObjectURL:url=>revoked.push(url)},setTimeout:(fn,ms)=>{const id=++timerId;timeouts.set(id,{fn,ms});return id;},clearTimeout:id=>timeouts.delete(id),setInterval:()=>++timerId,clearInterval(){},fetch:async(url,options)=>{requests.push({url,options});return fetchImpl?fetchImpl(url,options,defaultResponse):defaultResponse(url);}});
 const instrumented=js.replace(/\}\)\(\);\s*$/, 'globalThis.monitor={render,selectFrame,requestPreview,refresh,showApp,showLogin,jsonFetch};})();');
 vm.runInContext(instrumented,runtime);await new Promise(setImmediate);
 return {monitor:runtime.monitor,element,clock,timeouts,requests,revoked};
}
const light=(extra={})=>({frameIndex:6,timestampUtc:'2026-09-29T12:00:00Z',status:'ACCEPTED',quality:90,fileName:'assessed-six.fits',...extra});
const snapshot=frames=>({configured:true,reachable:true,sessionActive:true,latencyMs:10,snapshot:{frames,currentFrame:frames.at(-1)||{},summary:{captured:frames.length,usable:frames.length},settings:{},mode:{monitorOnly:true},guidingLive:{series:[]}}});

test('connection and session badge recover accurately across offline, idle and active states',async()=>{
 const h=await monitorHarness();
 h.monitor.render(snapshot([light()]));
 assert.equal(h.element('#modeTag').textContent,'LIVE');assert.equal(h.element('.instrument-nav').hidden,false);
 h.monitor.render({configured:true,reachable:false,sessionActive:false,snapshot:null});
 assert.equal(h.element('#modeTag').textContent,'OFFLINE');assert.equal(h.element('.instrument-nav').hidden,true);assert.equal(h.element('#dashboard').hidden,true);
 h.monitor.render({configured:true,reachable:true,sessionActive:false,snapshot:null});
 assert.equal(h.element('#modeTag').textContent,'STANDBY');assert.equal(h.element('#emptyTitle').textContent,'In attesa di una sessione N.I.N.A.');assert.equal(h.element('.instrument-nav').hidden,true);
 h.monitor.render({configured:false,reachable:false,sessionActive:false,snapshot:null});
 assert.equal(h.element('#modeTag').textContent,'CONFIGURA');
 h.monitor.render({...snapshot([light()]),sessionActive:false});
 assert.equal(h.element('#modeTag').textContent,'STANDBY');assert.equal(h.element('.instrument-nav').hidden,false);
 const synthetic=snapshot([light()]);synthetic.snapshot.mode.syntheticMode=true;h.monitor.render(synthetic);
 assert.equal(h.element('#modeTag').textContent,'SYNTHETIC LAB');
 h.monitor.render(snapshot([light()]));
 assert.equal(h.element('#modeTag').textContent,'LIVE');assert.equal(h.element('#emptyState').hidden,true);
});

test('selected inspector updates verdict, preserves unchanged details, and clears reused indices',async()=>{
 const h=await monitorHarness();h.monitor.render(snapshot([light()]));h.monitor.selectFrame(6);
 assert.match(h.element('#frameInspector').innerHTML,/ACCEPTED/);
 h.monitor.render(snapshot([light({status:'REJECTED',probableCause:'STELLAR_FLUX_LOSS'})]));
 assert.match(h.element('#frameInspector').innerHTML,/REJECTED/);assert.match(h.element('#frameInspector').innerHTML,/STELLAR_FLUX_LOSS/);
 const writes=h.element('#frameInspector').writes;h.monitor.render(snapshot([light({status:'REJECTED',probableCause:'STELLAR_FLUX_LOSS'})]));assert.equal(h.element('#frameInspector').writes,writes);
 h.monitor.render(snapshot([light({timestampUtc:'2026-09-29T13:00:00Z'})]));
 assert.equal(h.element('#frameInspector').innerHTML,'Seleziona un frame.');assert.equal(h.element('#inspectorHint').textContent,'clicca una riga');assert.doesNotMatch(h.element('#frameHistoryBody').innerHTML,/ selected/);
});

test('photometric recovery appears alongside guiding recovery in rejected and recovered history',async()=>{
 const h=await monitorHarness();h.monitor.render(snapshot([light({status:'WARNING',starCountFalsePositive:true,guideFalsePositive:false,fileName:'photometric-rescue.fits'})]));
 assert.match(h.element('#rejectedTableBody').innerHTML,/photometric-rescue\.fits/);
});

test('real preview has its own timestamp and refreshes independently of assessed or synthetic frame identity',async()=>{
 let previews=0;
 const h=await monitorHarness((url,options,fallback)=>{
  if(url!=='api/preview.jpg')return fallback(url);previews++;
  const headers={'Content-Type':'image/jpeg','X-QSM-Preview-Utc':'2026-09-29T12:05:00Z','ETag':'"jpeg-1"'};
  return {ok:previews===1,status:previews===1?200:304,headers:{get:name=>headers[name]||null},blob:async()=>({type:'image/jpeg'})};
 });
 await h.monitor.requestPreview(light(),false);h.element('#previewImage').onload();
 assert.equal(previews,1);assert.match(h.element('#previewState').textContent,/30 s fa/);assert.doesNotMatch(h.element('#previewState').textContent,/assessed-six/);
 h.clock.now+=1000;await h.monitor.requestPreview(light(),false);assert.equal(previews,1);
 h.clock.now+=15000;await h.monitor.requestPreview(light(),true);assert.equal(previews,2);
 const request=h.requests.filter(r=>r.url==='api/preview.jpg').at(-1);assert.equal(request.options.headers['If-None-Match'],'"jpeg-1"');assert.equal(h.element('#previewImage').src,'blob:test-1');
 h.monitor.showLogin();assert.deepEqual(h.revoked,['blob:test-1']);
});

test('preview can show a saved real light before QSM has assessed its first frame',async()=>{
 const h=await monitorHarness((url,_,fallback)=>url!=='api/preview.jpg'?fallback(url):{ok:true,status:200,headers:{get:name=>name==='Content-Type'?'image/jpeg':null},blob:async()=>({})});
 await h.monitor.requestPreview({},false);h.element('#previewImage').onload();assert.equal(h.element('#previewImage').hidden,false);assert.equal(h.element('#previewPlaceholder').hidden,true);
});

test('logout discards an older state response and clears a pending preview URL',async()=>{
 let resolveState;
 const h=await monitorHarness((url,_,fallback)=>{
  if(url.startsWith('api/state'))return new Promise(resolve=>{resolveState=resolve;});
  if(url==='api/preview.jpg')return {ok:true,status:200,headers:{get:name=>name==='Content-Type'?'image/jpeg':null},blob:async()=>({})};
  return fallback(url);
 });
 h.monitor.showApp();await h.monitor.requestPreview(light(),false);assert.equal(h.element('#previewImage').src,'blob:test-1');
 h.monitor.showLogin();resolveState({ok:true,status:200,json:async()=>snapshot([light()])});await new Promise(setImmediate);
 assert.equal(h.element('#appView').hidden,true);assert.equal(h.element('#loginView').hidden,false);assert.equal(h.element('#previewImage').src,'');assert.deepEqual(h.revoked,['blob:test-1']);assert.doesNotMatch(h.element('#linkState').querySelector('b').textContent,/online/);
});

test('a hanging state request is aborted so polling can recover',async()=>{
 const h=await monitorHarness((url,options,fallback)=>url==='api/hang'?new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})),{once:true})):fallback(url));
 const pending=h.monitor.jsonFetch('api/hang');const timeout=[...h.timeouts.values()].find(t=>t.ms===8000);assert.ok(timeout);timeout.fn();await assert.rejects(pending,{name:'AbortError'});
 assert.equal([...h.timeouts.values()].filter(t=>t.ms===8000).length,0);
});

test('stellar selector escapes upstream status text',async()=>{
 const h=await monitorHarness();h.monitor.render(snapshot([light({status:'<img src=x>',imageEvidenceAvailable:true})]));
 assert.doesNotMatch(h.element('#stellarSelect').innerHTML,/<img/);assert.match(h.element('#stellarSelect').innerHTML,/&lt;img src=x&gt;/);
});
