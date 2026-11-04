const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
async function exercise(userAgent='',debug=false){
 const elements=new Map(),observers=[],handlers={},timers=new Map(),requests=[],reports=[],posts=[];let next=0;
 function element(id){if(!elements.has(id)){const attrs={},events={};elements.set(id,{id,events,paused:true,readyState:0,currentTime:0,duration:3,networkState:1,videoWidth:2304,videoHeight:1296,loads:0,
 value:({serraWindow:'24',serraArchive:'',serraRotation:'0',serraCleanupPeriod:'24'})[id]??'1',classList:{toggle(){},remove(){}},style:{setProperty(){}},
 addEventListener(k,fn){const prior=events[k];events[k]=prior?(...a)=>{prior(...a);fn(...a);}:fn;},getAttribute(k){return attrs[k]||null;},removeAttribute(k){delete attrs[k];},setAttribute(k,v){attrs[k]=v;},get src(){return attrs.src;},set src(v){attrs.src=v;},closest(){return{classList:{toggle(){}}};},pause(){this.paused=true;},load(){this.loads++;},play(){this.paused=false;return Promise.resolve();},buffered:{length:1,start(){return 0;},end(){return .083333;}},getVideoPlaybackQuality(){return{totalVideoFrames:36,droppedVideoFrames:0};}});}return elements.get(id);}
 const document={hidden:false,activeElement:null,body:{classList:{remove(){},add(){}}},getElementById:element,addEventListener(k,fn){handlers[k]=fn;}};
 const data={settings:{enabled:true,interval_seconds:15,quota_gb:4,rotation_degrees:0},storage_online:true,used_bytes:1,free_bytes:1e9,frames:3,pending_frames:1,blocks:1,last_compact:1,width:2304,height:1296};
 const context=vm.createContext({document,navigator:{userAgent},location:{search:debug?'?serraDebug=1':''},AbortController,AbortSignal,Date,Intl,URLSearchParams,console,
 setTimeout(fn,ms){timers.set(++next,{fn,ms});return next;},clearTimeout(id){timers.delete(id);},IntersectionObserver:class{constructor(fn){this.fn=fn;observers.push(this);}observe(){}},
 async fetch(url,options={}){requests.push(url);if(options.method==='POST'){const body=JSON.parse(options.body);if(body.action==='playback-report')reports.push(body.report);else posts.push(body);}return{ok:true,status:200,json:async()=>url.includes('preview-info')?{url:'/api/serra/preview.mp4?first=1&last=2&v=1',duration:3}:data};}});
 vm.runInContext(fs.readFileSync(process.env.SERRA_TEST_SOURCE||path.join(__dirname,'../control-panel/static/serra.js'),'utf8'),context);
 const api=context.OpenAstroSerra,video=element('serraVideo');api.setView('serra');api.setAuthenticated(true);api.update({csrf:'test-csrf'});await new Promise(setImmediate);
 assert.equal(video.src,undefined);assert.equal(element('serraLive').src,undefined);
 observers[0].fn([{target:element('serraLive'),isIntersecting:true},{target:video,isIntersecting:true}]);await new Promise(setImmediate);
 assert.match(video.src,/^\/api\/serra\/preview\.mp4/);assert.equal(requests.some(url=>url.includes('playlist.m3u8')),false,'all browsers must use the same indexed MP4');
 video.readyState=2;video.events.loadedmetadata();assert.equal(video.preload,'auto');assert.equal(video.paused,true);assert.match(element('serraBufferStatus').textContent,/Preparazione video/);
 video.paused=false;video.events.play();assert.equal(video.paused,true,'early Play waits for a useful buffer');
 video.buffered.end=()=>1;video.events.progress();assert.equal(video.paused,true);video.buffered.end=()=>3;video.events.progress();assert.equal(video.paused,false);assert.equal(element('serraBufferStatus').textContent,'');
 video.duration=30;video.currentTime=4;video.buffered.end=()=>4.1;video.events.waiting();assert.equal(video.paused,true);video.buffered.end=()=>12;video.events.progress();assert.equal(video.paused,false,'underruns recover without another Play');
 video.duration=3;video.currentTime=1.5;video.buffered.end=()=>3;
 const app=fs.readFileSync(path.join(__dirname,'../control-panel/static/app.js'),'utf8');Object.assign(context,{refreshBusy:false,signedIn:true,latestState:{},$:element,render(v){api.update(v);}});
 vm.runInContext(app.slice(app.indexOf('async function refresh() {'),app.indexOf('function showLogin() {'))+app.slice(app.indexOf('function hideLogin() {'),app.indexOf('function toast(')),context);
 const loads=video.loads,src=video.src,live=element('serraLive').src,count=requests.filter(url=>url.startsWith('/api/serra/')).length;for(let i=0;i<3;i++)await context.refresh();
 assert.equal(video.loads,loads);assert.equal(video.src,src);assert.equal(video.currentTime,1.5);assert.equal(video.paused,false);assert.equal(element('serraLive').src,live);assert.equal(requests.filter(url=>url.startsWith('/api/serra/')).length,count);
 if(debug){video.events.waiting();for(let i=0;i<8;i++){const[id,task]=[...timers].find(([,v])=>v.ms===3000);timers.delete(id);await task.fn();}assert.equal(reports.length,8);assert.equal(reports[0].decoded_frames,36);assert.equal(reports[0].events.some(v=>v.event==='waiting'),true);}else assert.equal([...timers.values()].some(v=>v.ms===3000),false,'normal viewing must not send diagnostics');
 video.duration=30;video.buffered.end=()=>1.5;video.events.waiting();assert.equal([...timers.values()].some(v=>v.ms===500),true);document.hidden=true;handlers.visibilitychange();assert.equal([...timers.values()].some(v=>v.ms===500),false);assert.equal(video.src,undefined);assert.equal(video.preload,'none');assert.equal(element('serraLive').src,undefined);api.setAuthenticated(false);assert.deepEqual(posts,[],'viewing never changes capture settings');
 return{api,context,data,elements,observers,handlers,requests,posts};
}
test('desktop buffers indexed MP4, survives panel polling and stops when hidden',()=>exercise());
test('Firefox Android uses the same player with bounded opt-in diagnostics',()=>exercise('Mozilla/5.0 (Android 17; Mobile; rv:159.0) Gecko/159.0 Firefox/159.0',true));
test('desktop diagnostics are available only when requested',()=>exercise('Mozilla/5.0 (Windows NT 10.0) Firefox/159.0',true));
