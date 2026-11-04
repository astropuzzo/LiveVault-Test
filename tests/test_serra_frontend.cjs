const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

test('greenhouse streams stop offscreen, when hidden and on logout; capture is independent', async () => {
  const elements = new Map(), observers = [], handlers = {}, players = [], posts = [];
  function element(id) {
    if (!elements.has(id)) {
      const attributes = {}, events = {};
      elements.set(id, {
        id, events, paused:true, currentTime:0, value:id === 'serraWindow' ? '24' : '1',
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
    static Events = {MANIFEST_PARSED:'manifest',ERROR:'error'};
    static isSupported(){return true;}
    constructor(){players.push(this);}
    loadSource(url){this.url=url;} attachMedia(){} on(){} stopLoad(){} startLoad(){}
    destroy(){this.destroyed=true;}
  }
  const document = {hidden:false,activeElement:null,getElementById:element,addEventListener(name,fn){handlers[name]=fn;}};
  const data={settings:{enabled:true,interval_seconds:15,quota_gb:4},storage_online:true,used_bytes:1,free_bytes:1e9,
    frames:3,pending_frames:1,blocks:1,last_compact:1,width:2304,height:1296};
  const context=vm.createContext({document,Hls,AbortController,Date,Intl,URLSearchParams,console,
    setTimeout(){return 1;},clearTimeout(){},
    IntersectionObserver:class {constructor(fn){this.fn=fn;observers.push(this);}observe(){}},
    async fetch(url,options={}){if(options.method==='POST')posts.push(url);return {ok:true,status:200,json:async()=>data};},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../control-panel/static/serra.js'),'utf8'),context);
  const api=context.OpenAstroSerra;
  api.setView('serra');api.setAuthenticated(true);api.update({csrf:'test-csrf'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(element('serraLive').src,undefined);
  assert.equal(players.length,0);
  observers[0].fn([{target:element('serraLive'),isIntersecting:true},{target:element('serraVideo'),isIntersecting:true}]);
  assert.match(element('serraLive').src,/^\/api\/serra\/live/);
  assert.equal(players.length,1);
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
});
