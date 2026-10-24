import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {createViewshedPlugin, VIEWSHED_ID, DEFAULT_CONFIG, wedgePoints, rayEnds, haversine, checkLineOfSight} from "./viewshedPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const near = (actual, expected, tolerance = 1e-9) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} is not within ${tolerance} of ${expected}`);

// --- pure pieces ---

test("DEFAULT_CONFIG: the old defaults, colours as names with an alpha", () => {
  assert.equal(DEFAULT_CONFIG.horizontalFov, 60);
  assert.equal(DEFAULT_CONFIG.verticalFov, 45);
  assert.equal(DEFAULT_CONFIG.distance, 20000);
  assert.equal(DEFAULT_CONFIG.rayCount, 36);
  assert.equal(DEFAULT_CONFIG.verticalRays, 8);
  assert.deepEqual(DEFAULT_CONFIG.visibleColor, {name: "GREEN", alpha: 0.25});
  assert.deepEqual(DEFAULT_CONFIG.hiddenColor, {name: "RED", alpha: 0.25});
  assert.deepEqual(DEFAULT_CONFIG.lineColor, {name: "YELLOW", alpha: 0.6});
  assert.ok(Object.isFrozen(DEFAULT_CONFIG));
});

test("wedgePoints: the observer, 25 points along the arc, the observer again", () => {
  const points = wedgePoints({lon: 10, lat: 20}, 0, 60, 111320);
  assert.equal(points.length, 2 + 25 * 2 + 2);
  assert.deepEqual(points.slice(0, 2), [10, 20]);
  assert.deepEqual(points.slice(-2), [10, 20]);
  near(points[2], 10 + Math.sin(-Math.PI / 6));   // the first arc point is 30 degrees left of the direction, one degree of arc away
  near(points[3], 20 + Math.cos(-Math.PI / 6));
  near(points[2 + 24], 10 + Math.sin(0));          // the middle point is straight ahead
  near(points[3 + 24], 21);
  near(points[2 + 48], 10 + Math.sin(Math.PI / 6));
  const east = wedgePoints({lon: 0, lat: 0}, 90, 60, 111320);
  near(east[2 + 24], 1);
  near(east[3 + 24], 0, 1e-9);
});

test("wedgePoints: the arc is a full circle for 360 degrees and the range scales it", () => {
  const points = wedgePoints({lon: 0, lat: 0}, 0, 360, 55660);
  near(points[2], 0.5 * Math.sin(-Math.PI), 1e-9);
  near(points[3], 0.5 * Math.cos(-Math.PI), 1e-9);
  near(points[points.length - 4], 0.5 * Math.sin(Math.PI), 1e-9);
});

test("rayEnds: rayCount rays spread across the field of view, from direction - fov/2", () => {
  const ends = rayEnds({lon: 0, lat: 0}, 90, 60, 111320, 3);
  assert.equal(ends.length, 3);
  near(ends[0].lon, Math.sin(radiansOf(60)));
  near(ends[0].lat, Math.cos(radiansOf(60)));
  near(ends[1].lon, 1);
  near(ends[1].lat, 0, 1e-9);
  near(ends[2].lon, Math.sin(radiansOf(120)));
  assert.equal(rayEnds({lon: 0, lat: 0}, 0, 60, 1000, 36).length, 36);
  assert.equal(rayEnds({lon: 0, lat: 0}, 0, 60, 1000, 0).length, 0);
});
const radiansOf = (d) => (d * Math.PI) / 180;

test("PINNED: a single ray lands at NaN (the angle step divides by zero)", () => {
  const [only] = rayEnds({lon: 0, lat: 0}, 0, 60, 1000, 1);
  assert.ok(Number.isNaN(only.lon) && Number.isNaN(only.lat));
});

test("haversine: meters between two points on a sphere of 6371 km", () => {
  assert.equal(haversine(10, 20, 10, 20), 0);
  near(haversine(0, 0, 1, 0), 6371000 * Math.PI / 180, 1e-6);
  near(haversine(0, 0, 0, 1), 6371000 * Math.PI / 180, 1e-6);
  near(haversine(10, 20, 30, 40), haversine(30, 40, 10, 20), 1e-6);
  near(haversine(0, 0, 0, 180), 6371000 * Math.PI, 1e-3);
  near(haversine(60, 0, 60, 1), 6371000 * Math.cos(radiansOf(60)) * Math.PI / 180, 100);
});

const START = {lon: 0, lat: 0, height: 1000};
const END = {lon: 1, lat: 0, height: 0};

