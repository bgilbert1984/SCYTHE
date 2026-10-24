import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {createPresencePlugin, PRESENCE_ID, POLL_MS, NO_OPERATORS, roleColorName, roleIcon, renderPresenceList, indicatorsOf} from "./presencePlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const settle = () => new Promise((resolve) => setImmediate(resolve));
const gate = () => { let open; const promise = new Promise((resolve) => { open = resolve; }); return {promise, open}; };

function fakeClock() {
  let at = 0, next = 1;
  const timers = new Map();
  return {
    setInterval: (fn, ms) => { const id = next++; timers.set(id, {at: at + ms, fn, every: ms}); return id; },
    clearInterval: (id) => timers.delete(id),
    setTimeout: () => 0, clearTimeout() {},
    async tick(ms) {
      const target = at + ms;
      for (;;) {
        const due = [...timers.values()].filter((t) => t.at <= target).sort((a, b) => a.at - b.at)[0];
        if (!due) break;
        at = due.at;
        due.at += due.every;
        due.fn();
        await settle();
      }
      at = target;
    },
    pending: () => timers.size,
  };
}

// --- pure pieces ---

test("roleColorName and roleIcon: one per known role, white and the plain operator's for anything else", () => {
  assert.deepEqual(["admin", "supervisor", "operator", "observer", "guest", undefined].map(roleColorName), ["RED", "ORANGE", "CYAN", "GRAY", "WHITE", "WHITE"]);
  assert.deepEqual(["admin", "supervisor", "operator", "observer", "guest", undefined].map(roleIcon), ["👑", "⭐", "👤", "👁️", "👤", "👤"]);
});

