import { evidenceStyle, flowDirectionStyle, flowMotion, flowTypeStyle, graphPurposeStyle, hostLivenessStyle } from "./evidenceStyles.js";
import { graphEntityTooltip } from "./graphEntityTooltip.js";
import {GRAPH_VISUAL_SCALE_BOUNDARY, graphFlowScale, graphNodeScale} from "./graphVisualScale.js";
import {projectCityContext} from "./cityContextProjection.js";
import {separateNewSpatialPoint} from "./topologyGeometry.js";

function hash(value) {
  let result = 2166136261;
  for (const char of String(value)) { result ^= char.charCodeAt(0); result = Math.imul(result, 16777619); }
  return result >>> 0;
}

function graphKind(node) {
  const kind = String(node.kind ?? "").toLowerCase();
  return kind === "event" || kind.includes("burst") ? "event" : "graph-node";
}

function safeEvidence(value) {
  try { return {name: value ?? "INFERRED", style: evidenceStyle(value ?? "INFERRED")}; }
  catch { return {name: "INFERRED", style: evidenceStyle("INFERRED")}; }
}

export function seededTopologyPosition(id, radius = 100) {
  const a = hash(id); const b = hash(`${id}:z`);
  const longitude = ((a % 3600) / 3600) * Math.PI * 2;
  const vertical = ((b % 2001) / 1000) - 1;
  const planar = Math.sqrt(Math.max(0, 1 - vertical * vertical));
  const shell = radius * (0.62 + ((a >>> 12) % 39) / 100);
  return {x: Math.cos(longitude) * planar * shell, y: vertical * shell,
    z: Math.sin(longitude) * planar * shell};
}

/**
 * Preserve every retained node exactly. New nodes enter near known neighbors;
 * otherwise they receive a deterministic topology-space seed. No coordinate is
 * geographic and no graph metadata is promoted to spatial authority.
 */
export function stableTopologyLayout(previous, nodes, edges, radius = 100) {
  const positions = new Map(); const ids = new Set(nodes.map((node) => node.id));
  for (const [id, point] of previous ?? []) if (ids.has(id)) positions.set(id, {...point});
  const neighbors = new Map();
  for (const edge of edges) for (const id of edge.nodes ?? []) {
    if (!ids.has(id)) continue;
    if (!neighbors.has(id)) neighbors.set(id, new Set());
    for (const other of edge.nodes ?? []) if (other !== id && ids.has(other)) neighbors.get(id).add(other);
  }
  for (const node of nodes) {
    if (positions.has(node.id)) continue;
    const known = [...(neighbors.get(node.id) ?? [])].map((id) => positions.get(id)).filter(Boolean);
    const seed = seededTopologyPosition(node.id, radius);
    if (known.length) {
      const center = known.reduce((sum, point) => ({x: sum.x + point.x, y: sum.y + point.y,
        z: sum.z + point.z}), {x: 0, y: 0, z: 0});
      const candidate = {x: center.x / known.length + seed.x * 0.18,
        y: center.y / known.length + seed.y * 0.18, z: center.z / known.length + seed.z * 0.18};
      positions.set(node.id, separateNewSpatialPoint(node.id, candidate, positions, 48));
    } else positions.set(node.id, separateNewSpatialPoint(node.id, seed, positions, 48));
  }
  return positions;
}

