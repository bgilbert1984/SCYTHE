import assert from "node:assert/strict";
import test from "node:test";

import {collectWorkbenchEntityIds, formatWorkbenchInvestigation, formatWorkbenchStatus,
  normalizeWorkbenchSelection, workbenchEntityKind} from "./contextualWorkbench.js";

test("workbench selection retains a bounded graph revision", () => {
  assert.deepEqual(normalizeWorkbenchSelection({kind: "graph-node", entityId: "host:203.0.113.8",
    graphRevision: "graph-a", ignored: "no"}),
  {kind: "graph-node", entityId: "host:203.0.113.8", graphRevision: "graph-a"});
  assert.equal(normalizeWorkbenchSelection(null), null);
});

test("workbench extracts selectable entities from bounded MCP results", () => {
  const ids = [...collectWorkbenchEntityIds({entities: [{id: "host:1.2.3.4"}],
    nested: {node_id: "event:burst-1"}, suggestion: {id: "card1234"}, prose: "not-an-entity"})];
  assert.deepEqual(ids, ["host:1.2.3.4", "event:burst-1"]);
  assert.equal(workbenchEntityKind("event:burst-1"), "event");
  assert.equal(workbenchEntityKind("flow:a-b"), "graph-edge");
});

test("workbench output states live MCP and retained selection revision separately", () => {
  const snapshot = {status: "ok", panel: "events", selection: {entityId: "host:a", graphRevision: "graph-1"},
    records: [{tool: "get_engine_metrics", status: "ok", authority: "OBSERVATIONAL_MCP",
      result: {node_count: 4}}], proposals: [], boundary: "LIVE RESULTS; REVISION RETAINED"};
  assert.match(formatWorkbenchStatus(snapshot), /1\/1 READ TOOLS/);
  assert.match(formatWorkbenchStatus(snapshot), /REVISION graph-1/);
  assert.match(formatWorkbenchInvestigation(snapshot), /MCP TOOL \/\/ get_engine_metrics/);
  assert.match(formatWorkbenchInvestigation(snapshot), /BOUNDARY \/\/ LIVE RESULTS/);
});
