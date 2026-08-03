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
    const response = await this.fetchImpl.call(globalThis, tile.url, { cache: "force-cache" });
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

/**
 * Decode a compact tile using an explicit, checksum-bound tile descriptor:
 * encoding: { scalarType: "UINT16", byteOrder, scale, offset, noDataRaw? }.
 * Returned values are physical Float32 values; scale and offset are never
 * inferred. The tile descriptor itself should be carried by a hashed contract
 * asset so these semantics are covered by Global Contract lineage.
 */
export function decodeScaledUint16Grid(buffer, tile) {
  const shape = tile.shape;
  if (!Array.isArray(shape) || shape.length !== 2 ||
      !shape.every((n) => Number.isInteger(n) && n > 0)) {
    throw new Error(`Tile ${tile.id} requires shape [width, height]`);
  }
  const encoding = tile.encoding;
  if (!encoding || encoding.scalarType !== "UINT16") {
    throw new Error(`Tile ${tile.id} requires explicit UINT16 encoding metadata`);
  }
  if (!["LITTLE_ENDIAN", "BIG_ENDIAN"].includes(encoding.byteOrder)) {
    throw new Error(`Tile ${tile.id} requires explicit byteOrder`);
  }
  if (!Number.isFinite(encoding.scale) || encoding.scale === 0 ||
      !Number.isFinite(encoding.offset)) {
    throw new Error(`Tile ${tile.id} requires finite non-zero scale and offset`);
  }
  const count = shape[0] * shape[1];
  if (buffer.byteLength !== count * Uint16Array.BYTES_PER_ELEMENT) {
    throw new Error(`Uint16 payload length does not match tile ${tile.id}`);
  }
  if (encoding.noDataRaw != null &&
      (!Number.isInteger(encoding.noDataRaw) || encoding.noDataRaw < 0 ||
       encoding.noDataRaw > 65535)) {
    throw new Error(`Tile ${tile.id} has invalid noDataRaw`);
  }
  const view = new DataView(buffer);
  const values = new Float32Array(count);
  const validMask = encoding.noDataRaw == null ? null : new Uint8Array(count);
  const littleEndian = encoding.byteOrder === "LITTLE_ENDIAN";
  for (let index = 0; index < count; index += 1) {
    const raw = view.getUint16(index * 2, littleEndian);
    const valid = encoding.noDataRaw == null || raw !== encoding.noDataRaw;
    if (validMask) validMask[index] = valid ? 1 : 0;
    values[index] = valid ? encoding.scale * raw + encoding.offset : Number.NaN;
  }
  return Object.freeze({
    shape: Object.freeze([...shape]),
    values,
    ...(validMask ? { validMask } : {}),
    encoding: Object.freeze({ ...encoding }),
  });
}
