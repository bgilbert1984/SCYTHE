import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {createEntitySyncPlugin, createHandlerRegistry, createEntityCache, reconnectDelay, ENTITY_SYNC_ID} from "./entitySyncPlugin.js";

const settle = () => new Promise((resolve) => setImmediate(resolve));
const gate = () => { let open; const promise = new Promise((resolve) => { open = resolve; }); return {promise, open}; };

function fakeClock() {
  let at = 0, next = 1;
  const timers = new Map();
  return {
    setTimeout: (fn, ms) => { const id = next++; timers.set(id, {at: at + ms, fn}); return id; },
    clearTimeout: (id) => timers.delete(id),
    setInterval: () => 0, clearInterval() {},
    async tick(ms) {
      const target = at + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        const [id, t] = due;
        at = t.at;
        timers.delete(id);
        t.fn();
        await settle();
      }
      at = target;
    },
    pending: () => timers.size,
  };
}

class FakeSource {
  constructor(url) { this.url = url; this.closed = false; this.listeners = new Map(); }
  addEventListener(type, fn) { this.listeners.set(type, [...(this.listeners.get(type) || []), fn]); }
  removeEventListener(type, fn) { this.listeners.set(type, (this.listeners.get(type) || []).filter((f) => f !== fn)); }
  close() { this.closed = true; }
  emit(type, data) { for (const fn of this.listeners.get(type) || []) fn(data === undefined ? {} : {data: JSON.stringify(data)}); }
  count(type) { return (this.listeners.get(type) || []).length; }
}

function setup({token = "tok-1", status = 200, probe, sourceThrows = false, publish} = {}) {
  const clock = fakeClock();
  const logs = {warn: [], debug: [], log: [], error: []};
  const log = Object.fromEntries(Object.keys(logs).map((k) => [k, (...a) => logs[k].push(a)]));
  const state = {token, status, requests: [], sources: [], infos: [], alerts: [], cleared: 0};
  const http = async (url, init = {}) => {
    state.requests.push({url, init});
    if (url === "/api/entities/publish") return publish ? publish(url, init) : {ok: true, json: async () => ({status: "ok", event_type: "CREATE"})};
    if (probe) return probe(url, init);
    return {status: state.status, ok: state.status >= 200 && state.status < 300};
  };
  const workspace = createWorkspace({log, timers: clock});
  workspace.provide("httpFetch", http);
  workspace.provide("openEventSource", (url) => {
    if (sourceThrows) throw new Error("no EventSource");
    const source = new FakeSource(url);
    state.sources.push(source);
    return source;
  });
  workspace.provide("operatorSession", {token: () => state.token, clear: () => { state.cleared++; }});
  workspace.provide("entityConsole", {info: (t) => state.infos.push(t), alert: (t) => state.alerts.push(t)});
  const plugin = createEntitySyncPlugin({log});
  workspace.register(plugin.tool);
  return {workspace, controls: plugin.controls, clock, logs, state};
}
const up = async (t) => { await t.workspace.activate(ENTITY_SYNC_ID); return t; };
const connected = async (t, since) => { await up(t); await t.controls.connect(since); t.state.sources.at(-1).emit("open"); return t.state.sources.at(-1); };

// --- pieces ---

test("handler registry: calls in order, off removes, a throwing handler is reported and does not stop the rest", () => {
  const errors = [];
  const registry = createHandlerRegistry({log: {error: (...a) => errors.push(a)}});
  const seen = [];
  const h1 = (d) => seen.push(["h1", d]);
  registry.on("x", () => { throw new Error("bad"); });
  registry.on("x", h1);
  registry.on("x", (d) => seen.push(["h3", d]));
  registry.notify("x", 1);
  assert.deepEqual(seen, [["h1", 1], ["h3", 1]]);
  assert.equal(errors.length, 1);
  assert.match(errors[0][0], /Handler error for x/);
  registry.off("x", h1);
  registry.notify("x", 2);
  assert.deepEqual(seen.slice(2), [["h3", 2]]);
  registry.off("never", h1);   // no such event: fine
  registry.notify("never", 1);
});

