import assert from "node:assert/strict";
import test from "node:test";
import {chromium} from "playwright";

import {buildOfflineHypergraphBundle} from "./offlineHypergraphBundle.js";

test("offline bundle boots and verifies without external resources", async () => {
  const html = await buildOfflineHypergraphBundle({
    graphRevision: "graph-browser-1", snapshotAuthority: "RETAINED_IMMUTABLE_GRAPH_STATE",
    nodes: [
      {id: "host:a", kind: "network_host", evidenceClass: "OBSERVED", labels: {ip: "10.0.0.1"}},
      {id: "host:b", kind: "network_host", evidenceClass: "MEASURED", labels: {ip: "10.0.0.2"}},
    ],
    edges: [{id: "flow:1", kind: "network_flow", evidenceClass: "OBSERVED",
      nodes: ["host:a", "host:b"]}],
  });
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    const errors = [];
    const requests = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => requests.push(request.url()));
    const bundleUrl = "http://127.0.0.1/offline-hypergraph.html";
    await page.route(bundleUrl, (route) => route.fulfill({status: 200, contentType: "text/html", body: html}));
    await page.goto(bundleUrl, {waitUntil: "load"});
    await page.waitForTimeout(250);
    const state = await page.evaluate(() => ({
      verified: document.querySelector("#verify").textContent,
      canvasWidth: document.querySelector("canvas").width,
      rows: document.querySelectorAll("#rows tr").length,
    }));
    assert.deepEqual(errors, []);
    assert.match(state.verified, /VERIFIED/);
    assert.ok(state.canvasWidth > 0);
    assert.equal(state.rows, 3);
    assert.deepEqual(requests, [bundleUrl]);
  } finally {
    await browser.close();
  }
});
