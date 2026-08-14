import assert from "node:assert/strict";
import test from "node:test";

import {summarizeInfrastructureCluster} from "./infrastructureGlobeLayer.js";

test("InfraFlow screen clusters count unique hosts and preserve inferred-location boundary", () => {
  const entities = [
    {properties: {domainId: "asn:8075", organization: "Microsoft Corporation",
      hostIdsJson: JSON.stringify(["host:20.1.1.1", "host:20.1.1.2"])}},
    {properties: {domainId: "asn:15169", organization: "Google LLC",
      hostIdsJson: JSON.stringify(["host:20.1.1.2", "host:142.250.1.1"])}},
  ];
  const summary = summarizeInfrastructureCluster(entities);
  assert.equal(summary.hostCount, 3); assert.equal(summary.domainCount, 2); assert.equal(summary.markerCount, 3);
  assert.match(summary.text, /3 HOSTS \/\/ 2 NETWORK DOMAINS/);
  assert.match(summary.text, /host:142\.250\.1\.1/);
  assert.match(summary.text, /ASN OWNERSHIP AND GEOIP LOCATION REMAIN INFERRED/);
});
