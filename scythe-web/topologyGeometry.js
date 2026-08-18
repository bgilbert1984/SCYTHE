import {graphNodeScale} from "./graphVisualScale.js";

function hash(value) {
  let result = 2166136261;
  for (const char of String(value)) { result ^= char.charCodeAt(0); result = Math.imul(result, 16777619); }
  return result >>> 0;
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

/** Deterministically relax a bounded 2D layout until node bodies have clear air. */
export function separatePlanarNodes(nodes, initial, width, height, {
  padding = 8, arrowRunway = 22, iterations = 64,
} = {}) {
  const positions = new Map([...initial].map(([id, point]) => [id, {...point}]));
  const radii = new Map(nodes.map((node) => [node.id, graphNodeScale(node).topologyRadius]));
  const ordered = nodes.filter((node) => positions.has(node.id));
  for (let pass = 0; pass < iterations; pass += 1) {
    let moved = false;
    for (let i = 0; i < ordered.length; i += 1) for (let j = i + 1; j < ordered.length; j += 1) {
      const a = positions.get(ordered[i].id); const b = positions.get(ordered[j].id);
      let dx = b.x - a.x; let dy = b.y - a.y; let distance = Math.hypot(dx, dy);
      const minimum = radii.get(ordered[i].id) + radii.get(ordered[j].id) + padding + arrowRunway;
      if (distance >= minimum) continue;
      if (distance < .001) {
        const angle = (hash(`${ordered[i].id}:${ordered[j].id}`) % 360) * Math.PI / 180;
        dx = Math.cos(angle); dy = Math.sin(angle); distance = 1;
      }
      const shift = (minimum - distance) * .52; const ux = dx / distance; const uy = dy / distance;
      a.x -= ux * shift; a.y -= uy * shift; b.x += ux * shift; b.y += uy * shift; moved = true;
    }
    for (const node of ordered) {
      const point = positions.get(node.id); const radius = radii.get(node.id) + 5;
      point.x = clamp(point.x, radius, Math.max(radius, width - radius));
      point.y = clamp(point.y, radius, Math.max(radius, height - radius));
    }
    if (!moved) break;
  }
  return positions;
}

/** Trim a line away from node bodies and require clear runway around its arrow. */
export function topologyEdgeGeometry(source, target, sourceRadius, targetRadius, arrowPixels) {
  const dx = target.x - source.x; const dy = target.y - source.y;
  const centerDistance = Math.hypot(dx, dy) || 1; const ux = dx / centerDistance; const uy = dy / centerDistance;
  const startInset = Math.max(0, Number(sourceRadius) || 0) + 4;
  const endInset = Math.max(0, Number(targetRadius) || 0) + 4;
  const start = {x: source.x + ux * startInset, y: source.y + uy * startInset};
  const end = {x: target.x - ux * endInset, y: target.y - uy * endInset};
  const visibleLength = Math.max(0, centerDistance - startInset - endInset);
  const requestedArrow = Math.max(0, Number(arrowPixels) || 0);
  const arrowLength = Math.min(requestedArrow, Math.max(0, visibleLength - 16));
  const fraction = .5; const arrow = {x: start.x + (end.x - start.x) * fraction,
    y: start.y + (end.y - start.y) * fraction};
  const before = visibleLength * fraction - arrowLength / 2;
  const after = visibleLength * (1 - fraction) - arrowLength / 2;
  return {start, end, arrow, ux, uy, centerDistance, visibleLength, arrowLength,
    arrowVisible: arrowLength >= 4 && before >= 8 && after >= 8,
    minimumArrowRunway: 8};
}

/** Place only a new 3D point; retained nodes remain stable across revisions. */
export function separateNewSpatialPoint(id, candidate, occupied, minimumDistance = 32) {
  const point = {...candidate}; const entries = [...occupied.entries()];
  for (let pass = 0; pass < 48; pass += 1) {
    let nearest = null; let distance = Infinity;
    for (const [otherId, other] of entries) {
      const dx = point.x - other.x; const dy = point.y - other.y; const dz = point.z - other.z;
      const current = Math.hypot(dx, dy, dz);
      if (current < distance) nearest = {otherId, other, dx, dy, dz}, distance = current;
    }
    if (!nearest || distance >= minimumDistance) break;
    let {dx, dy, dz} = nearest;
    if (distance < .001) {
      const a = hash(`${id}:${nearest.otherId}`); dx = ((a & 255) / 127.5) - 1;
      dy = (((a >>> 8) & 255) / 127.5) - 1; dz = (((a >>> 16) & 255) / 127.5) - 1;
      distance = Math.hypot(dx, dy, dz) || 1;
    }
    const shift = minimumDistance - distance + .5;
    point.x += dx / distance * shift; point.y += dy / distance * shift; point.z += dz / distance * shift;
  }
  return point;
}
