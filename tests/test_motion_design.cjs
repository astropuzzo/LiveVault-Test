const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'app/static/motion.js'), 'utf8');
function setup({reduced = false, intersection = true, animated = true} = {}) {
  let now=10000;
  class MotionDate extends Date { static now(){return now;} }
  const callbacks = {}, documentCallbacks = {}, frames = [], animations = [], nodes = [], orbits = [];
  const media = {matches: reduced, addEventListener: (_name, callback) => { media.change = callback; }};
  class Element {
    constructor(id) {
      this.id=id;this.hidden=false;this.rects=[{}];this.dataset={};this.children=[];this.tagName='DIV';this.disabled=false;
      this.classes=new Set();this.classList={add:(v)=>this.classes.add(v),remove:(v)=>this.classes.delete(v)};
      this.selectors=new Map();
    }
    getClientRects() { return this.rects; }
    querySelectorAll(selector) { return this.selectors.get(selector) || []; }
    closest(selector) { return this.selectors.get('closest:'+selector) || null; }
    animate(keyframes, timing) {
      let resolve, reject;
      const item={node:this,keyframes,timing,cancelled:false,finished:new Promise((done,fail)=>{resolve=done;reject=fail;}),cancel(){this.cancelled=true;reject(new Error('cancelled'));},complete(){resolve();}};
      animations.push(item);return item;
    }
  }
  if (!animated) delete Element.prototype.animate;
  const body=new Element('body'),app=new Element('app'),panels=[new Element('libraryView')];panels[0].hidden=true;
  const document={hidden:false,body,getElementById:id=>id==='app'?app:null,querySelectorAll:selector=>selector==='[data-motion]'?nodes:selector==='[data-orbit]'?orbits:selector==='[data-page], [data-view-panel]'?panels:[],addEventListener:(name,callback)=>{documentCallbacks[name]=callback;}};
  let mutations,intersections;
  class MutationObserver {
    constructor(callback){mutations=this;this.callback=callback;this.targets=[];}
    observe(target,options){this.targets.push({target,options});this.disconnected=false;}
    disconnect(){this.disconnected=true;}
  }
  class IntersectionObserver {
    constructor(callback){intersections=this;this.callback=callback;this.targets=new Set();}
    observe(node){this.targets.add(node);}
    unobserve(node){this.targets.delete(node);}
    disconnect(){this.targets.clear();this.disconnected=true;}
  }
  const location={pathname:'/',hash:''};
  const window={innerWidth:1440,innerHeight:1000,matchMedia:()=>media,addEventListener:(name,callback)=>{callbacks[name]=callback;},requestAnimationFrame:callback=>{frames.push(callback);}};
  nodes.push(new Element('hero'));
  vm.runInNewContext(source,{Date:MotionDate,window,document,location,Element,MutationObserver,IntersectionObserver:intersection?IntersectionObserver:undefined});
  const flush=()=>{while(frames.length)frames.shift()();};
  return {nodes,panels,orbits,animations,media,document,callbacks,documentCallbacks,location,Element,flush,frames,mutations,intersections,motion:window.OpenAstroMotion,advance:ms=>{now+=ms;}};
}
test('three panels share the same bounded motion implementation', () => {
  for (const folder of ['control-panel/static', 'nina-monitor/static']) assert.equal(fs.readFileSync(path.join(root, folder, 'motion.js'), 'utf8'), source);
});
test('reduced motion and unsupported Web Animations keep content visible without effects', () => {
  for (const config of [{reduced:true}, {animated:false}]) {
    const env = setup(config); env.flush(); assert.equal(env.animations.length, 0); assert.equal(env.nodes[0].hidden, false);
  }
});
test('viewport entry runs once; repeated polling and reentry do not replay it', () => {
  const env = setup(); env.flush(); assert.equal(env.animations.length, 0);
  env.intersections.callback([{target:env.nodes[0],isIntersecting:true}]); assert.equal(env.animations.length, 1);
  env.mutations.callback([]); env.mutations.callback([]); assert.equal(env.frames.length, 1); env.flush();
  env.intersections.callback([{target:env.nodes[0],isIntersecting:true}]); assert.equal(env.animations.length, 1);
  assert.equal(env.mutations.targets.some(row=>row.options.subtree), false);
});
test('navigation animates newly exposed surfaces; all delays are bounded', () => {
  const env = setup({intersection:false}); env.flush();
  for (let i=0;i<12;i++) env.nodes.push(new env.Element('route'+i));
  env.location.hash='#library'; env.callbacks.hashchange(); env.flush();
  assert.equal(env.animations.length, 14);
  for (const animation of env.animations) { assert.ok(animation.timing.delay <= 220); assert.equal(animation.timing.duration, 560); }
  env.callbacks.hashchange(); env.flush(); assert.equal(env.animations.length, 14);
});
test('hidden tab cancels animations and suppresses viewport crossings until return', () => {
  const env = setup(); env.flush(); env.intersections.callback([{target:env.nodes[0],isIntersecting:true}]);
  env.document.hidden=true; env.documentCallbacks.visibilitychange(); assert.equal(env.animations[0].cancelled,true);
  const later = new env.Element('later');env.nodes.push(later);env.intersections.callback([{target:later,isIntersecting:true}]); assert.equal(env.animations.length,1);
  env.document.hidden=false;env.documentCallbacks.visibilitychange();env.flush();env.intersections.callback([{target:later,isIntersecting:true}]); assert.equal(env.animations.length,2);
});
test('changing motion preference cancels active entrances immediately', () => {
  const env = setup({intersection:false});env.flush();env.media.matches=true;env.media.change();env.flush();assert.equal(env.animations[0].cancelled,true);
  env.location.hash='#archive';env.nodes.push(new env.Element('archive'));env.callbacks.hashchange();env.flush();assert.equal(env.animations.length,1);
});
test('pagehide prevents a queued entrance; BFCache restore resumes observation', () => {
  const env = setup({intersection:false});env.callbacks.pagehide();env.flush();assert.equal(env.animations.length,0);assert.equal(env.mutations.disconnected,true);
  env.callbacks.pageshow({persisted:true});env.flush();assert.equal(env.animations.length,1);assert.equal(env.mutations.disconnected,false);
});

