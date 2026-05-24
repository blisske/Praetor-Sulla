// sw.js — KILL SWITCH (2026-05-23)
//
// Previously this was a NetworkFirst-for-nav + CacheFirst-for-assets
// service worker. After repeated cache-related deployment headaches
// (stale shell holding the old bundle hash, browsers refusing to
// re-fetch sw.js itself, demo-flow auth loops caused by stale JS),
// we've decided the PWA niceties aren't worth the operational pain
// during active SaaS development.
//
// This SW immediately:
//   1. Skips any installation steps (becomes active right away)
//   2. Claims all clients (so it takes control from any older SW)
//   3. Deletes ALL caches (evicts any previously-cached stale shell)
//   4. Unregisters itself (so the next visit has NO SW intercepting)
//
// Net effect on user: their existing OLD SW gets replaced by this
// suicide-pill SW on first visit, which then nukes itself + the
// caches. Subsequent visits hit the network directly, always loading
// the freshest bundle hash.

self.addEventListener('install', (e) => {
  // Activate immediately — don't wait for any clients to close
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    // 1. Wipe every cache this SW or its ancestors created
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => caches.delete(k)));
    // 2. Take control of any open tabs immediately
    await self.clients.claim();
    // 3. Unregister this SW so future page loads bypass it entirely
    await self.registration.unregister();
    // 4. Reload all tabs once so they pick up the un-SW'd state
    const clients = await self.clients.matchAll();
    clients.forEach((c) => c.navigate(c.url));
  })());
});

// No fetch handler — anything that slips through during the brief
// "alive but unregistering" window goes straight to network.
