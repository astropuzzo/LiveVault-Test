const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'app/static/motion.js'), 'utf8');
function setup({reduced = false, intersection = true, animated = true} = {}) {
  const callbacks = {}, documentCallbacks = {}, frames = [], animations = [], nodes = [];
  const media = {matches: reduced, addEventListener: (_name, callback) => { media.change = callback; }};
  class Element {
    constructor(id) { this.id = id; this.hidden = false; this.rects = [{}]; this.dataset = {}; }
    getClientRects() { return this.rects; }
    animate(keyframes, timing) {
      let reject;
      const item = {node: this, keyframes, timing, cancelled: false, finished: new Promise((_resolve, fail) => { reject = fail; }), cancel() { this.cancelled = true; reject(new Error('cancelled')); }};
      animations.push(item); return item;
    }
  }
  if (!animated) delete Element.prototype.animate;
  const body = new Element('body'), app = new Element('app'), panels = [new Element('libraryView')]; panels[0].hidden=true;
  const document = {hidden: false, body, getElementById: id => id === 'app' ? app : null, querySelectorAll: selector => selector === '[data-motion]' ? nodes : panels, addEventListener: (name, callback) => { documentCallbacks[name] = callback; }};
  let mutations, intersections;
  class MutationObserver {
    constructor(callback) { mutations = this; this.callback = callback; this.targets = []; }
    observe(target, options) { this.targets.push({target, options}); this.disconnected = false; }
    disconnect() { this.disconnected = true; }
  }
  class IntersectionObserver {
    constructor(callback) { intersections = this; this.callback = callback; this.targets = new Set(); }
    observe(node) { this.targets.add(node); }
    unobserve(node) { this.targets.delete(node); }
    disconnect() { this.targets.clear(); this.disconnected = true; }
  }
  const location = {pathname: '/', hash: ''};
  const window = {matchMedia: () => media, addEventListener: (name, callback) => { callbacks[name] = callback; }, requestAnimationFrame: callback => { frames.push(callback); }};
  nodes.push(new Element('hero'));
  vm.runInNewContext(source, {window, document, location, Element, MutationObserver, IntersectionObserver: intersection ? IntersectionObserver : undefined});
  const flush = () => { while (frames.length) frames.shift()(); };
  return {nodes, panels, animations, media, document, callbacks, documentCallbacks, location, Element, flush, frames, mutations, intersections};
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
  assert.equal(env.animations.length, 13);
  for (const animation of env.animations) { assert.ok(animation.timing.delay <= 220); assert.equal(animation.timing.duration, 560); }
  env.callbacks.hashchange(); env.flush(); assert.equal(env.animations.length, 13);
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
  env.panels[0].hidden=false;env.mutations.callback([]);env.flush();assert.equal(env.animations.length,2);
  assert.ok(env.mutations.targets.some(row=>row.target===env.panels[0]));
});
