import { EVIDENCE_CLASSES } from "./evidenceStyles.js";

const SHA256 = /^[a-f0-9]{64}$/;

function requireObject(value, path) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${path} must be an object`);
  }
  return value;
}

function requireString(value, path) {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${path} must be a non-empty string`);
  }
  return value;
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) {
    return value;
  }
  Object.freeze(value);
  for (const child of Object.values(value)) deepFreeze(child);
  return value;
}

/**
 * Enforce the browser's critical epistemic boundary.
 *
 * This deliberately does not claim to be a complete JSON Schema
 * implementation. Dataset production must still pass the authoritative Python
 * validator. The browser repeats the safety-critical checks before display.
 */
export function validateContractBoundary(manifest) {
  requireObject(manifest, "manifest");

  if (manifest.schemaVersion !== "1.0") {
    throw new Error(`Unsupported contract version: ${String(manifest.schemaVersion)}`);
  }
  requireString(manifest.datasetId, "datasetId");
  if (!EVIDENCE_CLASSES.includes(manifest.evidenceClass)) {
    throw new Error(`Invalid evidenceClass: ${String(manifest.evidenceClass)}`);
  }
  if (manifest.visualizationIsAuthoritative !== false) {
    throw new Error("visualizationIsAuthoritative must be false");
  }

  const authority = requireObject(manifest.authority, "authority");
  requireString(authority.solverName, "authority.solverName");
  requireString(authority.solverVersion, "authority.solverVersion");
  requireString(authority.sourceRevision, "authority.sourceRevision");
  requireString(authority.runId, "authority.runId");

  const spatial = requireObject(manifest.spatialReference, "spatialReference");
  if (!["GEODETIC_GRID", "LOCAL_ENU", "LOCAL_CARTESIAN"].includes(spatial.type)) {
    throw new Error(`Unsupported spatialReference.type: ${String(spatial.type)}`);
  }
  if (spatial.type === "GEODETIC_GRID") {
    if (spatial.horizontalCrs !== "EPSG:4326") {
      throw new Error("SCYTHE-Web geodetic grids require EPSG:4326");
    }
    if (spatial.coordinateOrder !== "longitude,latitude,height") {
      throw new Error("Unexpected geodetic coordinate order");
    }
  }

  const physics = requireObject(manifest.physics, "physics");
  if (!["RF", "OPTICAL", "RF_AND_OPTICAL"].includes(physics.domain)) {
    throw new Error(`Unsupported physics.domain: ${String(physics.domain)}`);
  }

  const quantity = requireObject(manifest.quantity, "quantity");
  requireString(quantity.name, "quantity.name");
  requireString(quantity.units, "quantity.units");
  requireObject(quantity.uncertainty, "quantity.uncertainty");

  const grid = requireObject(manifest.grid, "grid");
  if (!Array.isArray(grid.dimensions) || grid.dimensions.length === 0) {
    throw new Error("grid.dimensions must be a non-empty array");
  }
  if (grid.lodPolicy?.authoritativeValuesImmutable !== true) {
    throw new Error("Authoritative values must be immutable");
  }
  requireString(grid.authoritativeAssetPath, "grid.authoritativeAssetPath");

  if (!Array.isArray(manifest.assets) || manifest.assets.length === 0) {
    throw new Error("assets must be a non-empty array");
  }
  for (const [index, asset] of manifest.assets.entries()) {
    requireString(asset.path, `assets[${index}].path`);
    if (!SHA256.test(asset.sha256)) {
      throw new Error(`assets[${index}].sha256 must be lowercase SHA-256`);
    }
  }
  const authoritative = manifest.assets.find(
    (asset) => asset.path === grid.authoritativeAssetPath &&
      asset.role === "AUTHORITATIVE_VALUES",
  );
  if (!authoritative) {
    throw new Error("authoritativeAssetPath must reference AUTHORITATIVE_VALUES");
  }

  return deepFreeze(manifest);
}

export async function loadContract(url, { fetchImpl = globalThis.fetch } = {}) {
  if (typeof fetchImpl !== "function") {
    throw new Error("A fetch implementation is required");
  }
  const response = await fetchImpl(url, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Contract fetch failed (${response.status}) for ${url}`);
  }
  return validateContractBoundary(await response.json());
}
