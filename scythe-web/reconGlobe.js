/**
 * The Auto-Recon drawing as a service (moved out of command-ops-visualization.html as `ReconDrawing`; step 5, the last, of
 * docs/autorecon_extraction_plan.md). The recon tool (reconPlugin.js) reaches the globe only through it: markers for the entities (with an altitude
 * line for aircraft and a threat ring for the dangerous ones), the reference point and the proximity rings, task lines, the camera flights, the icons,
 * the clustered data source and its styling, the MLDC label feed, and the altitude listener that shows and hides labels and rings.
 *
 * What is decided without Cesium is plain functions here (`summarizeCluster`, `clusterColorKey`, `clusterIconSize`, `hasLocation`, `isAircraft`, `RING_CONFIGS`);
 * the rest takes the page's `Cesium`, `document`, the render tool (`HierarchicalRenderManager`) and the MLDC layer from `win` at the time they are
 * needed, and the entities from the store (`store.entities`).
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - A marker is added to the clustered data source if there is one, else to the viewer; the altitude line, the threat ring, the reference point, the
 *    proximity rings and the task lines always go to the viewer.
 *  - `removeMarker` takes the marker from the data source if it is there, else from the viewer; the altitude line and the threat ring from the viewer only.
 *  - Hiding the labels with the checkbox hides the labels of the markers in `entityMarkers` (the ones drawn this pass); the altitude listener changes the labels
 *    of every `recon_entity_` entity in the collection, ANDed with the checkbox.
 *  - The altitude listener's label update ignores the clustered markers' own `show` flags set at creation: it overwrites them.
 *  - Drawing the proximity rings and the markers' labels asks the render tool whether the altitude allows it, and does both if there is no render tool.
 *  - Clusters take the colour of the worst disposition in them (hostile, then suspicious, unknown, neutral, else friend), and a glow when the worst threat is high or critical.
 *  - A marker for an entity without a numeric location is skipped with a warning.
 *
 * Deliberate changes:
 *  - `detach()` (the tool's teardown) takes the markers down, hides the layer and removes the altitude listener (it was never removed); the listener is
 *    bound again when the layer is shown. The old object had no teardown.
 *  - A cluster member whose data cannot be read (no `entityData`, or a value of null) is skipped; the old code threw inside Cesium's cluster event.
 *  - The colour tables are made when first needed, not when the page loads, and a table answers only for its own keys (a disposition called "constructor" is grey).
 */

import {describeEntity, mldcLabel} from "./reconLogic.js";

export const THREAT_ORDER = ["NONE", "MINIMAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"];
const CLUSTER_DISPOSITIONS = ["HOSTILE", "SUSPICIOUS", "UNKNOWN", "NEUTRAL", "FRIEND"];

/** The proximity rings: radius in nautical miles, the Cesium colour, its alpha, and the name. */
export const RING_CONFIGS = [
  {radius: 1.0, color: "RED", alpha: 0.3, name: "CRITICAL (1 NM)"},
  {radius: 3.0, color: "ORANGE", alpha: 0.2, name: "WARNING (3 NM)"},
  {radius: 5.0, color: "YELLOW", alpha: 0.15, name: "ALERT (5 NM)"},
  {radius: 10.0, color: "LIME", alpha: 0.1, name: "AWARENESS (10 NM)"},
];

const NM = 1852;   // metres in a nautical mile

const own = (table, key) => (Object.hasOwn(table, key) ? table[key] : undefined);

/** Whether an entity can be drawn: it has a numeric latitude and longitude. */
export const hasLocation = (entity) => Boolean(entity && entity.location && typeof entity.location.lat === "number" && typeof entity.location.lon === "number");

/** Whether the entity's ontology names an aircraft. */
export const isAircraft = (entity) => Boolean(entity.ontology && entity.ontology.includes("aircraft"));

/** What a cluster holds: how many entities of each of the five dispositions, and the highest threat level among them. `data` is each entity's data, or null. */
export function summarizeCluster(dataList) {
  const counts = {};
  for (const name of CLUSTER_DISPOSITIONS) counts[name] = 0;
  let maxThreat = "NONE";
  for (const data of dataList) {
    if (!data) continue;
    if (data.disposition && own(counts, data.disposition) !== undefined) counts[data.disposition]++;
    if (THREAT_ORDER.indexOf(data.threat_level) > THREAT_ORDER.indexOf(maxThreat)) maxThreat = data.threat_level;   // a missing or unknown level is -1: never higher
  }
  return {counts, maxThreat};
}

