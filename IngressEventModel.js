/**
 * IngressEventModel.js — Event & Causality Foundations (Layer 1)
 *
 * Defines immutable event records, causal relationships, and event inference.
 * Events become the primary source of truth; all topology changes flow through
 * the event stream for deterministic, explainable state transitions.
 *
 * Event Types:
 *   - INGRESS_SPIKE: rx_mbps or interface activity increases significantly
 *   - INTERFACE_DOWN: interface becomes inactive
 *   - INTERFACE_UP: interface becomes active
 *   - ROLE_CHANGE: interface role classification changes
 *   - ENTROPY_SHIFT: spectral entropy or signal volatility changes
 *   - FLOW_START: new data flow detected (source → destination)
 *   - FLOW_END: data flow terminated
 *   - ANOMALY_DETECTED: behavioral anomaly inferred
 *   - SYNCHRONIZATION: phase alignment event
 *
 * Each event is immutable and linked to causal predecessors, enabling:
 *   - Forensic replay (reconstruct state from event log)
 *   - Temporal queries (what events led to this state?)
 *   - Causality analysis (why did this happen?)
 */

/**
 * IngressEvent — Immutable event record
 * @typedef {Object} IngressEvent
 * @property {number} ts - Simulation time (milliseconds from clock epoch)
 * @property {string} event_id - Globally unique event identifier (hash-based)
 * @property {string} entity_id - Primary subject (interface ID, host ID, etc.)
 * @property {string} type - Event type (INGRESS_SPIKE, ROLE_CHANGE, etc.)
 * @property {any} value - Event payload (new value, delta, classification, etc.)
 * @property {number} confidence - [0,1] — certainty of event (1.0 = deterministic, 0.5 = inferred)
 * @property {string} provenance - Source of event (telemetry, inference, user_input)
 * @property {Array<string>} causal_parents - Event IDs that caused this event
 * @property {boolean} is_forensic - true if event was reconstructed from history (replay)
 */
class IngressEvent {
  constructor(opts = {}) {
    this.ts = opts.ts ?? performance.now();
    this.entity_id = opts.entity_id;
    this.type = opts.type;
    this.value = opts.value ?? null;
    this.confidence = Math.min(1, Math.max(0, opts.confidence ?? 1.0));
    this.provenance = opts.provenance ?? 'unknown';
    this.causal_parents = opts.causal_parents ?? [];
    this.is_forensic = opts.is_forensic ?? false;

    // Generate deterministic event_id from content hash
    this.event_id = this._computeEventId();
  }

  _computeEventId() {
    // Simple hash-based ID (FNV-1a 32-bit); in production, use crypto.subtle.digest
    const content = `${this.ts}:${this.entity_id}:${this.type}:${JSON.stringify(this.value)}`;
    let hash = 2166136261;  // FNV offset basis (32-bit)
    for (let i = 0; i < content.length; i++) {
      hash ^= content.charCodeAt(i);
      hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
      hash >>>= 0;  // Keep 32-bit
    }
    return `evt_${hash.toString(16).padStart(8, '0')}`;
  }

  /**
   * Create a new event that depends on this one (for causality chaining)
   */
  chainEvent(opts = {}) {
    return new IngressEvent({
      ...opts,
      causal_parents: [...(opts.causal_parents ?? []), this.event_id]
    });
  }

  /**
   * Freeze event for immutability (prevents accidental mutation)
   */
  freeze() {
    Object.freeze(this);
    return this;
  }

  toJSON() {
    return {
      ts: this.ts,
      event_id: this.event_id,
      entity_id: this.entity_id,
      type: this.type,
      value: this.value,
      confidence: this.confidence,
      provenance: this.provenance,
      causal_parents: this.causal_parents,
      is_forensic: this.is_forensic,
    };
  }
}

/**
 * CausalEdge — Relationship between two events
 * @typedef {Object} CausalEdge
 * @property {string} source_id - Causal source (parent event ID)
 * @property {string} target_id - Causal target (child event ID)
 * @property {string} relationship - How source causes target (direct, inferred, temporal_correlation)
 * @property {number} confidence - [0,1] — confidence in the causal link
 */
class CausalEdge {
  constructor(opts = {}) {
    this.source_id = opts.source_id;
    this.target_id = opts.target_id;
    this.relationship = opts.relationship ?? 'direct';  // direct | inferred | temporal_correlation
    this.confidence = Math.min(1, Math.max(0, opts.confidence ?? 1.0));
  }