test("entity cache: stores the latest state, deletes, skips events without an id, and DELETE of an unknown id is fine", () => {
  const cache = createEntityCache();
  cache.apply("CREATE", {entity_id: "a", entity_type: "T", entity_data: {n: 1}, provenance: {p: 1}, timestamp: "t1"});
  assert.deepEqual(cache.entries.a, {id: "a", type: "T", data: {n: 1}, provenance: {p: 1}, lastUpdated: "t1"});
  cache.apply("UPDATE", {entity_id: "a", entity_type: "T", entity_data: {n: 2}, timestamp: "t2"});
  assert.equal(cache.entries.a.data.n, 2);
  cache.apply("CREATE", {entity_type: "T"});
  assert.deepEqual(Object.keys(cache.entries), ["a"]);
  cache.apply("DELETE", {entity_id: "zzz"});
  cache.apply("DELETE", {entity_id: "a"});
  assert.deepEqual(cache.entries, {});
});

test("entity cache: trimmed to the newest `max` only every `every` inserts, oldest lastUpdated first", () => {
  const cache = createEntityCache({max: 3, every: 4});
  for (let i = 1; i <= 3; i++) cache.apply("CREATE", {entity_id: `e${i}`, timestamp: `2026-01-0${i}`});
  assert.equal(Object.keys(cache.entries).length, 3);
  cache.apply("CREATE", {entity_id: "e4", timestamp: "2026-01-04"});   // 4th insert: trim to 3
  assert.deepEqual(Object.keys(cache.entries).sort(), ["e2", "e3", "e4"]);
  cache.apply("CREATE", {entity_id: "e5", timestamp: "2026-01-05"});   // 5th: over max but not a trim point
  assert.equal(Object.keys(cache.entries).length, 4);
  cache.clear();
  assert.deepEqual(cache.entries, {});
  for (let i = 1; i <= 3; i++) cache.apply("CREATE", {entity_id: `f${i}`, timestamp: `2026-02-0${i}`});
  cache.apply("CREATE", {entity_id: "f4", timestamp: "2026-02-04"});   // the counter restarted: 4th insert trims
  assert.equal(Object.keys(cache.entries).length, 3);
});

test("entity cache: entries without a timestamp sort first, so they go first", () => {
  const cache = createEntityCache({max: 1, every: 2});
  cache.apply("CREATE", {entity_id: "dated", timestamp: "2026-01-01"});
  cache.apply("CREATE", {entity_id: "undated"});
  assert.deepEqual(Object.keys(cache.entries), ["dated"]);
});

test("the reconnect delay doubles from 3 s and stops at 60 s", () => {
  assert.deepEqual([1, 2, 3, 4, 5, 6, 7].map(reconnectDelay), [3000, 6000, 12000, 24000, 48000, 60000, 60000]);
});

// --- inactive ---

test("while inactive: on/off work and chain, the rest does nothing, connected is false, publish says so", async () => {
  const t = setup();
  const seen = [];
  assert.equal(t.controls.on("x", (d) => seen.push(d)), t.controls);
  assert.equal(t.controls.connected, false);
  assert.deepEqual(t.controls.entityCache, {});
  assert.equal(t.controls.ingest("CREATE", {entity_id: "a"}), undefined);
  assert.equal(t.controls.disconnect(), undefined);
  assert.deepEqual(await t.controls.publishEntity("a", "T", {}), {success: false, message: "Entity sync is not running"});
  assert.deepEqual(t.state.requests, []);
  assert.deepEqual(seen, []);
});

// --- connect ---

test("connect without a session token warns and opens nothing", async () => {
  const t = await up(setup({token: null}));
  await t.controls.connect();
  assert.deepEqual(t.state.requests, []);
  assert.deepEqual(t.state.sources, []);
  assert.match(t.logs.warn[0][0], /no session token/);
});

