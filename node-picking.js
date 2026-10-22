/**
 * Screen-space node picking for the globe's instanced nodes.
 *
 * The node mesh is a plain THREE.Mesh over instance attributes, so THREE.Raycaster cannot return an instance id for
 * it. This projects each node exactly as NODE_VERT places it (ECEF position lifted by log(cluster) along the surface
 * normal, then projection * view) and picks the nearest node whose drawn disk contains the pointer. A node on the
 * far side of the Earth from the camera is not pickable (the globe hides it).
 * Plain data in, index out: no THREE or Cesium needed, so it is unit tested on its own.
 */
(function (root) {
  const EARTH_MIN_RADIUS = 6356752;   // metres, WGS84 polar radius
  const smoothstep = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

  /**
   * @param {object} q
   * @param {number} q.px,q.py       pointer, in CSS pixels from the canvas top-left
   * @param {number} q.width,q.height canvas size in CSS pixels
   * @param {ArrayLike<number>} q.viewProj  projection * view, column-major 4x4 (the matrix NODE_VERT applies)
   * @param {object} q.nodes         {position (xyz per node), conf, lifecycle, cluster, visible (optional)}
   * @param {number} q.count         how many nodes are live
   * @param {number} q.heightScale   metres per log-unit of cluster size (RECON_HEIGHT_SCALE)
   * @param {ArrayLike<number>} [q.cameraPosition] camera ECEF xyz; enables the far-side-of-the-Earth test
   * @param {number} [q.baseRadiusPx=14] drawn radius at scale 1 (the shader's 14.0)
   * @param {number} [q.minRadiusPx=10]  smallest hit radius, so a tiny node can still be clicked
   * @returns {number} the node index, or -1
   */
  function pickNode(q) {
    const m = q.viewProj, n = q.nodes;
    const base = q.baseRadiusPx ?? 14, floor = q.minRadiusPx ?? 10;
    let best = -1, bestDist = Infinity, bestDepth = Infinity;
    for (let i = 0; i < q.count; i++) {
      if (n.visible && n.visible[i] < 0.5) continue;                       // hidden by the mission timeline
      const emerge = smoothstep(0, 0.85, n.lifecycle[i]);
      if (emerge < 0.05) continue;                                         // not drawn yet / removed
      let x = n.position[i * 3], y = n.position[i * 3 + 1], z = n.position[i * 3 + 2];
      const len = Math.hypot(x, y, z);
      if (len > 0) {
        const lift = Math.log(Math.max(n.cluster ? n.cluster[i] : 1, 1)) * q.heightScale / len;
        x += x * lift; y += y * lift; z += z * lift;
      }
      const w = m[3] * x + m[7] * y + m[11] * z + m[15];
      if (w < 0.001) continue;                                             // behind the camera
      const cx = (m[0] * x + m[4] * y + m[8] * z + m[12]) / w;
      const cy = (m[1] * x + m[5] * y + m[9] * z + m[13]) / w;
      const depth = (m[2] * x + m[6] * y + m[10] * z + m[14]) / w;
      // Beyond the horizon: a point p is visible from camera c only if p . c >= R^2 (R = the smaller Earth radius, so
      // nodes on the limb stay pickable).
      if (q.cameraPosition && x * q.cameraPosition[0] + y * q.cameraPosition[1] + z * q.cameraPosition[2] < EARTH_MIN_RADIUS * EARTH_MIN_RADIUS) continue;
      const scale = Math.min((0.3 + 0.7 * Math.pow(n.conf[i], 0.4)) * emerge, 1);
      const radius = Math.max(base * scale, floor);
      const dx = (cx * 0.5 + 0.5) * q.width - q.px;
      const dy = (1 - (cy * 0.5 + 0.5)) * q.height - q.py;
      const dist = Math.hypot(dx, dy);
      if (dist > radius) continue;
      if (dist < bestDist - 0.5 || (Math.abs(dist - bestDist) <= 0.5 && depth < bestDepth)) {
        best = i; bestDist = dist; bestDepth = depth;
      }
    }
    return best;
  }

  const api = {pickNode};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.ScytheNodePicking = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