test("checkLineOfSight with terrain: clear when no sample is above the line", () => {
  const seen = [];
  const terrain = {elevation: (lat, lon) => { seen.push([lat, lon]); return 0; }};
  const r = checkLineOfSight(START, END, 20000, terrain);
  assert.deepEqual(r, {blocked: false, lon: 1, lat: 0, height: 0});
  assert.equal(seen.length, 9);
  near(seen[0][1], 0.1);
  near(seen[8][1], 0.9);
});

test("checkLineOfSight with terrain: blocked at the first sample whose terrain is above the line, which is where the ray stops", () => {
  const heights = [];
  const terrain = {elevation: (lat, lon) => { heights.push(lon); return lon >= 0.45 ? 5000 : 0; }};
  const r = checkLineOfSight(START, END, 20000, terrain);
  assert.equal(r.blocked, true);
  near(r.lon, 0.5);
  assert.equal(r.lat, 0);
  assert.equal(r.height, 5000);
  assert.equal(heights.length, 5);   // it stopped at the fifth sample
});

test("checkLineOfSight with terrain: the line height falls from the observer to the end, and equal height is not blocked", () => {
  const onTheLine = (lat, lon) => START.height + (END.height - START.height) * lon;   // lon is the fraction along the line here
  const exact = {elevation: onTheLine};
  assert.equal(checkLineOfSight(START, END, 20000, exact).blocked, false);
  const above = {elevation: (lat, lon) => onTheLine(lat, lon) + 0.001};
  const r = checkLineOfSight(START, END, 20000, above);
  assert.equal(r.blocked, true);
  near(r.lon, 0.1);
  const sloped = checkLineOfSight({lon: 0, lat: 0, height: 0}, {lon: 1, lat: 0, height: 1000}, 20000, {elevation: (lat, lon) => 500 * lon});
  assert.equal(sloped.blocked, false);
});

test("checkLineOfSight without terrain: never blocked within 60% of the range", () => {
  const r = checkLineOfSight({lon: 0, lat: 0, height: 100}, {lon: 0.1, lat: 0, height: 0}, 20000, null, () => 0.99);   // about 11 km of a 20 km range
  assert.deepEqual(r, {blocked: false, lon: 0.1, lat: 0, height: 0});
});

test("checkLineOfSight without terrain: beyond 60% of the range it is blocked when the draw is above 0.7, stopping 40-70% of the way out", () => {
  const far = {lon: 0.2, lat: 0, height: 0};   // about 22 km
  const draws = [0.9, 0.5];
  const blocked = checkLineOfSight({lon: 0, lat: 0, height: 100}, far, 20000, null, () => draws.shift());
  assert.equal(blocked.blocked, true);
  near(blocked.lon, 0.2 * (0.4 + 0.5 * 0.3));
  assert.equal(blocked.lat, 0);
  near(blocked.height, 100 * (1 - (0.4 + 0.5 * 0.3)));
  const clear = checkLineOfSight({lon: 0, lat: 0, height: 100}, far, 20000, null, () => 0.7);
  assert.deepEqual(clear, {blocked: false, lon: 0.2, lat: 0, height: 0});
  const edge = checkLineOfSight({lon: 0, lat: 0, height: 100}, far, 20000, null, () => 0.7000001);
  assert.equal(edge.blocked, true);
});

// --- the tool ---

function setup({globeAvailable = true, terrainAvailable = true, elevation = () => 0, coverage = (spec) => ({handle: spec.id}), random = () => 0.1} = {}) {
  const state = {calls: [], attached: [], terrainInit: [], metrics: [], removed: [], available: globeAvailable, terrain: terrainAvailable, ids: new Set()};
  const globe = {
    attach: (viewer) => { state.attached.push(viewer); state.available = Boolean(viewer); },
    available: () => state.available,
    drawObserver(spec) { state.calls.push(["observer", spec]); if (state.ids.has(spec.id)) throw new Error("duplicate id"); state.ids.add(spec.id); return {handle: spec.id}; },
    drawCoverage(spec) { state.calls.push(["coverage", spec]); return coverage(spec); },
    drawRay(spec) { state.calls.push(["ray", spec]); return {handle: spec.id}; },
    remove: (handle) => state.removed.push(handle),
  };
  const terrain = {available: () => state.terrain, initialize: (viewer) => state.terrainInit.push(viewer), elevation};
  const workspace = createWorkspace({log: quiet});
  workspace.provide("viewshedGlobe", globe);
  workspace.provide("terrainModel", terrain);
  workspace.provide("activityLog", {metric: (...a) => state.metrics.push(a)});
  const plugin = createViewshedPlugin({log: quiet, random});
  workspace.register(plugin.tool);
  return {workspace, controls: plugin.controls, state};
}
const up = async (t, viewer = {v: 1}) => { await t.workspace.activate(VIEWSHED_ID); if (viewer !== undefined) t.controls.initialize(viewer); return t; };
const of = (t, kind) => t.state.calls.filter((c) => c[0] === kind).map((c) => c[1]);

