/**
 * CesiumHypergraphGlobe — Operational Intelligence Surface
 *
 * Architecture:
 *   Cesium  → globe, terrain, geospatial truth (ECEF coordinate space)
 *   Three.js→ hypergraph overlay: instanced nodes + arc mesh, all in ECEF
 *   Camera sync runs every frame: Cesium cam → Three.js cam matrices
 *   GPU buffers mutated in place — NO scene rebuilds on update
 *
 * Event model (SocketIO):
 *   subscribe_edges  → edges stream  (edge_update)
 *   graph_event_bus  → node_update, hyperedge_update, convergence_bloom
 *
 * Node rendering: THREE.InstancedMesh, one draw call for all nodes
 * Arc rendering:  THREE.LineSegments + InstancedBufferGeometry — GPU SLERP in vertex
 *                 shader; 50k simultaneous arcs; temporal decay via lastSeen uniform
 * Hyperedge:      THREE.Points cluster flare columns (surface-projected, above-terrain)
 * Propagation:    shader-side adjacency texture → wave from selected node
 *
 * Recon entity stacking: density → altitude (log scale).
 * Confidence culling: edges/nodes with confidence < CONF_CULL_THRESHOLD hidden.
 * Temporal batching: GPU buffers flushed every BATCH_INTERVAL_MS (100ms).
 */

/* ─── Constants ──────────────────────────────────────────────────────────── */
const EARTH_RADIUS_M      = 6_371_000;
const MAX_NODES           = 50_000;
// GPU Instanced Arc Renderer — one InstancedBufferGeometry draw call for all arcs.
// Template: ARC_TEMPLATE_PTS-point spine; GPU slerp computes ECEF positions per vertex.
// At 50k instances × 31 segments × 2 verts = 3.1M GPU verts; instance data ≈ 2MB.
const MAX_ARC_INSTANCES   = 50_000;
const ARC_TEMPLATE_PTS    = 32;          // spine resolution (power-of-2 friendly)
const ARC_EDGE_DECAY_START = 45.0;       // seconds idle before fade begins
const ARC_EDGE_DECAY_RATE  = 0.04;       // exp decay coefficient after fade start
const MAX_HYPEREDGES      = 1_000;
const MAX_HE_PARTICLES    = 32;          // particles per hyperedge
const CLUSTER_FLARE_BASE_ALT = 12_000;   // keep cluster flares above terrain / globe skin
const CONF_CULL_THRESHOLD = 0.20;
const BATCH_INTERVAL_MS   = 100;
const RECON_HEIGHT_SCALE  = 50_000;     // metres per log-unit
const ADJ_TEX_SIZE        = 256;        // adjacency texture: 256×256 nodes max
const EDGE_META_CHANNELS  = 4;          // RGBA → confidence, entropy, rf_corr, cluster_id
const DECAY_RATE          = 0.3;
const PROPAGATION_SPEED   = 1.5;
const MAX_RF_EMITTERS     = 512;      // max simultaneous RF shell emitters
const RF_DEFAULT_RADIUS   = 120000;   // 120km — visible but won't overpower labels
const MAX_EDGE_BEAMS      = 128;    // max directional beam instances
const MAX_STROBES         = 256;    // GPU shockwave event ring buffer
const STROBE_FLOATS       = 16;    // position(3) + t0(1) + energy(1) + type(1) + dir(2) + rfFingerprint(8) → 16 floats per strobe

/* ───────────────────────────────────────────────────────────────────────
 * Major cities — swarm spawn reference points
 * ─────────────────────────────────────────────────────────────────────── */
const MAJOR_CITIES = [
  { name: 'Houston',     lat: 29.7604, lon: -95.3698 },
  { name: 'New York',    lat: 40.7128, lon: -74.0060 },
  { name: 'London',      lat: 51.5074, lon: -0.1278  },
  { name: 'Tokyo',       lat: 35.6762, lon: 139.6503 },
  { name: 'Beijing',     lat: 39.9042, lon: 116.4074 },
  { name: 'Moscow',      lat: 55.7558, lon:  37.6173 },
  { name: 'Paris',       lat: 48.8566, lon:   2.3522 },
  { name: 'Los Angeles', lat: 34.0522, lon: -118.2437},
  { name: 'Sydney',      lat: -33.8688,lon: 151.2093 },
  { name: 'Cape Town',   lat: -33.9249,lon:  18.4241 },
  { name: 'Dubai',       lat: 25.2048, lon:  55.2708 },
  { name: 'Mumbai',      lat: 19.0760, lon:  72.8777 },
];

/* ───────────────────────────────────────────────────────────────────────
 * UAV RF-cone shaders (minimal — additive pulsing cone, no custom attrs)
 * ─────────────────────────────────────────────────────────────────────── */
const UAV_CONE_VERT = /* glsl */`
  varying vec2 vUV;
  void main() {
    vUV = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

// Soft ring fade — subtle downward C2 link, not a solid blob
const UAV_CONE_FRAG = /* glsl */`
  precision mediump float;
  uniform float uTime;
  varying vec2 vUV;
  void main() {
    // vUV.y: 0 = tip (UAV), 1 = base (ground)
    float radialFade = vUV.y * (1.0 - vUV.y) * 4.0;   // fade at tip and base
    float pulse = 0.5 + 0.5 * sin(uTime * 3.5 - vUV.y * 6.0);
    float alpha = radialFade * pulse * 0.09;
    if (alpha < 0.005) discard;
    gl_FragColor = vec4(0.05, 0.9, 1.0, alpha);
  }
`;

// Individual RTS drone palette — clearly distinguishable colours per slot
const UAV_PALETTE = [
  0x00e5ff,  // 0  cyan     — default recon
  0xff6b35,  // 1  orange   — C2 relay
  0xffee58,  // 2  yellow   — EW / jammer
  0x69f0ae,  // 3  green    — support
  0xff4081,  // 4  pink     — high-threat
  0xb388ff,  // 5  violet   — sensor
  0x40c4ff,  // 6  sky      — recon B
  0xccff90,  // 7  lime     — utility
];

// Strobe type enum — encodes meaning into the shockwave waveform
const STROBE_TYPE = Object.freeze({
  NETWORK:      0.0,   // spherical ripple
  RF:           1.0,   // directional cone + ring
  C2:           2.0,   // pulsing + rotating wedge
  UAV:          3.0,   // forward-propagating trail
  ANOMALY:      4.0,   // jagged multi-frequency ripple
  CLUSTER:      5.0,   // cluster intel emission — radiates intelligence outward
  INTERFERENCE: 6.0,   // non-physical motion distortion (ROUTED / VPN teleport)
  PATH:         7.0,   // ASN transit hop — tight bright pulse for hop-by-hop animation
  CONFLICT:     8.0,   // IX peering conflict — white-hot pulsing contention
  PHANTOM:      9.0,   // Phantom IX attractor — inward-pulsing ghost convergence node
});

/* ─── FrameBudgetGovernor — auto-tunes shader quality from GPU timer queries ─ */
class FrameBudgetGovernor {
  constructor() {
    this._quality      = 1.0;          // current quality scalar [0.25, 1.5]
    this._history      = new Float32Array(8).fill(16.6); // rolling 8-frame GPU times
    this._histIdx      = 0;
    this._query        = null;         // pending WebGL timer query object
    this._queryActive  = false;        // true between beginQuery/endQuery
    this._ext          = null;         // EXT_disjoint_timer_query_webgl2 or null
    this._extChecked   = false;
  }

  _getExt(gl) {
    if (!this._extChecked) {
      this._ext = gl.getExtension('EXT_disjoint_timer_query_webgl2') || null;
      this._extChecked = true;
    }
    return this._ext;
  }

  beginFrame(gl) {
    const ext = this._getExt(gl);
    // Skip if no extension, or a query is already in flight (active or awaiting poll)
    if (!ext || this._query) return;
    this._query = gl.createQuery();
    gl.beginQuery(ext.TIME_ELAPSED_EXT, this._query);
    this._queryActive = true;
  }

  endFrame(gl) {
    const ext = this._getExt(gl);
    // Only end if a query was explicitly started this frame
    if (!ext || !this._query || !this._queryActive) return;
    gl.endQuery(ext.TIME_ELAPSED_EXT);
    this._queryActive = false;
  }

  /** Returns true when a result was consumed. */
  poll(gl) {
    const ext = this._getExt(gl);
    // Don't poll while query is still active (endFrame not yet called)
    if (!ext || !this._query || this._queryActive) return false;
    const available = gl.getQueryParameter(this._query, gl.QUERY_RESULT_AVAILABLE);
    const disjoint  = gl.getParameter(ext.GPU_DISJOINT_EXT);
    if (!available || disjoint) {
      if (disjoint) { gl.deleteQuery(this._query); this._query = null; }
      return false;
    }
    const ns = gl.getQueryParameter(this._query, gl.QUERY_RESULT);
    gl.deleteQuery(this._query);
    this._query = null;
    this._adjust(ns / 1e6); // convert ns → ms
    return true;
  }

  _adjust(ms) {
    this._history[this._histIdx % 8] = ms;
    this._histIdx++;
    // Rolling mean over last 8 frames
    let sum = 0;
    for (let i = 0; i < 8; i++) sum += this._history[i];
    const mean = sum / 8;

    if (mean > 16.6) {
      this._quality *= 0.92;
    } else {
      this._quality = Math.min(1.5, this._quality * 1.03);
    }
    this._quality = Math.max(0.25, Math.min(1.5, this._quality));
  }

  get quality() { return this._quality; }
}

/* ─── DeckBridge — syncs Cesium camera matrices into a Deck.gl instance ────
 *
 * Deck.gl is optional; if window.deck is absent the bridge no-ops.
 *
 * Usage:
 *   const bridge = new DeckBridge(cesiumViewer, deckInstance);
 *   bridge.sync();   // call each frame after Cesium renders
 */
class DeckBridge {
  constructor(cesiumViewer, deckInstance) {
    this._viewer = cesiumViewer;
    this._deck   = deckInstance;
    this._frustumPlanes = new Float32Array(24); // 6 planes × 4 components
  }

  sync() {
    if (!this._deck || !this._viewer) return;
    try {
      this._syncViewState();
      this._extractFrustumPlanes();
    } catch (e) {
      // Deck.gl may not be fully initialized yet; silently skip
    }
  }

  _syncViewState() {
    const cv   = this._viewer.camera;
    const cart = Cesium.Cartographic.fromCartesian(cv.positionWC);
    const lon  = Cesium.Math.toDegrees(cart.longitude);
    const lat  = Cesium.Math.toDegrees(cart.latitude);
    const h    = cart.height;

    // Estimate Deck.gl zoom from Cesium camera height (empirical fit)
    const zoom = Math.max(0, Math.min(22, Math.log2(35200000 / Math.max(h, 1))));

    // Extract bearing and pitch from Cesium camera
    const bearing = Cesium.Math.toDegrees(cv.heading);
    const pitch   = Cesium.Math.toDegrees(cv.pitch) + 90; // Cesium pitch 0 = horizontal

    this._deck.setProps({
      viewState: {
        longitude: lon,
        latitude: lat,
        zoom,
        bearing,
        pitch: Math.max(0, Math.min(85, pitch)),
        transitionDuration: 0
      }
    });
  }

  _extractFrustumPlanes() {
    // Extract 6 frustum planes from Cesium's culling volume
    // for optional injection into custom Deck.gl shaders
    try {
      const culling = this._viewer.scene.frameState.cullingVolume;
      const planes  = culling.planes;
      for (let i = 0; i < Math.min(6, planes.length); i++) {
        this._frustumPlanes[i * 4 + 0] = planes[i].x;
        this._frustumPlanes[i * 4 + 1] = planes[i].y;
        this._frustumPlanes[i * 4 + 2] = planes[i].z;
        this._frustumPlanes[i * 4 + 3] = planes[i].w;
      }
    } catch (_) {}
  }

  /** Float32Array(24): 6 frustum planes each as [nx, ny, nz, d].
   *  Pass as `uniform vec4 uFrustumPlanes[6]` in Deck custom shaders. */
  get frustumPlanes() { return this._frustumPlanes; }

  /** Attach a new Deck.gl instance at runtime */
  setDeck(deckInstance) { this._deck = deckInstance; }
}

/* ─── Node GLSL (instanced, ECEF positions) ─────────────────────────────── */
const NODE_VERT = /* glsl */`
  precision highp float;

  attribute vec3  instancePosition;   // ECEF metres
  attribute float instanceId;
  attribute float instanceConf;       // 0–1
  attribute float instanceLastUpdate; // unix seconds
  attribute float instanceCluster;    // cluster size (for recon height)
  attribute vec3  instanceColor;
  attribute float instanceViolations; // packed: dns|c2|rst|port in nibbles
  attribute float instanceLifecycle;  // 0=ghost → 1=active

  uniform float uTime;
  uniform float uSelectedId;          // -1 = none
  uniform sampler2D uAdjTex;          // adjacency distance texture
  uniform float uViewHeight;          // canvas height in pixels

  varying float vConf;
  varying float vHighlight;
  varying vec3  vColor;
  varying float vViolations;
  varying float vLifecycle;
  varying vec2  vUv;
  varying vec3  vNormal;
  varying float vFarFade;

  // Decode graph distance from adjacency texture
  float graphDistance(float nodeId) {
    float u = mod(nodeId, float(${ADJ_TEX_SIZE})) / float(${ADJ_TEX_SIZE});
    float v = floor(nodeId / float(${ADJ_TEX_SIZE})) / float(${ADJ_TEX_SIZE});
    return texture2D(uAdjTex, vec2(u, v)).r * 32.0;
  }

  void main() {
    vUv        = uv;
    vNormal    = vec3(0.0, 0.0, 1.0);
    vConf      = instanceConf;
    vColor     = instanceColor;
    vViolations= instanceViolations;
    vLifecycle = instanceLifecycle;

    float emerge = smoothstep(0.0, 0.85, instanceLifecycle);

    // Graph-distance propagation wave from selected node
    float dist = (uSelectedId < 0.0) ? 99.0 : graphDistance(instanceId);
    float wave = exp(-dist * ${DECAY_RATE}) * sin(uTime * ${PROPAGATION_SPEED} - dist * 1.2 + 0.1);
    vHighlight  = clamp(wave, -1.0, 1.0);

    // Recon density → altitude (height above ECEF surface)
    float height = log(max(instanceCluster, 1.0)) * float(${RECON_HEIGHT_SCALE});
    vec3 ecefNorm = length(instancePosition) > 0.0
                  ? normalize(instancePosition)
                  : vec3(0.0, 1.0, 0.0);
    vec3 worldPos = instancePosition + ecefNorm * height;

    float pulse = (uSelectedId == instanceId) ? 1.0 + 0.15 * sin(uTime * 7.0) : 1.0;
    float scale = min((0.3 + 0.7 * pow(instanceConf, 0.4)) * emerge * pulse, 1.0);

    // Screen-space billboard: project centre to clip space, offset quad vertices in NDC
    vec4 centerClip = projectionMatrix * modelViewMatrix * vec4(worldPos, 1.0);

    // Clip nodes behind camera before perspective divide
    if (centerClip.w < 0.001) {
      gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      vFarFade = 0.0;
      return;
    }

    // Depth fade: smooth out as node approaches far clip plane (horizon limb)
    float ndcDepth = centerClip.z / centerClip.w;
    vFarFade = 1.0 - smoothstep(0.82, 0.97, ndcDepth);

    float ndcHalfSize = 14.0 * scale / (uViewHeight * 0.5);
    gl_Position = centerClip + vec4(position.xy * ndcHalfSize * centerClip.w, 0.0, 0.0);
  }
`;

const NODE_FRAG = /* glsl */`
  precision highp float;

  uniform float uTime;
  varying float vConf;
  varying float vHighlight;
  varying vec3  vColor;
  varying float vViolations;
  varying float vLifecycle;
  varying vec2  vUv;
  varying vec3  vNormal;
  varying float vFarFade;

  float hash(float n) { return fract(sin(n) * 43758.5453); }

  void main() {
    // Clip to circle and derive fake-sphere normal from disk UV
    vec2 centeredUV = vUv * 2.0 - 1.0;
    float d = length(centeredUV);
    if (d > 1.0) discard;

    vec3 fakeNormal = normalize(vec3(centeredUV, sqrt(max(0.001, 1.0 - d * d))));
    float ndl  = max(0.0, dot(fakeNormal, normalize(vec3(0.5, 0.8, 0.5))));
    float light= 0.35 + 0.65 * ndl;
    vec3 color = vColor * light;

    // Highlight wave tinting
    if (vHighlight > 0.7) {
      color = mix(color, vec3(1.0, 0.38, 0.08), vHighlight * 0.85);
    } else if (vHighlight > 0.2) {
      color = mix(color, vec3(0.15, 0.55, 1.0), vHighlight * 0.6);
    }

    // Violation signals (packed nibbles → bitmask floats)
    float vpack = vViolations;
    float dns  = step(0.5, mod(vpack,       2.0));
    float c2   = step(0.5, mod(vpack / 2.0, 2.0));
    float rst  = step(0.5, mod(vpack / 4.0, 2.0));
    float port = step(0.5, mod(vpack / 8.0, 2.0));

    if (dns  > 0.5) { float f = step(0.45, hash(floor(uTime*11.0))); color = mix(color, vec3(0.1,1.0,0.35), 0.4*f); }
    if (c2   > 0.5) { float b = 0.5+0.5*sin(uTime*1.8); color = mix(color, vec3(1.0,0.04,0.04), 0.5*b); }
    if (rst  > 0.5) { float s = pow(max(0.0,sin(uTime*22.0)),10.0); color += vec3(1.0,0.0,0.0)*s*1.5; }
    if (port > 0.5) { float bd = step(0.5, fract(vUv.y*3.5+uTime*0.4)); color = mix(color, vec3(1.0,0.58,0.0), 0.3*bd); }

    // Anomaly rim halo using disk radius as proxy
    float rim = pow(d, 3.0);
    color += vec3(1.0,0.25,0.0) * rim * vConf * 0.4;

    float alpha = smoothstep(0.0, 0.35, vLifecycle) * max(0.1, vConf);
    alpha = min(alpha, 0.75);   // cap opacity so labels remain visible through nodes
    alpha *= smoothstep(1.0, 0.8, d);  // soft anti-aliased circle edge
    alpha *= vFarFade;                 // fade at horizon / far clip plane
    gl_FragColor = vec4(clamp(color, 0.0, 2.0), alpha);
  }
`;

/* ─── Arc GLSL — GPU Instanced (InstancedBufferGeometry, ECEF) ──────────────
 * Template geometry: ARC_TEMPLATE_PTS spine vertices with aT 0→1.
 * Per-instance attributes: iStart/iEnd ECEF, iConf, iEntropy, iRfCorr,
 *   iShadow, iAnomaly, iTimeOff (pulse stagger), iLastSeen (temporal decay), iEdgeId.
 * Vertex shader performs spherical linear interpolation on the unit sphere
 * and applies proportional arc lift (sin curve, max ~300km).
 */
const ARC_VERT = /* glsl */`
  precision highp float;

  // Template
  attribute float aT;           // 0–1 position along arc spine

  // Per-instance
  attribute vec3  iStart;       // ECEF position A
  attribute vec3  iEnd;         // ECEF position B
  attribute float iConf;
  attribute float iEntropy;
  attribute float iRfCorr;
  attribute float iShadow;
  attribute float iAnomaly;     // 0–1 constraint violation score
  attribute float iTimeOff;     // per-instance pulse stagger 0–1
  attribute float iLastSeen;    // performance.now()*0.001 at last update
  attribute float iEdgeId;

  uniform float uTime;
  uniform float uSelectedId;
  uniform float uQuality;

  varying float vConf;
  varying float vEntropy;
  varying float vRfCorr;
  varying float vT;
  varying float vShadow;
  varying float vHighlight;
  varying float vAge;
  varying float vTimeOff;
  varying float vAnomaly;
  varying float vFacing;   // dot(surfaceNormal, toCam): >0 = front, <0 = back

  void main() {
    vConf      = iConf;
    vEntropy   = iEntropy;
    vRfCorr    = iRfCorr;
    vT         = aT;
    vShadow    = iShadow;
    vHighlight = (uSelectedId == iEdgeId) ? 1.0 : 0.0;
    vAge       = max(0.0, uTime - iLastSeen);
    vTimeOff   = iTimeOff;
    vAnomaly   = iAnomaly;

    // ── GPU spherical linear interpolation in ECEF ────────────────────────
    vec3 nA = normalize(iStart);
    vec3 nB = normalize(iEnd);

    float cosTheta = clamp(dot(nA, nB), -1.0, 1.0);
    float theta    = acos(cosTheta);
    float sinTheta = sin(theta);

    vec3 slerpDir;
    if (sinTheta < 0.0005) {
      // Nearly antipodal or coincident — linear fallback
      slerpDir = normalize(mix(nA, nB, aT));
    } else {
      slerpDir = (sin((1.0 - aT) * theta) * nA + sin(aT * theta) * nB) / sinTheta;
    }

    // Earth radius from source point magnitude
    float R = length(iStart);
    if (R < 6200000.0) R = 6371000.0;  // safety clamp

    // Arc lift: proportional to arc chord length (longer arc = higher arc)
    float arcLen = theta * R;
    float liftFactor = min(arcLen * 0.025, 350000.0);  // max 350km lift
    float lift = sin(3.14159265 * aT) * liftFactor;

    vec3 worldPos = slerpDir * (R + lift);
    gl_Position   = projectionMatrix * modelViewMatrix * vec4(worldPos, 1.0);

    // ── Hemisphere facing: cameraPosition is Three.js built-in (ECEF) ────────
    // Surface normal = normalize(worldPos) since globe centre = origin.
    // vFacing interpolated per fragment → back-hemisphere segments fade/discard.
    vFacing = dot(normalize(worldPos), normalize(cameraPosition - worldPos));
  }
`;

const ARC_FRAG = /* glsl */`
  precision highp float;

  uniform float uTime;
  uniform float uQuality;

  varying float vConf;
  varying float vEntropy;
  varying float vRfCorr;
  varying float vT;
  varying float vShadow;
  varying float vHighlight;
  varying float vAge;
  varying float vTimeOff;
  varying float vAnomaly;
  varying float vFacing;

  void main() {
    // ── Back-hemisphere cull: discard segments behind the globe ───────────────
    // Wider transition band + 0.03 bias avoids binary threshold flicker at limb.
    if (vFacing < -0.15) discard;
    float limbFade = smoothstep(-0.15, 0.20, vFacing + 0.03);

    // ── Temporal decay — arcs fade after ARC_EDGE_DECAY_START idle seconds ─
    float freshness = 1.0;
    float decayStart = ${ARC_EDGE_DECAY_START.toFixed(1)};
    float decayRate  = ${ARC_EDGE_DECAY_RATE.toFixed(3)};
    if (vAge > decayStart) {
      freshness = exp(-(vAge - decayStart) * decayRate);
    }
    if (freshness < 0.02) discard;

    // ── Speed driven by signal confidence + entropy activity ──────────────
    float baseSpeed   = 0.35 + vConf * 1.2;
    float entropyBoost = (1.0 - vEntropy) * 0.5;  // low entropy = fast beacon
    float speed        = baseSpeed + entropyBoost;

    // ── Multi-pulse: quality-gated pulse count; iTimeOff desynchronises instances ─
    float pulse   = 0.0;
    float HEAD_W  = 0.07;
    float TAIL_W  = 0.22;
    {
      float offset = vTimeOff;
      float t0 = fract(uTime * speed + offset);
      float d0 = vT - t0;
      pulse = max(pulse, smoothstep(-HEAD_W, 0.0, d0) * smoothstep(TAIL_W, 0.0, d0));
    }
    if (uQuality > 0.5) {
      float offset = vTimeOff + 0.333;
      float t1 = fract(uTime * speed + offset);
      float d1 = vT - t1;
      pulse = max(pulse, smoothstep(-HEAD_W, 0.0, d1) * smoothstep(TAIL_W, 0.0, d1));
    }
    if (uQuality > 0.9) {
      float offset = vTimeOff + 0.666;
      float t2 = fract(uTime * speed + offset);
      float d2 = vT - t2;
      pulse = max(pulse, smoothstep(-HEAD_W, 0.0, d2) * smoothstep(TAIL_W, 0.0, d2));
    }

    // ── Anomaly: pulse vibration — frequency modulated, "angry" at high scores
    // Jitter adds temporal instability: clean arcs flow smoothly, anomalous arcs buzz
    float jitter = sin(uTime * (6.0 + vAnomaly * 20.0 * uQuality)) * vAnomaly * 0.15 * uQuality;
    pulse = pulse * (1.0 + vAnomaly * 0.55) + jitter;

    // ── Ambient base glow ─────────────────────────────────────────────────
    float ambient = vConf * 0.12;

    // ── Semantic color: entropy → cyan / low-entropy amber / RF-corr green ─
    float beaconStrength = 1.0 - vEntropy;
    vec3 outboundColor = vec3(0.0,  0.75, 1.0);   // cyan   — data exfil
    vec3 inboundColor  = vec3(1.0,  0.45, 0.05);  // amber  — C2 beacon
    vec3 rfColor       = vec3(0.2,  1.0,  0.7);   // green  — RF-correlated
    vec3 base = mix(outboundColor, inboundColor, beaconStrength * 0.7);
    base = mix(base, rfColor, vRfCorr * 0.6);

    // ── Anomaly color: gamma-curved bleed — low noise invisible, high scores explode
    float aCurved = pow(vAnomaly, 2.2);  // perceptual gamma: compresses noise, amplifies violations
    vec3 anomalyColor = vec3(1.0, 0.08, 0.22);
    base = mix(base, anomalyColor, aCurved * 0.85);

    // ── Selection highlight ────────────────────────────────────────────────
    if (vHighlight > 0.5) {
      base  = mix(base, vec3(1.0, 0.1, 0.9), 0.75);
      pulse = max(pulse, 0.4);
    }

    // ── Outer glow + end-feathering ────────────────────────────────────────
    float glowEnvelope = pulse * 0.55 + ambient;
    float endFade      = smoothstep(0.0, 0.06, vT) * smoothstep(1.0, 0.94, vT);
    glowEnvelope *= endFade;

    // ── Shadow graph: dashed ghost arcs ───────────────────────────────────
    float solidFactor = vShadow > 0.5 ? step(0.5, fract(vT * 8.0)) : 1.0;
    float shadowDim   = vShadow > 0.5 ? 0.3 : 1.0;

    float alpha = glowEnvelope * solidFactor * shadowDim * freshness * limbFade;
    if (alpha < 0.005) discard;

    gl_FragColor = vec4(base * (1.0 + pulse * 0.6), clamp(alpha, 0.0, 1.0));
  }
`;

/* ─── Hyperedge cluster flare GLSL ───────────────────────────────────────── */
const HE_VERT = /* glsl */`
  precision highp float;

  attribute float aParticleId;
  attribute float aHEConf;
  attribute float aHEHeight;
  attribute float aHEWidth;

  uniform float uTime;

  varying float vConf;
  varying float vAlpha;
  varying float vRise;

  void main() {
    vConf   = aHEConf;
    float rings = 4.0;
    float bands = float(${MAX_HE_PARTICLES}) / rings;
    float band  = floor(aParticleId / rings);
    float ringId = mod(aParticleId, rings);
    float rise = bands > 1.0 ? band / (bands - 1.0) : 0.0;
    vRise = rise;

    vec3 up = length(position) > 0.0 ? normalize(position) : vec3(0.0, 1.0, 0.0);
    vec3 refAxis = abs(up.z) > 0.92 ? vec3(0.0, 1.0, 0.0) : vec3(0.0, 0.0, 1.0);
    vec3 east = normalize(cross(refAxis, up));
    vec3 north = normalize(cross(up, east));

    float angle = ringId * 6.28318 / rings
                + uTime * (0.35 + aHEConf * 0.85)
                + band * 0.42;
    float breathe = 0.88 + 0.12 * sin(uTime * (1.6 + aHEConf * 2.4) + aParticleId * 0.37);
    float width = mix(aHEWidth * 0.38, aHEWidth, smoothstep(0.0, 1.0, rise));
    width *= mix(1.15, 0.75, rise) * breathe;

    float lift = float(${CLUSTER_FLARE_BASE_ALT}) + aHEHeight * rise;
    float shimmer = sin(uTime * 3.2 + aParticleId * 1.3) * (3500.0 + aHEConf * 4500.0) * (1.0 - rise * 0.55);
    vec3 orbit = (east * cos(angle) + north * sin(angle)) * width;
    vec3 worldPos = position + up * (lift + shimmer) + orbit;

    vec4 mvPos = modelViewMatrix * vec4(worldPos, 1.0);
    gl_Position = projectionMatrix * mvPos;
    float distScale = clamp(1200000.0 / max(1.0, -mvPos.z), 0.55, 4.0);
    gl_PointSize = (6.0 + aHEConf * 10.0) * mix(0.85, 1.65, rise) * distScale;
    vAlpha = aHEConf * (0.35 + 0.65 * (1.0 - abs(rise - 0.68)));
  }
`;

const HE_FRAG = /* glsl */`
  precision highp float;

  uniform vec3 uColor;
  varying float vConf;
  varying float vAlpha;
  varying float vRise;

  void main() {
    vec2 uv   = gl_PointCoord - 0.5;
    float d = length(uv);
    float halo = 1.0 - smoothstep(0.28, 0.50, d);
    float core = 1.0 - smoothstep(0.08, 0.34, d);
    if (halo < 0.01) discard;

    vec3 colLow = uColor;
    vec3 colHigh = vec3(1.0, 0.62, 0.18);
    vec3 color = mix(colLow, colHigh, smoothstep(0.0, 1.0, vRise));
    color = mix(color, vec3(1.0, 0.96, 0.90), core * 0.35);

    float alpha = (halo * 0.45 + core * 0.70) * vAlpha;
    gl_FragColor = vec4(color, alpha);
  }
`;

/* ─── RF Volumetric Shell GLSL — billboard sphere shells per emitter ────── */
const RF_VOL_VERT = /* glsl */`
  precision highp float;

  attribute vec2 aUV;           // quad UV: (-1,-1) to (1,1)
  attribute vec3 iCenter;       // ECEF emitter position
  attribute float iRadius;      // shell radius (world units: metres ECEF)
  attribute float iIntensity;   // 0–1 signal strength
  attribute float iAnomaly;     // 0–1 CVE score
  attribute float iPhase;       // per-emitter time phase stagger

  uniform float uTime;
  uniform float uQuality;

  varying vec2  vUV;
  varying float vIntensity;
  varying float vAnomaly;
  varying float vPhase;

  void main() {
    vUV        = aUV;
    vIntensity = iIntensity;
    vAnomaly   = iAnomaly;
    vPhase     = iPhase;

    // Expand billboard quad around emitter center in view space
    vec4 clipCenter = projectionMatrix * modelViewMatrix * vec4(iCenter, 1.0);
    float r = iRadius * (1.0 + 0.08 * sin(uTime * 2.5 + iPhase)); // breathing
    vec2 offset = aUV * r * uQuality;
    // Offset in clip space proportional to screen
    gl_Position = clipCenter + vec4(offset * clipCenter.w, 0.0, 0.0);
  }
`;

const RF_VOL_FRAG = /* glsl */`
  precision highp float;

  uniform float uTime;
  uniform float uQuality;

  varying vec2  vUV;
  varying float vIntensity;
  varying float vAnomaly;
  varying float vPhase;

  void main() {
    float dist = length(vUV);  // 0 = center, 1 = edge
    if (dist > 1.0) discard;

    // Shell ring: peak at distance ~0.75, fall off on both sides
    float shellR   = 0.75;
    float shellW   = mix(0.18, 0.10, uQuality); // thinner at high quality
    float ring     = exp(-pow((dist - shellR) / shellW, 2.0));

    // Wave propagation: outward-moving ripple
    float wave = 0.5 + 0.5 * sin(dist * 8.0 - uTime * 4.0 + vPhase);
    float field = ring * wave;

    // Temporal dithering: alternate pixels each frame for ~2× perf on low quality
    if (uQuality < 0.5) {
      float checker = mod(gl_FragCoord.x + gl_FragCoord.y, 2.0);
      float frameCheck = mod(uTime * 60.0, 2.0);
      if (abs(checker - frameCheck) < 0.5) discard;
    }

    // Color: clean signal → blue-cyan, anomalous → red-magenta bleed
    float aCurved = pow(vAnomaly, 2.2);
    vec3 cleanColor   = vec3(0.1, 0.55, 1.0);
    vec3 anomalyColor = vec3(1.0, 0.08, 0.22);
    vec3 color = mix(cleanColor, anomalyColor, aCurved * 0.9);

    // Anomaly boost: contaminated fields glow brighter
    float boost = 1.0 + aCurved * 1.5;

    float alpha = field * vIntensity * boost * 0.15;   // reduced: was 0.35; shells hint, not dominate
    if (alpha < 0.005) discard;

    gl_FragColor = vec4(color * (1.0 + field * 0.2), clamp(alpha, 0.0, 0.4));
  }
