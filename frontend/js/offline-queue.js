/**
 * INK2Insights Offline Queue — Phase 4
 * ======================================
 * Encrypted IndexedDB scan queue with WebCrypto AES-GCM.
 *
 * Architecture
 * ─────────────
 *  ┌────────────────────┐    offline     ┌──────────────────────┐
 *  │   api.js           │ ──────────────▶│  OfflineQueue.enqueue│
 *  │ extractPages()     │                │  (encrypt + IDB put) │
 *  └────────────────────┘                └──────────────────────┘
 *         │ online                                │
 *         │                         SW sync event │ or network restore
 *         ▼                                       ▼
 *  ┌────────────────────┐            ┌─────────────────────────┐
 *  │  Flask /api/extract│◀───────────│  OfflineQueue.drain()   │
 *  │  (normal path)     │            │  (decrypt + fetch + del)│
 *  └────────────────────┘            └─────────────────────────┘
 *
 * Security model
 * ───────────────
 * • Each session generates a fresh AES-GCM-256 key stored only in
 *   sessionStorage (cleared on tab close — deliberate ephemeral scope).
 * • Per-entry 96-bit IV is randomly generated and stored alongside
 *   the ciphertext in IDB.
 * • Images are encrypted as ArrayBuffers; metadata (campId, etc.) is
 *   encrypted as JSON.  No plaintext PHI ever touches IDB.
 * • On drain, each entry is decrypted, submitted to /api/extract,
 *   then deleted from IDB regardless of server response (success or
 *   permanent 4xx).  Transient errors (5xx / network) leave the entry
 *   for the next drain cycle.
 *
 * DPDP compliance
 * ────────────────
 * • Queue entries automatically expire after MAX_AGE_MS (default 48 h).
 * • Expired entries are purged without upload on every drain call.
 * • purgeAll() is available for DPDP right-to-erasure requests.
 */

const DB_NAME = 'ink2i_offline_queue';
const DB_VERSION = 1;
const STORE_NAME = 'scan_queue';
const KEY_STORE = 'crypto_keys';

/** Maximum age a queued scan is retained before being purged (48 hours). */
const MAX_AGE_MS = 48 * 60 * 60 * 1000;

/** sessionStorage key for the base64-exported AES-GCM key. */
const SESSION_KEY_NAME = 'ink2i_queue_aes_key';

// ─────────────────────────────────────────────────────────────────────────────
// IndexedDB helpers
// ─────────────────────────────────────────────────────────────────────────────

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, {
          keyPath: 'id',
          autoIncrement: true,
        });
        store.createIndex('created_at', 'created_at', { unique: false });
        store.createIndex('camp_id', 'camp_id', { unique: false });
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

function idbPut(db, record) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const req = tx.objectStore(STORE_NAME).put(record);
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

function idbGetAll(db) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

function idbDelete(db, id) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const req = tx.objectStore(STORE_NAME).delete(id);
    req.onsuccess = () => resolve();
    req.onerror = (e) => reject(e.target.error);
  });
}

function idbCount(db) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const req = tx.objectStore(STORE_NAME).count();
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

function idbClear(db) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const req = tx.objectStore(STORE_NAME).clear();
    req.onsuccess = () => resolve();
    req.onerror = (e) => reject(e.target.error);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// WebCrypto AES-GCM helpers
// ─────────────────────────────────────────────────────────────────────────────

async function getOrCreateKey() {
  // Reuse within the same session tab
  const stored = sessionStorage.getItem(SESSION_KEY_NAME);
  if (stored) {
    try {
      const raw = Uint8Array.from(atob(stored), (c) => c.charCodeAt(0));
      return await crypto.subtle.importKey(
        'raw', raw, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']
      );
    } catch {
      // Fall through to generate new
    }
  }

  const key = await crypto.subtle.generateKey(
    { name: 'AES-GCM', length: 256 },
    true,
    ['encrypt', 'decrypt']
  );

  // Export and persist for the lifetime of the browser tab
  const exported = await crypto.subtle.exportKey('raw', key);
  const b64 = btoa(String.fromCharCode(...new Uint8Array(exported)));
  try { sessionStorage.setItem(SESSION_KEY_NAME, b64); } catch { /* quota */ }

  return key;
}

async function encrypt(key, plaintextBuffer) {
  const iv = crypto.getRandomValues(new Uint8Array(12)); // 96-bit IV
  const ciphertext = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    key,
    plaintextBuffer
  );
  return { iv: Array.from(iv), ciphertext: Array.from(new Uint8Array(ciphertext)) };
}

