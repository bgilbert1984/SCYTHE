import { EVIDENCE_CLASSES } from "./evidenceStyles.js";

const SHA256 = /^[a-f0-9]{64}$/;
const DATASET_ID = /^[a-z0-9][a-z0-9._-]{2,127}$/;
const SAFE_PATH = /^(?!\/)(?!.*\.\.)[^\\]+$/;
const DOMAINS = ["RF", "OPTICAL", "RF_AND_OPTICAL"];
const INTERPOLATIONS = ["NONE", "NEAREST", "BILINEAR", "TRILINEAR", "MODEL_DEFINED"];
const ASSET_ROLES = [
  "AUTHORITATIVE_VALUES", "UNCERTAINTY", "MASK", "COORDINATES",
  "ANTENNA_PATTERN", "MATERIALS", "DERIVED_VISUALIZATION", "OTHER",
];

function fail(path, message) {
  throw new Error(`${path}: ${message}`);
}

function object(value, path, required, allowed = required) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail(path, "must be an object");
  }
  for (const key of required) if (!(key in value)) fail(`${path}.${key}`, "is required");
  for (const key of Object.keys(value)) {
    if (!allowed.includes(key)) fail(`${path}.${key}`, "is not allowed by Contract v1");
  }
  return value;
}

function string(value, path, { nullable = false, pattern } = {}) {
  if (nullable && value === null) return value;
  if (typeof value !== "string" || value.length === 0) fail(path, "must be a non-empty string");
  if (pattern && !pattern.test(value)) fail(path, "has an invalid format");
  return value;
}

function number(value, path, { nullable = false, min, max, exclusiveMin } = {}) {
  if (nullable && value === null) return value;
  if (!Number.isFinite(value)) fail(path, "must be a finite number");
  if (min != null && value < min) fail(path, `must be >= ${min}`);
  if (max != null && value > max) fail(path, `must be <= ${max}`);
  if (exclusiveMin != null && value <= exclusiveMin) fail(path, `must be > ${exclusiveMin}`);
  return value;
}

function integer(value, path, options) {
  number(value, path, options);
  if (!Number.isInteger(value)) fail(path, "must be an integer");
  return value;
}

function enumeration(value, allowed, path) {
  if (!allowed.includes(value)) fail(path, `must be one of ${allowed.join(", ")}`);
  return value;
}

function array(value, path, { min = 0, max = Infinity } = {}) {
  if (!Array.isArray(value) || value.length < min || value.length > max) {
    fail(path, `must be an array with ${min}-${max === Infinity ? "unbounded" : max} items`);
  }
  return value;
}

function dateTime(value, path, nullable = false) {
  if (nullable && value === null) return value;
  string(value, path);
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(value) ||
      !Number.isFinite(Date.parse(value))) fail(path, "must be an RFC 3339 date-time");
  return value;
}

function validateHashedArtifact(value, path) {
  object(value, path, ["path", "sha256"]);
  string(value.path, `${path}.path`);
  string(value.sha256, `${path}.sha256`, { pattern: SHA256 });
}

function validateAuthority(value) {
  const keys = ["solverName", "solverVersion", "modelName", "standardRevision",
    "sourceRevision", "sourceTreeSha256", "provenanceStatus", "solverLicense",
    "datasetLicense", "runId", "deterministic", "executionEnvironment", "inputHashes"];
  object(value, "authority", keys);
  for (const key of ["solverName", "solverVersion", "modelName", "sourceRevision",
    "solverLicense", "datasetLicense", "runId", "executionEnvironment"]) {
    string(value[key], `authority.${key}`);
  }
  string(value.standardRevision, "authority.standardRevision", { nullable: true });
  string(value.sourceTreeSha256, "authority.sourceTreeSha256", { nullable: true, pattern: SHA256 });
  enumeration(value.provenanceStatus, ["COMPLETE", "ARCHIVE_DIGEST_ONLY", "INCOMPLETE"],
    "authority.provenanceStatus");
  if (typeof value.deterministic !== "boolean") fail("authority.deterministic", "must be boolean");
  array(value.inputHashes, "authority.inputHashes").forEach((item, index) =>
    validateHashedArtifact(item, `authority.inputHashes[${index}]`));
}