`;

/* ─── RF Edge Beam Shaders ──────────────────────────────────────────────────
 * Directional beam along a graph edge: flow-aligned wave streak.
 * Each instance is a billboard quad stretched src→dst.
 * Anomaly amplifies intensity and shifts color toward red-magenta.
 */
const RF_BEAM_VERT = /* glsl */`
  precision highp float;

  attribute vec2  aUV;         // quad: x=-1..1 (along beam), y=-1..1 (width)
  attribute vec3  iSrc;        // ECEF source position
  attribute vec3  iDst;        // ECEF destination position
  attribute float iIntensity;
  attribute float iAnomaly;
  attribute float iPhase;

  uniform float uTime;
  uniform float uQuality;

  varying vec2  vUV;
  varying float vIntensity;
  varying float vAnomaly;

  void main() {
    vUV        = aUV;
    vIntensity = iIntensity;
    vAnomaly   = iAnomaly;

    // Interpolate position along beam direction
    float t = aUV.x * 0.5 + 0.5;      // 0 at src, 1 at dst
    vec3 beamPos = mix(iSrc, iDst, t);

    // Beam width: narrow with quality scaling, anomaly widens slightly
    float width = 80000.0 * (1.0 + iAnomaly * 0.5) * uQuality;

    // Perpendicular offset: compute view-space perp to beam direction
    vec4 clipSrc = projectionMatrix * modelViewMatrix * vec4(iSrc, 1.0);
    vec4 clipDst = projectionMatrix * modelViewMatrix * vec4(iDst, 1.0);
    vec2 beamDir2D = normalize(clipDst.xy / clipDst.w - clipSrc.xy / clipSrc.w);
    vec2 perp2D = vec2(-beamDir2D.y, beamDir2D.x);

    vec4 clipPos = projectionMatrix * modelViewMatrix * vec4(beamPos, 1.0);
    clipPos.xy += perp2D * aUV.y * width * clipPos.w / 6371000.0;

    gl_Position = clipPos;
  }
`;

const RF_BEAM_FRAG = /* glsl */`
  precision highp float;

  uniform float uTime;
  uniform float uQuality;

  varying vec2  vUV;
  varying float vIntensity;
  varying float vAnomaly;

  void main() {
    // Cross-section: Gaussian falloff from beam center
    float width  = exp(-vUV.y * vUV.y * 18.0);

    // Longitudinal flow: animated wave along beam direction
    float t       = vUV.x * 0.5 + 0.5;
    float flowFreq = 4.0 + vAnomaly * 10.0;
    float flow    = 0.5 + 0.5 * sin(t * 6.28 * 3.0 - uTime * flowFreq);

    // Anomaly gamma curve (matches arc shader)
    float aCurved = pow(vAnomaly, 2.2);

    // Color: directional flow cyan → anomaly red-magenta
    vec3 flowColor    = vec3(0.0, 0.85, 1.0);
    vec3 anomalyColor = vec3(1.0, 0.08, 0.22);
    vec3 color = mix(flowColor, anomalyColor, aCurved * 0.85);

    // Fade at beam endpoints (avoid hard cuts)
    float endFade = smoothstep(0.0, 0.12, t) * smoothstep(1.0, 0.88, t);

    float alpha = width * flow * vIntensity * endFade * 0.45;
    if (alpha < 0.005) discard;

    gl_FragColor = vec4(color * (1.0 + flow * 0.4), clamp(alpha, 0.0, 1.0));
  }
`;

/* ─── Heatmap GLSL — three-pass GPU temporal splat field ─────────────────────
 * Pass 0 (BLIT):  ping-pong copy of current RT → previous RT for velocity.
 * Pass 1 (SPLAT): projects each node as a multi-channel anisotropic Gaussian
 *   into a half-resolution FloatType RGB render target (additive accumulation).
 *   R=shadow/C2, G=anomaly/confidence, B=entropy/cluster.
 *   Back-hemisphere nodes are culled in vertex shader; limb nodes fade out.
 * Pass 2 (COMP):  full-screen composite — reads curr+prev, computes per-channel
 *   velocity, colorizes semantically, adds predictive bloom.
 *   Ray-sphere Earth intersection masks the composite to the visible hemisphere only.
 */
const HEATMAP_SPLAT_VERT = /* glsl */`
  precision highp float;

  attribute vec3  aPos;
  attribute float aConf;
  attribute float aLifecycle;
  attribute float aShadow;    // stealth / C2 signal  0–1
  attribute float aAnomaly;   // violation / anomaly  0–1
  attribute float aEntropy;   // cluster entropy      0–1
  attribute float aAngle;     // smear orientation (radians)

  uniform float uSplatSize;
  uniform float uViewHeight;

  varying float vShadow;
  varying float vAnomaly;
  varying float vEntropy;
  varying float vAngle;
  varying float vFacing;   // dot(surfaceNormal, toCam): 1=front, 0=limb, <0=back

  void main() {
    // ── Hemisphere cull: discard nodes on the back of the globe ──────────
    // cameraPosition is Three.js built-in (world-space camera ECEF position)
    vec3 nodeNormal = normalize(aPos);           // outward surface normal (ECEF)
    vec3 toCam = normalize(cameraPosition - aPos);
    float facing = dot(nodeNormal, toCam);

    // Hard cull on back hemisphere — wider buffer so small camera jitter
    // doesn't flip fragments across the threshold (strobe fix).
    if (facing < -0.15) {
      gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      gl_PointSize = 0.0;
      return;
    }
    vFacing  = facing;
    vShadow  = aShadow;
    vAnomaly = aAnomaly;
    vEntropy = aEntropy;
    vAngle   = aAngle;

    // ── Behind-camera cull ────────────────────────────────────────────────
    // Elevate splat 8km above surface so the Gaussian disk doesn't fight the
    // globe edge at grazing angles (heatmap-only; arcs/nodes unchanged).
    vec3 elevatedPos = aPos + normalize(aPos) * 8000.0;
    vec4 clip = projectionMatrix * modelViewMatrix * vec4(elevatedPos, 1.0);
    if (clip.w < 0.001) {
      gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      gl_PointSize = 0.0;
      return;
    }
    gl_Position = clip;
    float emerge = smoothstep(0.0, 0.85, aLifecycle);
    gl_PointSize = uSplatSize * pow(max(aConf, 0.05), 0.35) * emerge * (uViewHeight / 600.0);
  }
`;

const HEATMAP_SPLAT_FRAG = /* glsl */`
  precision mediump float;

  varying float vShadow;
  varying float vAnomaly;
  varying float vEntropy;
  varying float vAngle;
  varying float vFacing;

  void main() {
    // Rotate point-coord UV to smear axis
    vec2 uv = gl_PointCoord - vec2(0.5);
    float c = cos(vAngle), s = sin(vAngle);
    vec2 r = vec2(c * uv.x + s * uv.y, -s * uv.x + c * uv.y);

    // Anisotropic: high entropy → elongated splat (aspect 1–2.5)
    float aspect = 1.0 + vEntropy * 1.5;
    vec2 stretched = vec2(r.x, r.y / aspect);
    float d = length(stretched) * 2.0;
    if (d > 1.0) discard;

    float intensity = exp(-d * d * 3.5);

    // Limb fade: wider band + 0.03 bias — prevents threshold-flip strobe near horizon
    float limbFade = smoothstep(-0.05, 0.15, vFacing + 0.03);

    // Write to RGB semantic channels only; alpha=0 keeps RF channel (alpha) clean
    // so network and RF fields accumulate independently in the same RT.
    gl_FragColor = vec4(
      intensity * max(vShadow,  0.04) * limbFade,   // R: shadow / C2
      intensity * max(vAnomaly, 0.04) * limbFade,   // G: anomaly / conf
      intensity * max(vEntropy, 0.04) * limbFade,   // B: entropy / cluster
      0.0                                            // A: reserved for RF cone splats
    );
  }
`;

const HEATMAP_BLIT_VERT = /* glsl */`
  precision mediump float;
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }
`;

const HEATMAP_BLIT_FRAG = /* glsl */`
  precision mediump float;
  uniform sampler2D uSrc;
  uniform float     uDecay;    // RGB decay rate (network field)
  uniform float     uDecayRF;  // A  decay rate  (RF field — persists longer)
  varying vec2 vUv;
  void main() {
    vec4 s = texture2D(uSrc, vUv);
    gl_FragColor = vec4(s.rgb * uDecay, s.a * uDecayRF);
  }
`;

/* ─── RF Cone Splatting GLSL ─────────────────────────────────────────────────
 * Each RF observation is rendered as a directed bearing wedge (Gaussian lobe).
 * The wedge is shaped in screen space using the projected ENU bearing direction
 * so it always points correctly regardless of camera orientation.
 *
 * Written into the ALPHA channel of the heatmap RT (network = RGB, RF = A).
 * Composite pass fuses all four channels for convergence detection.
 * ────────────────────────────────────────────────────────────────────────── */
const RF_CONE_VERT = /* glsl */`
  precision highp float;

  attribute vec3  aRfPos;        // ECEF emitter position (mean sphere surface)
  attribute float aRfBearing;    // bearing from North, clockwise (radians)
  attribute float aRfBeamWidth;  // cone half-angle (radians, e.g. π/12 = 15°)
  attribute float aRfStrength;   // 0–1 normalized signal strength

  uniform float uRfSplatSize;    // base point size in pixels
  uniform float uViewHeight;     // viewport height for DPI scaling

  // Passed to fragment for 3D cone math
  varying vec3  vEcefPos;        // elevated ECEF origin
  varying vec3  vEcefDir;        // ECEF bearing direction (unit)
  varying float vBeamWidth;
  varying float vStrength;
  varying float vFacingRf;

  void main() {
    // ── Hemisphere cull ───────────────────────────────────────────────────
    vec3 normal = normalize(aRfPos);
    vec3 toCam  = normalize(cameraPosition - aRfPos);
    vFacingRf   = dot(normal, toCam);
    if (vFacingRf < -0.15) {
      gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      gl_PointSize = 0.0;
      return;
    }

    vBeamWidth = aRfBeamWidth;
    vStrength  = aRfStrength;

    // ── Elevate to troposphere (~20 km above surface) ─────────────────────
    vec3 elevated = aRfPos + normal * 20000.0;
    vEcefPos = elevated;

    // ── Compute ECEF bearing direction from ENU bearing ───────────────────
    // ENU basis: east = cross(Z, normal), north = cross(normal, east)
    vec3 zAxis = vec3(0.0, 0.0, 1.0);
    vec3 east  = normalize(cross(zAxis, normal));
    if (length(east) < 0.001) east = normalize(cross(vec3(0.0, 1.0, 0.0), normal));
    vec3 north = cross(normal, east);
    // cos(bearing) = North component, sin(bearing) = East component
    vEcefDir = normalize(north * cos(aRfBearing) + east * sin(aRfBearing));

    // ── Screen-space size: scale by distance so far objects don't shrink to 1px
    vec4 clipPos   = projectionMatrix * modelViewMatrix * vec4(elevated, 1.0);
    float distFade = clamp(800.0 / (clipPos.w + 1.0), 0.3, 2.0);
    gl_Position  = clipPos;
    gl_PointSize = uRfSplatSize * vStrength * distFade * (uViewHeight / 600.0);
  }
`;

const RF_CONE_FRAG = /* glsl */`
  precision highp float;

  varying vec3  vEcefPos;      // troposphere-elevated origin in ECEF
  varying vec3  vEcefDir;      // unit bearing direction in ECEF
  varying float vBeamWidth;    // cone half-angle (radians)
  varying float vStrength;
  varying float vFacingRf;

  // Earth occlusion guard: returns false when p is behind the globe
  // as seen from the elevated origin along the bearing direction.
  bool behindGlobe(vec3 origin, vec3 worldPt) {
    float R = 6.371e6;
    vec3 o = origin;
    vec3 d = normalize(worldPt - o);
    float b = dot(o, d);
    float c = dot(o, o) - R * R;
    float disc = b * b - c;
    if (disc < 0.0) return false;                // ray misses sphere
    float t = -b - sqrt(disc);
    return t > 0.0 && t < length(worldPt - o);  // intersection between origin and fragment
  }

  void main() {
    // ── Soft hemisphere fade ──────────────────────────────────────────────
    float limbFade = smoothstep(-0.05, 0.20, vFacingRf + 0.03);

    // ── Point-coord UV (–0.5 → +0.5) ─────────────────────────────────────
    vec2 pc = gl_PointCoord - vec2(0.5);
    float r = length(pc);
    if (r > 0.5) discard;

    // ── Reconstruct approximate world-space fragment position ─────────────
    // Treat the splat as a local tangent-plane disc oriented toward the camera.
    // We expand the disc radius in world units proportional to gl_PointSize.
    // This gives per-fragment ECEF positions good enough for cone angle tests.
    vec3 normal   = normalize(vEcefPos);
    vec3 toCam    = normalize(cameraPosition - vEcefPos);
    // Build a local 2D frame in the plane perpendicular to toCam
    vec3 right    = normalize(cross(toCam, normal));
    vec3 up       = cross(right, toCam);
    float discR   = 120000.0 * vStrength + 40000.0;  // ~40–160 km disc radius
    vec3 worldPt  = vEcefPos + (pc.x * right + pc.y * up) * discR;

    // ── Earth occlusion: cull fragments that would pass through the planet ─
    if (behindGlobe(vEcefPos, worldPt)) discard;

    // ── 3D cone test: angle between fragment direction and bearing axis ────
    vec3 toFrag   = normalize(worldPt - vEcefPos);
    float cosAngle = dot(toFrag, vEcefDir);
    float coneEdge = cos(vBeamWidth);

    // Outside cone → discard (hard cull slightly beyond soft edge)
    if (cosAngle < coneEdge - 0.05) discard;

    // Soft angular falloff: full brightness on-axis, zero at cone edge
    float beam = smoothstep(coneEdge, coneEdge + (1.0 - coneEdge) * 0.7, cosAngle);

    // Radial falloff: Gaussian lobe that peaks ~30% out from center
    // (avoids bright dot at emitter origin, simulates propagating wavefront)
    float d = r * 2.0;  // 0 at center, 1 at edge
    float radial = exp(-pow(d - 0.3, 2.0) * 12.0);

    float rf = beam * radial * vStrength * limbFade;
    if (rf < 0.005) discard;

    // Write ONLY to alpha channel — RGB is owned by network splats
    gl_FragColor = vec4(0.0, 0.0, 0.0, rf);
  }
`;

// Full-screen quad vertex shader shared by all composite passes.
// Three.js auto-injects: projectionMatrix, modelViewMatrix, position, uv.
const HEATMAP_COMP_VERT = /* glsl */`
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const HEATMAP_COMP_FRAG = /* glsl */`
  precision highp float;

  // ── Heatmap accumulation buffers ─────────────────────────────────────────
  uniform sampler2D uHeatmap;      // current frame  (RGB=shadow/anomaly/entropy  A=RF)
  uniform sampler2D uHeatmapPrev;  // previous frame (same layout)
  uniform float     uBlend;

  // ── Globe occlusion: ray-sphere masking ──────────────────────────────────
  uniform mat4  uInvProjView;    // inverse of (proj × view) for ray reconstruction
  uniform vec3  uCameraECEF;     // camera position in ECEF metres
  uniform float uEarthRadius;    // 6.371e6 + 8848 (Everest ceiling)

  // ── Volumetric RF emitter array (up to 16 directional sources) ────────────
  // Packed as parallel arrays to avoid struct limitations in GLSL ES 1.00.
  // Set uRfVolCount = 0 to disable the raymarch entirely.
  uniform int   uRfVolCount;
  uniform vec3  uRfVolOrigin[16];   // ECEF origin (surface elevation)
  uniform vec3  uRfVolDir[16];      // unit ECEF bearing direction
  uniform float uRfVolAngle[16];    // cone half-angle (radians)
  uniform float uRfVolStrength[16]; // 0–1 signal intensity
  uniform float uRfVolFreq[16];     // normalized frequency bucket 0–1 (for colour)

  // ── Sparse voxel field atlas (CPU-injected, GPU-sampled) ──────────────────
  // 128×64×16 lat/lon/alt grid packed into a 512×256 2D texture.
  // 4×4 tile layout: tileCol = altLayer % 4, tileRow = altLayer / 4.
  // R=RF energy  G=Network density  B=Classification conf  A=Recency (0=stale,1=fresh)
  uniform sampler2D uVoxelAtlas;

  // ── GPU Strobe shockwave field ────────────────────────────────────────────
  // Ring buffer of discrete events encoded into a 2D float texture.
  // 4-wide × MAX_STROBES texture — each row = one strobe event:
  //   col 0 (x=0.125): posX, posY, posZ, t0
  //   col 1 (x=0.375): energy, type, dirX, dirY
  //   col 2 (x=0.625): fh_bw, fh_dt, fh_dc, fh_pp
  //   col 3 (x=0.875): snr, spectral_entropy, hop_variance, modulation_class
  uniform sampler2D uStrobeTex;    // MAX_STROBES × 4 RGBA Float
  uniform int       uStrobeCount;  // active strobe count (0 = disabled)
  uniform float     uTime;         // wall-clock seconds

  varying vec2 vUv;

  // ── Voxel atlas lookup: trilinear interpolation across altitude layers ─────
  // Converts ECEF world position → normalized lat/lon/alt → atlas UV.
  vec4 sampleVoxelAtlas(vec3 worldPos) {
    float r   = length(worldPos);
    float alt = r - uEarthRadius;
    if (alt < 0.0 || alt > 200000.0) return vec4(0.0);

    // ECEF → spherical → normalized UVs
    float sinLat = clamp(worldPos.z / r, -1.0, 1.0);
    float u = (atan(worldPos.y, worldPos.x) + 3.14159265) / 6.28318530;  // 0–1
    float v = (asin(sinLat)                 + 1.57079633) / 3.14159265;  // 0–1

    // Altitude → fractional layer index → two adjacent tile coords
    float layerF   = clamp(alt / 200000.0, 0.0, 1.0) * 15.0;
    int   layerLo  = int(floor(layerF));
    int   layerHi  = min(layerLo + 1, 15);
    float frac     = fract(layerF);

    // 4×4 tile layout (4 cols × 4 rows, each tile = LON×LAT = 128×64 pixels)
    vec2 loUV = vec2((float(layerLo  - (layerLo  / 4) * 4) + u) * 0.25,
                     (float(layerLo  / 4)                   + v) * 0.25);
    vec2 hiUV = vec2((float(layerHi  - (layerHi  / 4) * 4) + u) * 0.25,
                     (float(layerHi  / 4)                   + v) * 0.25);

    return mix(texture2D(uVoxelAtlas, loUV), texture2D(uVoxelAtlas, hiUV), frac);
  }

  // ── Strobe shockwave field: expanding causality rings from discrete events ──
  // Each strobe emits a ring-shaped impulse that propagates outward at waveSpeed
  // m/s, decays exponentially over time, and encodes meaning via type:
  //   0=network (spherical), 1=RF (directional cone+ring), 2=C2 (pulsing),
  //   3=UAV (forward trail), 4=anomaly (jagged multi-freq),
  //   5=cluster (intelligence emission), 6=interference (non-physical distortion)
  float sampleStrobeField(vec3 worldPos) {
    if (uStrobeCount <= 0) return 0.0;

    const float WAVE_SPEED  = 300000.0;  // m/s propagation (~ speed of light vis scale)
    const float RING_SHARP  = 8e-11;     // ring tightness (inverse variance)
    const float TIME_DECAY  = 1.8;       // exponential time decay rate
    const float MAX_AGE     = 8.0;       // seconds before strobe is invisible

    float field = 0.0;

    for (int i = 0; i < 256; i++) {
      if (i >= uStrobeCount) break;

      // rowV maps row i to the correct texel centre in the 256-row DataTexture.
      // MUST divide by the full texture height (256), NOT uStrobeCount.
      float rowV = (float(i) + 0.5) / 256.0;
      vec4 colA  = texture2D(uStrobeTex, vec2(0.125, rowV));  // posX, posY, posZ, t0
      vec4 colB  = texture2D(uStrobeTex, vec2(0.375, rowV));  // energy, type, dirX, dirY
      vec4 colC  = texture2D(uStrobeTex, vec2(0.625, rowV));  // fh_bw, fh_dt, fh_dc, fh_pp
      vec4 colD  = texture2D(uStrobeTex, vec2(0.875, rowV));  // snr, spectral_entropy, hop_variance, modulation_class

      // RF fingerprint fields (floats 8-15)
      float fhBw          = colC.x;   // freq-hop bandwidth (0-1)
      float fhDt          = colC.y;   // dwell time
      float fhDc          = colC.z;   // duty cycle
      float fhPp          = colC.w;   // pattern predictability
      float rfSnr         = colD.x;   // SNR
      float specEntropy   = colD.y;   // spectral entropy (0=pure, 1=noise)
      float hopVar        = colD.z;   // hop variance
      float modClass      = colD.w;   // modulation class

      vec3  sPos    = colA.xyz;
      float t0      = colA.w;
      float energy  = colB.x;
      float sType   = colB.y;
      vec3  sDir    = vec3(colB.z, colB.w, 0.0);

      float dt = uTime - t0;
      if (dt < 0.0 || dt > MAX_AGE) continue;

      float radius   = dt * WAVE_SPEED;
      float dist     = distance(worldPos, sPos);

      // Base ring-shaped impulse: peaks where dist ~ radius, falls off Gaussian
      float ringDelta = dist - radius;
      float ring      = exp(-ringDelta * ringDelta * RING_SHARP);
      float timeFade  = exp(-dt * TIME_DECAY);

      // -- Waveform fingerprinting -- each type gets a physically distinct signature --
      float typeModulation = 1.0;

      if (sType < 0.5) {
        // NETWORK (0): clean spherical ripple
        typeModulation = 1.0;

      } else if (sType < 1.5) {
        // RF (1): directional cone modulation
        float lenDir = length(sDir);
        if (lenDir > 0.1) {
          float alignment = dot(normalize(worldPos - sPos), normalize(sDir));
          // RF fingerprint: constructive interference for matching identity signature
          float fpModulation = 1.0 + fhBw * 0.5 - specEntropy * 0.25 + (1.0 - fhPp) * 0.3;
          // Modulation class shifts waveform character
          float modShift = 0.8 + modClass * 0.4 + sin(dt * (8.0 + modClass * 20.0)) * 0.2;
          typeModulation = smoothstep(0.3, 0.85, alignment) * fpModulation * modShift;
        }

      } else if (sType < 2.5) {
        // C2 (2): periodic beacon -- rhythmic amplitude modulation
        typeModulation = 0.6 + 0.4 * sin(dt * 12.0);

      } else if (sType < 3.5) {
        // UAV (3): forward-biased trail
        float lenDir = length(sDir);
        if (lenDir > 0.1) {
          float alignment = dot(normalize(worldPos - sPos), normalize(sDir));
          typeModulation = smoothstep(-0.2, 0.7, alignment) * (1.0 + alignment * 0.5);
        }

      } else if (sType < 4.5) {
        // ANOMALY (4): jagged multi-frequency interference pattern
        typeModulation = 0.5 + 0.5 * sin(dist * 0.00005 + dt * 8.0)
                               * sin(dist * 0.00013 - dt * 5.0);

      } else if (sType < 5.5) {
        // CLUSTER (5): slow breathing intelligence emission
        // Wide ring + sinusoidal breathing -- clusters radiate awareness outward
        float wideRing = exp(-ringDelta * ringDelta * RING_SHARP * 0.25);
        float breathe  = 0.7 + 0.3 * sin(dt * 4.0);
        ring = wideRing;
        typeModulation = breathe;

      } else if (sType < 6.5) {
        // INTERFERENCE (6): spacetime distortion -- non-physical motion
        // Rapid oscillation + double-ring creates visual instability
        float warp = sin(dist * 0.00002 + uTime * 10.0) * 0.5 + 0.5;
        float ghost = exp(-(ringDelta - radius * 0.3) * (ringDelta - radius * 0.3) * RING_SHARP);
        ring = ring + ghost * 0.5;
        typeModulation = warp * 1.4;

      } else if (sType < 7.5) {
        // PATH (7): ASN transit hop — tight bright pulse, fast propagation
        // Short-lived intense ring for hop-by-hop animation across globe
        float tightRing = exp(-ringDelta * ringDelta * RING_SHARP * 4.0);
        float flashPulse = exp(-dt * 4.0);  // faster decay than default
        ring = tightRing;
        timeFade = flashPulse;
        typeModulation = 1.5;  // extra bright

      } else if (sType < 8.5) {
        // CONFLICT (8): IX peering contention — behavioral shader
        // Pulsing radius ∝ heat velocity, flicker ∝ instability
        // Energy encodes CSI: purple=synthetic, white=conflict, orange=stress
        float wideZone = exp(-ringDelta * ringDelta * RING_SHARP * 0.15);
        // Velocity-driven pulse: energy > 1.2 = high velocity
        float velPulse = 0.6 + 0.4 * sin(uTime * (4.0 + energy * 8.0));
        // Instability flicker: sDir.x encodes instability (0-1)
        float flickerRate = 12.0 + sDir.x * 30.0;
        float flicker = 0.5 + 0.5 * sign(sin(dt * flickerRate));
        // Pressure distortion wave
        float pressure = 0.7 + 0.3 * sin(dist * 0.00003 + uTime * 6.0);
        ring = wideZone;
        typeModulation = velPulse * flicker * pressure * 1.6;

      } else {
        // PHANTOM (9): non-physical convergence attractor — inward-pulsing ghost node
        // dirX = phantom_pull (0-1): how strongly it attracts flows
        // dirY = synthetic_ratio (0-1): how non-physical the routing is
        // Energy encodes confidence
        // Visualize as gravitational well: tight center glow + inward-collapsing rings
        float innerGlow = exp(-dist * dist * 9.0e-12) * 1.8;
        // Inward ripple: reversed wave phase creates convergent visual
        float inwardPhase = dt * WAVE_SPEED - dist;
        float inwardRing  = exp(-inwardPhase * inwardPhase * RING_SHARP * 0.08);
        // Confidence-driven pull strength
        float pullStrength = 0.3 + 0.7 * sDir.x;
        // Instability drift: ghost node spatially unstable
        float drift = 0.7 + 0.3 * sin(uTime * (1.5 + sDir.y * 4.0) + dist * 8.0e-6);
        // Standing wave: multiple convergent shells create depth
        float shell2 = exp(-pow(dist - radius * 0.5, 2.0) * RING_SHARP * 0.2);
        ring = innerGlow + inwardRing + shell2 * 0.4;
        timeFade = exp(-dt * 0.8);  // slow fade — phantom persists
        typeModulation = drift * pullStrength * 2.2;
        // Phantom fingerprint: high spectral entropy + high hop variance = stronger ghost signal
        float ghostResonance = 0.8 + specEntropy * 0.4 + hopVar * 0.3;
        typeModulation *= ghostResonance;
      }

      field += ring * timeFade * energy * typeModulation;
    }
    return clamp(field, 0.0, 1.0);
  }

  // ── Volumetric RF density at a world-space point ──────────────────────────
  // Combines two sources:
  //   1. Real-time emitter array (newly pushed bearings this frame)
  //   2. Persistent voxel atlas (accumulated history, survives frame boundaries)
  // The product of both reinforces zones where evidence is consistent over time.
  float sampleRfDensity(vec3 p) {
    // ── Real-time emitter cones ────────────────────────────────────────────
    float emitterRf = 0.0;
    for (int i = 0; i < 16; i++) {
      if (i >= uRfVolCount) break;
      vec3 toP   = p - uRfVolOrigin[i];
      float dist = length(toP);
      if (dist < 1.0) continue;
      float align   = dot(normalize(toP), uRfVolDir[i]);
      float edge    = cos(uRfVolAngle[i]);
      float cone    = smoothstep(edge - 0.05, edge + 0.20, align);
      float falloff = exp(-dist * dist * 2.5e-11);
      float alt     = length(p) - uEarthRadius;
      float altW    = smoothstep(0.0, 5000.0, alt) * smoothstep(120000.0, 40000.0, alt);
      emitterRf    += cone * falloff * altW * uRfVolStrength[i];
    }

    // ── Persistent voxel field ─────────────────────────────────────────────
    vec4 vox = sampleVoxelAtlas(p);
    float voxRf  = vox.r * vox.a;             // RF energy weighted by freshness
    float voxNet = vox.g * 0.4;               // network co-location boosts density
    float voxCls = vox.b * 0.25;              // classification confidence adds weight

    // Additive fusion: emitter cones + persistent voxel field.
    // Spatial overlap between the two layers multiplicatively reinforces.
    float voxFused = voxRf * (1.0 + voxNet + voxCls);
    return emitterRf + voxFused + emitterRf * voxFused * 0.5;
  }

  // ── Ray-march: integrate RF density from camera along view ray ───────────
  // Uses 24 steps of 8 km each covering ~192 km of atmosphere above the globe.
  // Terminates early at the first Earth intersection to avoid underground march.
  vec2 raymarchRf(vec3 rayOrigin, vec3 rayDir, float tSurface) {
    float accum  = 0.0;
    vec3  tinted = vec3(0.0); // unused placeholder — colour computed in main
    float t      = max(0.0, tSurface - 200000.0); // start 200 km above surface hit
    const float STEP = 8000.0;  // 8 km per step

    for (int i = 0; i < 24; i++) {
      if (t >= tSurface) break;                    // hit earth surface
      vec3 p = rayOrigin + rayDir * t;
      if (length(p) < uEarthRadius - 1000.0) break; // underground guard

      accum += sampleRfDensity(p) * 0.042;           // step weight
      t += STEP;
    }
    return vec2(clamp(accum, 0.0, 1.0), t);
  }

  void main() {
    vec4 currFull = texture2D(uHeatmap,     vUv);
    vec4 prevFull = texture2D(uHeatmapPrev, vUv);
    vec3 curr = currFull.rgb;
    vec3 prev = prevFull.rgb;
    float rfHeat = currFull.a;
    float rfPrev = prevFull.a;

    float intensity = length(curr);

    // ── Globe occlusion: ray-sphere intersection ──────────────────────────
    vec4 wFar = uInvProjView * vec4(vUv * 2.0 - 1.0, 1.0, 1.0);
    wFar /= wFar.w;
    vec3 rayDir = normalize(wFar.xyz - uCameraECEF);

    float halfB = dot(rayDir, uCameraECEF);
    float rc    = dot(uCameraECEF, uCameraECEF) - uEarthRadius * uEarthRadius;
    float disc  = halfB * halfB - rc;

    // Ray misses Earth entirely → pure space pixel, discard unless volumetric RF
    float tSurface = 1e20;
    if (disc >= 0.0) {
      float tNear = -halfB - sqrt(disc);
      if (tNear >= 0.0) tSurface = tNear;
    }

    // ── Volumetric RF raymarch ─────────────────────────────────────────────
    float volRf = 0.0;
    if (uRfVolCount > 0) {
      // Compute march start: begin just outside atmosphere (200 km above surface)
      float tAtmo = max(0.0, tSurface - 200000.0);
      vec2 march = raymarchRf(uCameraECEF, rayDir, tSurface);
      volRf = march.x;
    }

    // For space pixels with no heatmap data and no volumetric RF → discard
    bool hasField = (intensity >= 0.04 || rfHeat >= 0.02 || volRf >= 0.01);
    if (disc < 0.0) {
      // Space pixel: only keep if volumetric RF is strong enough
      if (volRf < 0.03) discard;
    } else {
      float tNear = -halfB - sqrt(max(disc, 0.0));
      if (tNear < 0.0) discard;
      if (!hasField) discard;
    }

    // Surface geometry for limb shading (use tSurface when available)
    vec3 hitNormal   = (tSurface < 1e19) ? normalize(uCameraECEF + tSurface * rayDir) : normalize(-rayDir);
    float hitFacing  = dot(hitNormal, normalize(-rayDir));
    float globeLimbFade = smoothstep(0.0, 0.25, hitFacing + 0.03);
    float limbTint   = 1.0 - globeLimbFade;

    // ── Voxel field at surface hit point ─────────────────────────────────
    // Samples the persistent world model directly at the globe surface so
    // areas with accumulated evidence glow even between raymarch steps.
    vec4 surfaceVox = (tSurface < 1e19)
        ? sampleVoxelAtlas(uCameraECEF + tSurface * rayDir)
        : vec4(0.0);
    float voxSurfRf  = surfaceVox.r * surfaceVox.a;
    float voxSurfNet = surfaceVox.g * surfaceVox.a;
    float voxSurfCls = surfaceVox.b;

    // ── Temporal velocity ─────────────────────────────────────────────────
    vec3 vel = clamp(curr - prev, -0.5, 0.5);
    float horizonDamp = smoothstep(0.0, 0.20, hitFacing);
    vel *= horizonDamp;
    float velSum = vel.r + vel.g + vel.b;
    float emerge = max(0.0,  velSum * 2.0);
    float decay  = max(0.0, -velSum * 1.5);

    vec3  projected = curr + vel * 1.8;
    float bloomOut  = max(0.0, length(projected) - intensity) * horizonDamp;

    // ── Semantic channel colorization ─────────────────────────────────────
    vec3 shadowCol  = vec3(0.75, 0.05, 0.90);
    vec3 anomalyCol = vec3(1.00, 0.38, 0.02);
    vec3 entropyCol = vec3(0.50, 0.95, 0.25);
    vec3 coldColor  = vec3(0.00, 0.28, 0.72);

    float total = max(curr.r + curr.g + curr.b, 0.001);
    vec3 color = (curr.r / total) * shadowCol
               + (curr.g / total) * anomalyCol
               + (curr.b / total) * entropyCol;

    color = mix(coldColor, color, smoothstep(0.05, 0.35, intensity));
    color = mix(color, vec3(1.0, 0.98, 0.92), clamp(emerge * 0.6, 0.0, 0.70));
    color = mix(color, coldColor * 0.5,        clamp(decay  * 0.4, 0.0, 0.50));
    color += vec3(0.35, 0.80, 1.0) * clamp(bloomOut, 0.0, 0.25) * 0.5;

    color = mix(color, vec3(0.04, 0.14, 0.45), limbTint * 0.50);

    // ── RT heatmap RF splat channel (bearing wedges from _rfConeScene) ────
    float rfVel = rfHeat - rfPrev;
    color += vec3(0.10, 0.95, 0.45) * smoothstep(0.04, 0.30, rfHeat) * 0.55;
    color += vec3(0.40, 1.0, 0.60)  * clamp(rfVel * 3.5, 0.0, 0.45);

    // ── Voxel field surface layer ──────────────────────────────────────────
    // RGB channels of the atlas contribute directly to surface glow, giving
    // persistent visual evidence even when real-time cones are inactive.
    if (voxSurfRf > 0.02) {
      color += vec3(0.15, 1.0, 0.50) * smoothstep(0.02, 0.25, voxSurfRf) * 0.60;
    }
    if (voxSurfNet > 0.02) {
      // Network density in voxels tints toward the network semantic colour
      color += vec3(0.80, 0.35, 1.0) * smoothstep(0.02, 0.20, voxSurfNet) * 0.35;
    }
    // Classification confidence: bright cyan overlay marks typed emitters
    if (voxSurfCls > 0.15) {
      color = mix(color, vec3(0.30, 0.95, 1.0), smoothstep(0.15, 0.60, voxSurfCls) * 0.50);
    }

    // ── Volumetric RF glow (raymarched atmosphere) ─────────────────────────
    if (volRf > 0.005) {
      vec3 volColor = mix(vec3(1.0, 0.55, 0.10), vec3(0.10, 0.85, 1.0), clamp(volRf, 0.0, 1.0));
      color += volColor * smoothstep(0.01, 0.25, volRf) * 0.70;
    }

    // ── Strobe shockwave field (discrete event causality rings) ────────────
    // Sample at the surface hit point — expanding rings radiate from event origins.
    float strobeField = 0.0;
    if (uStrobeCount > 0 && tSurface < 1e19) {
      vec3 surfHit = uCameraECEF + tSurface * rayDir;
      strobeField = sampleStrobeField(surfHit);
    }
    // Strobe colourization: white-hot core → amber ring → cyan fade
    if (strobeField > 0.005) {
      vec3 strobeCore  = vec3(1.0, 0.98, 0.90);   // hot white
      vec3 strobeEdge  = vec3(1.0, 0.60, 0.10);   // amber
      vec3 strobeFade  = vec3(0.15, 0.85, 1.0);   // cyan
      float t = smoothstep(0.005, 0.35, strobeField);
      vec3 strobeColor = mix(strobeFade, mix(strobeEdge, strobeCore, t), t);
      color += strobeColor * t * 0.80;
    }

    // ── Convergence zone: all sources fused ───────────────────────────────
    // True convergence requires co-location of *multiple independent evidence types*:
    //   heatmap RT splats (frame-fresh) + voxel field (temporally accumulated)
    //   + volumetric atmosphere (directional cones) + network density + strobe events
    float allRf      = max(max(rfHeat, volRf * 0.5), max(voxSurfRf, strobeField * 0.4));
    float networkHeat = max(length(curr), voxSurfNet);
    float convergence = networkHeat * allRf * (1.0 + voxSurfCls * 0.5);
    float convPeak    = smoothstep(0.10, 0.45, convergence);
    color = mix(color, vec3(1.0, 1.0, 0.95), convPeak * 0.85);

    // ── Alpha ─────────────────────────────────────────────────────────────
    float voxActivity = max(voxSurfRf, max(voxSurfNet, voxSurfCls));
    float combinedIntensity = max(max(sqrt(intensity), rfHeat * 0.7),
                                  max(max(volRf * 0.9, voxActivity * 0.8),
                                      strobeField * 0.85));
    float alpha = smoothstep(0.02, 0.40, combinedIntensity) * uBlend * globeLimbFade;
    alpha = min(alpha, 0.62);

    gl_FragColor = vec4(color, alpha);
  }
`;

