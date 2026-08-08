import { bodyFixedCartesian, bodyFixedCartographic, moonEllipsoid } from "./celestialBodies.js";

export async function createLunarViewer({Cesium, container, textureUrl}) {
  if (!Cesium?.Viewer || !container) throw new TypeError("Cesium and a lunar viewer container are required");
  const ellipsoid = moonEllipsoid(Cesium);
  const globe = new Cesium.Globe(ellipsoid);
  globe.terrainProvider = new Cesium.EllipsoidTerrainProvider({ellipsoid});
  const viewer = new Cesium.Viewer(container, {
    animation: false, baseLayer: false, baseLayerPicker: false, fullscreenButton: false,
    geocoder: false, homeButton: false, infoBox: false, navigationHelpButton: false,
    sceneModePicker: false, selectionIndicator: false, timeline: false, globe,
  });
  viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString("#181a1d");
  viewer.scene.globe.enableLighting = true;
  viewer.scene.globe.depthTestAgainstTerrain = false;
  if (textureUrl && Cesium.SingleTileImageryProvider) {
    const options = {url: textureUrl, rectangle: Cesium.Rectangle.MAX_VALUE, ellipsoid,
      credit: "ILLUSTRATIVE BASE TEXTURE // NOT TERRAIN AUTHORITY"};
    const provider = Cesium.SingleTileImageryProvider.fromUrl
      ? await Cesium.SingleTileImageryProvider.fromUrl(textureUrl, options)
      : new Cesium.SingleTileImageryProvider(options);
    viewer.imageryLayers.addImageryProvider(provider);
  }
  viewer.camera.setView({
    destination: bodyFixedCartesian(Cesium, "MOON", 0, -89, 850000, ellipsoid),
    orientation: {heading: 0, pitch: -Math.PI / 2, roll: 0},
  });
  return Object.freeze({viewer, ellipsoid, body: "MOON", referenceFrame: "MOON_ME_DE421"});
}

export class LunarSurfaceLayer {
  constructor({viewer, Cesium, ellipsoid, container, descriptor}) {
    this.viewer = viewer; this.Cesium = Cesium; this.ellipsoid = ellipsoid;
    this.container = container; this.descriptor = descriptor; this.entityIds = [];
    this.handler = null;
  }

  start() {
    this.#grid(); this.#southPole();
    this.handler = new this.Cesium.ScreenSpaceEventHandler(this.viewer.scene.canvas);
    this.handler.setInputAction((movement) => {
      const point = this.viewer.camera.pickEllipsoid(movement.position, this.ellipsoid);
      if (!point) return;
      const location = bodyFixedCartographic(this.Cesium, "MOON", point, this.ellipsoid);
      const detail = Object.freeze({
        kind: "lunar-location", datasetId: this.descriptor.datasetId,
        locationId: `moon:${location.latitudeDegrees.toFixed(4)}:${location.longitudeDegrees.toFixed(4)}`,
        celestialBody: "MOON", referenceFrame: this.descriptor.spatialReference.bodyFixedFrame,
        longitudeDegrees: location.longitudeDegrees, latitudeDegrees: location.latitudeDegrees,
        heightMeters: 0, spatialAuthority: "REFERENCE_ELLIPSOID_ONLY",
      });
      this.container.dispatchEvent(new CustomEvent("scythe-web:lunar-location-selected", {bubbles: true, detail}));
    }, this.Cesium.ScreenSpaceEventType.LEFT_CLICK);
    return this;
  }

  #add(entity) { this.entityIds.push(entity.id); return this.viewer.entities.add(entity); }
  #grid() {
    const gridColor = this.Cesium.Color.fromCssColorString("#4cb8c4").withAlpha(0.34);
    for (const latitude of [-89, -87.5, -85, -80, -75]) {
      const positions = [];
      for (let longitude = -180; longitude <= 180; longitude += 4) {
        positions.push(bodyFixedCartesian(this.Cesium, "MOON", longitude, latitude, 120, this.ellipsoid));
      }
      this.#add({id: `lunar:grid:lat:${latitude}`, polyline: {positions, width: 1, material: gridColor,
        arcType: this.Cesium.ArcType.NONE}, properties: {evidenceClass: "ILLUSTRATIVE"}});
    }
    for (let longitude = -180; longitude < 180; longitude += 30) {
      const positions = [];
      for (let latitude = -89.8; latitude <= -70; latitude += 0.25) {
        positions.push(bodyFixedCartesian(this.Cesium, "MOON", longitude, latitude, 140, this.ellipsoid));
      }
      this.#add({id: `lunar:grid:lon:${longitude}`, polyline: {positions, width: 1, material: gridColor,
        arcType: this.Cesium.ArcType.NONE}, properties: {evidenceClass: "ILLUSTRATIVE"}});
    }
  }

  #southPole() {
    const position = bodyFixedCartesian(this.Cesium, "MOON", 0, -89.999, 500, this.ellipsoid);
    this.#add({id: "lunar:reference:south-pole", position,
      point: {pixelSize: 12, color: this.Cesium.Color.fromCssColorString("#f7d154"),
        outlineColor: this.Cesium.Color.BLACK, outlineWidth: 2},
      label: {text: "LUNAR SOUTH POLE\nREFERENCE LOCATION", font: "11px monospace",
        fillColor: this.Cesium.Color.fromCssColorString("#f7d154"),
        pixelOffset: new this.Cesium.Cartesian2(0, 30), showBackground: true,
        backgroundColor: this.Cesium.Color.BLACK.withAlpha(0.72)},
      properties: {evidenceClass: "REFERENCE_LOCATION", terrainAuthority: "ABSENT_M0"}});
  }

  destroy() {
    this.handler?.destroy(); this.handler = null;
    for (const id of this.entityIds) this.viewer.entities.removeById(id);
    this.entityIds = [];
  }
}
