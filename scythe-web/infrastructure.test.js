import assert from "node:assert/strict";
import test from "node:test";
import { webcrypto } from "node:crypto";

import { GeodeticTileIndex } from "./tileIndex.js";
import {
  VerifiedTileLoader,
  decodeFloat32Grid,
  decodeScaledUint16Grid,
  sha256Hex,
} from "./tileLoader.js";
import { ScenarioManifestWeb } from "./scenarioManifestWeb.js";

test("tile index deterministically selects highest LOD and normalized coordinates", () => {
  const index = new GeodeticTileIndex([
    { id: "coarse", westDegrees: 0, southDegrees: 0, eastDegrees: 2, northDegrees: 2 },
    { id: "fine", lod: 2, westDegrees: 0, southDegrees: 0, eastDegrees: 1, northDegrees: 1 },
  ]);
  assert.deepEqual(index.locate({ longitudeDegrees: 0.5, latitudeDegrees: 0.5 }), {
    tileId: "fine", u: 0.5, v: 0.5, lod: 2,
  });
  assert.equal(index.locate({ longitudeDegrees: 5, latitudeDegrees: 5 }), null);
});

test("verified loader rejects corruption and decodes valid Float32 tiles", async () => {
  const values = new Float32Array([1, 2, 3, 4]);
  const bytes = values.buffer.slice(0);
  const digest = await sha256Hex(bytes, webcrypto);
  const response = {
    ok: true,
    status: 200,
    arrayBuffer: async () => bytes.slice(0),
  };
  const loader = new VerifiedTileLoader({
    tiles: [{ id: "a", url: "/a.bin", sha256: digest, sizeBytes: 16, shape: [2, 2] }],
    decode: decodeFloat32Grid,
    fetchImpl: async () => response,
    cryptoImpl: webcrypto,
  });
  assert.deepEqual([...((await loader.getTilePayload("a")).values)], [1, 2, 3, 4]);

  const corrupt = new VerifiedTileLoader({
    tiles: [{ id: "a", url: "/a.bin", sha256: "0".repeat(64) }],
    decode: decodeFloat32Grid,
    fetchImpl: async () => response,
    cryptoImpl: webcrypto,
  });
  await assert.rejects(corrupt.getTilePayload("a"), /SHA-256 mismatch/);
});

test("scenario manifest rejects an unbound active transmitter", () => {
  assert.throws(() => new ScenarioManifestWeb({
    datasets: [{ id: "rf-v1", kind: "RF", contractUrl: "/rf-v1/manifest.json" }],
    activeTransmitterId: "missing",
    transmitters: [],
  }), /does not reference/);
});

test("Uint16 decoding requires and applies explicit physical scaling", () => {
  const bytes = new Uint8Array([0, 0, 10, 0, 255, 255, 20, 0]).buffer;
  const tile = {
    id: "compact",
    shape: [2, 2],
    encoding: {
      scalarType: "UINT16",
      byteOrder: "LITTLE_ENDIAN",
      scale: 0.5,
      offset: -10,
      noDataRaw: 65535,
    },
  };
  const payload = decodeScaledUint16Grid(bytes, tile);
  assert.deepEqual([...payload.values].slice(0, 2), [-10, -5]);
  assert.deepEqual([...payload.validMask], [1, 1, 0, 1]);
  assert.equal(Number.isNaN(payload.values[2]), true);
  assert.throws(() => decodeScaledUint16Grid(bytes, {
    ...tile, encoding: { scalarType: "UINT16", byteOrder: "LITTLE_ENDIAN" },
  }), /scale and offset/);
});