test('pushState navigation registers a newly visible panel without hashchange', () => {
  const env=setup({intersection:false});env.flush();env.nodes.push(new env.Element('librarySurface'));
  env.panels[0].hidden=false;env.mutations.callback([]);env.flush();assert.equal(env.animations.length,3);
  assert.ok(env.mutations.targets.some(row=>row.target===env.panels[0]));
});


test('press feedback is finite and disabled or reduced controls stay still', () => {
  const env=setup({intersection:false});env.flush();const button=new env.Element('button');
  env.motion.press(button);assert.equal(env.animations.at(-1).timing.duration,280);
  const count=env.animations.length;button.disabled=true;env.motion.press(button);env.media.matches=true;button.disabled=false;env.motion.press(button);assert.equal(env.animations.length,count);
});
test('close preserves a surface through its exit then performs cleanup exactly once', async () => {
  const env=setup({intersection:false});env.flush();const modal=new env.Element('modal');let closed=0;
  env.motion.close(modal,()=>{modal.hidden=true;closed++;});assert.equal(modal.hidden,false);assert.equal(closed,0);
  const exit=env.animations.at(-1);assert.equal(exit.timing.duration,180);exit.complete();await Promise.resolve();await Promise.resolve();
  assert.equal(modal.hidden,true);assert.equal(closed,1);assert.equal(exit.cancelled,true);assert.equal(modal.classes.has('motion-closing'),false);
});
test('reduced motion or hidden tab immediately completes a pending exit', () => {
  for(const reason of ['reduced','hidden']){
    const env=setup({intersection:false});env.flush();const modal=new env.Element('modal');let closed=0;
    env.motion.close(modal,()=>closed++);
    if(reason==='reduced'){env.media.matches=true;env.media.change();}else{env.document.hidden=true;env.documentCallbacks.visibilitychange();}
    assert.equal(closed,1);assert.equal(modal.classes.size,0);
  }
});
test('reopening during an exit cancels stale hiding without blocking the new entrance', async () => {
  const env=setup({intersection:false});env.flush();const modal=new env.Element('modal');let closed=0;
  env.motion.close(modal,()=>closed++);const exit=env.animations.at(-1);env.motion.open(modal);await Promise.resolve();
  assert.equal(exit.cancelled,true);assert.equal(closed,0);assert.equal(env.animations.at(-1).timing.duration,380);assert.equal(modal.classes.has('motion-closing'),false);
});
test('telemetry replacement does not replay chart drawing; a range selection does', () => {
  const env=setup({intersection:false});env.flush();const chart=new env.Element('chart');const path=new env.Element('path');path.getTotalLength=()=>120;
  const paths='.chart-line, .tl-q, .tl-c, .tl-g, .tl-s, .tl-b, .line-ra, .line-dec';chart.selectors.set(paths,[path]);
  env.motion.render(chart,'chart');let count=env.animations.length;assert.equal(env.animations.at(-1).keyframes[0].strokeDashoffset,120);
  const freshPath=new env.Element('freshPath');freshPath.getTotalLength=()=>150;chart.selectors.set(paths,[freshPath]);env.motion.render(chart,'chart');assert.equal(env.animations.length,count);
  const range=new env.Element('range');range.selectors.set('closest:[data-range], #statisticsDays, #statisticsRefresh, #refreshButton',range);env.documentCallbacks.change({target:range});env.motion.render(chart,'chart');assert.equal(env.animations.length,count+1);
  env.advance(2000);env.motion.render(chart,'chart');assert.equal(env.animations.length,count+1);
});
test('an empty chart is not marked drawn before asynchronous data arrives', () => {
  const env=setup({intersection:false});env.flush();const chart=new env.Element('chart');env.motion.draw(chart);const count=env.animations.length;
  const bar=new env.Element('bar');chart.selectors.set('.chart-bar, .stellar-bar, .bar-column',[bar]);env.motion.draw(chart);assert.equal(env.animations.length,count+1);
});
test('dialog and disclosure entrances leave native toggle semantics intact', () => {
  const env=setup({intersection:false});env.flush();const dialog=new env.Element('dialog');dialog.tagName='DIALOG';dialog.open=true;
  env.documentCallbacks.toggle({target:dialog});assert.equal(dialog.open,true);assert.equal(env.animations.at(-1).timing.duration,380);
  const details=new env.Element('details'),summary=new env.Element('summary'),content=new env.Element('content');summary.tagName='SUMMARY';details.children=[summary,content];details.open=true;
  summary.parentElement=details;summary.selectors.set('closest:summary',summary);env.documentCallbacks.click({target:summary});
  env.documentCallbacks.toggle({target:details});assert.equal(details.open,true);assert.equal(env.animations.at(-1).node,content);assert.equal(env.animations.at(-1).timing.duration,360);
});
test('animation work has a concurrent budget and rapid feedback replaces an older effect', () => {
  const env=setup({intersection:false});env.flush();const button=new env.Element('button');env.motion.press(button);const first=env.animations.at(-1);env.motion.press(button);assert.equal(first.cancelled,true);
  for(let i=0;i<100;i++)env.motion.press(new env.Element('press'+i));assert.ok(env.animations.filter(a=>!a.cancelled).length<=80);
});

test('an exit at the animation budget completes and does not leave a phantom closing state', () => {
  const env=setup({intersection:false});env.flush();for(let i=0;i<100;i++)env.motion.press(new env.Element('busy'+i));
  const modal=new env.Element('modal');let closed=0;env.motion.close(modal,()=>closed++);assert.equal(closed,1);
  env.document.hidden=true;env.documentCallbacks.visibilitychange();env.document.hidden=false;env.motion.open(modal);env.motion.close(modal,()=>closed++);
  assert.equal(closed,1);assert.equal(env.animations.at(-1).timing.duration,180);
});

test('open details recreated by polling have no disclosure entrance', () => {
  const env=setup({intersection:false});env.flush();const details=new env.Element('recreated');details.open=true;details.tagName='DETAILS';details.children=[new env.Element('content')];
  const count=env.animations.length;env.documentCallbacks.toggle({target:details});assert.equal(env.animations.length,count);
});
