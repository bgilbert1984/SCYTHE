/**
 * The real-time entity stream as a workspace tool (the seventh tool on the lifecycle contract in lifecycle.js; moved out of
 * command-ops-visualization.html as `EntitySync`).
 *
 * While active it holds the entity cache and the event handlers other modules registered, opens the server-sent-events stream on
 * `connect(since)`, reconnects with a growing delay when the stream fails, and passes every event to the handlers. Entities also arrive
 * over the WebSocket path; `ingest(eventType, data)` is the one place an event from either transport updates the cache and reaches
 * the handlers. Deactivating closes the stream and cancels anything pending, and a probe or a reconnect that finishes afterwards
 * does nothing.
 *
 * Handlers are registered with `on` / `off`, as before, and outlive an activation: the modules that subscribe do not know about the
 * lifecycle. The cache and the connection belong to an activation.
 *
 * Deliberate changes (each has a test):
 *  - The status probe no longer leaves a stream open. `connect` asks the endpoint for its status with a GET before it opens the
 *    EventSource (an EventSource cannot read a status). The old code never read or closed that response, so each connect probably held
 *    a second open stream to the server. The probe is now aborted as soon as its status is known.
 *  - A `connect` while another is still probing or connected replaces it. The old code let two overlapping connects each open an
 *    EventSource and lost the first without closing it.
 *  - `disconnect` (and `connect`) cancel a pending reconnect; the old timer fired after a logout.
 *  - An event without an entity id is not cached (the SSE path used to store it under the key "undefined"; the WebSocket path already
 *    skipped it).
 *  - `ingest` notifies the handlers, so a WebSocket event now reaches them before WebSocketSync's own handlers, not after.
 *  - The unused `deleteEntity`, `getEntity`, `getAllEntities`, `getEntitiesByType`, and the write-only `lastEventId` and HEARTBEAT listener
 *    are gone.
 *
 * Kept as it was: a reconnect calls `connect()` without `since`, so nothing is replayed after an outage; the error handler schedules its
 * own reconnect while the browser's EventSource also retries on its own; a 401 from the probe clears the operator session.
 *
 * Services it needs (see `requires`):
 *   httpFetch          (url, init?) => Promise<Response>
 *   openEventSource    (url) => EventSource
 *   operatorSession    {token() => string|null, clear()}
 *   entityConsole      {info(text), alert(text)}: the page's console messages
 */

export const ENTITY_SYNC_ID = "entity-sync";
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY_MS = 3000;
const MAX_RECONNECT_DELAY_MS = 60000;
const MAX_CACHED = 5000;
const TRIM_EVERY = 100;
const SSE_EVENTS = ["PREEXISTING", "CREATE", "UPDATE", "DELETE"];

/** Event handlers by event name. A handler that throws is reported and does not stop the others. */
export function createHandlerRegistry({log = console, label = "EntitySync"} = {}) {
  const handlers = {};
  return {
    on(event, handler) { (handlers[event] ||= []).push(handler); },
    off(event, handler) { if (handlers[event]) handlers[event] = handlers[event].filter((h) => h !== handler); },
    notify(event, data) {
      for (const handler of handlers[event] || []) {
        try { handler(data); } catch (error) { log.error?.(`[${label}] Handler error for ${event}:`, error); }
      }
    },
  };
}

/** The latest state of each entity, newest `max` kept: trimmed every `every` inserts, oldest `lastUpdated` first. */
export function createEntityCache({max = MAX_CACHED, every = TRIM_EVERY} = {}) {
  const entries = {};
  let inserts = 0;
  return {
    entries,
    apply(eventType, data) {
      const entityId = data.entity_id;
      if (eventType === "DELETE") { delete entries[entityId]; return; }
      if (!entityId) return;
      entries[entityId] = {id: entityId, type: data.entity_type, data: data.entity_data, provenance: data.provenance, lastUpdated: data.timestamp};
      if (++inserts % every !== 0) return;
      const all = Object.entries(entries);
      if (all.length <= max) return;
      all.sort((a, b) => ((a[1].lastUpdated || "") < (b[1].lastUpdated || "") ? -1 : 1));
      for (const [key] of all.slice(0, all.length - max)) delete entries[key];
    },
    clear() { for (const key of Object.keys(entries)) delete entries[key]; inserts = 0; },
  };
}

/** The reconnect delay in ms: 3 s, doubling per attempt, at most 60 s. `attempt` counts from 1. */
export const reconnectDelay = (attempt) => Math.min(MAX_RECONNECT_DELAY_MS, RECONNECT_DELAY_MS * 2 ** (attempt - 1));