test("while inactive: no viewsheds, creating returns null, clearing does nothing, and the config can still be changed", async () => {
  const t = setup();
  assert.deepEqual(t.controls.viewsheds, {});
  assert.equal(t.controls.createTowerViewshed({position: [1, 2], name: "T"}), null);
  assert.equal(t.controls.createEntityViewshed({entity_id: "e", location: {lat: 1, lon: 2}}), null);
  assert.equal(t.controls.clearAllViewsheds(), undefined);
  t.controls.DEFAULT_CONFIG.horizontalFov = 90;
  assert.equal(t.controls.DEFAULT_CONFIG.horizontalFov, 90);
  assert.deepEqual(t.state.calls, []);
});

test("the config the page changes outlives an activation and applies to the next viewshed", async () => {
  const t = setup();
  t.controls.DEFAULT_CONFIG.horizontalFov = 120;
  t.controls.DEFAULT_CONFIG.distance = 7000;
  await up(t);
  t.controls.createEntityViewshed({entity_id: "e", location: {lat: 1, lon: 2}});
  const wedge = of(t, "coverage")[0];
  assert.equal(wedge.points.length, 54);
  const ray = of(t, "ray")[0];
  assert.equal(of(t, "ray").length, 36);
  assert.ok(ray);
  assert.equal(DEFAULT_CONFIG.horizontalFov, 60);   // the shared defaults are not touched
  await t.workspace.deactivate(VIEWSHED_ID);
  await up(t);
  assert.equal(t.controls.DEFAULT_CONFIG.horizontalFov, 120);
});

test("initialize attaches the viewer and initializes the terrain model with it, if there is one", async () => {
  const viewer = {v: "viewer"};
  const t = await up(setup(), viewer);
  assert.deepEqual(t.state.attached, [viewer]);
  assert.deepEqual(t.state.terrainInit, [viewer]);
  const none = await up(setup({terrainAvailable: false}), viewer);
  assert.deepEqual(none.state.attached, [viewer]);
  assert.deepEqual(none.state.terrainInit, []);
});

test("initialize with no viewer attaches nothing and does not initialize the terrain model; nothing can be created", async () => {
  const t = await up(setup(), null);
  assert.deepEqual(t.state.attached, [null]);
  assert.deepEqual(t.state.terrainInit, []);
  assert.equal(t.controls.createTowerViewshed({position: [1, 2]}), null);
  assert.deepEqual(t.state.calls, []);
});

test("a viewer handed over before the tool is active is used when it activates", async () => {
  const t = setup();
  const viewer = {v: 2};
  assert.equal(t.controls.initialize(viewer), t.controls);
  assert.deepEqual(t.state.attached, []);
  await t.workspace.activate(VIEWSHED_ID);
  assert.deepEqual(t.state.attached, [viewer]);
  assert.deepEqual(t.state.terrainInit, [viewer]);
  await t.workspace.deactivate(VIEWSHED_ID);
  await t.workspace.activate(VIEWSHED_ID);
  assert.deepEqual(t.state.attached, [viewer]);   // handed over once
});

test("a tower viewshed: 360 degrees, the tower's height and range, an observer, a wedge, and 36 rays", async () => {
  const t = await up(setup());
  const viewshed = t.controls.createTowerViewshed({name: "T1", position: [-86.5, 36.1], height: 80, range: 9000});
  assert.equal(viewshed.id, "tower_T1");
  assert.deepEqual(viewshed.position, {lon: -86.5, lat: 36.1, height: 80});
  assert.equal(viewshed.direction, 0);
  assert.equal(viewshed.horizontalFov, 360);
  assert.equal(viewshed.distance, 9000);
  assert.deepEqual(of(t, "observer"), [{id: "viewshed_observer_tower_T1", position: {lon: -86.5, lat: 36.1, height: 80}, label: "📡 LOS: tower_T1"}]);
  const [wedge] = of(t, "coverage");
  assert.equal(wedge.id, "viewshed_coverage_tower_T1");
  assert.equal(wedge.height, 80);
  assert.deepEqual([wedge.visibleColor, wedge.lineColor], [DEFAULT_CONFIG.visibleColor, DEFAULT_CONFIG.lineColor]);
  assert.deepEqual(wedge.points.slice(0, 2), [-86.5, 36.1]);
  const rays = of(t, "ray");
  assert.equal(rays.length, 36);
  assert.equal(rays[0].id, "viewshed_ray_tower_T1_0");
  assert.equal(rays[35].id, "viewshed_ray_tower_T1_35");
  assert.deepEqual(rays[0].from, {lon: -86.5, lat: 36.1, height: 80});
  assert.deepEqual([rays[0].visibleColor, rays[0].hiddenColor], [DEFAULT_CONFIG.visibleColor, DEFAULT_CONFIG.hiddenColor]);
  assert.equal(viewshed.handles.length, 1 + 1 + 36);
  assert.deepEqual(Object.keys(t.controls.viewsheds), ["tower_T1"]);
});