function validateSpatial(value) {
  object(value, "spatialReference", ["type"], Object.keys(value));
  enumeration(value.type, ["GEODETIC_GRID", "LOCAL_CARTESIAN", "LOCAL_ENU"],
    "spatialReference.type");
  if (value.type === "GEODETIC_GRID") {
    const keys = ["type", "horizontalCrs", "verticalDatum", "coordinateOrder", "heightUnits",
      "ecefCompatible", "boundsDegrees", "crossesAntimeridian"];
    object(value, "spatialReference", keys);
    if (value.horizontalCrs !== "EPSG:4326") fail("spatialReference.horizontalCrs", "must be EPSG:4326");
    enumeration(value.verticalDatum, ["WGS84_ELLIPSOID", "EGM96", "EGM2008", "LOCAL", "NONE"],
      "spatialReference.verticalDatum");
    if (value.coordinateOrder !== "longitude,latitude,height") fail("spatialReference.coordinateOrder", "is invalid");
    if (value.heightUnits !== "m") fail("spatialReference.heightUnits", "must be m");
    if (typeof value.ecefCompatible !== "boolean" || typeof value.crossesAntimeridian !== "boolean") {
      fail("spatialReference", "ECEF and antimeridian flags must be boolean");
    }
    const bounds = array(value.boundsDegrees, "spatialReference.boundsDegrees", { min: 4, max: 4 });
    bounds.forEach((item, index) => number(item, `spatialReference.boundsDegrees[${index}]`, {
      min: index % 2 ? -90 : -180, max: index % 2 ? 90 : 180,
    }));
    return;
  }
  const keys = ["type", "axes", "distanceUnits", "metersPerSolverUnit",
    "originDescription", "geodeticRegistration"];
  object(value, "spatialReference", keys);
  string(value.axes, "spatialReference.axes");
  string(value.originDescription, "spatialReference.originDescription");
  if (value.type === "LOCAL_CARTESIAN") {
    enumeration(value.distanceUnits, ["m", "solver_length_unit"], "spatialReference.distanceUnits");
    number(value.metersPerSolverUnit, "spatialReference.metersPerSolverUnit",
      { nullable: true, exclusiveMin: 0 });
    if (value.geodeticRegistration !== null) fail("spatialReference.geodeticRegistration", "must be null");
    return;
  }
  if (value.axes !== "x-east,y-up,z-north" || value.distanceUnits !== "m" ||
      value.metersPerSolverUnit !== null) fail("spatialReference", "has invalid LOCAL_ENU axes or units");
  const registrationKeys = ["longitudeDegrees", "latitudeDegrees", "heightMeters", "verticalDatum"];
  object(value.geodeticRegistration, "spatialReference.geodeticRegistration", registrationKeys);
  number(value.geodeticRegistration.longitudeDegrees,
    "spatialReference.geodeticRegistration.longitudeDegrees", { min: -180, max: 180 });
  number(value.geodeticRegistration.latitudeDegrees,
    "spatialReference.geodeticRegistration.latitudeDegrees", { min: -90, max: 90 });
  number(value.geodeticRegistration.heightMeters,
    "spatialReference.geodeticRegistration.heightMeters");
  enumeration(value.geodeticRegistration.verticalDatum,
    ["WGS84_ELLIPSOID", "EGM96", "EGM2008", "LOCAL"],
    "spatialReference.geodeticRegistration.verticalDatum");
}

