import test from "node:test";
import assert from "node:assert/strict";
import {createReconStore, adoptReconStore} from "./reconStore.js";

test("upsert files an entity under its id and returns it, and a second one with that id replaces it", () => {
  const store = createReconStore();
  const first = {entity_id: "a", name: "one"};
  assert.equal(store.upsert(first), first);
  assert.equal(store.entities.a, first);
  const second = {entity_id: "a", name: "two"};
  store.upsert(second);
  assert.deepEqual(Object.keys(store.entities), ["a"]);
  assert.equal(store.entities.a, second);
});

test("clear empties the map in place: the same object, so anything holding it sees the change", () => {
  const map = {a: {entity_id: "a"}, b: {entity_id: "b"}};
  const store = createReconStore(map);
  assert.equal(store.entities, map);
  store.clear();
  assert.equal(store.entities, map);
  assert.deepEqual(Object.keys(map), []);
  store.upsert({entity_id: "c"});
  assert.deepEqual(Object.keys(map), ["c"]);
});

test("a store given no map makes its own, and two stores do not share one", () => {
  const one = createReconStore(), two = createReconStore();
  one.upsert({entity_id: "a"});
  assert.deepEqual(Object.keys(two.entities), []);
});

test("adopting makes the page object's map the store's, keeping what was already in it", () => {
  const earlier = {entity_id: "early"};
  const win = {AutoReconVisualization: {entities: {early: earlier}, keep: 1}};
  const held = win.AutoReconVisualization.entities;
  const store = adoptReconStore(win);
  assert.equal(store.entities, held);
  assert.equal(win.AutoReconVisualization.entities, held);
  assert.equal(win.AutoReconVisualization.keep, 1);
  store.upsert({entity_id: "late"});
  assert.deepEqual(Object.keys(win.AutoReconVisualization.entities), ["early", "late"]);
});

test("adopting a page object with no map gives it the store's", () => {
  const win = {AutoReconVisualization: {}};
  const store = adoptReconStore(win);
  assert.equal(win.AutoReconVisualization.entities, store.entities);
});

test("adopting with no page object makes an empty store and does not create the object", () => {
  const win = {};
  const store = adoptReconStore(win);
  assert.deepEqual(Object.keys(store.entities), []);
  assert.equal("AutoReconVisualization" in win, false);
});

test("upsert can file an entity under another id, as the sync code does with the id its event carries", () => {
  const store = createReconStore();
  const entity = {entity_id: "from-data"};
  store.upsert(entity, "from-event");
  assert.deepEqual(Object.keys(store.entities), ["from-event"]);
  assert.equal(store.entities["from-event"], entity);
});

test("remove drops one entity and leaves the others, and removing one that is not held does nothing", () => {
  const store = createReconStore({a: {entity_id: "a"}, b: {entity_id: "b"}});
  store.remove("a");
  assert.deepEqual(Object.keys(store.entities), ["b"]);
  store.remove("nonesuch");
  assert.deepEqual(Object.keys(store.entities), ["b"]);
});
