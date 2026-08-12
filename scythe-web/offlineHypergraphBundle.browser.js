import assert from "node:assert/strict";
import test from "node:test";
import {chromium} from "playwright";

import {buildOfflineHypergraphBundle} from "./offlineHypergraphBundle.js";

test("offline bundle boots and verifies without external resources", async () => {
  const html = await buildOfflineHypergraphBundle({
    graphRevision: "graph-browser-1", snapshotAuthority: "RETAINED_IMMUTABLE_GRAPH_STATE",
    nodes: [
      {id: "host:a", kind: "network_host", evidenceClass: "OBSERVED", labels: {ip: "8.8.8.8"},
        enrichment: {geo: {latitude: 37.386, longitude: -122.084, city: "Mountain View",
          country: "United States", uncertaintyRadiusKm: 500}}},
      {id: "host:b", kind: "network_host", evidenceClass: "MEASURED", labels: {ip: "1.1.1.1"},
        enrichment: {geo: {latitude: -33.87, longitude: 151.21, city: "Sydney",
          country: "Australia", uncertaintyRadiusKm: 250}}},
      {id: "host:c", kind: "network_host", evidenceClass: "OBSERVED", labels: {ip: "10.0.0.3"}},
      {id: "host:d", kind: "network_host", evidenceClass: "OBSERVED", labels: {ip: "10.0.0.4"}},
    ],
    edges: [
      {id: "flow:1", kind: "network_flow", evidenceClass: "OBSERVED", nodes: ["host:a", "host:b"]},
      {id: "flow:2", kind: "network_flow", evidenceClass: "OBSERVED", nodes: ["host:b", "host:c"]},
      {id: "flow:3", kind: "network_flow", evidenceClass: "OBSERVED", nodes: ["host:c", "host:d"]},
    ],
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
      hops: document.querySelector("#cfg-hop").value,
      maxLabels: document.querySelector("#cfg-max").value,
      locationMode: Boolean(document.querySelector("#mode-location")),
    }));
    assert.deepEqual(errors, []);
    assert.match(state.verified, /VERIFIED/);
    assert.ok(state.canvasWidth > 0);
    assert.equal(state.rows, 7);
    assert.equal(state.hops, "1");
    assert.equal(state.maxLabels, "24");
    assert.equal(state.locationMode, true);
    await page.locator("#mode-location").click();
    assert.match(await page.locator("#location-status").textContent(), /2 GEOIP-PLOTTED \/\/ 2 UNLOCATED/);
    assert.ok(await page.locator("#location-canvas").evaluate((canvas) => canvas.width > 0));
    await page.locator("#mode-table").click();
    await page.locator('tr[data-entity-id="host:a"]').click();
    assert.equal(await page.locator(".neighbor-label").count(), 1);
    assert.match(await page.locator("#label-status").textContent(), /WITHIN 1 HOP/);
    await page.locator("#cfg-hop").fill("3");
    await page.locator("#cfg-max").fill("2");
    assert.equal(await page.locator(".neighbor-label").count(), 2);
    assert.match(await page.locator("#label-status").textContent(), /2 SHOWN \/\/ WITHIN 3 HOPS \/\/ CAP 2/);
    assert.deepEqual(await page.locator(".neighbor-label").evaluateAll((items) =>
      items.map((item) => [item.dataset.entityId, item.dataset.hop])),
    [["host:b", "1"], ["host:c", "2"]]);
    await page.locator("#mode-table").click();
    await page.locator('tr[data-entity-id="host:a"]').click();
    assert.equal(await page.locator(".neighbor-label").count(), 0);
    assert.match(await page.locator("#label-status").textContent(), /SELECT A NODE/);
    assert.deepEqual(requests, [bundleUrl]);
  } finally {
    await browser.close();
  }
});
