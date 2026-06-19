/**
 * BehavioralHostModel.js — Behavioral Host Abstraction (Layer 2)
 *
 * Transforms the model from interface-centric to host-centric:
 *   - A "host" is a behavioral actor (not just an IP node)
 *   - Interfaces are symptoms of host intent
 *   - Behavior is profiled across multiple dimensions
 *   - Trust is scored incrementally as events arrive
 *
 * Example: Before Layer 2, you see:
 *   eth0: 120 Mbps, role=GW
 *   wg0: 80 Mbps, role=VPN
 *
 * After Layer 2, you understand:
 *   Host A:
 *     outbound_pressure: high
 *     entropy: rising
 *     tunnel_bias: vpn-dominant
 *     trust: 0.6 (medium concern)
 *     behavioral_signature: [scanning, data_exfil_pattern, protocol_switching]
 */

/**
 * BehavioralSignature — Multi-dimensional behavioral fingerprint
 * @typedef {Object} BehavioralSignature
 * @property {Array<string>} signatures — Detected patterns (scanning, exfil, beaconing, etc.)
 * @property {number} entropy_trend — Rising (+1), stable (0), falling (-1)
 * @property {number} interface_churn — How often interfaces appear/disappear (0-1)
 * @property {number} role_volatility — Frequency of role classification changes (0-1)
 * @property {number} protocol_diversity — How many distinct L7 protocols used (0-1)
 */

class BehavioralSignature {
  constructor(opts = {}) {
    this.signatures = opts.signatures ?? [];  // ['scanning', 'exfil', 'beaconing', 'command', 'lateral']
    this.entropy_trend = opts.entropy_trend ?? 0;  // -1, 0, +1
    this.interface_churn = opts.interface_churn ?? 0;  // 0-1
    this.role_volatility = opts.role_volatility ?? 0;  // 0-1
    this.protocol_diversity = opts.protocol_diversity ?? 0;  // 0-1
    this.last_update = opts.last_update ?? Date.now();
  }

  /**
   * Add a signature (e.g., when scanning detected)
   */
  addSignature(sig) {
    if (!this.signatures.includes(sig)) {
      this.signatures.push(sig);
      this.last_update = Date.now();
    }
  }

  /**
   * Compute aggregate behavioral risk (0-1)
   */
  computeRisk() {
    let risk = 0;

    // Signature weighting
    const riskMap = {
      'scanning': 0.7,
      'exfil': 0.9,
      'beaconing': 0.8,
      'command': 0.85,
      'lateral': 0.75,
      'tunnel_escape': 0.6,
      'dns_tunneling': 0.5,
      'protocol_switching': 0.4,
    };

    for (const sig of this.signatures) {
      risk = Math.max(risk, riskMap[sig] ?? 0.3);
    }

    // Rising entropy adds risk
    if (this.entropy_trend > 0) {
      risk = Math.min(1, risk + 0.15);
    }

    // High churn adds risk
    risk = Math.min(1, risk + this.interface_churn * 0.2);

    // High role volatility adds risk
    risk = Math.min(1, risk + this.role_volatility * 0.1);

    return Math.min(1, risk);
  }

  toJSON() {
    return {
      signatures: this.signatures,
      entropy_trend: this.entropy_trend,
      interface_churn: this.interface_churn,
      role_volatility: this.role_volatility,
      protocol_diversity: this.protocol_diversity,
      risk: this.computeRisk(),
      last_update: this.last_update,
    };
  }
}

/**
 * TrustProfile — Incremental trust scoring based on behavioral patterns
 * @typedef {Object} TrustProfile
 * @property {number} score — [0,1] Trust confidence (1=trusted, 0=compromised)
 * @property {number} anomaly_count — Number of anomalies attributed to this host
 * @property {number} violation_count — Critical violations (command, lateral, etc.)
 * @property {boolean} is_flagged — True if flagged as suspicious by operator/rule
 * @property {Array<string>} known_patterns — Known good behavior patterns
 */

class TrustProfile {
  constructor(opts = {}) {
    this.score = opts.score ?? 0.5;  // Start neutral
    this.anomaly_count = opts.anomaly_count ?? 0;
    this.violation_count = opts.violation_count ?? 0;
    this.is_flagged = opts.is_flagged ?? false;
    this.known_patterns = opts.known_patterns ?? [];
    this.last_update = opts.last_update ?? Date.now();
  }

