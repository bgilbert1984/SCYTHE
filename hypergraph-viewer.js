/**
 * hypergraph-viewer.js  —  <hypergraph-viewer> Web Component
 *
 * Modes:   viewer (3D default) | autopsy (3D + info panel) | rf (volumetric field) | lite (nodes only)
 * Methods: loadGraph(data), exportPNG(), exportJSON(), exportField(size)
 * Events:  graph-loaded, node-click
 * Attrs:   src, mode, cluster-id
 *
 * Graph data formats accepted:
 *   Gravity: { nodes:[{id,kind,label,mass,threat_level,...}], nodes_index, edges:[[si,di,kind,conf],...], edge_metadata:[...] }
 *   Export:  { cluster_id, nodes:[{id,...,x,y,z,intensity}], edges:[...], edge_metadata:[...], metadata:{...} }
 *
 * THREE.js must be on window.THREE (set by command-ops import-map module or CDN script).
 * OrbitControls from window.ThreeOrbitControls || window.THREE.OrbitControls.
 */
(function () {
  'use strict';

  class HypergraphViewer extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this._data      = null;
      this._animId    = null;
      this._renderer  = null;
      this._scene     = null;
      this._camera    = null;
      this._controls  = null;
      this._nodeMesh  = null;
      this._edgeMesh  = null;
      this._fieldMesh = null;
      this._uncertaintyMesh = null;
      this._fieldTex  = null;
      this._edgeStats = null;
      this._geoProjection = null;
      this._ro        = null;
      this._abortCtrl = null;
      this._initDone  = false;
    }

    static get observedAttributes() { return ['src', 'mode', 'theme', 'cluster-id']; }

    connectedCallback() {
      this._buildShadow();
      if (window.THREE) {
        this._initRenderer();
      } else {
        // THREE not yet available — handles module-script vs regular-script ordering
        const poll = () => window.THREE ? this._initRenderer() : requestAnimationFrame(poll);
        requestAnimationFrame(poll);
      }
    }

    disconnectedCallback() { this._destroy(); }

    attributeChangedCallback(name, _old, val) {
      if (!this.isConnected) return;
      if      (name === 'src'        && val) this._fetchSrc(val);
      else if (name === 'cluster-id' && val) this._fetchCluster(val);
      else if (name === 'mode')              this._applyMode();
    }

    // ─── Shadow DOM ────────────────────────────────────────────────────────
    _buildShadow() {
      this.shadowRoot.innerHTML = `
        <style>
          :host {
            display: block; position: relative; overflow: hidden;
            background: #080810; font-family: monospace;
          }
          canvas { width: 100%; height: 100%; display: block; }
          #hv-info {
            position: absolute; top: 8px; right: 8px; width: 185px;
            display: none; background: rgba(5,5,20,0.92);
            border: 1px solid #2a2a4a; border-radius: 6px;
            padding: 10px; font-size: 11px; color: #aaa; line-height: 1.7;
            pointer-events: none;
          }
          :host([mode="autopsy"]) #hv-info { display: block; }
          #hv-status {
            position: absolute; bottom: 6px; left: 8px;
            font-size: 10px; color: #444; pointer-events: none;
          }
          #hv-toolbar {
            position: absolute; top: 6px; left: 8px;
            display: flex; gap: 5px; pointer-events: auto;
          }
          #hv-toolbar button {
            padding: 3px 9px; font-size: 10px; font-family: monospace;
            background: rgba(0,40,80,0.75); border: 1px solid #1a4a6a;
            border-radius: 3px; color: #4af; cursor: pointer;
          }
          #hv-toolbar button:hover { background: rgba(0,80,160,0.8); color: #fff; }
        </style>
        <canvas id="hv-c"></canvas>
        <div id="hv-toolbar">
          <button id="btn-png"    title="Export PNG">📸</button>
          <button id="btn-json"   title="Export JSON">📄</button>
          <button id="btn-cycle"  title="Cycle mode">⊕</button>
        </div>
        <div id="hv-info"><div id="hv-info-body">—</div></div>
        <div id="hv-status">initializing…</div>`;

      this.shadowRoot.getElementById('btn-png') .addEventListener('click', () => this.exportPNG());
      this.shadowRoot.getElementById('btn-json').addEventListener('click', () => this.exportJSON());
      this.shadowRoot.getElementById('btn-cycle').addEventListener('click', () => this._cycleMode());
    }

    // ─── Renderer init ─────────────────────────────────────────────────────
    _initRenderer() {
      if (this._initDone) return;
      this._initDone = true;

      const THREE  = window.THREE;
      const canvas = this.shadowRoot.getElementById('hv-c');
      if (!canvas || !THREE) { this._setStatus('THREE.js not found'); return; }

      this._renderer = new THREE.WebGLRenderer({
        canvas,
        antialias: true,
        alpha: false,
        preserveDrawingBuffer: true,   // required: canvas.toDataURL() only works with this
      });
      this._renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

      this._scene = new THREE.Scene();
      this._scene.background = new THREE.Color(0x080810);

      const { w, h } = this._dims();
      this._camera = new THREE.PerspectiveCamera(60, w / h, 0.1, 5000);
      this._camera.position.set(0, 0, 260);

      const Controls = window.ThreeOrbitControls || (THREE.OrbitControls);
      if (Controls) {
        this._controls = new Controls(this._camera, canvas);
        this._controls.enableDamping   = true;
        this._controls.dampingFactor   = 0.08;
        this._controls.minDistance     = 15;
        this._controls.maxDistance     = 1500;
      }

      const amb = new THREE.AmbientLight(0x223344, 1.2);
      const dl  = new THREE.DirectionalLight(0x4499ff, 1.5);
      dl.position.set(1, 2, 3);
      this._scene.add(amb, dl);

      this._renderer.setSize(w, h);
      this._ro = new ResizeObserver(() => this._resize());
      this._ro.observe(this);
      this._animate();

      // Replay pending attribute values that arrived before THREE was ready
      const src = this.getAttribute('src');
      const cid = this.getAttribute('cluster-id');
      if      (src) this._fetchSrc(src);
      else if (cid) this._fetchCluster(cid);
      this._setStatus('ready');
    }

    _dims() {
      const w = this.clientWidth  || 800;
      const h = this.clientHeight || 500;
      return { w, h };
    }

    _resize() {
      if (!this._renderer) return;
      const { w, h } = this._dims();
      this._renderer.setSize(w, h);
      if (this._camera) {
        this._camera.aspect = w / h;
        this._camera.updateProjectionMatrix();
      }
    }

    _animate() {
      this._animId = requestAnimationFrame(() => this._animate());
      if (this._controls) this._controls.update();
      this._animateEdges((typeof performance !== 'undefined' ? performance.now() : Date.now()) / 1000);
      if (this._renderer && this._scene && this._camera) {
        this._renderer.render(this._scene, this._camera);
      }
    }

    _destroy() {
      if (this._animId)   { cancelAnimationFrame(this._animId); this._animId = null; }
      if (this._abortCtrl){ this._abortCtrl.abort();            this._abortCtrl = null; }
      if (this._ro)       { this._ro.disconnect();              this._ro = null; }
      if (this._controls) { this._controls.dispose();           this._controls = null; }
      this._clearMeshes();
      if (this._renderer) { this._renderer.dispose();           this._renderer = null; }
      this._scene = this._camera = null;
    }

    _disposeObject3D(obj) {
      if (!obj) return;
      if (this._scene) this._scene.remove(obj);
      if (typeof obj.traverse === 'function') {
        obj.traverse(child => {
          if (child.geometry) child.geometry.dispose();
          const mats = Array.isArray(child.material) ? child.material : [child.material];
          mats.filter(Boolean).forEach(mt => mt.dispose());
        });
        return;
      }
      if (obj.geometry) obj.geometry.dispose();
      const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
      mats.filter(Boolean).forEach(mt => mt.dispose());
    }

    _clearMeshes() {
      for (const m of [this._nodeMesh, this._edgeMesh, this._fieldMesh, this._uncertaintyMesh]) {
        if (!m) continue;
        this._disposeObject3D(m);
      }
      if (this._fieldTex) { this._fieldTex.dispose(); this._fieldTex = null; }
      this._nodeMesh = this._edgeMesh = this._fieldMesh = this._uncertaintyMesh = null;
      this._edgeStats = null;
      this._geoProjection = null;
    }

    // ─── Fetch helpers ─────────────────────────────────────────────────────
    async _fetchSrc(url) {
      if (this._abortCtrl) this._abortCtrl.abort();
      this._abortCtrl = new AbortController();
      try {
        this._setStatus('fetching…');
        const res = await fetch(url, { signal: this._abortCtrl.signal });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        this.loadGraph(await res.json());
      } catch (e) {
        if (e.name !== 'AbortError') this._setStatus(`Error: ${e.message}`);
      }
    }

    _fetchCluster(id) {
      const base = window.SCYTHE_API_BASE || window.API_BASE || '';
      this._fetchSrc(`${base}/api/clusters/export-data/${encodeURIComponent(id)}`);
    }

    // ─── Public API ────────────────────────────────────────────────────────

    /** Load a graph data object; accepts gravity format or export format. */
    loadGraph(data) {
      if (!this._renderer) {
        // Renderer not ready yet — wait (e.g. if THREE was slow to load)
        const check = setInterval(() => {
          if (this._renderer) { clearInterval(check); this.loadGraph(data); }
        }, 50);
        return;
      }
      this._data = data;
      this._buildScene(data);
      this.dispatchEvent(new CustomEvent('graph-loaded', { bubbles: true, detail: data }));
    }

    /** Render current frame and download as PNG. Returns data URL. */
    exportPNG() {
      if (!this._renderer) return null;
      this._renderer.render(this._scene, this._camera);
      const url = this._renderer.domElement.toDataURL('image/png', 1.0);
      const a   = document.createElement('a');
      a.href     = url;
      a.download = `hypergraph-${Date.now()}.png`;
      a.click();
      return url;
    }

    /** Download current graph data as JSON. */
    exportJSON() {
      if (!this._data) return;
      const blob = new Blob([JSON.stringify(this._data, null, 2)], { type: 'application/json' });
      const a    = document.createElement('a');
      a.href     = URL.createObjectURL(blob);
      a.download = `hypergraph-${this._data.cluster_id || Date.now()}.json`;
      a.click();
    }

    /** Compute and return the 3D volumetric field array (Float32Array serialized as Array). */
    exportField(size = 32) {
      if (!this._data) return null;
      const nodes = this._normalizeNodes(this._data);
      return { size, data: Array.from(this._computeField(nodes, size)) };
    }

    // ─── Scene construction ────────────────────────────────────────────────
    _buildScene(data) {
      const THREE = window.THREE;
      if (!THREE || !this._scene) return;

      this._clearMeshes();

      const nodes = this._normalizeNodes(data);
      const edges = this._normalizeEdges(data, nodes);
      data._nodeCount = nodes.length;
      data._edgeCount = edges.length;
      data._geoCount = nodes.filter(n => n.geospatial).length;

      const mode     = this.getAttribute('mode') || 'viewer';
      // Field path: large clusters or explicit rf mode; requires WebGL2 (Data3DTexture)
      const useField = (nodes.length > 300 || mode === 'rf') && !!THREE.Data3DTexture;

      if (useField) {
        this._buildField(nodes);
      } else {
        this._buildNodes(nodes, THREE);
        this._buildUncertaintyShells(nodes, THREE);
        if (mode !== 'lite') this._buildEdges(edges, nodes, THREE);
      }

      this._applyMode();
      this._updateInfoPanel(data);
      this._setStatus(`${nodes.length} nodes · ${edges.length} edges`);
    }

    _num(value) {
      const n = Number(value);
      return Number.isFinite(n) ? n : null;
    }

    _extractGeo(raw) {
      const stack = [raw];
      const seen = new Set();
      while (stack.length) {
        const src = stack.pop();
        if (!src || typeof src !== 'object') continue;
        if (seen.has(src)) continue;
        seen.add(src);

        let lat = this._num(src.lat ?? src.latitude);
        let lon = this._num(src.lon ?? src.lng ?? src.longitude);
        let alt = this._num(src.alt ?? src.altitude ?? src.altitude_m);
        const pos = src.position || src.coordinates || src.centroid;
        if ((lat == null || lon == null) && Array.isArray(pos) && pos.length >= 2) {
          lat = this._num(pos[0]);
          lon = this._num(pos[1]);
          if (pos.length >= 3) alt = this._num(pos[2]);
        }
        if (lat != null && lon != null && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180) {
          const uncertainty = this._num(src.uncertainty_radius ?? src.uncertaintyRadius ?? src.accuracy_m ?? src.radius_m);
          const confidence = this._num(src.confidence ?? (src.labels && src.labels.confidence) ?? (src.metadata && src.metadata.confidence));
          return {
            lat,
            lon,
            alt: alt || 0,
            uncertainty_radius: uncertainty,
            confidence,
            spatial_frame: src.spatial_frame || 'EPSG:4326',
          };
        }

        ['geospatial', 'geo', 'location', 'metadata', 'labels', 'semanticPayload', 'semantic_payload', 'afterState', 'after_state'].forEach(k => {
          if (src[k] && typeof src[k] === 'object') stack.push(src[k]);
        });
      }
      return null;
    }

    _projectGeoNodes(nodes) {
      const geoNodes = nodes.filter(n => n.geospatial);
      if (!geoNodes.length) return nodes;

      const lats = geoNodes.map(n => n.geospatial.lat);
      const lons = geoNodes.map(n => n.geospatial.lon);
      const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2;
      const centerLon = (Math.min(...lons) + Math.max(...lons)) / 2;
      const latSpan = Math.max(0.01, Math.max(...lats) - Math.min(...lats));
      const lonSpan = Math.max(0.01, Math.max(...lons) - Math.min(...lons));
      const lonScale = Math.max(0.18, Math.cos(centerLat * Math.PI / 180));
      const scale = 180 / Math.max(latSpan, lonSpan * lonScale, 0.01);
      this._geoProjection = { centerLat, centerLon, scale, lonScale };

      return nodes.map((n, i) => {
        if (n.x !== undefined && n.y !== undefined) return n;
        if (!n.geospatial) return { ...n, ...this._fallbackPoint(i, nodes.length) };
        const g = n.geospatial;
        return {
          ...n,
          x: (g.lon - centerLon) * lonScale * scale,
          y: (g.lat - centerLat) * scale,
          z: Math.max(-60, Math.min(180, (g.alt || 0) / 120)),
        };
      });
    }

    _fallbackPoint(i, n) {
      const phi = Math.PI * (1 + Math.sqrt(5));
      const polar = Math.acos(1 - 2 * (i + 0.5) / Math.max(1, n));
      const az = phi * i;
      const r = 80;
      return {
        x: r * Math.sin(polar) * Math.cos(az),
        y: r * Math.sin(polar) * Math.sin(az),
        z: r * Math.cos(polar),
      };
    }

    /**
     * Normalize node positions to {x,y,z}.
     * Geospatial nodes (lat/lon/alt) are projected into a stable local plane.
     * If a node lacks positions, apply a deterministic Fibonacci sphere layout.
     */
    _normalizeNodes(data) {
      const raw = (data.nodes || []).map(n => ({
        ...n,
        geospatial: n.geospatial || this._extractGeo(n),
      }));
      if (!raw.length) return [];

      const needLayout = raw.some(n => n.x === undefined || n.y === undefined);
      if (!needLayout) return raw.map(n => ({
        ...n,
        intensity: Math.min(1, n.intensity !== undefined ? n.intensity : (n.mass || 0.3)),
      }));

      const geoCount = raw.filter(n => n.geospatial).length;
      if (geoCount) {
        return this._projectGeoNodes(raw).map(n => ({
          ...n,
          intensity: Math.min(1, n.intensity !== undefined ? n.intensity : (n.mass || n.geospatial?.confidence || 0.3)),
        }));
      }

      const n = raw.length;
      const φ = Math.PI * (1 + Math.sqrt(5));   // golden angle in radians

      return raw.map((nd, i) => {
        if (nd.x !== undefined && nd.y !== undefined) return {
          ...nd,
          intensity: Math.min(1, nd.intensity !== undefined ? nd.intensity : (nd.mass || 0.3)),
        };
        const polar = Math.acos(1 - 2 * (i + 0.5) / n);
        const az    = φ * i;
        const r     = 80 + Math.min(1, nd.mass || 0.5) * 60;
        return {
          ...nd,
          x: r * Math.sin(polar) * Math.cos(az),
          y: r * Math.sin(polar) * Math.sin(az),
          z: r * Math.cos(polar),
          intensity: Math.min(1, nd.intensity !== undefined ? nd.intensity : (nd.mass || 0.3)),
        };
      });
    }

    _edgeNumber(value) {
      const n = Number(value);
      return Number.isFinite(n) ? n : null;
    }

    _normalizeEdgeRecord(base, raw) {
      const srcMeta = (raw && typeof raw === 'object') ? raw : {};
      const metadata = srcMeta.metadata || {};
      const labels = srcMeta.labels || {};
      const renderStyle = srcMeta.render_style || metadata.render_style || {};
      const supporting = srcMeta.supporting_evidence || metadata.supporting_evidence || {};
      const fieldView = srcMeta.field_view || metadata.field_view || {};
      const styleHints = srcMeta.style_hints || metadata.style_hints || {};
      const obsClass =
        srcMeta.obs_class ||
        metadata.obs_class ||
        labels.obs_class ||
        (metadata.forecast ? 'forecast' : (metadata.observed ? 'observed' : ''));
      const entropy = this._edgeNumber(srcMeta.entropy ?? supporting.entropy ?? metadata.entropy);
      const divergenceRisk = this._edgeNumber(srcMeta.divergence_risk ?? supporting.divergence_risk ?? metadata.divergence_risk);
      const identityPressure = this._edgeNumber(srcMeta.identity_pressure ?? supporting.identity_pressure ?? metadata.identity_pressure);
      const periodicityS = this._edgeNumber(srcMeta.periodicity_s ?? supporting.periodicity_s ?? metadata.periodicity_s);
      const temporalCohesion = this._edgeNumber(srcMeta.temporal_cohesion ?? supporting.temporal_cohesion ?? metadata.temporal_cohesion);
      const resilienceScore = this._edgeNumber(srcMeta.resilience_score ?? supporting.resilience_score ?? metadata.resilience_score);
      return {
        ...base,
        id: srcMeta.id || metadata.id || '',
        metadata,
        labels,
        render_style: renderStyle,
        supporting_evidence: supporting,
        field_view: fieldView,
        style_hints: styleHints,
        obs_class: obsClass,
        temporal_phase: srcMeta.temporal_phase || supporting.temporal_phase || metadata.temporal_phase || '',
        dissonance_zone: srcMeta.dissonance_zone || supporting.dissonance_zone || metadata.dissonance_zone || '',
        entropy,
        divergence_risk: divergenceRisk,
        identity_pressure: identityPressure,
        periodicity_s: periodicityS,
        temporal_cohesion: temporalCohesion,
        resilience_score: resilienceScore,
        top_intent_label: srcMeta.top_intent_label || supporting.top_intent_label || metadata.top_intent_label || '',
      };
    }

    /**
     * Normalise edges to [{src, dst, kind, confidence, ...visual metadata}].
     * Handles both indexed format [[si,di,kind,conf],...] and object format [{src,dst,...}].
     */
    _normalizeEdges(data, nodesArr) {
      const rawEdges = data.edges || [];
      const edgeMetadata = Array.isArray(data.edge_metadata) ? data.edge_metadata : [];
      if (!rawEdges.length) return [];

      if (Array.isArray(rawEdges[0])) {
        // Indexed gravity format
        const index = data.nodes_index || nodesArr.map(n => n.id);
        return rawEdges.map(([si, di, kind, conf], idx) => this._normalizeEdgeRecord({
          src: index[si], dst: index[di], kind, confidence: conf,
        }, edgeMetadata[idx] || {}));
      }
      // Object format
      return rawEdges.map(e => this._normalizeEdgeRecord({
        src:        e.src  || e.source  || '',
        dst:        e.dst  || e.target  || '',
        kind:       e.kind || e.type    || '',
        confidence: e.confidence ?? e.weight ?? 0.5,
      }, e));
    }

    _edgeVisualProfile(edge) {
      const kind = String(edge.kind || '').toUpperCase();
      const render = edge.render_style || {};
      const hints = edge.style_hints || {};
      const obsClass = String(edge.obs_class || '').toLowerCase();
      const identityPressure = this._edgeNumber(edge.identity_pressure);
      const entropy = this._edgeNumber(edge.entropy);
      const divergenceRisk = this._edgeNumber(edge.divergence_risk);
      const periodicityS = this._edgeNumber(edge.periodicity_s);
      const temporalCohesion = this._edgeNumber(edge.temporal_cohesion);

      const ghost = !!(hints.ghost ?? render.ghost) ||
        (obsClass && obsClass !== 'observed') ||
        /PREDICTED|HYPOTHESIS|INFERRED/.test(kind);
      const identity = !!(hints.identity_color_lock ?? render.color_lock ?? (edge.field_view || {}).identity_color_lock) ||
        /IDENTIT/.test(kind) ||
        (identityPressure != null && identityPressure >= 0.72);
      const flicker = !!(hints.flicker ?? render.flicker) ||
        (entropy != null && entropy >= 0.58) ||
        (divergenceRisk != null && divergenceRisk >= 0.62) ||
        edge.dissonance_zone === 'COGNITIVE_CONFLICT_ZONE';
      const pulse = (hints.pulse || render.pulse || (
        periodicityS != null &&
        periodicityS <= 15 &&
        (entropy == null || entropy <= 0.45) &&
        (temporalCohesion == null || temporalCohesion >= 0.45)
      )) ? true : false;

      let family = 'observed';
      if (ghost) family = 'ghost';
      if (identity) family = 'identity';

      let animation = 'static';
      if (pulse) animation = 'pulse';
      if (flicker) animation = 'flicker';

      return { family, animation, ghost, identity, pulse, flicker };
    }

    _edgeMaterialSpec(profile) {
      const key = `${profile.family}:${profile.animation}`;
      const specs = {
        'observed:static': { color: 0x2c74d6, opacity: 0.34, dashed: false },
        'observed:pulse': { color: 0x4de6ff, opacity: 0.46, dashed: false, animate: 'pulse', amplitude: 0.22, frequency: 2.2 },
        'observed:flicker': { color: 0xff8f4a, opacity: 0.28, dashed: false, animate: 'flicker', amplitude: 0.18, frequency: 11.0 },
        'ghost:static': { color: 0x89a9c6, opacity: 0.13, dashed: true },
        'ghost:pulse': { color: 0x8fefff, opacity: 0.2, dashed: true, animate: 'pulse', amplitude: 0.16, frequency: 2.0 },
        'ghost:flicker': { color: 0xffa15f, opacity: 0.16, dashed: true, animate: 'flicker', amplitude: 0.14, frequency: 12.0 },
        'identity:static': { color: 0xcd7bff, opacity: 0.72, dashed: false },
        'identity:pulse': { color: 0xe0a6ff, opacity: 0.8, dashed: false, animate: 'pulse', amplitude: 0.14, frequency: 2.6 },
        'identity:flicker': { color: 0xd694ff, opacity: 0.62, dashed: false, animate: 'flicker', amplitude: 0.12, frequency: 10.0 },
      };
      return specs[key] || specs['observed:static'];
    }

    _animateEdges(t) {
      const group = this._edgeMesh;
      if (!group || !group.children || !group.children.length) return;
      for (const child of group.children) {
        const anim = child.userData && child.userData.edgeAnim;
        if (!anim || !child.material) continue;
        if (anim.type === 'pulse') {
          child.material.opacity = Math.min(0.98, anim.base + anim.amplitude * (0.5 + 0.5 * Math.sin(t * anim.frequency + anim.phase)));
        } else if (anim.type === 'flicker') {
          child.material.opacity = Math.min(
            0.95,
            anim.base * 0.7 + anim.amplitude * (0.45 + 0.55 * Math.sin(t * anim.frequency + anim.phase))
          );
        } else if (child.material.opacity !== anim.base) {
          child.material.opacity = anim.base;
        }
      }
    }

    // ─── Mesh builders ────────────────────────────────────────────────────

    _buildNodes(nodes, THREE) {
      const count = Math.min(nodes.length, 8000);
      const C = {
        benign:  new THREE.Color(0x00ccff),
        suspect: new THREE.Color(0xff9900),
        threat:  new THREE.Color(0xff3333),
      };
      const geom  = new THREE.SphereGeometry(1, 6, 5);
      const mat   = new THREE.MeshPhongMaterial();
      const mesh  = new THREE.InstancedMesh(geom, mat, count);
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);

      const dummy = new THREE.Object3D();
      const col   = new THREE.Color();

      for (let i = 0; i < count; i++) {
        const n  = nodes[i];
        const sz = 1.5 + Math.min(1, n.intensity || 0.3) * 4;
        dummy.position.set(n.x, n.y, n.z);
        dummy.scale.setScalar(sz);
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);

        const tl = n.threat_level || 0;
        col.copy(tl >= 2 ? C.threat : tl >= 1 ? C.suspect : C.benign);
        mesh.setColorAt(i, col);
      }
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;

      // Raycasting for node-click event
      this.shadowRoot.getElementById('hv-c').addEventListener('click', (ev) => {
        this._pickNode(ev, mesh, nodes.slice(0, count), THREE);
      });

      this._nodeMesh = mesh;
      this._scene.add(mesh);
    }

    _buildUncertaintyShells(nodes, THREE) {
      const uncertain = nodes.filter(n => n.geospatial && Number.isFinite(Number(n.geospatial.uncertainty_radius)));
      if (!uncertain.length) return;

      const group = new THREE.Group();
      const color = new THREE.Color(0x39d6ff);
      for (const n of uncertain.slice(0, 512)) {
        const radiusM = Math.max(1, Number(n.geospatial.uncertainty_radius));
        const radius = Math.max(2.5, Math.min(70, Math.sqrt(radiusM) * 0.7));
        const geom = new THREE.RingGeometry(radius * 0.82, radius, 48);
        const mat = new THREE.MeshBasicMaterial({
          color,
          transparent: true,
          opacity: Math.max(0.08, Math.min(0.28, 22 / (radius + 22))),
          side: THREE.DoubleSide,
          depthWrite: false,
        });
        const ring = new THREE.Mesh(geom, mat);
        ring.position.set(n.x || 0, n.y || 0, n.z || 0);
        ring.userData.geospatialUncertainty = {
          entity_id: n.id,
          uncertainty_radius: radiusM,
          lat: n.geospatial.lat,
          lon: n.geospatial.lon,
        };
        group.add(ring);
      }

      this._uncertaintyMesh = group;
      this._scene.add(group);
    }

    _buildEdges(edges, nodes, THREE) {
      const posMap = {};
      nodes.forEach(n => { posMap[n.id] = [n.x, n.y, n.z]; });

      const buckets = new Map();
      const summary = { observed: 0, ghost: 0, identity: 0, pulse: 0, flicker: 0 };
      let drawn = 0;
      for (const e of edges) {
        if (drawn >= 1500) break;
        const s = posMap[e.src], d = posMap[e.dst];
        if (!s || !d) continue;
        const profile = this._edgeVisualProfile(e);
        const bucketKey = `${profile.family}:${profile.animation}`;
        if (!buckets.has(bucketKey)) buckets.set(bucketKey, { profile, verts: [] });
        buckets.get(bucketKey).verts.push(s[0], s[1], s[2], d[0], d[1], d[2]);
        if (profile.ghost) summary.ghost++;
        else summary.observed++;
        if (profile.identity) summary.identity++;
        if (profile.pulse) summary.pulse++;
        if (profile.flicker) summary.flicker++;
        drawn++;
      }
      if (!buckets.size) return;

      const edgeGroup = new THREE.Group();
      let layerIndex = 0;
      for (const bucket of buckets.values()) {
        const spec = this._edgeMaterialSpec(bucket.profile);
        const geom = new THREE.BufferGeometry();
        geom.setAttribute('position', new THREE.Float32BufferAttribute(bucket.verts, 3));
        const mat = spec.dashed
          ? new THREE.LineDashedMaterial({
              color: spec.color,
              transparent: true,
              opacity: spec.opacity,
              dashSize: 4,
              gapSize: 2.5,
            })
          : new THREE.LineBasicMaterial({
              color: spec.color,
              transparent: true,
              opacity: spec.opacity,
            });
        const lines = new THREE.LineSegments(geom, mat);
        if (spec.dashed && typeof lines.computeLineDistances === 'function') lines.computeLineDistances();
        lines.userData.edgeAnim = {
          type: spec.animate || 'static',
          base: spec.opacity,
          amplitude: spec.amplitude || 0,
          frequency: spec.frequency || 0,
          phase: layerIndex * 0.9,
        };
        edgeGroup.add(lines);
        layerIndex++;
      }
      this._edgeMesh = edgeGroup;
      this._edgeStats = summary;
      this._scene.add(edgeGroup);
    }

    _buildField(nodes) {
      const THREE = window.THREE;
      const S     = 32;
      const data  = this._computeField(nodes, S);

      const tex = new THREE.Data3DTexture(data, S, S, S);
      tex.format         = THREE.RedFormat;
      tex.type           = THREE.FloatType;
      tex.minFilter      = THREE.LinearFilter;
      tex.magFilter      = THREE.LinearFilter;
      tex.unpackAlignment = 1;
      tex.needsUpdate    = true;
      this._fieldTex = tex;

      const mat = new THREE.RawShaderMaterial({
        glslVersion: THREE.GLSL3,
        uniforms: {
          uField: { value: tex },
          uThr:   { value: 0.10 },
        },
        vertexShader: `
          in vec3 position;
          uniform mat4 modelViewMatrix, projectionMatrix;
          out vec3 vPos;
          void main() {
            vPos = position;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          }
        `,
        fragmentShader: `
          precision highp float;
          precision highp sampler3D;
          uniform sampler3D uField;
          uniform float uThr;
          in vec3 vPos;
          out vec4 fragColor;
          void main() {
            float d = texture(uField, vPos * 0.5 + 0.5).r;
            if (d < uThr) discard;
            vec3 cold = vec3(0.0, 0.10, 0.55);
            vec3 hot  = vec3(0.0, 1.00, 1.00);
            fragColor = vec4(mix(cold, hot, d), d * 0.75);
          }
        `,
        transparent: true,
        depthWrite:  false,
        side:        THREE.BackSide,
      });

      const geom = new THREE.BoxGeometry(160, 160, 160);
      this._fieldMesh = new THREE.Mesh(geom, mat);
      this._scene.add(this._fieldMesh);
    }

    /**
     * Gaussian-splat volumetric field at size^3.
     * Uses bounded neighbourhood loop (O(nodes × kernel)) instead of O(S³ × nodes) inversion.
     */
    _computeField(nodes, S) {
      const field = new Float32Array(S * S * S);
      if (!nodes.length) return field;

      let minX = Infinity, maxX = -Infinity;
      let minY = Infinity, maxY = -Infinity;
      let minZ = Infinity, maxZ = -Infinity;
      for (const n of nodes) {
        if (n.x < minX) minX = n.x;  if (n.x > maxX) maxX = n.x;
        if (n.y < minY) minY = n.y;  if (n.y > maxY) maxY = n.y;
        if (n.z < minZ) minZ = n.z;  if (n.z > maxZ) maxZ = n.z;
      }
      const rx = (maxX - minX) || 1;
      const ry = (maxY - minY) || 1;
      const rz = (maxZ - minZ) || 1;

      const R = 4;   // splat radius in voxels
      for (const n of nodes) {
        const nx = ((n.x - minX) / rx) * (S - 1);
        const ny = ((n.y - minY) / ry) * (S - 1);
        const nz = ((n.z - minZ) / rz) * (S - 1);
        const w  = n.intensity || 0.5;

        const x0 = Math.max(0, Math.round(nx) - R);
        const x1 = Math.min(S - 1, Math.round(nx) + R);
        const y0 = Math.max(0, Math.round(ny) - R);
        const y1 = Math.min(S - 1, Math.round(ny) + R);
        const z0 = Math.max(0, Math.round(nz) - R);
        const z1 = Math.min(S - 1, Math.round(nz) + R);

        for (let z = z0; z <= z1; z++)
          for (let y = y0; y <= y1; y++)
            for (let x = x0; x <= x1; x++) {
              const dx = x - nx, dy = y - ny, dz = z - nz;
              field[x + y * S + z * S * S] += w / (dx*dx + dy*dy + dz*dz + 0.5);
            }
      }

      // Normalise to [0, 1]
      let mx = 0;
      for (let i = 0; i < field.length; i++) if (field[i] > mx) mx = field[i];
      if (mx > 0) for (let i = 0; i < field.length; i++) field[i] /= mx;

      return field;
    }

    // ─── Mode and UI ──────────────────────────────────────────────────────

    _applyMode() {
      const m = this.getAttribute('mode') || 'viewer';
      if (this._nodeMesh)  this._nodeMesh.visible  = m !== 'rf';
      if (this._edgeMesh)  this._edgeMesh.visible  = m !== 'rf' && m !== 'lite';
      if (this._fieldMesh) this._fieldMesh.visible  = m === 'rf';
    }

    _cycleMode() {
      const ORDER = ['viewer', 'autopsy', 'rf', 'lite'];
      const cur   = this.getAttribute('mode') || 'viewer';
      this.setAttribute('mode', ORDER[(ORDER.indexOf(cur) + 1) % ORDER.length]);
    }

    _updateInfoPanel(data) {
      const el = this.shadowRoot.getElementById('hv-info-body');
      if (!el) return;
      const m = data.metadata || data.decomposition || {};
      // archetype / node_tier / silence_pressure may be strings (bundle path)
      // or dicts (live export-data path) — normalise both
      const _str = v => (v && typeof v === 'object') ? (v.label || JSON.stringify(v)) : (v ?? '');
      const _sil = v => (v && typeof v === 'object') ? v.normalized : v;
      const lines = [
        data.cluster_id
          ? `<b style="color:#c39bd3">${data.cluster_id}</b>` : '',
        m.archetype
          ? `<span style="color:#4af">${_str(m.archetype)}</span>` : '',
        m.silence_pressure != null
          ? `Silence: <b style="color:#f80">${(+_sil(m.silence_pressure)).toFixed(2)}</b>` : '',
        m.node_tier   ? `Tier: ${_str(m.node_tier)}`  : '',
        m.threat_score != null
          ? `Threat: ${(+m.threat_score).toFixed(3)}` : '',
        this._edgeStats && (this._edgeStats.observed || this._edgeStats.ghost || this._edgeStats.identity)
          ? `Arcs: <span style="color:#4f8dff">${this._edgeStats.observed || 0}</span> solid · <span style="color:#8ea7be">${this._edgeStats.ghost || 0}</span> ghost · <span style="color:#cd7bff">${this._edgeStats.identity || 0}</span> locked`
          : '',
        this._edgeStats && (this._edgeStats.pulse || this._edgeStats.flicker)
          ? `Rhythm: <span style="color:#4de6ff">${this._edgeStats.pulse || 0}</span> pulse · <span style="color:#ff9f5c">${this._edgeStats.flicker || 0}</span> flicker`
          : '',
        data._geoCount
          ? `Geo: <span style="color:#55efc4">${data._geoCount}</span> anchored`
          : '',
        `<span style="color:#444">${data._nodeCount || 0}N &middot; ${data._edgeCount || 0}E</span>`,
      ].filter(Boolean);
      el.innerHTML = lines.join('<br>');
    }

    _setStatus(msg) {
      const el = this.shadowRoot.getElementById('hv-status');
      if (el) el.textContent = msg;
    }

    // Basic instanced-mesh raycasting for node-click
    _pickNode(ev, mesh, nodes, THREE) {
      if (!this._renderer || !mesh) return;
      const canvas = this.shadowRoot.getElementById('hv-c');
      const rect   = canvas.getBoundingClientRect();
      const ndc    = new THREE.Vector2(
        ((ev.clientX - rect.left) / rect.width)  * 2 - 1,
        -((ev.clientY - rect.top) / rect.height) * 2 + 1,
      );
      const raycaster = new THREE.Raycaster();
      raycaster.setFromCamera(ndc, this._camera);
      const hits = raycaster.intersectObject(mesh);
      if (!hits.length) return;
      const node = nodes[hits[0].instanceId];
      if (node) this.dispatchEvent(new CustomEvent('node-click', { bubbles: true, detail: node }));
    }
  }

  if (!customElements.get('hypergraph-viewer')) {
    customElements.define('hypergraph-viewer', HypergraphViewer);
  }
})();
