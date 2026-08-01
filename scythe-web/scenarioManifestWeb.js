function finite(value, path, { min = -Infinity, max = Infinity } = {}) {
  if (!Number.isFinite(value) || value < min || value > max) {
    throw new TypeError(`${path} must be finite within [${min}, ${max}]`);
  }
}

function parseUtc(value, path) {
  if (value == null) return null;
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) throw new TypeError(`${path} must be a valid UTC time`);
  return parsed.toISOString();
}

/** Immutable, browser-side scenario binding. This selects data; it defines no physics. */
export class ScenarioManifestWeb {
  constructor(json) {
    if (!json || typeof json !== "object") throw new TypeError("scenario must be an object");
    if (!Array.isArray(json.datasets) || json.datasets.length === 0) {
      throw new Error("scenario.datasets must identify at least one contract");
    }
    const datasetIds = new Set();
    const datasets = json.datasets.map((item, index) => {
      if (!item?.id || datasetIds.has(item.id)) throw new Error(`Invalid or duplicate dataset ${index}`);
      if (typeof item.contractUrl !== "string" || !item.contractUrl) {
        throw new Error(`datasets[${index}].contractUrl is required`);
      }
      if (item.kind != null && !["RF", "OPTICAL"].includes(item.kind)) {
        throw new Error(`datasets[${index}].kind must be RF or OPTICAL`);
      }
      datasetIds.add(item.id);
      return Object.freeze({ enabled: true, ...item });
    });
    const transmitterIds = new Set();
    const transmitters = (json.transmitters ?? []).map((tx, index) => {
      if (!tx?.id || transmitterIds.has(tx.id)) {
        throw new Error(`Invalid or duplicate transmitter ${index}`);
      }
      transmitterIds.add(tx.id);
      finite(tx.longitudeDegrees, `transmitters[${index}].longitudeDegrees`, { min: -180, max: 180 });
      finite(tx.latitudeDegrees, `transmitters[${index}].latitudeDegrees`, { min: -90, max: 90 });
      finite(tx.frequencyHz, `transmitters[${index}].frequencyHz`, { min: Number.MIN_VALUE });
      if (tx.rangeMeters != null) finite(tx.rangeMeters, `transmitters[${index}].rangeMeters`, { min: 0 });
      return Object.freeze({ heightMeters: 0, ...tx });
    });
    if (json.activeTransmitterId && !transmitterIds.has(json.activeTransmitterId)) {
      throw new Error("activeTransmitterId does not reference a transmitter");
    }
    const timeWindow = json.timeWindow ? Object.freeze({
      startUtc: parseUtc(json.timeWindow.startUtc, "timeWindow.startUtc"),
      endUtc: parseUtc(json.timeWindow.endUtc, "timeWindow.endUtc"),
    }) : null;
    if (timeWindow?.startUtc && timeWindow?.endUtc && timeWindow.startUtc > timeWindow.endUtc) {
      throw new Error("timeWindow startUtc must not follow endUtc");
    }
    if (json.operatorStart) {
      finite(json.operatorStart.longitudeDegrees, "operatorStart.longitudeDegrees", { min: -180, max: 180 });
      finite(json.operatorStart.latitudeDegrees, "operatorStart.latitudeDegrees", { min: -90, max: 90 });
      finite(json.operatorStart.heightMeters ?? 0, "operatorStart.heightMeters");
    }
    Object.assign(this, {
      id: json.id ?? "scythe-web-scenario",
      datasets: Object.freeze(datasets),
      transmitters: Object.freeze(transmitters),
      activeTransmitterId: json.activeTransmitterId ?? transmitters[0]?.id ?? null,
      timeWindow,
      operatorStart: json.operatorStart ? Object.freeze({ heightMeters: 0, ...json.operatorStart }) : null,
      coverageThreshold: json.coverageThreshold ? Object.freeze({ ...json.coverageThreshold }) : null,
      coverageFootprintMeters: json.coverageFootprintMeters ?? null,
      opticalDepthPlaneIndex: json.opticalDepthPlaneIndex ?? null,
      coverageGrid: json.coverageGrid ? Object.freeze({ ...json.coverageGrid }) : null,
    });
    if (this.coverageFootprintMeters != null) {
      finite(this.coverageFootprintMeters, "coverageFootprintMeters", { min: Number.MIN_VALUE });
    }
    if (this.opticalDepthPlaneIndex != null &&
        (!Number.isInteger(this.opticalDepthPlaneIndex) || this.opticalDepthPlaneIndex < 0)) {
      throw new TypeError("opticalDepthPlaneIndex must be a non-negative integer");
    }
    if (this.coverageGrid) {
      for (const key of ["westDegrees", "southDegrees", "eastDegrees", "northDegrees"])
        finite(this.coverageGrid[key], `coverageGrid.${key}`);
      for (const key of ["longitudeCells", "latitudeCells"]) {
        if (!Number.isInteger(this.coverageGrid[key]) || this.coverageGrid[key] < 1) {
          throw new TypeError(`coverageGrid.${key} must be a positive integer`);
        }
      }
      if (this.coverageGrid.longitudeCells * this.coverageGrid.latitudeCells > 4096) {
        throw new RangeError("coverageGrid cannot exceed 4096 cells");
      }
    }
    Object.freeze(this);
  }
}
