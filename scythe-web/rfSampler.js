import { validateContractBoundary } from "./contractLoader.js";

export const SAMPLE_STATUS = Object.freeze({
  OK: "OK",
  OUTSIDE_DATASET: "OUTSIDE_DATASET",
  OUTSIDE_TIME: "OUTSIDE_TIME",
  OUTSIDE_FREQUENCY: "OUTSIDE_FREQUENCY",
  NO_DATA: "NO_DATA",
  INTERPOLATION_FORBIDDEN: "INTERPOLATION_FORBIDDEN",
});

const SUPPORTED_INTERPOLATION = new Set(["NONE", "NEAREST", "BILINEAR"]);
const INTEGER_EPSILON = 1e-9;

function finite(value, name) {
  if (!Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}

function normalizeQuery(query) {
  if (!query || typeof query !== "object") {
    throw new TypeError("sample query must be an object");
  }
  const longitudeDegrees = finite(query.longitudeDegrees, "longitudeDegrees");
  const latitudeDegrees = finite(query.latitudeDegrees, "latitudeDegrees");
  const heightMeters = finite(query.heightMeters ?? 0, "heightMeters");
  const frequencyHz = finite(query.frequencyHz, "frequencyHz");
  if (longitudeDegrees < -180 || longitudeDegrees > 180) {
    throw new RangeError("longitudeDegrees must be within [-180, 180]");
  }
  if (latitudeDegrees < -90 || latitudeDegrees > 90) {
    throw new RangeError("latitudeDegrees must be within [-90, 90]");
  }
  if (frequencyHz <= 0) throw new RangeError("frequencyHz must be positive");

  const utc = new Date(query.utc);
  if (!Number.isFinite(utc.getTime())) throw new TypeError("utc must be a valid time");

  let coverageThreshold = null;
  if (query.coverageThreshold != null) {
    const threshold = query.coverageThreshold;
    finite(threshold.value, "coverageThreshold.value");
    if (!["GTE", "LTE"].includes(threshold.comparison)) {
      throw new TypeError("coverageThreshold.comparison must be GTE or LTE");
    }
    if (typeof threshold.units !== "string" || threshold.units.length === 0) {
      throw new TypeError("coverageThreshold.units is required");
    }
    coverageThreshold = Object.freeze({
      value: threshold.value,
      comparison: threshold.comparison,
      units: threshold.units,
    });
  }

  return Object.freeze({
    longitudeDegrees,
    latitudeDegrees,
    heightMeters,
    frequencyHz,
    utc: utc.toISOString(),
    coverageThreshold,
  });
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

function checkTime(descriptor, query) {
  const t = Date.parse(query.utc);
  const from = descriptor.temporal?.validFromUtc
    ? Date.parse(descriptor.temporal.validFromUtc)
    : null;
  const to = descriptor.temporal?.validToUtc
    ? Date.parse(descriptor.temporal.validToUtc)
    : null;
  return !((from != null && t < from) || (to != null && t > to));
}

function checkFrequency(descriptor, query) {
  const rf = descriptor.physics.rf;
  const halfBandwidth = rf.bandwidthHz / 2;
  return halfBandwidth === 0
    ? query.frequencyHz === rf.frequencyHz
    : Math.abs(query.frequencyHz - rf.frequencyHz) <= halfBandwidth;
}

function payloadShape(payload) {
  if (!Array.isArray(payload.shape) || payload.shape.length < 2) {
    throw new Error("Tile payload shape must be [width, height]");
  }
  const [width, height] = payload.shape;
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1) {
    throw new Error("Tile payload width and height must be positive integers");
  }
  if (!ArrayBuffer.isView(payload.values) || payload.values.length < width * height) {
    throw new Error("Tile payload values do not match its shape");
  }
  return { width, height };
}

function coordinate(location, width, height) {
  if (Number.isFinite(location.x) && Number.isFinite(location.y)) {
    return { x: location.x, y: location.y };
  }
  if (Number.isFinite(location.u) && Number.isFinite(location.v)) {
    return { x: location.u * (width - 1), y: location.v * (height - 1) };
  }
  throw new Error("Tile location must provide x/y or normalized u/v coordinates");
}

function valueIsNoData(value, index, payload, noData) {
  if (payload.validMask && payload.validMask[index] === 0) return true;
  if (noData.policy === "NAN" && Number.isNaN(value)) return true;
  if (noData.policy === "SENTINEL" && value === noData.value) return true;
  return false;
}

function readValue(values, index, payload, noData) {
  const value = values[index];
  return valueIsNoData(value, index, payload, noData) ? null : value;
}

function nearest(values, x, y, shape, payload, noData) {
  const xi = Math.max(0, Math.min(shape.width - 1, Math.round(x)));
  const yi = Math.max(0, Math.min(shape.height - 1, Math.round(y)));
  return readValue(values, yi * shape.width + xi, payload, noData);
}

function bilinear(values, x, y, shape, payload, noData) {
  const x0 = Math.max(0, Math.min(shape.width - 1, Math.floor(x)));
  const y0 = Math.max(0, Math.min(shape.height - 1, Math.floor(y)));
  const x1 = Math.min(shape.width - 1, x0 + 1);
  const y1 = Math.min(shape.height - 1, y0 + 1);
  const tx = x - x0;
  const ty = y - y0;
  const indices = [
    y0 * shape.width + x0,
    y0 * shape.width + x1,
    y1 * shape.width + x0,
    y1 * shape.width + x1,
  ];
  const samples = indices.map((index) => readValue(values, index, payload, noData));
  if (samples.some((value) => value == null)) return null;
  const top = samples[0] * (1 - tx) + samples[1] * tx;
  const bottom = samples[2] * (1 - tx) + samples[3] * tx;
  return top * (1 - ty) + bottom * ty;
}

function exact(values, x, y, shape, payload, noData) {
  if (
    Math.abs(x - Math.round(x)) > INTEGER_EPSILON ||
    Math.abs(y - Math.round(y)) > INTEGER_EPSILON
  ) {
    return undefined;
  }
  return nearest(values, x, y, shape, payload, noData);
}

function interpolate(values, method, x, y, shape, payload, noData) {
  if (method === "NEAREST") return nearest(values, x, y, shape, payload, noData);
  if (method === "BILINEAR") return bilinear(values, x, y, shape, payload, noData);
  return exact(values, x, y, shape, payload, noData);
}

/**
 * Browser-side sampler for already-computed RF grids.
 *
 * tileIndex.locate(query) must return null or:
 *   { tileId, x, y }                     // grid coordinates, or
 *   { tileId, u, v }                     // normalized [0,1] coordinates.
 *
 * tileLoader.getTilePayload(tileId) must return:
 *   { values: TypedArray, shape: [width, height],
 *     validMask?: Uint8Array, uncertaintyValues?: TypedArray }.
 *
 * The adapter owns binary layout and axis-order knowledge. This class never
 * guesses them and never computes propagation.
 */
export class ScytheRfSampler {
  constructor({ descriptor, tileIndex, tileLoader }) {
    this.descriptor = validateContractBoundary(descriptor);
    if (!["RF", "RF_AND_OPTICAL"].includes(this.descriptor.physics.domain)) {
      throw new Error("ScytheRfSampler requires an RF dataset");
    }
    if (!this.descriptor.physics.rf) {
      throw new Error("RF dataset is missing physics.rf");
    }
    if (this.descriptor.spatialReference.type !== "GEODETIC_GRID") {
      throw new Error("The first SCYTHE-Web RF sampler requires GEODETIC_GRID");
    }
    if (!SUPPORTED_INTERPOLATION.has(this.descriptor.grid.interpolation)) {
      throw new Error(
        `Interpolation ${this.descriptor.grid.interpolation} requires a dedicated adapter`,
      );
    }
    if (typeof tileIndex?.locate !== "function") {
      throw new TypeError("tileIndex.locate(query) is required");
    }
    if (
      this.descriptor.spatialReference.crossesAntimeridian === true &&
      tileIndex.supportsAntimeridian !== true
    ) {
      throw new Error(
        "Antimeridian-crossing datasets require tileIndex.supportsAntimeridian",
      );
    }
    if (typeof tileLoader?.getTilePayload !== "function") {
      throw new TypeError("tileLoader.getTilePayload(tileId) is required");
    }
    this.tileIndex = tileIndex;
    this.tileLoader = tileLoader;
  }

  async sample(queryInput) {
    const query = normalizeQuery(queryInput);
    const descriptor = this.descriptor;

    if (!checkTime(descriptor, query)) {
      return unavailable(SAMPLE_STATUS.OUTSIDE_TIME, descriptor, query, "UTC outside validity");
    }
    if (!checkFrequency(descriptor, query)) {
      return unavailable(
        SAMPLE_STATUS.OUTSIDE_FREQUENCY,
        descriptor,
        query,
        "Frequency outside dataset band",
      );
    }

    const location = await this.tileIndex.locate(query);
    if (!location) {
      return unavailable(
        SAMPLE_STATUS.OUTSIDE_DATASET,
        descriptor,
        query,
        "Position outside indexed tiles",
      );
    }

    const payload = await this.tileLoader.getTilePayload(location.tileId);
    const shape = payloadShape(payload);
    const { x, y } = coordinate(location, shape.width, shape.height);
    if (x < 0 || x > shape.width - 1 || y < 0 || y > shape.height - 1) {
      return unavailable(
        SAMPLE_STATUS.OUTSIDE_DATASET,
        descriptor,
        query,
        "Tile coordinate outside payload",
      );
    }

    const method = descriptor.grid.interpolation;
    const value = interpolate(
      payload.values,
      method,
      x,
      y,
      shape,
      payload,
      descriptor.grid.noData,
    );
    if (value === undefined) {
      return unavailable(
        SAMPLE_STATUS.INTERPOLATION_FORBIDDEN,
        descriptor,
        query,
        "Contract interpolation NONE requires an exact grid coordinate",
      );
    }
    if (value === null) {
      return unavailable(
        SAMPLE_STATUS.NO_DATA,
        descriptor,
        query,
        "No-data encountered; browser does not synthesize a fallback",
      );
    }

    let uncertaintyValue = null;
    if (payload.uncertaintyValues) {
      uncertaintyValue = interpolate(
        payload.uncertaintyValues,
        method,
        x,
        y,
        shape,
        payload,
        { policy: "NAN", value: null },
      );
      if (uncertaintyValue === undefined) uncertaintyValue = null;
    }

    const threshold = query.coverageThreshold;
    if (threshold && threshold.units !== descriptor.quantity.units) {
      throw new Error(
        `Coverage threshold units ${threshold.units} do not match ${descriptor.quantity.units}`,
      );
    }
    const coverage = threshold
      ? threshold.comparison === "GTE"
        ? value >= threshold.value
        : value <= threshold.value
      : null;

    return Object.freeze({
      status: SAMPLE_STATUS.OK,
      available: true,
      datasetId: descriptor.datasetId,
      evidenceClass: descriptor.evidenceClass,
      visualizationIsAuthoritative: false,
      tileId: location.tileId,
      quantity: descriptor.quantity.name,
      value,
      units: descriptor.quantity.units,
      valueSemantics: descriptor.quantity.valueSemantics,
      coverage,
      uncertainty: Object.freeze({
        kind: descriptor.quantity.uncertainty.kind,
        description: descriptor.quantity.uncertainty.description,
        value: uncertaintyValue,
        units: uncertaintyValue == null ? null : descriptor.quantity.units,
      }),
      provenance: Object.freeze({
        solverName: descriptor.authority.solverName,
        solverVersion: descriptor.authority.solverVersion,
        sourceRevision: descriptor.authority.sourceRevision,
        runId: descriptor.authority.runId,
      }),
      query,
    });
  }

  /** Sample a bounded geodetic grid exclusively through the scalar sampler. */
  async sampleGrid({
    westDegrees, southDegrees, eastDegrees, northDegrees,
    longitudeCells, latitudeCells, ...query
  }) {
    for (const [name, value] of Object.entries({
      westDegrees, southDegrees, eastDegrees, northDegrees,
    })) finite(value, name);
    if (!(eastDegrees > westDegrees) || !(northDegrees > southDegrees)) {
      throw new RangeError("sampleGrid requires increasing west/south/east/north bounds");
    }
    if (!Number.isInteger(longitudeCells) || !Number.isInteger(latitudeCells) ||
        longitudeCells < 1 || latitudeCells < 1 ||
        longitudeCells * latitudeCells > 4096) {
      throw new RangeError("sampleGrid cell counts must define 1-4096 cells");
    }
    const longitudeStep = (eastDegrees - westDegrees) / longitudeCells;
    const latitudeStep = (northDegrees - southDegrees) / latitudeCells;
    const cells = [];
    for (let y = 0; y < latitudeCells; y += 1) {
      for (let x = 0; x < longitudeCells; x += 1) {
        const boundsDegrees = Object.freeze([
          westDegrees + x * longitudeStep,
          southDegrees + y * latitudeStep,
          westDegrees + (x + 1) * longitudeStep,
          southDegrees + (y + 1) * latitudeStep,
        ]);
        const sample = await this.sample({
          ...query,
          longitudeDegrees: (boundsDegrees[0] + boundsDegrees[2]) / 2,
          latitudeDegrees: (boundsDegrees[1] + boundsDegrees[3]) / 2,
        });
        cells.push(Object.freeze({ x, y, boundsDegrees, sample }));
      }
    }
    return Object.freeze(cells);
  }
}
