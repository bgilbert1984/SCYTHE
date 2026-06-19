/**
 * HyperField — converts a live hypergraph snapshot into a field emitter dataset.
 *
 * Transforms:
 *   Node  → spherical wave emitter (intensity ∝ log(degree+1), anomaly amplified)
 *   Edge  → directional beam emitter (aligned to src→dst, anomaly-weighted)
 *   Cluster/HE → interference region (standing wave, density ∝ member count)
 *
 * Temporal memory: EMA on emitter intensities — anomalies linger, cold nodes decay.
 *
 * Usage (browser global):
 *   const hf = new HyperField({ maxEmitters: 256, maxBeams: 128, maxClusters: 64 });
 *   hf.update(globe._graph);          // pass globe._graph directly
 *   globe.updateRFEmitters(hf.emitters);
 *   globe.updateEdgeBeams(hf.edgeBeams);
 */
/* global Cesium */
class HyperField {
  constructor(opts = {}) {
    this._maxEmitters = opts.maxEmitters ?? 256;
    this._maxBeams    = opts.maxBeams    ?? 128;
    this._maxClusters = opts.maxClusters ?? 64;

    // EMA state maps: nodeId → smoothed intensity
    this._nodeEMA     = new Map();
    this._edgeEMA     = new Map();

    // Output arrays (rebuilt on update())
    this._emitters = [];   // [{pos:{x,y,z}, intensity, anomaly, radius, phase}]
    this._beams    = [];   // [{src:{x,y,z}, dst:{x,y,z}, anomaly, intensity}]
    this._clusters = [];   // [{center:{x,y,z}, density, anomaly}]

    this._frameCount = 0;
  }

  // ── Main entry point ──────────────────────────────────────────────────────

  /**
   * Update from a graph snapshot. Pass globe._graph directly.
   * @param {{ nodes: Map, edges: Map, hyperedges: Map }} graph
   * @param {Map}  geoCache  — globe._geoCache (entityId → Cesium.Cartesian3)
   * @param {number} quality — 0.25–1.5 from FrameBudgetGovernor
   */
  update(graph, geoCache, quality = 1.0) {
    this._frameCount++;
    const emaAlpha = 0.15;   // EMA smoothing: slow rise, slow decay
    const decayRate = 0.93;  // cold nodes decay each frame

    // ── 1. Age all existing EMAs (decay cold entries) ──
    for (const [k, v] of this._nodeEMA) {
      this._nodeEMA.set(k, v * decayRate);
      if (this._nodeEMA.get(k) < 0.005) this._nodeEMA.delete(k);
    }
    for (const [k, v] of this._edgeEMA) {
      this._edgeEMA.set(k, v * decayRate);
      if (this._edgeEMA.get(k) < 0.005) this._edgeEMA.delete(k);
    }

    // ── 2. Build emitters from nodes ──
    const rawEmitters = [];
    const nodes = graph.nodes || new Map();
    const edges = graph.edges || new Map();
    const hyperedges = graph.hyperedges || new Map();

    for (const [id, n] of nodes) {
      const pos = geoCache?.get(id);
      if (!pos) continue;

      // Degree = number of edges involving this node
      let degree = 0;
      for (const [, e] of edges) {
        if (e.src === id || e.dst === id) degree++;
      }

      const anomaly = parseFloat(n.anomaly_score ?? n.anomaly ?? 0);
      const rawIntensity = Math.min(1.0, Math.log(degree + 1) / Math.log(10) + anomaly * 0.4);

      // EMA update
      const prev = this._nodeEMA.get(id) ?? rawIntensity;
      const smoothed = prev * (1 - emaAlpha) + rawIntensity * emaAlpha;
      this._nodeEMA.set(id, smoothed);

      rawEmitters.push({
        pos:       { x: pos.x, y: pos.y, z: pos.z },
        intensity: smoothed,
        anomaly,
        radius:    Math.max(200000, 400000 + degree * 30000), // scale with connectivity
        phase:     this._hash(id) % (Math.PI * 2),
      });
    }

    // Sort by intensity desc, cap at maxEmitters (quality-scaled)
    const emitterCap = Math.round(this._maxEmitters * Math.min(1.0, quality));
    rawEmitters.sort((a, b) => b.intensity - a.intensity);
    this._emitters = rawEmitters.slice(0, emitterCap);

    // ── 3. Build edge beams ──
    const rawBeams = [];
    const beamCap = Math.round(this._maxBeams * Math.min(1.0, quality));

    for (const [id, e] of edges) {
      const srcPos = geoCache?.get(e.src);
      const dstPos = geoCache?.get(e.dst);
      if (!srcPos || !dstPos) continue;

      const anomaly = parseFloat(e.anomaly_score ?? e.anomaly ?? 0);
      const conf = parseFloat(e.confidence ?? e.conf ?? 0.5);
      const rawIntensity = conf * (1 + anomaly * 1.5);

      const prev = this._edgeEMA.get(id) ?? rawIntensity;
      const smoothed = prev * (1 - emaAlpha) + rawIntensity * emaAlpha;
      this._edgeEMA.set(id, smoothed);

      rawBeams.push({
        src:       { x: srcPos.x, y: srcPos.y, z: srcPos.z },
        dst:       { x: dstPos.x, y: dstPos.y, z: dstPos.z },
        intensity: smoothed,
        anomaly,
      });
    }

    rawBeams.sort((a, b) => (b.anomaly - a.anomaly) || (b.intensity - a.intensity));
    this._beams = rawBeams.slice(0, beamCap);

    // ── 4. Build cluster interference regions from hyperedges ──
    this._clusters = [];
    for (const [, he] of hyperedges) {
      if (this._clusters.length >= this._maxClusters) break;
      const members = (he.members || []).map(mid => geoCache?.get(mid)).filter(Boolean);
      if (members.length < 2) continue;

      // Centroid
      let cx = 0, cy = 0, cz = 0;
      for (const m of members) { cx += m.x; cy += m.y; cz += m.z; }
      cx /= members.length; cy /= members.length; cz /= members.length;

      this._clusters.push({
        center:  { x: cx, y: cy, z: cz },
        density: Math.min(2.0, members.length / 5.0),
        anomaly: parseFloat(he.anomaly ?? 0),
      });
    }
  }

  // ── Accessors ─────────────────────────────────────────────────────────────

  get emitters() { return this._emitters; }
  get edgeBeams() { return this._beams; }
  get clusters()  { return this._clusters; }
  get frameCount() { return this._frameCount; }

  // Simple deterministic hash for phase stagger
  _hash(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) {
      h = (Math.imul(31, h) + str.charCodeAt(i)) | 0;
    }
    return Math.abs(h);
  }
}

// Expose as browser global
if (typeof window !== 'undefined') window.HyperField = HyperField;
if (typeof module !== 'undefined') module.exports = HyperField;
