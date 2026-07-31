import { EVIDENCE_STYLES } from "./evidenceStyles.js";

export const CONTRACT_VERSION = "1.0";

export const DEFAULT_SCYTHE_WEB_CONFIG = Object.freeze({
  enabled: false,
  datasetBaseUrl: "/datasets/global",
  contractVersion: CONTRACT_VERSION,
  evidenceStyles: EVIDENCE_STYLES,
  fixedStepSeconds: 0.25,
  tileCacheEntries: 32,
});

export function resolveScytheWebConfig(overrides = {}) {
  const config = { ...DEFAULT_SCYTHE_WEB_CONFIG, ...overrides };
  if (config.contractVersion !== CONTRACT_VERSION) {
    throw new Error(`SCYTHE-Web supports contract ${CONTRACT_VERSION} only`);
  }
  if (!(config.fixedStepSeconds > 0)) {
    throw new RangeError("fixedStepSeconds must be positive");
  }
  if (!Number.isInteger(config.tileCacheEntries) || config.tileCacheEntries < 1) {
    throw new RangeError("tileCacheEntries must be a positive integer");
  }
  return Object.freeze(config);
}