test("connect probes the endpoint with the token, then opens the stream on the same url; since is passed on, both encoded", async () => {
  const t = await up(setup({token: "a b&c"}));
  await t.controls.connect("seq 7");
  const url = "/api/entities/stream?token=a%20b%26c&since=seq%207";
  assert.equal(t.state.requests[0].url, url);
  assert.equal(t.state.requests[0].init.method, "GET");
  assert.equal(t.state.requests[0].init.headers.Accept, "text/event-stream");
  assert.equal(t.state.sources[0].url, url);
  await t.controls.connect();
  assert.equal(t.state.sources[1].url, "/api/entities/stream?token=a%20b%26c");
});

test("the probe is aborted as soon as its status is known, so it holds no stream", async () => {
  let signal;
  const t = await up(setup({probe: async (url, init) => { signal = init.signal; assert.equal(init.signal.aborted, false); return {status: 200, ok: true}; }}));
  await t.controls.connect();
  assert.equal(signal.aborted, true);
  assert.equal(t.state.sources.length, 1);
});

test("a 401 from the probe clears the session, says so, and opens nothing", async () => {
  const t = await up(setup({status: 401}));
  await t.controls.connect();
  assert.equal(t.state.cleared, 1);
  assert.deepEqual(t.state.alerts, ["Entity stream unauthorized - please log in"]);
  assert.deepEqual(t.state.sources, []);
  assert.equal(t.clock.pending(), 0);   // no reconnect
});

test("a non-OK probe schedules a reconnect with a growing delay, and gives up after five", async () => {
  const t = await up(setup({status: 503}));
  await t.controls.connect("keep");
  assert.equal(t.state.requests.length, 1);
  await t.clock.tick(2999);
  assert.equal(t.state.requests.length, 1);
  await t.clock.tick(1);
  assert.equal(t.state.requests.length, 2);
  assert.equal(t.state.requests[1].url, "/api/entities/stream?token=tok-1");   // the reconnect does not carry since
  await t.clock.tick(6000);
  assert.equal(t.state.requests.length, 3);
  await t.clock.tick(12000);
  assert.equal(t.state.requests.length, 4);
  await t.clock.tick(24000);
  assert.equal(t.state.requests.length, 5);
  assert.deepEqual(t.state.alerts, []);
  await t.clock.tick(48000);
  assert.equal(t.state.requests.length, 6);   // the fifth reconnect fails too: no sixth
  assert.deepEqual(t.state.alerts, ["⚠️ Entity stream disconnected - max reconnects reached"]);
  await t.clock.tick(300000);
  assert.equal(t.state.requests.length, 6);
  assert.equal(t.clock.pending(), 0);
});

test("a probe that throws schedules a reconnect; so does an EventSource that cannot be created", async () => {
  const a = await up(setup({probe: async () => { throw new Error("offline"); }}));
  await a.controls.connect();
  assert.equal(a.clock.pending(), 1);
  const b = await up(setup({sourceThrows: true}));
  await b.controls.connect();
  assert.equal(b.clock.pending(), 1);
  assert.equal(b.state.sources.length, 0);
});

// --- stream ---

test("opening the stream marks it connected, tells the console and the handlers, and resets the retry count", async () => {
  const t = await up(setup({status: 503}));
  const seen = [];
  t.controls.on("connected", (d) => seen.push(d));
  await t.controls.connect();            // fails: attempt 1 pending
  t.state.status = 200;
  await t.clock.tick(3000);              // the retry succeeds
  assert.equal(t.controls.connected, false);
  t.state.sources[0].emit("open");
  assert.equal(t.controls.connected, true);
  assert.deepEqual(t.state.infos, ["📡 Connected to real-time entity stream"]);
  assert.deepEqual(seen, [{}]);
  t.state.sources[0].emit("error");      // a new failure starts again from 3 s
  const before = t.state.requests.length;
  await t.clock.tick(3000);
  assert.equal(t.state.requests.length, before + 1);
});

