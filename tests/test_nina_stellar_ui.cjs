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
