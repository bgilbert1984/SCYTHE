const SHA256 = /^[a-f0-9]{64}$/;

function bytesToHex(bytes) {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(buffer, cryptoImpl = globalThis.crypto) {
  if (!cryptoImpl?.subtle?.digest) throw new Error("Web Crypto SHA-256 is required");
  return bytesToHex(new Uint8Array(await cryptoImpl.subtle.digest("SHA-256", buffer)));
}

/**
 * Fetches immutable tile assets, verifies their declared digest, and delegates
 * binary interpretation to an explicit decoder.
 */
export class VerifiedTileLoader {
  constructor({
    tiles,
    decode,
    fetchImpl = globalThis.fetch,
    cryptoImpl = globalThis.crypto,
    maxCacheEntries = 32,
  }) {
    if (!Array.isArray(tiles) || tiles.length === 0) throw new TypeError("tiles are required");
    if (typeof decode !== "function") throw new TypeError("decode(buffer, tile) is required");
    if (typeof fetchImpl !== "function") throw new TypeError("fetch is required");
    if (!Number.isInteger(maxCacheEntries) || maxCacheEntries < 1) {
      throw new RangeError("maxCacheEntries must be a positive integer");
    }
    this.tiles = new Map(tiles.map((tile) => {
      if (!tile?.id || !tile.url || !SHA256.test(tile.sha256)) {
        throw new Error("Each tile requires id, url, and lowercase SHA-256");
      }
      return [tile.id, Object.freeze({ ...tile })];
    }));
    this.decode = decode;
    this.fetchImpl = fetchImpl;
    this.cryptoImpl = cryptoImpl;
    this.maxCacheEntries = maxCacheEntries;
    this.cache = new Map();
    this.inFlight = new Map();
  }

  async getTilePayload(tileId) {
    if (this.cache.has(tileId)) {
      const payload = this.cache.get(tileId);
      this.cache.delete(tileId);
      this.cache.set(tileId, payload);
      return payload;
    }
    if (this.inFlight.has(tileId)) return this.inFlight.get(tileId);
    const promise = this.#load(tileId).finally(() => this.inFlight.delete(tileId));
    this.inFlight.set(tileId, promise);
    return promise;
  }

  async #load(tileId) {
    const tile = this.tiles.get(tileId);
    if (!tile) throw new Error(`Unknown tile: ${String(tileId)}`);
    const response = await this.fetchImpl(tile.url, { cache: "force-cache" });
    if (!response.ok) throw new Error(`Tile fetch failed (${response.status}): ${tile.id}`);
    const buffer = await response.arrayBuffer();
    if (Number.isInteger(tile.sizeBytes) && buffer.byteLength !== tile.sizeBytes) {
      throw new Error(`Tile size mismatch: ${tile.id}`);
    }
    const digest = await sha256Hex(buffer, this.cryptoImpl);
    if (digest !== tile.sha256) throw new Error(`Tile SHA-256 mismatch: ${tile.id}`);
    const payload = await this.decode(buffer, tile);
    if (!payload || typeof payload !== "object") {
      throw new Error(`Tile decoder returned no payload: ${tile.id}`);
    }
    this.cache.set(tileId, payload);
    while (this.cache.size > this.maxCacheEntries) {
      this.cache.delete(this.cache.keys().next().value);
    }
    return payload;
  }

  clear() {
    this.cache.clear();
  }
}

export function decodeFloat32Grid(buffer, tile) {
  const shape = tile.shape;
  if (!Array.isArray(shape) || shape.length !== 2 ||
      !shape.every((n) => Number.isInteger(n) && n > 0)) {
    throw new Error(`Tile ${tile.id} requires shape [width, height]`);
  }
  if (buffer.byteLength !== shape[0] * shape[1] * Float32Array.BYTES_PER_ELEMENT) {
    throw new Error(`Float32 payload length does not match tile ${tile.id}`);
  }
  return Object.freeze({ shape: Object.freeze([...shape]), values: new Float32Array(buffer) });
}