/* ═══════════════════════════════════════════════════════════════════════════
 * VoxelField — Sparse persistent 3D world model
 * ─────────────────────────────────────────────────────────────────────────
 * Maintains a lat/lon/alt voxel grid representing accumulated RF energy,
 * network density, and classification confidence over time.
 *
 * Grid: 128 lon × 64 lat × 16 alt layers (0–200 km), packed into a
 * 512 × 256 2D texture atlas (4×4 tile arrangement, one tile per alt layer).
 * Channels:  R=RF energy   G=Network density   B=Class confidence   A=Recency
 *
 * Designed for WebGL2 — no compute shaders.  Injection is CPU-side; the GPU
 * just samples the texture in the composite fragment shader.
 * ═══════════════════════════════════════════════════════════════════════════ */
class VoxelField {
  static LON     = 128;      // longitude cells
  static LAT     = 64;       // latitude cells
  static ALT     = 16;       // altitude layers  (0–200 km)
  static ALT_MAX = 200000;   // metres — atmosphere ceiling
  static W       = 512;      // atlas pixel width  (4 × LON)
  static H       = 256;      // atlas pixel height (4 × LAT)

  constructor() {
    const { W, H } = VoxelField;
    // RGBA float32 — R=RF, G=network, B=class, A=recency
    this._data    = new Float32Array(W * H * 4);
    this._dirty   = false;
    this._texture = new THREE.DataTexture(
      this._data, W, H,
      THREE.RGBAFormat, THREE.FloatType
    );
    this._texture.minFilter = THREE.LinearFilter;
    this._texture.magFilter = THREE.LinearFilter;
    this._texture.wrapS     = THREE.ClampToEdgeWrapping;
    this._texture.wrapT     = THREE.ClampToEdgeWrapping;
    this._texture.needsUpdate = true;
  }

  // ── Returns the Float32Array offset for a given lat/lon/alt ──────────────
  _idx(latDeg, lonDeg, altM) {
    const { LON, LAT, ALT, ALT_MAX, W } = VoxelField;
    const u  = Math.max(0, Math.min(LON - 1, Math.floor(((lonDeg + 180) / 360) * LON)));
    const v  = Math.max(0, Math.min(LAT - 1, Math.floor(((latDeg +  90) / 180) * LAT)));
    const al = Math.max(0, Math.min(ALT - 1, Math.floor((Math.max(0, altM) / ALT_MAX) * ALT)));
    const px = (al % 4) * LON + u;
    const py = Math.floor(al / 4) * LAT + v;
    return (py * W + px) * 4;
  }

  /**
   * Inject an RF bearing cone into the voxel field.
   * Paints energy into all cells within the cone at the requested altitude.
   * Uses 1° iteration steps (grid resolution is ~2.8° so this is slightly
   * oversampled — fine for accumulation).
   */
  injectRfCone(lat, lon, bearingDeg, beamWidthDeg, strength, altM = 20000) {
    const halfAngle = (beamWidthDeg * 0.5) * (Math.PI / 180);
    const cosHalf   = Math.cos(halfAngle);
    const rangeKm   = 300;
    const dLat      = rangeKm / 111.32;
    const dLon      = rangeKm / (111.32 * Math.max(0.08, Math.cos(lat * Math.PI / 180)));
    const step      = 1.0;   // 1° steps — coarser than grid res is fine for injection

    const latR = lat * (Math.PI / 180);
    const lonR = lon * (Math.PI / 180);

    for (let dlat = -dLat; dlat <= dLat; dlat += step) {
      const cellLat = lat + dlat;
      if (cellLat < -90 || cellLat > 90) continue;
      const cellLatR = cellLat * (Math.PI / 180);

      for (let dlon = -dLon; dlon <= dLon; dlon += step) {
        const cellLon  = lon + dlon;
        const cellLonR = cellLon * (Math.PI / 180);

        // Great-circle bearing from origin to cell
        const y = Math.sin(cellLonR - lonR) * Math.cos(cellLatR);
        const x = Math.cos(latR) * Math.sin(cellLatR)
                - Math.sin(latR) * Math.cos(cellLatR) * Math.cos(cellLonR - lonR);
        let angleDiff = Math.abs(Math.atan2(y, x) - bearingDeg * (Math.PI / 180));
        while (angleDiff > Math.PI) angleDiff = Math.abs(angleDiff - 2 * Math.PI);

        if (angleDiff >= halfAngle + 0.1) continue;

        const cosAlign   = Math.max(0, Math.cos(angleDiff));
        const angFalloff = Math.max(0, (cosAlign - cosHalf) / Math.max(0.001, 1 - cosHalf));
        const distKm     = Math.hypot(dlat * 111.32, dlon * 111.32 * Math.cos(latR));
        const distFalloff = Math.exp(-distKm * distKm * 1.1e-4); // ~100 km half-power

        const idx = this._idx(cellLat, cellLon, altM);
        this._data[idx]     = Math.min(1, this._data[idx]     + strength * angFalloff * distFalloff);
        this._data[idx + 3] = 1.0;  // mark fresh
      }
    }
    this._dirty = true;
  }

  /**
   * Inject a point observation — RF, network, or classification at a location.
   * Suitable for node-level injection from the network graph.
   */
  injectPoint(latDeg, lonDeg, altM, rf = 0, network = 0, cls = 0) {
    const idx = this._idx(latDeg, lonDeg, altM);
    if (rf > 0)      this._data[idx]     = Math.min(1, this._data[idx]     + rf);
    if (network > 0) this._data[idx + 1] = Math.min(1, this._data[idx + 1] + network);
    if (cls > 0)     this._data[idx + 2] = Math.min(1, this._data[idx + 2] + cls);
    this._data[idx + 3] = 1.0;
    this._dirty = true;
  }

  /**
   * Importance-based adaptive decay — call at ~4–15 Hz.
   *
   * Each voxel's importance = RF * coherence * rarity * freshness.
   * High-importance voxels decay slowly (persist for minutes).
   * Low-importance voxels decay aggressively (cleared in seconds).
   *
   * Decay rate per channel is modulated by importance:
   *   effective_rate = baseRate ^ (1 - importance * 0.7)
   * So importance=1 → rate^0.3 (much slower), importance=0 → rate^1 (full speed).
   *
   * Periodic signals are additionally compressed: if a voxel shows stable,
   * low-variance RF energy it receives a "keyframe" boost (slower A-channel decay)
   * to preserve the standing pattern without re-injecting every frame.
   */
  decay(rfR = 0.97, netR = 0.94, clsR = 0.96, ageR = 0.95) {
    const d = this._data;
    if (!this._importanceAccum) this._importanceAccum = new Float32Array(d.length >> 2);
    const imp = this._importanceAccum;

    for (let i = 0, p = 0; i < d.length; i += 4, p++) {
      const rf  = d[i];
      const net = d[i + 1];
      const cls = d[i + 2];
      const age = d[i + 3];

      if (rf < 0.001 && net < 0.001) {
        // Cold voxel — fast clear with no math overhead
        d[i]   = 0;
        d[i+1] = 0;
        d[i+2] = 0;
        d[i+3] = 0;
        imp[p] = 0;
        continue;
      }

      // Importance = energy × classification confidence × freshness
      // rarity: low-occupancy cells are rarer → higher importance weight
      const importance = Math.min(1.0, (rf * 0.5 + net * 0.3 + cls * 0.2) * age);

      // Temporal compression: stable RF (similar to recent moving average)
      // → detect quasi-periodic signals and hold them as keyframes
      const prevImp = imp[p];
      const stability = 1.0 - Math.abs(importance - prevImp);  // 0=volatile, 1=stable
      imp[p] = importance * 0.2 + prevImp * 0.8;               // EMA

      // Adaptive decay exponent: importance=1 → 0.3× normal decay, importance=0 → full
      const iScale   = 1.0 - importance * 0.7;
      const rfDecay  = Math.pow(rfR,  iScale);
      const netDecay = Math.pow(netR, iScale);
      const clsDecay = Math.pow(clsR, iScale);
      // Age channel: stable periodic signals get keyframe boost (slower age decay)
      const ageDecay = stability > 0.85 ? Math.pow(ageR, 0.2) : Math.pow(ageR, iScale);

      d[i]   = rf  * rfDecay;
      d[i+1] = net * netDecay;
      d[i+2] = cls * clsDecay;
      d[i+3] = age * ageDecay;
    }
    this._dirty = true;
  }

  /** Upload CPU data to GPU texture if dirty. */
  upload() {
    if (!this._dirty) return;
    this._texture.needsUpdate = true;
    this._dirty = false;
  }

  get texture() { return this._texture; }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * ═══════════════════════════════════════════════════════════════════════════ */
class CesiumHypergraphGlobe {

  constructor() {
    this._viewer   = null;          // Cesium.Viewer
    this._renderer = null;          // THREE.WebGLRenderer
    this._scene    = null;          // THREE.Scene
    this._camera   = null;          // THREE.PerspectiveCamera

    /* ── Node layer ── */
    this._nodeMesh      = null;     // THREE.InstancedMesh
    this._nodeIdxMap    = new Map();// entityId → instance index
    this._nodeCount     = 0;
    this._nodeConf      = new Float32Array(MAX_NODES);
    this._nodeLifecycle = new Float32Array(MAX_NODES);   // 0→1 emergence
    this._nodeCluster   = new Float32Array(MAX_NODES).fill(1);
    this._selectedEntityId = null;

    /* ── Arc layer ── */
    this._arcMesh       = null;     // THREE.LineSegments
    this._arcGeo        = null;     // BufferGeometry reused
    this._arcEdges      = [];       // [{src,dst,conf,entropy,rfCorr,shadow,id}]
    this._arcDirty      = false;

    /* ── Hyperedge layer ── */
    this._heMesh        = null;     // THREE.Points
    this._heList        = [];       // [{centroid, conf, color, height, width, memberCount}]
    this._heDirty       = false;
    this._clusterFlareVisible = true;

    /* ── Shared uniforms ── */
    this._uTime         = { value: 0 };
    this._uSelectedId   = { value: -1 };
    this._uAdjTex       = { value: null };
    this._uQuality      = { value: 1.0 };
    this._uViewHeight   = { value: window.innerHeight || 800 };

    /* ── Adjacency texture (BFS distances, updated after topo change) ── */
    this._adjData       = new Float32Array(ADJ_TEX_SIZE * ADJ_TEX_SIZE).fill(255);

    /* ── Graph state ── */
    this._graph = {
      nodes: new Map(),   // id → {lat, lon, conf, color, violations, cluster, shadow}
      edges: new Map(),   // id → {src, dst, conf, entropy, rfCorr, shadow, kind}
      hyperedges: new Map() // id → {members:[], conf, color}
    };

    /* ── GeoIP cache ── */
    this._geoCache      = new Map(); // entityId → Cesium.Cartesian3 (ECEF)

    /* ── WebSocket ── */
    this._socket        = null;
    this._scopeId       = null;

    /* ── Batching ── */
    this._updateQueue   = [];
    this._batchTimer    = null;

    /* ── Convergence bloom ── */
    this._bloomActive   = false;
    this._bloomNodes    = [];
    this._bloomStart    = 0;

    /* ── Shadow reveal mode ── */
    this._shadowReveal  = false;

    /* ── Temporal scrubber ── */
    this._edgeStore     = new Map();   // id → arc entry with serverTs for time-travel
    this._timeCursor    = 0;           // epoch-seconds; 0 means live follows _lastEdgeTs
    this._timeWindow    = 120;         // seconds of history visible in scrub mode
    this._isLive        = true;
    this._scrubPlayId   = null;

    /* ── Frame budget governor ── */
    this._governor = new FrameBudgetGovernor();

    /* ── Deck.gl bridge ── */
    this._deckBridge = null;

    /* ── RF Volumetric shell mesh ── */
    this._rfVolMesh = null;
    this._beamMesh   = null;   // RF edge beam layer
    this._hyperField = null;   // HyperField instance (lazy init)

    /* ── Semantic zones ── */
    this._semanticDataSource  = null;   // Cesium.CustomDataSource
    this._semanticZoneInterval = null;

    /* ── Geo labels ── */
    this._labelCollection    = null;   // Cesium.LabelCollection
    this._observerLat        = 0;
    this._observerLon        = 0;
    this._observerAlt        = 0;
    this._geoLabelsInterval  = null;

    /* ── GPU heatmap field (three-pass temporal splat renderer) ── */
    this._heatmapRT          = null;   // THREE.WebGLRenderTarget — current frame (half-res, FloatType RGB)
    this._heatmapRT_prev     = null;   // THREE.WebGLRenderTarget — previous frame (ping-pong)
    this._heatmapSplatMesh   = null;   // THREE.Points — one point per live node
    this._heatmapSplatScene  = null;   // THREE.Scene used only for splat pass
    this._heatmapBlitScene   = null;   // THREE.Scene used for ping-pong blit pass (identity copy)
    this._heatmapDecayScene  = null;   // THREE.Scene used for temporal decay seed (decay × prev → current)
    this._heatmapCompScene   = null;   // THREE.Scene with fullscreen composite quad
    this._heatmapCompMat     = null;   // retained ShaderMaterial for uniform updates
    this._voxelField         = null;   // VoxelField — sparse persistent world model
    this._heatmapOrthoCamera = null;   // THREE.OrthographicCamera for 2D passes
    this._heatmapSplatPos    = null;   // Float32Array sync'd from nodeMesh
    this._heatmapSplatConf   = null;
    this._heatmapSplatLife   = null;
    this._heatmapSplatShadow = null;   // R channel: C2/stealth signal
    this._heatmapSplatAnomaly= null;   // G channel: violation/anomaly signal
    this._heatmapSplatEntropy= null;   // B channel: cluster entropy signal
    this._heatmapSplatAngle  = null;   // anisotropic smear orientation per node
    this._uHeatmapBlend      = { value: 1.0 };
    this._uCameraECEF        = { value: new THREE.Vector3() };
    this._uHeatmapInvProjView= { value: new THREE.Matrix4() };
    this._tmpProjViewMat     = new THREE.Matrix4();   // scratch — avoids per-frame alloc
    this._fieldZoneCache     = new Map();  // cell key → field-derived zone classification
    this._fieldReadbackBuf   = null;       // Float32Array for CPU readback
    this._fieldReadbackTimer = 0;          // last readback timestamp

    /* ── RF cone splat layer ── */
    this._rfConeScene    = null;
    this._rfConeMesh     = null;
    this._rfConeCount    = 0;
    this._rfConePos      = null;  // Float32Array(MAX_RF_CONES * 3) ECEF positions
    this._rfConeBearing  = null;  // Float32Array(MAX_RF_CONES)     radians from North
    this._rfConeBeam     = null;  // Float32Array(MAX_RF_CONES)     half-angle radians
    this._rfConeStrength = null;  // Float32Array(MAX_RF_CONES)     0–1

    /* ── GPU strobe shockwave layer ── */
    this._strobeData     = new Float32Array(MAX_STROBES * STROBE_FLOATS);  // ring buffer
    this._strobeCount    = 0;      // total injected (wraps via modulo)
    this._strobeTex      = null;   // THREE.DataTexture (MAX_STROBES × 1, RGBA Float)
    this._strobeDirty    = false;

    /* ── Recon Entity state (shared by init() and attachToViewer()) ── */
    this._reconEntities       = new Map();   // id → entity record
    this._reconCesiumEntities = new Map();   // id → Cesium.Entity
    this._deckReconBuffer     = [];          // deck.gl HeatmapLayer input
    this._clusterCentroids    = new Map();   // cluster_id → {lat,lon,count}
    this._uavMeshes           = new Map();   // id → {droneMesh, coneMesh}
    this._uavFrameCount       = 0;
    this._uavSyncInterval     = null;
  }

