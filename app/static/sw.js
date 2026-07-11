// Service worker tối thiểu cho PWA: network-first mọi GET (không bao giờ
// serve bản cũ khi có mạng), cache lại làm fallback khi offline. /ws không đụng.
const CACHE = "manju-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/ws")) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        // Chỉ cache shell app (HTML/JS/manifest) — API và audio thì không.
        const cacheable = url.pathname === "/" || url.pathname.startsWith("/static/") || url.pathname === "/sw.js";
        if (res.ok && cacheable){
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