  /**
   * Update trust based on anomaly event
   */
  recordAnomaly(anomalyScore = 0.5) {
    this.anomaly_count++;
    // Anomalies gradually reduce trust
    const impact = anomalyScore * 0.05;  // Each anomaly impacts trust by 0-5%
    this.score = Math.max(0, this.score - impact);
    this.last_update = Date.now();
  }

  /**
   * Record a critical violation (scanning, exfil, etc.)
   */
  recordViolation(severity = 0.8) {
    this.violation_count++;
    // Violations heavily reduce trust
    const impact = severity * 0.15;  // Each violation impacts trust by 0-15%
    this.score = Math.max(0, this.score - impact);
    this.last_update = Date.now();
  }

  /**
   * Mark host as flagged (manual operator decision)
   */
  flag(reason = '') {
    this.is_flagged = true;
    this.score = Math.max(0, this.score - 0.2);
    this.last_update = Date.now();
  }

  /**
   * Unflag host
   */
  unflag() {
    this.is_flagged = false;
    // Gradual trust recovery (not immediately)
    this.score = Math.min(1, this.score + 0.1);
    this.last_update = Date.now();
  }

  /**
   * Register a known good pattern
   */
  addKnownPattern(pattern) {
    if (!this.known_patterns.includes(pattern)) {
      this.known_patterns.push(pattern);
    }
  }

  /**
   * Get trust level descriptor
   */
  getTrustLevel() {
    if (this.is_flagged) return 'FLAGGED';
    if (this.score >= 0.8) return 'TRUSTED';
    if (this.score >= 0.6) return 'NORMAL';
    if (this.score >= 0.4) return 'SUSPICIOUS';
    return 'COMPROMISED';
  }

  toJSON() {
    return {
      score: this.score,
      level: this.getTrustLevel(),
      anomaly_count: this.anomaly_count,
      violation_count: this.violation_count,
      is_flagged: this.is_flagged,
      known_patterns: this.known_patterns,
      last_update: this.last_update,
    };
  }
}

/**
 * BehavioralHost — Host as a behavioral actor
 *
 * Aggregates:
 *   - Multiple interfaces (eth0, wg0, tun0, etc.)
 *   - Interface events (up, down, role change, spike)
 *   - Behavioral patterns (signatures, entropy trends)
 *   - Trust profile (scored incrementally)
 */
class BehavioralHost {
  /**
   * @param {string} host_id — Unique host identifier
   * @param {Object} opts
   */
  constructor(host_id, opts = {}) {
    this.host_id = host_id;
    this.created_at = opts.created_at ?? Date.now();
    this.last_activity = opts.created_at ?? Date.now();

    // Interface tracking
    this.interfaces = new Map();  // iface_id → { role, rx_mbps, status, last_seen }
    this.interface_history = [];  // Chronological list of interface events

    // Behavioral profiling
    this.signature = new BehavioralSignature(opts.signature);
    this.trust = new TrustProfile(opts.trust);

    // Aggregated metrics
    this.total_rx_mbps = 0;
    this.entropy_samples = [];  // Ring buffer of recent entropy readings
    this.max_entropy_samples = opts.max_entropy_samples ?? 100;

    // Relationship graph
    this.parent_host = opts.parent_host ?? null;  // e.g., if this is a child process/container
    this.child_hosts = [];
    this.peer_hosts = [];  // Hosts it communicates with
  }

  /**
   * Register or update an interface
   */
  registerInterface(iface_id, iface_data) {
    const prev = this.interfaces.get(iface_id);

    this.interfaces.set(iface_id, {
      role: iface_data.role,
      rx_mbps: iface_data.rx_mbps ?? 0,
      status: iface_data.status ?? 'active',
      last_seen: Date.now(),
    });

    this.last_activity = Date.now();

    // Track interface churn
    if (!prev) {
      this.interface_history.push({ event: 'up', iface_id, ts: Date.now() });
      this.signature.interface_churn = Math.min(1, this.signature.interface_churn + 0.1);
    }

    // Track role changes
    if (prev && prev.role !== iface_data.role) {
      this.interface_history.push({
        event: 'role_change',
        iface_id,
        from: prev.role,
        to: iface_data.role,
        ts: Date.now(),
      });
      this.signature.role_volatility = Math.min(1, this.signature.role_volatility + 0.15);
    }
  }

