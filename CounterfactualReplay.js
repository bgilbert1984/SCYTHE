/**
 * CounterfactualReplay.js — Branch simulation over the causal event log
 *
 * Answers: "What if this event never happened?" via deterministic re-derivation.
 */

class CounterfactualReplay {
  /**
   * @param {ScytheCognitionRuntime} runtime
   */
  constructor(runtime) {
    this.runtime = runtime;
    this._branchHistory = [];
  }

  /**
   * Apply event mutations from a fork point and measure divergence.
   *
   * @param {Object} opts
   * @param {number} opts.from - simTime ms (inclusive fork window start)
   * @param {number} [opts.to] - simTime ms (inclusive fork window end)
   * @param {function} opts.mutate - (event, ctx) => event | null (null = remove)
   * @param {string} [opts.label]
   * @param {string} [opts.lesion_family] - event|temporal|identity|field|protocol|resonance|narrative
   * @param {boolean} [opts.reconstructToEnd] - rebuild isolated cognition state
   */
  branchReplay(opts = {}) {
    const from = opts.from ?? 0;
    const to = opts.to ?? Infinity;
    const mutate = opts.mutate ?? ((e) => e);
    const label = opts.label ?? `branch_${Date.now()}`;
    const lesion_family = opts.lesion_family ?? 'event';

    const exported = this.runtime.exportCausalityForReplay();
    const sourceEvents = (exported.events || []).map((e) => ({ ...e }));

    const branched = [];
    const mutations = { removed: 0, modified: 0, kept: 0 };

    for (const raw of sourceEvents) {
      if (raw.ts < from) {
        branched.push(raw);
        mutations.kept++;
        continue;
      }
      if (raw.ts > to) {
        branched.push(raw);
        mutations.kept++;
        continue;
      }

      const ctx = this._buildLesionContext(raw);
      const out = mutate.length >= 2 ? mutate(raw, ctx) : mutate(raw);
      if (!out) {
        mutations.removed++;
        continue;
      }
      if (out !== raw && JSON.stringify(out) !== JSON.stringify(raw)) {
        mutations.modified++;
      } else {
        mutations.kept++;
      }
      branched.push(out);
    }

    branched.sort((a, b) => a.ts - b.ts);

    const baselineState = this._simulateCognitionState(sourceEvents);
    const branchState = opts.reconstructToEnd !== false
      ? this._simulateCognitionState(branched)
      : null;

    const lesionEvents = sourceEvents.filter((e) => {
      if (e.ts < from || e.ts > to) return false;
      return !branched.some(
        (b) => b.event_id === e.event_id || (
          b.ts === e.ts && b.type === e.type && b.entity_id === e.entity_id
        )
      );
    });

    const divergence = branchState
      ? this._measureDivergence(baselineState, branchState, sourceEvents, branched)
      : { note: 'reconstruction disabled' };

    if (branchState && typeof CausalShockwave !== 'undefined') {
      divergence.shockwave_topology = CausalShockwave.map(
        baselineState,
        branchState,
        sourceEvents,
        branched,
        exported,
        lesionEvents
      );
    }

    const result = {
      label,
      from,
      to,
      lesion_family,
      mutations,
      sourceEventCount: sourceEvents.length,
      branchedEventCount: branched.length,
      divergence,
      branchedEvents: branched,
    };

    this._branchHistory.push({
      label,
      at: Date.now(),
      divergence_score: divergence.overall_score ?? 0,
    });
    if (this._branchHistory.length > 50) this._branchHistory.shift();

    return result;
  }

  /**
   * Isolated cognition rebuild (does not mutate live runtime).
   */
  _simulateCognitionState(events) {
    const graph = new HostCognitionGraph();
    const identities = new Map();
    const hostIds = new Set();

    for (const raw of events) {
      const hostId = this._hostIdFromEvent(raw);
      if (!hostId) continue;

      hostIds.add(hostId);
      if (!identities.has(hostId)) {
        identities.set(
          hostId,
          new HostIdentity(hostId, { first_seen_simTime: raw.ts })
        );
      }
      const identity = identities.get(hostId);
      identity.mutateFromEvidence({ simTime: raw.ts, event: raw });

      graph.recordHostEvent(hostId, identity, raw, raw.ts, {
        pressure: raw.value?.curr_rx_mbps ?? raw.value?.delta_ratio ?? 0,
        volatility: raw.type === 'ROLE_CHANGE' ? 0.2 : 0,
      });
    }

    return {
      host_count: hostIds.size,
      identities: Array.from(identities.entries()).map(([id, ident]) => ({
        host_id: id,
        snapshot: ident.toJSON(),
      })),
      graph: graph.exportGraph(),
    };
  }

