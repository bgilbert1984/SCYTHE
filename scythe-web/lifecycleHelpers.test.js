import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";

const quiet = {warn() {}, debug() {}};
const flush = () => new Promise((resolve) => setImmediate(resolve));
function clock() {
  let next = 1; const t = new Map();
  return {t, setTimeout: (fn, ms) => { t.set(next, {fn, every: 0, ms}); return next++; }, setInterval: (fn, ms) => { t.set(next, {fn, every: ms}); return next++; },
    clearTimeout: (id) => t.delete(id), clearInterval: (id) => t.delete(id), fire: (id) => t.get(id)?.fn(), ids: () => [...t.keys()]};
}
const emitter = () => { const h = new Map(); return {h, on(e, f) { h.set(e, [...(h.get(e) || []), f]); }, off(e, f) { h.set(e, (h.get(e) || []).filter((x) => x !== f)); }, emit(e, p) { for (const f of h.get(e) || []) f(p); }, n: (e) => (h.get(e) || []).length}; };
const domTarget = () => { const l = []; return {l, addEventListener(e, f, o) { l.push([e, f, o]); }, removeEventListener(e, f, o) { const i = l.findIndex((x) => x[0] === e && x[1] === f && x[2] === o); if (i >= 0) l.splice(i, 1); }}; };
async function run(activate, opts = {}) {
  const ws = createWorkspace({log: quiet, ...opts});
  ws.register({id: "t", activate});
  await ws.activate("t");
  return ws;
}

test("listen subscribes now and unsubscribes on deactivate, for on/off emitters", async () => {
  const e = emitter(); let seen = 0;
  const ws = await run((ctx) => { ctx.listen(e, "x", () => { seen += 1; }); });
  e.emit("x"); assert.equal(seen, 1);
  await ws.deactivate("t");
  assert.equal(e.n("x"), 0);
});

test("listen works for DOM-style targets and removes with the same options", async () => {
  const d = domTarget();
  const ws = await run((ctx) => { ctx.listen(d, "click", () => {}, {capture: true}); });
  assert.equal(d.l.length, 1);
  await ws.deactivate("t");
  assert.equal(d.l.length, 0, "a different options object would leave it attached");
});

test("guard: a callback does nothing once the tool is deactivated, and is live before", async () => {
  let calls = 0, cb;
  const ws = await run((ctx) => { cb = ctx.guard(() => { calls += 1; return "ran"; }); });
  assert.equal(cb(), "ran");
  await ws.deactivate("t");
  assert.equal(cb(), undefined);
  assert.equal(calls, 1);
});

test("the abort signal is aborted before any cleanup runs, so a callback fired during an async cleanup is dropped", async () => {
  let cb, seenDuringCleanup = null, calls = 0;
  const ws = await run((ctx) => {
    cb = ctx.guard(() => { calls += 1; });
    ctx.onCleanup(async () => { seenDuringCleanup = ctx.signal.aborted; await flush(); cb(); });
  });
  await ws.deactivate("t");
  assert.equal(seenDuringCleanup, true);
  assert.equal(calls, 0);
});

test("interval and timeout are cleared on deactivate, can be stopped early, and a fired callback is guarded", async () => {
  const c = clock(); let stopInterval, stopTimeout, ticks = 0;
  const ws = await run((ctx) => { stopInterval = ctx.interval(() => { ticks += 1; }, 10); stopTimeout = ctx.timeout(() => {}, 10); }, {timers: c});
  assert.equal(c.ids().length, 2);
  stopTimeout();
  assert.equal(c.ids().length, 1);
  await ws.deactivate("t");
  assert.equal(c.ids().length, 0);
  void stopInterval; void ticks;
});

test("a child scope is closed when its parent is, and can be closed alone", async () => {
  const c = clock(); let child1, child2, undone = [];
  const ws = await run((ctx) => {
    child1 = ctx.scope(); child2 = ctx.scope();
    child1.onCleanup(() => undone.push("c1")); child2.onCleanup(() => undone.push("c2"));
    child2.interval(() => {}, 5);
  }, {timers: c});
  await child1.close();
  assert.deepEqual(undone, ["c1"]);
  assert.equal(child1.signal.aborted, true);
  await ws.deactivate("t");
  assert.deepEqual(undone, ["c1", "c2"]);
  assert.equal(c.ids().length, 0, "the child's timer went with it");
  assert.equal(child2.signal.aborted, true);
});

test("a resource registered after its scope closed is undone at once, not leaked", async () => {
  let late, undone = 0;
  const ws = await run((ctx) => { late = ctx; });
  await ws.deactivate("t");
  late.onCleanup(() => { undone += 1; });
  await flush();
  assert.equal(undone, 1);
});

test("errors from a child scope's cleanup are reported by the deactivation, not swallowed", async () => {
  let child;
  const ws = await run((ctx) => { child = ctx.scope(); child.onCleanup(() => { throw new Error("child cleanup failed"); }); });
  const errors = await ws.deactivate("t");
  assert.equal(errors.length, 1);
  assert.match(String(errors[0].message), /child cleanup failed/);
});

test("closing a scope twice runs its cleanups once", async () => {
  let n = 0, child;
  await run((ctx) => { child = ctx.scope(); child.onCleanup(() => { n += 1; }); });
  await child.close(); await child.close();
  assert.equal(n, 1);
});

test("guarded callbacks are already dropped while the tool's own deactivate() hook runs", async () => {
  let cb, calls = 0;
  const ws = createWorkspace({log: quiet});
  ws.register({
    id: "t",
    activate(ctx) { cb = ctx.guard(() => { calls += 1; }); },
    async deactivate() { await flush(); cb(); },
  });
  await ws.activate("t");
  await ws.deactivate("t");
  assert.equal(calls, 0);
});
