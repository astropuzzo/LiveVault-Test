const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('control-panel/static/app.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function progressContext() {
  const pending = [];
  let now = 100000;
  const ctx = vm.createContext({
    signedIn: true, csrf: 'test', document: {hidden: false},
    mediaPlayerUuid: 'USB-1', mediaPlayerPath: 'a.mp4', mediaPlaybackBase: 0, mediaPlaybackDuration: 1000,
    Date: {now: () => now}, AbortSignal: {timeout: () => undefined}, showLogin: () => {},
    fetch: (url, options) => new Promise((resolve, reject) => pending.push({url, body: JSON.parse(options.body), resolve, reject})),
  });
  vm.runInContext(source.slice(source.indexOf('const MEDIA_PROGRESS_INTERVAL_MS'), source.indexOf('function mediaProfileCard(')), ctx);
  return {ctx, pending, advance: value => { now += value; }};
}

test('progress saves are throttled and never overlap for the same file', async () => {
  const {ctx, pending, advance} = progressContext();
  const first = ctx.saveMediaProgress({currentTime: 50, duration: 1000});
  const overlap = ctx.saveMediaProgress({currentTime: 51, duration: 1000});
  assert.equal(pending.length, 1);
  pending[0].resolve({ok: true, status: 200});
  await Promise.all([first, overlap]);
  await ctx.saveMediaProgress({currentTime: 55, duration: 1000});
  assert.equal(pending.length, 1);
  advance(10000);
  const next = ctx.saveMediaProgress({currentTime: 60, duration: 1000});
  assert.equal(pending.length, 2);
  pending[1].resolve({ok: true, status: 200});
  await next;
});

test('forced final progress coalesces and retains the original file across a player switch', async () => {
  const {ctx, pending} = progressContext();
  const first = ctx.saveMediaProgress({currentTime: 50, duration: 1000});
  const final = ctx.saveMediaProgress({currentTime: 70, duration: 1000}, true);
  const newest = ctx.saveMediaProgress({currentTime: 80, duration: 1000}, true);
  ctx.mediaPlayerPath = 'b.mp4'; ctx.mediaPlaybackBase = 400;
  assert.equal(pending.length, 1);
  pending[0].resolve({ok: true, status: 200});
  await tick();
  assert.equal(pending.length, 2);
  assert.deepEqual(pending[1].body, {uuid:'USB-1', path:'a.mp4', position:80, duration:1000});
  pending[1].resolve({ok: true, status: 200});
  await Promise.all([first, final, newest]);
});

test('hidden pages skip periodic progress but preserve the explicit final save', async () => {
  const {ctx, pending} = progressContext();
  ctx.document.hidden = true;
  await ctx.saveMediaProgress({currentTime: 50, duration: 1000});
  assert.equal(pending.length, 0);
  const final = ctx.saveMediaProgress({currentTime: 50, duration: 1000}, true);
  assert.equal(pending.length, 1);
  pending[0].resolve({ok: true, status: 200});
  await final;
});

test('failed progress can retry and nonfinite durations are never posted', async () => {
  const {ctx, pending} = progressContext();
  const first = ctx.saveMediaProgress({currentTime: 50, duration: 1000});
  pending[0].reject(new Error('offline'));
  await first;
  const retry = ctx.saveMediaProgress({currentTime: 51, duration: 1000});
  assert.equal(pending.length, 2);
  pending[1].resolve({ok: true, status: 200});
  await retry;
  ctx.mediaPlaybackDuration = 0;
  await ctx.saveMediaProgress({currentTime: 52, duration: Infinity}, true);
  assert.equal(pending.length, 2);
});

function homeContext() {
  const pending = [], rendered = [];
  const ctx = vm.createContext({
    signedIn: true, currentView: 'media', document: {hidden:false},
    mediaHomeLoadedAt:0, mediaHomeRequest:null, mediaHomeRefreshQueued:false,
    Date: {now:()=>100000}, AbortSignal: {timeout:()=>undefined}, showLogin:()=>{},
    renderMediaHome: payload => rendered.push(payload),
    fetch: url => new Promise((resolve,reject)=>pending.push({url,resolve,reject})),
  });
  vm.runInContext(source.slice(source.indexOf('async function loadMediaHome('), source.indexOf('function mediaCategoryLabel(')), ctx);
  return {ctx, pending, rendered};
}

