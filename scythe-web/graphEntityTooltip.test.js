import assert from "node:assert/strict";
import test from "node:test";

import { graphEntityPromptContext, graphEntityTooltip } from "./graphEntityTooltip.js";

test("public host tooltip separates observation from local enrichment claims", () => {
  const value = graphEntityTooltip({
    id: "host:8.8.8.8", kind: "network_host", evidenceClass: "OBSERVED", observedAt: 1_700_000_000,
    labels: {ip: "8.8.8.8", flowRole: "destination"},
    enrichment: {ip: "8.8.8.8", scope: "PUBLIC",
      network: {asn: 15169, organization: "Google LLC", prefix: "8.8.8.0/24"},
      geo: {city: "Mountain View", region: "California", countryCode: "US",
        latitude: 37.4, longitude: -122.1, uncertaintyRadiusKm: 25}},
  });
  assert.match(value, /AS15169 · Google LLC/);
  assert.match(value, /Mountain View, California, US/);
  assert.match(value, /±25 km/);
  assert.match(value, /IP PRESENCE \/\/ OBSERVED/);
  assert.match(value, /PLACE ESTIMATE \/\/ INFERRED · GEOIP/);
  assert.match(value, /TOPOLOGY IS NOT GEOLOCATION/);
});

test("private host tooltip refuses public place claims", () => {
  const value = graphEntityTooltip({id: "host:192.168.1.4", kind: "network_host",
    evidenceClass: "OBSERVED", enrichment: {ip: "192.168.1.4", scope: "PRIVATE"}});
  assert.match(value, /PRIVATE HOST/);
  assert.match(value, /PLACE \/\/ NOT APPLICABLE TO LOCAL SCOPE/);
  assert.doesNotMatch(value, /PLACE ESTIMATE/);
});

test("non-host graph entities retain a compact fallback", () => {
  assert.equal(graphEntityTooltip({id: "edge:a", kind: "network_flow", evidenceClass: "OBSERVED"}),
    "network_flow\nedge:a\nOBSERVED");
});

test("host tooltip labels measured ping liveness independently of GeoIP", () => {
  const value = graphEntityTooltip({id: "host:8.8.8.8", kind: "network_host",
    evidenceClass: "OBSERVED", liveness: {state: "active", rttMs: 11, tool: "windows-ping-via-wsl"}});
  assert.match(value, /LIVENESS \/\/ ACTIVE · ICMP MEASURED/);
  assert.match(value, /windows-ping-via-wsl · 11 ms/);
});

test("prompt context carries tooltip claims without promoting display enrichment", () => {
  const value = graphEntityPromptContext({id: "host:8.8.8.8", kind: "network_host",
    evidenceClass: "OBSERVED", enrichment: {ip: "8.8.8.8", scope: "PUBLIC",
      network: {asn: 15169, organization: "Google LLC"}}});
  assert.match(value, /SELECTED GRAPH ENTITY \/\/ TOOLTIP CONTEXT/);
  assert.match(value, /AS15169 · Google LLC/);
  assert.match(value, /CONTEXT AUTHORITY \/\/ MIXED/);
  assert.match(value, /SERVER-RESOLVED GRAPH EVIDENCE GOVERNS ANSWERS/);
});

test("multicast tooltip describes a group and suppresses unicast liveness", () => {
  const value = graphEntityTooltip({id:"host:ff02::1:3",kind:"network_multicast_group",
    evidenceClass:"OBSERVED",labels:{ip:"ff02::1:3"},enrichment:{scope:"MULTICAST"}});
  assert.match(value,/MULTICAST GROUP/);
  assert.match(value,/LIVENESS \/\/ NOT APPLICABLE/);
  assert.doesNotMatch(value,/NOT YET MEASURED/);
});
