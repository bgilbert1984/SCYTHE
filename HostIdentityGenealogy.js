/**
 * HostIdentityGenealogy.js — Identity merge/split lineage with confidence decay
 *
 * Prevents identity explosion (one actor → many) and collapse (many → one).
 */

function _jaccard(a, b) {
  if (!a?.length || !b?.length) return 0;
  const sa = new Set(a);
  const sb = new Set(b);
  let inter = 0;
  for (const x of sa) if (sb.has(x)) inter++;
  const union = sa.size + sb.size - inter;
  return union ? inter / union : 0;
}

class HostIdentityGenealogy {
  constructor(opts = {}) {
    this.mergeThreshold = opts.mergeThreshold ?? 0.72;
    this.forkThreshold = opts.forkThreshold ?? 0.35;
    this.decayHalfLifeMs = opts.decayHalfLifeMs ?? 600_000;
    this.maxCandidates = opts.maxCandidates ?? 200;

    /** @type {Map<string, Object>} */
    this.lineage = new Map();
    this.mergeCandidates = [];
    this.canonical = new Map();
  }

  _node(hostId) {
    if (!this.lineage.has(hostId)) {
      this.lineage.set(hostId, {
        host_id: hostId,
        parent_id: null,
        children: [],
        forks: [],
        merged_from: [],
        merged_into: null,
        confidence: 1,
        last_seen_simTime: 0,
        created_simTime: 0,
      });
    }
    return this.lineage.get(hostId);
  }

  canonicalId(hostId) {
    let id = hostId;
    let guard = 0;
    while (this.canonical.has(id) && guard++ < 32) {
      id = this.canonical.get(id);
    }
    return id;
  }

  register(hostId, identity, simTime) {
    const canon = this.canonicalId(hostId);
    const node = this._node(canon);
    node.last_seen_simTime = simTime;
    if (!node.created_simTime) node.created_simTime = simTime;
    node.identity_ref = identity;
    return canon;
  }

  /**
   * Score pairwise merge affinity from soft biometric overlap.
   */
  scoreMerge(idA, idB, identityA, identityB) {
    const transport = _jaccard(identityA?.transport_signature, identityB?.transport_signature);
    const protocol = _jaccard(identityA?.protocol_dna, identityB?.protocol_dna);
    const mac = _jaccard(identityA?.mac_lineage, identityB?.mac_lineage);
    const timing = 1 - Math.min(
      1,
      Math.abs((identityA?.timing_fingerprint?.rx_mean ?? 0) -
        (identityB?.timing_fingerprint?.rx_mean ?? 0)) / 100
    );
    const entropy = 1 - Math.min(
      1,
      Math.abs((identityA?.entropy_baseline ?? 0) - (identityB?.entropy_baseline ?? 0))
    );
    const vpn = 1 - Math.abs((identityA?.vpn_affinity ?? 0) - (identityB?.vpn_affinity ?? 0));

    return Math.min(1, transport * 0.25 + protocol * 0.2 + mac * 0.2 + timing * 0.15 + entropy * 0.1 + vpn * 0.1);
  }

  proposeMerges(hostEntries) {
    this.mergeCandidates = [];
    for (let i = 0; i < hostEntries.length; i++) {
      for (let j = i + 1; j < hostEntries.length; j++) {
        const [a, b] = [hostEntries[i], hostEntries[j]];
        const score = this.scoreMerge(a.host_id, b.host_id, a.identity, b.identity);
        if (score >= this.mergeThreshold) {
          this.mergeCandidates.push({
            host_a: a.host_id,
            host_b: b.host_id,
            score,
            reason: 'soft_biometric_overlap',
          });
        }
      }
    }
    this.mergeCandidates.sort((x, y) => y.score - x.score);
    if (this.mergeCandidates.length > this.maxCandidates) {
      this.mergeCandidates = this.mergeCandidates.slice(0, this.maxCandidates);
    }
    return this.mergeCandidates;
  }

  /**
   * Merge b into a (a becomes canonical).
   */
  merge(hostA, hostB, confidence = 0.8) {
    const canonA = this.canonicalId(hostA);
    const canonB = this.canonicalId(hostB);
    if (canonA === canonB) return canonA;

    const nodeA = this._node(canonA);
    const nodeB = this._node(canonB);

    nodeA.merged_from.push(canonB);
    nodeA.confidence = Math.min(1, nodeA.confidence * 0.5 + confidence * 0.5);
    nodeB.merged_into = canonA;
    nodeB.confidence *= 0.3;

    this.canonical.set(canonB, canonA);
    return canonA;
  }

  /**
   * Fork lineage when behavioral divergence exceeds threshold.
   */
  fork(parentId, newHostId, reason, simTime) {
    const parent = this._node(this.canonicalId(parentId));
    const child = this._node(newHostId);
    child.parent_id = parent.host_id;
    child.created_simTime = simTime;
    parent.children.push(newHostId);
    parent.forks.push({ child_id: newHostId, reason, simTime });
    return newHostId;
  }

  decay(simTime) {
    for (const node of this.lineage.values()) {
      if (!node.last_seen_simTime) continue;
      const age = simTime - node.last_seen_simTime;
      if (age <= 0) continue;
      const decay = Math.pow(0.5, age / this.decayHalfLifeMs);
      node.confidence = Math.max(0.05, node.confidence * decay);
    }
  }

  exportGenealogy() {
    return {
      nodes: Array.from(this.lineage.values()),
      merge_candidates: [...this.mergeCandidates],
      canonical_map: Object.fromEntries(this.canonical),
    };
  }
}

if (typeof window !== 'undefined') {
  window.HostIdentityGenealogy = HostIdentityGenealogy;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { HostIdentityGenealogy };
}
