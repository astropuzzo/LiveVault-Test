const CACHE = "openastro-control-v3.5.1-biglie1";
const SHELL = ["/app.css", "/apps.css", "/apps.js", "/biglie-hunt.js", "/media-upload.css", "/torrent-manager.css", "/diagnostics.css", "/diagnostics.js", "/app.js", "/torrent-manager.js", "/fonts/mona-sans-latin-wght.woff2", "/media-upload.js", "/hls.min.js", "/icon.svg", "/manifest.webmanifest"];
self.addEventListener("install", event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith("openastro-control-") && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim())));
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || !SHELL.includes(url.pathname)) return;
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) { const copy = response.clone(); event.waitUntil(caches.open(CACHE).then(cache => cache.put(url.pathname, copy))); }
    return response;
  }).catch(() => caches.match(url.pathname).then(response => response || Response.error())));
});
// Panel notices (web_push.py), shown and opened the way InstaVault's service worker does it.
self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (error) { data = {}; }
  event.waitUntil(self.registration.showNotification(data.title || "OpenAstro", {body: data.body || "", data: {url: data.url || "/"}, lang: "it"}));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.registration.scope).href;
  event.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(windows => {
    const open = windows.find(w => w.url.startsWith(self.registration.scope));
    if (!open) return self.clients.openWindow(url);
    return open.focus().then(w => w.navigate ? w.navigate(url) : w).catch(() => self.clients.openWindow(url));
  }));
});
