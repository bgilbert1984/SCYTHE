import { GeodeticTileIndex } from "./tileIndex.js";
import {
  VerifiedTileLoader,
  decodeScaledUint16Grid,
  sha256Hex,
} from "./tileLoader.js";

const metadataPromises = new Map();

function contractAsset(descriptor, path, role = null) {
  const asset = descriptor.assets.find((candidate) =>
    candidate.path === path && (role === null || candidate.role === role));
  if (!asset) throw new Error(`Contract does not declare ${role ?? "asset"} ${path}`);
  return asset;
}

async function verifiedJson(url, asset, fetchImpl, cryptoImpl) {
  const response = await fetchImpl(url, { cache: "force-cache" });
  if (!response.ok) throw new Error(`Metadata fetch failed (${response.status}): ${asset.path}`);
  const buffer = await response.arrayBuffer();
  if (buffer.byteLength !== asset.sizeBytes) throw new Error(`Metadata size mismatch: ${asset.path}`);
  if (await sha256Hex(buffer, cryptoImpl) !== asset.sha256) {
    throw new Error(`Metadata SHA-256 mismatch: ${asset.path}`);
  }
  return JSON.parse(new TextDecoder().decode(buffer));
}

function validateMetadata(metadata, descriptor) {
  if (metadata?.format !== "SCYTHE_GEODETIC_TILESET_V1" ||
      metadata.datasetId !== descriptor.datasetId) {
    throw new Error("Tile metadata identity does not match the dataset contract");
  }
  if (metadata.visualizationIsAuthoritative !== false) {
    throw new Error("Tile metadata must identify itself as non-authoritative visualization");
  }
  if (metadata.derivedFrom !== descriptor.grid.authoritativeAssetPath) {
    throw new Error("Tile metadata does not identify the contract authority it derives from");
  }
  if (metadata.quantity !== descriptor.quantity.name || metadata.units !== descriptor.quantity.units) {
    throw new Error("Tile metadata quantity or units do not match the dataset contract");
  }
  if (!Array.isArray(metadata.tiles) || metadata.tiles.length === 0) {
    throw new Error("Tile metadata requires at least one tile");
  }
  const authority = contractAsset(descriptor, descriptor.grid.authoritativeAssetPath);
  for (const tile of metadata.tiles) {
    const compact = contractAsset(descriptor, tile.path, "DERIVED_VISUALIZATION");
    if (compact.sha256 !== tile.sha256 || compact.sizeBytes !== tile.sizeBytes) {
      throw new Error(`Tile ${tile.id} integrity does not match its contract asset`);
    }
    const transform = descriptor.lineage.transformations.find((item) =>
      item.name === "linear-uint16-quantization" &&
      item.inputSha256 === authority.sha256 && item.outputSha256 === tile.sha256);
    if (!transform || transform.parameters.scale !== tile.encoding?.scale ||
        transform.parameters.offset !== tile.encoding?.offset ||
        transform.parameters.noDataRaw !== tile.encoding?.noDataRaw) {
      throw new Error(`Tile ${tile.id} scale/offset are not bound by contract lineage`);
    }
  }
  return Object.freeze({
    ...metadata,
    tiles: Object.freeze(metadata.tiles.map((tile) => Object.freeze({
      ...tile,
      shape: Object.freeze([...tile.shape]),
      encoding: Object.freeze({ ...tile.encoding }),
    }))),
  });
}

export async function loadRegionalTileMetadata(descriptor, binding, {
  fetchImpl = globalThis.fetch,
  cryptoImpl = globalThis.crypto,
} = {}) {
  const contractUrl = binding?.contractUrl;
  if (!contractUrl) throw new Error("A scenario contractUrl is required for regional tiles");
  const key = `${contractUrl}|${descriptor.datasetId}`;
  if (!metadataPromises.has(key)) {
    const promise = (async () => {
      const asset = contractAsset(descriptor, "tile-metadata.json");
      const url = new URL(asset.path, contractUrl).href;
      return validateMetadata(await verifiedJson(url, asset, fetchImpl, cryptoImpl), descriptor);
    })().catch((error) => {
      metadataPromises.delete(key);
      throw error;
    });
    metadataPromises.set(key, promise);
  }
  return metadataPromises.get(key);
}

export async function createRegionalTileIndex(descriptor, binding, options = {}) {
  const metadata = await loadRegionalTileMetadata(descriptor, binding, options);
  return new GeodeticTileIndex(metadata.tiles);
}

export async function createRegionalTileLoader(descriptor, binding, options = {}) {
  const metadata = await loadRegionalTileMetadata(descriptor, binding, options);
  return new VerifiedTileLoader({
    tiles: metadata.tiles.map((tile) => ({
      ...tile,
      url: new URL(tile.path, binding.contractUrl).href,
    })),
    decode: decodeScaledUint16Grid,
    fetchImpl: options.fetchImpl ?? globalThis.fetch,
    cryptoImpl: options.cryptoImpl ?? globalThis.crypto,
    maxCacheEntries: options.maxCacheEntries ?? 4,
  });
}

export function regionalRfDemoConfig(baseUrl = import.meta.url) {
  const contractUrl = new URL("../datasets/ntia-itm-sf-bay-area-v1/manifest.json", baseUrl).href;
  return {
    contractUrl,
    fixedStepSeconds: 10,
    scenario: {
      id: "ntia-itm-sf-bay-demo",
      datasets: [{ id: "regional-rf", kind: "RF", contractUrl }],
      activeTransmitterId: "sf-itm-tx",
      operatorStart: {
        longitudeDegrees: -122.50,
        latitudeDegrees: 37.84,
        heightMeters: 2500,
      },
      coverageThreshold: { value: 145, units: "dB", comparison: "LTE" },
      coverageFootprintMeters: 300,
      coverageGrid: {
        westDegrees: -122.5994,
        southDegrees: 37.5949,
        eastDegrees: -122.2394,
        northDegrees: 37.9549,
        longitudeCells: 12,
        latitudeCells: 12,
        heightMeters: 0,
      },
      transmitters: [{
        id: "sf-itm-tx",
        label: "NTIA ITM // SF TX",
        longitudeDegrees: -122.4194,
        latitudeDegrees: 37.7749,
        heightMeters: 30,
        frequencyHz: 900_000_000,
        rangeMeters: 20_000,
      }],
    },
    createTileIndex: (descriptor, binding) => createRegionalTileIndex(descriptor, binding),
    createTileLoader: (descriptor, binding) => createRegionalTileLoader(descriptor, binding),
  };
}
