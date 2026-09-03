const RELEASE='2.8.12-r1';
const CACHE=`livevault-shell-${RELEASE}`;
const SHELL_PATHS=new Set(['/static/style.css','/static/enhancements.css','/static/app.js','/static/icon.svg','/manifest.webmanifest']);
const SHELL=[...SHELL_PATHS].map(path=>`${path}?v=${RELEASE}`);
self.addEventListener('install',event=>event.waitUntil(
  caches.open(CACHE).then(cache=>cache.addAll(SHELL)).then(()=>self.skipWaiting())
));
self.addEventListener('activate',event=>event.waitUntil(
  caches.keys()
    .then(keys=>Promise.all(keys.filter(key=>key.startsWith('livevault-shell-')&&key!==CACHE).map(key=>caches.delete(key))))
    .then(()=>self.clients.claim())
));
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  if(event.request.method!=='GET'||url.origin!==self.location.origin||url.pathname.startsWith('/api/')) return;
  event.respondWith(
    fetch(event.request,{cache:'no-store'})
      .then(response=>{
        if(response.ok&&SHELL_PATHS.has(url.pathname)){
          const copy=response.clone();
          event.waitUntil(caches.open(CACHE).then(cache=>cache.put(event.request,copy)));
        }
        return response;
      })
      .catch(async()=>{
        const exact=await caches.match(event.request);
        if(exact) return exact;
        if(SHELL_PATHS.has(url.pathname)){
          const fallback=await caches.open(CACHE).then(cache=>cache.match(`${url.pathname}?v=${RELEASE}`));
          if(fallback) return fallback;
        }
        return Response.error();
      })
  );
});
