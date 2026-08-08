import { evidenceStyle } from "./evidenceStyles.js";

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
      positions.set(node.id, {x: center.x / known.length + seed.x * 0.18,
        y: center.y / known.length + seed.y * 0.18, z: center.z / known.length + seed.z * 0.18});
    } else positions.set(node.id, seed);
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
    this.visible = !this.container.hidden; this.selected = null; this.started = false;
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
    // atmospheric depth subtle enough that a fitted 200-node scene is visible.
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
    const nodes = (graph.nodes ?? []).slice(0, 500); const edges = (graph.edges ?? []).slice(0, 1000);
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
    this.stats.textContent = `${nodes.length} NODES · ${edges.length - hyperedges} EDGES · ${hyperedges} HYPEREDGES · THREE r${this.THREE.REVISION}`;
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
    let mesh = this.nodeMeshes.get(node.id);
    if (!mesh) {
      const radius = node.kind === "network_host" ? 4.6 : graphKind(node) === "event" ? 3.7 : 3.2;
      const material = new T.MeshStandardMaterial({color: evidence.style.color,
        emissive: new T.Color(evidence.style.color).multiplyScalar(0.28), metalness: 0.25,
        roughness: 0.52, transparent: evidence.style.alpha < 1, opacity: evidence.style.alpha});
      mesh = new T.Mesh(this.#nodeGeometry(evidence.name, radius), material);
      mesh.userData.arrivedAt = this.window.performance?.now?.() ?? Date.now();
      this.nodeMeshes.set(node.id, mesh); this.nodeGroup.add(mesh);
    } else {
      mesh.material.color.set(evidence.style.color); mesh.material.opacity = evidence.style.alpha;
      mesh.material.transparent = evidence.style.alpha < 1;
    }
    const point = this.positions.get(node.id); mesh.position.set(point.x, point.y, point.z);
    mesh.userData = {...mesh.userData, selection: {kind: graphKind(node), entityId: node.id,
      graphRevision: revision, ...(node.position ? {position: node.position} : {}), observedAt: node.observedAt ?? null},
      label: `${node.kind ?? "node"}\n${node.id}\n${evidence.name}`, evidenceClass: evidence.name};
  }

  #addEdge(edge, revision) {
    const T = this.THREE; const members = (edge.nodes ?? []).filter((id) => this.positions.has(id));
    if (members.length < 2) return;
    const evidence = safeEvidence(edge.evidenceClass); const selection = {kind: "graph-edge", entityId: edge.id,
      graphRevision: revision, observedAt: edge.observedAt ?? edge.timestamp ?? null};
    const points = members.map((id) => this.positions.get(id));
    let object;
    if (members.length === 2) {
      const geometry = new T.BufferGeometry().setFromPoints(points.map((point) => new T.Vector3(point.x, point.y, point.z)));
      const dashed = evidence.style.line !== "solid";
      const material = dashed ? new T.LineDashedMaterial({color: evidence.style.color, transparent: true,
        opacity: evidence.style.alpha, dashSize: evidence.style.line === "dotted" ? 1.5 : 4, gapSize: 2.5}) :
        new T.LineBasicMaterial({color: evidence.style.color, transparent: true, opacity: evidence.style.alpha});
      object = new T.Line(geometry, material); if (dashed) object.computeLineDistances();
      object.userData = {selection, label: `${edge.kind ?? "edge"}\n${edge.id}\n${evidence.name}`};
      this.edgeGroup.add(object); this.pickTargets.push(object);
    } else {
      const center = points.reduce((sum, point) => ({x: sum.x + point.x, y: sum.y + point.y,
        z: sum.z + point.z}), {x: 0, y: 0, z: 0});
      center.x /= points.length; center.y /= points.length; center.z /= points.length;
      object = new T.Group();
      const hub = new T.Mesh(new T.TorusGeometry(3.5, 1.1, 8, 20), new T.MeshStandardMaterial({color: evidence.style.color,
        emissive: new T.Color(evidence.style.color).multiplyScalar(0.35), transparent: true, opacity: evidence.style.alpha}));
      hub.position.set(center.x, center.y, center.z); hub.userData = {selection,
        label: `HYPEREDGE\n${edge.id}\n${members.length} MEMBERS · ${evidence.name}`};
      object.add(hub); this.pickTargets.push(hub);
      const vertices = [];
      for (const point of points) vertices.push(center.x, center.y, center.z, point.x, point.y, point.z);
      const geometry = new T.BufferGeometry(); geometry.setAttribute("position", new T.Float32BufferAttribute(vertices, 3));
      const spokes = new T.LineSegments(geometry, new T.LineDashedMaterial({color: evidence.style.color,
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
    // Give every node a small screen-space hit target. At a 200-node camera fit
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
    this.renderer.domElement.style.cursor = hit ? "pointer" : "grab";
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
      if (!this.reducedMotion) {
        const now = this.window.performance?.now?.() ?? Date.now();
        for (const mesh of this.nodeMeshes.values()) {
          const age = now - mesh.userData.arrivedAt; const bloom = age < 700 ? 1 + (1 - age / 700) * 0.7 : 1;
          mesh.scale.setScalar(bloom);
        }
      }
      this.renderer.render(this.scene, this.camera);
    } : null);
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