test("a tower viewshed's defaults: unknown name, 50 m, 15 km; a tower without a position is null", async () => {
  const t = await up(setup());
  const viewshed = t.controls.createTowerViewshed({position: [1, 2]});
  assert.equal(viewshed.id, "tower_unknown");
  assert.equal(viewshed.position.height, 50);
  assert.equal(viewshed.distance, 15000);
  assert.equal(t.controls.createTowerViewshed({name: "x"}), null);
  assert.equal(t.controls.createTowerViewshed(null), null);
  assert.equal(t.controls.createTowerViewshed(undefined), null);
});

test("an entity viewshed: the entity's heading and height; a radar sees 90 degrees out to 50 km, anything else 60 out to 20 km", async () => {
  const t = await up(setup());
  const plain = t.controls.createEntityViewshed({entity_id: "e1", location: {lat: 36, lon: -86, altitude_m: 300}, velocity: {heading_deg: 45}});
  assert.equal(plain.id, "entity_e1");
  assert.deepEqual(plain.position, {lon: -86, lat: 36, height: 300});
  assert.equal(plain.direction, 45);
  assert.equal(plain.horizontalFov, 60);
  assert.equal(plain.distance, 20000);
  const radar = t.controls.createEntityViewshed({entity_id: "e2", location: {lat: 36, lon: -86}, ontology: ["sensor", "radar"]});
  assert.equal(radar.horizontalFov, 90);
  assert.equal(radar.distance, 50000);
  assert.equal(radar.position.height, 100);
  assert.equal(radar.direction, 0);
  const texty = t.controls.createEntityViewshed({entity_id: "e3", location: {lat: 1, lon: 2}, ontology: "ground-radar-unit"});
  assert.equal(texty.horizontalFov, 90);   // `includes` also works on a string
  const notRadar = t.controls.createEntityViewshed({entity_id: "e4", location: {lat: 1, lon: 2}, ontology: ["sensor"]});
  assert.equal(notRadar.horizontalFov, 60);
});

test("an entity without a location is null", async () => {
  const t = await up(setup());
  assert.equal(t.controls.createEntityViewshed({entity_id: "e"}), null);
  assert.equal(t.controls.createEntityViewshed(null), null);
});

test("viewsheds are logged to the metrics tool: rays cast, blocked, clear, and the visible percentage", async () => {
  const t = await up(setup({elevation: (lat, lon) => (lon > 0 ? 1e6 : 0)}));   // anything east of the meridian is blocked
  t.controls.createTowerViewshed({name: "T", position: [0, 0], range: 10000});
  const metrics = Object.fromEntries(t.state.metrics.map(([module, name, value]) => [name, [module, value]]));
  assert.equal(metrics.rays_cast[0], "viewshed");
  assert.equal(metrics.rays_cast[1], 36);
  const blocked = metrics.rays_blocked[1], clear = metrics.rays_clear[1];
  assert.equal(blocked + clear, 36);
  assert.ok(blocked > 0 && clear > 0);
  near(metrics.visibility_percent[1], (clear / 36) * 100);
  assert.deepEqual(t.state.metrics.map((m) => m[1]), ["rays_cast", "rays_blocked", "rays_clear", "visibility_percent"]);
});

test("the metrics count blocked and clear rays the right way round: all blocked, then all clear", async () => {
  const blocked = await up(setup({elevation: () => 1e6}));
  blocked.controls.createTowerViewshed({name: "B", position: [0, 0]});
  const m = (t) => Object.fromEntries(t.state.metrics.map(([, name, value]) => [name, value]));
  assert.deepEqual(m(blocked), {rays_cast: 36, rays_blocked: 36, rays_clear: 0, visibility_percent: 0});
  const clear = await up(setup({elevation: () => -1}));
  clear.controls.createTowerViewshed({name: "C", position: [0, 0]});
  assert.deepEqual(m(clear), {rays_cast: 36, rays_blocked: 0, rays_clear: 36, visibility_percent: 100});
  const some = await up(setup({elevation: () => -1}));
  some.controls.DEFAULT_CONFIG.rayCount = 4;
  some.controls.createTowerViewshed({name: "S", position: [0, 0]});
  assert.equal(m(some).rays_cast, 4);
});

