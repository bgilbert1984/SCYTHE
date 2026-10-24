import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {createChatPlugin, CHAT_ID, DEFAULT_ROOM} from "./chatPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const flush = () => new Promise((resolve) => setImmediate(resolve));

function fakeClock() {
  let now = 0, next = 1;
  const timers = new Map();   // id -> {at, fn, every}
  return {
    timers,
    setTimeout: (fn, ms) => { const id = next++; timers.set(id, {at: now + ms, fn, every: 0}); return id; },
    setInterval: (fn, ms) => { const id = next++; timers.set(id, {at: now + ms, fn, every: ms}); return id; },
    clearTimeout: (id) => timers.delete(id),
    clearInterval: (id) => timers.delete(id),
    async tick(ms) {
      const target = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        const [id, t] = due;
        now = t.at;
        if (t.every) t.at += t.every; else timers.delete(id);
        t.fn();
        await flush();
      }
      now = target;
    },
    pending: () => timers.size,
  };
}

function setup({stream = true, history = {}, fetchImpl, postImpl} = {}) {
  const clock = fakeClock();
  const sources = [];
  const fetched = [];
  const shown = [], posts = [], saved = [], events = [];
  const ui = {message: "", callsign: "", bound: 0, unbound: 0, connecting: [], hid: 0, cleared: 0, restored: 0, handlers: null};
  const roomHandlers = new Set();
  const ws = createWorkspace({log: quiet, timers: clock});
  ws.provide("openEventSource", (url) => {
    if (!stream) throw new Error("no EventSource");
    const es = {url, closed: false, close() { this.closed = true; }};
    sources.push(es);
    return es;
  });
  ws.provide("fetchJson", fetchImpl || (async (url) => { fetched.push(url); return history[url] ?? {messages: []}; }));
  ws.provide("postJson", postImpl || (async (url, body) => { posts.push({url, body}); return {ok: true}; }));
  ws.provide("chatView", {
    showConnecting: (room) => ui.connecting.push(room),
    hideConnecting: () => { ui.hid += 1; },
    addMessage: (m) => shown.push(m.message ?? m.id),
    callsign: () => ui.callsign,
    messageText: () => ui.message,
    clearMessage: () => { ui.cleared += 1; ui.message = ""; },
    saveCallsign: (t) => saved.push(t),
    restoreCallsign: () => { ui.restored += 1; },
    bind: (handlers) => { ui.bound += 1; ui.handlers = handlers; return () => { ui.unbound += 1; ui.handlers = null; }; },
  });
  ws.provide("roomChanges", {subscribe: (fn) => { roomHandlers.add(fn); return () => roomHandlers.delete(fn); }});
  const chat = createChatPlugin({log: quiet});
  ws.register(chat.tool);
  return {ws, chat, clock, sources, fetched, shown, posts, saved, ui, roomHandlers, events};
}
const H = "/api/chat/GLOBAL/messages", S = "/api/chat/GLOBAL/stream";
const sse = (es, data) => es.onmessage({data: JSON.stringify(data)});

test("activating connects to GLOBAL: history is shown and a stream is opened", async () => {
  const {ws, sources, ui, shown} = setup({history: {[H]: {messages: [{message: "a", timestamp: "t1"}, {message: "b", timestamp: "t2"}]}}});
  await ws.activate(CHAT_ID);
  await flush();
  assert.deepEqual(ui.connecting, [DEFAULT_ROOM]);
  assert.equal(ui.restored, 1);
  assert.equal(sources.length, 1);
  assert.equal(sources[0].url, S);
  assert.deepEqual(shown, ["a", "b"]);
  assert.equal(ui.hid, 1);
});

test("stream messages are shown, other types and bad JSON are ignored", async () => {
  const {ws, sources, shown} = setup();
  await ws.activate(CHAT_ID);
  sse(sources[0], {type: "connected"});
  sse(sources[0], {type: "chat", message: "hi"});
  sse(sources[0], {type: "ping"});
  sources[0].onmessage({data: "not json"});
  assert.deepEqual(shown, ["hi"]);
});

test("no stream event within 3 s: polling starts, with the last seen timestamp", async () => {
  const {ws, clock, fetched, shown} = setup({history: {[H]: {messages: [{message: "a", timestamp: "t1"}]}, [`${H}?since=t1`]: {messages: [{message: "b", timestamp: "t2"}]}}});
  await ws.activate(CHAT_ID);
  await flush();
  await clock.tick(2999);
  assert.deepEqual(fetched, [H], "still waiting on the stream");
  await clock.tick(1 + 2000);               // fallback fires at 3 s, first poll 2 s later
  assert.ok(fetched.includes(`${H}?since=t1`));
  assert.deepEqual(shown, ["a", "b"]);
});