test("stream events update the cache and reach the handlers by name and as 'entity' with the original event type", async () => {
  const t = await up(setup());
  const source = await connected(t);
  const byName = [], all = [];
  t.controls.on("create", (d) => byName.push(d));
  t.controls.on("entity", (d) => all.push(d));
  const event = {entity_id: "a", entity_type: "SENSOR", entity_data: {x: 1}, provenance: {source_description: "Alice"}, timestamp: "t1"};
  source.emit("CREATE", event);
  assert.deepEqual(byName, [event]);
  assert.deepEqual(all, [{eventType: "CREATE", ...event}]);
  assert.equal(t.controls.entityCache.a.type, "SENSOR");
  source.emit("DELETE", {entity_id: "a"});
  assert.deepEqual(t.controls.entityCache, {});
  source.emit("PREEXISTING", {entity_id: "b"});
  source.emit("UPDATE", {entity_id: "b", entity_data: {y: 2}});
  assert.equal(t.controls.entityCache.b.data.y, 2);
});

test("events are logged except PREEXISTING, with who made them", async () => {
  const t = await up(setup());
  const source = await connected(t);
  source.emit("PREEXISTING", {entity_id: "a", entity_type: "T"});
  assert.ok(!t.logs.log.some((l) => /PREEXISTING/.test(l[0])));
  source.emit("UPDATE", {entity_id: "a", entity_type: "T", provenance: {source_description: "Alice"}});
  source.emit("CREATE", {entity_id: "b", entity_type: "T"});
  assert.ok(t.logs.log.some((l) => l[0] === "[EntitySync] UPDATE: a (T) by Alice"));
  assert.ok(t.logs.log.some((l) => l[0] === "[EntitySync] CREATE: b (T) by System"));
});

test("a stream error marks it disconnected, tells the handlers, and schedules a reconnect", async () => {
  const t = await up(setup());
  const source = await connected(t);
  const seen = [];
  t.controls.on("error", (d) => seen.push(d));
  source.emit("error");
  assert.equal(t.controls.connected, false);
  assert.equal(seen.length, 1);
  assert.equal(t.clock.pending(), 1);
  await t.clock.tick(3000);
  assert.equal(t.state.sources.length, 2);
  assert.equal(source.closed, true);   // replaced
});

test("a second connect replaces the first: its stream is closed and only one is live", async () => {
  const t = await up(setup());
  const first = await connected(t);
  await t.controls.connect();
  await settle();
  assert.equal(first.closed, true);
  assert.equal(t.state.sources.length, 2);
  assert.equal(t.controls.connected, false);   // the new one has not opened yet
  first.emit("CREATE", {entity_id: "late"});   // the replaced stream's events are dropped
  assert.deepEqual(t.controls.entityCache, {});
});

test("overlapping connects: the first probe finishing late opens nothing", async () => {
  const hold = gate();
  let calls = 0;
  const t = await up(setup({probe: async () => { if (calls++ === 0) await hold.promise; return {status: 200, ok: true}; }}));
  const first = t.controls.connect("one");
  await settle();
  await t.controls.connect("two");
  hold.open();
  await first;
  assert.equal(t.state.sources.length, 1);
  assert.match(t.state.sources[0].url, /since=two/);
});

test("a probe that is aborted by a newer connect and rejects does not schedule a reconnect", async () => {
  const hold = gate();
  let calls = 0;
  const t = await up(setup({probe: async (url, init) => {
    if (calls++ === 0) { await hold.promise; if (init.signal.aborted) throw new Error("AbortError"); }
    return {status: 200, ok: true};
  }}));
  const first = t.controls.connect();
  await settle();
  await t.controls.connect();
  hold.open();
  await first;
  assert.equal(t.clock.pending(), 0);
});

// --- disconnect and deactivate ---

test("disconnect closes the stream, marks it disconnected, and cancels a pending reconnect", async () => {
  const t = await up(setup());
  const source = await connected(t);
  source.emit("error");
  assert.equal(t.clock.pending(), 1);
  t.controls.disconnect();
  await settle();
  assert.equal(source.closed, true);
  assert.equal(t.controls.connected, false);
  assert.equal(t.clock.pending(), 0);
  await t.clock.tick(120000);
  assert.equal(t.state.sources.length, 1);
});

