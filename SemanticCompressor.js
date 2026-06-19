/**
 * SemanticCompressor.js — Ontology stabilization via compression layers
 *
 * Raw events → canonical primitives → behavioral motifs → operational narratives
 *
 * Reduces semantic entropy as event classes expand (DNS, RF, QUIC, AIS, …).
 */

const PRIMITIVE_MAP = {
  INGRESS_SPIKE: 'pressure_surge',
  INTERFACE_DOWN: 'link_loss',
  INTERFACE_UP: 'link_acquire',
  ROLE_CHANGE: 'role_transition',
  ENTROPY_SHIFT: 'signal_disorder',
  ANOMALY_DETECTED: 'trust_rupture',
  SYNCHRONIZATION: 'phase_lock',
  FIELD_PERTURBATION: 'field_distortion',
};

const MOTIF_RULES = [
  {
    motif: 'covert_tunneling',
    match: (primitives) =>
      primitives.includes('role_transition') &&
      primitives.includes('pressure_surge'),
  },
  {
    motif: 'beaconing_cycle',
    match: (primitives, events) =>
      primitives.filter((p) => p === 'link_loss' || p === 'link_acquire').length >= 2,
  },
  {
    motif: 'coordinated_surge',
    match: (primitives, events) =>
      primitives.filter((p) => p === 'pressure_surge').length >= 3,
  },
  {
    motif: 'trust_erosion',
    match: (primitives) =>
      primitives.includes('trust_rupture') || primitives.includes('signal_disorder'),
  },
  {
    motif: 'mesh_participation',
    match: (primitives, events) =>
      events.some((e) => String(e.value?.to || e.value?.role || '').toLowerCase().includes('vpn')),
  },
];

class SemanticCompressor {
  constructor(opts = {}) {
    this.windowMs = opts.windowMs ?? 60_000;
    this._buffers = new Map();
  }

  /**
   * Ingest event; returns compression layers for this host/entity window.
   */
  compress(hostId, event, simTime) {
    const key = hostId || event.entity_id || 'global';
    if (!this._buffers.has(key)) {
      this._buffers.set(key, []);
    }
    const buf = this._buffers.get(key);
    buf.push({ event, simTime });
    const cutoff = simTime - this.windowMs;
    while (buf.length && buf[0].simTime < cutoff) buf.shift();

    const events = buf.map((b) => b.event);
    const primitives = SemanticCompressor.toPrimitives(events);
    const motifs = SemanticCompressor.toMotifs(primitives, events);
    const narrative = SemanticCompressor.toNarrative(motifs, primitives);

    return { primitives, motifs, narrative, window_event_count: events.length };
  }

  static toPrimitives(events) {
    const set = new Set();
    for (const e of events) {
      const p = PRIMITIVE_MAP[e.type] || `raw:${e.type}`;
      set.add(p);
    }
    return [...set];
  }

  static toMotifs(primitives, events) {
    const motifs = [];
    for (const rule of MOTIF_RULES) {
      if (rule.match(primitives, events)) motifs.push(rule.motif);
    }
    return motifs;
  }

  static toNarrative(motifs, primitives) {
    if (!motifs.length) {
      return primitives.length
        ? `ambient_activity:${primitives.slice(0, 3).join('+')}`
        : 'quiescent';
    }
    if (motifs.includes('coordinated_surge') && motifs.includes('trust_erosion')) {
      return 'adversarial_coordination_likely';
    }
    if (motifs.includes('covert_tunneling')) {
      return 'tunnel_establishment_sequence';
    }
    if (motifs.includes('beaconing_cycle')) {
      return 'intermittent_beacon_host';
    }
    return `operational:${motifs[0]}`;
  }
}

if (typeof window !== 'undefined') {
  window.SemanticCompressor = SemanticCompressor;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { SemanticCompressor };
}