function validatePhysics(value) {
  object(value, "physics", ["domain", "rf", "optical"]);
  enumeration(value.domain, DOMAINS, "physics.domain");
  if (value.domain !== "OPTICAL") {
    const keys = ["frequencyHz", "bandwidthHz", "polarization", "transmitterHeightMeters",
      "receiverHeightMeters", "antennaPatternAssetPath", "atmosphericModel", "earthSpaceModel"];
    object(value.rf, "physics.rf", keys);
    number(value.rf.frequencyHz, "physics.rf.frequencyHz", { exclusiveMin: 0 });
    number(value.rf.bandwidthHz, "physics.rf.bandwidthHz", { min: 0 });
    string(value.rf.polarization, "physics.rf.polarization");
    for (const key of ["transmitterHeightMeters", "receiverHeightMeters"])
      number(value.rf[key], `physics.rf.${key}`, { nullable: true });
    for (const key of ["antennaPatternAssetPath", "atmosphericModel", "earthSpaceModel"])
      string(value.rf[key], `physics.rf.${key}`, { nullable: true });
  } else if (value.rf !== null) fail("physics.rf", "must be null for OPTICAL");
  if (value.domain !== "RF") {
    const keys = ["wavelengthNanometers", "frequencySolverUnits", "polarizationRepresentation",
      "materialModel", "boundaryConditions"];
    object(value.optical, "physics.optical", keys);
    number(value.optical.wavelengthNanometers, "physics.optical.wavelengthNanometers",
      { nullable: true, exclusiveMin: 0 });
    number(value.optical.frequencySolverUnits, "physics.optical.frequencySolverUnits",
      { nullable: true, exclusiveMin: 0 });
    enumeration(value.optical.polarizationRepresentation,
      ["STOKES_IQUV", "JONES_EXEY_COMPLEX", "FIELD_COMPONENTS", "NONE"],
      "physics.optical.polarizationRepresentation");
    string(value.optical.materialModel, "physics.optical.materialModel");
    string(value.optical.boundaryConditions, "physics.optical.boundaryConditions");
  } else if (value.optical !== null) fail("physics.optical", "must be null for RF");
}

function validateTemporal(value) {
  const keys = ["generatedUtc", "validFromUtc", "validToUtc", "statisticalTimePercentage", "timeSemantics"];
  object(value, "temporal", keys);
  dateTime(value.generatedUtc, "temporal.generatedUtc");
  dateTime(value.validFromUtc, "temporal.validFromUtc", true);
  dateTime(value.validToUtc, "temporal.validToUtc", true);
  number(value.statisticalTimePercentage, "temporal.statisticalTimePercentage",
    { nullable: true, exclusiveMin: 0, max: 100 });
  enumeration(value.timeSemantics, ["STATIC", "INSTANTANEOUS", "INTERVAL",
    "STATISTICAL_PERCENTAGE", "TIME_SERIES"], "temporal.timeSemantics");
}

function validateQuantity(value) {
  const keys = ["name", "definition", "units", "valueSemantics", "complexRepresentation", "uncertainty"];
  object(value, "quantity", keys);
  string(value.name, "quantity.name");
  string(value.definition, "quantity.definition");
  string(value.units, "quantity.units");
  enumeration(value.valueSemantics, ["INSTANTANEOUS", "MEAN", "MEDIAN", "PERCENTILE",
    "COMPLEX_AMPLITUDE", "PHASE", "INTENSITY", "POWER_DENSITY", "FIELD_STRENGTH",
    "PATH_LOSS", "ATTENUATION"], "quantity.valueSemantics");
  enumeration(value.complexRepresentation,
    ["NONE", "REAL_IMAGINARY", "MAGNITUDE_PHASE"], "quantity.complexRepresentation");
  object(value.uncertainty, "quantity.uncertainty", ["kind", "description", "assetPath"]);
  enumeration(value.uncertainty.kind, ["NUMERICAL_CONVERGENCE", "STATISTICAL",
    "MEASUREMENT", "NOT_QUANTIFIED", "NONE"], "quantity.uncertainty.kind");
  string(value.uncertainty.description, "quantity.uncertainty.description");
  string(value.uncertainty.assetPath, "quantity.uncertainty.assetPath", { nullable: true });
}