export class LiveHypergraph3DView {
  constructor({root, controller, THREE, OrbitControls = null, reducedMotion = null}) {
    if (!root || !controller || !THREE) throw new TypeError("3D view requires root, controller, and THREE");
    this.root = root; this.controller = controller; this.THREE = THREE; this.OrbitControls = OrbitControls;
    this.container = root.querySelector("[data-live-graph-3d]");
    if (!this.container) throw new TypeError("3D view container is required");
    this.document = root.ownerDocument ?? globalThis.document;
    this.window = this.document?.defaultView ?? globalThis;
    this.reducedMotion = reducedMotion ?? Boolean(this.window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
    this.positions = new Map(); this.nodeMeshes = new Map(); this.edgeObjects = new Map();
    this.pickTargets = []; this.graphRevision = null; this.unsubscribe = null;
    this.visible = !this.container.hidden; this.selected = null; this.started = false; this.lastFrameAt = null;
  }

  async start() {
    if (this.started) return this;
    this.started = true; this.#initializeScene();
    this.unsubscribe = this.controller.subscribe((update) => {
      if (update.available && update.graph && (update.changed || update.graph.graphRevision !== this.graphRevision)) {
        this.render(update.graph);
      }
    });
    await this.controller.start();
    return this;
  }

  #initializeScene() {
    const T = this.THREE; const width = Math.max(this.container.clientWidth || 420, 240);
    const height = Math.max(this.container.clientHeight || 220, 160);
    this.scene = new T.Scene(); this.scene.background = new T.Color(0x030914);
    // The bounded production graph can span hundreds of topology units. Keep
    // atmospheric depth subtle enough that a fitted bounded scene is visible.
    this.scene.fog = new T.FogExp2(0x030914, 0.00045);
    this.camera = new T.PerspectiveCamera(56, width / height, 0.1, 3000);
    this.camera.position.set(170, 125, 190);
    this.renderer = new T.WebGLRenderer({antialias: true, alpha: false, powerPreference: "high-performance"});
    this.renderer.setPixelRatio(Math.min(this.window.devicePixelRatio || 1, 2));
    this.renderer.setSize(width, height, false);
    this.renderer.domElement.setAttribute("role", "img");
    this.renderer.domElement.setAttribute("aria-label", "Revision-pinned live Three.js network hypergraph; topology, not geolocation");
    this.container.replaceChildren(this.renderer.domElement);
    this.nodeGroup = new T.Group(); this.edgeGroup = new T.Group();
    this.scene.add(this.edgeGroup, this.nodeGroup, new T.AmbientLight(0x91b8ff, 0.72));
    const light = new T.DirectionalLight(0xffffff, 1.15); light.position.set(120, 180, 140); this.scene.add(light);
    this.controls = this.OrbitControls ? new this.OrbitControls(this.camera, this.renderer.domElement) : null;
    if (this.controls) {
      this.controls.enableDamping = !this.reducedMotion; this.controls.dampingFactor = 0.1;
      this.controls.autoRotate = false; this.controls.minDistance = 20; this.controls.maxDistance = 1600;
    }
    this.raycaster = new T.Raycaster(); this.raycaster.params.Line.threshold = 4;
    this.pointer = new T.Vector2();
    this.tooltip = this.document.createElement("div"); this.tooltip.className = "live-hypergraph__tooltip";
    this.tooltip.hidden = true; this.container.appendChild(this.tooltip);
    this.stats = this.document.createElement("div"); this.stats.className = "live-hypergraph__three-stats";
    this.container.appendChild(this.stats);
    this.onPointerMove = (event) => this.#point(event, false);
    this.onClick = (event) => this.#point(event, true);
    this.renderer.domElement.addEventListener("pointermove", this.onPointerMove);
    this.renderer.domElement.addEventListener("click", this.onClick);
    this.resizeObserver = this.window.ResizeObserver ? new this.window.ResizeObserver(() => this.resize()) : null;
    this.resizeObserver?.observe(this.container);
    this.onVisibility = () => this.#setAnimation(!this.document.hidden && this.visible);
    this.document.addEventListener?.("visibilitychange", this.onVisibility);
    this.clock = new T.Clock(); this.#setAnimation(this.visible);
  }