test('media home stops hidden polling and coalesces a refresh burst', async () => {
  const {ctx,pending,rendered} = homeContext();
  ctx.document.hidden=true;
  await ctx.loadMediaHome(true);
  assert.equal(pending.length,0);
  ctx.document.hidden=false;
  const first=ctx.loadMediaHome(), second=ctx.loadMediaHome();
  assert.equal(pending.length,1);
  pending[0].resolve({ok:true,status:200,json:async()=>({ok:true,id:1})});
  await Promise.all([first,second]);
  assert.equal(rendered.length,1);
  await ctx.loadMediaHome();
  assert.equal(pending.length,1);
});

test('media home failed refresh retries without waiting and does not render after hiding', async () => {
  const {ctx,pending,rendered} = homeContext();
  const failed=ctx.loadMediaHome();
  pending[0].reject(new Error('offline'));
  await failed;
  const next=ctx.loadMediaHome();
  assert.equal(pending.length,2);
  ctx.document.hidden=true;
  pending[1].resolve({ok:true,status:200,json:async()=>({ok:true,id:2})});
  await next;
  assert.equal(rendered.length,0);
});

test('media mutations queue one fresh home snapshot after an older in-flight request', async () => {
  const {ctx,pending,rendered} = homeContext();
  const first=ctx.loadMediaHome();
  const mutation=ctx.loadMediaHome(true), anotherMutation=ctx.loadMediaHome(true);
  pending[0].resolve({ok:true,status:200,json:async()=>({ok:true,favorite:false})});
  await tick();
  assert.equal(pending.length,2);
  assert.equal(rendered.length,0);
  pending[1].resolve({ok:true,status:200,json:async()=>({ok:true,favorite:true})});
  await Promise.all([first,mutation,anotherMutation]);
  assert.equal(pending.length,2);
  assert.equal(rendered.length,1);
  assert.equal(rendered[0].favorite,true);
});

function mediaSelectionContext() {
  const elements = new Map(), buttons = new Map(), libraryRenders = [], directoryLoads = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {textContent:'', innerHTML:'', style:{}, disabled:false});
    return elements.get(id);
  };
  const ctx = vm.createContext({
    mediaUuid:'NVME', mediaPath:'old', mediaSignature:'old', currentView:'media',
    mediaItems:[{path:'old.mp4', name:'old.mp4'}], mediaItemsUuid:'NVME', mediaLibrary:{uuid:'NVME'}, mediaView:'grid',
    $:element,
    $$: selector => selector === '.media-select' ? [...buttons.values()] : [],
    bytes: value => String(value), escapeHtml: value => String(value),
    mediaFilteredItems: () => ctx.mediaItems,
    mediaCard: item => `FILE:${item.path}`,
    renderMediaLibrary: value => {ctx.mediaLibrary = value; libraryRenders.push(value);},
    loadMediaDirectory: path => directoryLoads.push({uuid:ctx.mediaUuid,path}),
    loadMediaLibrary: () => {}, openConfirm: () => {},
  });
  vm.runInContext(source.slice(source.indexOf('function renderMediaFiles()'), source.indexOf('function renderMediaLibrary(')), ctx);
  vm.runInContext(source.slice(source.indexOf('function renderMedia(media = {})'), source.indexOf('function selectView(')), ctx);
  const addButton = uuid => {
    const button={dataset:{mediaUuid:uuid}, addEventListener:(_,handler)=>{button.click=handler;}};
    buttons.set(uuid,button); return button;
  };
  return {ctx, element, addButton, libraryRenders, directoryLoads};
}

test('SMB hint and access path follow the mounted library and selection clears old file identity', () => {
  const {ctx,element,addButton,directoryLoads} = mediaSelectionContext();
  const usb = addButton('USB'), nvme = addButton('NVME');
  const media = {smb_path:'\\\\OPENASTRO\\Media', devices:[
    {uuid:'NVME', mounted:true, smb_path:'\\\\OPENASTRO\\NVMeMedia', fstype:'exfat'},
    {uuid:'USB', mounted:true, smb_path:'\\\\OPENASTRO\\Media', fstype:'ext4'},
  ]};
  ctx.renderMedia(media);
  assert.equal(element('#mediaUploadSmbPath').textContent,'\\\\OPENASTRO\\NVMeMedia');
  assert.equal(element('#mediaSmbPath').textContent,'\\\\OPENASTRO\\NVMeMedia');
  assert.equal(element('#mediaUploadCopySmb').disabled,false);
  const initialLoads=directoryLoads.length;
  usb.click();
  assert.equal(element('#mediaUploadSmbPath').textContent,'\\\\OPENASTRO\\Media');
  assert.equal(ctx.mediaItems.length,0);
  assert.equal(ctx.mediaItemsUuid,'USB');
  assert.equal(ctx.mediaLibrary,null);
  assert.equal(element('#mediaFiles').innerHTML.includes('old.mp4'),false);
  assert.equal(directoryLoads.length,initialLoads+1);
  nvme.click();
  assert.equal(element('#mediaUploadSmbPath').textContent,'\\\\OPENASTRO\\NVMeMedia');
  assert.equal(ctx.mediaItemsUuid,'NVME');
});

