import { loadContract } from "./contractLoader.js";
import { ScytheRfSampler } from "./rfSampler.js";
import { ScytheOpticsSampler } from "./opticsSampler.js";
import { MonocleOverlayLayer } from "./monocleOverlayLayer.js";
import { resolveScytheWebConfig } from "./scytheWebConfig.js";

async function waitForViewer(resolveViewer, timeoutMilliseconds = 15_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMilliseconds) {
    const viewer = resolveViewer();
    if (viewer?.scene) return viewer;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Cesium viewer was not available before timeout");
}

/**
 * Start the contract-backed browser instrument.
 *
 * createTileIndex and createTileLoader are intentionally required. Binary
 * layout, axis order, and derived-tile lineage belong to the dataset adapter,
 * not to the generic sampler.
 */
export async function installScytheWeb(config) {
  config = resolveScytheWebConfig(config);
  if (!config?.contractUrl) throw new Error("SCYTHE-Web contractUrl is required");
  if (typeof config.createTileIndex !== "function") {
    throw new Error("SCYTHE-Web createTileIndex(descriptor) is required");
  }
  if (typeof config.createTileLoader !== "function") {
    throw new Error("SCYTHE-Web createTileLoader(descriptor) is required");
  }

  const descriptor = await loadContract(config.contractUrl);
  const viewer = await waitForViewer(
    config.resolveViewer ?? (() => globalThis.globe?._viewer),
    config.viewerTimeoutMilliseconds,
  );
  const rfSampler = new ScytheRfSampler({
    descriptor,
    tileIndex: await config.createTileIndex(descriptor),
    tileLoader: await config.createTileLoader(descriptor),
  });
  const layer = new MonocleOverlayLayer({
    viewer,
    Cesium: config.Cesium ?? globalThis.Cesium,
    rfSampler,
    scenario: config.scenario,
    fixedStepSeconds: config.fixedStepSeconds,
    timeSource: config.timeSource,
  }).start();
  return Object.freeze({ descriptor, rfSampler, layer });
}

if (typeof window !== "undefined") {
  window.SCYTHEWeb = Object.freeze({
    install: installScytheWeb,
    ScytheRfSampler,
    ScytheOpticsSampler,
    MonocleOverlayLayer,
    resolveConfig: resolveScytheWebConfig,
  });

  const config = window.SCYTHE_WEB_CONFIG;
  if (config?.enabled === true) {
    installScytheWeb(config)
      .then((client) => {
        window.scytheWebClient = client;
        console.info(
          `[SCYTHE-Web] Contract-backed client active: ${client.descriptor.datasetId}`,
        );
      })
      .catch((error) => {
        console.error("[SCYTHE-Web] Refused to start:", error);
      });
  }
}
