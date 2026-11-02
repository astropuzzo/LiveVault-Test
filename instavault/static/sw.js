// Shows InstaVault notices; the page itself is never cached here.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (error) { data = {}; }
  event.waitUntil(self.registration.showNotification(data.title || 'InstaVault', {
    body: data.body || 'Novità nell’archivio', data: {url: data.url || './'}, lang: 'it'}));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || './', self.registration.scope).href;
  event.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(windows => {
    const open = windows.find(w => w.url.startsWith(self.registration.scope));
    if (!open) return self.clients.openWindow(url);
    return open.focus().then(w => w.navigate ? w.navigate(url) : w).catch(() => self.clients.openWindow(url));
  }));
});
