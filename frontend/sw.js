const STATIC_CACHE = "sports-tv-static-v5";
const API_CACHE = "sports-tv-api-v1";
const STATIC = ["/", "/index.html", "/style.css", "/app.js", "/manifest.json"];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(STATIC_CACHE).then(c => c.addAll(STATIC))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k !== STATIC_CACHE && k !== API_CACHE)
          .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  if (e.request.url.includes("/api/events")) {
    e.respondWith(
      fetch(e.request)
        .then(async res => {
          const clone = res.clone();
          try {
            const cache = await caches.open(API_CACHE);
            await cache.put(e.request, clone);
          } catch (err) {
            console.warn("[SW] Failed to cache API response:", err);
          }
          return res;
        })
        .catch(async () => {
          const cached = await caches.match(e.request, { cacheName: API_CACHE });
          if (cached) return cached;
          return new Response(
            JSON.stringify({ error: "Offline", events: [], date: "", scraped_at: new Date().toISOString() }),
            { status: 503, headers: { "Content-Type": "application/json" } }
          );
        })
    );
  } else {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request))
    );
  }
});
