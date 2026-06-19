/**
 * ImmutableEventLog.js — Append-only hot path for causal events
 *
 * Architecture:
 *   EventLog (immutable) → semantic derivations → identity → fields
 *
 * Warm path: IndexedDB chunks (optional, async).
 */

class ImmutableEventLog {
  constructor(opts = {}) {
    this.chunkSize = opts.chunkSize ?? 500;
    this.maxChunksInMemory = opts.maxChunksInMemory ?? 200;
    this.dbName = opts.dbName ?? 'scythe_event_log';
    this.storeName = opts.storeName ?? 'chunks';

    this._chunks = [];
    this._current = [];
    this._seq = 0;
    this._totalEvents = 0;
    this._idb = null;
    this._persistToIdb = opts.persistToIdb ?? true;
  }

  get totalEvents() {
    return this._totalEvents;
  }

  /**
   * Append immutable event record (serialized JSON-safe).
   */
  append(eventRecord) {
    const entry = {
      seq: ++this._seq,
      wall_ingest: Date.now(),
      ...eventRecord,
    };

    this._current.push(Object.freeze({ ...entry }));
    this._totalEvents++;

    if (this._current.length >= this.chunkSize) {
      this._sealChunk();
    }

    if (this._persistToIdb) {
      this._scheduleIdbFlush();
    }

    return entry.seq;
  }

  _sealChunk() {
    if (!this._current.length) return;
    const chunk = {
      id: `chunk_${this._chunks.length}`,
      start_seq: this._current[0].seq,
      end_seq: this._current[this._current.length - 1].seq,
      count: this._current.length,
      t_min: this._current[0].ts,
      t_max: this._current[this._current.length - 1].ts,
      events: [...this._current],
    };
    this._chunks.push(chunk);
    this._current = [];

    while (this._chunks.length > this.maxChunksInMemory) {
      this._chunks.shift();
    }
  }

  /**
   * Events in simTime range [tMin, tMax].
   */
  queryByTimeRange(tMin, tMax) {
    const out = [];
    const scan = (events) => {
      for (const e of events) {
        if (e.ts >= tMin && e.ts <= tMax) out.push(e);
      }
    };
    for (const c of this._chunks) scan(c.events);
    scan(this._current);
    return out;
  }

  exportAll() {
    if (this._current.length) this._sealChunk();
    const events = [];
    for (const c of this._chunks) events.push(...c.events);
    return {
      metadata: {
        totalEvents: this._totalEvents,
        chunkCount: this._chunks.length,
        exported_at: Date.now(),
      },
      chunks: this._chunks.map((c) => ({
        id: c.id,
        t_min: c.t_min,
        t_max: c.t_max,
        count: c.count,
      })),
      events,
    };
  }

  async _openIdb() {
    if (this._idb || typeof indexedDB === 'undefined') return this._idb;
    return new Promise((resolve) => {
      const req = indexedDB.open(this.dbName, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(this.storeName)) {
          db.createObjectStore(this.storeName, { keyPath: 'id' });
        }
      };
      req.onsuccess = () => {
        this._idb = req.result;
        resolve(this._idb);
      };
      req.onerror = () => resolve(null);
    });
  }

  _scheduleIdbFlush() {
    if (this._flushTimer) return;
    this._flushTimer = setTimeout(async () => {
      this._flushTimer = null;
      await this._flushLastChunkToIdb();
    }, 2000);
  }

  async _flushLastChunkToIdb() {
    const db = await this._openIdb();
    if (!db || !this._chunks.length) return;
    const chunk = this._chunks[this._chunks.length - 1];
    if (chunk._persisted) return;
    try {
      const tx = db.transaction(this.storeName, 'readwrite');
      tx.objectStore(this.storeName).put(chunk);
      chunk._persisted = true;
    } catch (_) { /* quota */ }
  }
}

if (typeof window !== 'undefined') {
  window.ImmutableEventLog = ImmutableEventLog;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { ImmutableEventLog };
}