function validateGrid(value) {
  const keys = ["representation", "dimensions", "resolution", "noData", "interpolation",
    "authoritativeAssetPath", "lodPolicy"];
  object(value, "grid", keys);
  enumeration(value.representation, ["HDF5", "NETCDF", "GEOTIFF", "ZARR", "3D_TILES", "CUSTOM_BINARY"],
    "grid.representation");
  array(value.dimensions, "grid.dimensions", { min: 1, max: 4 }).forEach((item, index) =>
    integer(item, `grid.dimensions[${index}]`, { min: 1 }));
  array(value.resolution, "grid.resolution", { min: 1, max: 4 }).forEach((item, index) =>
    number(item, `grid.resolution[${index}]`, { exclusiveMin: 0 }));
  object(value.noData, "grid.noData", ["policy", "value"]);
  enumeration(value.noData.policy, ["NONE", "SENTINEL", "NAN", "MASK_ASSET"], "grid.noData.policy");
  if (!(value.noData.value === null || typeof value.noData.value === "string" ||
      Number.isFinite(value.noData.value))) fail("grid.noData.value", "has an invalid type");
  enumeration(value.interpolation, INTERPOLATIONS, "grid.interpolation");
  string(value.authoritativeAssetPath, "grid.authoritativeAssetPath");
  const lodKeys = ["authoritativeValuesImmutable", "derivedTilesAllowed", "aggregationMethod", "description"];
  object(value.lodPolicy, "grid.lodPolicy", lodKeys);
  if (value.lodPolicy.authoritativeValuesImmutable !== true) fail("grid.lodPolicy.authoritativeValuesImmutable", "must be true");
  if (typeof value.lodPolicy.derivedTilesAllowed !== "boolean") fail("grid.lodPolicy.derivedTilesAllowed", "must be boolean");
  string(value.lodPolicy.aggregationMethod, "grid.lodPolicy.aggregationMethod", { nullable: true });
  string(value.lodPolicy.description, "grid.lodPolicy.description");
}

function validateAssets(value) {
  array(value, "assets", { min: 1 }).forEach((asset, index) => {
    const path = `assets[${index}]`;
    object(asset, path, ["path", "role", "mediaType", "sha256", "sizeBytes"]);
    string(asset.path, `${path}.path`, { pattern: SAFE_PATH });
    enumeration(asset.role, ASSET_ROLES, `${path}.role`);
    string(asset.mediaType, `${path}.mediaType`);
    string(asset.sha256, `${path}.sha256`, { pattern: SHA256 });
    integer(asset.sizeBytes, `${path}.sizeBytes`, { min: 0 });
  });
}

function validateLineage(value) {
  object(value, "lineage", ["parentDatasetIds", "transformations"]);
  array(value.parentDatasetIds, "lineage.parentDatasetIds").forEach((item, index) =>
    string(item, `lineage.parentDatasetIds[${index}]`));
  array(value.transformations, "lineage.transformations").forEach((item, index) => {
    const path = `lineage.transformations[${index}]`;
    object(item, path, ["name", "version", "parameters", "inputSha256", "outputSha256"]);
    string(item.name, `${path}.name`); string(item.version, `${path}.version`);
    object(item.parameters, `${path}.parameters`, [], Object.keys(item.parameters ?? {}));
    string(item.inputSha256, `${path}.inputSha256`, { pattern: SHA256 });
    string(item.outputSha256, `${path}.outputSha256`, { pattern: SHA256 });
  });
}

function validateCrossReferences(manifest) {
  const paths = new Set(manifest.assets.map((asset) => asset.path));
  const authoritative = manifest.assets.find((asset) =>
    asset.path === manifest.grid.authoritativeAssetPath && asset.role === "AUTHORITATIVE_VALUES");
  if (!authoritative) fail("grid.authoritativeAssetPath", "must reference AUTHORITATIVE_VALUES");
  const uncertaintyPath = manifest.quantity.uncertainty.assetPath;
  if (uncertaintyPath !== null && !paths.has(uncertaintyPath)) {
    fail("quantity.uncertainty.assetPath", "must reference a declared asset");
  }
  const spatial = manifest.spatialReference;
  if (spatial.type === "LOCAL_CARTESIAN") {
    if (spatial.distanceUnits === "m" && ![null, 1].includes(spatial.metersPerSolverUnit)) {
      fail("spatialReference.metersPerSolverUnit", "meter coordinates require unit scale");
    }
    if (spatial.distanceUnits === "solver_length_unit" && spatial.metersPerSolverUnit === null &&
        !["SYNTHETIC", "ILLUSTRATIVE"].includes(manifest.evidenceClass)) {
      fail("spatialReference.metersPerSolverUnit", "physical normalized coordinates require a scale");
    }
  }
  if (["OPTICAL", "RF_AND_OPTICAL"].includes(manifest.physics.domain) &&
      manifest.physics.optical.wavelengthNanometers === null &&
      !["SYNTHETIC", "ILLUSTRATIVE"].includes(manifest.evidenceClass)) {
    fail("physics.optical.wavelengthNanometers", "physical optical output requires a wavelength");
  }
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.freeze(value);
  for (const child of Object.values(value)) deepFreeze(child);
  return value;
}