test("renderPresenceList: one row per operator with icon, callsign and role; the current operator is marked; none is a message", () => {
  assert.equal(renderPresenceList([], "me"), NO_OPERATORS);
  const html = renderPresenceList([{operator_id: "a", callsign: "ALPHA", role: "admin"}, {operator_id: "me", callsign: "BRAVO", role: "observer"}], "me");
  assert.equal((html.match(/operator-presence-item/g) || []).length, 2);
  assert.match(html, /class="operator-presence-item " data-operator-id="a"/);
  assert.match(html, /class="operator-presence-item current-operator" data-operator-id="me"/);
  assert.match(html, /operator-icon">👑</);
  assert.match(html, /operator-callsign">ALPHA</);
  assert.match(html, /operator-callsign">BRAVO \(You\)</);
  assert.match(html, /operator-role">observer</);
  assert.doesNotMatch(renderPresenceList([{operator_id: "a", callsign: "A", role: "x"}], undefined), /\(You\)/);
});

test("renderPresenceList escapes the id, the callsign and the role", () => {
  const evil = "<img src=x onerror=alert(1)>";
  const html = renderPresenceList([{operator_id: `a"${evil}`, callsign: evil, role: evil}], "me");
  assert.doesNotMatch(html, /<img/);
  assert.doesNotMatch(html, /data-operator-id="a"</);
  assert.equal((html.match(/&lt;img src=x onerror=alert\(1\)&gt;/g) || []).length, 3);
});

test("indicatorsOf: the others who have a view with a centre, at [lat, lon], coloured by role", () => {
  const ops = [
    {operator_id: "me", callsign: "ME", role: "admin", current_view: {center: [1, 2]}},
    {operator_id: "a", callsign: "ALPHA", role: "supervisor", current_view: {center: [36.1, -86.7], altitude: 5000}},
    {operator_id: "b", callsign: "BRAVO", role: "guest", current_view: {}},
    {operator_id: "c", callsign: "CHARLIE", role: "operator"},
    {operator_id: "d", callsign: "DELTA", role: "observer", current_view: {center: [10, 20]}},
  ];
  assert.deepEqual(indicatorsOf(ops, "me"), [
    {id: "a", lat: 36.1, lon: -86.7, label: "👤 ALPHA", color: "ORANGE"},
    {id: "d", lat: 10, lon: 20, label: "👤 DELTA", color: "GRAY"},
  ]);
  assert.equal(indicatorsOf(ops, undefined).length, 3);   // with no current operator, everyone with a view shows
  assert.deepEqual(indicatorsOf([], "me"), []);
});

test("PINNED: a view whose centre is not a pair throws", () => {
  assert.throws(() => indicatorsOf([{operator_id: "a", callsign: "A", role: "admin", current_view: {center: 5}}], "me"), TypeError);
});

// --- the tool ---

function setup({operators = [], myId = "me", listExists = true, globeAvailable = true} = {}) {
  const clock = fakeClock();
  const state = {operators, myId, listExists, calls: [], lists: [], attached: [], replaced: [], cleared: 0, errors: [], fail: null, hold: null};
  const source = {
    operators: async () => { state.calls.push("fetch"); if (state.hold) await state.hold; if (state.fail) throw state.fail; return state.operators; },
    myId: () => state.myId,
  };
  const view = {setList: (html) => { if (!state.listExists) return false; state.lists.push(html); return true; }};
  const globe = {
    attach: (viewer) => { state.attached.push(viewer); state.viewer = viewer; },
    available: () => Boolean(state.viewer) && globeAvailable,
    replace: (indicators) => state.replaced.push(indicators),
    clear: () => { state.cleared++; },
  };
  const log = {...quiet, error: (...a) => state.errors.push(a)};
  const workspace = createWorkspace({log: quiet, timers: clock});
  workspace.provide("presenceSource", source);
  workspace.provide("presenceView", view);
  workspace.provide("presenceGlobe", globe);
  const plugin = createPresencePlugin({log});
  workspace.register(plugin.tool);
  return {workspace, controls: plugin.controls, clock, state};
}
const up = async (t) => { await t.workspace.activate(PRESENCE_ID); return t; };
const OPS = [
  {operator_id: "me", callsign: "ME", role: "admin", current_view: {center: [1, 2]}},
  {operator_id: "a", callsign: "ALPHA", role: "operator", current_view: {center: [36, -86]}},
];

test("while inactive: no operators, refreshing resolves and asks nothing, toggling does nothing, and a viewer is remembered", async () => {
  const t = setup({operators: OPS});
  assert.deepEqual(t.controls.operators, {});
  const answer = t.controls.fetchActiveOperators();
  assert.ok(answer instanceof Promise);   // callers can await it either way
  assert.equal(await answer, undefined);
  assert.equal(t.controls.toggleVisibility(false), undefined);
  assert.deepEqual(t.state.calls, []);
  assert.equal(t.controls.initialize({v: 1}), undefined);
  assert.deepEqual(t.state.attached, []);
});

test("the Refresh button works before the viewer is handed over; nothing is polled and no marker is drawn until then", async () => {
  const t = await up(setup({operators: OPS}));
  await t.controls.fetchActiveOperators();
  assert.deepEqual(Object.keys(t.controls.operators), ["me", "a"]);
  assert.equal(t.state.lists.length, 1);
  assert.deepEqual(t.state.replaced, []);   // no viewer yet
  await t.clock.tick(POLL_MS * 3);
  assert.deepEqual(t.state.calls, ["fetch"]);
  assert.equal(t.clock.pending(), 0);
});

test("initialize attaches the viewer, fetches at once, and again every 10 s", async () => {
  const t = await up(setup({operators: OPS}));
  const viewer = {v: 1};
  t.controls.initialize(viewer);
  assert.deepEqual(t.state.attached, [viewer]);
  await settle();
  assert.deepEqual(t.state.calls, ["fetch"]);
  assert.equal(t.clock.pending(), 1);
  await t.clock.tick(POLL_MS - 1);
  assert.equal(t.state.calls.length, 1);
  await t.clock.tick(1);
  assert.equal(t.state.calls.length, 2);
  await t.clock.tick(POLL_MS);
  assert.equal(t.state.calls.length, 3);
  assert.equal(POLL_MS, 10000);
});

test("a second initialize hands over the viewer again without a second timer or fetch", async () => {
  const t = await up(setup({operators: OPS}));
  t.controls.initialize({v: 1});
  await settle();
  t.controls.initialize({v: 2});
  await settle();
  assert.deepEqual(t.state.attached, [{v: 1}, {v: 2}]);
  assert.equal(t.clock.pending(), 1);
  assert.equal(t.state.calls.length, 1);
});

test("a refresh keeps the operators by id, lists them with the current one marked, and marks where the others are looking", async () => {
  const t = await up(setup({operators: OPS}));
  t.controls.initialize({v: 1});
  await settle();
  assert.deepEqual(Object.keys(t.controls.operators), ["me", "a"]);
  assert.match(t.state.lists[0], /ME \(You\)/);
  assert.deepEqual(t.state.replaced[0], [{id: "a", lat: 36, lon: -86, label: "👤 ALPHA", color: "CYAN"}]);
  t.state.operators = [OPS[1]];
  await t.controls.fetchActiveOperators();
  assert.deepEqual(Object.keys(t.controls.operators), ["a"]);   // replaced, not merged
  assert.equal(t.state.replaced.length, 2);
});

test("no operators lists the empty message and clears the markers", async () => {
  const t = await up(setup({operators: []}));
  t.controls.initialize({v: 1});
  await settle();
  assert.equal(t.state.lists[0], NO_OPERATORS);
  assert.deepEqual(t.state.replaced[0], []);
});

test("turning the markers off takes them down and they stay down while off; the list still updates; turning them on puts them back", async () => {
  const t = await up(setup({operators: OPS}));
  t.controls.initialize({v: 1});
  await settle();
  assert.equal(t.state.replaced.length, 1);
  assert.equal(t.state.replaced[0].length, 1);
  t.controls.toggleVisibility(false);
  assert.equal(t.state.replaced.length, 2);
  assert.deepEqual(t.state.replaced[1], []);   // replaced with nothing: the markers come down
  await t.controls.fetchActiveOperators();
  assert.deepEqual(t.state.replaced.at(-1), []);   // a refresh while off keeps the set empty
  assert.equal(t.state.lists.length, 2);      // the list still updates
  t.controls.toggleVisibility(true);
  assert.equal(t.state.replaced.at(-1).length, 1);
  t.controls.toggleVisibility(true);
  assert.equal(t.state.replaced.at(-1).length, 1);   // on again is harmless
});

test("the markers are not worked out while they are off, so a malformed view does not stop the list", async () => {
  const t = await up(setup({operators: OPS}));
  t.controls.initialize({v: 1});
  await settle();
  t.controls.toggleVisibility(false);
  t.state.operators = [OPS[1], {operator_id: "z", callsign: "Z", role: "x", current_view: {center: 5}}];
  const lists = t.state.lists.length;
  await t.controls.fetchActiveOperators();
  assert.equal(t.state.errors.length, 0);
  assert.equal(t.state.lists.length, lists + 1);
});

test("turning the markers off before the viewer is handed over does nothing, and they are not drawn when it arrives while off", async () => {
  const t = await up(setup({operators: OPS}));
  t.controls.toggleVisibility(false);
  assert.deepEqual(t.state.replaced, []);   // no globe yet
  t.controls.initialize({v: 1});
  await settle();
  assert.deepEqual(t.state.replaced, [[]]);
});

test("the list is skipped when the page has none; markers are skipped when the globe is not there", async () => {
  const noList = await up(setup({operators: OPS, listExists: false}));
  noList.controls.initialize({v: 1});
  await settle();
  assert.deepEqual(noList.state.lists, []);
  assert.equal(noList.state.replaced.length, 1);
  const noGlobe = await up(setup({operators: OPS, globeAvailable: false}));
  noGlobe.controls.initialize({v: 1});
  await settle();
  assert.deepEqual(noGlobe.state.replaced, []);
  assert.equal(noGlobe.state.lists.length, 1);
});

test("a failed refresh is logged and keeps what was there", async () => {
  const t = await up(setup({operators: OPS}));
  await t.controls.fetchActiveOperators();
  t.state.fail = new Error("offline");
  await t.controls.fetchActiveOperators();
  assert.equal(t.state.errors[0][0], "[OperatorPresence] Error fetching operators:");
  assert.deepEqual(Object.keys(t.controls.operators), ["me", "a"]);
  t.state.fail = null;
  t.state.operators = undefined;   // a list that is not a list
  await t.controls.fetchActiveOperators();
  assert.equal(t.state.errors.length, 2);
});

test("PINNED: one operator with a malformed view stops the list updating, and the markers already up stay", async () => {
  const t = await up(setup({operators: OPS}));
  t.controls.initialize({v: 1});
  await settle();
  const lists = t.state.lists.length, replaced = t.state.replaced.length;
  t.state.operators = [OPS[1], {operator_id: "z", callsign: "Z", role: "x", current_view: {center: 5}}];
  await t.controls.fetchActiveOperators();
  assert.equal(t.state.errors.length, 1);
  assert.equal(t.state.lists.length, lists);
  assert.equal(t.state.replaced.length, replaced);   // the previous markers are left, not a half-drawn set
  assert.deepEqual(Object.keys(t.controls.operators), ["a", "z"]);
});

test("a refresh that finishes after deactivation changes and draws nothing", async () => {
  const t = await up(setup({operators: OPS}));
  const hold = gate();
  t.state.hold = hold.promise;
  const pending = t.controls.fetchActiveOperators();
  await settle();
  await t.workspace.deactivate(PRESENCE_ID);
  hold.open();
  await pending;
  assert.deepEqual([t.state.lists, t.state.replaced], [[], []]);
  assert.deepEqual(t.controls.operators, {});
});

test("deactivating stops the polling and clears the markers; a new activation starts a fresh one when the viewer is handed over again", async () => {
  const t = await up(setup({operators: OPS}));
  t.controls.initialize({v: 1});
  await settle();
  assert.deepEqual(await t.workspace.deactivate(PRESENCE_ID), []);
  assert.equal(t.clock.pending(), 0);
  assert.equal(t.state.cleared, 1);
  assert.deepEqual(t.controls.operators, {});
  const before = t.state.calls.length;
  await t.clock.tick(POLL_MS * 3);
  assert.equal(t.state.calls.length, before);
  await up(t);
  t.controls.initialize({v: 2});
  await settle();
  assert.equal(t.clock.pending(), 1);
  assert.equal(t.state.calls.length, before + 1);
});

test("a viewer handed over before the tool is active is used when it activates, once", async () => {
  const t = setup({operators: OPS});
  t.controls.initialize({v: 1});
  await up(t);
  await settle();
  assert.deepEqual(t.state.attached, [{v: 1}]);
  assert.equal(t.state.calls.length, 1);
  await t.workspace.deactivate(PRESENCE_ID);
  await up(t);
  assert.deepEqual(t.state.attached, [{v: 1}]);   // not again
});
