import { loadContract } from "./contractLoader.js";
import { ScytheRfSampler } from "./rfSampler.js";
import { ScytheOpticsSampler } from "./opticsSampler.js";
import { MonocleOverlayLayer } from "./monocleOverlayLayer.js";
import { resolveScytheWebConfig } from "./scytheWebConfig.js";
import { ScenarioManifestWeb } from "./scenarioManifestWeb.js";

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
  if (typeof config.createTileIndex !== "function") {
    throw new Error("SCYTHE-Web createTileIndex(descriptor) is required");
  }
  if (typeof config.createTileLoader !== "function") {
    throw new Error("SCYTHE-Web createTileLoader(descriptor) is required");
  }

  const scenarioInput = config.scenario ?? {};
  const scenario = scenarioInput instanceof ScenarioManifestWeb
    ? scenarioInput
    : new ScenarioManifestWeb({
      ...scenarioInput,
      datasets: scenarioInput.datasets ?? (config.contractUrl ? [{
        id: "primary-rf",
        kind: "RF",
        contractUrl: config.contractUrl,
      }] : []),
    });
  const activeDatasets = scenario.datasets.filter((item) => item.enabled !== false);
  const loaded = await Promise.all(activeDatasets.map(async (binding) => {
    const descriptor = await loadContract(binding.contractUrl, { fetchImpl: config.fetchImpl });
    if (binding.contractId && binding.contractId !== descriptor.datasetId) {
      throw new Error(
        `Scenario dataset ${binding.id} expected ${binding.contractId}, received ${descriptor.datasetId}`,
      );
    }
    const tileIndex = await config.createTileIndex(descriptor, binding);
    const tileLoader = await config.createTileLoader(descriptor, binding);
    const kind = binding.kind ?? descriptor.physics.domain;
    if (kind === "RF") {
      return { binding, descriptor, rfSampler: new ScytheRfSampler({ descriptor, tileIndex, tileLoader }) };
    }
    if (kind === "OPTICAL") {
      return { binding, descriptor, opticsSampler: new ScytheOpticsSampler({ descriptor, tileIndex, tileLoader }) };
    }
    throw new Error(`Dataset ${binding.id} requires an explicit RF or OPTICAL kind`);
  }));
  const rfEntry = loaded.find((item) => item.rfSampler);
  const opticsEntry = loaded.find((item) => item.opticsSampler);
  if (!rfEntry && !opticsEntry) throw new Error("Scenario has no active RF or optical dataset");
  const viewer = await waitForViewer(
    config.resolveViewer ?? (() => globalThis.globe?._viewer),
    config.viewerTimeoutMilliseconds,
  );
  const layer = new MonocleOverlayLayer({
    viewer,
    Cesium: config.Cesium ?? globalThis.Cesium,
    rfSampler: rfEntry?.rfSampler ?? null,
    opticsSampler: opticsEntry?.opticsSampler ?? null,
    scenario,
    fixedStepSeconds: config.fixedStepSeconds,
    timeSource: config.timeSource,
  }).start();
  if (scenario.operatorStart) {
    viewer.camera.setView({
      destination: (config.Cesium ?? globalThis.Cesium).Cartesian3.fromDegrees(
        scenario.operatorStart.longitudeDegrees,
        scenario.operatorStart.latitudeDegrees,
        scenario.operatorStart.heightMeters,
      ),
    });
  }
  return Object.freeze({
    scenario,
    datasets: Object.freeze(loaded),
    descriptor: rfEntry?.descriptor ?? opticsEntry.descriptor,
    rfSampler: rfEntry?.rfSampler ?? null,
    opticsSampler: opticsEntry?.opticsSampler ?? null,
    layer,
  });
}

if (typeof window !== "undefined") {
  window.SCYTHEWeb = Object.freeze({
    install: installScytheWeb,
    ScytheRfSampler,
    ScytheOpticsSampler,
    MonocleOverlayLayer,
    resolveConfig: resolveScytheWebConfig,
    ScenarioManifestWeb,
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