test("disconnect while a probe is in flight stops that connect", async () => {
  const hold = gate();
  const t = await up(setup({probe: async () => { await hold.promise; return {status: 200, ok: true}; }}));
  const pending = t.controls.connect();
  await settle();
  t.controls.disconnect();
  hold.open();
  await pending;
  assert.deepEqual(t.state.sources, []);
});

test("deactivating closes the stream, cancels the reconnect, empties the cache, and drops late events", async () => {
  const t = await up(setup());
  const source = await connected(t);
  source.emit("CREATE", {entity_id: "a"});
  source.emit("error");
  assert.deepEqual(await t.workspace.deactivate(ENTITY_SYNC_ID), []);
  assert.equal(source.closed, true);
  assert.equal(t.clock.pending(), 0);
  assert.deepEqual(t.controls.entityCache, {});
  assert.equal(t.controls.connected, false);
  source.emit("CREATE", {entity_id: "late"});
  assert.deepEqual(t.controls.entityCache, {});
});

test("a probe that finishes after deactivation opens nothing", async () => {
  const hold = gate();
  const t = await up(setup({probe: async () => { await hold.promise; return {status: 200, ok: true}; }}));
  const pending = t.controls.connect();
  await settle();
  await t.workspace.deactivate(ENTITY_SYNC_ID);
  hold.open();
  await pending;
  assert.deepEqual(t.state.sources, []);
  assert.equal(t.clock.pending(), 0);
});

test("a failing probe that finishes after deactivation schedules nothing", async () => {
  const hold = gate();
  const t = await up(setup({probe: async () => { await hold.promise; throw new Error("late"); }}));
  const pending = t.controls.connect();
  await settle();
  await t.workspace.deactivate(ENTITY_SYNC_ID);
  hold.open();
  await pending;
  assert.equal(t.clock.pending(), 0);
});

test("handlers outlive an activation; a connect asked for while inactive is made on activation", async () => {
  const t = setup();
  const seen = [];
  t.controls.on("create", (d) => seen.push(d.entity_id));
  t.controls.connect("early");
  assert.deepEqual(t.state.requests, []);
  await up(t);
  await settle();
  assert.match(t.state.requests[0].url, /since=early/);
  t.state.sources[0].emit("CREATE", {entity_id: "one"});
  await t.workspace.deactivate(ENTITY_SYNC_ID);
  await up(t);
  await t.controls.connect();
  t.state.sources.at(-1).emit("CREATE", {entity_id: "two"});
  assert.deepEqual(seen, ["one", "two"]);
});

test("disconnect before activation cancels a remembered connect", async () => {
  const t = setup();
  t.controls.connect();
  t.controls.disconnect();
  await up(t);
  await settle();
  assert.deepEqual(t.state.requests, []);
});

// --- ingest and publish ---

test("ingest is the one path: it updates the cache and notifies by name and as 'entity'", async () => {
  const t = await up(setup());
  const names = [];
  t.controls.on("update", (d) => names.push(["update", d.entity_id]));
  t.controls.on("entity", (d) => names.push(["entity", d.eventType]));
  t.controls.ingest("UPDATE", {entity_id: "w", entity_type: "T", entity_data: {k: 1}});
  assert.deepEqual(names, [["update", "w"], ["entity", "UPDATE"]]);
  assert.equal(t.controls.entityCache.w.data.k, 1);
  t.controls.ingest("DELETE", {entity_id: "w"});
  assert.deepEqual(t.controls.entityCache, {});
});

test("publishing sends the entity with the session token and reports the server's answer", async () => {
  const t = await up(setup());
  const result = await t.controls.publishEntity("e1", "SENSOR", {a: 1});
  const request = t.state.requests[0];
  assert.equal(request.url, "/api/entities/publish");
  assert.equal(request.init.method, "POST");
  assert.equal(request.init.headers["X-Session-Token"], "tok-1");
  assert.deepEqual(JSON.parse(request.init.body), {entity_id: "e1", entity_type: "SENSOR", entity_data: {a: 1}});
  assert.deepEqual(result, {success: true, status: "ok", event_type: "CREATE"});
});