  toJSON() {
    return {
      source_id: this.source_id,
      target_id: this.target_id,
      relationship: this.relationship,
      confidence: this.confidence,
    };
  }
}

/**
 * Event Type Constants
 */
const EVENT_TYPES = {
  INGRESS_SPIKE: 'INGRESS_SPIKE',
  INTERFACE_DOWN: 'INTERFACE_DOWN',
  INTERFACE_UP: 'INTERFACE_UP',
  ROLE_CHANGE: 'ROLE_CHANGE',
  ENTROPY_SHIFT: 'ENTROPY_SHIFT',
  FLOW_START: 'FLOW_START',
  FLOW_END: 'FLOW_END',
  ANOMALY_DETECTED: 'ANOMALY_DETECTED',
  SYNCHRONIZATION: 'SYNCHRONIZATION',
  PHASE_ALIGNMENT: 'PHASE_ALIGNMENT',
  FIELD_PERTURBATION: 'FIELD_PERTURBATION',
  TRUST_SCORE_UPDATE: 'TRUST_SCORE_UPDATE',
};

/**
 * Event Provenance Constants
 */
const EVENT_PROVENANCE = {
  TELEMETRY: 'telemetry',
  INFERENCE: 'inference',
  PHYSICS: 'physics',
  USER_INPUT: 'user_input',
  REPLAY: 'replay',
  SYSTEM: 'system',
};

/**
 * IngressCausalStore — Event log + causality graph
 *
 * Maintains:
 *   - Ring-buffer of recent events (forensic history)
 *   - Causality graph (parent → child relationships)
 *   - Efficient query interface (by entity, type, time range, causal chain)
 *   - Incremental indexing (for replay consistency)
 */
class IngressCausalStore {
  constructor(opts = {}) {
    this.maxEvents = opts.maxEvents ?? 100_000;
    this.events = [];           // Ring-buffer of IngressEvent objects
    this.eventIndex = new Map(); // event_id → event (for rapid lookup)
    this.entityIndex = new Map(); // entity_id → [event_id...] (events by entity)
    this.typeIndex = new Map();   // event_type → [event_id...] (events by type)
    this.causalEdges = [];        // Array of CausalEdge objects
    this.causalGraph = new Map(); // source_id → [target_id...] (adjacency list)
    this.nextEventIdx = 0;        // Ring-buffer write pointer
  }

  /**
   * Append an event to the store (immutably)
   */
  addEvent(event) {
    const frozen = event.freeze ? event.freeze() : Object.freeze(event);

    // Ring-buffer: if full, overwrite oldest
    if (this.events.length < this.maxEvents) {
      this.events.push(frozen);
      this.nextEventIdx = this.events.length;
    } else {
      const oldEvent = this.events[this.nextEventIdx];
      if (oldEvent) {
        this.eventIndex.delete(oldEvent.event_id);
        this._removeFromIndex(this.entityIndex, oldEvent.entity_id, oldEvent.event_id);
        this._removeFromIndex(this.typeIndex, oldEvent.type, oldEvent.event_id);
      }
      this.events[this.nextEventIdx] = frozen;
      this.nextEventIdx = (this.nextEventIdx + 1) % this.maxEvents;
    }

    // Index by event_id
    this.eventIndex.set(frozen.event_id, frozen);

    // Index by entity_id
    if (!this.entityIndex.has(frozen.entity_id)) {
      this.entityIndex.set(frozen.entity_id, []);
    }
    this.entityIndex.get(frozen.entity_id).push(frozen.event_id);

    // Index by type
    if (!this.typeIndex.has(frozen.type)) {
      this.typeIndex.set(frozen.type, []);
    }
    this.typeIndex.get(frozen.type).push(frozen.event_id);

    // Link causal parents
    for (const parent_id of frozen.causal_parents) {
      const edge = new CausalEdge({
        source_id: parent_id,
        target_id: frozen.event_id,
        relationship: 'direct',
        confidence: frozen.confidence,
      });
      this.causalEdges.push(edge);
      if (!this.causalGraph.has(parent_id)) {
        this.causalGraph.set(parent_id, []);
      }
      this.causalGraph.get(parent_id).push(frozen.event_id);
    }

    return frozen.event_id;
  }

