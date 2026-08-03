// Minimal SW for installability on Howlscan public wallet
// Bump CACHE when /app HTML/JS balance logic changes so offline shell updates.
const CACHE = "howl-public-wallet-v18";
self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(["/app", "/manifest.webmanifest"])).then(() => self.skipWaiting())
  );
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});
// Network-first for app shell + API so SOL balances and wallet UI stay fresh
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  const isApp = url.pathname === "/app" || url.pathname.startsWith("/app/");
  const isApi = url.pathname.startsWith("/api/");
  if(isApi || isApp || e.request.mode === "navigate"){
    e.respondWith(
      fetch(e.request).then((r) => {
        if(isApp && r.ok){
          const copy = r.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(()=>{});
        }
        return r;
      }).catch(() => caches.match(e.request))
    );
    return;
  }
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
