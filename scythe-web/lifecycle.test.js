import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace, STATES} from "./lifecycle.js";

const quiet = {warn() {}, debug() {}};
const make = () => createWorkspace({log: quiet});
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test("a tool starts inactive, activates once, and activating again does nothing", async () => {
  const ws = make();
  let activations = 0;
  ws.register({id: "t", activate() { activations += 1; }});
  assert.equal(ws.state("t"), STATES.INACTIVE);
  await ws.activate("t");
  await ws.activate("t");
  assert.equal(activations, 1);
  assert.equal(ws.state("t"), STATES.ACTIVE);
});

test("concurrent activations share one attempt", async () => {
  const ws = make();
  let activations = 0, release;
  ws.register({id: "t", activate() { activations += 1; return new Promise((r) => { release = r; }); }});
  const first = ws.activate("t"), second = ws.activate("t");
  assert.equal(ws.state("t"), STATES.ACTIVATING);
  release();
  await Promise.all([first, second]);
  assert.equal(activations, 1);
});

test("cleanups run on deactivate, last registered first, after the tool's own deactivate", async () => {
  const ws = make(), order = [];
  ws.register({
    id: "t",
    activate(ctx) { ctx.onCleanup(() => order.push("a")); ctx.onCleanup(() => order.push("b")); },
    deactivate() { order.push("own"); },
  });
  await ws.activate("t");
  assert.deepEqual(await ws.deactivate("t"), []);
  assert.deepEqual(order, ["own", "b", "a"]);
  assert.equal(ws.state("t"), STATES.INACTIVE);
});

test("a tool can be activated again after deactivation and gets fresh cleanups", async () => {
  const ws = make();
  let undone = 0;
  ws.register({id: "t", activate(ctx) { ctx.onCleanup(() => { undone += 1; }); }});
  await ws.activate("t"); await ws.deactivate("t");
  await ws.activate("t"); await ws.deactivate("t");
  assert.equal(undone, 2);
});

test("a failed activation is rolled back: cleanups taken so far run, the tool is inactive, the error is kept", async () => {
  const ws = make();
  let undone = 0;
  const boom = new Error("boom");
  ws.register({id: "t", activate(ctx) { ctx.onCleanup(() => { undone += 1; }); throw boom; }});
  await assert.rejects(ws.activate("t"), boom);
  assert.equal(undone, 1);
  assert.equal(ws.state("t"), STATES.INACTIVE);
  assert.equal(ws.error("t"), boom);
});

test("teardown never stops half way: every step runs and the errors come back", async () => {
  const ws = make(), ran = [];
  ws.register({
    id: "t",
    activate(ctx) { ctx.onCleanup(() => ran.push("first")); ctx.onCleanup(() => { throw new Error("bad cleanup"); }); ctx.onCleanup(() => ran.push("last")); },
    deactivate() { throw new Error("bad deactivate"); },
  });
  await ws.activate("t");
  const errors = await ws.deactivate("t");
  assert.deepEqual(ran, ["last", "first"]);
  assert.deepEqual(errors.map((e) => e.message), ["bad deactivate", "bad cleanup"]);
  assert.equal(ws.state("t"), STATES.INACTIVE);
});

test("a tool is refused while a service it requires is missing, and nothing of it runs", async () => {
  const ws = make();
  let ran = false;
  ws.register({id: "t", requires: ["socket", "sink"], activate() { ran = true; }});
  ws.provide("sink", {});
  await assert.rejects(ws.activate("t"), /socket/);
  assert.equal(ran, false);
  assert.equal(ws.state("t"), STATES.INACTIVE);
  ws.provide("socket", {});
  await ws.activate("t");
  assert.equal(ran, true);
});

test("a tool may only read services it declared", async () => {
  const ws = make();
  ws.provide("declared", 1); ws.provide("other", 2);
  ws.register({id: "t", requires: ["declared"], activate(ctx) { assert.equal(ctx.services.get("declared"), 1); ctx.services.get("other"); }});
  await assert.rejects(ws.activate("t"), /did not declare/);
});

test("deactivating while activation is in flight waits for it, then tears down", async () => {
  const ws = make();
  let release, undone = 0;
  ws.register({id: "t", activate(ctx) { ctx.onCleanup(() => { undone += 1; }); return new Promise((r) => { release = r; }); }});
  const activating = ws.activate("t");
  const deactivating = ws.deactivate("t");
  await tick();
  assert.equal(undone, 0, "not torn down before activation finished");
  release();
  await activating;
  await deactivating;
  assert.equal(undone, 1);
  assert.equal(ws.state("t"), STATES.INACTIVE);
});

test("activating while deactivation is in flight waits for it, then activates once", async () => {
  const ws = make();
  let releaseCleanup, activations = 0;
  ws.register({id: "t", activate(ctx) { activations += 1; ctx.onCleanup(() => new Promise((r) => { releaseCleanup = r; })); }});
  await ws.activate("t");
  const deactivating = ws.deactivate("t");
  const again = [ws.activate("t"), ws.activate("t")];
  await tick();
  assert.equal(ws.state("t"), STATES.DEACTIVATING);
  releaseCleanup();
  await deactivating;
  await Promise.all(again);
  assert.equal(activations, 2, "one initial activation and one re-activation, not two");
  assert.equal(ws.state("t"), STATES.ACTIVE);
});

test("dispose deactivates, calls the tool's dispose once, and a disposed tool cannot be activated", async () => {
  const ws = make(), order = [];
  ws.register({id: "t", activate(ctx) { ctx.onCleanup(() => order.push("cleanup")); }, dispose() { order.push("dispose"); }});
  await ws.activate("t");
  assert.deepEqual(await ws.dispose("t"), []);
  assert.deepEqual(await ws.dispose("t"), []);
  assert.deepEqual(order, ["cleanup", "dispose"]);
  assert.equal(ws.state("t"), STATES.DISPOSED);
  await assert.rejects(ws.activate("t"), /disposed/);
});

test("disposeAll retires tools last registered first and reports failures by id", async () => {
  const ws = make(), order = [];
  for (const id of ["a", "b", "c"]) {
    ws.register({id, activate() {}, dispose() { order.push(id); if (id === "b") throw new Error("nope"); }});
    await ws.activate(id);
  }
  const failures = await ws.disposeAll();
  assert.deepEqual(order, ["c", "b", "a"]);
  assert.deepEqual(Object.keys(failures), ["b"]);
});

test("definitions are validated and ids are unique", async () => {
  const ws = make();
  assert.throws(() => ws.register(null), TypeError);
  assert.throws(() => ws.register({id: "Bad Id", activate() {}}), /id must match/);
  assert.throws(() => ws.register({id: "t"}), /activate/);
  assert.throws(() => ws.register({id: "t", activate() {}, dispose: 1}), /dispose/);
  assert.throws(() => ws.register({id: "t", activate() {}, requires: [1]}), /requires/);
  ws.register({id: "t", activate() {}});
  assert.throws(() => ws.register({id: "t", activate() {}}), /already registered/);
  assert.throws(() => ws.provide("s", 1) || ws.provide("s", 2), /already provided/);
  await assert.rejects(ws.activate("missing"), /unknown tool/);
});

test("list reports each tool's title, requirements and state", async () => {
  const ws = make();
  ws.provide("s", 1);
  ws.register({id: "t", title: "Tool", requires: ["s"], activate() {}});
  await ws.activate("t");
  assert.deepEqual(ws.list(), [{id: "t", title: "Tool", requires: ["s"], state: "active", error: null}]);
});