export function createEntitySyncPlugin({log = console} = {}) {
  let run = null;   // null while the tool is inactive
  let early = null;   // [since] of a connect asked for while inactive: made when the tool activates
  const registry = createHandlerRegistry({log});
  const cache = createEntityCache();

  const tool = {
    id: ENTITY_SYNC_ID,
    title: "Entity stream",
    requires: ["httpFetch", "openEventSource", "operatorSession", "entityConsole"],
    activate(ctx) {
      const http = ctx.services.get("httpFetch");
      const openEventSource = ctx.services.get("openEventSource");
      const session = ctx.services.get("operatorSession");
      const out = ctx.services.get("entityConsole");

      let connected = false;
      let attempts = 0;
      let current = null;   // the scope of the connect in progress or established
      let retry = null;     // the scope holding the pending reconnect timer

      const cancelRetry = () => { const pending = retry; retry = null; pending?.close(); };
      function release() {   // close the current connect and any pending reconnect
        cancelRetry();
        const previous = current;
        current = null;
        connected = false;
        previous?.close();
      }
      const ended = (attempt) => attempt.signal.aborted;   // replaced, disconnected or deactivated while we waited (each closes the attempt at once)
      const whileLive = (attempt, fn) => attempt.guard((...args) => { if (!ctx.signal.aborted) fn(...args); });   // the root closes its children one step at a time

      function ingest(eventType, data) {
        cache.apply(eventType, data);
        registry.notify(eventType.toLowerCase(), data);
        registry.notify("entity", {eventType, ...data});
      }

      function scheduleReconnect() {
        if (attempts >= MAX_RECONNECT_ATTEMPTS) {
          out.alert("⚠️ Entity stream disconnected - max reconnects reached");
          return;
        }
        attempts += 1;
        const delay = reconnectDelay(attempts);
        log.log?.(`[EntitySync] Reconnect attempt ${attempts}/${MAX_RECONNECT_ATTEMPTS} in ${delay}ms`);
        cancelRetry();
        retry = ctx.scope();
        retry.timeout(() => { retry = null; connect().catch((error) => log.debug?.("[EntitySync] reconnect.connect error:", error)); }, delay);
      }

      async function connect(since) {
        const token = session.token();
        if (!token) { log.warn?.("[EntitySync] Cannot connect - no session token"); return; }
        release();
        const attempt = ctx.scope();
        current = attempt;
        let url = `/api/entities/stream?token=${encodeURIComponent(token)}`;
        if (since) url += `&since=${encodeURIComponent(since)}`;

        // Preflight: an EventSource cannot read a status, so ask the endpoint first. Only the status matters, so abort the request
        // as soon as it is known; left open it would hold a second stream.
        const probe = new AbortController();
        attempt.onCleanup(() => probe.abort());
        let status;
        try {
          const response = await http(url, {method: "GET", headers: {Accept: "text/event-stream"}, signal: probe.signal});
          status = {code: response.status, ok: response.ok};
          probe.abort();
        } catch (error) {
          if (ended(attempt)) return;
          log.debug?.("[EntitySync] SSE probe failed:", error);
          scheduleReconnect();
          return;
        }
        if (ended(attempt)) return;
        if (status.code === 401) {
          log.debug?.("[EntitySync] SSE probe returned 401 - clearing session");
          current = null;
          attempt.close();
          session.clear();
          out.alert("Entity stream unauthorized - please log in");
          return;
        }
        if (!status.ok) {
          log.debug?.(`[EntitySync] SSE probe returned status ${status.code}`);
          scheduleReconnect();
          return;
        }

        let source;
        try {
          source = openEventSource(url);
        } catch (error) {
          log.debug?.("[EntitySync] Failed to create EventSource:", error);
          scheduleReconnect();
          return;
        }
        attempt.onCleanup(() => source.close());
        attempt.listen(source, "open", whileLive(attempt, () => {
          connected = true;
          attempts = 0;
          log.log?.("[EntitySync] Connected to entity stream");
          out.info("📡 Connected to real-time entity stream");
          registry.notify("connected", {});
        }));
        attempt.listen(source, "error", whileLive(attempt, (event) => {
          log.debug?.("[EntitySync] Connection error (SSE event)", event);
          connected = false;
          registry.notify("error", {error: event});
          scheduleReconnect();
        }));
        for (const type of SSE_EVENTS) {
          attempt.listen(source, type, whileLive(attempt, (event) => {
            const data = JSON.parse(event.data);
            if (type !== "PREEXISTING") log.log?.(`[EntitySync] ${type}: ${data.entity_id} (${data.entity_type}) by ${data.provenance?.source_description || "System"}`);
            ingest(type, data);
          }));
        }
      }

      function disconnect() {
        release();
        log.log?.("[EntitySync] Disconnected from entity stream");
      }

      async function publishEntity(entityId, entityType, entityData) {
        const token = session.token();
        if (!token) {
          log.warn?.("[EntitySync] Cannot publish - no session");
          return {success: false, message: "Not authenticated"};
        }
        try {
          const response = await http("/api/entities/publish", {
            method: "POST",
            headers: {"Content-Type": "application/json", "X-Session-Token": token},
            body: JSON.stringify({entity_id: entityId, entity_type: entityType, entity_data: entityData}),
          });
          const data = await response.json();
          return {success: data.status === "ok", ...data};
        } catch (error) {
          log.error?.("[EntitySync] Publish error:", error);
          return {success: false, message: error.message};
        }
      }

      ctx.onCleanup(() => { run = null; connected = false; cache.clear(); });
      run = {connect, disconnect, ingest, publishEntity, isConnected: () => connected};
      if (early) { const [since] = early; early = null; connect(since).catch((error) => log.warn?.("[EntitySync] connect failed:", error)); }
    },
  };

  /**
   * What the page uses as `window.EntitySync`. `on` and `off` always work and `on` chains; `connect` while inactive is remembered and made
   * on activation; the rest does nothing while the tool is inactive. `entityCache` is live (read it, do not write it).
   */
  const controls = {
    on(event, handler) { registry.on(event, handler); return controls; },
    off(event, handler) { registry.off(event, handler); return controls; },
    connect(since) { if (run) return run.connect(since); early = [since]; },   // asked for before the tool is up: made on activation
    disconnect() { early = null; return run?.disconnect(); },
    ingest: (eventType, data) => run?.ingest(eventType, data),
    publishEntity: (entityId, entityType, entityData) => (run ? run.publishEntity(entityId, entityType, entityData)
      : Promise.resolve({success: false, message: "Entity sync is not running"})),
    get connected() { return run ? run.isConnected() : false; },
    get entityCache() { return cache.entries; },
  };

  return {tool, controls};
}