async function decrypt(key, iv, ciphertext) {
  const ivBuf = new Uint8Array(iv);
  const ctBuf = new Uint8Array(ciphertext);
  return crypto.subtle.decrypt({ name: 'AES-GCM', iv: ivBuf }, key, ctBuf);
}

async function encryptBlob(key, blob) {
  const buf = await blob.arrayBuffer();
  return encrypt(key, buf);
}

async function decryptToBlob(key, encryptedBlob, mimeType = 'image/jpeg') {
  const plainBuf = await decrypt(key, encryptedBlob.iv, encryptedBlob.ciphertext);
  return new Blob([plainBuf], { type: mimeType });
}

async function encryptJson(key, obj) {
  const str = JSON.stringify(obj);
  const buf = new TextEncoder().encode(str);
  return encrypt(key, buf);
}

async function decryptJson(key, encryptedObj) {
  const plainBuf = await decrypt(key, encryptedObj.iv, encryptedObj.ciphertext);
  const str = new TextDecoder().decode(plainBuf);
  return JSON.parse(str);
}

// ─────────────────────────────────────────────────────────────────────────────
// Public OfflineQueue class
// ─────────────────────────────────────────────────────────────────────────────

export class OfflineQueue {
  constructor() {
    this._db = null;
    this._key = null;
    this._draining = false;
    this._onProgress = null; // callback(event) for UI updates
  }

  /**
   * Initialize DB and crypto key. Call once at app startup.
   * Returns { supported: bool, queueLength: number }
   */
  async init(onProgress = null) {
    this._onProgress = onProgress;
    if (!window.indexedDB || !window.crypto?.subtle) {
      console.warn('[OfflineQueue] IndexedDB or WebCrypto not available.');
      return { supported: false, queueLength: 0 };
    }
    try {
      this._db = await openDB();
      this._key = await getOrCreateKey();
      const queueLength = await idbCount(this._db);
      this._emit('init', { queueLength });
      return { supported: true, queueLength };
    } catch (err) {
      console.error('[OfflineQueue] init failed:', err);
      return { supported: false, queueLength: 0 };
    }
  }

  /**
   * Add a scan to the encrypted offline queue.
   * @param {Blob} page1Blob
   * @param {Blob} page2Blob
   * @param {object} meta  – { campId, apiHeaders }
   * @returns {number} IDB record id
   */
  async enqueue(page1Blob, page2Blob, meta = {}) {
    if (!this._ready()) throw new Error('OfflineQueue not initialized.');

    const key = this._key;
    const [encPage1, encPage2, encMeta] = await Promise.all([
      encryptBlob(key, page1Blob),
      encryptBlob(key, page2Blob),
      encryptJson(key, {
        campId: meta.campId || null,
        apiHeaders: meta.apiHeaders || {},
        page1Mime: page1Blob.type || 'image/jpeg',
        page2Mime: page2Blob.type || 'image/jpeg',
      }),
    ]);

    const record = {
      created_at: Date.now(),
      camp_id: meta.campId || null,  // unencrypted for index only — no PHI
      enc_page1: encPage1,
      enc_page2: encPage2,
      enc_meta: encMeta,
      attempts: 0,
      last_error: null,
    };

    const id = await idbPut(this._db, record);
    const total = await idbCount(this._db);
    this._emit('enqueue', { id, queueLength: total });

    // Request background sync if available
    this._requestBackgroundSync();

    return id;
  }

