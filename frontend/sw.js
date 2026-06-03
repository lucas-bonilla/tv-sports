const STATIC_CACHE = "sports-tv-static-v10";
const API_CACHE = "sports-tv-api-v1";
const STATIC = ["/", "/index.html", "/style.css", "/app.js", "/padel.js", "/manifest.json"];

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

// Network-first API endpoints: always fetch fresh, fall back to cache offline.
const NETWORK_FIRST_API = ["/api/events", "/api/padel/"];

self.addEventListener("fetch", e => {
  const isNetworkFirstApi = NETWORK_FIRST_API.some(p => e.request.url.includes(p));
  if (isNetworkFirstApi) {
    const isEvents = e.request.url.includes("/api/events");
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
          const body = isEvents
            ? { error: "Offline", events: [], date: "", scraped_at: new Date().toISOString() }
            : { error: "Offline" };
          return new Response(JSON.stringify(body), {
            status: 503,
            headers: { "Content-Type": "application/json" },
          });
        })
    );
  } else {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request))
    );
  }
});
