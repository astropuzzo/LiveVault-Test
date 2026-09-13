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
 assert.match(render({status:'REJECTED',imageEvidenceAttempted:true}),/Insufficient reliable stars/);
 const html=render({...frame,starRescueEccentricityLimit:undefined});assert.match(html,/Applied limits unavailable/);
});
test('file and decision text are escaped and proof URLs are constrained',()=>{
 const html=render({...frame,decisionSummary:'<script>attack()</script>',fileName:'<img src=x>',starProofPng:'" onerror="attack()'});
 assert.doesNotMatch(html,/<script>|<img/);assert.match(html,/&lt;script&gt;/);
});