  render(graph) {
    const displayGraph = projectCityContext(graph);
    const nodes = (displayGraph.nodes ?? []).slice(0, 500 + displayGraph.cityContext.nodeCount);
    const edges = (displayGraph.edges ?? []).slice(0, 1000 + displayGraph.cityContext.edgeCount);
    this.positions = stableTopologyLayout(this.positions, nodes, edges, Math.max(70, Math.sqrt(nodes.length || 1) * 22));
    const nodeIds = new Set(nodes.map((node) => node.id));
    for (const [id, mesh] of this.nodeMeshes) if (!nodeIds.has(id)) {
      this.nodeGroup.remove(mesh); this.#disposeObject(mesh); this.nodeMeshes.delete(id);
    }
    for (const node of nodes) this.#upsertNode(node, graph.graphRevision);
    for (const object of this.edgeObjects.values()) { this.edgeGroup.remove(object); this.#disposeObject(object); }
    this.edgeObjects.clear(); this.pickTargets = [...this.nodeMeshes.values()];
    for (const edge of edges) this.#addEdge(edge, graph.graphRevision);
    const hyperedges = edges.filter((edge) => (edge.nodes ?? []).filter((id) => nodeIds.has(id)).length > 2).length;
    this.stats.textContent = `${nodes.length} NODES · ${edges.length - hyperedges} EDGES · ${hyperedges} HYPEREDGES · ${displayGraph.cityContext.nodeCount} INFERRED CITIES · THREE r${this.THREE.REVISION}`;
    this.graphRevision = graph.graphRevision;
    if (!this.hasFramed && nodes.length) { this.#frame(); this.hasFramed = true; }
  }

  #nodeGeometry(evidenceClass, radius) {
    const T = this.THREE;
    if (evidenceClass === "MEASURED") return new T.OctahedronGeometry(radius, 1);
    if (evidenceClass === "SYNTHETIC") return new T.TetrahedronGeometry(radius, 1);
    if (["INFERRED", "COUNTERFACTUAL", "ILLUSTRATIVE"].includes(evidenceClass)) return new T.IcosahedronGeometry(radius, 1);
    return new T.SphereGeometry(radius, 16, 12);
  }

  #upsertNode(node, revision) {
    const T = this.THREE; const evidence = safeEvidence(node.evidenceClass);
    const visual = graphPurposeStyle(node);
    const scale = graphNodeScale(node);
    let mesh = this.nodeMeshes.get(node.id);
    if (!mesh) {
      const radius = scale.threeRadius;
      const material = new T.MeshStandardMaterial({color: visual.color,
        emissive: new T.Color(visual.color).multiplyScalar(0.28), metalness: 0.25,
        roughness: 0.52, transparent: visual.alpha < 1, opacity: visual.alpha});
      mesh = new T.Mesh(this.#nodeGeometry(evidence.name, radius), material);
      mesh.userData.visualRadius = radius;
      mesh.userData.arrivedAt = this.window.performance?.now?.() ?? Date.now();
      this.nodeMeshes.set(node.id, mesh); this.nodeGroup.add(mesh);
    } else {
      if (Math.abs(Number(mesh.userData.visualRadius) - scale.threeRadius) > .01) {
        mesh.geometry?.dispose?.(); mesh.geometry = this.#nodeGeometry(evidence.name, scale.threeRadius);
        mesh.userData.visualRadius = scale.threeRadius;
      }
      mesh.material.color.set(visual.color); mesh.material.emissive.set(visual.color).multiplyScalar(0.28);
      mesh.material.opacity = visual.alpha; mesh.material.transparent = visual.alpha < 1;
    }
    let badge = mesh.getObjectByName?.("scythe-liveness-badge");
    const liveness = hostLivenessStyle(node);
    if (liveness && !badge) {
      badge = new T.Mesh(new T.SphereGeometry(1.35, 10, 8),
        new T.MeshBasicMaterial({color: liveness.color}));
      badge.name = "scythe-liveness-badge"; badge.position.set(0, scale.threeRadius + 3.4, 0); mesh.add(badge);
    } else if (liveness && badge) {
      badge.material.color.set(liveness.color); badge.position.set(0, scale.threeRadius + 3.4, 0); badge.visible = true;
    } else if (badge) badge.visible = false;
    const point = this.positions.get(node.id); mesh.position.set(point.x, point.y, point.z);
    mesh.userData = {...mesh.userData, selection: node.display?.selectionDisabled ? null : {kind: graphKind(node), entityId: node.id,
      entityType: node.kind, graphRevision: revision,
      ...(node.position ? {position: node.position} : {}), observedAt: node.observedAt ?? null},
      label: `${graphEntityTooltip(node)}\n\nVISUAL SCALE // ${scale.basis.replaceAll("_", " ")} · NODE RADIUS ${scale.threeRadius.toFixed(2)} UNITS\nBOUNDARY // SIZE IS PRESENTATION METADATA; IT DOES NOT CHANGE EVIDENCE AUTHORITY`,
      evidenceClass: evidence.name};
  }

  #addEdge(edge, revision) {
    const T = this.THREE; const members = (edge.nodes ?? []).filter((id) => this.positions.has(id));
    if (members.length < 2) return;
    const evidence = safeEvidence(edge.evidenceClass); const visual = flowTypeStyle(edge);
    const directionStyle = flowDirectionStyle(edge); const motion = flowMotion(edge);
    const scale = graphFlowScale(edge);
    const flowLabel = visual.type ? `${visual.label} · ${String(visual.basis).replaceAll("_", " ")}` : evidence.name;
    const selection = edge.display?.selectionDisabled ? null : {kind: "graph-edge", entityId: edge.id,
      entityType: edge.kind,
      graphRevision: revision, observedAt: edge.observedAt ?? edge.timestamp ?? null};
    const points = members.map((id) => this.positions.get(id));
    let object;
    if (members.length === 2) {
      const sourceCenter = new T.Vector3(points[0].x, points[0].y, points[0].z);
      const targetCenter = new T.Vector3(points[1].x, points[1].y, points[1].z);
      const centerVector = new T.Vector3().subVectors(targetCenter, sourceCenter);
      const centerUnit = centerVector.clone().normalize();
      const sourceRadius = this.nodeMeshes.get(members[0])?.userData?.visualRadius ?? 3.5;
      const targetRadius = this.nodeMeshes.get(members[1])?.userData?.visualRadius ?? 3.5;
      const start = sourceCenter.clone().add(centerUnit.clone().multiplyScalar(sourceRadius + 1.5));
      const end = targetCenter.clone().add(centerUnit.clone().multiplyScalar(-(targetRadius + 1.5)));
      const geometry = new T.BufferGeometry().setFromPoints([start,end]);
      const dashed = evidence.style.line !== "solid";
      const material = dashed ? new T.LineDashedMaterial({color: visual.color, transparent: true,
        opacity: evidence.style.alpha, linewidth: scale.topologyWidth,
        dashSize: evidence.style.line === "dotted" ? 1.5 : 4, gapSize: 2.5}) :
        new T.LineBasicMaterial({color: visual.color, transparent: true, opacity: visual.alpha,
          linewidth: scale.topologyWidth});
      object = new T.Line(geometry, material); if (dashed) object.computeLineDistances();
      object.userData = {selection, label: edge.kind === "geoip_city_membership" ?
        `CITY MEMBERSHIP // INFERRED\n${edge.id}\nDISPLAY-DERIVED FROM HOST GEOIP ESTIMATE\nNOT A PHYSICAL LINK OR GRAPHOPS EXECUTION TARGET` :
        `${edge.kind ?? "edge"}\n${edge.id}\n${flowLabel}\nTUPLE // SOURCE → DESTINATION · ${String(directionStyle.tupleBasis).replaceAll("_", " ")}\nOPERATIONAL // ${directionStyle.label} · ${String(directionStyle.basis).replaceAll("_", " ")}\nMOTION // ${motion.measured ? `${motion.forwardPackets} FORWARD · ${motion.reversePackets} REVERSE PACKETS / ${motion.intervalMilliseconds} ms` : "STATIC · INSUFFICIENT TEMPORAL COUNTER DELTAS"}\nVISUAL SCALE // ${scale.basis.replaceAll("_", " ")} · WIDTH ${scale.topologyWidth.toFixed(2)} · ARROW ×${scale.threeArrowScale.toFixed(2)}\n${evidence.name}\nBOUNDARY // ${GRAPH_VISUAL_SCALE_BOUNDARY}`};
      const vector = new T.Vector3().subVectors(end, start); const unit = vector.clone().normalize();
      // WebGL commonly ignores LineBasicMaterial.linewidth. A restrained
      // translucent tube makes bounded counter magnitude visible while the
      // original line continues to carry evidence pattern semantics.
      const tube = new T.Mesh(new T.CylinderGeometry(.11 + scale.intensity * .42,
        .11 + scale.intensity * .42, vector.length(), 6, 1, true),
      new T.MeshBasicMaterial({color: visual.color, transparent: true,
        opacity: visual.alpha * .28, depthWrite: false}));
      tube.name = "scythe-flow-magnitude"; tube.position.copy(start).lerp(end, .5);
      tube.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), unit); object.add(tube);
      const directional = edge.display?.directional !== false;
      const arrowLength = 5.5 * scale.threeArrowScale;
      const arrowClear = vector.length() >= arrowLength + 16;
      if (directional && arrowClear) {
        const arrow = new T.Mesh(new T.ConeGeometry(1.8 * scale.threeArrowScale,
          arrowLength, 8), new T.MeshBasicMaterial({color: directionStyle.color}));
        arrow.name = "scythe-flow-direction"; arrow.position.copy(start).lerp(end, .5);
        arrow.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), unit); object.add(arrow);
      }
      if (directional && motion.measured && !this.reducedMotion) {
        const addParticle = (reverse, count) => {
          if (!(count > 0)) return;
          const particle = new T.Mesh(new T.SphereGeometry(Math.min(2.2, 1 + Math.log2(count + 1) * .2), 8, 6),
            new T.MeshBasicMaterial({color: reverse ? 0xff6fb7 : 0xffffff}));
          particle.name = reverse ? "scythe-flow-particle-reverse" : "scythe-flow-particle-forward";
          particle.userData.motion = {start: reverse ? end.clone() : start.clone(),
            end: reverse ? start.clone() : end.clone(), duration: motion.durationSeconds * 1000,
            phase: (hash(`${edge.id}:${reverse}`) % 1000) / 1000};
          object.add(particle);
        };
        addParticle(false, motion.forwardPackets); addParticle(true, motion.reversePackets);
      }
      this.edgeGroup.add(object); this.pickTargets.push(object);
    } else {
      const center = points.reduce((sum, point) => ({x: sum.x + point.x, y: sum.y + point.y,
        z: sum.z + point.z}), {x: 0, y: 0, z: 0});
      center.x /= points.length; center.y /= points.length; center.z /= points.length;
      object = new T.Group();
      const hub = new T.Mesh(new T.TorusGeometry(3.5, 1.1, 8, 20), new T.MeshStandardMaterial({color: visual.color,
        emissive: new T.Color(visual.color).multiplyScalar(0.35), transparent: true, opacity: visual.alpha}));
      hub.position.set(center.x, center.y, center.z); hub.userData = {selection,
        label: `HYPEREDGE\n${edge.id}\n${members.length} MEMBERS · ${flowLabel} · ${evidence.name}`};
      object.add(hub); this.pickTargets.push(hub);
      const vertices = [];
      for (const point of points) vertices.push(center.x, center.y, center.z, point.x, point.y, point.z);
      const geometry = new T.BufferGeometry(); geometry.setAttribute("position", new T.Float32BufferAttribute(vertices, 3));
      const spokes = new T.LineSegments(geometry, new T.LineDashedMaterial({color: visual.color,
        transparent: true, opacity: evidence.style.alpha * 0.7, dashSize: 3, gapSize: 2}));
      spokes.computeLineDistances(); spokes.userData = {selection, label: hub.userData.label};
      object.add(spokes); this.pickTargets.push(spokes); this.edgeGroup.add(object);
    }
    this.edgeObjects.set(edge.id, object);
  }

  #point(event, select) {
    const bounds = this.renderer.domElement.getBoundingClientRect();
    this.pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
    this.pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    // Give every node a small screen-space hit target. At a dense bounded camera fit
    // a sphere can be visually meaningful yet sub-pixel to mesh raycasting.
    // Nodes take precedence inside this target so dense lines cannot steal an
    // intentional click.
    let nodeHit = null; let nearestPixelsSquared = 81;
    const projected = new this.THREE.Vector3();
    for (const mesh of this.nodeMeshes.values()) {
      mesh.getWorldPosition(projected).project(this.camera);
      if (projected.z < -1 || projected.z > 1) continue;
      const dx = (projected.x + 1) * bounds.width / 2 - (event.clientX - bounds.left);
      const dy = (-projected.y + 1) * bounds.height / 2 - (event.clientY - bounds.top);
      const distance = dx * dx + dy * dy;
      if (distance < nearestPixelsSquared) { nearestPixelsSquared = distance; nodeHit = mesh; }
    }
    const hit = nodeHit ?? this.raycaster.intersectObjects(this.pickTargets, false)[0]?.object;
    this.renderer.domElement.style.cursor = hit ? (hit.userData?.selection ? "pointer" : "help") : "grab";
    this.tooltip.hidden = !hit;
    if (hit) {
      this.tooltip.textContent = hit.userData.label ?? "GRAPH ENTITY";
      this.tooltip.style.left = `${event.clientX - bounds.left + 12}px`;
      this.tooltip.style.top = `${event.clientY - bounds.top + 12}px`;
    }
    if (select && hit?.userData?.selection) {
      this.selected = hit.userData.selection.entityId;
      this.root.dispatchEvent(new this.window.CustomEvent("scythe-web:graph-selection", {bubbles: true,
        detail: {...hit.userData.selection}}));
    }
  }

  #frame() {
    const T = this.THREE; const box = new T.Box3().setFromObject(this.nodeGroup);
    const center = box.getCenter(new T.Vector3()); const size = box.getSize(new T.Vector3());
    const distance = Math.max(size.x, size.y, size.z, 80) * 1.55;
    this.camera.position.set(center.x + distance * 0.75, center.y + distance * 0.52, center.z + distance);
    this.camera.lookAt(center); if (this.controls) { this.controls.target.copy(center); this.controls.update(); }
  }

  #setAnimation(enabled) {
    if (!this.renderer) return;
    this.renderer.setAnimationLoop(enabled ? () => {
      this.controls?.update();
      const frameAt = this.window.performance?.now?.() ?? Date.now();
      if (this.lastFrameAt != null) this.controller.reportFrameTime?.(frameAt - this.lastFrameAt);
      this.lastFrameAt = frameAt;
      if (!this.reducedMotion) {
        const now = frameAt;
        for (const mesh of this.nodeMeshes.values()) {
          const age = now - mesh.userData.arrivedAt; const bloom = age < 700 ? 1 + (1 - age / 700) * 0.7 : 1;
          mesh.scale.setScalar(bloom);
        }
        for (const edge of this.edgeObjects.values()) edge.traverse?.((child) => {
          const motion = child.userData?.motion; if (!motion) return;
          const progress = ((now / motion.duration) + motion.phase) % 1;
          child.position.copy(motion.start).lerp(motion.end, progress);
        });
      }
      this.renderer.render(this.scene, this.camera);
    } : null);
    if (!enabled) this.lastFrameAt = null;
  }

  setVisible(visible) { this.visible = Boolean(visible); this.container.hidden = !this.visible;
    this.#setAnimation(this.visible && !this.document.hidden); if (this.visible) this.resize(); }

  resize() {
    if (!this.renderer || !this.visible) return;
    const width = this.container.clientWidth; const height = this.container.clientHeight;
    if (width < 1 || height < 1) return;
    this.camera.aspect = width / height; this.camera.updateProjectionMatrix(); this.renderer.setSize(width, height, false);
  }

  #disposeObject(object) {
    object.traverse?.((child) => { child.geometry?.dispose?.();
      if (Array.isArray(child.material)) child.material.forEach((material) => material.dispose?.());
      else child.material?.dispose?.(); });
  }

  destroy() {
    this.unsubscribe?.(); this.unsubscribe = null; this.#setAnimation(false);
    this.resizeObserver?.disconnect(); this.document.removeEventListener?.("visibilitychange", this.onVisibility);
    this.renderer?.domElement.removeEventListener("pointermove", this.onPointerMove);
    this.renderer?.domElement.removeEventListener("click", this.onClick);
    this.controls?.dispose(); this.#disposeObject(this.scene); this.renderer?.dispose();
    this.renderer?.forceContextLoss?.(); this.container.replaceChildren(); this.started = false;
  }
}
