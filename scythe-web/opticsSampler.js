import { validateContractBoundary } from "./contractLoader.js";

const METHODS = new Set(["NONE", "NEAREST", "BILINEAR"]);
const EPSILON = 1e-9;

function coordinate(location, width, height) {
  if (Number.isFinite(location.x) && Number.isFinite(location.y)) {
    return { x: location.x, y: location.y };
  }
  if (Number.isFinite(location.u) && Number.isFinite(location.v)) {
    return { x: location.u * (width - 1), y: location.v * (height - 1) };
  }
  throw new Error("Tile location must provide x/y or u/v");
}

function read(payload, index, noData) {
  const value = payload.values[index];
  if (payload.validMask?.[index] === 0) return null;
  if (noData.policy === "NAN" && Number.isNaN(value)) return null;
  if (noData.policy === "SENTINEL" && value === noData.value) return null;
  return value;
}

function interpolate(payload, method, x, y, width, height, noData) {
  if (method === "NONE") {
    if (Math.abs(x - Math.round(x)) > EPSILON ||
        Math.abs(y - Math.round(y)) > EPSILON) return undefined;
    return read(payload, Math.round(y) * width + Math.round(x), noData);
  }
  if (method === "NEAREST") {
    const xi = Math.max(0, Math.min(width - 1, Math.round(x)));
    const yi = Math.max(0, Math.min(height - 1, Math.round(y)));
    return read(payload, yi * width + xi, noData);
  }
  const x0 = Math.max(0, Math.min(width - 1, Math.floor(x)));
  const y0 = Math.max(0, Math.min(height - 1, Math.floor(y)));
  const x1 = Math.min(width - 1, x0 + 1);
  const y1 = Math.min(height - 1, y0 + 1);
  const tx = x - x0;
  const ty = y - y0;
  const values = [
    read(payload, y0 * width + x0, noData),
    read(payload, y0 * width + x1, noData),
    read(payload, y1 * width + x0, noData),
    read(payload, y1 * width + x1, noData),
  ];
  if (values.some((value) => value == null)) return null;
  return (values[0] * (1 - tx) + values[1] * tx) * (1 - ty) +
    (values[2] * (1 - tx) + values[3] * tx) * ty;
}

function unavailable(status, descriptor, query, reason) {
  return Object.freeze({
    status,
    available: false,
    reason,
    datasetId: descriptor.datasetId,
    evidenceClass: descriptor.evidenceClass,
    visualizationIsAuthoritative: false,
    query,
  });
}

/**
 * Samples a single contract-declared optical quantity. Phase, intensity, and
 * depth planes remain separate datasets unless an explicit adapter exposes a
 * selected plane through tileIndex.locate().
 */
export class ScytheOpticsSampler {
  constructor({ descriptor, tileIndex, tileLoader }) {
    this.descriptor = validateContractBoundary(descriptor);
    if (!["OPTICAL", "RF_AND_OPTICAL"].includes(this.descriptor.physics.domain) ||
        !this.descriptor.physics.optical) {
      throw new Error("ScytheOpticsSampler requires an optical dataset");
    }
    if (!METHODS.has(this.descriptor.grid.interpolation)) {
      throw new Error(`Unsupported optical interpolation: ${this.descriptor.grid.interpolation}`);
    }
    if (typeof tileIndex?.locate !== "function" ||
        typeof tileLoader?.getTilePayload !== "function") {
      throw new TypeError("Optical tile index and loader are required");
    }
    this.tileIndex = tileIndex;
    this.tileLoader = tileLoader;
  }

  async sample(input) {
    const wavelengthNanometers = Number(input?.wavelengthNanometers);
    for (const [name, value] of Object.entries({
      longitudeDegrees: input?.longitudeDegrees,
      latitudeDegrees: input?.latitudeDegrees,
      heightMeters: input?.heightMeters ?? 0,
      wavelengthNanometers,
    })) {
      if (!Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
    }
    const declared = this.descriptor.physics.optical.wavelengthNanometers;
    const query = Object.freeze({
      longitudeDegrees: input.longitudeDegrees,
      latitudeDegrees: input.latitudeDegrees,
      heightMeters: input.heightMeters ?? 0,
      wavelengthNanometers,
      depthPlaneIndex: input.depthPlaneIndex ?? null,
    });
    if (declared != null && wavelengthNanometers !== declared) {
      return unavailable("OUTSIDE_WAVELENGTH", this.descriptor, query,
        "Wavelength does not match the solver dataset");
    }
    const location = await this.tileIndex.locate(query);
    if (!location) {
      return unavailable("OUTSIDE_DATASET", this.descriptor, query,
        "Position outside indexed optical tiles");
    }
    const payload = await this.tileLoader.getTilePayload(location.tileId);
    const [width, height] = payload.shape ?? [];
    if (!Number.isInteger(width) || !Number.isInteger(height) ||
        !ArrayBuffer.isView(payload.values) || payload.values.length < width * height) {
      throw new Error("Optical tile payload does not match shape");
    }
    const { x, y } = coordinate(location, width, height);
    if (x < 0 || x > width - 1 || y < 0 || y > height - 1) {
      return unavailable("OUTSIDE_DATASET", this.descriptor, query,
        "Tile coordinate outside payload");
    }
    const value = interpolate(payload, this.descriptor.grid.interpolation,
      x, y, width, height, this.descriptor.grid.noData);
    if (value === undefined) {
      return unavailable("INTERPOLATION_FORBIDDEN", this.descriptor, query,
        "Contract interpolation NONE requires an exact coordinate");
    }
    if (value === null) {
      return unavailable("NO_DATA", this.descriptor, query,
        "No-data encountered; browser does not synthesize a fallback");
    }
    return Object.freeze({
      status: "OK",
      available: true,
      datasetId: this.descriptor.datasetId,
      tileId: location.tileId,
      quantity: this.descriptor.quantity.name,
      valueSemantics: this.descriptor.quantity.valueSemantics,
      value,
      units: this.descriptor.quantity.units,
      depthPlaneIndex: location.depthPlaneIndex ?? query.depthPlaneIndex,
      evidenceClass: this.descriptor.evidenceClass,
      visualizationIsAuthoritative: false,
      provenance: Object.freeze({
        solverName: this.descriptor.authority.solverName,
        solverVersion: this.descriptor.authority.solverVersion,
        sourceRevision: this.descriptor.authority.sourceRevision,
        runId: this.descriptor.authority.runId,
      }),
      query,
    });
  }
}
