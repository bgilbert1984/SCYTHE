export function getOperatorGeodetic(viewer, Cesium) {
  const position = viewer?.scene?.camera?.positionWC;
  if (!position) throw new Error("Cesium camera positionWC is unavailable");
  const cartographic = Cesium.Ellipsoid.WGS84.cartesianToCartographic(position);
  if (!cartographic) throw new Error("Camera cannot be converted to WGS84");
  return Object.freeze({
    longitudeDegrees: Cesium.Math.toDegrees(cartographic.longitude),
    latitudeDegrees: Cesium.Math.toDegrees(cartographic.latitude),
    heightMeters: cartographic.height,
  });
}

export function geodeticToEcef(Cesium, geodetic) {
  return Cesium.Cartesian3.fromDegrees(
    geodetic.longitudeDegrees,
    geodetic.latitudeDegrees,
    geodetic.heightMeters ?? 0,
  );
}

export function ecefToGeodetic(Cesium, ecef) {
  const value = Cesium.Ellipsoid.WGS84.cartesianToCartographic(ecef);
  if (!value) throw new Error("ECEF position cannot be converted to WGS84");
  return Object.freeze({
    longitudeDegrees: Cesium.Math.toDegrees(value.longitude),
    latitudeDegrees: Cesium.Math.toDegrees(value.latitude),
    heightMeters: value.height,
  });
}

export function ecefToEnu(Cesium, ecef, originGeodetic) {
  const origin = geodeticToEcef(Cesium, originGeodetic);
  const frame = Cesium.Transforms.eastNorthUpToFixedFrame(origin);
  const inverse = Cesium.Matrix4.inverseTransformation(frame, new Cesium.Matrix4());
  return Cesium.Matrix4.multiplyByPoint(inverse, ecef, new Cesium.Cartesian3());
}

export function getOperatorENU(viewer, Cesium, originGeodetic) {
  return ecefToEnu(Cesium, viewer.scene.camera.positionWC, originGeodetic);
}
