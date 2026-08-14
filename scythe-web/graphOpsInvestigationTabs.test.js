import assert from "node:assert/strict";
import test from "node:test";
import {GraphOpsInvestigationTabs, investigationKey} from "./graphOpsInvestigationTabs.js";

function fixture() {
  const buttons = [];
  const document = {createElement: () => ({dataset: {}, setAttribute() {},
    addEventListener(type, fn) { this[type] = fn; }})};
  const root = {ownerDocument: document, children: buttons, hidden: true,
    replaceChildren() { buttons.length = 0; }, append(item) { buttons.push(item); }};
  return {root, buttons};
}

test("one bounded investigation tab is retained per graph entity", () => {
  const {root, buttons} = fixture(); const activated = [];
  const tabs = new GraphOpsInvestigationTabs({root, onActivate: (record) => activated.push(record.key)});
  tabs.open({kind:"graph-node", entityId:"host:1", graphRevision:"g1"});
  tabs.update("graph-node:host:1", {question:"Why?", output:"trace one"});
  tabs.open({kind:"graph-node", entityId:"host:2", graphRevision:"g2"});
  tabs.open({kind:"graph-node", entityId:"host:1", graphRevision:"g3"});
  assert.equal(tabs.records.size, 2);
  assert.equal(tabs.active().state.output, "trace one");
  assert.equal(tabs.active().selection.graphRevision, "g3");
  assert.equal(buttons.length, 2);
  assert.deepEqual(activated, ["graph-node:host:1", "graph-node:host:2", "graph-node:host:1"]);
});

test("tab count is bounded and oldest inactive investigation is evicted", () => {
  const {root} = fixture(); const tabs = new GraphOpsInvestigationTabs({root, maxTabs:2});
  for (const id of ["a","b","c"]) tabs.open({kind:"graph-node", entityId:id, graphRevision:"g"});
  assert.deepEqual([...tabs.records.keys()], ["graph-node:b", "graph-node:c"]);
  assert.equal(investigationKey({kind:"event", entityId:"e1"}), "event:e1");
});