  /**
   * Drain the queue: decrypt each entry, POST to /api/extract, delete on success.
   * Transient errors (network, 5xx) leave the entry for the next cycle.
   * Permanent errors (4xx, decryption) or expired entries are deleted.
   *
   * @param {Function} extractFn  – async (page1Blob, page2Blob, apiHeaders) => response
   */
  async drain(extractFn) {
    if (!this._ready() || this._draining) return { synced: 0, failed: 0, expired: 0 };
    this._draining = true;

    const stats = { synced: 0, failed: 0, expired: 0 };

    try {
      const entries = await idbGetAll(this._db);
      const now = Date.now();

      for (const entry of entries) {
        // 1. Purge expired entries (DPDP / 48-hour TTL)
        if (now - entry.created_at > MAX_AGE_MS) {
          await idbDelete(this._db, entry.id);
          stats.expired++;
          this._emit('expired', { id: entry.id });
          continue;
        }

        // 2. Decrypt
        let page1Blob, page2Blob, meta;
        try {
          const key = this._key;
          [page1Blob, page2Blob, meta] = await Promise.all([
            decryptToBlob(key, entry.enc_page1, 'image/jpeg'),
            decryptToBlob(key, entry.enc_page2, 'image/jpeg'),
            decryptJson(key, entry.enc_meta),
          ]);
        } catch (decErr) {
          // Cannot decrypt (tab was reloaded — key lost). Purge entry.
          console.warn('[OfflineQueue] Decryption failed for entry', entry.id, decErr);
          await idbDelete(this._db, entry.id);
          stats.failed++;
          this._emit('decrypt_error', { id: entry.id });
          continue;
        }

        // 3. Submit to backend
        this._emit('syncing', { id: entry.id, attempt: entry.attempts + 1 });
        try {
          await extractFn(page1Blob, page2Blob, meta.apiHeaders || {});
          await idbDelete(this._db, entry.id);
          stats.synced++;
          this._emit('synced', { id: entry.id, queueLength: await idbCount(this._db) });
        } catch (fetchErr) {
          const status = fetchErr.status || 0;
          // Permanent failure (bad request / payload too large) — purge
          if (status >= 400 && status < 500) {
            await idbDelete(this._db, entry.id);
            stats.failed++;
            this._emit('permanent_error', { id: entry.id, status });
          } else {
            // Transient — increment attempts, leave in queue
            await idbPut(this._db, {
              ...entry,
              attempts: entry.attempts + 1,
              last_error: fetchErr.message || String(fetchErr),
            });
            this._emit('retry_later', { id: entry.id, attempts: entry.attempts + 1 });
          }
        }
      }
    } finally {
      this._draining = false;
    }

    const remaining = await idbCount(this._db);
    this._emit('drain_complete', { ...stats, queueLength: remaining });
    return stats;
  }

  /** Return the current number of queued (pending) scans. */
  async length() {
    if (!this._ready()) return 0;
    return idbCount(this._db);
  }

  /**
   * DPDP right-to-erasure: wipe all queued encrypted entries immediately.
   * Called from the DPDP erasure flow in app.js.
   */
  async purgeAll() {
    if (!this._ready()) return;
    await idbClear(this._db);
    this._emit('purge_all', { queueLength: 0 });
  }

  // ── Internals ──────────────────────────────────────────────────────────────

  _ready() {
    return Boolean(this._db && this._key);
  }

  _emit(type, detail = {}) {
    if (this._onProgress) {
      try { this._onProgress({ type, ...detail }); } catch { /* ignore */ }
    }
    // Also dispatch a custom DOM event for UI components to listen to
    try {
      window.dispatchEvent(new CustomEvent('ink2i:queue', { detail: { type, ...detail } }));
    } catch { /* ignore in non-window contexts */ }
  }

  _requestBackgroundSync() {
    if ('serviceWorker' in navigator && 'SyncManager' in window) {
      navigator.serviceWorker.ready
        .then((reg) => reg.sync.register('ink2i-offline-sync'))
        .catch(() => {}); // Not fatal
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Singleton export + SW message listener
// ─────────────────────────────────────────────────────────────────────────────

/** Singleton accessible from app.js and api.js */
export const offlineQueue = new OfflineQueue();

/**
 * Register the ServiceWorker and wire the SW→window drain message.
 * Call once at app startup (from app.js).
 *
 * @param {Function} extractFn  – api.extractPages function reference
 */
export async function registerOfflineSupport(extractFn) {
  // Register SW
  if ('serviceWorker' in navigator) {
    try {
      const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      console.log('[OfflineQueue] ServiceWorker registered:', reg.scope);
    } catch (err) {
      console.warn('[OfflineQueue] ServiceWorker registration failed:', err.message);
    }

    // Listen for SW-triggered drain messages
    navigator.serviceWorker.addEventListener('message', (event) => {
      if (event.data && event.data.type === 'INK2I_DRAIN_QUEUE') {
        offlineQueue.drain(extractFn);
      }
    });
  }

  // Also drain immediately on network restoration
  window.addEventListener('online', () => {
    console.log('[OfflineQueue] Network restored — draining queue.');
    offlineQueue.drain(extractFn);
  });

  // Drain on visibility change (tab regains focus while online)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && navigator.onLine) {
      offlineQueue.drain(extractFn);
    }
  });
}
