// The Bot Barn: keeps the app opening offline. Numbers are always fetched fresh when online.
const CACHE = "bot-barn-v1";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  // network first, fall back to the last copy (so the last numbers still show offline)
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok) { const copy = r.clone(); const key = url.pathname; caches.open(CACHE).then(c => c.put(key, copy)); }
    return r;
  }).catch(() => caches.match(url.pathname).then(m => m || caches.match("index.html"))));
});