  _buildLesionContext(event) {
    const hostId = this._hostIdFromEvent(event);
    const runtime = this.runtime;
    const identity = runtime?._identityByHost?.get?.(hostId);
    const narratives = runtime?.getOperationalNarratives?.() ?? [];
    const narrative = narratives.find((n) => n.entityId === hostId)?.narrative ?? null;
    const gravity = runtime?.semanticGravity?.attraction?.(hostId, { ambiguous: true }) ?? 0;
    return {
      host_id: hostId,
      identity: identity?.toJSON?.() ?? identity,
      narrative,
      gravity_pull: gravity,
      simTime: runtime?.clock?.simTime ?? 0,
    };
  }

  _hostIdFromEvent(event) {
    if (event.value?.host_id) return event.value.host_id;
    const entity = event.entity_id || '';
    if (entity.includes('/')) return entity.split('/')[0];
    const inst = typeof window !== 'undefined'
      ? (window.SCYTHE_INSTANCE_ID || 'local')
      : 'local';
    return `host-${inst}`;
  }

  _measureDivergence(baseline, branch, sourceEvents, branchedEvents) {
    const baseMap = new Map(baseline.identities.map((i) => [i.host_id, i.snapshot]));
    const branchMap = new Map(branch.identities.map((i) => [i.host_id, i.snapshot]));

    const identityDrift = [];
    let driftSum = 0;

    for (const [hostId, baseSnap] of baseMap) {
      const br = branchMap.get(hostId);
      if (!br) {
        identityDrift.push({ host_id: hostId, type: 'absent_in_branch' });
        driftSum += 1;
        continue;
      }
      const drift =
        Math.abs((br.mutation_count ?? 0) - (baseSnap.mutation_count ?? 0)) * 0.05 +
        Math.abs((br.vpn_affinity ?? 0) - (baseSnap.vpn_affinity ?? 0)) * 0.3 +
        Math.abs((br.stability ?? 0) - (baseSnap.stability ?? 0)) * 0.2;
      if (drift > 0.05) {
        identityDrift.push({ host_id: hostId, type: 'identity_drift', drift });
        driftSum += drift;
      }
    }

    for (const hostId of branchMap.keys()) {
      if (!baseMap.has(hostId)) {
        identityDrift.push({ host_id: hostId, type: 'new_in_branch' });
        driftSum += 0.5;
      }
    }

    const typeHist = (list) => {
      const h = {};
      for (const e of list) h[e.type] = (h[e.type] || 0) + 1;
      return h;
    };
    const baseHist = typeHist(sourceEvents);
    const branchHist = typeHist(branchedEvents);
    const eventTypeDelta = {};
    const allTypes = new Set([...Object.keys(baseHist), ...Object.keys(branchHist)]);
    for (const t of allTypes) {
      const d = (branchHist[t] || 0) - (baseHist[t] || 0);
      if (d !== 0) eventTypeDelta[t] = d;
    }

    const edgeDelta =
      (branch.graph?.cross_host_edges?.length ?? 0) -
      (baseline.graph?.cross_host_edges?.length ?? 0);

    const overall = Math.min(
      1,
      driftSum / Math.max(1, baseMap.size) +
        Math.abs(sourceEvents.length - branchedEvents.length) / Math.max(1, sourceEvents.length) * 0.3 +
        Math.abs(edgeDelta) * 0.05
    );

    return {
      overall_score: overall,
      identity_drift: identityDrift,
      event_type_delta: eventTypeDelta,
      cross_host_edge_delta: edgeDelta,
      host_count_delta: branch.host_count - baseline.host_count,
      sensitive_events: this._rankEventSensitivity(sourceEvents, branchedEvents),
    };
  }

  /**
   * Events whose removal/modification caused largest identity impact (greedy).
   */
  _rankEventSensitivity(source, branched) {
    const branchIds = new Set(branched.map((e) => e.event_id));
    return source
      .filter((e) => !branchIds.has(e.event_id))
      .slice(0, 10)
      .map((e) => ({ event_id: e.event_id, type: e.type, ts: e.ts, entity_id: e.entity_id }));
  }

  getBranchHistory() {
    return [...this._branchHistory];
  }
}

if (typeof window !== 'undefined') {
  window.CounterfactualReplay = CounterfactualReplay;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { CounterfactualReplay };
}
