// YojanaSaathi service worker: makes the app installable and opens the shell offline.
// Privacy: case data is NEVER cached. /api/* (the agent) and /voice/* (the voice bot) and
// anything that is not a GET always go to the network.

const CACHE = "ys-shell-v1";
const SHELL = ["/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png", "/icons/icon.svg"];
const FONT_HOSTS = ["fonts.googleapis.com", "fonts.gstatic.com"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function cacheFirst(request) {
  const hit = await caches.match(request);
  if (hit) return hit;
  const res = await fetch(request);
  if (res.ok || res.type === "opaque") (await caches.open(CACHE)).put(request, res.clone());
  return res;
}

async function networkFirst(request, fallback) {
  try {
    return await fetch(request);
  } catch (e) {
    const hit = (await caches.match(request)) || (fallback && (await caches.match(fallback)));
    if (hit) return hit;
    throw e;
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (FONT_HOSTS.includes(url.hostname)) {
    event.respondWith(cacheFirst(request));
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/voice/")) return;
  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, "/"));
    return;
  }
  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/")) {
    event.respondWith(cacheFirst(request)); // hashed build files and icons
    return;
  }
  event.respondWith(networkFirst(request));
});
