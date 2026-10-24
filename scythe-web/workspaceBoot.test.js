import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {bootWorkspace} from "./workspaceBoot.js";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");
const quiet = {warn() {}, debug() {}, log() {}};

function fakeWindow(extra = {}) {
  const events = [];
  return {
    console: quiet, events,
    Event: class { constructor(type) { this.type = type; } },
    dispatchEvent(event) { events.push(event.type); },
    fetch: async () => ({ok: true, json: async () => ({tracks: []})}),
    ...extra,
  };
}

test("booting registers ADS-B inactive, exposes the workspace and announces it", () => {
  const win = fakeWindow();
  const workspace = bootWorkspace(win);
  assert.equal(win.ScytheWorkspace, workspace);
  assert.deepEqual(win.events, ["scythe:workspace_ready"]);
  assert.equal(workspace.state("adsb"), "inactive");
});

test("the services read the page's globals when called, not when the module loads", async () => {
  const win = fakeWindow();
  const workspace = bootWorkspace(win);          // AutoReconVisualization and WebSocketSync do not exist yet
  const listeners = [];
  const upserts = [], removes = [];
  win.WebSocketSync = {socket: {on: (n) => listeners.push(n), off() {}}};
  win.AutoReconVisualization = {addEntityMarker: (e) => upserts.push(e.entity_id), removeEntityMarker: (id) => removes.push(id)};
  await workspace.activate("adsb");
  assert.deepEqual(listeners.sort(), ["adsb_snapshot", "adsb_update"]);
  win.WebSocketSync.socket.on = (n, fn) => { if (n === "adsb_update") fn({icao: "X1", lat: 1, lon: 2}); };
  await workspace.deactivate("adsb");
  await workspace.activate("adsb");
  assert.deepEqual(upserts, ["adsb_X1"]);
  await workspace.deactivate("adsb");
  assert.deepEqual(removes, ["adsb_X1"]);
});

test("a renderer that is not there yet is tolerated, not thrown on", async () => {
  const win = fakeWindow({WebSocketSync: {socket: {on: (n, fn) => { if (n === "adsb_update") fn({icao: "X1", lat: 1, lon: 2}); }, off() {}}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("adsb");
  assert.deepEqual(await workspace.deactivate("adsb"), []);
});

test("the REST seed rejects on an HTTP error instead of parsing an error page", async () => {
  const win = fakeWindow({fetch: async () => ({ok: false, status: 502, json: async () => { throw new Error("not json"); }})});
  const workspace = bootWorkspace(win);
  await workspace.activate("adsb");               // the seed failure is logged, activation still succeeds
  assert.equal(workspace.state("adsb"), "active");
});

// --- the page itself ---

test("the page boots the workspace and activates ADS-B where AISStreamManager starts", () => {
  assert.match(page, /import \{ bootWorkspace \} from "\.\/scythe-web\/workspaceBoot\.js";/);
  assert.match(page, /window\.ScytheWorkspace\.activate\('adsb'\)/);
  assert.match(page, /addEventListener\('scythe:workspace_ready', activateAdsb, \{ once: true \}\)/);
});

test("the old inline ADS-B handlers are gone, so aircraft are not handled twice", () => {
  assert.doesNotMatch(page, /handleAircraftUpdate/);
  assert.doesNotMatch(page, /socket\.on\('adsb_update'/);
  assert.doesNotMatch(page, /fetch\('\/api\/adsb\/tracks'\)/);
});

test("AutoReconVisualization.removeEntityMarker removes the marker, its altitude line and threat ring from both collections", () => {
  const start = page.indexOf("            removeEntityMarker: function(entityId) {");
  assert.ok(start > 0, "removeEntityMarker is defined on the page");
  const end = page.indexOf("            clearAllEntities: function() {", start);
  const source = page.slice(start, end).trim().replace(/,$/, "").replace(/^removeEntityMarker: /, "");
  const removeEntityMarker = new Function(`return (${source})`)();

  const collection = (items = {}) => ({
    items: {...items},
    getById(id) { return this.items[id]; },
    contains(m) { return Object.values(this.items).includes(m); },
    remove(m) { for (const k of Object.keys(this.items)) if (this.items[k] === m) delete this.items[k]; return true; },
  });
  const marker = {n: "marker"}, alt = {n: "alt"}, ring = {n: "ring"}, other = {n: "other"};
  const self = {
    dataSource: {entities: collection({recon_entity_adsb_1: marker, recon_entity_adsb_2: other})},
    viewer: {entities: collection({alt_line: alt, threat_ring: ring})},
    entityMarkers: {adsb_1: marker, adsb_1_alt: alt, adsb_1_threat: ring, adsb_2: other},
  };
  removeEntityMarker.call(self, "adsb_1");
  assert.deepEqual(Object.keys(self.dataSource.entities.items), ["recon_entity_adsb_2"]);
  assert.deepEqual(Object.keys(self.viewer.entities.items), []);
  assert.deepEqual(Object.keys(self.entityMarkers), ["adsb_2"]);
  removeEntityMarker.call(self, "adsb_1");                       // removing again is harmless
  removeEntityMarker.call({entityMarkers: {}}, "adsb_9");        // no viewer, no data source
});
