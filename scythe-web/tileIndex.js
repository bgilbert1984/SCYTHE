function finite(value, name) {
  if (!Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}

function containsLongitude(tile, longitude) {
  if (tile.westDegrees <= tile.eastDegrees) {
    return longitude >= tile.westDegrees && longitude <= tile.eastDegrees;
  }
  return longitude >= tile.westDegrees || longitude <= tile.eastDegrees;
}

function longitudeFraction(tile, longitude) {
  if (tile.westDegrees <= tile.eastDegrees) {
    return (longitude - tile.westDegrees) /
      (tile.eastDegrees - tile.westDegrees);
  }
  const span = 360 - tile.westDegrees + tile.eastDegrees;
  const offset = longitude >= tile.westDegrees
    ? longitude - tile.westDegrees
    : 360 - tile.westDegrees + longitude;
  return offset / span;
}

function validateTile(tile, index) {
  if (!tile || typeof tile !== "object") throw new TypeError(`tiles[${index}] is invalid`);
  if (typeof tile.id !== "string" || !tile.id) throw new TypeError(`tiles[${index}].id is required`);
  for (const key of ["westDegrees", "southDegrees", "eastDegrees", "northDegrees"]) {
    finite(tile[key], `tiles[${index}].${key}`);
  }
  if (tile.southDegrees >= tile.northDegrees) {
    throw new RangeError(`tiles[${index}] has invalid latitude bounds`);
  }
  return Object.freeze({ lod: 0, priority: 0, ...tile });
}

/**
 * Deterministic geodetic tile lookup. Overlaps select the highest LOD, then
 * priority, then lexical ID. No-data fallback is never inferred here.
 */
export class GeodeticTileIndex {
  constructor(tiles, { supportsAntimeridian = false } = {}) {
    if (!Array.isArray(tiles) || tiles.length === 0) {
      throw new TypeError("tiles must be a non-empty array");
    }
    this.tiles = Object.freeze(tiles.map(validateTile).sort((a, b) =>
      b.lod - a.lod || b.priority - a.priority || a.id.localeCompare(b.id)));
    this.supportsAntimeridian = supportsAntimeridian;
    if (!supportsAntimeridian && this.tiles.some((tile) =>
      tile.westDegrees > tile.eastDegrees)) {
      throw new Error("Antimeridian-crossing tiles require supportsAntimeridian");
    }
  }

  locate(query) {
    const longitude = finite(query?.longitudeDegrees, "longitudeDegrees");
    const latitude = finite(query?.latitudeDegrees, "latitudeDegrees");
    const tile = this.tiles.find((candidate) =>
      latitude >= candidate.southDegrees &&
      latitude <= candidate.northDegrees &&
      containsLongitude(candidate, longitude));
    if (!tile) return null;
    return Object.freeze({
      tileId: tile.id,
      u: longitudeFraction(tile, longitude),
      v: (latitude - tile.southDegrees) /
        (tile.northDegrees - tile.southDegrees),
      lod: tile.lod,
    });
  }
}