export function normalizeDatasetDescriptor(manifest) {
  const copy = JSON.parse(JSON.stringify(manifest));
  return deepFreeze({
    ...copy,
    descriptorType: "SCYTHE_DATASET_DESCRIPTOR_V1",
    solver: {
      name: copy.authority.solverName,
      version: copy.authority.solverVersion,
      model: copy.authority.modelName,
      standardRevision: copy.authority.standardRevision,
      deterministic: copy.authority.deterministic,
      runId: copy.authority.runId,
    },
    coordinateFrame: {
      type: copy.spatialReference.type,
      horizontalCrs: copy.spatialReference.horizontalCrs ?? null,
      verticalDatum: copy.spatialReference.verticalDatum ??
        copy.spatialReference.geodeticRegistration?.verticalDatum ?? null,
      ecefCompatible: copy.spatialReference.ecefCompatible ?? false,
    },
    quantityDescriptor: {
      name: copy.quantity.name,
      units: copy.quantity.units,
      valueSemantics: copy.quantity.valueSemantics,
      complexRepresentation: copy.quantity.complexRepresentation,
      uncertainty: copy.quantity.uncertainty,
    },
    epistemics: {
      evidenceClass: copy.evidenceClass,
      provenanceStatus: copy.authority.provenanceStatus,
      visualizationIsAuthoritative: false,
    },
    samplingPolicy: {
      interpolation: copy.grid.interpolation,
      noData: copy.grid.noData,
      immutableAuthority: true,
    },
    integrity: {
      authoritativeAssetPath: copy.grid.authoritativeAssetPath,
      assets: copy.assets.map(({ path, role, sha256, sizeBytes }) =>
        ({ path, role, sha256, sizeBytes })),
      lineage: copy.lineage,
    },
  });
}

/** Validate all JSON Schema v1 fields plus Python validator cross-references. */
export function validateContractBoundary(manifest) {
  const rootKeys = ["schemaVersion", "datasetId", "title", "description", "evidenceClass",
    "authority", "spatialReference", "temporal", "physics", "quantity", "grid", "assets",
    "lineage", "visualizationIsAuthoritative"];
  if (manifest?.descriptorType === "SCYTHE_DATASET_DESCRIPTOR_V1") {
    manifest = Object.fromEntries(rootKeys.map((key) => [key, manifest[key]]));
  }
  object(manifest, "manifest", rootKeys);
  if (manifest.schemaVersion !== "1.0") fail("schemaVersion", "must be 1.0");
  string(manifest.datasetId, "datasetId", { pattern: DATASET_ID });
  string(manifest.title, "title"); string(manifest.description, "description");
  enumeration(manifest.evidenceClass, EVIDENCE_CLASSES, "evidenceClass");
  if (manifest.visualizationIsAuthoritative !== false) {
    fail("visualizationIsAuthoritative", "must be false");
  }
  validateAuthority(manifest.authority);
  validateSpatial(manifest.spatialReference);
  validateTemporal(manifest.temporal);
  validatePhysics(manifest.physics);
  validateQuantity(manifest.quantity);
  validateGrid(manifest.grid);
  validateAssets(manifest.assets);
  validateLineage(manifest.lineage);
  validateCrossReferences(manifest);
  return normalizeDatasetDescriptor(manifest);
}

export async function loadContract(url, { fetchImpl = globalThis.fetch } = {}) {
  if (typeof fetchImpl !== "function") throw new Error("A fetch implementation is required");
  const response = await fetchImpl(url, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Contract fetch failed (${response.status}) for ${url}`);
  return validateContractBoundary(await response.json());
}