test('offline selection switches the SMB target and no mounted library disables copying', () => {
  const {ctx,element}=mediaSelectionContext();
  ctx.renderMedia({devices:[{uuid:'NVME',mounted:false,fstype:'exfat'}, {uuid:'USB',mounted:true,fstype:'ext4'}]});
  assert.equal(ctx.mediaUuid,'USB');
  assert.equal(element('#mediaUploadSmbPath').textContent,'\\\\OPENASTRO\\Media');
  ctx.renderMedia({devices:[{uuid:'USB',mounted:false,fstype:'ext4'}]});
  assert.equal(ctx.mediaUuid,'');
  assert.equal(element('#mediaUploadCopySmb').disabled,true);
  assert.equal(element('#mediaUploadSmbPath').textContent,'Seleziona un supporto online');
});

test('file renderer rejects another library identity while the current directory is loading', () => {
  const {ctx,element}=mediaSelectionContext();
  ctx.mediaUuid='USB';
  ctx.renderMediaFiles();
  assert.equal(element('#mediaFiles').innerHTML.includes('old.mp4'),false);
});

test('SMB copy uses the same selected-library path shown in the upload hint', async () => {
  const uploadSource=fs.readFileSync('control-panel/static/media-upload.js','utf8'), copied=[];
  let click;
  const ctx=vm.createContext({
    mediaUuid:'NVME', latestState:{media:{devices:[
      {uuid:'NVME',mounted:true,smb_path:'\\\\OPENASTRO\\NVMeMedia'},
      {uuid:'USB',mounted:true,smb_path:'\\\\OPENASTRO\\Media'},
    ]}},
    SMB_PATH:'\\\\OPENASTRO\\Media',
    navigator:{clipboard:{writeText:async path=>copied.push(path)}}, toast:()=>{},
    copySmb:{addEventListener:(_,handler)=>{click=handler;}},
  });
  vm.runInContext(uploadSource.slice(uploadSource.indexOf('  function selectedDevice()'), uploadSource.indexOf('  function syncTarget()')),ctx);
  vm.runInContext(uploadSource.slice(uploadSource.indexOf("  copySmb.addEventListener('click'"), uploadSource.indexOf('  routeState();')),ctx);
  await click();
  ctx.mediaUuid='USB';
  await click();
  assert.deepEqual(copied,['\\\\OPENASTRO\\NVMeMedia','\\\\OPENASTRO\\Media']);
});

test('a late directory reply cannot bind old-library files to the new selection', async () => {
  const pending=[], renders=[];
  const ctx=vm.createContext({
    mediaUuid:'NVME', mediaPath:'', mediaItems:[], mediaItemsUuid:'', mediaDirectoryRequest:0, mediaBusy:false,
    URLSearchParams, AbortSignal:{timeout:()=>undefined},
    $:()=>({innerHTML:'',textContent:'',dataset:{}}), showLogin:()=>{}, escapeHtml:String,
    renderMediaFiles:()=>renders.push({uuid:ctx.mediaItemsUuid,path:ctx.mediaPath}),
    fetch:()=>new Promise(resolve=>pending.push(resolve)),
  });
  vm.runInContext(source.slice(source.indexOf('async function loadMediaDirectory('), source.indexOf('function closeMediaPlayer()')),ctx);
  const old=ctx.loadMediaDirectory('old');
  ctx.mediaUuid='USB';
  const fresh=ctx.loadMediaDirectory('new');
  pending[1]({ok:true,status:200,json:async()=>({ok:true,path:'new',items:[{name:'fresh.mp4'}]})});
  await fresh;
  pending[0]({ok:true,status:200,json:async()=>({ok:true,path:'old',items:[{name:'stale.mp4'}]})});
  await old;
  assert.equal(ctx.mediaItemsUuid,'USB');
  assert.equal(ctx.mediaItems[0].name,'fresh.mp4');
  assert.deepEqual(renders,[{uuid:'USB',path:'new'}]);
});