  /**
   * Query events by entity_id (all events for an interface/host)
   */
  getEventsByEntity(entity_id) {
    const eventIds = this.entityIndex.get(entity_id) ?? [];
    return eventIds.map(id => this.eventIndex.get(id)).filter(e => e);
  }

  /**
   * Query events by type
   */
  getEventsByType(type) {
    const eventIds = this.typeIndex.get(type) ?? [];
    return eventIds.map(id => this.eventIndex.get(id)).filter(e => e);
  }

  /**
   * Query events in a time range [ts_min, ts_max]
   */
  getEventsByTimeRange(ts_min, ts_max) {
    return this.events.filter(e => e && e.ts >= ts_min && e.ts <= ts_max);
  }

  /**
   * Get causal chain leading to an event (all ancestors)
   */
  getCausalChain(event_id, visited = new Set()) {
    if (visited.has(event_id)) return [];
    visited.add(event_id);

    const event = this.eventIndex.get(event_id);
    if (!event) return [];

    const chain = [event];
    for (const parent_id of event.causal_parents) {
      chain.push(...this.getCausalChain(parent_id, visited));
    }
    return chain;
  }

  /**
   * Get causal descendants (all events caused by this one)
   */
  getCausalDescendants(event_id) {
    const descendants = [];
    const queue = [event_id];
    const visited = new Set();

    while (queue.length > 0) {
      const current = queue.shift();
      if (visited.has(current)) continue;
      visited.add(current);

      const children = this.causalGraph.get(current) ?? [];
      for (const child_id of children) {
        const child = this.eventIndex.get(child_id);
        if (child) {
          descendants.push(child);
          queue.push(child_id);
        }
      }
    }
    return descendants;
  }

  /**
   * Infer a causal edge between two events (used by causality inference engine)
   */
  addCausalEdge(source_id, target_id, relationship = 'inferred', confidence = 0.7) {
    const source = this.eventIndex.get(source_id);
    const target = this.eventIndex.get(target_id);
    if (!source || !target) return false;

    const edge = new CausalEdge({
      source_id,
      target_id,
      relationship,
      confidence,
    });
    this.causalEdges.push(edge);

    if (!this.causalGraph.has(source_id)) {
      this.causalGraph.set(source_id, []);
    }
    this.causalGraph.get(source_id).push(target_id);

    return true;
  }

  /**
   * Export causality graph as JSON (for visualization, export)
   */
  exportCausalityGraph() {
    return {
      events: this.events.filter(e => e).map(e => e.toJSON()),
      edges: this.causalEdges.map(e => e.toJSON()),
    };
  }

  /**
   * Helper: remove item from a multi-value map
   */
  _removeFromIndex(index, key, value) {
    const arr = index.get(key);
    if (arr) {
      const idx = arr.indexOf(value);
      if (idx >= 0) arr.splice(idx, 1);
    }
  }

  /**
   * Get statistics about the store
   */
  getStats() {
    return {
      totalEvents: this.events.filter(e => e).length,
      maxEvents: this.maxEvents,
      entityCount: this.entityIndex.size,
      typeCount: this.typeIndex.size,
      causalEdgeCount: this.causalEdges.length,
    };
  }
}

/**
 * Event Inference Rules — Deterministic classification of telemetry into events
 *
 * These rules convert raw telemetry deltas into semantic events, enabling
 * the causality layer to work with meaningful state transitions rather than
 * raw numbers.
 */
class EventInferenceEngine {
  constructor(store, opts = {}) {
    this.store = store;
    this.spikeThreshold = opts.spikeThreshold ?? 0.5;      // rx delta / baseline
    this.entropyShiftThreshold = opts.entropyShiftThreshold ?? 0.25;
    this.anomalyScoreThreshold = opts.anomalyScoreThreshold ?? 0.7;
    /** @type {AdaptiveInferenceBaselines|null} */
    this.baselineProvider = opts.baselineProvider ?? null;
  }

