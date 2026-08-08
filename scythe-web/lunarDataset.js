const SHA256 = /^[a-f0-9]{64}$/;
const ROLES = new Set(["REFERENCE_VISUALIZATION", "ILLUSTRATIVE_BASE_TEXTURE"]);

function object(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${name} must be an object`);
  return value;
}

function exact(value, keys, name) {
  const allowed = new Set(keys); const unknown = Object.keys(value).filter((key) => !allowed.has(key));
  if (unknown.length) throw new TypeError(`${name} has unknown fields: ${unknown.join(", ")}`);
}

export function validateLunarReferenceManifest(input) {
  const value = object(input, "lunar manifest");
  exact(value, ["schemaVersion", "datasetId", "title", "description", "celestialBody", "evidenceClass",
    "spatialReference", "viewer", "assets", "authoritativeSources", "limitations", "retrievedAt"], "lunar manifest");
  if (value.schemaVersion !== "SCYTHE_LUNAR_REFERENCE_V1" || value.celestialBody !== "MOON") {
    throw new Error("manifest is not a supported lunar reference dataset");
  }
  if (value.evidenceClass !== "DERIVED_VISUALIZATION") throw new Error("M0 lunar evidence must remain derived visualization");
  const spatial = object(value.spatialReference, "spatialReference");
  exact(spatial, ["bodyFixedFrame", "longitudeConvention", "latitudeType", "referenceRadiusMeters",
    "verticalDatum", "renderRegistration"], "spatialReference");
  if (spatial.bodyFixedFrame !== "MOON_ME_DE421" || spatial.longitudeConvention !== "EAST_POSITIVE_180" ||
      spatial.latitudeType !== "PLANETOCENTRIC" || spatial.referenceRadiusMeters !== 1737400 ||
      spatial.renderRegistration !== "REFERENCE_PANEL_ONLY") throw new Error("lunar spatial reference is invalid");
  const viewer = object(value.viewer, "viewer");
  if (viewer.terrainAuthority !== "ABSENT_M0" || viewer.textureRole !== "ILLUSTRATIVE_BASE_TEXTURE") {
    throw new Error("M0 viewer must declare absent terrain authority");
  }
  if (!Array.isArray(value.assets) || value.assets.length < 1 || value.assets.length > 16) throw new Error("assets are invalid");
  for (const [index, asset] of value.assets.entries()) {
    object(asset, `assets[${index}]`);
    exact(asset, ["id", "path", "mediaType", "role", "sha256", "sourceUrl", "productPage", "credit",
      "instrument", "mission"], `assets[${index}]`);
    if (!ROLES.has(asset.role) || !SHA256.test(asset.sha256) || asset.path.includes("..") || asset.path.startsWith("/")) {
      throw new Error(`assets[${index}] violates the bounded asset contract`);
    }
  }
  if (!Array.isArray(value.authoritativeSources) || !Array.isArray(value.limitations) || !value.limitations.length) {
    throw new Error("authority sources and limitations are required");
  }
  return Object.freeze(JSON.parse(JSON.stringify(value)));
}

async function sha256(response) {
  const bytes = await response.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
}

export async function loadLunarReferenceDataset(manifestUrl, fetchImpl = globalThis.fetch) {
  const response = await fetchImpl(manifestUrl, {cache: "no-store"});
  if (!response.ok) throw new Error(`Lunar manifest HTTP ${response.status}`);
  const descriptor = validateLunarReferenceManifest(await response.json());
  const assets = new Map();
  for (const asset of descriptor.assets) {
    const url = new URL(asset.path, manifestUrl).href;
    const assetResponse = await fetchImpl(url, {cache: "no-store"});
    if (!assetResponse.ok) throw new Error(`Lunar asset HTTP ${assetResponse.status}: ${asset.id}`);
    const actual = await sha256(assetResponse);
    if (actual !== asset.sha256) throw new Error(`Lunar asset checksum mismatch: ${asset.id}`);
    assets.set(asset.id, Object.freeze({...asset, url}));
  }
  return Object.freeze({descriptor, assets});
}