  /**
   * Mark interface as inactive
   */
  deregisterInterface(iface_id) {
    if (this.interfaces.has(iface_id)) {
      this.interfaces.delete(iface_id);
      this.interface_history.push({ event: 'down', iface_id, ts: Date.now() });
      this.signature.interface_churn = Math.min(1, this.signature.interface_churn + 0.1);
    }
  }

  /**
   * Record an entropy reading
   */
  recordEntropy(entropy_value) {
    this.entropy_samples.push(entropy_value);
    if (this.entropy_samples.length > this.max_entropy_samples) {
      this.entropy_samples.shift();
    }

    // Detect entropy trend
    if (this.entropy_samples.length >= 5) {
      const recent = this.entropy_samples.slice(-5);
      const older = this.entropy_samples.slice(-10, -5);

      if (older.length >= 1) {
        const recentAvg = recent.reduce((a, b) => a + b) / recent.length;
        const olderAvg = older.reduce((a, b) => a + b) / older.length;

        if (recentAvg > olderAvg + 0.15) {
          this.signature.entropy_trend = 1;  // Rising
        } else if (recentAvg < olderAvg - 0.15) {
          this.signature.entropy_trend = -1;  // Falling
        } else {
          this.signature.entropy_trend = 0;  // Stable
        }
      }
    }
  }

  /**
   * Aggregate metrics across all interfaces
   */
  computeAggregateMetrics() {
    this.total_rx_mbps = 0;
    let activeInterfaces = 0;

    for (const [_, iface] of this.interfaces) {
      if (iface.status === 'active') {
        this.total_rx_mbps += iface.rx_mbps;
        activeInterfaces++;
      }
    }

    return {
      total_rx_mbps: this.total_rx_mbps,
      active_interfaces: activeInterfaces,
      entropy_avg: this.entropy_samples.length > 0
        ? this.entropy_samples.reduce((a, b) => a + b) / this.entropy_samples.length
        : 0,
    };
  }

  /**
   * Infer behavioral patterns from event history
   */
  inferPatterns(recentEvents) {
    // recentEvents: array of IngressEvent objects from the past N seconds

    for (const event of recentEvents) {
      switch (event.type) {
        case 'INGRESS_SPIKE':
          // High ingress on multiple interfaces in short time = possible scanning
          if (this.interfaces.size > 1 && event.value.delta_ratio > 2.0) {
            this.signature.addSignature('scanning');
          }
          break;

        case 'ROLE_CHANGE':
          // Frequent role changes = protocol switching
          if (this.signature.role_volatility > 0.5) {
            this.signature.addSignature('protocol_switching');
          }
          break;

        case 'ENTROPY_SHIFT':
          // Rising entropy on multiple interfaces = signal manipulation
          if (event.value.direction === 'up' && this.signature.entropy_trend > 0) {
            this.signature.addSignature('protocol_switching');
          }
          break;

        case 'ANOMALY_DETECTED':
          this.trust.recordAnomaly(event.value.anomaly_score ?? 0.5);
          break;
      }
    }
  }

  /**
   * Add child host (e.g., spawn detection)
   */
  addChild(child_host_id) {
    if (!this.child_hosts.includes(child_host_id)) {
      this.child_hosts.push(child_host_id);
    }
  }

  /**
   * Add peer host (communication relationship)
   */
  addPeer(peer_host_id) {
    if (!this.peer_hosts.includes(peer_host_id)) {
      this.peer_hosts.push(peer_host_id);
    }
  }

  /**
   * Get comprehensive host snapshot
   */
  toJSON() {
    const metrics = this.computeAggregateMetrics();

    return {
      host_id: this.host_id,
      created_at: this.created_at,
      last_activity: this.last_activity,
      interfaces: Array.from(this.interfaces.entries()).map(([id, data]) => ({
        iface_id: id,
        ...data,
      })),
      behavioral_signature: this.signature.toJSON(),
      trust_profile: this.trust.toJSON(),
      aggregate_metrics: metrics,
      relationships: {
        parent_host: this.parent_host,
        child_hosts: this.child_hosts,
        peer_hosts: this.peer_hosts,
      },
    };
  }
}

// Export
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    BehavioralSignature,
    TrustProfile,
    BehavioralHost,
  };
}