  /**
   * Infer events from interface telemetry delta
   * @param {string} iface_id - Interface identifier
   * @param {Object} prev - Previous telemetry state
   * @param {Object} curr - Current telemetry state
   * @returns {Array<IngressEvent>} — Events generated from this delta
   */
  inferEventsFromTelemetryDelta(iface_id, prev, curr) {
    const events = [];

    // INTERFACE_DOWN: was active, now inactive
    if (prev && prev.is_active && !curr.is_active) {
      events.push(new IngressEvent({
        entity_id: iface_id,
        type: EVENT_TYPES.INTERFACE_DOWN,
        value: { reason: 'lost_signal' },
        confidence: 1.0,
        provenance: EVENT_PROVENANCE.TELEMETRY,
      }));
    }

    // INTERFACE_UP: was inactive, now active
    if (!prev || !prev.is_active && curr.is_active) {
      events.push(new IngressEvent({
        entity_id: iface_id,
        type: EVENT_TYPES.INTERFACE_UP,
        value: { reason: 'signal_acquired' },
        confidence: 1.0,
        provenance: EVENT_PROVENANCE.TELEMETRY,
      }));
    }

    // ROLE_CHANGE: classification changed
    if (prev && prev.role !== curr.role) {
      events.push(new IngressEvent({
        entity_id: iface_id,
        type: EVENT_TYPES.ROLE_CHANGE,
        value: { from: prev.role, to: curr.role },
        confidence: curr.role_confidence ?? 0.8,
        provenance: EVENT_PROVENANCE.TELEMETRY,
      }));
    }

    // INGRESS_SPIKE: rx_mbps increased significantly (adaptive threshold when provider set)
    if (prev && curr.rx_mbps != null) {
      const baseline = prev.rx_mbps ?? 1;
      const delta = (curr.rx_mbps - baseline) / Math.max(baseline, 1);
      const spikeThreshold = this.baselineProvider
        ? this.baselineProvider.getSpikeThreshold(iface_id)
        : this.spikeThreshold;
      if (delta > spikeThreshold) {
        const confidence = this.baselineProvider
          ? this.baselineProvider.spikeConfidence(iface_id, baseline, curr.rx_mbps)
          : Math.min(1, 0.6 + 0.4 * Math.min(delta, 2) / 2);
        events.push(new IngressEvent({
          entity_id: iface_id,
          type: EVENT_TYPES.INGRESS_SPIKE,
          value: {
            prev_rx_mbps: baseline,
            curr_rx_mbps: curr.rx_mbps,
            delta_ratio: delta,
            threshold_used: spikeThreshold,
          },
          confidence,
          provenance: EVENT_PROVENANCE.TELEMETRY,
        }));
      }
    }

    // ENTROPY_SHIFT: signal entropy changed significantly
    if (prev && curr.spectral_entropy !== undefined) {
      const baseline = prev.spectral_entropy ?? 0;
      const delta = Math.abs(curr.spectral_entropy - baseline);
      const entropyThreshold = this.baselineProvider
        ? this.baselineProvider.getEntropyShiftThreshold(iface_id)
        : this.entropyShiftThreshold;
      if (delta > entropyThreshold) {
        const confidence = this.baselineProvider
          ? this.baselineProvider.entropyShiftConfidence(iface_id, baseline, curr.spectral_entropy)
          : Math.min(1, 0.5 + 0.5 * (delta / 1.0));
        events.push(new IngressEvent({
          entity_id: iface_id,
          type: EVENT_TYPES.ENTROPY_SHIFT,
          value: {
            prev_entropy: baseline,
            curr_entropy: curr.spectral_entropy,
            delta: delta,
            direction: curr.spectral_entropy > baseline ? 'up' : 'down',
            threshold_used: entropyThreshold,
          },
          confidence,
          provenance: EVENT_PROVENANCE.TELEMETRY,
        }));
      }
    }

    return events;
  }

  /**
   * Infer anomaly event from behavioral signature
   */
  inferAnomalyEvent(host_id, anomalyScore, details) {
    if (anomalyScore > this.anomalyScoreThreshold) {
      return new IngressEvent({
        entity_id: host_id,
        type: EVENT_TYPES.ANOMALY_DETECTED,
        value: {
          anomaly_score: anomalyScore,
          details: details,
        },
        confidence: Math.min(1, anomalyScore),
        provenance: EVENT_PROVENANCE.INFERENCE,
      });
    }
    return null;
  }
}

// Export for use in browser/Node.js
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    IngressEvent,
    CausalEdge,
    IngressCausalStore,
    EventInferenceEngine,
    EVENT_TYPES,
    EVENT_PROVENANCE,
  };
}