test("an entity whose velocity has no heading looks north", async () => {
  const t = await up(setup());
  assert.equal(t.controls.createEntityViewshed({entity_id: "e", location: {lat: 1, lon: 2}, velocity: {speed: 3}}).direction, 0);
  assert.equal(t.controls.createEntityViewshed({entity_id: "f", location: {lat: 1, lon: 2, altitude_m: 0}}).position.height, 100);
  assert.equal(t.controls.createEntityViewshed({entity_id: "g", location: {lat: 1, lon: 2, altitude_m: 250}}).position.height, 250);
});

test("with a terrain model each ray stops at the terrain that blocks it; without one the injected random decides", async () => {
  const blockedAll = await up(setup({elevation: () => 1e6}));
  blockedAll.controls.createTowerViewshed({name: "B", position: [10, 20], height: 100, range: 5000});
  for (const ray of of(blockedAll, "ray")) {
    assert.equal(ray.blocked, true);
    assert.equal(ray.to.height, 1e6);
    assert.equal(ray.from.height, 100);
  }
  const open = await up(setup({elevation: () => 0}));
  open.controls.createTowerViewshed({name: "O", position: [10, 20], height: 100, range: 5000});
  assert.ok(of(open, "ray").every((r) => r.blocked === false && r.to.height === 0));
  const noTerrain = await up(setup({terrainAvailable: false, random: () => 0.99}));
  noTerrain.controls.createTowerViewshed({name: "N", position: [10, 20], height: 100, range: 20000});
  assert.ok(of(noTerrain, "ray").every((r) => r.blocked === true));   // every ray runs the full 20 km, beyond 60% of the range, and the draw is 0.99
});

test("a wedge the globe cannot make is left out; the observer and rays are still kept", async () => {
  const t = await up(setup({coverage: () => null}));
  const viewshed = t.controls.createTowerViewshed({name: "T", position: [1, 2]});
  assert.equal(viewshed.handles.length, 1 + 36);
  assert.ok(viewshed.handles.every(Boolean));
});

test("PINNED: a ray count of 1 puts its one ray at NaN", async () => {
  const t = await up(setup());
  t.controls.DEFAULT_CONFIG.rayCount = 1;
  t.controls.createTowerViewshed({name: "T", position: [1, 2]});
  const [ray] = of(t, "ray");
  assert.ok(Number.isNaN(ray.to.lon));
});

test("PINNED: creating a viewshed with an id that is showing is refused by the globe, and the error reaches the caller", async () => {
  const t = await up(setup());
  t.controls.createTowerViewshed({name: "T", position: [1, 2]});
  assert.throws(() => t.controls.createTowerViewshed({name: "T", position: [1, 2]}), /duplicate id/);
  assert.equal(Object.keys(t.controls.viewsheds).length, 1);
});

test("clearing removes every handle of every viewshed (not the empty ones) and empties the list", async () => {
  const t = await up(setup({coverage: () => null}));
  const a = t.controls.createTowerViewshed({name: "A", position: [1, 2]});
  const b = t.controls.createTowerViewshed({name: "B", position: [3, 4]});
  t.controls.clearAllViewsheds();
  assert.deepEqual(t.state.removed, [...a.handles, ...b.handles]);
  assert.deepEqual(t.controls.viewsheds, {});
  t.controls.clearAllViewsheds();
  assert.equal(t.state.removed.length, a.handles.length + b.handles.length);
});

test("deactivating removes every entity of every viewshed and forgets them; a new activation starts empty", async () => {
  const t = await up(setup());
  const a = t.controls.createTowerViewshed({name: "T", position: [1, 2]});
  const b = t.controls.createTowerViewshed({name: "U", position: [3, 4]});
  assert.deepEqual(await t.workspace.deactivate(VIEWSHED_ID), []);
  assert.deepEqual(t.state.removed, [...a.handles, ...b.handles]);
  assert.deepEqual(t.controls.viewsheds, {});
  assert.equal(t.controls.createTowerViewshed({name: "U", position: [1, 2]}), null);
  await t.workspace.activate(VIEWSHED_ID);
  assert.deepEqual(t.controls.viewsheds, {});
});