test("a stream that says 'connected' in time means polling never starts", async () => {
  const {ws, clock, fetched, sources} = setup();
  await ws.activate(CHAT_ID);
  await flush();
  sse(sources[0], {type: "connected"});
  await clock.tick(10000);
  assert.deepEqual(fetched, [H], "history only");
});

test("a stream error starts polling, and a later 'connected' stops it", async () => {
  const {ws, clock, fetched, sources} = setup();
  await ws.activate(CHAT_ID);
  await flush();
  sources[0].onerror();
  await clock.tick(2000);
  assert.equal(fetched.length, 2);
  sources[0].onerror();                      // a second error does not start a second poller
  await clock.tick(2000);
  assert.equal(fetched.length, 3);
  sse(sources[0], {type: "connected"});
  await clock.tick(10000);
  assert.equal(fetched.length, 3, "polling stopped");
});

test("when no event stream can be opened it polls straight away", async () => {
  const {ws, clock, fetched} = setup({stream: false});
  await ws.activate(CHAT_ID);
  await flush();
  await clock.tick(2000);
  assert.equal(fetched.length, 2);
});

test("a failing poll is ignored and the next one tries again", async () => {
  let fail = true, calls = 0;
  const {ws, clock} = setup({stream: false, fetchImpl: async () => { calls += 1; if (calls > 1 && fail) throw new Error("offline"); return {messages: []}; }});
  await ws.activate(CHAT_ID);
  await flush();
  await clock.tick(2000);
  fail = false;
  await clock.tick(2000);
  assert.equal(calls, 3);
});

test("switching room closes the old connection completely and opens the new one", async () => {
  const {ws, chat, clock, sources, ui, fetched} = setup();
  await ws.activate(CHAT_ID);
  await flush();
  chat.controls.switchRoom("ROOM B");
  await flush();
  assert.equal(sources[0].closed, true);
  assert.equal(sources[1].url, "/api/chat/ROOM%20B/stream");
  assert.deepEqual(ui.connecting, [DEFAULT_ROOM, "ROOM B"]);
  assert.equal(chat.controls.room(), "ROOM B");
  assert.equal(clock.pending(), 1, "only the new connection's fallback timer is pending");
  await clock.tick(3000);
  assert.ok(!fetched.slice(2).some((u) => u.includes("/GLOBAL/")), "the old room is not polled after the switch");
});

test("a history fetch that lands after a room switch is dropped, not written into the new room's feed", async () => {
  const pending = new Map();
  const {ws, chat, shown} = setup({fetchImpl: (url) => new Promise((resolve) => pending.set(url, resolve))});
  await ws.activate(CHAT_ID);
  chat.controls.switchRoom("B");
  pending.get("/api/chat/GLOBAL/messages")({messages: [{message: "from the old room", timestamp: "x"}]});
  pending.get("/api/chat/B/messages")({messages: [{message: "from B", timestamp: "y"}]});
  await flush();
  assert.deepEqual(shown, ["from B"]);
});

test("a stream message from a closed connection is dropped", async () => {
  const {ws, chat, sources, shown} = setup();
  await ws.activate(CHAT_ID);
  chat.controls.switchRoom("B");
  sse(sources[0], {type: "chat", message: "stale"});
  assert.deepEqual(shown, []);
});

test("room changes from the room manager switch room, once per change", async () => {
  const {ws, sources, roomHandlers} = setup();
  await ws.activate(CHAT_ID);
  assert.equal(roomHandlers.size, 1);
  [...roomHandlers][0]("B");
  assert.equal(sources.length, 2);
});

test("sending posts the message and callsign to the current room, saves the callsign, then clears the input", async () => {
  const {ws, chat, posts, saved, ui} = setup();
  await ws.activate(CHAT_ID);
  ui.message = "hello"; ui.callsign = "ALPHA";
  await chat.controls.send();
  assert.deepEqual(posts, [{url: "/api/chat/GLOBAL/send", body: {message: "hello", callsign: "ALPHA"}}]);
  assert.deepEqual(saved, ["ALPHA"]);
  assert.equal(ui.cleared, 1);
  chat.controls.switchRoom("B");
  ui.message = "again";
  await chat.controls.send();
  assert.equal(posts.at(-1).url, "/api/chat/B/send");
});

