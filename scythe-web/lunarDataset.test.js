import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";
import {loadLunarReferenceDataset, validateLunarReferenceManifest} from "./lunarDataset.js";

const directory = new URL("../datasets/lunar-south-pole-reference-m0/", import.meta.url);

test("lunar reference manifest verifies every asset without asserting terrain", async () => {
  const manifest = JSON.parse(await readFile(new URL("manifest.json", directory), "utf8"));
  const fetchImpl = async (url) => {
    const name = new URL(url).pathname.split("/").pop();
    return new Response(await readFile(new URL(name, directory)), {status:200});
  };
  const dataset = await loadLunarReferenceDataset("https://fixture.local/lunar/manifest.json", fetchImpl);
  assert.equal(dataset.descriptor.viewer.terrainAuthority, "ABSENT_M0");
  assert.equal(dataset.descriptor.spatialReference.bodyFixedFrame, "MOON_ME_DE421");
  assert.equal(dataset.assets.size, 3);
  assert.throws(() => validateLunarReferenceManifest({...manifest,
    viewer:{...manifest.viewer,terrainAuthority:"LOLA"}}), /absent terrain authority/);
});
