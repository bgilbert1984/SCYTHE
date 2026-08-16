import assert from "node:assert/strict";
import test from "node:test";

import {graphTransportNotice} from "./graphOpsTransportStatus.js";

test("transient graph transport failures never replace an active investigation", () => {
  const completed = {key: "graph-edge:flow:1", state: {output: "OLLAMA CLOUD // COMPLETED"}};
  assert.equal(graphTransportNotice({status: "unavailable", reason: "HTTP 502"}, completed), null);
});

test("graph transport status is visible when no investigation exists", () => {
  assert.equal(graphTransportNotice({status: "unavailable", reason: "HTTP 502"}),
    "GRAPH // UNAVAILABLE\nHTTP 502");
});