  /**
   * Resolve best-available world terrain provider across Cesium versions.
   * Falls back gracefully when the legacy helper is absent (e.g., Cesium 1.114+).
   */
  _resolveTerrainProvider() {
    const opts = { requestVertexNormals: true, requestWaterMask: true };
    try {
      if (Cesium.Terrain && typeof Cesium.Terrain.fromWorldTerrain === 'function') {
        console.log('[Globe] Using Cesium.Terrain.fromWorldTerrain()');
        return Cesium.Terrain.fromWorldTerrain(opts);
      }
      if (typeof Cesium.createWorldTerrain === 'function') {
        console.log('[Globe] Using Cesium.createWorldTerrain()');
        return Cesium.createWorldTerrain(opts);
      }
      if (Cesium.CesiumTerrainProvider && typeof Cesium.CesiumTerrainProvider.fromIonAssetId === 'function') {
        console.log('[Globe] Using CesiumTerrainProvider.fromIonAssetId(1)');
        return Cesium.CesiumTerrainProvider.fromIonAssetId(1, opts);
      }
      if (typeof Cesium.createWorldTerrainAsync === 'function') {
        console.log('[Globe] Using Cesium.createWorldTerrainAsync()');
        Cesium.createWorldTerrainAsync(opts)
          .then((provider) => {
            if (this._viewer && provider) this._viewer.terrainProvider = provider;
          })
          .catch((err) => console.warn('[Globe] Async terrain load failed', err));
      } else {
        console.warn('[Globe] World terrain not available; using ellipsoid');
      }
    } catch (err) {
      console.warn('[Globe] Terrain init error, continuing without terrain', err);
    }
    return null;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * init — set up Cesium + Three.js, start shared render loop
   * ----------------------------------------------------------------------- */
  init(cesiumContainerId, token) {
    Cesium.Ion.defaultAccessToken = token;

    const terrainObj = this._resolveTerrainProvider();
    const viewerOptions = {
      useDefaultRenderLoop: false,
      timeline: false,
      animation: false,
      baseLayerPicker: false,
      geocoder: false,
      sceneModePicker: false,
      navigationHelpButton: false,
      homeButton: false,
      scene3DOnly: true,
      infoBox: false,
      selectionIndicator: false,
      creditContainer: document.createElement('div')
    };
    // Cesium 1.114 uses `terrain:` (Cesium.Terrain object); older versions use `terrainProvider:`.
    // `fromWorldTerrain` returns a Terrain object, not a TerrainProvider.
    if (terrainObj) {
      if (terrainObj.provider || typeof terrainObj.readyEvent !== 'undefined') {
        // Cesium 1.114 Terrain wrapper object → use `terrain` key
        viewerOptions.terrain = terrainObj;
      } else {
        // Legacy TerrainProvider instance
        viewerOptions.terrainProvider = terrainObj;
      }
    }

    // ── Cesium viewer (manual render loop) ──────────────────────────────
    this._viewer = new Cesium.Viewer(cesiumContainerId, viewerOptions);
    this._viewer.scene.fog.enabled = false;
    this._viewer.scene.globe.enableLighting = true;

    // Strip Cesium's default Bing/Ion imagery so URS owns the imagery layer.
    // Without this, URS removeAll() + re-add could leave duplicate layers.
    try { this._viewer.imageryLayers.removeAll(); } catch (_) {}

    // ── Three.js renderer overlaid on Cesium canvas ──────────────────────
    const cesiumCanvas = this._viewer.scene.canvas;

    // Ensure the parent div is a positioning context so that our
    // `position: absolute` canvas anchors to it — not to <body> or some
    // outer scrolling ancestor that Cesium repositions each frame (strobe fix).
    const cesiumParent = cesiumCanvas.parentElement;
    const parentPos = window.getComputedStyle(cesiumParent).position;
    if (parentPos === 'static') cesiumParent.style.position = 'relative';
    cesiumParent.style.overflow = 'hidden';

    const cw = cesiumCanvas.clientWidth  || cesiumParent.clientWidth  || window.innerWidth;
    const ch = cesiumCanvas.clientHeight || cesiumParent.clientHeight || window.innerHeight;

    this._renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    // Pass false for updateStyle — Three.js must NOT override the CSS width/height.
    // We control display size via CSS so it always fills the parent regardless of
    // how the Cesium layout reflows.
    this._renderer.setSize(cw, ch, false);
    this._renderer.setPixelRatio(window.devicePixelRatio);
    this._renderer.autoClear = false;
    this._renderer.setClearColor(0x000000, 0);

    // Overlay the Three.js canvas exactly over the Cesium canvas.
    // inset: 0 + width/height 100% keeps it anchored to the parent
    // even when Cesium calls layout recalculations internally.
    const threeCanvas = this._renderer.domElement;
    threeCanvas.style.cssText = [
      'position: absolute',
      'inset: 0',
      'width: 100%',
      'height: 100%',
      'pointer-events: none',
    ].join('; ') + ';';
    cesiumParent.appendChild(threeCanvas);

    this._scene  = new THREE.Scene();
    this._camera = new THREE.PerspectiveCamera(60, cesiumCanvas.clientWidth / cesiumCanvas.clientHeight, 1, 1e10);
    // Disable Three.js auto-update: we manually sync camera matrices from Cesium
    // each frame via _syncCamera(). Without this, renderer.render() calls
    // updateMatrixWorld() which resets matrixWorld to identity (pos=0,0,0) and
    // overwrites our Cesium-synced view matrix → all ECEF nodes project to nowhere.
    this._camera.matrixAutoUpdate = false;

    // ── Adjacency texture ────────────────────────────────────────────────
    const adjTex = new THREE.DataTexture(
      this._adjData, ADJ_TEX_SIZE, ADJ_TEX_SIZE,
      THREE.RedFormat, THREE.FloatType
    );
    adjTex.needsUpdate = true;
    this._uAdjTex.value = adjTex;

    // ── Build GPU layers ────────────────────────────────────────────────
    this._buildNodeLayer();
    this._buildArcLayer();
    this._buildHyperedgeLayer();
    this._buildRFVolumetricLayer();
    this._buildEdgeBeamLayer();
    this._buildHeatmapLayer();

    // ── Node lifecycle animation ────────────────────────────────────────
    this._lifecycleInterval = setInterval(() => this._stepLifecycles(), 50);

    // ── Batch flush ─────────────────────────────────────────────────────
    this._batchTimer = setInterval(() => this._flushBatch(), BATCH_INTERVAL_MS);

    // ── Resize ──────────────────────────────────────────────────────────
    window.addEventListener('resize', () => this._onResize());

    // ── Cesium canvas click → node pick ─────────────────────────────────
    cesiumCanvas.addEventListener('click', (e) => this._onCanvasClick(e));

    // ── Geo labels (countries + dynamic cities) ─────────────────────────
    this._initGeoLabels();

    // ── Render loop ──────────────────────────────────────────────────────
    this._renderLoop();
    console.log('[Globe] Cesium + Three.js intelligence surface ready');
    return this;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * attachToViewer — mount GPU pipeline onto an existing Cesium.Viewer
   *
   * Use this instead of init() when the host page already owns a Cesium
   * viewer (e.g. command-ops-visualization.html).  Builds the entire
   * Three.js overlay + all GPU layers (heatmap, RF cones, voxel field,
   * nodes, arcs) without creating or reconfiguring the viewer itself.
   *
   * Usage:
   *   const globe = new CesiumHypergraphGlobe();
   *   globe.attachToViewer(viewer);          // existing Cesium.Viewer
   *   window.__URS__.attachGlobe(globe);     // URS drives render loop
   * ----------------------------------------------------------------------- */
  attachToViewer(existingViewer) {
    if (!existingViewer || !existingViewer.scene) {
      console.error('[Globe] attachToViewer requires a valid Cesium.Viewer');
      return this;
    }
    this._viewer = existingViewer;

    // Suppress our own RAF loop — URS will drive us via tickFrame + _renderThreeLayers
    this._ursAttached = true;

    // ── Three.js renderer overlaid on Cesium canvas ──────────────────────
    const cesiumCanvas = this._viewer.scene.canvas;
    const cesiumParent = cesiumCanvas.parentElement;
    const parentPos = window.getComputedStyle(cesiumParent).position;
    if (parentPos === 'static') cesiumParent.style.position = 'relative';
    cesiumParent.style.overflow = 'hidden';

    const cw = cesiumCanvas.clientWidth  || cesiumParent.clientWidth  || window.innerWidth;
    const ch = cesiumCanvas.clientHeight || cesiumParent.clientHeight || window.innerHeight;

    this._renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    this._renderer.setSize(cw, ch, false);
    this._renderer.setPixelRatio(window.devicePixelRatio);
    this._renderer.autoClear = false;
    this._renderer.setClearColor(0x000000, 0);

    const threeCanvas = this._renderer.domElement;
    threeCanvas.style.cssText = [
      'position: absolute',
      'inset: 0',
      'width: 100%',
      'height: 100%',
      'pointer-events: none',
    ].join('; ') + ';';
    cesiumParent.appendChild(threeCanvas);

    this._scene  = new THREE.Scene();
    this._camera = new THREE.PerspectiveCamera(60, cw / ch, 1, 1e10);
    this._camera.matrixAutoUpdate = false;

    // ── Adjacency texture ────────────────────────────────────────────────
    const adjTex = new THREE.DataTexture(
      this._adjData, ADJ_TEX_SIZE, ADJ_TEX_SIZE,
      THREE.RedFormat, THREE.FloatType
    );
    adjTex.needsUpdate = true;
    this._uAdjTex.value = adjTex;

    // ── Build GPU layers ────────────────────────────────────────────────
    this._buildNodeLayer();
    this._buildArcLayer();
    this._buildHyperedgeLayer();
    this._buildRFVolumetricLayer();
    this._buildEdgeBeamLayer();
    this._buildHeatmapLayer();

    // ── Node lifecycle animation ────────────────────────────────────────
    this._lifecycleInterval = setInterval(() => this._stepLifecycles(), 50);

    // ── Batch flush ─────────────────────────────────────────────────────
    this._batchTimer = setInterval(() => this._flushBatch(), BATCH_INTERVAL_MS);

    // ── Resize ──────────────────────────────────────────────────────────
    window.addEventListener('resize', () => this._onResize());

    // ── Cesium canvas click → node pick ─────────────────────────────────
    cesiumCanvas.addEventListener('click', (e) => this._onCanvasClick(e));

    // ── Geo labels ──────────────────────────────────────────────────────
    this._initGeoLabels();

    console.log('[Globe] GPU pipeline attached to existing Cesium viewer');
    return this;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _buildNodeLayer — screen-space billboard nodes via InstancedBufferGeometry
   * ----------------------------------------------------------------------- */
  _buildNodeLayer() {
    const basePlane = new THREE.PlaneGeometry(2, 2);
    const geo = new THREE.InstancedBufferGeometry();
    geo.index = basePlane.index;
    geo.setAttribute('position', basePlane.attributes.position);
    geo.setAttribute('uv',       basePlane.attributes.uv);
    if (basePlane.attributes.normal) geo.setAttribute('normal', basePlane.attributes.normal);

    // Per-instance attribute buffers
    const posArr  = new Float32Array(MAX_NODES * 3);
    const idArr   = new Float32Array(MAX_NODES);
    const confArr = new Float32Array(MAX_NODES);
    const luArr   = new Float32Array(MAX_NODES);
    const clArr   = new Float32Array(MAX_NODES).fill(1);
    const colArr  = new Float32Array(MAX_NODES * 3).fill(0.3);
    const violArr = new Float32Array(MAX_NODES);
    const lcArr   = new Float32Array(MAX_NODES);   // lifecycle

    geo.setAttribute('instancePosition',   new THREE.InstancedBufferAttribute(posArr,  3));
    geo.setAttribute('instanceId',         new THREE.InstancedBufferAttribute(idArr,   1));
    geo.setAttribute('instanceConf',       new THREE.InstancedBufferAttribute(confArr, 1));
    geo.setAttribute('instanceLastUpdate', new THREE.InstancedBufferAttribute(luArr,   1));
    geo.setAttribute('instanceCluster',    new THREE.InstancedBufferAttribute(clArr,   1));
    geo.setAttribute('instanceColor',      new THREE.InstancedBufferAttribute(colArr,  3));
    geo.setAttribute('instanceViolations', new THREE.InstancedBufferAttribute(violArr, 1));
    geo.setAttribute('instanceLifecycle',  new THREE.InstancedBufferAttribute(lcArr,   1));

    const mat = new THREE.ShaderMaterial({
      uniforms: {
        uTime:       this._uTime,
        uSelectedId: this._uSelectedId,
        uAdjTex:     this._uAdjTex,
        uViewHeight: this._uViewHeight
      },
      vertexShader:   NODE_VERT,
      fragmentShader: NODE_FRAG,
      transparent:    true,
      depthWrite:     false
    });

    this._nodeMesh = new THREE.Mesh(geo, mat);
    geo.instanceCount = 0;
    this._nodeMesh.frustumCulled = false;
    this._nodeMesh.renderOrder = 1;   // nodes below arcs; labels (Cesium) always on top
    this._scene.add(this._nodeMesh);
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _buildArcLayer — GPU Instanced Arc Renderer
   *
   * InstancedBufferGeometry with:
   *   Template: (ARC_TEMPLATE_PTS-1)*2 vertices, aT=0→1 repeated as line-pairs
   *   Per-instance: iStart/iEnd ECEF, iConf, iEntropy, iRfCorr, iShadow,
   *                 iTimeOff, iLastSeen, iEdgeId
   * Vertex shader performs SLERP on the unit sphere, adds proportional arc lift.
   * Supports up to MAX_ARC_INSTANCES simultaneous arcs with a single draw call.
   * ----------------------------------------------------------------------- */
  _buildArcLayer() {
    const N    = MAX_ARC_INSTANCES;
    const PTS  = ARC_TEMPLATE_PTS;
    const SEGS = PTS - 1;
    const VERTS = SEGS * 2;    // LineSegments pairs: [t0,t1, t1,t2, ...]

    // ── Template geometry: spine t-values, dummy positions (overridden by GPU slerp)
    const tArr  = new Float32Array(VERTS);
    const posArr = new Float32Array(VERTS * 3);  // all zeros (unused by vertex shader)

    for (let i = 0; i < SEGS; i++) {
      tArr[i * 2]     = i       / SEGS;
      tArr[i * 2 + 1] = (i + 1) / SEGS;
    }

    this._arcGeo = new THREE.InstancedBufferGeometry();
    this._arcGeo.setAttribute('position', new THREE.BufferAttribute(posArr, 3));
    this._arcGeo.setAttribute('aT',       new THREE.BufferAttribute(tArr,   1));

    // ── Per-instance CPU-side storage (written by _rebuildArcBuffers)
    this._arcInstStart    = new Float32Array(N * 3);
    this._arcInstEnd      = new Float32Array(N * 3);
    this._arcInstConf     = new Float32Array(N);
    this._arcInstEntropy  = new Float32Array(N);
    this._arcInstRfCorr   = new Float32Array(N);
    this._arcInstShadow   = new Float32Array(N);
    this._arcInstAnomaly  = new Float32Array(N);
    this._arcInstTimeOff  = new Float32Array(N);
    this._arcInstLastSeen = new Float32Array(N);
    this._arcInstEdgeId   = new Float32Array(N);

    this._arcGeo.setAttribute('iStart',    new THREE.InstancedBufferAttribute(this._arcInstStart,    3));
    this._arcGeo.setAttribute('iEnd',      new THREE.InstancedBufferAttribute(this._arcInstEnd,      3));
    this._arcGeo.setAttribute('iConf',     new THREE.InstancedBufferAttribute(this._arcInstConf,     1));
    this._arcGeo.setAttribute('iEntropy',  new THREE.InstancedBufferAttribute(this._arcInstEntropy,  1));
    this._arcGeo.setAttribute('iRfCorr',   new THREE.InstancedBufferAttribute(this._arcInstRfCorr,   1));
    this._arcGeo.setAttribute('iShadow',   new THREE.InstancedBufferAttribute(this._arcInstShadow,   1));
    this._arcGeo.setAttribute('iAnomaly',  new THREE.InstancedBufferAttribute(this._arcInstAnomaly,  1));
    this._arcGeo.setAttribute('iTimeOff',  new THREE.InstancedBufferAttribute(this._arcInstTimeOff,  1));
    this._arcGeo.setAttribute('iLastSeen', new THREE.InstancedBufferAttribute(this._arcInstLastSeen, 1));
    this._arcGeo.setAttribute('iEdgeId',   new THREE.InstancedBufferAttribute(this._arcInstEdgeId,   1));

    this._arcGeo.instanceCount = 0;
    // Large bounding sphere so frustumCulled=false is coherent
    this._arcGeo.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), 8e6);

    const mat = new THREE.ShaderMaterial({
      uniforms: {
        uTime:       this._uTime,
        uSelectedId: this._uSelectedId,
        uQuality:    this._uQuality
      },
      vertexShader:   ARC_VERT,
      fragmentShader: ARC_FRAG,
      transparent:    true,
      depthWrite:     false,
      blending:       THREE.AdditiveBlending
    });

    this._arcMesh = new THREE.LineSegments(this._arcGeo, mat);
    this._arcMesh.frustumCulled = false;
    this._arcMesh.renderOrder = 2;   // arcs above nodes
    this._scene.add(this._arcMesh);
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _buildHyperedgeLayer — vertical cluster flare field above projected centroids
   * ----------------------------------------------------------------------- */
  _buildHyperedgeLayer() {
    const count = MAX_HYPEREDGES * MAX_HE_PARTICLES;
    const geo   = new THREE.BufferGeometry();

    const POS  = new Float32Array(count * 3);
    const PID  = new Float32Array(count);   // particle id within cluster
    const HCON = new Float32Array(count);
    const HHGT = new Float32Array(count);
    const HWID = new Float32Array(count);

    for (let i = 0; i < count; i++) {
      PID[i] = i % MAX_HE_PARTICLES;
      HHGT[i] = 60_000;
      HWID[i] = 18_000;
    }

    geo.setAttribute('position',    new THREE.BufferAttribute(POS,  3));
    geo.setAttribute('aParticleId', new THREE.BufferAttribute(PID,  1));
    geo.setAttribute('aHEConf',     new THREE.BufferAttribute(HCON, 1));
    geo.setAttribute('aHEHeight',   new THREE.BufferAttribute(HHGT, 1));
    geo.setAttribute('aHEWidth',    new THREE.BufferAttribute(HWID, 1));
    geo.setDrawRange(0, 0);

    const mat = new THREE.ShaderMaterial({
      uniforms: { uTime: this._uTime, uColor: { value: new THREE.Color(0.3, 0.7, 1.0) } },
      vertexShader:   HE_VERT,
      fragmentShader: HE_FRAG,
      transparent:    true,
      depthWrite:     false,
      blending:       THREE.AdditiveBlending
    });

    this._heMesh = new THREE.Points(geo, mat);
    this._heMesh.frustumCulled = false;
    this._heMesh.renderOrder = 3;   // cluster flare above arcs
    this._heMesh.visible = this._clusterFlareVisible;
    this._scene.add(this._heMesh);
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Render loop — Cesium then Three.js, camera synced each frame
   * ----------------------------------------------------------------------- */
  _renderLoop() {
    // If URS is attached it will call _syncCamera + Three.js render each frame.
    // Run our own RAF only when standalone (no URS).
    if (this._ursAttached) return;

    let _frame = 0;
    const loop = () => {
      requestAnimationFrame(loop);
      _frame++;
      const t = performance.now() * 0.001;
      this._uTime.value = t;

      this._viewer.render();
      this._governor.endFrame(this._renderer.getContext());
      this._syncCamera();
      this._tickBloom(t);

      this._renderThreeLayers(_frame);

      // Poll previous frame timer, update quality uniform
      const gl = this._renderer.getContext();
      this._governor.poll(gl);
      this._governor.beginFrame(gl);
      this._uQuality.value = this._governor.quality;
    };
    loop();
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _renderThreeLayers — hybrid heatmap + node render
   * Called by standalone render loop and by URS._renderGlobe().
   * Camera altitude thresholds:
   *   > 3 Mm  → heatmap only (nodes hidden)
   *   0.8–3 Mm → heatmap + nodes (crossfade)
   *   < 0.8 Mm → nodes only
   * ----------------------------------------------------------------------- */
  _renderThreeLayers(frame = 0) {
    const camHeight = this._viewer.camera.positionCartographic?.height ?? 5e6;
    const showHeatmap = camHeight > 800_000 && this._heatmapRT !== null;
    const showNodes   = camHeight < 3_000_000;
    // Crossfade blend: 0 at 3 Mm (heatmap dominates) → 1 at 800 km (full heatmap intensity)
    const heatmapBlend = showNodes
      ? Math.min(1, (camHeight - 800_000) / 2_200_000)
      : 1.0;

    this._nodeMesh.visible = showNodes;

    this.updateUAVMovement();

    this._renderer.setRenderTarget(null);
    this._renderer.clear(true, true, true);
    this._renderer.render(this._scene, this._camera);

    // Heatmap runs at ~30 Hz (every other frame) — halves fill-rate cost with
    // no perceptible quality loss since the field changes slowly.
    if (showHeatmap && (frame & 1) === 0) this._renderHeatmapPass(heatmapBlend);
  }

  /* Called by URS each frame instead of the internal RAF loop */
  tickFrame() {
    const t = performance.now() * 0.001;
    this._uTime.value = t;
    this._tickBloom(t);
    const gl = this._renderer.getContext();
    this._governor.endFrame(gl);   // end query started last frame
    this._governor.poll(gl);
    this._governor.beginFrame(gl);
    this._uQuality.value = this._governor.quality;
  }

  _tickBloom(t) {
    if (!this._bloomActive) return;
    const elapsed = (t - this._bloomStart);
    if (elapsed < 3.0) {
      const pulse = Math.max(0, Math.sin(elapsed * Math.PI * 3) * Math.exp(-elapsed));
      this._bloomNodes.forEach(idx => {
        const attr = this._nodeMesh.geometry.attributes.instanceConf;
        attr.array[idx] = Math.min(1, (attr.array[idx] || 0) + pulse * 0.3);
        attr.needsUpdate = true;
      });
    } else {
      this._bloomActive = false;
    }
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Geo Labels — country anchors + activity-driven city labels
   * ----------------------------------------------------------------------- */

  /** Static curated country label seed + top cities index */
  static get ZONE_STYLES() {
    return {
      'Exfiltration Hub':   { r: 1.0, g: 0.4, b: 0.0, pulse: true  },
      'Beacon Cluster':     { r: 0.6, g: 0.0, b: 1.0, pulse: true  },
      'Scan Surface':       { r: 1.0, g: 0.0, b: 0.1, pulse: false },
      'C2 Relay':           { r: 1.0, g: 0.0, b: 0.6, pulse: true  },
      'High-Entropy Field': { r: 1.0, g: 0.8, b: 0.0, pulse: false },
      'RF Correlation Zone':{ r: 0.0, g: 0.9, b: 1.0, pulse: false },
      'General Activity':   { r: 0.2, g: 0.6, b: 1.0, pulse: false },
    };
  }

  static get COUNTRY_LABELS() {
    // Fallback used until async geo data loads
    return CesiumHypergraphGlobe._geoCountries || [
      { name: 'USA',          lat:  39.8,   lon:  -98.6  },
      { name: 'China',        lat:  35.8,   lon:  104.1  },
      { name: 'Russia',       lat:  61.5,   lon:  105.3  },
      { name: 'India',        lat:  22.3,   lon:   78.9  },
      { name: 'Brazil',       lat: -14.2,   lon:  -51.9  },
      { name: 'UK',           lat:  55.4,   lon:   -3.4  },
      { name: 'France',       lat:  46.2,   lon:    2.2  },
      { name: 'Germany',      lat:  51.2,   lon:   10.5  },
      { name: 'Australia',    lat: -25.3,   lon:  133.8  },
      { name: 'Japan',        lat:  36.2,   lon:  138.3  },
    ];
  }

  static get CITY_INDEX() {
    // Fallback used until async geo data loads
    return CesiumHypergraphGlobe._geoCities || [
      { name: 'New York',    lat:  40.71, lon:  -74.01 },
      { name: 'London',      lat:  51.51, lon:   -0.13 },
      { name: 'Tokyo',       lat:  35.69, lon:  139.69 },
      { name: 'Beijing',     lat:  39.91, lon:  116.39 },
      { name: 'Moscow',      lat:  55.75, lon:   37.62 },
      { name: 'Mumbai',      lat:  19.08, lon:   72.88 },
      { name: 'São Paulo',   lat: -23.55, lon:  -46.63 },
      { name: 'Cairo',       lat:  30.04, lon:   31.24 },
      { name: 'Lagos',       lat:   6.52, lon:    3.38 },
      { name: 'Singapore',   lat:   1.35, lon:  103.82 },
    ];
  }

  /**
   * Load geo label data from JSON assets and refresh labels.
   * Called once after init(); silently no-ops if assets are unavailable.
   * Assets: /assets/geo_countries.json, /assets/geo_cities.json
   */
  async _loadGeoData(assetsBase = '') {
    try {
      const [cRes, citRes] = await Promise.all([
        fetch(`${assetsBase}/assets/geo_countries.json`),
        fetch(`${assetsBase}/assets/geo_cities.json`),
      ]);

      if (cRes.ok) {
        const raw = await cRes.json();
        // Normalize: dataset uses {n, la, lo, cap}; internal API uses {name, lat, lon}
        CesiumHypergraphGlobe._geoCountries = raw.map(r => ({
          name: r.n, lat: r.la, lon: r.lo, capital: r.cap || '', iso2: r.iso2 || ''
        }));
        // Build a fast Set of capital city names for priority rendering
        CesiumHypergraphGlobe._capitalNames = new Set(
          CesiumHypergraphGlobe._geoCountries
            .filter(c => c.capital)
            .map(c => c.capital.toLowerCase())
        );
        console.log(`[Globe] 🌍 Loaded ${CesiumHypergraphGlobe._geoCountries.length} country labels, ${CesiumHypergraphGlobe._capitalNames.size} capitals`);
      }

      if (citRes.ok) {
        const raw = await citRes.json();
        const capNames = CesiumHypergraphGlobe._capitalNames || new Set();
        CesiumHypergraphGlobe._geoCities = raw.map(r => ({
          name: r.n, lat: r.la, lon: r.lo,
          isCapital: capNames.has(r.n.toLowerCase())
        }));
        const capCount = CesiumHypergraphGlobe._geoCities.filter(c => c.isCapital).length;
        console.log(`[Globe] 🏙️  Loaded ${CesiumHypergraphGlobe._geoCities.length} city labels (${capCount} capitals)`);
        // Build spatial index now that cities are loaded
        this._buildCityGridIndex();
      }

      // Rebuild static country labels now that real data is available
      if (this._labelCollection) {
        const toRemove = [];
        for (let i = 0; i < this._labelCollection.length; i++) {
          const lbl = this._labelCollection.get(i);
          if (lbl._isCountry) toRemove.push(lbl);
        }
        toRemove.forEach(l => this._labelCollection.remove(l));
        this._seedCountryLabels();
      }
    } catch (err) {
      console.warn('[Globe] Geo data load failed, using fallback labels:', err.message);
    }
  }

  _haversineKm(lat1, lon1, lat2, lon2) {
    const R = 6371;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) ** 2 +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  /**
   * Build a 4°-cell spatial grid index over the city list for O(1) range queries.
   * Called automatically after _loadGeoData() populates _geoCities.
   */
  _buildCityGridIndex() {
    const G = 4; // grid cell size in degrees
    const idx = new Map();
    for (const city of CesiumHypergraphGlobe.CITY_INDEX) {
      const cr = Math.round(city.lat / G);
      const cc = Math.round(city.lon / G);
      // Insert into own cell and all 8 neighbours so radius queries don't miss edges
      for (let dr = -1; dr <= 1; dr++) {
        for (let dc = -1; dc <= 1; dc++) {
          const key = `${cr + dr},${cc + dc}`;
          if (!idx.has(key)) idx.set(key, []);
          idx.get(key).push(city);
        }
      }
    }
    this._cityGridIndex = idx;
    this._cityGridCellSize = G;
  }

  _findNearbyCities(lat, lon, radiusKm = 400) {
    // Use grid index when available (built after async geo load)
    let candidates;
    if (this._cityGridIndex) {
      const G = this._cityGridCellSize;
      const cr = Math.round(lat / G);
      const cc = Math.round(lon / G);
      const key = `${cr},${cc}`;
      candidates = this._cityGridIndex.get(key) || [];
    } else {
      candidates = CesiumHypergraphGlobe.CITY_INDEX;
    }

    // Distance filter; capitals attract from 40% wider radius (gravity bias)
    return candidates.filter(c => {
      const km = this._haversineKm(lat, lon, c.lat, c.lon);
      return km < (c.isCapital ? radiusKm * 1.4 : radiusKm);
    });
  }

  _initGeoLabels() {
    this._labelCollection = this._viewer.scene.primitives.add(
      new Cesium.LabelCollection()
    );

    // ── Static country anchors (seeded now, replaced after async load) ────
    this._seedCountryLabels();

    // ── Dynamic city labels updated on a slow interval ────────────────────
    this._geoLabelsInterval = setInterval(() => this._updateDynamicCityLabels(), 2000);

    // ── Semantic zone layer ────────────────────────────────────────────────
    const ds = new Cesium.CustomDataSource('semantic-zones');
    this._viewer.dataSources.add(ds);
    this._semanticDataSource = ds;
    this._zoneHistory = new Map(); // cell → { typeHistory[], smoothedIntensity }
    this._semanticZoneInterval = setInterval(() => this._updateSemanticZones(), 3000);
  }

  _seedCountryLabels() {
    CesiumHypergraphGlobe.COUNTRY_LABELS.forEach(c => {
      this._labelCollection.add({
        position: Cesium.Cartesian3.fromDegrees(c.lon, c.lat, 50000),
        text: c.name,
        font: '700 16px "Share Tech Mono", monospace',
        fillColor: Cesium.Color.fromCssColorString('#a8d8ff').withAlpha(0.90),
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 3,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        scale: 1.1,
        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
        verticalOrigin: Cesium.VerticalOrigin.CENTER,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(8e5, 1.2e7),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        _isCountry: true   // tag for update pass
      });
    });
  }

  _classifyZone(features) {
    const { shadowRatio, anomalyAvg, entropyAvg, rfCorrAvg, edgeCount, outRatio } = features;
    if (shadowRatio > 0.3 || anomalyAvg > 0.7) return 'C2 Relay';
    if (rfCorrAvg > 0.6)                        return 'RF Correlation Zone';
    if (outRatio > 0.75 && edgeCount > 5)       return 'Exfiltration Hub';
    if (entropyAvg > 0.75)                       return 'High-Entropy Field';
    if (edgeCount > 20 && anomalyAvg > 0.4)     return 'Scan Surface';
    if (edgeCount > 8)                           return 'Beacon Cluster';
    return 'General Activity';
  }

  _updateSemanticZones() {
    if (!this._semanticDataSource) return;
    const ds = this._semanticDataSource;
    ds.entities.removeAll();

    const edges = this._getVisibleEdges().slice(0, 500);
    if (edges.length < 3) return;

    // Adaptive grid size: coarse at global altitude, fine when zoomed in
    const altM = this._viewer.camera.positionCartographic?.height ?? 1e7;
    const cellDeg = altM > 8e6 ? 5 : altM > 3e6 ? 3 : altM > 1e6 ? 2 : 1;

    // Bucket edge midpoints onto adaptive grid
    const buckets = new Map();
    edges.forEach(e => {
      let lat = e.srcLat, lon = e.srcLon;
      if ((!lat && !lon) && e.srcPos) {
        const c = Cesium.Cartographic.fromCartesian(e.srcPos);
        lat = Cesium.Math.toDegrees(c.latitude);
        lon = Cesium.Math.toDegrees(c.longitude);
      }
      if (!lat && !lon) return;
      const cell = `${Math.round(lat / cellDeg)},${Math.round(lon / cellDeg)}`;
      if (!buckets.has(cell)) {
        buckets.set(cell, { lat: 0, lon: 0, edges: [], count: 0 });
      }
      const b = buckets.get(cell);
      b.lat += lat; b.lon += lon; b.count++;
      b.edges.push(e);
    });

    buckets.forEach((b, cell) => {
      if (b.count < 3) return;
      const centerLat = b.lat / b.count;
      const centerLon = b.lon / b.count;
      const edgeList  = b.edges;
      const n = edgeList.length;

      // Extract zone features
      let shadowSum = 0, anomalySum = 0, entropySum = 0, rfCorrSum = 0, outCount = 0;
      edgeList.forEach(e => {
        shadowSum  += e.shadow  || 0;
        anomalySum += e.anomaly_smoothed ?? e.anomaly ?? 0;
        entropySum += e.entropy ?? 0.5;
        rfCorrSum  += e.rfCorr  ?? 0;
      });
      const features = {
        shadowRatio: shadowSum / n,
        anomalyAvg:  anomalySum / n,
        entropyAvg:  entropySum / n,
        rfCorrAvg:   rfCorrSum / n,
        edgeCount:   n,
        outRatio:    outCount / n,
      };

      const rawLabel    = this._classifyZone(features);
      const rawIntensity = Math.min(1.0, n / 30);

      // ── Field-driven override: if _fieldReadback classified this cell, prefer it ──
      // Use the same 5° lat/lon bucketing key that _fieldReadback uses
      const fieldKey = `${Math.round(centerLat / 5) * 5}_${Math.round(centerLon / 5) * 5}`;
      const fieldCell = this._fieldZoneCache?.get(fieldKey);
      const effectiveLabel = (fieldCell && fieldCell.intensity > 0.3)
        ? fieldCell.type   // field signal is strong → trust GPU-derived type
        : rawLabel;        // fall back to edge-bucketing

      // ── Temporal hysteresis: stabilise zone type + smooth intensity ──────
      if (!this._zoneHistory.has(cell)) {
        this._zoneHistory.set(cell, { typeHistory: [], smoothedIntensity: rawIntensity });
      }
      const hist = this._zoneHistory.get(cell);

      // Rolling type window (last 5 reads)
      hist.typeHistory.push(effectiveLabel);
      if (hist.typeHistory.length > 5) hist.typeHistory.shift();

      // Majority-vote stable type; require >0.6 confidence to switch
      const typeCounts = {};
      hist.typeHistory.forEach(t => { typeCounts[t] = (typeCounts[t] || 0) + 1; });
      const [majorType, majorCount] = Object.entries(typeCounts)
        .sort((a, b) => b[1] - a[1])[0];
      const confidence = majorCount / hist.typeHistory.length;
      const stableLabel = confidence >= 0.6 ? majorType : (hist.stableLabel || rawLabel);
      hist.stableLabel = stableLabel;

      // Lerp intensity (0.25 blend → smooth, not instant)
      hist.smoothedIntensity += (rawIntensity - hist.smoothedIntensity) * 0.25;
      const intensity = hist.smoothedIntensity;

      if (stableLabel === 'General Activity' && n < 8) return;
      const style = CesiumHypergraphGlobe.ZONE_STYLES[stableLabel] || CesiumHypergraphGlobe.ZONE_STYLES['General Activity'];

      // Radius: proportional to edge count, 80km min, 500km max
      const radiusM = Math.min(500000, Math.max(80000, n * 15000));

      const fillAlpha    = 0.08 + intensity * 0.10;
      const outlineAlpha = 0.4  + intensity * 0.3;

      const color = Cesium.Color.fromBytes(
        Math.round(style.r * 255),
        Math.round(style.g * 255),
        Math.round(style.b * 255),
        Math.round(fillAlpha * 255)
      );
      const outlineColor = Cesium.Color.fromBytes(
        Math.round(style.r * 255),
        Math.round(style.g * 255),
        Math.round(style.b * 255),
        Math.round(outlineAlpha * 255)
      );

      ds.entities.add({
        position: Cesium.Cartesian3.fromDegrees(centerLon, centerLat),
        ellipse: {
          semiMajorAxis: radiusM,
          semiMinorAxis: radiusM * 0.75,
          material: new Cesium.ColorMaterialProperty(
            style.pulse
              ? new Cesium.CallbackProperty(() => {
                  const pulse = 0.5 + 0.5 * Math.sin(Date.now() * 0.002);
                  return Cesium.Color.fromBytes(
                    Math.round(style.r * 255),
                    Math.round(style.g * 255),
                    Math.round(style.b * 255),
                    Math.round((fillAlpha + pulse * 0.06) * 255)
                  );
                }, false)
              : color
          ),
          outline: true,
          outlineColor: outlineColor,
          outlineWidth: 1.5,
          height: 0,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        }
      });

      // Semantic label with zone intensity as sub-text
      const edgeLabel = n >= 15 ? ` (${n})` : '';
      this._labelCollection.add({
        position: Cesium.Cartesian3.fromDegrees(centerLon, centerLat, 80000),
        text: stableLabel + edgeLabel,
        font: '700 13px "Share Tech Mono", monospace',
        fillColor: Cesium.Color.fromBytes(
          Math.round(style.r * 255),
          Math.round(style.g * 255),
          Math.round(style.b * 255),
          230
        ),
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 3,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        scale: 0.9,
        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
        verticalOrigin: Cesium.VerticalOrigin.CENTER,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(5e4, 5e6),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        _isCountry: false,
      });
    });

    // Prune stale zone history entries (cells with no recent activity)
    if (this._zoneHistory.size > 200) {
      const activeCells = new Set(buckets.keys());
      for (const k of this._zoneHistory.keys()) {
        if (!activeCells.has(k)) this._zoneHistory.delete(k);
      }
    }
  }

  _updateDynamicCityLabels() {
    if (!this._labelCollection) return;

    // Remove all non-country labels
    const toRemove = [];
    for (let i = 0; i < this._labelCollection.length; i++) {
      const lbl = this._labelCollection.get(i);
      if (!lbl._isCountry) toRemove.push(lbl);
    }
    toRemove.forEach(l => this._labelCollection.remove(l));

    // Derive hot zones from visible edge midpoints
    const edges = this._getVisibleEdges().slice(0, 300);
    const hotZones = [];

    edges.forEach(e => {
      let srcLat = e.srcLat, srcLon = e.srcLon;
      let dstLat = e.dstLat, dstLon = e.dstLon;

      if ((!srcLat && !srcLon) && e.srcPos) {
        const carto = Cesium.Cartographic.fromCartesian(e.srcPos);
        srcLat = Cesium.Math.toDegrees(carto.latitude);
        srcLon = Cesium.Math.toDegrees(carto.longitude);
      }
      if ((!dstLat && !dstLon) && e.dstPos) {
        const carto = Cesium.Cartographic.fromCartesian(e.dstPos);
        dstLat = Cesium.Math.toDegrees(carto.latitude);
        dstLon = Cesium.Math.toDegrees(carto.longitude);
      }

      if (srcLat != null && dstLat != null) {
        hotZones.push({ lat: (srcLat + dstLat) / 2, lon: (srcLon + dstLon) / 2 });
      }
    });

    // Deduplicate into 3° grid cells
    const seenCells = new Set();
    const dedupedZones = hotZones.filter(z => {
      const cell = `${Math.round(z.lat / 3)},${Math.round(z.lon / 3)}`;
      if (seenCells.has(cell)) return false;
      seenCells.add(cell);
      return true;
    });

    if (this._observerLat !== 0 || this._observerLon !== 0) {
      dedupedZones.unshift({ lat: this._observerLat, lon: this._observerLon, isObserver: true });
    }

    // ── Label arbitration: score all candidates, budget 3 per 5° tile ────
    const MAX_PER_TILE = 3;
    const TILE_DEG     = 5;
    const tileBudget   = new Map();
    const candidates   = [];

    dedupedZones.slice(0, 20).forEach(zone => {
      const nearKm = zone.isObserver ? 600 : 350;
      const cities = this._findNearbyCities(zone.lat, zone.lon, nearKm);

      cities.forEach(c => {
        const distKm  = this._haversineKm(zone.lat, zone.lon, c.lat, c.lon);
        const isNearObs = this._observerLat !== 0 &&
          this._haversineKm(this._observerLat, this._observerLon, c.lat, c.lon) < 600;

        // Arbitration score (higher = wins tile slot)
        const score = (c.isCapital ? 50 : 0)
                    + (isNearObs   ? 40 : 0)
                    + Math.max(0, 30 - distKm / 20);  // proximity bonus

        candidates.push({ city: c, score, isNearObs });
      });
    });

    // Sort globally by score descending, then allocate within tiles
    candidates.sort((a, b) => b.score - a.score);

    const addedCityNames = new Set();
    for (const { city: c, score, isNearObs } of candidates) {
      if (addedCityNames.has(c.name)) continue;

      const tileKey = `${Math.floor(c.lat / TILE_DEG)},${Math.floor(c.lon / TILE_DEG)}`;
      const tileCount = tileBudget.get(tileKey) || 0;
      if (tileCount >= MAX_PER_TILE) continue;

      tileBudget.set(tileKey, tileCount + 1);
      addedCityNames.add(c.name);

      const fillColor = isNearObs
        ? Cesium.Color.fromCssColorString('#ffe066').withAlpha(1.0)
        : c.isCapital
          ? Cesium.Color.fromCssColorString('#ffffff').withAlpha(0.95)
          : Cesium.Color.fromCssColorString('#00e5ff').withAlpha(0.85);

      this._labelCollection.add({
        position: Cesium.Cartesian3.fromDegrees(c.lon, c.lat, 50000),
        text: c.isCapital ? `⬡ ${c.name}` : c.name,
        font: `${(isNearObs || c.isCapital) ? '700' : '500'} ${c.isCapital ? 15 : 14}px "Share Tech Mono", monospace`,
        fillColor,
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 3,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        scale: isNearObs ? 1.0 : c.isCapital ? 0.95 : 0.85,
        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
        verticalOrigin: Cesium.VerticalOrigin.CENTER,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(1e4, c.isCapital ? 6e6 : 4e6),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        _isCountry: false
      });
    }
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _syncCamera — copy Cesium camera matrices to Three.js camera each frame
   * ----------------------------------------------------------------------- */
  _syncCamera() {
    const cv = this._viewer.camera;

    // View matrix: Cesium Matrix4 → Three.js Matrix4 (both column-major)
    const vm = new Float64Array(16);
    Cesium.Matrix4.toArray(cv.viewMatrix, vm);
    this._camera.matrixWorldInverse.fromArray(vm);
    this._camera.matrixWorld.copy(this._camera.matrixWorldInverse).invert();

    // Projection matrix
    const pm = new Float64Array(16);
    Cesium.Matrix4.toArray(cv.frustum.projectionMatrix, pm);
    this._camera.projectionMatrix.fromArray(pm);
    this._camera.projectionMatrixInverse.copy(this._camera.projectionMatrix).invert();

    // Sync Deck.gl bridge if attached
    this._deckBridge?.sync();
  }

  /* ───────────────────────────────────────────────────────────────────────
   * WebSocket / SSE connection
   * ----------------------------------------------------------------------- */
  connectStream(socketIOUrl, apiBase = '', token = null) {
    this._apiBase = apiBase;
    this._streamToken = token || (window.SCYTHE_AUTH ? SCYTHE_AUTH.restoreSession() : null) || localStorage.getItem('scythe_session_token') || null;
    this._reconnectAttempts = 0;
    this._lastEventTs = Date.now();
    this._lastEdgeTs  = 0;   // epoch-seconds; tracks highest edge timestamp seen

    if (typeof io === 'undefined') {
      console.warn('[Globe] socket.io client not found — using SSE fallback');
      this._connectSSE(apiBase);
      return;
    }

    // Wait for the server to be healthy before opening the socket.
    // This eliminates the race on fresh instance spin-up where the WS
    // upgrade is rejected because the backend isn't ready yet.
    this._waitForServer(apiBase).then(() => this._doConnect(socketIOUrl));
  }

  async _waitForServer(apiBase, maxTries = 20, intervalMs = 300) {
    // If the orchestrator URL is available (injected via bootstrap), poll
    // /api/scythe/ready — which only returns 200 once Socket.IO is confirmed
    // accepting connections on the child instance.
    const orchUrl = (window.__SCYTHE_BOOTSTRAP__ || {}).orchestrator_url;
    if (orchUrl) {
      const readyUrl = `${orchUrl}/api/scythe/ready?wait=1`;
      for (let i = 0; i < maxTries; i++) {
        try {
          const r = await fetch(readyUrl, { cache: 'no-store' });
          if (r.ok) {
            const data = await r.json();
            console.info('[Globe] orchestrator ready:', data);
            return;
          }
        } catch (_) {}
        await new Promise(res => setTimeout(res, intervalMs));
      }
      console.warn('[Globe] orchestrator /api/scythe/ready timed out — falling back to health check');
    }

    // Direct health check (fallback when no orchestrator is configured)
    const url = `${apiBase}/api/health`;
    for (let i = 0; i < maxTries; i++) {
      try {
        const r = await fetch(url, { cache: 'no-store' });
        if (r.ok) return;
      } catch (_) {}
      await new Promise(res => setTimeout(res, intervalMs));
    }
    // Proceed anyway after timeout — server may be partially up
    console.warn('[Globe] /api/health not reachable — connecting anyway');
  }

  _doConnect(socketIOUrl) {
    this._socketIOUrl = socketIOUrl;

    if (this._socket) {
      this._socket.removeAllListeners();
      this._socket.close();
    }

    const token = this._streamToken;

    // When api_base includes a path (orchestrator /scythe/i/<id>/ proxy), Socket.IO
    // must use site origin + explicit `path` (not a URL with a pathname as the first arg).
    let connectUrl = socketIOUrl;
    if (/^wss?:\/\//i.test(connectUrl)) {
      connectUrl = connectUrl.replace(/^ws/i, 'http');
    }
    const proxiedSocket = Boolean(window.__SCYTHE_BOOTSTRAP__?.path_prefix);
    const ioOpts = {
      transports: proxiedSocket ? ['polling'] : ['polling', 'websocket'],
      upgrade: !proxiedSocket,
      withCredentials: false,
      forceNew: true,
      reconnection: false,     // we manage reconnect manually for auth-aware retry
      timeout: 10000,
      query: token ? { token } : {},
      auth:  token ? { token } : {},
    };
    try {
      const u = new URL(connectUrl, typeof location !== 'undefined' ? location.href : undefined);
      ioOpts.secure = u.protocol === 'https:';
      if (typeof window !== 'undefined' && window.__SCYTHE_BOOTSTRAP__ && window.__SCYTHE_BOOTSTRAP__.socketio_path) {
        ioOpts.path = window.__SCYTHE_BOOTSTRAP__.socketio_path;
        connectUrl = u.origin;
      }
    } catch (_) {
      ioOpts.secure = /^https/i.test(connectUrl);
    }

    this._socket = io(connectUrl, ioOpts);

    this._socket.on('connect', () => {
      console.log('[Globe] ✅ SocketIO connected via', this._socket.io.engine.transport.name,
                  token ? '(authenticated)' : '(anonymous)');
      this._reconnectAttempts = 0;
      this._wsFallbackAttempted = false;

      this._socket.emit('subscribe_edges', {
        scope: { type: 'all', min_weight: CONF_CULL_THRESHOLD, since_secs: 3600 },
        since: this._lastEdgeTs > 0 ? this._lastEdgeTs : undefined,
      });

      this._startStreamHeartbeat();
    });

    // Soft transport fallback: if WS upgrade fails on a strict proxy, retry polling once
    this._socket.on('connect_error', (err) => {
      console.warn('[Globe] ⚠ Connect error:', err.message);
      if (!this._wsFallbackAttempted) {
        this._wsFallbackAttempted = true;
        console.warn('[Globe] Retrying with polling fallback');
        this._socket.io.opts.transports = ['polling'];
        this._socket.io.opts.upgrade = false;
        this._socket.connect();
        return;
      }
      this._scheduleStreamReconnect();
    });

    this._socket.on('subscribed', (data) => {
      this._scopeId = data.scope_id;
      console.log('[Globe] Edge stream scope:', this._scopeId);
    });

    this._socket.on('edges', (msg) => {
      this._lastEventTs = Date.now();
      this._onEdgesEvent(msg);
    });

    // Replay batch sent by server on reconnect when _lastEdgeTs > 0
    this._socket.on('edges_replay', (msg) => {
      const edges = msg.edges || [];
      if (edges.length) {
        console.log(`[Globe] ⏪ Replay: ${edges.length} edges since ${msg.since}`);
        this._onEdgesEvent({ edges });
      }
    });

    this._socket.on('entity_update', (ev) => {
      this._lastEventTs = Date.now();
      this._queueUpdate({ type: 'node_update', ...ev });
    });
    this._socket.on('entity_delete', (ev) => this._queueUpdate({ type: 'node_remove', id: ev.entity_id }));

    this._socket.on('graphops_convergence', (ev) => this._onConvergence(ev));
    this._socket.on('graphops_suggest',     (ev) => this._onSuggestedPrompts(ev));

    // RF classification results from /api/rf/classify → inject into voxel world model
    this._socket.on('rf_classification', (ev) => {
      if (ev.lat != null && ev.lon != null) {
        this.injectPointVoxel(ev.lat, ev.lon, 20000,
          (ev.confidence ?? 0.5) * 0.8,  // rf
          0,                              // network (not a network event)
          ev.confidence ?? 0.3            // classification confidence
        );
        if (ev.bearing_deg != null) {
          this.injectRfBearing(ev.lat, ev.lon, ev.bearing_deg, 30, ev.confidence ?? 0.5);
        }
      }
    });

    this._socket.on('disconnect', (reason) => {
      console.warn('[Globe] ⚠ SocketIO disconnected:', reason);
      clearInterval(this._streamHeartbeat);
      // 'io server disconnect' = server kicked us (bad token / auth expiry).
      // Re-auth before reconnecting.
      if (reason === 'io server disconnect') {
        console.warn('[Globe] Server-initiated disconnect — clearing cached token');
        localStorage.removeItem('scythe_session_token');
        this._streamToken = null;
      }
      this._scheduleStreamReconnect();
    });

    this._socket.on('error', (e) => console.error('[Globe] SocketIO error:', e));

    // AR skeet events — when an AR device destroys a UAV
    this._socket.on('uav_hit', (ev) => this._handleUAVHit(ev));
  }

  _scheduleStreamReconnect() {
    const delay = Math.min(1000 * Math.pow(2, this._reconnectAttempts), 15000);
    this._reconnectAttempts++;
    console.log(`[Globe] 🔁 Reconnect in ${delay}ms (attempt ${this._reconnectAttempts})`);
    setTimeout(async () => {
      if (this._reconnectAttempts > 6) {
        console.warn('[Globe] WebSocket reconnects exhausted — switching to SSE');
        this._connectSSE(this._apiBase);
        return;
      }
      // If token was cleared (server kicked us), try to re-auth before reconnecting
      if (!this._streamToken) {
        await this._reAuth();
      }
      this._doConnect(this._socketIOUrl);
    }, delay);
  }

  async _reAuth() {
    const TOKEN_KEY    = 'scythe_session_token';
    const CALLSIGN_KEY = 'scythe_callsign';
    try {
      let callsign = localStorage.getItem(CALLSIGN_KEY);
      if (!callsign) {
        const rnd = Math.random().toString(36).slice(2, 7).toUpperCase();
        callsign = `SCYTHE-${rnd}`;
        localStorage.setItem(CALLSIGN_KEY, callsign);
      }
      const password = `auto-${callsign.toLowerCase()}`;

      // register (idempotent) then login
      await fetch(`${this._apiBase}/api/operator/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ callsign, email: `${callsign}@scythe.local`, password, role: 'operator' })
      }).catch(() => {});

      const r = await fetch(`${this._apiBase}/api/operator/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ callsign, password })
      });
      if (r.ok) {
        const d = await r.json();
        const token = d?.session?.session_token;
        if (token) {
          localStorage.setItem(TOKEN_KEY, token);
          this._streamToken = token;
          console.log('[Globe] 🔑 Re-auth successful for', callsign);
        }
      }
    } catch (err) {
      console.warn('[Globe] Re-auth failed, will connect anonymously:', err.message);
    }
  }

  _startStreamHeartbeat() {
    clearInterval(this._streamHeartbeat);
    this._streamHeartbeat = setInterval(() => {
      if (Date.now() - this._lastEventTs > 20000 && this._socket?.connected) {
        // Ping the server to confirm the stream is still alive
        this._socket.emit('ping');
      }
    }, 10000);
  }

  _connectSSE(apiBase) {
    const url = `${apiBase}/api/graphops/stream`;
    const es  = new EventSource(url);
    es.onmessage = (e) => {
      try { this._onRawEvent(JSON.parse(e.data)); } catch (_) {}
    };
    es.onerror = () => { setTimeout(() => this._connectSSE(apiBase), 3000); };
    console.log('[Globe] SSE stream connected →', url);
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Temporal scrubber — time-travel across edge history
   * ----------------------------------------------------------------------- */

  /** Filter arc entries to those within the current time window.
   *  In live mode returns the live _arcEdges array directly (zero overhead). */
  _getVisibleEdges() {
    if (this._isLive) return this._arcEdges;
    const cursor   = this._timeCursor;
    const winStart = cursor - this._timeWindow;
    const result   = [];
    for (const e of this._edgeStore.values()) {
      const ts = e.serverTs || 0;
      if (ts >= winStart && ts <= cursor) result.push(e);
    }
    return result;
  }

  /** Move the time cursor to a specific epoch-seconds position.
   *  Pass live=true to resume the live stream. */
  setTimeCursor(ts, live = false) {
    this._isLive     = live;
    this._timeCursor = live ? this._lastEdgeTs : ts;
    this._arcDirty   = true;
    this._rebuildArcBuffers();
  }

  /** Auto-play the edge history from oldest to newest at `speed`× real-time.
   *  Each RAF step advances by 0.5 * speed seconds. */
  playTimeline(speed = 1.0) {
    this.stopTimeline();
    this._isLive = false;
    // Seek to oldest available event
    let oldest = Infinity;
    for (const e of this._edgeStore.values()) {
      if (e.serverTs && e.serverTs < oldest) oldest = e.serverTs;
    }
    this._timeCursor = isFinite(oldest) ? oldest : (this._lastEdgeTs - this._timeWindow);
    const step = () => {
      this._timeCursor += 0.5 * speed;
      this._arcDirty = true;
      this._rebuildArcBuffers();
      if (this._timeCursor < this._lastEdgeTs) {
        this._scrubPlayId = requestAnimationFrame(step);
      } else {
        this.stopTimeline();
        this._isLive = true;   // snap back to live when playback reaches now
      }
    };
    this._scrubPlayId = requestAnimationFrame(step);
  }

  /** Cancel an in-progress auto-play. */
  stopTimeline() {
    if (this._scrubPlayId) {
      cancelAnimationFrame(this._scrubPlayId);
      this._scrubPlayId = null;
    }
  }

  /** Count edges within `windowSecs` of the current cursor (burst detection). */
  getBurstDensity(windowSecs = 10) {
    const cursor = this._isLive ? this._lastEdgeTs : this._timeCursor;
    let count = 0;
    for (const e of this._edgeStore.values()) {
      if (e.serverTs && e.serverTs >= cursor - windowSecs && e.serverTs <= cursor) count++;
    }
    return count;
  }

  /** Temporal stats snapshot — consumed by the scrubber UI. */
  getTemporalStats() {
    let oldest = Infinity, newest = 0;
    for (const e of this._edgeStore.values()) {
      if (!e.serverTs) continue;
      if (e.serverTs < oldest) oldest = e.serverTs;
      if (e.serverTs > newest) newest = e.serverTs;
    }
    return {
      storeSize:  this._edgeStore.size,
      oldest:     isFinite(oldest) ? oldest : 0,
      newest,
      cursor:     this._isLive ? this._lastEdgeTs : this._timeCursor,
      isLive:     this._isLive,
      timeWindow: this._timeWindow,
    };
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Event ingestion
   * ----------------------------------------------------------------------- */
  _onEdgesEvent(msg) {
    const edges = Array.isArray(msg) ? msg : (msg.edges || []);
    edges.forEach(e => {
      this._queueUpdate({ type: 'edge_update', ...e });
      // advance temporal cursor so reconnects can request a replay window
      const ts = e.last_seen || e.emitted_at || 0;
      if (ts > this._lastEdgeTs) this._lastEdgeTs = ts;
    });
  }

  _onRawEvent(ev) {
    const type = ev.event_type || ev.type || '';
    if (type.includes('node') || type.includes('entity')) {
      this._queueUpdate({ type: 'node_update', ...ev });
    } else if (type.includes('edge')) {
      this._queueUpdate({ type: 'edge_update', ...ev });
    } else if (type.includes('hyperedge') || type.includes('cluster')) {
      this._queueUpdate({ type: 'hyperedge_update', ...ev });
    } else if (type.includes('convergence')) {
      this._onConvergence(ev);
    }
  }

  _queueUpdate(event) {
    this._updateQueue.push(event);
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Batch flush — applies queued graph updates to GPU buffers
   * ----------------------------------------------------------------------- */
  _flushBatch() {
    if (this._updateQueue.length === 0) return;

    const batch = this._updateQueue.splice(0, 2000);  // cap per-flush
    let topoChanged = false;

    for (const ev of batch) {
      switch (ev.type) {
        case 'node_update':     topoChanged |= this._applyNodeUpdate(ev);
                                this._reconEntityPipeline(ev);            break;
        case 'node_remove':     topoChanged |= this._applyNodeRemove(ev); break;
        case 'edge_update':     this._applyEdgeUpdate(ev);                 break;
        case 'edge_remove':     this._applyEdgeRemove(ev);                 break;
        case 'hyperedge_update':this._applyHyperedgeUpdate(ev);            break;
      }
    }

    if (this._arcDirty)    this._rebuildArcBuffers();
    if (this._heDirty)     this._rebuildHEBuffers();
    if (topoChanged)       this._rebuildAdjTex();
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Node update → mutate InstancedMesh attribute arrays in place
   * ----------------------------------------------------------------------- */
  _applyNodeUpdate(ev) {
    const id   = ev.entity_id || ev.id || ev.src;
    if (!id) return false;

    const lat  = parseFloat(ev.lat  ?? ev.latitude  ?? (ev.payload && ev.payload.lat)  ?? 0);
    const lon  = parseFloat(ev.lon  ?? ev.longitude ?? (ev.payload && ev.payload.lon)  ?? 0);
    const conf = parseFloat(ev.confidence ?? ev.conf ?? 0.3);

    if (conf < CONF_CULL_THRESHOLD) return false;

    let idx = this._nodeIdxMap.get(id);
    const isNew = (idx === undefined);

    if (isNew) {
      if (this._nodeCount >= MAX_NODES) return false;
      idx = this._nodeCount++;
      this._nodeIdxMap.set(id, idx);
      this._nodeMesh.geometry.instanceCount = this._nodeCount;
    }

    // Resolve ECEF position (GeoIP cache)
    let pos = this._geoCache.get(id);
    if (!pos || isNew) {
      pos = Cesium.Cartesian3.fromDegrees(lon, lat);
      this._geoCache.set(id, pos);
    }

    const geo    = this._nodeMesh.geometry;
    const posArr = geo.attributes.instancePosition.array;
    posArr[idx*3]   = pos.x;
    posArr[idx*3+1] = pos.y;
    posArr[idx*3+2] = pos.z;

    geo.attributes.instanceId.array[idx]         = this._encodeId(id);
    geo.attributes.instanceConf.array[idx]        = conf;
    geo.attributes.instanceLastUpdate.array[idx]  = Date.now() / 1000;
    geo.attributes.instanceCluster.array[idx]     = ev.cluster_size ?? ev.clusterSize ?? 1;

    // Color from entity kind
    const col = this._kindColor(ev.entity_kind || ev.kind || 'host');
    geo.attributes.instanceColor.array[idx*3]     = col[0];
    geo.attributes.instanceColor.array[idx*3+1]   = col[1];
    geo.attributes.instanceColor.array[idx*3+2]   = col[2];

    // Emitter classification → override color if a semantic class is detected.
    // Feeds back into rendering: classMultiplier affects strobe type & frequency.
    const emitterClass = this.classifyEmitter({
      degree:   ev.cluster_size      ?? 1,
      incoming: ev.incoming_count    ?? 0,
      anomaly:  parseFloat(ev.anomaly_score ?? ev.anomaly ?? 0),
      variance: parseFloat(ev.variance ?? 0),
      velocity: parseFloat(ev.velocity ?? 0),
    });
    const ecol = this._emitterClassColor(emitterClass);
    if (ecol) {
      geo.attributes.instanceColor.array[idx*3]   = ecol[0];
      geo.attributes.instanceColor.array[idx*3+1] = ecol[1];
      geo.attributes.instanceColor.array[idx*3+2] = ecol[2];
    }

    // Violation packing: dns|c2|rst|port → nibbles
    const v = ev.violations || ev.payload?.violations || {};
    geo.attributes.instanceViolations.array[idx] =
      ((v.dns_tunnel     ? 1 : 0)) |
      ((v.c2_beacon      ? 1 : 0) << 1) |
      ((v.tcp_rst_flood  ? 1 : 0) << 2) |
      ((v.risk_port      ? 1 : 0) << 3);

    // Trigger emergence animation (lifecycle starts at 0, ramps to 1)
    if (isNew) geo.attributes.instanceLifecycle.array[idx] = 0;

    // Mark all position/attribute buffers dirty
    for (const k of Object.keys(geo.attributes)) geo.attributes[k].needsUpdate = true;

    // Store in graph state
    this._graph.nodes.set(id, { lat, lon, conf, idx, shadow: ev.shadow ?? false });
    return isNew;
  }

  _applyNodeRemove(ev) {
    const id = ev.id || ev.entity_id;
    if (!id) return false;
    const idx = this._nodeIdxMap.get(id);
    if (idx === undefined) return false;
    // Fade out by zeroing lifecycle — actual removal deferred until out of view
    this._nodeMesh.geometry.attributes.instanceLifecycle.array[idx] = 0;
    this._nodeMesh.geometry.attributes.instanceLifecycle.needsUpdate = true;
    this._graph.nodes.delete(id);
    this._nodeIdxMap.delete(id);
    return true;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Edge update — batch into _arcEdges, mark dirty
   * ----------------------------------------------------------------------- */
  _applyEdgeUpdate(ev) {
    const eid   = ev.edge_id || ev.id || `${ev.src}::${ev.dst}`;
    const conf  = parseFloat(ev.confidence ?? ev.conf ?? 0.3);
    if (conf < CONF_CULL_THRESHOLD && !ev.shadow) return;

    // Resolve ECEF positions for src/dst
    const srcPos = this._resolvePos(ev.src, ev.src_lat, ev.src_lon);
    const dstPos = this._resolvePos(ev.dst, ev.dst_lat, ev.dst_lon);
    if (!srcPos || !dstPos) return;

    const rawAnomaly = parseFloat(ev.anomaly_score ?? ev.anomaly ?? 0);

    const entry = {
      id:      eid,
      srcPos, dstPos,
      srcLat:  parseFloat(ev.src_lat ?? 0),
      srcLon:  parseFloat(ev.src_lon ?? 0),
      dstLat:  parseFloat(ev.dst_lat ?? 0),
      dstLon:  parseFloat(ev.dst_lon ?? 0),
      conf,
      entropy:  parseFloat(ev.entropy ?? 0.5),
      rfCorr:   parseFloat(ev.rf_corr ?? 0),
      shadow:   ev.shadow ? 1 : 0,
      anomaly:  rawAnomaly,
      kind:     ev.kind || 'FLOW',
      lastSeen: performance.now() * 0.001   // seconds, matches uTime
    };

    const existing = this._arcEdges.findIndex(e => e.id === eid);
    if (existing >= 0) {
      // EMA smoothing: anomaly lingers just enough to reveal patterns, no flicker noise
      const prev = this._arcEdges[existing].anomaly_smoothed ?? rawAnomaly;
      entry.anomaly_smoothed = prev * 0.85 + rawAnomaly * 0.15;
      this._arcEdges[existing] = entry;
    } else {
      entry.anomaly_smoothed = rawAnomaly;
      this._arcEdges.push(entry);
    }
    this._graph.edges.set(eid, ev);

    // Keep temporal archive — extract server-side timestamp for scrubber replay
    const serverTs = parseFloat(ev.last_seen ?? ev.emitted_at ?? 0) || (Date.now() / 1000);
    entry.serverTs = serverTs;
    this._edgeStore.set(eid, entry);
    // Prune store when it grows beyond 50k (remove oldest 5k entries)
    if (this._edgeStore.size > 50000) {
      const sorted = [...this._edgeStore.entries()].sort((a, b) => a[1].serverTs - b[1].serverTs);
      for (let i = 0; i < 5000; i++) this._edgeStore.delete(sorted[i][0]);
    }

    this._arcDirty = true;
  }

  _applyEdgeRemove(ev) {
    const eid = ev.edge_id || ev.id;
    this._arcEdges = this._arcEdges.filter(e => e.id !== eid);
    this._graph.edges.delete(eid);
    this._arcDirty = true;
  }

  _clusterFlareProfile(members, conf) {
    if (!members || members.length < 2) return null;

    let sumX = 0, sumY = 0, sumZ = 0, sumRadius = 0;
    for (const p of members) {
      sumX += p.x;
      sumY += p.y;
      sumZ += p.z;
      sumRadius += Math.hypot(p.x, p.y, p.z);
    }
    const memberCount = members.length;
    const avgRadius = memberCount > 0 ? (sumRadius / memberCount) : EARTH_RADIUS_M;
    const len = Math.hypot(sumX, sumY, sumZ);
    if (len < 1e-6 || avgRadius <= 0) return null;

    const centroid = new Cesium.Cartesian3(
      (sumX / len) * avgRadius,
      (sumY / len) * avgRadius,
      (sumZ / len) * avgRadius
    );

    let sumSpread = 0;
    let maxSpread = 0;
    for (const p of members) {
      const dx = p.x - centroid.x;
      const dy = p.y - centroid.y;
      const dz = p.z - centroid.z;
      const dist = Math.hypot(dx, dy, dz);
      sumSpread += dist;
      if (dist > maxSpread) maxSpread = dist;
    }
    const meanSpread = memberCount > 0 ? (sumSpread / memberCount) : 0;
    const flareWidth = Math.min(
      180_000,
      Math.max(12_000, meanSpread * 0.55 + maxSpread * 0.18 + memberCount * 1_800 + conf * 14_000)
    );
    const flareHeight = Math.min(
      260_000,
      Math.max(45_000, Math.log2(memberCount + 1) * 24_000 + meanSpread * 0.45 + conf * 50_000)
    );

    return {
      centroid,
      memberCount,
      width: flareWidth,
      height: flareHeight,
    };
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Hyperedge update
   * ----------------------------------------------------------------------- */
  _applyHyperedgeUpdate(ev) {
    const hid = ev.cluster_id || ev.id;
    const members = (ev.members || []).map(m => this._geoCache.get(m)).filter(Boolean);
    if (members.length < 2) return;
    const conf = parseFloat(ev.confidence ?? 0.5);
    const profile = this._clusterFlareProfile(members, conf);
    if (!profile) return;

    const entry = {
      id: hid,
      centroid: profile.centroid,
      conf,
      color: this._heColor(ev.label || ''),
      height: profile.height,
      width: profile.width,
      memberCount: profile.memberCount,
    };

    const existing = this._heList.findIndex(h => h.id === hid);
    if (existing >= 0) this._heList[existing] = entry;
    else               this._heList.push(entry);

    this._graph.hyperedges.set(hid, {
      ...ev,
      member_count: profile.memberCount,
      flare_height_m: CLUSTER_FLARE_BASE_ALT + profile.height,
      flare_width_m: profile.width,
    });
    this._heDirty = true;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * GPU buffer rebuilds
   * ----------------------------------------------------------------------- */
  _rebuildArcBuffers() {
    this._arcDirty = false;
    const source  = this._getVisibleEdges();
    const visible = this._shadowReveal
      ? source
      : source.filter(e => !e.shadow || e.shadow === 0);

    const count = Math.min(visible.length, MAX_ARC_INSTANCES);

    for (let ei = 0; ei < count; ei++) {
      const e = visible[ei];

      this._arcInstStart[ei*3]     = e.srcPos.x;
      this._arcInstStart[ei*3 + 1] = e.srcPos.y;
      this._arcInstStart[ei*3 + 2] = e.srcPos.z;

      this._arcInstEnd[ei*3]       = e.dstPos.x;
      this._arcInstEnd[ei*3 + 1]   = e.dstPos.y;
      this._arcInstEnd[ei*3 + 2]   = e.dstPos.z;

      // Temporal decay: fade edges by age when in scrub mode
      let conf = e.conf;
      if (!this._isLive && e.serverTs) {
        const age = this._timeCursor - e.serverTs;
        conf = conf * Math.exp(-Math.max(0, age) / 30);
      }
      this._arcInstConf[ei]     = conf;
      this._arcInstEntropy[ei]  = e.entropy;
      this._arcInstRfCorr[ei]   = e.rfCorr;
      this._arcInstShadow[ei]   = e.shadow;
      this._arcInstAnomaly[ei]  = e.anomaly_smoothed ?? e.anomaly ?? 0;
      // Golden-ratio stagger: each edge gets a unique, evenly-distributed pulse offset
      this._arcInstTimeOff[ei]  = (ei * 0.6180339887) % 1.0;
      this._arcInstLastSeen[ei] = e.lastSeen ?? 0;
      this._arcInstEdgeId[ei]   = this._encodeEdgeId(e.id);
    }

    // Mark all instance attributes dirty for GPU upload
    for (const k of ['iStart','iEnd','iConf','iEntropy','iRfCorr','iShadow','iAnomaly','iTimeOff','iLastSeen','iEdgeId']) {
      this._arcGeo.attributes[k].needsUpdate = true;
    }
    this._arcGeo.instanceCount = count;
  }

  _rebuildHEBuffers() {
    this._heDirty = false;
    const geo  = this._heMesh.geometry;
    const POS  = geo.attributes.position.array;
    const HCON = geo.attributes.aHEConf.array;
    const HHGT = geo.attributes.aHEHeight.array;
    const HWID = geo.attributes.aHEWidth.array;

    let vi = 0;
    const count = Math.min(this._heList.length, MAX_HYPEREDGES);

    for (let hi = 0; hi < count; hi++) {
      const h = this._heList[hi];
      for (let pi = 0; pi < MAX_HE_PARTICLES; pi++) {
        const i = hi * MAX_HE_PARTICLES + pi;
        POS[i*3]   = h.centroid.x;
        POS[i*3+1] = h.centroid.y;
        POS[i*3+2] = h.centroid.z;
        HCON[i]    = h.conf;
        HHGT[i]    = h.height ?? 60_000;
        HWID[i]    = h.width  ?? 18_000;
        vi++;
      }
    }

    geo.attributes.position.needsUpdate = true;
    geo.attributes.aHEConf.needsUpdate  = true;
    geo.attributes.aHEHeight.needsUpdate = true;
    geo.attributes.aHEWidth.needsUpdate  = true;
    geo.setDrawRange(0, vi);
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Adjacency texture — BFS from each node, store hop distances
   * Runs after topology changes; limited to ADJ_TEX_SIZE×ADJ_TEX_SIZE nodes
   * ----------------------------------------------------------------------- */
  _rebuildAdjTex() {
    const data = this._adjData;
    data.fill(255);   // 255 = unreachable (normalised to 1.0 in shader × 32)

    // Build adjacency list from edge state
    const adj = new Map();
    for (const [, e] of this._graph.edges) {
      const si = this._nodeIdxMap.get(e.src);
      const di = this._nodeIdxMap.get(e.dst);
      if (si === undefined || di === undefined) continue;
      if (!adj.has(si)) adj.set(si, []);
      if (!adj.has(di)) adj.set(di, []);
      adj.get(si).push(di);
      adj.get(di).push(si);
    }

    // BFS from selected node only (avoids full all-pairs cost)
    const selectedIdx = this._uSelectedId.value >= 0
      ? this._uSelectedId.value
      : -1;

    if (selectedIdx >= 0 && selectedIdx < ADJ_TEX_SIZE * ADJ_TEX_SIZE) {
      const dist = new Uint8Array(ADJ_TEX_SIZE * ADJ_TEX_SIZE).fill(255);
      const queue = [selectedIdx];
      dist[selectedIdx] = 0;

      while (queue.length) {
        const cur = queue.shift();
        const neighbors = adj.get(cur) || [];
        for (const nb of neighbors) {
          if (nb < dist.length && dist[nb] === 255) {
            dist[nb] = Math.min(dist[cur] + 1, 31);
            queue.push(nb);
          }
        }
      }

      for (let i = 0; i < dist.length; i++) {
        data[i] = dist[i] / 32;   // normalise to [0, 1]
      }
    }

    this._uAdjTex.value.needsUpdate = true;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Node lifecycle animation — ramps lifecycle 0→1 for new nodes
   * ----------------------------------------------------------------------- */
  _stepLifecycles() {
    const lc = this._nodeMesh.geometry.attributes.instanceLifecycle;
    let any  = false;
    for (let i = 0; i < this._nodeCount; i++) {
      if (lc.array[i] < 1.0) {
        lc.array[i] = Math.min(1.0, lc.array[i] + 0.04);
        any = true;
      }
    }
    if (any) lc.needsUpdate = true;
    this._decayReconEntities();
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Node selection — O(1), zero re-render
   * ----------------------------------------------------------------------- */
  selectNode(entityId) {
    this._selectedEntityId = entityId;
    const idx = entityId ? this._nodeIdxMap.get(entityId) : -1;
    this._uSelectedId.value = (idx !== undefined) ? idx : -1;
    if (idx !== undefined) this._rebuildAdjTex();
    this._emitSelectionEvent(entityId, idx);
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Canvas click → GPU picking (raycasting fallback)
   * ----------------------------------------------------------------------- */
  _onCanvasClick(e) {
    const rect = e.target.getBoundingClientRect();
    const x    = ((e.clientX - rect.left) / rect.width)  * 2 - 1;
    const y   = -((e.clientY - rect.top)  / rect.height) * 2 + 1;

    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera({ x, y }, this._camera);
    const hits = raycaster.intersectObject(this._nodeMesh);

    if (hits.length > 0) {
      const instanceIdx = hits[0].instanceId;
      // Reverse-lookup entityId from instance index
      const entityId = [...this._nodeIdxMap.entries()]
        .find(([, idx]) => idx === instanceIdx)?.[0];
      if (entityId) this.selectNode(entityId);
    }
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Convergence bloom — Phase 9 integration
   * Fired when GraphOps belief_state.converged = true
   * ----------------------------------------------------------------------- */
  _onConvergence(ev) {
    const members = ev.members || ev.nodes || [];
    this._bloomNodes = members
      .map(id => this._nodeIdxMap.get(id))
      .filter(idx => idx !== undefined);
    this._bloomActive = true;
    this._bloomStart  = this._uTime.value;

    // Dispatch UI event with classification
    window.dispatchEvent(new CustomEvent('globe:convergence', {
      detail: { classification: ev.classification, confidence: ev.confidence, members }
    }));
    console.log('[Globe] Convergence bloom:', ev.classification, '@', ev.confidence?.toFixed(3));
  }

  _onSuggestedPrompts(ev) {
    window.dispatchEvent(new CustomEvent('globe:suggested_prompts', { detail: ev }));
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Shadow reveal toggle
   * ----------------------------------------------------------------------- */
  setShadowReveal(enabled) {
    this._shadowReveal = enabled;
    this._arcDirty     = true;
    this._rebuildArcBuffers();
  }

  setClusterFlareVisible(enabled) {
    this._clusterFlareVisible = !!enabled;
    if (this._heMesh) this._heMesh.visible = this._clusterFlareVisible;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Confidence threshold cull
   * ----------------------------------------------------------------------- */
  setConfidenceCull(threshold) {
    this._arcEdges = this._arcEdges.filter(e => e.conf >= threshold);
    this._arcDirty = true;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Manual node upsert (from GraphOps DSL results)
   * ----------------------------------------------------------------------- */
  upsertNode(id, lat, lon, conf, options = {}) {
    this._queueUpdate({
      type: 'node_update',
      entity_id: id,
      lat, lon,
      confidence: conf,
      entity_kind: options.kind || 'host',
      violations: options.violations || {},
      cluster_size: options.clusterSize || 1,
      shadow: options.shadow || false
    });
  }

  upsertEdge(id, srcId, dstId, conf, options = {}) {
    const srcPos = this._geoCache.get(srcId);
    const dstPos = this._geoCache.get(dstId);
    if (!srcPos || !dstPos) return;
    this._queueUpdate({
      type: 'edge_update',
      edge_id: id,
      src: srcId,
      dst: dstId,
      src_lat: 0, src_lon: 0,   // not needed — resolved via cache
      dst_lat: 0, dst_lon: 0,
      confidence: conf,
      entropy: options.entropy ?? 0.5,
      rf_corr: options.rfCorr  ?? 0,
      shadow: options.shadow   ?? false,
      kind: options.kind       || 'FLOW'
    });
  }

  upsertHyperedge(id, memberIds, conf, label = '') {
    this._queueUpdate({
      type: 'hyperedge_update',
      cluster_id: id,
      members: memberIds,
      confidence: conf,
      label
    });
  }

  /* ───────────────────────────────────────────────────────────────────────
   * GraphOps integration — apply investigation report to globe
   * ----------------------------------------------------------------------- */
  applyGraphOpsReport(report, options = {}) {
    if (!report) return;
    const groundedEntities = [];
    let ungroundedEntityCount = 0;

    // Nodes from report entities
    (report.entities || []).forEach(e => {
      const loc = e.location || {};
      const lat = Number(loc.lat ?? e.lat);
      const lon = Number(loc.lon ?? e.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        ungroundedEntityCount += 1;
        return;
      }
      groundedEntities.push({ ...e, location: { ...loc, lat, lon } });
      this.upsertNode(e.id, lat, lon, e.confidence ?? 0.3, {
        kind: e.kind, violations: e.violations, clusterSize: e.cluster_size
      });
    });

    // Edges from inferred flows
    (report.edges || []).forEach(e => {
      this.upsertEdge(e.id || `${e.src}::${e.dst}`, e.src, e.dst, e.confidence ?? 0.3, {
        entropy: e.entropy, rfCorr: e.rf_corr, kind: e.kind, shadow: e.shadow
      });
    });

    // Hyperedges from clusters
    (report.hyperedges || []).forEach(h => {
      this.upsertHyperedge(h.id, h.members, h.confidence, h.label);
    });

    // Convergence bloom if converged
    if (report.belief_state?.converged) {
      this._onConvergence({
        members: [...this._graph.nodes.keys()],
        classification: report.classification,
        confidence: report.belief_state.confidence_history?.slice(-1)[0]
      });
    }

    const normalizedReport = {
      ...report,
      entities: groundedEntities,
      grounded_entity_count: groundedEntities.length,
      ungrounded_entity_count: ungroundedEntityCount,
    };

    window.dispatchEvent(new CustomEvent('globe:graphops_report', {
      detail: {
        report: normalizedReport,
        source: options.source || 'graphops',
      }
    }));

    return normalizedReport;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Helpers
   * ----------------------------------------------------------------------- */

  /** ECEF great-circle slerp between two Cartesian3 points (n points) */
  _slerp(a, b, n) {
    const ra = Cesium.Cartesian3.magnitude(a);
    const rb = Cesium.Cartesian3.magnitude(b);
    const r  = (ra + rb) * 0.5 || EARTH_RADIUS_M;
    const na = Cesium.Cartesian3.normalize(a, new Cesium.Cartesian3());
    const nb = Cesium.Cartesian3.normalize(b, new Cesium.Cartesian3());
    const dot = Cesium.Cartesian3.dot(na, nb);
    const theta = Math.acos(Math.min(1, Math.max(-1, dot)));
    const pts   = [];
    for (let i = 0; i < n; i++) {
      const t = i / (n - 1);
      if (theta < 0.0001) {
        pts.push(Cesium.Cartesian3.lerp(a, b, t, new Cesium.Cartesian3()));
      } else {
        const sT = Math.sin(theta);
        const w0 = Math.sin((1 - t) * theta) / sT;
        const w1 = Math.sin(t * theta) / sT;
        // Arc height scales with angular separation so short edges stay low,
        // long intercontinental arcs rise dramatically. Max ~400km at antipodal.
        const lift = r * theta * 0.35 * Math.sin(t * Math.PI);
        pts.push(new Cesium.Cartesian3(
          (na.x * w0 + nb.x * w1) * r + na.x * w0 * lift + nb.x * w1 * lift,
          (na.y * w0 + nb.y * w1) * r + na.y * w0 * lift + nb.y * w1 * lift,
          (na.z * w0 + nb.z * w1) * r + na.z * w0 * lift + nb.z * w1 * lift
        ));
      }
    }
    return pts;
  }

  _resolvePos(entityId, fallbackLat, fallbackLon) {
    if (this._geoCache.has(entityId)) return this._geoCache.get(entityId);
    const lat = parseFloat(fallbackLat ?? 0);
    const lon = parseFloat(fallbackLon ?? 0);
    if (lat === 0 && lon === 0) return null;
    const pos = Cesium.Cartesian3.fromDegrees(lon, lat);
    this._geoCache.set(entityId, pos);
    return pos;
  }

  /** Stable float encoding of entityId string (djb2-lite) */
  _encodeId(str) {
    let h = 0;
    for (let i = 0; i < Math.min(str.length, 20); i++) {
      h = (h * 31 + str.charCodeAt(i)) & 0x7FFFFFFF;
    }
    return h & 0xFFFF;  // keep within shader float precision
  }

  _encodeEdgeId(str) {
    return this._encodeId(str);
  }

  _kindColor(kind) {
    const K = (kind || '').toLowerCase();
    if (K.includes('host'))      return [0.2, 0.6, 1.0];
    if (K.includes('service'))   return [0.3, 0.9, 0.5];
    if (K.includes('recon'))     return [1.0, 0.6, 0.1];
    if (K.includes('malicious')) return [1.0, 0.1, 0.1];
    if (K.includes('tls'))       return [0.7, 0.3, 1.0];
    if (K.includes('dns'))       return [0.2, 0.9, 0.9];
    if (K.includes('sensor'))    return [0.9, 0.9, 0.2];
    return [0.4, 0.6, 0.8];
  }

  /**
   * Classify an emitter node into a semantic role based on graph metrics.
   * Returns a string label used for color override and strobe type selection.
   *
   * @param {object} node  — {degree, incoming, anomaly, variance, velocity}
   *   These fields come from entity metadata / violations / cluster stats.
   * @returns {'datacenter'|'C2'|'relay'|'mobile'|'unknown'}
   */
  classifyEmitter(node) {
    const degree   = node.degree        ?? node.cluster_size     ?? 1;
    const incoming = node.incoming      ?? node.incoming_count   ?? 0;
    const anomaly  = node.anomaly       ?? node.anomaly_score    ?? 0;
    const variance = node.variance      ?? 0;
    const velocity = node.velocity      ?? node.speed            ?? 0;

    // High-degree low-anomaly hub → datacenter / CDN
    if (degree > 50 && anomaly < 0.2) return 'datacenter';
    // High incoming traffic with anomaly → C2 listener
    if (incoming > 20 && anomaly > 0.6) return 'C2';
    // High anomaly + high variance → relay / proxy hop
    if (anomaly > 0.7 && variance > 0.4) return 'relay';
    // Moving node → mobile emitter / drone
    if (velocity > 0.02) return 'mobile';
    return 'unknown';
  }

  /** RGB color per emitter classification (null = use kind color fallback). */
  _emitterClassColor(cls) {
    switch (cls) {
      case 'datacenter': return [0.15, 0.50, 1.00];  // steel blue
      case 'C2':         return [1.00, 0.10, 0.20];  // hot red
      case 'relay':      return [0.78, 0.18, 1.00];  // purple
      case 'mobile':     return [0.18, 1.00, 0.42];  // bright green
      default:           return null;
    }
  }

  _heColor(label) {
    const L = (label || '').toLowerCase();
    if (L.includes('beacon'))    return new THREE.Color(1.0, 0.4, 0.0);
    if (L.includes('proxy'))     return new THREE.Color(0.8, 0.2, 1.0);
    if (L.includes('scan'))      return new THREE.Color(0.9, 0.9, 0.0);
    return new THREE.Color(0.2, 0.8, 1.0);
  }

  _emitSelectionEvent(entityId, idx) {
    window.dispatchEvent(new CustomEvent('globe:node_selected', {
      detail: { entityId, instanceIdx: idx, node: this._graph.nodes.get(entityId) }
    }));
  }

  _onResize() {
    const c = this._viewer.scene.canvas;
    const w = c.clientWidth  || c.parentElement?.clientWidth  || window.innerWidth;
    const h = c.clientHeight || c.parentElement?.clientHeight || window.innerHeight;
    // false = don't let Three.js overwrite CSS width/height (strobe fix)
    this._renderer.setSize(w, h, false);
    this._camera.aspect = w / h;
    this._camera.updateProjectionMatrix();
    if (this._uViewHeight) this._uViewHeight.value = h;

    // Resize both heatmap RTs to match new half-res canvas dimensions
    if (this._heatmapRT) {
      const rtW = Math.max(1, Math.floor(w / 2));
      const rtH = Math.max(1, Math.floor(h / 2));
      this._heatmapRT.setSize(rtW, rtH);
      this._heatmapRT_prev?.setSize(rtW, rtH);
    }
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Snapshot export (for GraphOps report overlay)
   * ----------------------------------------------------------------------- */
  getGraphSnapshot() {
    return {
      nodeCount:     this._nodeCount,
      edgeCount:     this._arcEdges.length,
      hyperedgeCount:this._heList.length,
      nodes:         [...this._graph.nodes.entries()].map(([id, n]) => ({ id, ...n })),
      edges:         [...this._graph.edges.values()],
      hyperedges:    [...this._graph.hyperedges.values()]
    };
  }

  /** Return the currently selected node and its metadata. */
  getSelectionContext() {
    if (!this._selectedEntityId) return null;
    const node = this._graph.nodes.get(this._selectedEntityId);
    if (!node) return null;
    return { id: this._selectedEntityId, ...node };
  }

  /** Return a list of entities currently visible in the operator's view. */
  getViewContext(limit = 12) {
    if (!this._viewer || !this._camera) return [];
    
    const visible = [];
    const tmpV3 = new THREE.Vector3();
    const projView = new THREE.Matrix4().multiplyMatrices(
      this._camera.projectionMatrix,
      this._camera.matrixWorldInverse
    );

    // Filter nodes that project into the screen-space frustum
    for (const [id, node] of this._graph.nodes) {
      const pos = this._geoCache.get(id);
      if (!pos) continue;

      tmpV3.set(pos.x, pos.y, pos.z);
      tmpV3.applyMatrix4(projView);

      // NDC check: [-1, 1] range. z <= 1 ensures it's not behind the camera.
      if (Math.abs(tmpV3.x) <= 1.05 && Math.abs(tmpV3.y) <= 1.05 && tmpV3.z <= 1.0) {
        // Priority heuristic: center of screen > edges, high confidence > low.
        const distFromCenterSq = tmpV3.x * tmpV3.x + tmpV3.y * tmpV3.y;
        visible.push({ id, distSq: distFromCenterSq, conf: node.conf || 0 });
      }
    }

    // Sort by centrality then confidence
    visible.sort((a, b) => (a.distSq - b.distSq) || (b.conf - a.conf));
    
    return visible.slice(0, limit).map(v => {
      const node = this._graph.nodes.get(v.id);
      return { id: v.id, ...node };
    });
  }

  /* Programmatic fly-to a node */
  flyToNode(entityId, durationSec = 2.0) {
    const pos = this._geoCache.get(entityId);
    if (!pos) return;
    const cart = new Cesium.Cartographic();
    Cesium.Ellipsoid.WGS84.cartesianToCartographic(pos, cart);
    this._viewer.camera.flyTo({
      destination: Cesium.Cartesian3.fromRadians(
        cart.longitude, cart.latitude, 500_000
      ),
      duration: durationSec
    });
  }

  flyToCoords(lat, lon, altMeters = 2_000_000, durationSec = 2.0) {
    if (!this._viewer) return;
    this._viewer.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(lon, lat, altMeters),
      duration: durationSec
    });
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Feature 1: GPU Frame Budget Governor — public quality getter
   * ----------------------------------------------------------------------- */
  get renderQuality() { return this._governor.quality; }

  /* ───────────────────────────────────────────────────────────────────────
   * Feature 2: Deck.gl Bridge
   * ----------------------------------------------------------------------- */
  attachDeck(deckInstance) {
    if (!this._deckBridge) {
      this._deckBridge = new DeckBridge(this._viewer, deckInstance);
    } else {
      this._deckBridge.setDeck(deckInstance);
    }
    console.log('[Globe] Deck.gl bridge attached');
  }

  get deckBridge() { return this._deckBridge; }

  /* ───────────────────────────────────────────────────────────────────────
   * Feature 3: Volumetric RF Shell Layer
   * ----------------------------------------------------------------------- */
  _buildRFVolumetricLayer() {
    const N   = MAX_RF_EMITTERS;
    const geo = new THREE.InstancedBufferGeometry();

    // Billboard quad: 4 vertices (-1,-1) to (1,1) as 2 triangles
    const quadUV  = new Float32Array([-1,-1, 1,-1, 1,1, -1,1]);
    const quadIdx = new Uint16Array([0,1,2, 0,2,3]);
    geo.setAttribute('aUV',  new THREE.BufferAttribute(quadUV, 2));
    geo.setIndex(new THREE.BufferAttribute(quadIdx, 1));
    geo.instanceCount = 0;

    this._rfInstCenter    = new Float32Array(N * 3);
    this._rfInstRadius    = new Float32Array(N);
    this._rfInstIntensity = new Float32Array(N);
    this._rfInstAnomaly   = new Float32Array(N);
    this._rfInstPhase     = new Float32Array(N);

    for (let i = 0; i < N; i++) {
      this._rfInstRadius[i] = RF_DEFAULT_RADIUS;
      this._rfInstPhase[i]  = (i * 0.6180339887) % (Math.PI * 2);
    }

    geo.setAttribute('iCenter',    new THREE.InstancedBufferAttribute(this._rfInstCenter,    3));
    geo.setAttribute('iRadius',    new THREE.InstancedBufferAttribute(this._rfInstRadius,    1));
    geo.setAttribute('iIntensity', new THREE.InstancedBufferAttribute(this._rfInstIntensity, 1));
    geo.setAttribute('iAnomaly',   new THREE.InstancedBufferAttribute(this._rfInstAnomaly,   1));
    geo.setAttribute('iPhase',     new THREE.InstancedBufferAttribute(this._rfInstPhase,     1));

    geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(0,0,0), 8e6);

    const mat = new THREE.ShaderMaterial({
      uniforms: {
        uTime:    this._uTime,
        uQuality: this._uQuality,
      },
      vertexShader:   RF_VOL_VERT,
      fragmentShader: RF_VOL_FRAG,
      transparent:    true,
      depthWrite:     false,
      blending:       THREE.AdditiveBlending,
      side:           THREE.DoubleSide,
    });

    this._rfVolMesh = new THREE.Mesh(geo, mat);
    this._rfVolMesh.frustumCulled = false;
    this._rfVolMesh.renderOrder = 0;   // shells behind everything else
    this._rfVolMesh.visible = false;
    this._scene.add(this._rfVolMesh);
  }

  /**
   * Update the volumetric RF shell layer with emitter data.
   * @param {Array<{pos: Cesium.Cartesian3|{x,y,z}, intensity: number, anomaly: number, radius?: number}>} emitters
   */
  /**
   * Inject an RF bearing observation into the heatmap RF channel.
   * Each call appends one directional wedge splat for the current frame's
   * render pass, giving a tropospheric (20 km elevation) bearing cone.
   *
   * @param {number} lat          Degrees latitude
   * @param {number} lon          Degrees longitude
   * @param {number} bearingDeg   Bearing from North, clockwise (0–360)
   * @param {number} beamWidthDeg Full beam angle in degrees (e.g. 30 = ±15°)
   * @param {number} strength     Signal strength 0–1
   */
  injectRfBearing(lat, lon, bearingDeg, beamWidthDeg, strength = 0.5) {
    if (!this._rfConePos) return;
    const MAX_RF_CONES = 2048;
    const i = this._rfConeCount % MAX_RF_CONES;

    // Convert lat/lon to ECEF (WGS-84 mean sphere)
    const φ = lat  * (Math.PI / 180);
    const λ = lon  * (Math.PI / 180);
    const R = 6371000;
    const cosφ = Math.cos(φ);
    this._rfConePos[i * 3 + 0] = R * cosφ * Math.cos(λ);
    this._rfConePos[i * 3 + 1] = R * cosφ * Math.sin(λ);
    this._rfConePos[i * 3 + 2] = R * Math.sin(φ);

    this._rfConeBearing[i]  = bearingDeg   * (Math.PI / 180);
    this._rfConeBeam[i]     = (beamWidthDeg * 0.5) * (Math.PI / 180); // half-angle
    this._rfConeStrength[i] = Math.min(1, Math.max(0, strength));

    this._rfConeCount = Math.min(this._rfConeCount + 1, MAX_RF_CONES);

    // Mirror into voxel world model for temporal persistence across frames.
    // The RT cone splat lives only while active; the voxel field remembers.
    if (this._voxelField) {
      this._voxelField.injectRfCone(lat, lon, bearingDeg, beamWidthDeg, strength * 0.6);
    }
  }

  /** Clear all pending RF bearing splats (call once per frame after render). */
  clearRfBearings() {
    this._rfConeCount = 0;
  }

  /**
   * Push up to 16 directional RF emitters into the volumetric raymarch uniforms.
   * These produce 3D atmospheric glow cones visible above the globe surface.
   *
   * Each emitter:
   *   lat, lon        — decimal degrees
   *   bearingDeg      — from North, clockwise 0–360
   *   beamWidthDeg    — full cone angle (e.g. 30 = ±15°)
   *   strength        — 0–1
   *   freqNorm        — normalized frequency 0–1 (0=low-freq warm, 1=high-freq cyan)
   */
  updateRfVolumetric(emitters) {
    if (!this._heatmapCompMat) return;
    const u   = this._heatmapCompMat.uniforms;
    const N   = Math.min(emitters.length, 16);
    const R   = 6371000;

    for (let i = 0; i < N; i++) {
      const e  = emitters[i];
      const φ  = e.lat * (Math.PI / 180);
      const λ  = e.lon * (Math.PI / 180);
      const cosφ = Math.cos(φ);

      // ECEF origin (surface + 20 km troposphere elevation)
      const nx = cosφ * Math.cos(λ);
      const ny = cosφ * Math.sin(λ);
      const nz = Math.sin(φ);
      u.uRfVolOrigin.value[i].set(nx * (R + 20000), ny * (R + 20000), nz * (R + 20000));

      // ECEF bearing direction from ENU
      const zAxis = [0, 0, 1];
      let ex = zAxis[1] * nz - zAxis[2] * ny;
      let ey = zAxis[2] * nx - zAxis[0] * nz;
      let ez = zAxis[0] * ny - zAxis[1] * nx;
      const eLen = Math.hypot(ex, ey, ez);
      if (eLen < 1e-6) { ex = 1; ey = 0; ez = 0; } else { ex /= eLen; ey /= eLen; ez /= eLen; }
      const northX = ny * ez - nz * ey;
      const northY = nz * ex - nx * ez;
      const northZ = nx * ey - ny * ex;
      const b  = (e.bearingDeg ?? 0) * (Math.PI / 180);
      const cb = Math.cos(b), sb = Math.sin(b);
      u.uRfVolDir.value[i].set(
        northX * cb + ex * sb,
        northY * cb + ey * sb,
        northZ * cb + ez * sb
      ).normalize();

      u.uRfVolAngle.value[i]    = ((e.beamWidthDeg ?? 30) * 0.5) * (Math.PI / 180);
      u.uRfVolStrength.value[i] = Math.min(1, Math.max(0, e.strength ?? 0.5));
      u.uRfVolFreq.value[i]     = Math.min(1, Math.max(0, e.freqNorm  ?? 0.5));
    }
    u.uRfVolCount.value = N;
    this._heatmapCompMat.uniformsNeedUpdate = true;

    // Persist emitter cones into the voxel world model (troposphere layer)
    if (this._voxelField) {
      for (let i = 0; i < N; i++) {
        const e = emitters[i];
        this._voxelField.injectRfCone(
          e.lat, e.lon,
          e.bearingDeg   ?? 0,
          e.beamWidthDeg ?? 30,
          (e.strength    ?? 0.5) * 0.5,  // half-weight (real-time uniforms are authoritative)
          20000
        );
      }
    }
  }

  /**
   * Inject an arbitrary observation directly into the persistent voxel world model.
   * Use this for network node activity, classification results, or any other
   * geo-tagged evidence that should survive beyond the current frame.
   *
   * @param {number} latDeg
   * @param {number} lonDeg
   * @param {number} altM      Altitude in metres (0 = surface, 20000 = troposphere)
   * @param {number} rf        RF energy contribution 0–1
   * @param {number} network   Network density contribution 0–1
   * @param {number} cls       Classification confidence 0–1
   */
  injectPointVoxel(latDegOrObj, lonDeg, altM = 0, rf = 0, network = 0, cls = 0) {
    // Accept both positional args and an object: {lat, lon, alt, rf, net, confidence}
    if (typeof latDegOrObj === 'object' && latDegOrObj !== null) {
      const o = latDegOrObj;
      latDegOrObj = o.lat;
      lonDeg      = o.lon;
      altM        = o.alt       ?? 0;
      rf          = o.rf        ?? 0;
      network     = o.net       ?? o.network ?? 0;
      cls         = o.confidence ?? o.cls    ?? 0;
    }
    if (this._voxelField) this._voxelField.injectPoint(latDegOrObj, lonDeg, altM, rf, network, cls);
  }

  /**
   * Inject a discrete strobe event into the GPU shockwave field.
   * The event propagates as an expanding ring on the globe surface,
   * with type-specific waveform shaping (directional, pulsing, jagged).
   *
   * Also auto-injects into the voxel field for persistent spatial memory.
   *
   * @param {Object} opts
   * @param {number} opts.lat          Decimal degrees
   * @param {number} opts.lon          Decimal degrees
   * @param {number} [opts.alt=50000]  Altitude metres (for voxel injection layer)
   * @param {number} [opts.energy=1.0] Magnitude 0–2 (log-scaled visually)
   * @param {number} [opts.type=0]     STROBE_TYPE enum (0–9: network/RF/C2/UAV/anomaly/cluster/interference/phantom/conflict)
   * @param {number} [opts.bearingDeg] Direction for directional strobes (RF, UAV, C2)
   * @param {number} [opts.fhBw=0]         RF fingerprint: freq-hop bandwidth (0-1)
   * @param {number} [opts.fhDt=0]         RF fingerprint: dwell time (0-1)
   * @param {number} [opts.fhDc=0]         RF fingerprint: duty cycle (0-1)
   * @param {number} [opts.fhPp=0.5]       RF fingerprint: pattern predictability (0=random, 1=deterministic)
   * @param {number} [opts.rfSnr=0.5]      RF fingerprint: signal-to-noise ratio (0-1)
   * @param {number} [opts.specEntropy=0.5] RF fingerprint: spectral entropy (0=pure tone, 1=white noise)
   * @param {number} [opts.hopVariance=0]  RF fingerprint: variance in hop intervals (0=stable, 1=chaotic)
   * @param {number} [opts.modulationClass=0] RF fingerprint: modulation class (0=AM, 0.33=FM, 0.66=FSK, 1=FHSS)
   */
  injectStrobe(opts) {
    if (!this._strobeData) return;
    const R = 6371000;
    const i = this._strobeCount % MAX_STROBES;

    const lat = opts.lat ?? 0;
    const lon = opts.lon ?? 0;
    const energy = Math.min(2.0, Math.max(0, opts.energy ?? 1.0));
    const sType  = opts.type ?? STROBE_TYPE.NETWORK;

    // Convert lat/lon to ECEF
    const φ = lat * (Math.PI / 180);
    const λ = lon * (Math.PI / 180);
    const cosφ = Math.cos(φ);
    const ecefX = R * cosφ * Math.cos(λ);
    const ecefY = R * cosφ * Math.sin(λ);
    const ecefZ = R * Math.sin(φ);

    // Direction vector (ECEF) for directional strobes
    let dirX = 0, dirY = 0, dirZ = 0;
    if (opts.bearingDeg !== undefined && (sType === STROBE_TYPE.RF || sType === STROBE_TYPE.UAV || sType === STROBE_TYPE.C2)) {
      const nx = cosφ * Math.cos(λ), ny = cosφ * Math.sin(λ), nz = Math.sin(φ);
      // ENU basis at this lat/lon
      let ex = -Math.sin(λ), ey = Math.cos(λ), ez = 0;
      const northX = -Math.sin(φ) * Math.cos(λ);
      const northY = -Math.sin(φ) * Math.sin(λ);
      const northZ =  Math.cos(φ);
      const b  = (opts.bearingDeg ?? 0) * (Math.PI / 180);
      const cb = Math.cos(b), sb = Math.sin(b);
      dirX = northX * cb + ex * sb;
      dirY = northY * cb + ey * sb;
      dirZ = northZ * cb + ez * sb;
    } else if (sType === STROBE_TYPE.CONFLICT) {
      // CONFLICT: dir.x = instability (flicker rate), dir.y = CSI (intensity)
      dirX = opts.dirX ?? 0;
      dirY = opts.dirY ?? 0;
    } else if (sType === STROBE_TYPE.PHANTOM) {
      // PHANTOM: dir.x = phantom_pull (attractor strength), dir.y = synthetic_ratio
      dirX = opts.phantomPull ?? opts.dirX ?? 0;
      dirY = opts.syntheticRatio ?? opts.dirY ?? 0;
    }

    // Pack into ring buffer: 16 floats per strobe
    const base = i * STROBE_FLOATS;
    this._strobeData[base + 0] = ecefX;
    this._strobeData[base + 1] = ecefY;
    this._strobeData[base + 2] = ecefZ;
    this._strobeData[base + 3] = performance.now() * 0.001; // t0 in seconds
    this._strobeData[base + 4] = energy;
    this._strobeData[base + 5] = sType;
    this._strobeData[base + 6] = dirX;
    this._strobeData[base + 7] = dirY;
    // RF fingerprint fields (8-15) — defaults to 0 (neutral/unknown identity)
    this._strobeData[base +  8] = opts.fhBw          ?? 0.0;
    this._strobeData[base +  9] = opts.fhDt          ?? 0.0;
    this._strobeData[base + 10] = opts.fhDc          ?? 0.0;
    this._strobeData[base + 11] = opts.fhPp          ?? 0.5;
    this._strobeData[base + 12] = opts.rfSnr         ?? 0.5;
    this._strobeData[base + 13] = opts.specEntropy   ?? 0.5;
    this._strobeData[base + 14] = opts.hopVariance   ?? 0.0;
    this._strobeData[base + 15] = opts.modulationClass ?? 0.0;

    this._strobeCount++;
    this._strobeDirty = true;

    // Mirror into voxel field for persistence beyond shockwave fade
    // Upgrade 4: strobe-modulated coupling — high-energy strobes boost voxel
    // injection more aggressively, creating self-reinforcing hot zones
    const alt = opts.alt ?? 50000;
    const energyBoost = 1.0 + energy * 0.3; // stronger strobes → stronger memory
    const rf  = ((sType === STROBE_TYPE.RF || sType === STROBE_TYPE.C2 || sType === STROBE_TYPE.CLUSTER)
                   ? energy * 0.5 : energy * 0.15) * energyBoost;
    const net = (sType === STROBE_TYPE.NETWORK ? energy * 0.6 : 0.2) * energyBoost;
    const cls = (sType === STROBE_TYPE.C2 || sType === STROBE_TYPE.ANOMALY || sType === STROBE_TYPE.CLUSTER)
                  ? 0.7 * energyBoost : 0.3;
    this.injectPointVoxel(lat, lon, alt, rf, net, cls);

    // Strobe-modulated voxel persistence: inject extra energy at nearby alt layers
    // to create vertical column persistence for important events
    if (energy > 1.2 && this._voxelField) {
      for (let aOff = -20000; aOff <= 20000; aOff += 20000) {
        if (aOff === 0) continue;
        const neighborAlt = Math.max(0, alt + aOff);
        this._voxelField.injectPoint(lat, lon, neighborAlt,
          rf * 0.3, net * 0.3, cls * 0.2);
      }
    }
  }

  /**
   * Inject a sequence of strobes along an ASN transit path to animate
   * hop-by-hop control flow across the globe.
   *
   * @param {Object} opts
   * @param {Array<{lat, lon}>} opts.hops — intermediate positions for each ASN hop
   * @param {number} [opts.energy=1.2] — strobe energy per hop
   * @param {number} [opts.delayPerHop=0.15] — seconds between hop strobes
   */
  /**
   * Inject an intent-field heat point onto the GPU strobe system.
   * Called by the 🎯 INTENT panel when intent_score > 0.3 for a cluster.
   * Routes to CONFLICT (FORMING), PHANTOM (COVERT), or ANOMALY strobe type.
   *
   * @param {number} lat    WGS-84 latitude
   * @param {number} lon    WGS-84 longitude
   * @param {number} score  intent_score [0-1] → strobe energy
   * @param {string} [color] CSS hex hint — determines strobe type
   */
  injectHeatPoint(lat, lon, score, color) {
    const col = (color || '').toLowerCase();
    let type = STROBE_TYPE.ANOMALY;
    if (col.includes('f43f5e')) type = STROBE_TYPE.CONFLICT;   // FORMING
    else if (col.includes('a855f7')) type = STROBE_TYPE.PHANTOM; // COVERT
    this.injectStrobe({ lat, lon, energy: Math.max(0.4, score * 2.2), type, alt: 50000 });
  }

  injectPathStrobes(opts = {}) {
    const hops = opts.hops || [];
    if (hops.length < 2) return;
    const energy = opts.energy ?? 1.2;
    const delayPerHop = opts.delayPerHop ?? 0.15;

    for (let i = 0; i < hops.length; i++) {
      const hop = hops[i];
      // Stagger injection time so strobes propagate hop by hop
      setTimeout(() => {
        this.injectStrobe({
          lat: hop.lat,
          lon: hop.lon,
          energy: energy * (i === 0 || i === hops.length - 1 ? 1.5 : 1.0),
          type: STROBE_TYPE.PATH,
          alt: 60000,
        });
      }, i * delayPerHop * 1000);
    }
  }

  /**
   * Render submarine cables as Cesium polylines on the globe.
   * Called once with cable data from the API.
   *
   * @param {Array} cables — array of cable objects with landing_points
   * @param {Object} [viewer] — Cesium viewer (defaults to this._viewer)
   */
  renderCableOverlay(cables, viewer) {
    const v = viewer || this._viewer;
    if (!v || !cables || !cables.length) return;
    if (!this._cableEntities) this._cableEntities = [];

    // Remove previous cables
    for (const e of this._cableEntities) {
      v.entities.remove(e);
    }
    this._cableEntities = [];

    for (const cable of cables) {
      const pts = cable.landing_points;
      if (!pts || pts.length < 2) continue;

      const positions = [];
      for (const pt of pts) {
        positions.push(Cesium.Cartesian3.fromDegrees(pt.lon, pt.lat, 0));
      }

      const entity = v.entities.add({
        polyline: {
          positions: positions,
          width: 1.5,
          material: new Cesium.PolylineDashMaterialProperty({
            color: Cesium.Color.fromCssColorString('rgba(0, 180, 255, 0.25)'),
            dashLength: 16.0,
          }),
          clampToGround: true,
        },
        name: cable.name,
        description: `${cable.name} — ${cable.capacity_tbps} Tbps · ${(cable.owners || []).join(', ')}`,
      });
      this._cableEntities.push(entity);
    }
  }

  /**
   * Render IX points as pulsing markers on the globe.
   *
   * @param {Array} ixPoints — array of IX objects with lat, lon, name
   * @param {Object} [viewer] — Cesium viewer
   */
  renderIxOverlay(ixPoints, viewer) {
    const v = viewer || this._viewer;
    if (!v || !ixPoints || !ixPoints.length) return;
    if (!this._ixEntities) this._ixEntities = [];

    for (const e of this._ixEntities) {
      v.entities.remove(e);
    }
    this._ixEntities = [];

    for (const ix of ixPoints) {
      const entity = v.entities.add({
        position: Cesium.Cartesian3.fromDegrees(ix.lon, ix.lat, 500),
        point: {
          pixelSize: 6,
          color: Cesium.Color.fromCssColorString('rgba(255, 200, 0, 0.7)'),
          outlineColor: Cesium.Color.fromCssColorString('rgba(255, 140, 0, 0.5)'),
          outlineWidth: 2,
        },
        label: {
          text: ix.name,
          font: '10px monospace',
          fillColor: Cesium.Color.fromCssColorString('rgba(255, 200, 0, 0.6)'),
          style: Cesium.LabelStyle.FILL,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -10),
          scale: 0.8,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        name: ix.name,
        description: `${ix.name} — ${ix.peak_tbps} Tbps peak`,
      });
      this._ixEntities.push(entity);
    }
  }

  /**
   * Render ASN transit path arcs between clusters.
   *
   * @param {Array} paths — path objects from /api/infrastructure/flow
   * @param {Object} [viewer] — Cesium viewer
   */
  renderPathArcs(paths, viewer) {
    const v = viewer || this._viewer;
    if (!v || !paths) return;
    if (!this._pathEntities) this._pathEntities = [];

    for (const e of this._pathEntities) {
      v.entities.remove(e);
    }
    this._pathEntities = [];

    for (const p of paths) {
      const [srcLat, srcLon] = p.centroids[0];
      const [dstLat, dstLon] = p.centroids[1];

      // Arc colour: green=physical, red=synthetic, amber=uncertain
      const isSynthetic = p.is_synthetic;
      const aligned = p.cable_alignment?.aligned;
      const arcColor = isSynthetic
        ? 'rgba(255, 40, 40, 0.7)'
        : aligned
          ? 'rgba(0, 255, 120, 0.5)'
          : 'rgba(255, 180, 0, 0.5)';

      // Interpolate great-circle with altitude for visible arc
      const steps = 40;
      const positions = [];
      for (let i = 0; i <= steps; i++) {
        const t = i / steps;
        const lat = srcLat + (dstLat - srcLat) * t;
        const lon = srcLon + (dstLon - srcLon) * t;
        // Parabolic arc — peaks at midpoint
        const arcHeight = Math.sin(t * Math.PI) * Math.max(50000, p.cable_alignment?.distance_km * 30 || 100000);
        positions.push(Cesium.Cartesian3.fromDegrees(lon, lat, arcHeight));
      }

      const entity = v.entities.add({
        polyline: {
          positions: positions,
          width: isSynthetic ? 2.5 : 1.5,
          material: new Cesium.PolylineGlowMaterialProperty({
            glowPower: isSynthetic ? 0.3 : 0.15,
            color: Cesium.Color.fromCssColorString(arcColor),
          }),
        },
        name: `${p.src_asn} → ${p.dst_asn}`,
        description: `Path: ${p.hop_path.join(' → ')} · Score: ${p.path_score}`,
      });
      this._pathEntities.push(entity);
    }
  }

  /**
   * Render IX heatmap — size + colour-coded markers based on heat score.
   * Optionally injects CONFLICT strobes at hot IX nodes.
   *
   * @param {Array} ixHeats — array of IX heat objects from /api/infrastructure/ix/heatmap
   * @param {Object} [viewer] — Cesium viewer
   * @param {boolean} [injectStrobes=true] — inject CONFLICT strobes at hot IX
   */
  renderIxHeatmap(ixHeats, viewer, injectStrobes = true) {
    const v = viewer || this._viewer;
    if (!v || !ixHeats || !ixHeats.length) return;
    if (!this._ixHeatEntities) this._ixHeatEntities = [];

    // Remove previous heat markers
    for (const e of this._ixHeatEntities) {
      v.entities.remove(e);
    }
    this._ixHeatEntities = [];

    for (const ix of ixHeats) {
      const heat = ix.heat || 0;
      if (heat < 0.01) continue;

      const csi = ix.csi || {};
      const forecast = ix.forecast || {};
      const trend = ix.trend || {};
      const vel = Math.abs(trend.velocity || 0);

      // Size: base 8px + heat + velocity boost
      const size = 8 + heat * 22 + vel * 200;

      // Colour: CSI-aware — purple (synthetic), white (conflict), orange (stress)
      let r, g, b, a;
      const csiVal = csi.csi || 0;
      if (csiVal > 0.8) {
        // Active conflict: white-hot
        r = 255; g = 240; b = 240; a = 0.95;
      } else if (csiVal > 0.5) {
        // Contested: red-magenta
        r = 255; g = 60; b = 120; a = 0.85;
      } else if (heat > 0.7) {
        r = 255; g = 255 - (heat - 0.7) * 200; b = 255 - (heat - 0.7) * 600;
        a = 0.95;
      } else if (heat > 0.4) {
        const t = (heat - 0.4) / 0.3;
        r = 255; g = Math.round(180 * (1 - t)); b = 0; a = 0.8;
      } else if (heat > 0.15) {
        const t = (heat - 0.15) / 0.25;
        r = Math.round(255 * t); g = Math.round(200 + 55 * (1 - t)); b = 0; a = 0.6;
      } else {
        r = 0; g = 180; b = 60; a = 0.35;
      }

      // Forecast: brighten if imminent
      if (forecast.label === 'IMMINENT') {
        r = Math.min(255, r + 40);
        g = Math.min(255, g + 40);
        b = Math.min(255, b + 40);
        a = Math.min(1.0, a + 0.15);
      }

      const color = Cesium.Color.fromBytes(r, g, b, Math.round(a * 255));

      // Label text: heat + CSI + forecast
      const csiLabel = csi.label ? ` CSI:${csiVal.toFixed(2)}` : '';
      const fcLabel = forecast.label && forecast.label !== 'UNLIKELY'
        ? `\n⚠ ${forecast.label} ${(forecast.probability * 100).toFixed(0)}%` : '';
      const velArrow = vel > 0.001 ? (trend.velocity > 0 ? '↑' : '↓') : '';

      const entity = v.entities.add({
        position: Cesium.Cartesian3.fromDegrees(ix.lon, ix.lat, 1000),
        point: {
          pixelSize: size,
          color: color,
          outlineColor: Cesium.Color.fromBytes(r, g, b, Math.round(a * 0.4 * 255)),
          outlineWidth: size * 0.4,
        },
        label: {
          text: `${ix.name}\n${ix.tier} ${(heat * 100).toFixed(0)}%${velArrow}${csiLabel}${fcLabel}`,
          font: '11px monospace',
          fillColor: color,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -(size / 2 + 12)),
          scale: 0.9,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        name: ix.name,
        description: `Heat: ${(heat * 100).toFixed(1)}% · ${ix.tier} · CSI: ${csiVal.toFixed(3)} (${csi.label || 'N/A'})`,
      });
      this._ixHeatEntities.push(entity);

      // Inject CONFLICT strobes — encode velocity as energy, instability as dirX
      if (injectStrobes && heat > 0.4) {
        const instability = ix.latency_variance || 0;
        this.injectStrobe({
          lat: ix.lat,
          lon: ix.lon,
          energy: Math.min(2.5, heat * 2.0 + vel * 5.0),
          type: STROBE_TYPE.CONFLICT,
          alt: 80000,
          dirX: instability,  // shader reads dir.x for flicker rate
          dirY: csiVal,       // shader can read dir.y for CSI
        });
      }
    }
  }

  /**
   * Render conflict arcs between ASN pairs at an IX.
   *
   * @param {Array} conflicts — conflict objects from /api/infrastructure/ix/heatmap
   * @param {Array} ixHeats — IX heat data (for position lookup)
   * @param {Object} [viewer] — Cesium viewer
   */
  renderConflictArcs(conflicts, ixHeats, viewer) {
    const v = viewer || this._viewer;
    if (!v || !conflicts || !conflicts.length) return;
    if (!this._conflictEntities) this._conflictEntities = [];

    for (const e of this._conflictEntities) {
      v.entities.remove(e);
    }
    this._conflictEntities = [];

    // Build IX position lookup
    const ixPos = {};
    for (const ix of (ixHeats || [])) {
      ixPos[ix.name] = { lat: ix.lat, lon: ix.lon };
    }

    for (const c of conflicts) {
      const pos = ixPos[c.ix];
      if (!pos) continue;

      // Conflict severity → colour
      const sevColor = {
        'CRITICAL': 'rgba(255, 40, 40, 0.9)',
        'HIGH':     'rgba(255, 140, 0, 0.8)',
        'MEDIUM':   'rgba(255, 220, 0, 0.6)',
        'LOW':      'rgba(200, 200, 200, 0.4)',
      }[c.severity] || 'rgba(255, 255, 255, 0.5)';

      // Draw pulsing ring around IX conflict point
      const alt = 5000 + c.confidence * 50000;
      const positions = [];
      for (let a = 0; a <= 360; a += 10) {
        const rad = a * Math.PI / 180;
        const radius = 0.5 + c.confidence * 1.5; // degrees
        positions.push(Cesium.Cartesian3.fromDegrees(
          pos.lon + Math.cos(rad) * radius,
          pos.lat + Math.sin(rad) * radius * 0.7, // aspect correction
          alt
        ));
      }

      const entity = v.entities.add({
        polyline: {
          positions: positions,
          width: 2 + c.confidence * 3,
          material: new Cesium.PolylineGlowMaterialProperty({
            glowPower: 0.2 + c.confidence * 0.3,
            color: Cesium.Color.fromCssColorString(sevColor),
          }),
        },
        name: `${c.icon} ${c.type}: ${c.asn_labels.join(' ↔ ')}`,
        description: c.summary,
      });
      this._conflictEntities.push(entity);
    }
  }

  /**
   * Bearing intersection solver — finds the most likely emitter origin from
   * N directional observations using the pairwise closest-point method.
   *
   * @param {Array<{lat, lon, bearingDeg}>} observations
   * @returns {{ lat, lon, confidence }} or null if < 2 observations
   */
  solveRfOrigin(observations) {
    if (observations.length < 2) return null;
    const R = 6371000;

    // Convert each observation to ECEF origin + unit direction
    const lines = observations.map(o => {
      const φ = o.lat * (Math.PI / 180);
      const λ = o.lon * (Math.PI / 180);
      const cosφ = Math.cos(φ);
      const nx = cosφ * Math.cos(λ), ny = cosφ * Math.sin(λ), nz = Math.sin(φ);

      let ex = -Math.sin(λ), ey = Math.cos(λ), ez = 0;        // East
      const northX = -Math.sin(φ)*Math.cos(λ);
      const northY = -Math.sin(φ)*Math.sin(λ);
      const northZ =  Math.cos(φ);                              // North

      const b  = (o.bearingDeg ?? 0) * (Math.PI / 180);
      const cb = Math.cos(b), sb = Math.sin(b);
      return {
        o: [nx * R, ny * R, nz * R],
        d: [northX*cb + ex*sb, northY*cb + ey*sb, northZ*cb + ez*sb]
      };
    });

    // Weighted least-squares closest-point accumulator
    // For each pair, find the midpoint of the closest approach segment.
    let sumX = 0, sumY = 0, sumZ = 0, sumW = 0;
    let sumDist = 0, pairCount = 0;

    for (let a = 0; a < lines.length - 1; a++) {
      for (let b = a + 1; b < lines.length; b++) {
        const { o: o1, d: d1 } = lines[a];
        const { o: o2, d: d2 } = lines[b];

        const w0 = [o1[0]-o2[0], o1[1]-o2[1], o1[2]-o2[2]];
        const dot = (u, v) => u[0]*v[0] + u[1]*v[1] + u[2]*v[2];
        const aA = dot(d1,d1), bB = dot(d1,d2), c = dot(d2,d2);
        const d  = dot(d1,w0), e  = dot(d2,w0);
        const denom = aA*c - bB*bB;
        if (Math.abs(denom) < 1e-10) continue;

        const sc = (bB*e - c*d)  / denom;
        const tc = (aA*e - bB*d) / denom;
        const p1 = [o1[0]+sc*d1[0], o1[1]+sc*d1[1], o1[2]+sc*d1[2]];
        const p2 = [o2[0]+tc*d2[0], o2[1]+tc*d2[1], o2[2]+tc*d2[2]];
        const mx = (p1[0]+p2[0])*0.5, my = (p1[1]+p2[1])*0.5, mz = (p1[2]+p2[2])*0.5;
        const sepDist = Math.hypot(p1[0]-p2[0], p1[1]-p2[1], p1[2]-p2[2]);

        // Weight inversely proportional to line separation (tighter = more confident)
        const w = 1.0 / (sepDist + 1000);
        sumX += mx * w; sumY += my * w; sumZ += mz * w; sumW += w;
        sumDist += sepDist; pairCount++;
      }
    }
    if (sumW === 0) return null;

    const cx = sumX/sumW, cy = sumY/sumW, cz = sumZ/sumW;
    const r  = Math.hypot(cx, cy, cz);
    const lat = Math.asin(cz / r)  * (180 / Math.PI);
    const lon = Math.atan2(cy, cx) * (180 / Math.PI);
    const avgSep = sumDist / pairCount;
    const confidence = Math.min(1, 50000 / (avgSep + 1000)); // 50 km = ~100% conf

    return { lat, lon, confidence, avgSepMeters: Math.round(avgSep) };
  }

  updateRFEmitters(emitters) {
    if (!this._rfVolMesh) return;
    const N = Math.min(emitters.length, MAX_RF_EMITTERS);

    for (let i = 0; i < N; i++) {
      const e  = emitters[i];
      const p  = e.pos || e;
      this._rfInstCenter[i * 3 + 0] = p.x ?? 0;
      this._rfInstCenter[i * 3 + 1] = p.y ?? 0;
      this._rfInstCenter[i * 3 + 2] = p.z ?? 0;
      this._rfInstRadius[i]    = Math.min(e.radius ?? RF_DEFAULT_RADIUS, 300000); // cap at 300km
      this._rfInstIntensity[i] = Math.min(1, Math.max(0, e.intensity ?? 0.5));
      this._rfInstAnomaly[i]   = Math.min(1, Math.max(0, e.anomaly   ?? 0));
    }

    const geo = this._rfVolMesh.geometry;
    geo.instanceCount = N;
    for (const k of ['iCenter','iRadius','iIntensity','iAnomaly']) {
      if (geo.attributes[k]) geo.attributes[k].needsUpdate = true;
    }

    this._rfVolMesh.visible = N > 0;
  }

  setRFVolumetricVisible(visible) {
    if (this._rfVolMesh) this._rfVolMesh.visible = visible;
  }

  _buildEdgeBeamLayer() {
    const N   = MAX_EDGE_BEAMS;
    const geo = new THREE.InstancedBufferGeometry();

    // Quad: 4 verts spanning x=-1..1 (along), y=-1..1 (perp)
    const quadUV  = new Float32Array([-1,-1, 1,-1, 1,1, -1,1]);
    const quadIdx = new Uint16Array([0,1,2, 0,2,3]);
    geo.setAttribute('aUV',  new THREE.BufferAttribute(quadUV, 2));
    geo.setIndex(new THREE.BufferAttribute(quadIdx, 1));
    geo.instanceCount = 0;

    this._beamInstSrc       = new Float32Array(N * 3);
    this._beamInstDst       = new Float32Array(N * 3);
    this._beamInstIntensity = new Float32Array(N);
    this._beamInstAnomaly   = new Float32Array(N);
    this._beamInstPhase     = new Float32Array(N);
    for (let i = 0; i < N; i++) this._beamInstPhase[i] = (i * 0.6180339887) % (Math.PI * 2);

    geo.setAttribute('iSrc',       new THREE.InstancedBufferAttribute(this._beamInstSrc,       3));
    geo.setAttribute('iDst',       new THREE.InstancedBufferAttribute(this._beamInstDst,       3));
    geo.setAttribute('iIntensity', new THREE.InstancedBufferAttribute(this._beamInstIntensity, 1));
    geo.setAttribute('iAnomaly',   new THREE.InstancedBufferAttribute(this._beamInstAnomaly,   1));
    geo.setAttribute('iPhase',     new THREE.InstancedBufferAttribute(this._beamInstPhase,     1));

    geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(0,0,0), 8e6);

    const mat = new THREE.ShaderMaterial({
      uniforms:       { uTime: this._uTime, uQuality: this._uQuality },
      vertexShader:   RF_BEAM_VERT,
      fragmentShader: RF_BEAM_FRAG,
      transparent:    true,
      depthWrite:     false,
      blending:       THREE.AdditiveBlending,
      side:           THREE.DoubleSide,
    });

    this._beamMesh = new THREE.Mesh(geo, mat);
    this._beamMesh.frustumCulled = false;
    this._beamMesh.renderOrder = 2;   // beams same tier as arcs
    this._beamMesh.visible = false;
    this._scene.add(this._beamMesh);
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _buildHeatmapLayer — three-pass temporal GPU field renderer
   *
   * Pass 0 (BLIT):  ping-pong copy of current RT → previous RT.
   * Pass 1 (SPLAT): accumulates per-node anisotropic Gaussian splats into
   *   a half-resolution FloatType RGB render target.
   * Pass 2 (COMP):  composites the field over the main framebuffer with
   *   per-channel velocity, predictive bloom, and atmospheric horizon fade.
   * ----------------------------------------------------------------------- */
  _buildHeatmapLayer() {
    const w = Math.max(1, Math.floor(this._renderer.domElement.width  / 2));
    const h = Math.max(1, Math.floor(this._renderer.domElement.height / 2));

    const rtOpts = {
      type:          THREE.FloatType,
      minFilter:     THREE.LinearFilter,
      magFilter:     THREE.LinearFilter,
      depthBuffer:   false,
      stencilBuffer: false,
    };
    this._heatmapRT      = new THREE.WebGLRenderTarget(w, h, rtOpts);
    this._heatmapRT_prev = new THREE.WebGLRenderTarget(w, h, rtOpts);

    // ── Splat geometry: MAX_NODES points, one per live node ──────────────
    const splatGeo = new THREE.BufferGeometry();
    this._heatmapSplatPos     = new Float32Array(MAX_NODES * 3);
    this._heatmapSplatConf    = new Float32Array(MAX_NODES);
    this._heatmapSplatLife    = new Float32Array(MAX_NODES);
    this._heatmapSplatShadow  = new Float32Array(MAX_NODES);
    this._heatmapSplatAnomaly = new Float32Array(MAX_NODES);
    this._heatmapSplatEntropy = new Float32Array(MAX_NODES);
    this._heatmapSplatAngle   = new Float32Array(MAX_NODES);

    splatGeo.setAttribute('aPos',       new THREE.BufferAttribute(this._heatmapSplatPos,     3));
    splatGeo.setAttribute('aConf',      new THREE.BufferAttribute(this._heatmapSplatConf,    1));
    splatGeo.setAttribute('aLifecycle', new THREE.BufferAttribute(this._heatmapSplatLife,    1));
    splatGeo.setAttribute('aShadow',    new THREE.BufferAttribute(this._heatmapSplatShadow,  1));
    splatGeo.setAttribute('aAnomaly',   new THREE.BufferAttribute(this._heatmapSplatAnomaly, 1));
    splatGeo.setAttribute('aEntropy',   new THREE.BufferAttribute(this._heatmapSplatEntropy, 1));
    splatGeo.setAttribute('aAngle',     new THREE.BufferAttribute(this._heatmapSplatAngle,   1));
    splatGeo.setDrawRange(0, 0);

    const splatMat = new THREE.ShaderMaterial({
      uniforms: {
        uSplatSize:  { value: 32.0 },
        uViewHeight: this._uViewHeight,
      },
      vertexShader:   HEATMAP_SPLAT_VERT,
      fragmentShader: HEATMAP_SPLAT_FRAG,
      transparent:    false,
      depthWrite:     false,
      depthTest:      false,
      blending:       THREE.AdditiveBlending,
    });

    this._heatmapSplatMesh = new THREE.Points(splatGeo, splatMat);
    this._heatmapSplatMesh.frustumCulled = false;
    this._heatmapSplatScene = new THREE.Scene();
    this._heatmapSplatScene.add(this._heatmapSplatMesh);

    // ── Blit scene: copies current RT → previous RT (identity, ping-pong) ──
    const blitGeo = new THREE.PlaneGeometry(2, 2);
    this._uBlitSrc = { value: this._heatmapRT.texture };
    const blitMat = new THREE.ShaderMaterial({
      uniforms:       { uSrc: this._uBlitSrc, uDecay: { value: 1.0 }, uDecayRF: { value: 1.0 } },
      vertexShader:   HEATMAP_BLIT_VERT,
      fragmentShader: HEATMAP_BLIT_FRAG,
      transparent:    false,
      depthWrite:     false,
      depthTest:      false,
    });
    const blitMesh = new THREE.Mesh(blitGeo, blitMat);
    blitMesh.frustumCulled = false;
    this._heatmapBlitScene = new THREE.Scene();
    this._heatmapBlitScene.add(blitMesh);

    // ── Decay scene: prev RT → current RT (network×0.94, RF×0.97) ──────────
    // RF lingers longer than network packets (physics > protocol bursts).
    const FIELD_DECAY    = 0.94;
    const FIELD_DECAY_RF = 0.97;
    this._uDecaySrc = { value: this._heatmapRT_prev.texture };
    const decayMat = new THREE.ShaderMaterial({
      uniforms:       { uSrc: this._uDecaySrc, uDecay: { value: FIELD_DECAY }, uDecayRF: { value: FIELD_DECAY_RF } },
      vertexShader:   HEATMAP_BLIT_VERT,
      fragmentShader: HEATMAP_BLIT_FRAG,
      transparent:    false,
      depthWrite:     false,
      depthTest:      false,
    });
    const decayMesh = new THREE.Mesh(blitGeo, decayMat);
    decayMesh.frustumCulled = false;
    this._heatmapDecayScene = new THREE.Scene();
    this._heatmapDecayScene.add(decayMesh);

    // ── RF cone splat geometry ──────────────────────────────────────────────
    const MAX_RF_CONES = 2048;
    const rfGeo = new THREE.BufferGeometry();
    this._rfConePos      = new Float32Array(MAX_RF_CONES * 3);
    this._rfConeBearing  = new Float32Array(MAX_RF_CONES);
    this._rfConeBeam     = new Float32Array(MAX_RF_CONES);
    this._rfConeStrength = new Float32Array(MAX_RF_CONES);
    rfGeo.setAttribute('aRfPos',        new THREE.BufferAttribute(this._rfConePos,      3));
    rfGeo.setAttribute('aRfBearing',    new THREE.BufferAttribute(this._rfConeBearing,  1));
    rfGeo.setAttribute('aRfBeamWidth',  new THREE.BufferAttribute(this._rfConeBeam,     1));
    rfGeo.setAttribute('aRfStrength',   new THREE.BufferAttribute(this._rfConeStrength, 1));
    rfGeo.setDrawRange(0, 0);

    const rfMat = new THREE.ShaderMaterial({
      uniforms: {
        uRfSplatSize: { value: 80.0 },
        uViewHeight:  this._uViewHeight,
      },
      vertexShader:   RF_CONE_VERT,
      fragmentShader: RF_CONE_FRAG,
      transparent:    false,
      depthWrite:     false,
      depthTest:      false,
      blending:       THREE.AdditiveBlending,
    });
    this._rfConeMesh = new THREE.Points(rfGeo, rfMat);
    this._rfConeMesh.frustumCulled = false;
    this._rfConeScene = new THREE.Scene();
    this._rfConeScene.add(this._rfConeMesh);

    // ── Composite scene: colorize + blend over main framebuffer ─────────
    const compGeo = new THREE.PlaneGeometry(2, 2);
    const compMat = new THREE.ShaderMaterial({
      uniforms: {
        uHeatmap:         { value: this._heatmapRT.texture },
        uHeatmapPrev:     { value: this._heatmapRT_prev.texture },
        uBlend:           this._uHeatmapBlend,
        uCameraECEF:      this._uCameraECEF,
        uInvProjView:     this._uHeatmapInvProjView,
        uEarthRadius:     { value: 6.371e6 + 8848 },
        // Volumetric RF emitter array (up to 16 directional sources)
        uRfVolCount:      { value: 0 },
        uRfVolOrigin:     { value: new Array(16).fill(null).map(() => new THREE.Vector3()) },
        uRfVolDir:        { value: new Array(16).fill(null).map(() => new THREE.Vector3(0, 0, 1)) },
        uRfVolAngle:      { value: new Float32Array(16) },
        uRfVolStrength:   { value: new Float32Array(16) },
        uRfVolFreq:       { value: new Float32Array(16) },
        uVoxelAtlas:      { value: null },  // filled after VoxelField init below
        // Strobe shockwave field (filled after texture init below)
        uStrobeTex:       { value: null },
        uStrobeCount:     { value: 0 },
        uTime:            { value: 0 },
      },
      vertexShader:   HEATMAP_COMP_VERT,
      fragmentShader: HEATMAP_COMP_FRAG,
      transparent:    true,
      depthWrite:     false,
      depthTest:      false,
      blending:       THREE.NormalBlending,
    });
    this._heatmapCompMat = compMat;  // retained ref for updateRfVolumetric()
    const compMesh = new THREE.Mesh(compGeo, compMat);
    compMesh.frustumCulled = false;
    this._heatmapCompScene = new THREE.Scene();
    this._heatmapCompScene.add(compMesh);

    // ── Shared orthographic camera (NDC-aligned for all 2D passes) ──────
    this._heatmapOrthoCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

    // ── Sparse voxel world model ─────────────────────────────────────────
    this._voxelField = new VoxelField();
    this._heatmapCompMat.uniforms.uVoxelAtlas.value = this._voxelField.texture;

    // ── Strobe shockwave texture (ring buffer of discrete events) ────────
    // 4-column × MAX_STROBES rows, RGBA Float32:
    //   col 0 (x=0.125): posX, posY, posZ, t0
    //   col 1 (x=0.375): energy, type, dirX, dirY
    //   col 2 (x=0.625): fh_bw, fh_dt, fh_dc, fh_pp
    //   col 3 (x=0.875): snr, spectral_entropy, hop_variance, modulation_class
    this._strobeTex = new THREE.DataTexture(
      new Float32Array(MAX_STROBES * 4 * 4),  // 4 cols × N rows × RGBA
      4, MAX_STROBES,
      THREE.RGBAFormat, THREE.FloatType
    );
    this._strobeTex.needsUpdate = false;
    this._heatmapCompMat.uniforms.uStrobeTex.value   = this._strobeTex;
    this._heatmapCompMat.uniforms.uStrobeCount.value = 0;
    this._heatmapCompMat.uniforms.uTime.value        = 0;

    // ── CPU readback buffer: 64×32 × 4 floats (RGBA) ────────────────────
    this._fieldReadbackBuf = new Float32Array(64 * 32 * 4);

    console.log('[Globe] Temporal heatmap field ready (%dx%d RT)', w, h);
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _syncHeatmapSplats — copies live node attributes into splat geometry
   * Derives semantic channels (shadow/anomaly/entropy) from existing node data.
   * ----------------------------------------------------------------------- */
  _syncHeatmapSplats() {
    const geo       = this._nodeMesh.geometry;
    const srcPos    = geo.attributes.instancePosition.array;
    const srcConf   = geo.attributes.instanceConf.array;
    const srcLife   = geo.attributes.instanceLifecycle.array;
    const srcClust  = geo.attributes.instanceCluster.array;
    const srcViol   = geo.attributes.instanceViolations.array;
    const srcId     = geo.attributes.instanceId.array;
    const n         = this._nodeCount;

    this._heatmapSplatPos.set(srcPos.subarray(0, n * 3));

    for (let i = 0; i < n; i++) {
      const conf  = srcConf[i];
      const vpack = srcViol[i];
      const c2    = Math.floor(vpack / 2) % 2;   // bit 1
      const dns   = vpack % 2;                   // bit 0

      this._heatmapSplatConf[i]    = conf;
      this._heatmapSplatLife[i]    = srcLife[i];
      // R: shadow — strong on C2, medium on DNS anomaly, else low
      this._heatmapSplatShadow[i]  = c2 > 0.5 ? 0.9 : (dns > 0.5 ? 0.45 : conf * 0.25);
      // G: anomaly — directly driven by confidence level
      this._heatmapSplatAnomaly[i] = conf;
      // B: entropy — log-scaled cluster size (1→0, 150+→1)
      this._heatmapSplatEntropy[i] = Math.min(1, Math.log(Math.max(srcClust[i], 1)) / 5.0);
      // Smear angle: deterministic golden-angle spiral per node ID
      this._heatmapSplatAngle[i]   = (srcId[i] * 2.399963) % (Math.PI * 2);
    }

    const sg = this._heatmapSplatMesh.geometry;
    for (const k of ['aPos','aConf','aLifecycle','aShadow','aAnomaly','aEntropy','aAngle']) {
      sg.attributes[k].needsUpdate = true;
    }
    sg.setDrawRange(0, n);
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _renderHeatmapPass — executes all three heatmap render passes
   * @param {number} blend  0→1 composite opacity (altitude crossfade)
   * ----------------------------------------------------------------------- */
  _renderHeatmapPass(blend = 1.0) {
    if (!this._heatmapRT || this._nodeCount === 0) return;

    this._syncHeatmapSplats();
    this._uHeatmapBlend.value = blend;

    // ── Voxel field maintenance ───────────────────────────────────────────
    if (this._voxelField) {
      // Decay at ~4 Hz (every 16 frames at 60fps) — cheap CPU loop
      if ((this._frame ?? 0) % 16 === 0) this._voxelField.decay();
      this._voxelField.upload();   // no-op unless dirty
    }

    // ── Strobe shockwave texture upload ──────────────────────────────────
    if (this._strobeDirty && this._strobeTex && this._heatmapCompMat) {
      const N = Math.min(this._strobeCount, MAX_STROBES);
      const texData = this._strobeTex.image.data;
      // Pack ring buffer → 4-column texture (col0: pos+t0, col1: energy+type+dir, col2-3: rf fingerprint)
      for (let s = 0; s < N; s++) {
        const src = s * STROBE_FLOATS;
        const row = s * 4 * 4;  // 4 cols × 4 channels per texel
        // Column 0: posX, posY, posZ, t0
        texData[row + 0] = this._strobeData[src + 0];
        texData[row + 1] = this._strobeData[src + 1];
        texData[row + 2] = this._strobeData[src + 2];
        texData[row + 3] = this._strobeData[src + 3];
        // Column 1: energy, type, dirX, dirY
        texData[row + 4] = this._strobeData[src + 4];
        texData[row + 5] = this._strobeData[src + 5];
        texData[row + 6] = this._strobeData[src + 6];
        texData[row + 7] = this._strobeData[src + 7];
        // Column 2: fh_bw, fh_dt, fh_dc, fh_pp
        texData[row + 8]  = this._strobeData[src + 8];
        texData[row + 9]  = this._strobeData[src + 9];
        texData[row + 10] = this._strobeData[src + 10];
        texData[row + 11] = this._strobeData[src + 11];
        // Column 3: snr, spectral_entropy, hop_variance, modulation_class
        texData[row + 12] = this._strobeData[src + 12];
        texData[row + 13] = this._strobeData[src + 13];
        texData[row + 14] = this._strobeData[src + 14];
        texData[row + 15] = this._strobeData[src + 15];
      }
      this._strobeTex.needsUpdate = true;
      this._heatmapCompMat.uniforms.uStrobeCount.value = N;
      this._strobeDirty = false;
    }
    // Push wall-clock time for strobe propagation animation
    if (this._heatmapCompMat) {
      this._heatmapCompMat.uniforms.uTime.value = performance.now() * 0.001;
    }

    // Update globe-occlusion uniforms for the composite pass
    const camWC = this._viewer.camera.positionWC;
    this._uCameraECEF.value.set(camWC.x, camWC.y, camWC.z);
    this._tmpProjViewMat
      .multiplyMatrices(this._camera.projectionMatrix, this._camera.matrixWorldInverse)
      .invert();
    this._uHeatmapInvProjView.value.copy(this._tmpProjViewMat);

    // Pass 0 — save current → prev (velocity reference for composite)
    this._uBlitSrc.value = this._heatmapRT.texture;
    this._renderer.setRenderTarget(this._heatmapRT_prev);
    this._renderer.clear(true, false, false);
    this._renderer.render(this._heatmapBlitScene, this._heatmapOrthoCamera);

    // Pass 1 — decay-seed current from prev (replaces full clear)
    // Renders prev × FIELD_DECAY into current so the field persists across frames.
    // Without this, each frame was rebuilt from scratch → nodes not splatted this
    // frame disappeared entirely → 15 Hz strobe with 30Hz gate.
    this._uDecaySrc.value = this._heatmapRT_prev.texture;
    this._renderer.setRenderTarget(this._heatmapRT);
    this._renderer.clear(true, false, false);           // clear once for the decay seed
    this._renderer.render(this._heatmapDecayScene, this._heatmapOrthoCamera);

    // Pass 2 — additively splat new network activity on top of the persistent field
    // No clear here — we're accumulating on top of the decay-seeded base.
    this._renderer.render(this._heatmapSplatScene, this._camera);

    // Pass 3 — additively splat RF cone observations (alpha channel only)
    if (this._rfConeCount > 0) {
      this._rfConeMesh.geometry.setDrawRange(0, this._rfConeCount);
      this._rfConeMesh.geometry.attributes.aRfPos.needsUpdate      = true;
      this._rfConeMesh.geometry.attributes.aRfBearing.needsUpdate  = true;
      this._rfConeMesh.geometry.attributes.aRfBeamWidth.needsUpdate = true;
      this._rfConeMesh.geometry.attributes.aRfStrength.needsUpdate = true;
      this._renderer.render(this._rfConeScene, this._camera);
    }
    this._renderer.setRenderTarget(null);

    // Pass 3 — composite temporal field over main framebuffer (with globe occlusion)
    this._renderer.render(this._heatmapCompScene, this._heatmapOrthoCamera);

    // Low-frequency CPU readback for field-driven zone classification (~1 Hz)
    const now = performance.now();
    if (now - this._fieldReadbackTimer > 1800) {
      this._fieldReadbackTimer = now;
      this._fieldReadback();
    }
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _fieldReadback — reads downsampled heatmap RT back to CPU and
   *   classifies each screen-tile into a zone type, stored in _fieldZoneCache.
   *   Called at ~1 Hz from _renderHeatmapPass.  The semantic zone renderer
   *   consults _fieldZoneCache to optionally override edge-bucketing results.
   * ----------------------------------------------------------------------- */
  _fieldReadback() {
    if (!this._heatmapRT || !this._fieldReadbackBuf) return;
    const rtW = this._heatmapRT.width, rtH = this._heatmapRT.height;
    const RW = 64, RH = 32;

    try {
      // Read full half-res RT; on large screens this is ~240KB — acceptable at 1 Hz
      const fullBuf = new Float32Array(rtW * rtH * 4);
      this._renderer.readRenderTargetPixels(this._heatmapRT, 0, 0, rtW, rtH, fullBuf);

      this._fieldZoneCache.clear();
      for (let cy = 0; cy < RH; cy++) {
        for (let cx = 0; cx < RW; cx++) {
          const px  = Math.floor(cx / RW * rtW);
          const py  = Math.floor(cy / RH * rtH);
          const idx = (py * rtW + px) * 4;
          const shadow  = fullBuf[idx];
          const anomaly = fullBuf[idx + 1];
          const entropy = fullBuf[idx + 2];
          const total   = shadow + anomaly + entropy;
          if (total < 0.08) continue;  // below threshold — empty cell

          // Classify by dominant channel ratio
          let type = 'General Activity';
          if      (shadow  > 0.45 && shadow  > anomaly * 1.5) type = 'C2 Relay';
          else if (anomaly > 0.45 && anomaly > shadow  * 1.5 && anomaly > entropy) type = 'Exfiltration Hub';
          else if (entropy > 0.50 && entropy > anomaly * 1.2) type = 'High-Entropy Zone';
          else if (shadow  > 0.25 && anomaly > 0.25)           type = 'Beacon Cluster';

          // Map readback cell to approximate lat/lon for _zoneHistory key
          const lat = 90  - (cy / RH) * 180;
          const lon = -180 + (cx / RW) * 360;
          const key = `${Math.round(lat / 5) * 5}_${Math.round(lon / 5) * 5}`;
          this._fieldZoneCache.set(key, { type, intensity: Math.min(total, 1.0) });
        }
      }
    } catch (_) {
      // Silently absorb; readback can fail on context loss or size mismatch
    }
  }

  /**
   * Update the directional RF edge beam layer.
   * @param {Array<{src:{x,y,z}, dst:{x,y,z}, intensity:number, anomaly:number}>} beams
   */
  updateEdgeBeams(beams) {
    if (!this._beamMesh) return;
    const N = Math.min(beams.length, MAX_EDGE_BEAMS);
    for (let i = 0; i < N; i++) {
      const b = beams[i];
      this._beamInstSrc[i*3]   = b.src.x; this._beamInstSrc[i*3+1]   = b.src.y; this._beamInstSrc[i*3+2]   = b.src.z;
      this._beamInstDst[i*3]   = b.dst.x; this._beamInstDst[i*3+1]   = b.dst.y; this._beamInstDst[i*3+2]   = b.dst.z;
      this._beamInstIntensity[i] = Math.min(1, Math.max(0, b.intensity ?? 0.5));
      this._beamInstAnomaly[i]   = Math.min(1, Math.max(0, b.anomaly   ?? 0));
    }
    const geo = this._beamMesh.geometry;
    geo.instanceCount = N;
    for (const k of ['iSrc','iDst','iIntensity','iAnomaly']) {
      if (geo.attributes[k]) geo.attributes[k].needsUpdate = true;
    }
    this._beamMesh.visible = N > 0;
  }

  /**
   * Run a full HyperField update cycle from the current graph snapshot.
   * Creates HyperField lazily on first call.
   * Call this each frame or on graph change events.
   */
  updateHyperField() {
    if (typeof HyperField === 'undefined') return;
    if (!this._hyperField) {
      this._hyperField = new HyperField({
        maxEmitters: Math.min(MAX_RF_EMITTERS, 256),
        maxBeams:    MAX_EDGE_BEAMS,
        maxClusters: 64,
      });
    }
    this._hyperField.update(this._graph, this._geoCache, this._governor.quality);
    this.updateRFEmitters(this._hyperField.emitters);
    this.updateEdgeBeams(this._hyperField.edgeBeams);
  }

  /** HyperField instance (null until first updateHyperField() call) */
  get hyperfield() { return this._hyperField; }

  setEdgeBeamsVisible(visible) {
    if (this._beamMesh) this._beamMesh.visible = visible;
  }

  /**
   * Render Phantom IX nodes — inward-pulsing ghost convergence attractors.
   * Markers float above terrain (no ground anchor), semi-transparent,
   * purple for probable / magenta for confirmed.
   *
   * @param {Array}  phantoms — phantom objects from /api/infrastructure/phantom-ix
   * @param {Object} [viewer] — Cesium viewer
   * @param {boolean} [injectStrobes=true] — inject PHANTOM strobes
   */
  renderPhantomIX(phantoms, viewer, injectStrobes = true) {
    const v = viewer || this._viewer;
    if (!v || !phantoms || !phantoms.length) return;
    if (!this._phantomEntities) this._phantomEntities = [];

    for (const e of this._phantomEntities) v.entities.remove(e);
    this._phantomEntities = [];

    for (const px of phantoms) {
      // Guard: skip any phantom with non-finite coordinates (avoids geometry worker crash)
      const lat = +px.lat, lon = +px.lon;
      if (!isFinite(lat) || !isFinite(lon)) continue;

      const conf   = isFinite(+px.confidence)   ? +px.confidence   : 0;
      const pull   = isFinite(+px.phantom_pull)  ? +px.phantom_pull : 0;
      const isConf = px.label === 'CONFIRMED_PHANTOM';

      // Float altitude — phantom nodes hover (not grounded)
      const hoverAlt = 120000 + conf * 200000;

      // Influence ring radius — capped at 200 km to avoid geometry worker OOM.
      // Large filled ellipses at 500–680 km radius exhaust the Cesium Float64 packer.
      const radius_m = Math.min(200000, 50000 + pull * 150000);

      const outlineCol = isConf ? 'rgba(255,80,255,0.85)' : 'rgba(160,60,255,0.65)';
      const labelCol   = isConf ? 'rgba(255,100,255,1)'   : 'rgba(180,80,255,0.9)';

      // Use a lightweight point marker instead of a filled ellipse.
      // Points bypass the geometry worker entirely (no ArrayBuffer allocation).
      try {
        const pt = v.entities.add({
          position: Cesium.Cartesian3.fromDegrees(lon, lat, hoverAlt),
          point: {
            pixelSize: isConf ? 14 : 10,
            color: Cesium.Color.fromCssColorString(
              isConf ? 'rgba(255,80,255,0.85)' : 'rgba(160,60,255,0.7)'),
            outlineColor: Cesium.Color.fromCssColorString('rgba(255,255,255,0.4)'),
            outlineWidth: 2,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
            heightReference: Cesium.HeightReference.NONE,
          },
          label: {
            text: `👻 ${(px.type||'PHANTOM').replace(/_/g, ' ')}\n${(conf * 100).toFixed(0)}% · pull ${(pull * 100).toFixed(0)}%`,
            font: '11px monospace',
            fillColor: Cesium.Color.fromCssColorString(labelCol),
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
            pixelOffset: new Cesium.Cartesian2(0, -16),
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        });
        this._phantomEntities.push(pt);
      } catch (err) {
        console.warn('[PhantomIX] point entity failed:', err.message);
      }

      // Outline-only influence ring — much cheaper than a filled ellipse.
      // granularity = 10° → 36 line segments only; no triangle tessellation.
      try {
        const ring = v.entities.add({
          position: Cesium.Cartesian3.fromDegrees(lon, lat, hoverAlt),
          ellipse: {
            semiMajorAxis:  radius_m,
            semiMinorAxis:  radius_m * 0.65,
            height:         hoverAlt,
            fill:           false,   // ← no material → no triangle geometry
            outline:        true,
            outlineColor:   Cesium.Color.fromCssColorString(outlineCol),
            outlineWidth:   isConf ? 2 : 1,
            granularity:    Cesium.Math.toRadians(10),  // 36 pts max
          },
        });
        this._phantomEntities.push(ring);
      } catch (err) {
        console.warn('[PhantomIX] ring entity failed:', err.message);
      }

      if (injectStrobes) {
        this.injectStrobe({
          lat: px.lat, lon: px.lon, alt: hoverAlt,
          energy: 0.8 + conf * 1.2,
          type: STROBE_TYPE.PHANTOM,
          phantomPull:    pull,
          syntheticRatio: px.synthetic_ratio ?? 0.5,
        });
      }
    }
  }

  /**
   * Render Kill Chain correlation arcs — connects Phantom IX to nearby clusters,
   * colour-coded by kill chain type.
   *
   * @param {Array}  killChain — correlation events from /api/infrastructure/phantom-ix
   * @param {Object} [viewer] — Cesium viewer
   */
  renderKillChainGraph(killChain, viewer) {
    const v = viewer || this._viewer;
    if (!v || !killChain || !killChain.length) return;
    if (!this._kcEntities) this._kcEntities = [];

    for (const e of this._kcEntities) v.entities.remove(e);
    this._kcEntities = [];

    const KC_COLORS = {
      FULL_SPECTRUM_COORDINATION: 'rgba(255, 40, 80, 0.90)',
      RF_NETWORK_COUPLING:        'rgba(255, 140, 0, 0.80)',
      UAV_NETWORK_COUPLING:       'rgba(0, 220, 255, 0.75)',
      NETWORK_ONLY:               'rgba(160, 80, 255, 0.65)',
      PARTIAL_CORRELATION:        'rgba(120, 120, 180, 0.50)',
    };

    for (const kc of killChain) {
      // Guard: skip entries with non-finite coordinates
      const pLat = +kc.phantom_lat, pLon = +kc.phantom_lon;
      if (!isFinite(pLat) || !isFinite(pLon)) continue;

      const col   = KC_COLORS[kc.kill_chain_type] || 'rgba(200,200,200,0.5)';
      const score = isFinite(+kc.kill_chain_score) ? +kc.kill_chain_score : 0;
      const phantomAlt = 120000 + score * 200000;

      // Ring around phantom at kill-chain altitude — plain color material avoids
      // PolylineGlowMaterialProperty overhead (glow materials re-compile shaders).
      for (const _nc of (kc.nearby_clusters || [])) {
        try {
          const r  = 0.8 + score * 2.0;
          const pts = [];
          for (let deg = 0; deg <= 360; deg += 20) {   // 18 pts — was 24, lighter
            const rd = deg * Math.PI / 180;
            pts.push(Cesium.Cartesian3.fromDegrees(
              pLon + Math.cos(rd) * r,
              pLat + Math.sin(rd) * r * 0.7,
              phantomAlt
            ));
          }
          const a = v.entities.add({
            polyline: {
              positions:      pts,
              width:          1.5 + score * 2.5,
              material:       Cesium.Color.fromCssColorString(col),
              clampToGround:  false,
            },
          });
          this._kcEntities.push(a);
        } catch (err) {
          console.warn('[KillChain] ring entity failed:', err.message);
        }
        break; // one ring per phantom
      }

      // Label kill chain type at phantom
      try {
        const lbl = v.entities.add({
          position: Cesium.Cartesian3.fromDegrees(pLon, pLat, phantomAlt + 50000),
          label: {
            text: `⚡ ${kc.kill_chain_type.replace(/_/g, ' ')}\nscore: ${(score * 100).toFixed(0)}%`,
            font: '10px monospace',
            fillColor: Cesium.Color.fromCssColorString(col),
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        });
        this._kcEntities.push(lbl);
      } catch (err) {
        console.warn('[KillChain] label entity failed:', err.message);
      }
    }
  }

  /** Clear all Phantom IX entities from globe. */

  /* ═══════════════════════════════════════════════════════════════════════
   * RECON ENTITY PIPELINE
   * Transforms live node_update stream events into persistent, geo-anchored
   * "actor" objects with classification, DNA tracking, and decay.
   *
   * Pipeline:  event → _reconEntityPipeline → _createReconEntity / _updateReconEntity
   *            → Cesium point entity + GPU strobe + _deckReconBuffer
   *
   * Phantom tie-in:
   *   anomaly > 0.7 + no stable geo → injectHeatPoint (PHANTOM type) only
   *   anomaly > 0.7 + geo present   → add phantom heat, still track as relay
   *   stability ≥ 3 + geoConfidence ≥ 0.6 → render Cesium entity (auto-promote)
   * ═══════════════════════════════════════════════════════════════════════ */

  /** Dispatch: create or update recon entity for a node_update event. */
  _reconEntityPipeline(ev) {
    if (!ev) return;
    const id = ev.entity_id || ev.id || ev.src;
    if (!id) return;
    if (this._reconEntities.has(id)) {
      this._updateReconEntity(ev);
    } else {
      this._createReconEntity(ev);
    }
  }

  /**
   * Resolve geo coordinates from an event using multi-fallback chain:
   * 1. Direct lat/lon on event
   * 2. Graph node cache (already geo-resolved)
   * 3. Cluster centroid running average
   */
  _resolveGeo(ev) {
    const id = ev.entity_id || ev.id || ev.src || '';
    const lat = parseFloat(ev.lat ?? ev.src_lat ?? NaN);
    const lon = parseFloat(ev.lon ?? ev.src_lon ?? NaN);
    if (!isNaN(lat) && !isNaN(lon) && (lat !== 0 || lon !== 0)) return { lat, lon, confidence: 0.9 };

    const cached = id && this._graph.nodes.get(id);
    if (cached?.lat && cached?.lon) return { lat: cached.lat, lon: cached.lon, confidence: 0.7 };

    const cid = String(ev.cluster_id || ev.clusterId || '');
    const centroid = cid && this._clusterCentroids.get(cid);
    if (centroid) return { lat: centroid.lat, lon: centroid.lon, confidence: 0.4 };

    return null;
  }

  /** Maintain a running-average centroid per cluster_id for geo fallback. */
  _updateClusterCentroid(ev, geo) {
    const cid = String(ev.cluster_id || ev.clusterId || '');
    if (!cid) return;
    const prev = this._clusterCentroids.get(cid);
    if (!prev) {
      this._clusterCentroids.set(cid, { lat: geo.lat, lon: geo.lon, count: 1 });
    } else {
      const n = prev.count + 1;
      this._clusterCentroids.set(cid, {
        lat:   (prev.lat * prev.count + geo.lat) / n,
        lon:   (prev.lon * prev.count + geo.lon) / n,
        count: n,
      });
    }
  }

  /**
   * Create a new Recon Entity from a node_update event.
   * High-anomaly nodes with no stable geo become Phantom injections only.
   * High-anomaly nodes with geo are tracked as relay class + phantom heat.
   */
  _createReconEntity(ev) {
    const id     = ev.entity_id || ev.id || ev.src;
    const geo    = this._resolveGeo(ev);
    const anomaly = parseFloat(ev.anomaly_score ?? ev.anomaly ?? 0);

    // No geo + high anomaly → phantom heat only (can't anchor)
    if (!geo && anomaly > 0.6) {
      return;
    }
    if (!geo) return;

    // High anomaly with geo → inject phantom heat overlay AND track
    if (anomaly > 0.7) {
      this.injectHeatPoint(geo.lat, geo.lon, Math.min(1.0, anomaly * 1.1), '#a855f7');
    }

    const emClass = this.classifyEmitter({
      degree:   ev.cluster_size      ?? 1,
      incoming: ev.incoming_count    ?? 0,
      anomaly,
      variance: parseFloat(ev.variance ?? 0),
      velocity: parseFloat(ev.velocity ?? 0),
    });

    const entity = {
      id,
      lat:           geo.lat,
      lon:           geo.lon,
      alt:           parseFloat(ev.alt   ?? 1500),
      velocity:      parseFloat(ev.velocity ?? 0),
      type:          emClass,
      lastSeen:      Date.now(),
      rfDNA:         ev.rfDNA ?? ev.rf_dna ?? null,
      stability:     1,
      geoConfidence: geo.confidence,
      anomaly,
    };

    this._reconEntities.set(id, entity);

    // Feed deck.gl buffer (HeatmapLayer when deck is attached)
    this._deckReconBuffer.push({ position: [geo.lon, geo.lat], weight: 1, type: emClass, timestamp: Date.now() });
    if (this._deckReconBuffer.length > 2000) this._deckReconBuffer.splice(0, 400);

    this._updateClusterCentroid(ev, geo);

    // Render Cesium entity immediately for confirmed (high-confidence) geo
    if (geo.confidence >= 0.6) {
      this._renderReconEntity(entity);
    }
  }

  /**
   * Render a Recon Entity as a Cesium point + GPU strobe.
   * Called on creation (if geo is confident) and on auto-promote from phantom.
   */
  _renderReconEntity(entity) {
    if (this._reconCesiumEntities.has(entity.id)) return; // already rendered
    if (!this._viewer) return;

    const ecol = this._emitterClassColor(entity.type);
    const cesColor = ecol
      ? new Cesium.Color(ecol[0], ecol[1], ecol[2], 0.9)
      : Cesium.Color.CYAN.withAlpha(0.85);

    // Map emitter class to strobe type
    const strobeType = entity.type === 'C2'       ? STROBE_TYPE.CONFLICT  :
                       entity.type === 'relay'     ? STROBE_TYPE.PHANTOM   :
                       entity.type === 'mobile'    ? STROBE_TYPE.UAV       :
                                                     STROBE_TYPE.NETWORK;
    this.injectStrobe({ lat: entity.lat, lon: entity.lon, energy: 0.7, type: strobeType, alt: 50000 });

    try {
      const cesEntity = this._viewer.entities.add({
        id:       `recon-${entity.id}`,
        position: Cesium.Cartesian3.fromDegrees(entity.lon, entity.lat, 50000),
        point: {
          pixelSize:    entity.type === 'C2' ? 10 : 6,
          color:        cesColor,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 1,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        label: entity.type !== 'unknown' ? {
          text:       entity.type.toUpperCase(),
          font:       '9px monospace',
          fillColor:  cesColor,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
          pixelOffset: new Cesium.Cartesian2(0, -12),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          scale: 0.8,
        } : undefined,
      });
      this._reconCesiumEntities.set(entity.id, cesEntity);
    } catch (err) {
      console.warn('[Recon] entity render failed:', err.message);
    }
  }

  /**
   * Update an existing Recon Entity on each subsequent node_update event.
   * Detects RF DNA drift → injects amber heat point.
   * Auto-promotes phantom-tier entities to full Cesium render when stable.
   */
  _updateReconEntity(ev) {
    const id = ev.entity_id || ev.id || ev.src;
    const entity = this._reconEntities.get(id);
    if (!entity) return;

    entity.lastSeen = Date.now();
    entity.stability = Math.min(10, entity.stability + 1);

    entity.type = this.classifyEmitter({
      degree:   ev.cluster_size   ?? 1,
      incoming: ev.incoming_count ?? 0,
      anomaly:  parseFloat(ev.anomaly_score ?? ev.anomaly ?? 0),
      variance: parseFloat(ev.variance ?? 0),
      velocity: parseFloat(ev.velocity ?? 0),
    });

    // RF DNA drift detection — inject amber heat on fingerprint change
    const newDNA = ev.rfDNA ?? ev.rf_dna ?? null;
    if (newDNA && newDNA !== entity.rfDNA) {
      this.injectHeatPoint(entity.lat, entity.lon, 0.8, '#f59e0b');  // DRIFTING
      entity.rfDNA = newDNA;
    }

    // Auto-promote: entity was not yet rendered (low initial geo confidence)
    // Promote once stability > 3 and geo confidence is now sufficient
    if (!this._reconCesiumEntities.has(id) && entity.stability >= 3) {
      const geo = this._resolveGeo(ev);
      if (geo && geo.confidence >= 0.6) {
        entity.lat = geo.lat;
        entity.lon = geo.lon;
        entity.geoConfidence = geo.confidence;
        this._renderReconEntity(entity);
      }
    }
  }

  /**
   * Decay loop — called from _stepLifecycles() every 50ms.
   * Entities not updated within 30s are removed (Cesium entity + registry).
   */
  _decayReconEntities() {
    if (!this._reconEntities || !this._reconEntities.size) return;
    const now = Date.now();
    const DECAY_MS = 30_000;
    for (const [id, entity] of this._reconEntities) {
      if (now - entity.lastSeen > DECAY_MS) {
        const cesEnt = this._reconCesiumEntities.get(id);
        if (cesEnt && this._viewer) {
          try { this._viewer.entities.remove(cesEnt); } catch (_) {}
        }
        this._reconCesiumEntities.delete(id);
        this._reconEntities.delete(id);
      }
    }
  }

  /* ═══════════════════════════════════════════════════════════════════════
   * UAV SWARM SIMULATION
   * Spawns synthetic UAV swarms near major cities for sensor coverage
   * testing. Each UAV becomes a Recon Entity with a Three.js drone mesh,
   * RF cone beam, GPU strobe, and per-frame position update.
   *
   * Public API:
   *   simulateUAVSwarm(cityName?, count?, speedKmh?)  → spawn swarm
   *   clearUAVSwarm()                                 → remove all UAVs
   *
   * updateUAVMovement() is called automatically from _renderThreeLayers().
   * ═══════════════════════════════════════════════════════════════════════ */

  /**
   * Spawn a UAV swarm near a named city (random if omitted).
   * @param {string|null} cityName  Name from MAJOR_CITIES (case-insensitive), or null for random.
   * @param {number}      count     Number of UAVs (default 12).
   * @param {number}      speedKmh  Mean airspeed km/h (default 180).
   */
  simulateUAVSwarm(cityName = null, count = 12, speedKmh = 180) {
    let city = cityName
      ? MAJOR_CITIES.find(c => c.name.toLowerCase() === String(cityName).toLowerCase())
      : null;
    if (!city) city = MAJOR_CITIES[Math.floor(Math.random() * MAJOR_CITIES.length)];

    const swarmId  = `swarm-${Date.now()}`;
    const baseAlt  = 1200 + Math.random() * 800;   // 1.2–2 km
    const cosLat   = Math.cos(city.lat * Math.PI / 180);

    console.log(`[Globe] Simulating ${count} UAVs near ${city.name}`);

    for (let i = 0; i < count; i++) {
      const lat = city.lat + (Math.random() - 0.5) * 0.018;
      const lon = city.lon + (Math.random() - 0.5) * 0.018 / Math.max(0.01, cosLat);
      const alt = baseAlt + Math.random() * 400;
      const uavId = `${swarmId}-uav-${i}`;

      // Inject via Recon Entity pipeline — classifyEmitter will see velocity > 0.02 → 'mobile'
      this._reconEntityPipeline({
        entity_id:    uavId,
        lat, lon, alt,
        cluster_size: count,
        anomaly:      0.75 + Math.random() * 0.25,
        velocity:     (speedKmh / 3.6) / 111320,  // km/h → deg/s (approx)
        rfDNA:        `drone-c2-${Math.floor(Math.random() * 1000)}`,
        confidence:   0.92,
      });

      const entity = this._reconEntities.get(uavId);
      if (entity) this._renderUAV(entity, speedKmh);
    }

    // Cluster centroid for cone steering
    this._clusterCentroids.set(swarmId, { lat: city.lat, lon: city.lon, count });

    // Swarm-level UAV strobe burst
    this.injectStrobe({ lat: city.lat, lon: city.lon, energy: 1.8, type: STROBE_TYPE.UAV, alt: baseAlt });

    // Start periodic push to backend (AR clients poll /api/uav/positions)
    this._startUAVStateSync();

    // Fly camera to swarm location (angled top-down tactical view at ~80 km)
    this.flyToCoords(city.lat, city.lon, 80_000, 2.5);
  }

  /** Push live UAV positions to backend every 500 ms so AR clients can poll. */
  _startUAVStateSync() {
    if (this._uavSyncInterval) return;   // already running
    const API = (typeof API_BASE !== 'undefined' ? API_BASE : '') || '';
    const token = (typeof window !== 'undefined' && window.OperatorSession?.sessionToken) || '';
    this._uavSyncInterval = setInterval(() => {
      if (!this._uavMeshes || this._uavMeshes.size === 0) return;
      const uavs = [];
      for (const [id] of this._uavMeshes) {
        const e = this._reconEntities.get(id);
        if (!e) continue;
        const slot  = parseInt(id.split('-uav-').pop() ?? '0', 10) || 0;
        const color = '#' + (UAV_PALETTE[slot % UAV_PALETTE.length]).toString(16).padStart(6, '0');
        uavs.push({ id, lat: e.lat, lon: e.lon, alt: e.alt ?? 1500,
                    color, label: `UAV-${String(slot+1).padStart(2,'0')}`,
                    speedKmh: (e.velocity || 50e-6) * 111320 * 3.6,
                    rfDNA: e.dna || '' });
      }
      const body = JSON.stringify({ uavs });
      const hdrs = { 'Content-Type': 'application/json' };
      if (token) hdrs['Authorization'] = `Bearer ${token}`;
      fetch(`${API}/api/uav/positions`, { method: 'POST', headers: hdrs, body })
        .catch(() => {});  // fire-and-forget
    }, 500);
  }

  /**
   * Called when the Socket.IO stream delivers a `uav_hit` event from the AR skeet system.
   * Plays a kill-strobe at the UAV position and removes it from the globe.
   */
  _handleUAVHit(ev) {
    const { uav_id, lat, lon } = ev;
    if (lat && lon) {
      this.injectStrobe({ lat, lon, energy: 2.5, type: STROBE_TYPE.ANOMALY, alt: 1500 });
      this.injectHeatPoint(lat, lon, 0.9, 'red');
    }
    // Remove from Three.js + Cesium
    const meshes = this._uavMeshes.get(uav_id);
    if (meshes) {
      try { this._scene.remove(meshes.droneMesh); } catch (_) {}
      try { this._scene.remove(meshes.coneMesh);  } catch (_) {}
      this._uavMeshes.delete(uav_id);
    }
    const labelEnt = this._reconCesiumEntities.get(`label-${uav_id}`);
    if (labelEnt && this._viewer) try { this._viewer.entities.remove(labelEnt); } catch (_) {}
    this._reconCesiumEntities.delete(`label-${uav_id}`);
    const cesEnt = this._reconCesiumEntities.get(uav_id);
    if (cesEnt && this._viewer) try { this._viewer.entities.remove(cesEnt); } catch (_) {}
    this._reconCesiumEntities.delete(uav_id);
    this._reconEntities.delete(uav_id);
    console.log(`[Globe] UAV-HIT: ${uav_id} destroyed at ${lat?.toFixed(4)},${lon?.toFixed(4)}`);
  }

  /**
   * Render a single UAV as a quadcopter silhouette + subtle RF cone + Cesium HUD label.
   * Each drone gets a unique colour from UAV_PALETTE so the swarm is individually readable.
   */
  _renderUAV(entity, speedKmh = 180) {
    if (this._uavMeshes.has(entity.id)) return;

    // Per-drone colour from palette — parse slot index from id tail ("…-uav-7")
    const slot  = parseInt(entity.id.split('-uav-').pop() ?? '0', 10) || 0;
    const color = UAV_PALETTE[slot % UAV_PALETTE.length];
    const hex   = '#' + color.toString(16).padStart(6, '0');

    // ── Quadcopter shape ─────────────────────────────────────────────────
    // Body: two crossed arms (+ shape), 4 rotor rings at each arm tip.
    // Built as a THREE.Group so we can compose Shape fill + LineLoop rotors.
    const ARML = 0.9, ARMW = 0.22, ROT_R = 0.30;
    const bodyMat  = new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, depthTest: false });

    const hArm = new THREE.Shape();
    hArm.moveTo(-ARML, -ARMW);  hArm.lineTo(ARML, -ARMW);
    hArm.lineTo(ARML,  ARMW);   hArm.lineTo(-ARML, ARMW);  hArm.closePath();

    const vArm = new THREE.Shape();
    vArm.moveTo(-ARMW, -ARML);  vArm.lineTo(ARMW, -ARML);
    vArm.lineTo(ARMW,   ARML);  vArm.lineTo(-ARMW, ARML);  vArm.closePath();

    const group = new THREE.Group();
    group.add(new THREE.Mesh(new THREE.ShapeGeometry(hArm), bodyMat));
    group.add(new THREE.Mesh(new THREE.ShapeGeometry(vArm), bodyMat.clone()));

    // Centre hub (bright square for a pin-point visibility at distance)
    const hub = new THREE.Mesh(
      new THREE.PlaneGeometry(0.3, 0.3),
      new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false })
    );
    group.add(hub);

    // Rotor rings — white LineLoop circles at each arm tip
    const rotorLineMat = new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.7, depthTest: false });
    for (let i = 0; i < 4; i++) {
      const a  = i * Math.PI / 2;
      const cx = Math.cos(a) * ARML, cy = Math.sin(a) * ARML;
      const pts = [];
      for (let j = 0; j <= 20; j++) {
        const t = (j / 20) * Math.PI * 2;
        pts.push(new THREE.Vector3(cx + Math.cos(t) * ROT_R, cy + Math.sin(t) * ROT_R, 0));
      }
      group.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), rotorLineMat.clone()));
    }

    group.scale.setScalar(480);   // 480 m — individually visible, won't blob at 80 km altitude
    group.renderOrder = 3;
    group.userData = { entityId: entity.id, velocityKmh: speedKmh };
    this._scene.add(group);

    // ── RF cone (narrow, ground-illuminating C2 downlink) ────────────────
    const coneMat = new THREE.ShaderMaterial({
      uniforms: { uTime: this._uTime },
      vertexShader:   UAV_CONE_VERT,
      fragmentShader: UAV_CONE_FRAG,
      transparent: true,
      depthWrite:  false,
      side:        THREE.DoubleSide,
      blending:    THREE.AdditiveBlending,
    });
    // Smaller cone: 900 m base radius (was 8000), 5 km depth — one drone's footprint only
    const coneMesh = new THREE.Mesh(
      new THREE.CylinderGeometry(900, 40, 5000, 16, 1, true),
      coneMat
    );
    coneMesh.renderOrder = 1;
    this._scene.add(coneMesh);

    // ── Cesium billboard label (RTS HUD) ─────────────────────────────────
    const labelNum = `UAV-${String(slot + 1).padStart(2, '0')}`;
    if (this._viewer) {
      const labelAlt = (entity.alt ?? 1500) + 2500;
      const labelEnt = this._viewer.entities.add({
        id: `uav-label-${entity.id}`,
        position: Cesium.Cartesian3.fromDegrees(entity.lon, entity.lat, labelAlt),
        label: {
          text:                    labelNum,
          font:                    'bold 11px monospace',
          fillColor:               Cesium.Color.fromCssColorString(hex),
          outlineColor:            Cesium.Color.BLACK,
          outlineWidth:            2,
          style:                   Cesium.LabelStyle.FILL_AND_OUTLINE,
          pixelOffset:             new Cesium.Cartesian2(14, 0),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          translucencyByDistance:  new Cesium.NearFarScalar(50000, 1.0, 1500000, 0.0),
        },
        point: {
          pixelSize:               4,
          color:                   Cesium.Color.fromCssColorString(hex),
          outlineColor:            Cesium.Color.BLACK,
          outlineWidth:            1,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });
      this._reconCesiumEntities.set(`label-${entity.id}`, labelEnt);
    }

    this._uavMeshes.set(entity.id, { droneMesh: group, coneMesh });

    // Velocity (random heading, speed in lat/lon degrees-per-second)
    const heading  = Math.random() * Math.PI * 2;
    const speed_ds = (speedKmh / 3.6) / 111320;
    entity.velocityVec = { dlat: Math.cos(heading) * speed_ds, dlon: Math.sin(heading) * speed_ds };

    this.injectRfBearing(entity.lat, entity.lon, 180, 22, 0.45);
  }

  /**
   * Per-frame UAV position + orientation update.
   * Called from _renderThreeLayers() every rendered frame.
   */
  updateUAVMovement() {
    if (!this._uavMeshes || this._uavMeshes.size === 0) return;

    const dt = 0.016;
    if (!this._uavFrameCount) this._uavFrameCount = 0;
    this._uavFrameCount++;
    const updateLabels = (this._uavFrameCount % 6 === 0);  // labels every 6 frames

    // Swarm cohesion centroid (cheap running sum)
    let centLat = 0, centLon = 0, centCount = 0;
    for (const [id] of this._uavMeshes) {
      const e = this._reconEntities.get(id);
      if (e) { centLat += e.lat; centLon += e.lon; centCount++; }
    }
    if (centCount > 0) { centLat /= centCount; centLon /= centCount; }

    for (const [id, { droneMesh, coneMesh }] of this._uavMeshes) {
      const entity = this._reconEntities.get(id);
      if (!entity) {
        this._scene.remove(droneMesh);
        this._scene.remove(coneMesh);
        this._uavMeshes.delete(id);
        continue;
      }

      if (!entity.velocityVec) {
        const h = Math.random() * Math.PI * 2;
        const spd = (entity.velocity || 50e-6);
        entity.velocityVec = { dlat: Math.cos(h) * spd, dlon: Math.sin(h) * spd };
      }

      // Occasional heading perturbation (realistic UAV jitter)
      if (Math.random() < 0.008) {
        const turn = (Math.random() - 0.5) * 0.5;
        const c = Math.cos(turn), s = Math.sin(turn);
        const { dlat, dlon } = entity.velocityVec;
        entity.velocityVec = { dlat: c * dlat - s * dlon, dlon: s * dlat + c * dlon };
      }

      // Mild cohesion — pull toward swarm centroid if spread >3 km
      if (centCount > 1) {
        const dLat = centLat - entity.lat, dLon = centLon - entity.lon;
        const distDeg = Math.sqrt(dLat * dLat + dLon * dLon);
        if (distDeg > 0.03) {  // ~3.3 km threshold
          const pull = 0.15;
          entity.velocityVec.dlat += dLat * pull * entity.velocityVec.dlat;
          entity.velocityVec.dlon += dLon * pull * entity.velocityVec.dlon;
        }
      }

      // Advance lat/lon
      entity.lat = ((entity.lat + entity.velocityVec.dlat * dt) + 270) % 180 - 90;
      entity.lon = ((entity.lon + entity.velocityVec.dlon * dt) + 540) % 360 - 180;
      entity.lastSeen = Date.now();

      // Update Three.js positions
      const pos = Cesium.Cartesian3.fromDegrees(entity.lon, entity.lat, entity.alt ?? 1500);
      droneMesh.position.set(pos.x, pos.y, pos.z);

      // Orient drone nose along velocity direction (up = away from Earth)
      const earthUp = new THREE.Vector3(pos.x, pos.y, pos.z).normalize();
      const velDir  = new THREE.Vector3(entity.velocityVec.dlon, 0, entity.velocityVec.dlat).normalize();
      if (velDir.lengthSq() > 0.0001) {
        const right = new THREE.Vector3().crossVectors(velDir, earthUp).normalize();
        const fwd   = new THREE.Vector3().crossVectors(earthUp, right);
        droneMesh.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(right, earthUp, fwd));
      }

      // RF cone: below UAV, pointing toward Earth
      coneMesh.position.set(pos.x, pos.y, pos.z);
      const coneDir = new THREE.Vector3(-pos.x, -pos.y, -pos.z).normalize();
      coneMesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), coneDir);

      // Cesium label follows drone (every 6 frames to save CPU)
      if (updateLabels) {
        const labelEnt = this._reconCesiumEntities.get(`label-${id}`);
        if (labelEnt && labelEnt.position) {
          labelEnt.position = new Cesium.ConstantPositionProperty(
            Cesium.Cartesian3.fromDegrees(entity.lon, entity.lat, (entity.alt ?? 1500) + 2500)
          );
        }
      }
    }
  }

  /**
   * Remove all active UAV meshes, Cesium labels, and registry entries.
   */
  clearUAVSwarm() {
    for (const [id, { droneMesh, coneMesh }] of this._uavMeshes) {
      try { this._scene.remove(droneMesh); } catch (_) {}
      try { this._scene.remove(coneMesh);  } catch (_) {}
      // Recon entity Cesium point
      const cesEnt = this._reconCesiumEntities.get(id);
      if (cesEnt && this._viewer) try { this._viewer.entities.remove(cesEnt); } catch (_) {}
      this._reconCesiumEntities.delete(id);
      // HUD label
      const labelEnt = this._reconCesiumEntities.get(`label-${id}`);
      if (labelEnt && this._viewer) try { this._viewer.entities.remove(labelEnt); } catch (_) {}
      this._reconCesiumEntities.delete(`label-${id}`);
      this._reconEntities.delete(id);
    }
    this._uavMeshes.clear();
    this._uavFrameCount = 0;
    if (this._uavSyncInterval) { clearInterval(this._uavSyncInterval); this._uavSyncInterval = null; }
    console.log('[Globe] UAV swarm cleared');
  }


  clearPhantomIX(viewer) {
    const v = viewer || this._viewer;
    if (!v) return;
    for (const e of (this._phantomEntities || [])) v.entities.remove(e);
    this._phantomEntities = [];
    for (const e of (this._kcEntities || [])) v.entities.remove(e);
    this._kcEntities = [];
  }
}

/* ─── Module export ─────────────────────────────────────────────────────── */
if (typeof module !== 'undefined') module.exports = { CesiumHypergraphGlobe, STROBE_TYPE };
else {
  window.CesiumHypergraphGlobe = CesiumHypergraphGlobe;
  window.STROBE_TYPE = STROBE_TYPE;
}