test("an empty message sends nothing; a blank callsign is not saved", async () => {
  const {ws, chat, posts, saved, ui} = setup();
  await ws.activate(CHAT_ID);
  await chat.controls.send();
  assert.equal(posts.length, 0);
  ui.message = "anonymous";
  await chat.controls.send();
  assert.equal(posts.length, 1);
  assert.deepEqual(saved, []);
});

test("a failed send keeps the text so the operator can retry", async () => {
  const {ws, chat, ui} = setup({postImpl: async () => { throw new Error("502"); }});
  await ws.activate(CHAT_ID);
  ui.message = "keep me";
  await chat.controls.send();
  assert.equal(ui.cleared, 0);
  assert.equal(ui.message, "keep me");
});

test("the view's own events are wired to send and to saving the callsign", async () => {
  const {ws, posts, saved, ui} = setup();
  await ws.activate(CHAT_ID);
  ui.message = "via enter"; ui.callsign = "BRAVO";
  await ui.handlers.send();
  assert.equal(posts.length, 1);
  ui.callsign = "CHARLIE";
  ui.handlers.callsignChanged();
  assert.equal(saved.at(-1), "CHARLIE");
});

test("deactivating closes the stream, clears every timer, unbinds the view and unhooks the room manager", async () => {
  const {ws, clock, sources, ui, roomHandlers, fetched} = setup();
  await ws.activate(CHAT_ID);
  await flush();
  sources[0].onerror();                      // polling is running too
  assert.ok(clock.pending() >= 2);
  assert.deepEqual(await ws.deactivate(CHAT_ID), []);
  assert.equal(sources[0].closed, true);
  assert.equal(clock.pending(), 0);
  assert.equal(ui.unbound, 1);
  assert.equal(roomHandlers.size, 0);
  const before = fetched.length;
  await clock.tick(60000);
  assert.equal(fetched.length, before, "nothing polls after deactivation");
});

test("a history fetch that lands after deactivation is dropped", async () => {
  let release;
  const {ws, shown} = setup({fetchImpl: () => new Promise((resolve) => { release = resolve; })});
  await ws.activate(CHAT_ID);
  await ws.deactivate(CHAT_ID);
  release({messages: [{message: "late", timestamp: "t"}]});
  await flush();
  assert.deepEqual(shown, []);
});

test("the controls do nothing while the tool is inactive", async () => {
  const {chat, posts} = setup();
  assert.equal(await chat.controls.send(), undefined);
  assert.equal(chat.controls.switchRoom("B"), undefined);
  assert.equal(chat.controls.room(), null);
  assert.equal(posts.length, 0);
});

test("re-activation starts from GLOBAL with one connection, one binding and one room hook", async () => {
  const {ws, chat, sources, ui, roomHandlers, clock} = setup();
  await ws.activate(CHAT_ID);
  chat.controls.switchRoom("B");
  await ws.deactivate(CHAT_ID);
  await ws.activate(CHAT_ID);
  assert.equal(chat.controls.room(), DEFAULT_ROOM);
  assert.equal(sources.filter((s) => !s.closed).length, 1);
  assert.equal(ui.bound - ui.unbound, 1);
  assert.equal(roomHandlers.size, 1);
  assert.equal(clock.pending(), 1);
});

test("it is refused while a service is missing", async () => {
  const ws = createWorkspace({log: quiet});
  ws.provide("fetchJson", async () => ({}));
  ws.register(createChatPlugin({log: quiet}).tool);
  await assert.rejects(ws.activate(CHAT_ID), /openEventSource|postJson|timers|chatView|roomChanges/);
});

test("PINNED: stream-delivered messages do not advance the poll cursor, so a fallback poll can show them twice", async () => {
  const {ws, clock, sources, fetched, shown} = setup({history: {
    [H]: {messages: [{message: "old", timestamp: "t1"}]},
    [`${H}?since=t1`]: {messages: [{message: "streamed", timestamp: "t5"}]},
  }});
  await ws.activate(CHAT_ID);
  await flush();
  sse(sources[0], {type: "connected"});
  sse(sources[0], {type: "chat", message: "streamed", timestamp: "t5"});
  sources[0].onerror();                      // the stream drops, polling takes over
  await clock.tick(2000);
  assert.ok(fetched.includes(`${H}?since=t1`), "polls from the history timestamp, not from t5");
  assert.deepEqual(shown, ["old", "streamed", "streamed"]);
});
