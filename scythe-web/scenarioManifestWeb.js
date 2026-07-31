function finite(value, path) {
  if (!Number.isFinite(value)) throw new TypeError(`${path} must be finite`);
}

export class ScenarioManifestWeb {
  constructor(json) {
    if (!json || typeof json !== "object") throw new TypeError("scenario must be an object");
    if (!Array.isArray(json.datasets) || json.datasets.length === 0) {
      throw new Error("scenario.datasets must identify at least one contract");
    }
    const ids = new Set();
    const transmitters = (json.transmitters ?? []).map((tx, index) => {
      if (!tx?.id || ids.has(tx.id)) throw new Error(`Invalid or duplicate transmitter ${index}`);
      ids.add(tx.id);
      finite(tx.longitudeDegrees, `transmitters[${index}].longitudeDegrees`);
      finite(tx.latitudeDegrees, `transmitters[${index}].latitudeDegrees`);
      finite(tx.frequencyHz, `transmitters[${index}].frequencyHz`);
      return Object.freeze({ heightMeters: 0, ...tx });
    });
    if (json.activeTransmitterId && !ids.has(json.activeTransmitterId)) {
      throw new Error("activeTransmitterId does not reference a transmitter");
    }
    Object.assign(this, {
      id: json.id ?? "scythe-web-scenario",
      datasets: Object.freeze(json.datasets.map((item) => Object.freeze({ ...item }))),
      transmitters: Object.freeze(transmitters),
      activeTransmitterId: json.activeTransmitterId ?? transmitters[0]?.id ?? null,
      timeWindow: json.timeWindow ? Object.freeze({ ...json.timeWindow }) : null,
      operatorStart: json.operatorStart ? Object.freeze({ ...json.operatorStart }) : null,
      coverageThreshold: json.coverageThreshold
        ? Object.freeze({ ...json.coverageThreshold })
        : null,
    });
    Object.freeze(this);
  }
}
