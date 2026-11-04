const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

async function exercise(userAgent = '') {
  const elements = new Map(), observers = [], handlers = {}, players = [], posts = [], requests = [], reports = [], timers = new Map();
  let timerId = 0;
  function element(id) {
    if (!elements.has(id)) {
      const attributes = {}, events = {};
      elements.set(id, {
        id, events, paused:true, readyState:0, currentTime:0, value:id === 'serraWindow' ? '24' : '1',
        classList:{toggle(){},remove(){}}, addEventListener(name, fn){const previous=events[name];events[name]=previous ? (...args)=>{previous(...args);fn(...args);} : fn;},
        duration:3,networkState:1,videoWidth:2304,videoHeight:1296,buffered:{length:1,start(){return 0;},end(){return 3;}},
        getVideoPlaybackQuality(){return {totalVideoFrames:36,droppedVideoFrames:0};},
        getAttribute(name){return attributes[name] || null;}, removeAttribute(name){delete attributes[name];},
        setAttribute(name,value){attributes[name]=value;},
        get src(){return attributes.src;}, set src(value){attributes.src=value;},
        loads:0, closest(){return {classList:{toggle(){}}};}, pause(){this.paused=true;}, load(){this.loads++;},
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
  const document = {hidden:false,activeElement:null,body:{classList:{remove(){},add(){}}},getElementById:element,addEventListener(name,fn){handlers[name]=fn;}};
  const data={settings:{enabled:true,interval_seconds:15,quota_gb:4},storage_online:true,used_bytes:1,free_bytes:1e9,
    frames:3,pending_frames:1,blocks:1,last_compact:1,width:2304,height:1296};
  const context=vm.createContext({document,Hls,navigator:{userAgent},AbortController,AbortSignal,Date,Intl,URLSearchParams,console,
    setTimeout(fn,ms){timers.set(++timerId,{fn,ms});return timerId;},clearTimeout(id){timers.delete(id);},
    IntersectionObserver:class {constructor(fn){this.fn=fn;observers.push(this);}observe(){}},
    async fetch(url,options={}){requests.push(url);if(options.method==='POST'){const body=JSON.parse(options.body);if(body.action==='playback-report')reports.push(body.report);else posts.push(url);}return {ok:true,status:200,json:async()=>url.includes('preview-info')?{url:'/api/serra/preview.mp4?first=1&last=2&v=1',duration:3}:data};},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../control-panel/static/serra.js'),'utf8'),context);
  const api=context.OpenAstroSerra;
  api.setView('serra');api.setAuthenticated(true);api.update({csrf:'test-csrf'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(element('serraLive').src,undefined);
  assert.equal(players.length,0);
  observers[0].fn([{target:element('serraLive'),isIntersecting:true},{target:element('serraVideo'),isIntersecting:true}]);
  assert.match(element('serraLive').src,/^\/api\/serra\/live/);
  await new Promise(resolve=>setImmediate(resolve));
  // Run the main panel's actual refresh/login integration, not an isolated player.
  const app=fs.readFileSync(path.join(__dirname,'../control-panel/static/app.js'),'utf8');
  Object.assign(context, {refreshBusy:false,signedIn:true,latestState:{},$:element,render(value){api.update(value);}});
  vm.runInContext(app.slice(app.indexOf('async function refresh() {'),app.indexOf('function showLogin() {')) +
    app.slice(app.indexOf('function hideLogin() {'),app.indexOf('function toast(')),context);
  const video=element('serraVideo'), loads=video.loads, source=video.src, liveSource=element('serraLive').src;
  const mediaRequests=requests.filter(url=>url.startsWith('/api/serra/')).length;
  video.currentTime=1.5; video.paused=false;
  for(let i=0;i<3;i++) await context.refresh();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(video.loads,loads,'panel telemetry must not reload the video');
  assert.equal(video.src,source);
  assert.equal(element('serraLive').src,liveSource);
  assert.equal(video.currentTime,1.5);
  assert.equal(video.paused,false);
  assert.equal(requests.filter(url=>url.startsWith('/api/serra/')).length,mediaRequests);
  assert.equal(players.some(player=>player.destroyed),false);
  if (userAgent) {
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(players.length,0);
    assert.match(element('serraVideo').src,/^\/api\/serra\/preview\.mp4\?first=1&last=2&v=1$/);
    video.events.waiting();
    for(let i=0;i<8;i++) {
      const [id,task]=[...timers].find(([,value])=>value.ms===3000);
      timers.delete(id); await task.fn();
    }
    assert.equal(reports.length,8);
    assert.equal(reports[0].position,1.5);
    assert.equal(reports[0].decoded_frames,36);
    assert.deepEqual(JSON.parse(JSON.stringify(reports[0].buffered)),[[0,3]]);
    assert.equal(reports[0].events.at(-1).event,'waiting');
    assert.equal([...timers.values()].some(value=>value.ms===3000),false);
    document.hidden=true;handlers.visibilitychange();
    assert.equal(element('serraVideo').src,undefined);
    assert.equal(element('serraLive').src,undefined);
    api.setAuthenticated(false);
    assert.equal(element('serraVideo').src,undefined);
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
