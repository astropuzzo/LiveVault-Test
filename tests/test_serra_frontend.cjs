const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

async function exercise(userAgent = '') {
  const elements = new Map(), observers = [], handlers = {}, players = [], posts = [];
  function element(id) {
    if (!elements.has(id)) {
      const attributes = {}, events = {};
      elements.set(id, {
        id, events, paused:true, readyState:0, currentTime:0, value:id === 'serraWindow' ? '24' : '1',
        classList:{toggle(){}}, addEventListener(name, fn){events[name]=fn;},
        getAttribute(name){return attributes[name] || null;}, removeAttribute(name){delete attributes[name];},
        setAttribute(name,value){attributes[name]=value;},
        get src(){return attributes.src;}, set src(value){attributes.src=value;},
        closest(){return {classList:{toggle(){}}};}, pause(){this.paused=true;}, load(){},
        play(){this.paused=false;return Promise.resolve();}, canPlayType(){return '';},
      });
    }
    return elements.get(id);
  }
  class Hls {
    static Events = {MEDIA_ATTACHED:'attached',MANIFEST_PARSED:'manifest',ERROR:'error'};
    static isSupported(){return true;}
    constructor(){this.events={};this.stops=0;this.starts=0;players.push(this);}
    loadSource(url){this.url=url;} attachMedia(){this.events.attached?.();} on(event,callback){this.events[event]=callback;}
    stopLoad(){this.stops++;} startLoad(){this.starts++;}
    destroy(){this.destroyed=true;}
  }
  const document = {hidden:false,activeElement:null,getElementById:element,addEventListener(name,fn){handlers[name]=fn;}};
  const data={settings:{enabled:true,interval_seconds:15,quota_gb:4},storage_online:true,used_bytes:1,free_bytes:1e9,
    frames:3,pending_frames:1,blocks:1,last_compact:1,width:2304,height:1296};
  const context=vm.createContext({document,Hls,navigator:{userAgent},AbortController,Date,Intl,URLSearchParams,console,
    setTimeout(){return 1;},clearTimeout(){},
    IntersectionObserver:class {constructor(fn){this.fn=fn;observers.push(this);}observe(){}},
    async fetch(url,options={}){if(options.method==='POST')posts.push(url);return {ok:true,status:200,json:async()=>url.includes('preview-info')?{url:'/api/serra/preview.mp4?first=1&last=2&v=1',duration:3}:data};},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../control-panel/static/serra.js'),'utf8'),context);
  const api=context.OpenAstroSerra;
  api.setView('serra');api.setAuthenticated(true);api.update({csrf:'test-csrf'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(element('serraLive').src,undefined);
  assert.equal(players.length,0);
  observers[0].fn([{target:element('serraLive'),isIntersecting:true},{target:element('serraVideo'),isIntersecting:true}]);
  assert.match(element('serraLive').src,/^\/api\/serra\/live/);
  if (userAgent) {
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(players.length,0);
    assert.match(element('serraVideo').src,/^\/api\/serra\/preview\.mp4\?first=1&last=2&v=1$/);
    document.hidden=true;handlers.visibilitychange();
    assert.equal(element('serraVideo').src,undefined);
    assert.equal(element('serraLive').src,undefined);
    assert.deepEqual(posts,[]);
    return;
  }
  assert.equal(players.length,1);
  assert.match(players[0].url,/playlist\.m3u8/);
  // Firefox Android may pause/play during attachment, before media data exists.
  element('serraVideo').events.pause();
  element('serraVideo').events.play();
  assert.equal(players[0].stops,0);
  assert.equal(players[0].starts,0);
  // A pause after the first frame still suspends buffering.
  element('serraVideo').readyState=2;
  element('serraVideo').events.pause();
  assert.equal(players[0].stops,1);
  element('serraVideo').events.play();
  assert.equal(players[0].starts,1);
  observers[0].fn([{target:element('serraLive'),isIntersecting:false},{target:element('serraVideo'),isIntersecting:false}]);
  assert.equal(element('serraLive').src,undefined);
  assert.equal(players[0].destroyed,true);
  observers[0].fn([{target:element('serraLive'),isIntersecting:true}]);
  element('serraRetry').events.click();
  assert.equal(element('serraLive').src,undefined);
  document.hidden=true;handlers.visibilitychange();
  assert.equal(element('serraLive').src,undefined);
  api.setAuthenticated(false);
  assert.equal(element('serraLive').src,undefined);
  assert.deepEqual(posts,[]);
}

test('greenhouse streams stop offscreen, when hidden and on logout; capture is independent', () => exercise());
test('Firefox Android plays one continuous MP4 and suspends it when hidden', () => exercise('Mozilla/5.0 (Android 15; Mobile; rv:145.0) Gecko/145.0 Firefox/145.0'));
