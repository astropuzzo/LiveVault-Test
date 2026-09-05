const CACHE='livevault-shell-v3.0.0-redesign4';
const SHELL=['/static/style.css','/static/ui-fixes.css','/static/dashboard-tuning.css','/static/pulse-axis.css','/static/icons.svg','/static/app.js','/static/operations.js','/static/workspace.js','/static/ui.js','/static/pulse-tuning.js','/static/icon.svg','/manifest.webmanifest'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL)).then(()=>self.skipWaiting())));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith('livevault-shell-')&&key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  if(event.request.method!=='GET'||url.origin!==self.location.origin||!SHELL.includes(url.pathname))return;
  event.respondWith(fetch(event.request).then(response=>{
    if(response.ok){const copy=response.clone();event.waitUntil(caches.open(CACHE).then(cache=>cache.put(url.pathname,copy)));}
    return response;
  }).catch(()=>caches.match(url.pathname).then(response=>response||Response.error())));
});
