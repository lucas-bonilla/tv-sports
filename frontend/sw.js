const CACHE = "sports-tv-v3";
const STATIC = ["/", "/index.html", "/style.css", "/app.js", "/manifest.json"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
});

self.addEventListener("fetch", e => {
  // Network-first for API, cache-first for static assets
  if (e.request.url.includes("/events")) {
    e.respondWith(
      fetch(e.request)
        .then(res => res)
        .catch(() => new Response(JSON.stringify({ error: "Offline" }), {
          headers: { "Content-Type": "application/json" }
        }))
    );
  } else {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request))
    );
  }
});
