/**
 * INK2Insights Service Worker — Phase 4 Offline Queue
 * =====================================================
 * Strategy:
 *  - Cache-first for static assets (HTML, CSS, JS, fonts, images).
 *  - Network-first for API calls, with automatic retry on restoration.
 *  - Background Sync (if supported) for queued /api/extract POSTs.
 *
 * Sync tag: 'ink2i-offline-sync'
 *
 * NOTE: This SW intentionally does NOT cache patient image data.
 *       Raw image blobs are encrypted and stored in IndexedDB by
 *       offline-queue.js — this SW only handles asset caching and
 *       triggers the sync event so queue-draining can run.
 */

const CACHE_VERSION = 'ink2i-v1';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/js/app.js',
  '/js/api.js',
  '/js/scanner.js',
  '/js/offline-queue.js',
  '/css/styles.css',
];

// ── Install: pre-cache static shell ─────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => {
      // Cache what we can; ignore failures for assets that may not exist yet
      return Promise.allSettled(
        STATIC_ASSETS.map((url) => cache.add(url).catch(() => {}))
      );
    }).then(() => self.skipWaiting())
  );
});

// ── Activate: remove stale caches ───────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== CACHE_VERSION)
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: cache-first for assets, network-first for API ────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Never intercept non-GET or API calls (handled by offline-queue.js)
  if (request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        // Cache successful same-origin GET responses
        if (
          response.ok &&
          response.type === 'basic' &&
          !url.pathname.includes('socket')
        ) {
          const clone = response.clone();
          caches.open(CACHE_VERSION).then((c) => c.put(request, clone));
        }
        return response;
      }).catch(() => {
        // Offline fallback: serve root shell for navigation requests
        if (request.mode === 'navigate') {
          return caches.match('/index.html');
        }
        return new Response('Offline', { status: 503 });
      });
    })
  );
});

// ── Background Sync: drain the offline queue ────────────────────────────────
self.addEventListener('sync', (event) => {
  if (event.tag === 'ink2i-offline-sync') {
    event.waitUntil(notifyClientsToSync());
  }
});

/**
 * Post a message to all controlled client windows so they can
 * run the queue-drain logic from offline-queue.js.
 * The actual drain (decryption + fetch) must happen in the window
 * context because: (a) WebCrypto keys are non-transferable, and
 * (b) FormData with Blobs cannot be reconstructed in the SW.
 */
async function notifyClientsToSync() {
  const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const client of clients) {
    client.postMessage({ type: 'INK2I_DRAIN_QUEUE' });
  }
}

// ── Push: online restoration notification (fallback for no-SW-sync browsers) ─
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'PING') {
    event.source.postMessage({ type: 'PONG' });
  }
});