test("publishing: a non-ok status in the body is a failure, a thrown request reports its message, no token is refused", async () => {
  const bad = await up(setup({publish: async () => ({ok: false, json: async () => ({status: "error", message: "denied"})})}));
  assert.deepEqual(await bad.controls.publishEntity("e", "T", {}), {success: false, status: "error", message: "denied"});
  const thrown = await up(setup({publish: async () => { throw new Error("offline"); }}));
  assert.deepEqual(await thrown.controls.publishEntity("e", "T", {}), {success: false, message: "offline"});
  const anon = await up(setup({token: null}));
  assert.deepEqual(await anon.controls.publishEntity("e", "T", {}), {success: false, message: "Not authenticated"});
  assert.deepEqual(anon.state.requests, []);
});

// --- gaps found by mutation ---

test("reaching a trim point with fewer entries than the limit deletes nothing", () => {
  const cache = createEntityCache({max: 5, every: 4});
  for (const id of ["a", "b", "c", "d"]) cache.apply("CREATE", {entity_id: id, timestamp: id});
  assert.deepEqual(Object.keys(cache.entries).sort(), ["a", "b", "c", "d"]);
});

test("two errors from one stream leave one reconnect pending, not two", async () => {
  const t = await up(setup());
  const source = await connected(t);
  source.emit("error");
  source.emit("error");
  assert.equal(t.clock.pending(), 1);
  await t.clock.tick(6000);
  assert.equal(t.state.sources.length, 2);   // one reconnect, not two
});

test("disconnect and a newer connect abort a probe that is still in flight", async () => {
  const hold = gate();
  const signals = [];
  const t = await up(setup({probe: async (url, init) => { signals.push(init.signal); await hold.promise; return {status: 200, ok: true}; }}));
  const first = t.controls.connect();
  await settle();
  t.controls.disconnect();
  await settle();
  assert.equal(signals[0].aborted, true);
  const second = t.controls.connect();
  await settle();
  const third = t.controls.connect();
  await settle();
  assert.equal(signals[1].aborted, true);
  hold.open();
  await Promise.all([first, second, third]);
});

test("a PREEXISTING event reaches the 'preexisting' handlers", async () => {
  const t = await up(setup());
  const source = await connected(t);
  const seen = [];
  t.controls.on("preexisting", (d) => seen.push(d.entity_id));
  source.emit("PREEXISTING", {entity_id: "old"});
  assert.deepEqual(seen, ["old"]);
});

test("events that arrive while a deactivation is still closing things are dropped", async () => {
  const t = await up(setup());
  const source = await connected(t);
  source.emit("error");   // a pending reconnect is closed first, then the stream; events can arrive in between
  const seen = [];
  t.controls.on("create", (d) => seen.push(d.entity_id));
  t.controls.on("connected", () => seen.push("connected"));
  t.controls.on("error", () => seen.push("error"));
  const done = t.workspace.deactivate(ENTITY_SYNC_ID);   // aborts the tool, then starts closing; the stream is still open for a moment
  assert.equal(source.closed, false);
  source.emit("CREATE", {entity_id: "during"});
  source.emit("open");
  source.emit("error");
  await done;
  assert.equal(source.closed, true);
  assert.deepEqual(seen, []);
  assert.equal(t.clock.pending(), 0);
});

test("after deactivation the controls do nothing, and a connect remembered once is made once", async () => {
  const t = setup();
  t.controls.connect();
  await up(t);
  await settle();
  const made = t.state.requests.length;
  assert.equal(made, 1);
  await t.workspace.deactivate(ENTITY_SYNC_ID);
  await t.controls.connect();
  await settle();
  assert.equal(t.state.requests.length, made);   // inactive: remembered, not made
  await t.workspace.deactivate(ENTITY_SYNC_ID);
  await up(t);
  await settle();
  assert.equal(t.state.requests.length, made + 1);   // the connect asked for while inactive
  await t.workspace.deactivate(ENTITY_SYNC_ID);
  await up(t);
  await settle();
  assert.equal(t.state.requests.length, made + 1);   // and not again
});