/** The disposition whose colour a cluster takes: the worst one present. */
export function clusterColorKey(counts) {
  if (counts.HOSTILE > 0) return "HOSTILE";
  if (counts.SUSPICIOUS > 0) return "SUSPICIOUS";
  if (counts.UNKNOWN > 0) return "UNKNOWN";
  if (counts.NEUTRAL > 0) return "NEUTRAL";
  return "FRIEND";
}

/** The side, in pixels, of a cluster's icon: 36 plus four for each square root of the count, at most 60. */
export const clusterIconSize = (count) => Math.min(60, 36 + Math.sqrt(count) * 4);

export function createReconGlobe({win, store, log = console, now = () => Date.now()}) {
  let viewer = null;
  let dataSource = null;
  let visible = false;
  let showRings = true;
  let showLabels = true;
  let referencePoint = {lat: 37.7749, lon: -122.4194};
  let entityMarkers = {};
  let proximityCircles = {};
  let taskLines = {};
  let altitudeListener = null;
  let colorTables = null;

  const render = () => win.HierarchicalRenderManager;   // the render tool's controls, once the boot module has set them

  /** Disposition colours matching MIL-STD-2525, and the threat colours: made when first needed, from the page's Cesium. */
  function colors() {
    if (colorTables) return colorTables;
    const {Color} = win.Cesium;
    return colorTables = {
      disposition: {
        UNKNOWN: Color.YELLOW, PENDING: Color.YELLOW, ASSUMED_FRIEND: Color.BLUE.withAlpha(0.8), FRIEND: Color.BLUE, NEUTRAL: Color.LIME,
        SUSPICIOUS: Color.ORANGE, HOSTILE: Color.RED, JOKER: Color.MAGENTA, FAKER: Color.PURPLE,
      },
      threat: {CRITICAL: Color.RED, HIGH: Color.ORANGERED, MEDIUM: Color.ORANGE, LOW: Color.YELLOW, MINIMAL: Color.LIME, NONE: Color.CYAN},
    };
  }
  const dispositionColor = (disposition) => own(colors().disposition, disposition) || win.Cesium.Color.GRAY;

  /** True if the render tool is there and says the altitude allows `what`, or if there is no render tool. */
  const detailAllowed = (what) => { const r = render(); return r === undefined || r.shouldShowDetail(what); };

  function createTacticalClusterIcon(count, color, dispositions, threatLevel) {
    const size = clusterIconSize(count);
    const canvas = win.document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d");

    // Threat glow effect for high/critical threats
    if (threatLevel === "HIGH" || threatLevel === "CRITICAL") {
      ctx.beginPath();
      ctx.arc(size / 2, size / 2, size / 2, 0, 2 * Math.PI);
      const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
      gradient.addColorStop(0, threatLevel === "CRITICAL" ? "rgba(255,0,0,0.6)" : "rgba(255,100,0,0.4)");
      gradient.addColorStop(1, "rgba(255,0,0,0)");
      ctx.fillStyle = gradient;
      ctx.fill();
    }

    // Main circle
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2 - 4, 0, 2 * Math.PI);
    ctx.fillStyle = color.withAlpha(0.85).toCssColorString();
    ctx.fill();

    // Border
    ctx.strokeStyle = "white";
    ctx.lineWidth = 2;
    ctx.stroke();

    // Tactical diamond shape in center
    ctx.beginPath();
    const dSize = size / 4;
    ctx.moveTo(size / 2, size / 2 - dSize);
    ctx.lineTo(size / 2 + dSize, size / 2);
    ctx.lineTo(size / 2, size / 2 + dSize);
    ctx.lineTo(size / 2 - dSize, size / 2);
    ctx.closePath();
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.fill();

    return canvas.toDataURL();
  }

  /** Styles a cluster of recon entities: a count, the worst disposition's colour, and a tactical icon. */
  function styleCluster(clusteredEntities, cluster) {
    const {Cesium} = win;
    const {counts, maxThreat} = summarizeCluster(clusteredEntities.map((entity) => {
      const props = entity.properties;
      return props && props.entityData ? props.entityData.getValue() : null;
    }));
    const clusterColor = colors().disposition[clusterColorKey(counts)];

    cluster.label.text = clusteredEntities.length.toString();
    cluster.label.font = "bold 14px sans-serif";
    cluster.label.fillColor = Cesium.Color.WHITE;
    cluster.label.outlineColor = Cesium.Color.BLACK;
    cluster.label.outlineWidth = 2;
    cluster.label.style = Cesium.LabelStyle.FILL_AND_OUTLINE;
    cluster.label.verticalOrigin = Cesium.VerticalOrigin.CENTER;
    cluster.label.horizontalOrigin = Cesium.HorizontalOrigin.CENTER;

    cluster.billboard.image = createTacticalClusterIcon(clusteredEntities.length, clusterColor, counts, maxThreat);
    cluster.billboard.width = 44;
    cluster.billboard.height = 44;
  }

  function updateLabelVisibilityForAltitude(show) {
    const entityCollection = dataSource ? dataSource.entities : viewer.entities;
    entityCollection.values.forEach((entity) => {
      if (entity.id && entity.id.startsWith("recon_entity_") && entity.label) entity.label.show = show && showLabels;
    });
  }

  function updateProximityRingsForAltitude(show) {
    Object.values(proximityCircles).forEach((ring) => {
      if (ring && ring.ellipse) ring.show = show && showRings;
    });
  }

  function bindAltitudeListener() {
    if (altitudeListener) return;
    altitudeListener = () => {
      if (!visible) return;
      const shouldShowLabels = render().shouldShowDetail("labels");
      const shouldShowRings = render().shouldShowDetail("rings");
      updateLabelVisibilityForAltitude(shouldShowLabels);
      updateProximityRingsForAltitude(shouldShowRings);
    };
    win.document.addEventListener("altitudeChanged", altitudeListener);
  }

  function unbindAltitudeListener() {
    win.document.removeEventListener("altitudeChanged", altitudeListener);
    altitudeListener = null;
  }

  function createOwnShipIcon() {
    const canvas = win.document.createElement("canvas");
    canvas.width = 40;
    canvas.height = 40;
    const ctx = canvas.getContext("2d");

    ctx.clearRect(0, 0, 40, 40);
    ctx.save();
    ctx.translate(20, 20);

    // Draw diamond shape for own position
    ctx.beginPath();
    ctx.moveTo(0, -15);
    ctx.lineTo(12, 0);
    ctx.lineTo(0, 15);
    ctx.lineTo(-12, 0);
    ctx.closePath();

    ctx.fillStyle = "#00FFFF";
    ctx.fill();
    ctx.strokeStyle = "#FFFFFF";
    ctx.lineWidth = 2;
    ctx.stroke();

    // Center dot
    ctx.beginPath();
    ctx.arc(0, 0, 3, 0, Math.PI * 2);
    ctx.fillStyle = "#FFFFFF";
    ctx.fill();

    ctx.restore();
    return canvas.toDataURL();
  }

  function createEntityIcon(color, disposition, aircraft) {
    const canvas = win.document.createElement("canvas");
    canvas.width = 32;
    canvas.height = 32;
    const ctx = canvas.getContext("2d");

    ctx.clearRect(0, 0, 32, 32);
    ctx.save();
    ctx.translate(16, 16);

    if (aircraft) {
      // Aircraft symbol (triangle)
      ctx.beginPath();
      ctx.moveTo(0, -12);
      ctx.lineTo(10, 10);
      ctx.lineTo(0, 6);
      ctx.lineTo(-10, 10);
      ctx.closePath();
    } else {
      // Surface vessel/ground symbol (semicircle with line)
      ctx.beginPath();
      ctx.arc(0, 2, 10, Math.PI, 0);
      ctx.lineTo(10, 10);
      ctx.lineTo(-10, 10);
      ctx.closePath();
    }

    ctx.fillStyle = color.toCssColorString();
    ctx.fill();
    ctx.strokeStyle = "#FFFFFF";
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Add disposition indicator
    if (disposition === "HOSTILE") {
      ctx.beginPath();
      ctx.moveTo(-8, -8);
      ctx.lineTo(8, 8);
      ctx.moveTo(8, -8);
      ctx.lineTo(-8, 8);
      ctx.strokeStyle = "#FF0000";
      ctx.lineWidth = 2;
      ctx.stroke();
    } else if (disposition === "FRIEND" || disposition === "ASSUMED_FRIEND") {
      ctx.beginPath();
      ctx.arc(0, 0, 4, 0, Math.PI * 2);
      ctx.fillStyle = "#00FF00";
      ctx.fill();
    }

    ctx.restore();
    return canvas.toDataURL();
  }

  function addReferencePointMarker() {
    const {Cesium} = win;
    try {
      const refEntity = viewer.entities.add({
        id: "recon_reference_point",
        name: "Reference Point (Own Position)",
        position: Cesium.Cartesian3.fromDegrees(referencePoint.lon, referencePoint.lat, 0),
        billboard: {
          image: createOwnShipIcon(),
          scale: 0.8,
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
        },
        label: showLabels ? {
          text: "OWN POSITION",
          font: "bold 12px sans-serif",
          fillColor: Cesium.Color.CYAN,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.TOP,
          pixelOffset: new Cesium.Cartesian2(0, 20),
        } : undefined,
      });
      entityMarkers["reference"] = refEntity;
    } catch (error) {
      log.error?.("Error adding reference point:", error);
    }
  }

  function addProximityRings() {
    const {Cesium} = win;
    RING_CONFIGS.forEach((config, idx) => {
      try {
        const color = Cesium.Color[config.color].withAlpha(config.alpha);
        const ring = viewer.entities.add({
          id: `recon_proximity_ring_${idx}`,
          name: config.name,
          position: Cesium.Cartesian3.fromDegrees(referencePoint.lon, referencePoint.lat),
          ellipse: {
            semiMajorAxis: config.radius * NM,
            semiMinorAxis: config.radius * NM,
            material: color,
            outline: true,
            outlineColor: color.withAlpha(0.8),
            outlineWidth: 2,
            height: 0,
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          },
        });
        proximityCircles[idx] = ring;
      } catch (error) {
        log.error?.(`Error adding proximity ring ${idx}:`, error);
      }
    });
  }

  function addThreatIndicator(entity) {
    const {Cesium} = win;
    const entityId = entity.entity_id;
    const threatColor = own(colors().threat, entity.threat_level);
    try {
      // Pulsing circle around threat
      const threatCircle = viewer.entities.add({
        id: `recon_threat_${entityId}`,
        position: Cesium.Cartesian3.fromDegrees(entity.location.lon, entity.location.lat),
        ellipse: {
          semiMajorAxis: 500,
          semiMinorAxis: 500,
          material: threatColor.withAlpha(0.3),
          outline: true,
          outlineColor: threatColor,
          outlineWidth: 2,
          height: 0,
        },
      });
      entityMarkers[`${entityId}_threat`] = threatCircle;
    } catch (error) {
      log.error?.(`Error adding threat indicator for ${entityId}:`, error);
    }
  }

  function addMarker(entity) {
    if (!viewer) return;
    const {Cesium} = win;
    if (!hasLocation(entity)) {
      log.warn?.("[AutoRecon] Skipping marker for entity without valid location:", entity?.entity_id);
      return;
    }
    const entityId = entity.entity_id;
    const lat = entity.location.lat;
    const lon = entity.location.lon;
    const altitude = entity.location.altitude_m || 0;
    const disposition = entity.disposition || "UNKNOWN";
    const threatLevel = entity.threat_level;
    const aircraft = isAircraft(entity);
    const color = dispositionColor(disposition);

    // Use clustered data source if available
    const entityCollection = dataSource ? dataSource.entities : viewer.entities;

    // Determine if labels should be shown based on altitude
    const showLabelsNow = showLabels && detailAllowed("labels");

    try {
      // Check if entity already exists in collection
      const duplicate = entityCollection.getById(`recon_entity_${entityId}`);
      if (duplicate) entityCollection.remove(duplicate);

      const marker = entityCollection.add({
        id: `recon_entity_${entityId}`,
        name: `${entity.name} (${entityId})`,
        position: Cesium.Cartesian3.fromDegrees(lon, lat, altitude),
        billboard: {
          image: createEntityIcon(color, disposition, aircraft),
          scale: 0.6,
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
          rotation: entity.velocity ? Cesium.Math.toRadians(-entity.velocity.heading_deg) : 0,
        },
        label: {
          text: `${entity.name}\n${disposition}`,
          font: "10px sans-serif",
          fillColor: color,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.TOP,
          pixelOffset: new Cesium.Cartesian2(0, 18),
          show: showLabelsNow,
        },
        description: describeEntity(entity),
        properties: {type: "recon_entity", entityId, entityData: entity},
      });
      entityMarkers[entityId] = marker;

      // Push to the MLDC Deck.gl TextLayer (multi-channel signal)
      if (win.__MLDC__) {
        win._mldcLabelMap = win._mldcLabelMap || {};
        win._mldcLabelMap[entityId] = mldcLabel(entity, now());
        win.__MLDC__.setLabelData(Object.values(win._mldcLabelMap));
      }

      // Add altitude line for aircraft (only if zoomed in)
      const showAltLines = detailAllowed("altitudeLines");
      if (aircraft && altitude > 100 && showAltLines) {
        const altLine = viewer.entities.add({
          id: `recon_alt_line_${entityId}`,
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArrayHeights([lon, lat, 0, lon, lat, altitude]),
            width: 1,
            material: new Cesium.PolylineDashMaterialProperty({color: color.withAlpha(0.5)}),
          },
        });
        entityMarkers[`${entityId}_alt`] = altLine;
      }

      // Add threat indicator for hostile/suspicious
      if (threatLevel === "CRITICAL" || threatLevel === "HIGH") addThreatIndicator(entity);
    } catch (error) {
      log.error?.(`Error adding entity ${entityId}:`, error);
    }
  }

  /** Takes one entity's markers (billboard, altitude line, threat ring) down, wherever they were added. */
  function removeMarker(entityId) {
    const marker = entityMarkers[entityId];
    if (marker) {
      // Try to remove from clustered data source first
      if (dataSource && dataSource.entities.contains(marker)) dataSource.entities.remove(marker);
      // Also try viewer entities
      else if (viewer && viewer.entities.contains(marker)) viewer.entities.remove(marker);
      delete entityMarkers[entityId];
    }
    // Also remove associated markers (altitude line, threat indicator)
    const altMarker = entityMarkers[`${entityId}_alt`];
    if (altMarker && viewer && viewer.entities.contains(altMarker)) {
      viewer.entities.remove(altMarker);
      delete entityMarkers[`${entityId}_alt`];
    }
    const threatMarker = entityMarkers[`${entityId}_threat`];
    if (threatMarker && viewer && viewer.entities.contains(threatMarker)) {
      viewer.entities.remove(threatMarker);
      delete entityMarkers[`${entityId}_threat`];
    }
  }

  function clear() {
    // Clear from clustered data source if available
    if (dataSource) dataSource.entities.removeAll();

    // Remove entity markers from viewer
    Object.values(entityMarkers).forEach((entity) => {
      if (viewer && viewer.entities.contains(entity)) viewer.entities.remove(entity);
    });
    entityMarkers = {};

    // Clear MLDC TextLayer labels + ghost memory when all entities cleared
    if (win.__MLDC__) {
      win._mldcLabelMap = {};
      win.__MLDC__.clearLabels();
      win.__MLDC__.clearGhosts();
    }

    // Remove proximity circles
    Object.values(proximityCircles).forEach((entity) => {
      if (viewer && viewer.entities.contains(entity)) viewer.entities.remove(entity);
    });
    proximityCircles = {};

    // Remove task lines
    Object.values(taskLines).forEach((entity) => {
      if (viewer && viewer.entities.contains(entity)) viewer.entities.remove(entity);
    });
    taskLines = {};
  }

  function updatePosition(entity) {
    const marker = entityMarkers[entity.entity_id];
    if (!marker) return;
    const {Cesium} = win;
    marker.position = Cesium.Cartesian3.fromDegrees(entity.location.lon, entity.location.lat, entity.location.altitude_m || 0);
    if (marker.billboard && entity.velocity) marker.billboard.rotation = Cesium.Math.toRadians(-entity.velocity.heading_deg);
    // Update label
    if (marker.label) {
      marker.label.text = `${entity.name}\n${entity.disposition}`;
      marker.label.fillColor = dispositionColor(entity.disposition);
    }
  }

  /** Gives the camera back to the user: no transform, no tracked entity, every control enabled. */
  function releaseCamera() {
    const {Cesium} = win;
    viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
    viewer.trackedEntity = undefined;
    const controller = viewer.scene.screenSpaceCameraController;
    controller.enableRotate = controller.enableTilt = controller.enableZoom = controller.enableTranslate = controller.enableLook = true;
  }

  function flyTo(entityId) {
    const entity = store.entities[entityId];
    if (!entity || !viewer) return;
    const {Cesium} = win;

    // Always clear any prior tracking or custom transforms before flying. Without this, camera operations such as
    // rotate/tilt can remain locked after a programmatic flight (e.g. from an nmap scan).
    try {
      releaseCamera();
    } catch (error) {
      log.warn?.("[Recon] Failed to reset camera before flyTo:", error);
    }

    // The destination is the marker's position if it has one, else the entity's stored coordinates. The camera API is used directly:
    // flyTo(marker) can silently fail when the entity is clustered or otherwise unavailable.
    let destination;
    const marker = entityMarkers[entityId];
    if (marker && marker.position) destination = marker.position.getValue ? marker.position.getValue(new Date()) : marker.position;
    if (!destination) destination = Cesium.Cartesian3.fromDegrees(entity.location.lon, entity.location.lat, entity.location.altitude_m || 0);

    // A gentle tilt so the user can see context; when finished, the transform is reset and the controls are re-enabled.
    viewer.camera.flyTo({
      destination,
      orientation: {heading: viewer.camera.heading, pitch: Cesium.Math.toRadians(-45), roll: 0},
      duration: 1.5,
      complete() {
        try {
          releaseCamera();
        } catch (error) {
          log.warn?.("[Recon] Failed to reset camera after flyTo:", error);
        }
      },
    });
  }

  function addTaskLine(task) {
    if (!viewer) return;
    const entity = store.entities[task.entity_id];
    if (!entity) return;
    const {Cesium} = win;
    try {
      const taskLine = viewer.entities.add({
        id: `recon_task_${task.task_id}`,
        name: `Task: ${task.task_id}`,
        polyline: {
          positions: Cesium.Cartesian3.fromDegreesArray([referencePoint.lon, referencePoint.lat, entity.location.lon, entity.location.lat]),
          width: 2,
          material: new Cesium.PolylineDashMaterialProperty({color: Cesium.Color.YELLOW.withAlpha(0.7), dashLength: 16.0}),
          clampToGround: true,
        },
      });
      taskLines[task.task_id] = taskLine;
    } catch (error) {
      log.error?.("Error adding task line:", error);
    }
  }

  return {
    /** Takes the viewer from the recon tool and makes the clustered data source (once). */
    attach(cesiumViewer) {
      viewer = cesiumViewer;
      log.log?.("Auto-Recon drawing attached");
      const r = render();
      if (!dataSource && r !== undefined && r.viewer) {
        dataSource = r.createClusteredDataSource("Recon_Entities", {pixelRange: 45, minimumClusterSize: 2});
        dataSource.clustering.clusterEvent.addEventListener(styleCluster);
      }
      bindAltitudeListener();
    },
    /** Takes the markers down, hides the layer, and stops listening for altitude changes (the tool's teardown). The viewer and the data source are kept. */
    detach() {
      clear();
      visible = false;
      unbindAltitudeListener();
    },
    available: () => Boolean(viewer),
    /** Draws the layer; returns whether it did (the tool then refreshes the panel's list). */
    render() {
      if (!viewer || !visible) return false;
      clear();
      addReferencePointMarker();   // always to the viewer, not clustered
      if (showRings && detailAllowed("rings")) addProximityRings();
      Object.values(store.entities).forEach(addMarker);   // to the clustered data source if there is one
      return true;
    },
    addMarker, removeMarker, updatePosition, clear, flyTo, addTaskLine, createEntityIcon,
    setVisible(shown) {
      visible = shown;
      if (shown && viewer) bindAltitudeListener();
    },
    setShowRings(shown) {
      showRings = shown;
      Object.values(proximityCircles).forEach((entity) => { entity.show = showRings; });
    },
    setShowLabels(shown) {
      showLabels = shown;
      Object.values(entityMarkers).forEach((entity) => { if (entity.label) entity.label.show = showLabels; });
    },
    setReferencePoint(point) { referencePoint = point; },
    /** Where the camera is, in degrees. */
    cameraPosition() {
      const {Cesium} = win;
      const cartographic = Cesium.Cartographic.fromCartesian(viewer.camera.position);
      return {lat: Cesium.Math.toDegrees(cartographic.latitude), lon: Cesium.Math.toDegrees(cartographic.longitude)};
    },
  };
}
