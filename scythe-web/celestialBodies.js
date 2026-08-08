const BODY_IDS = new Set(["EARTH", "MOON"]);

export const CELESTIAL_BODIES = Object.freeze({
  EARTH: Object.freeze({
    id: "EARTH", label: "Earth", referenceFrame: "ITRF/WGS84",
    longitudeConvention: "EAST_POSITIVE_180", referenceRadiusMeters: 6378137,
  }),
  MOON: Object.freeze({
    id: "MOON", label: "Moon", referenceFrame: "MOON_ME_DE421",
    longitudeConvention: "EAST_POSITIVE_180", referenceRadiusMeters: 1737400,
  }),
});

export function celestialBody(id) {
  if (!BODY_IDS.has(id)) throw new TypeError(`Unsupported celestial body: ${id}`);
  return CELESTIAL_BODIES[id];
}

export function moonEllipsoid(Cesium) {
  if (!Cesium?.Ellipsoid) throw new TypeError("A compatible Cesium namespace is required");
  return Cesium.Ellipsoid.MOON ?? new Cesium.Ellipsoid(1737400, 1737400, 1737400);
}

export function bodyFixedCartesian(Cesium, bodyId, longitudeDegrees, latitudeDegrees,
                                   heightMeters = 0, ellipsoidOverride = null) {
  celestialBody(bodyId);
  for (const [name, value] of Object.entries({longitudeDegrees, latitudeDegrees, heightMeters})) {
    if (!Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  }
  if (longitudeDegrees < -180 || longitudeDegrees > 180 || latitudeDegrees < -90 || latitudeDegrees > 90) {
    throw new RangeError("body-fixed coordinates are out of range");
  }
  const ellipsoid = ellipsoidOverride ?? (bodyId === "MOON" ? moonEllipsoid(Cesium) : Cesium.Ellipsoid.WGS84);
  const cartographic = Cesium.Cartographic.fromDegrees(longitudeDegrees, latitudeDegrees, heightMeters);
  return ellipsoid.cartographicToCartesian(cartographic);
}

export function bodyFixedCartographic(Cesium, bodyId, cartesian, ellipsoidOverride = null) {
  celestialBody(bodyId);
  const ellipsoid = ellipsoidOverride ?? (bodyId === "MOON" ? moonEllipsoid(Cesium) : Cesium.Ellipsoid.WGS84);
  const value = ellipsoid.cartesianToCartographic(cartesian);
  if (!value) throw new Error("Cartesian position is outside the body-fixed conversion domain");
  return Object.freeze({
    celestialBody: bodyId,
    longitudeDegrees: Cesium.Math.toDegrees(value.longitude),
    latitudeDegrees: Cesium.Math.toDegrees(value.latitude),
    heightMeters: value.height,
  });
}
