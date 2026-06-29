// Service Worker — 缓存前端资源和 API 响应
const CACHE = "silver-research-v2";
const API_CACHE = "silver-research-api-v2";

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) =>
      c.addAll(["/", "/index.html"])
    )
  );
  self.skipWaiting();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/paper/") && url.pathname.endsWith("/progress")) {
    return; // Don't cache progress polling
  }
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(networkFirst(e.request, API_CACHE));
    return;
  }
  e.respondWith(cacheFirst(e.request, CACHE));
});

async function cacheFirst(req, cacheName) {
  const cached = await caches.match(req);
  if (cached) return cached;
  const resp = await fetch(req);
  if (resp.ok) {
    const c = await caches.open(cacheName);
    c.put(req, resp.clone());
  }
  return resp;
}

async function networkFirst(req, cacheName) {
  try {
    const resp = await fetch(req);
    if (resp.ok) {
      const c = await caches.open(cacheName);
      c.put(req, resp.clone());
    }
    return resp;
  } catch {
    const cached = await caches.match(req);
    return cached || new Response(JSON.stringify({ error: "offline" }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }
}
