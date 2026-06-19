/**
 * InterfaceToHostProjector.js — Map Interface Metrics → Host Behavior (Layer 2)
 *
 * Bridges the gap between interface-level telemetry and host-level behavior:
 *   - Aggregates interface metrics into host metrics
 *   - Detects multi-interface patterns (e.g., scanning across NICs)
 *   - Updates behavioral signatures as events arrive
 *   - Incremental trust scoring based on cumulative events
 *
 * Key insight: Interfaces are symptoms; hosts are agents with intent.
 *
 * Example Projection:
 *
 *   Interface Level:                Host Level:
 *   eth0: INGRESS_SPIKE 200 Mbps    Host-A: outbound_pressure = high
 *   wg0: INGRESS_SPIKE 150 Mbps     Host-A: entropy = rising
 *   tun0: INTERFACE_UP              Host-A: tunnel_bias = vpn-dominant
 *   eth0: ROLE_CHANGE GW→CLIENT     Host-A: trust = 0.45 (suspicious)
 *
 *   ↓ Pattern Inference ↓
 *   Signature: ['protocol_switching', 'tunnel_escape']
 *   Risk: 0.72 (HIGH)
 */

class InterfaceToHostProjector {
  /**
   * @param {HostRegistry} hostRegistry - Host management
   * @param {IngressCausalStore} causalStore - Event source
   * @param {Object} opts
   */
  constructor(hostRegistry, causalStore, opts = {}) {
    this.hostRegistry = hostRegistry;
    this.causalStore = causalStore;

    // Aggregation window (time lookback for pattern detection)
    this.aggregationWindowMs = opts.aggregationWindowMs ?? 10_000;  // 10 seconds

    // Thresholds for pattern detection
    this.multiInterfaceSpikeThreshold = opts.multiInterfaceSpikeThreshold ?? 2;  // 2+ ifaces spiking
    this.tunnelBiasThreshold = opts.tunnelBiasThreshold ?? 0.6;  // VPN traffic > 60%
    this.entropyTrendThreshold = opts.entropyTrendThreshold ?? 3;  // 3+ entropy shifts
    this.roleChangeThreshold = opts.roleChangeThreshold ?? 2;  // 2+ role changes

    // Callbacks
    this.onHostBehaviorChanged = opts.onHostBehaviorChanged ?? (() => {});
    this.onPatternDetected = opts.onPatternDetected ?? (() => {});

    // Bookkeeping
    this.lastProjectionTime = {};  // host_id → last projection timestamp
    this.eventProcessedCount = 0;
  }

  /**
   * Process an event and project it to host level
   * @param {IngressEvent} event - Event from causality layer
   * @returns {Object} — Projection result { host_id, metrics, signatures_added }
   */
  projectEvent(event) {
    const host_id = this._resolveHostId(event);
    if (!host_id) return null;

    const host = this.hostRegistry.getHost(host_id);
    if (!host) return null;

    let signaturesAdded = [];

    // Route event to appropriate projection handler
    switch (event.type) {
      case 'INGRESS_SPIKE':
        signaturesAdded = this._projectIngresses(host, event);
        break;

      case 'INTERFACE_UP':
      case 'INTERFACE_DOWN':
        signaturesAdded = this._projectInterfaceLifecycle(host, event);
        break;

      case 'ROLE_CHANGE':
        signaturesAdded = this._projectRoleChange(host, event);
        break;

      case 'ENTROPY_SHIFT':
        signaturesAdded = this._projectEntropyShift(host, event);
        break;

      case 'ANOMALY_DETECTED':
        signaturesAdded = this._projectAnomaly(host, event);
        break;
    }

    // Update last projection time
    this.lastProjectionTime[host_id] = Date.now();
    this.eventProcessedCount++;

    // Notify of changes
    if (signaturesAdded.length > 0) {
      this.onHostBehaviorChanged({
        host_id,
        host,
        signatures_added: signaturesAdded,
        event,
      });
    }

    return {
      host_id,
      metrics: host.computeAggregateMetrics(),
      signatures_added: signaturesAdded,
      trust_score: host.trust.score,
    };
  }

  /**
   * Project all ready events from the store
   */
  projectAllRecentEvents(sinceTimeMs = null) {
    const now = Date.now();
    const startTime = sinceTimeMs ?? (now - this.aggregationWindowMs);

    const recentEvents = this.causalStore.getEventsByTimeRange(startTime, now);
    const projections = [];

    for (const event of recentEvents) {
      const proj = this.projectEvent(event);
      if (proj) {
        projections.push(proj);
      }
    }

    // Run full pattern detection after processing events
    this._detectGlobalPatterns(projections);

    return projections;
  }

  /**
   * Detect multi-host patterns (coordination, clustering)
   */
  _detectGlobalPatterns(projections) {
    // Group projections by signature
    const sigMap = new Map();  // signature → [host_id...]

    for (const proj of projections) {
      for (const sig of proj.signatures_added) {
        if (!sigMap.has(sig)) {
          sigMap.set(sig, []);
        }
        if (!sigMap.get(sig).includes(proj.host_id)) {
          sigMap.get(sig).push(proj.host_id);
        }
      }
    }

    // Detect coordinated behavior (multiple hosts with same signature)
    for (const [sig, hostIds] of sigMap) {
      if (hostIds.length > 1) {
        this.onPatternDetected({
          type: 'coordinated_behavior',
          signature: sig,
          hosts: hostIds,
          hostCount: hostIds.length,
        });

        // Mark all hosts as peer-related
        for (let i = 0; i < hostIds.length; i++) {
          for (let j = i + 1; j < hostIds.length; j++) {
            this.hostRegistry.addPeerRelation(hostIds[i], hostIds[j]);
          }
        }
      }
    }
  }

  /**
   * Handle INGRESS_SPIKE events
   */
  _projectIngresses(host, event) {
    const sigs = [];

    // Record the spike
    host.recordEntropy(event.value.delta_ratio ?? 0.5);

    // Check for multi-interface spiking (scanning signature)
    const recentSpikes = this.causalStore.getEventsByTimeRange(
      Date.now() - 2000,  // Recent 2 seconds
      Date.now()
    ).filter(e => e.type === 'INGRESS_SPIKE' && e.entity_id.startsWith(event.entity_id?.split('-')[0]));

    if (recentSpikes.length >= this.multiInterfaceSpikeThreshold) {
      host.signature.addSignature('scanning');
      sigs.push('scanning');
    }

    // Check for sustained high bandwidth (exfil signature)
    const metrics = host.computeAggregateMetrics();
    if (metrics.total_rx_mbps > 500) {
      host.signature.addSignature('exfil');
      sigs.push('exfil');
      host.trust.recordViolation(0.7);
    }

    return sigs;
  }

  /**
   * Handle INTERFACE_UP/DOWN events
   */
  _projectInterfaceLifecycle(host, event) {
    const sigs = [];

    // Frequent interface churn = instability or intentional evasion
    if (host.signature.interface_churn > 0.7) {
      host.signature.addSignature('evasion');
      sigs.push('evasion');
      host.trust.recordAnomaly(0.4);
    }

    // Interface down + coming back up quickly = beaconing
    const recentDowns = host.interface_history.filter(
      e => e.event === 'down' && (Date.now() - e.ts) < 5000
    );

    if (event.type === 'INTERFACE_UP' && recentDowns.length > 0) {
      host.signature.addSignature('beaconing');
      sigs.push('beaconing');
      host.trust.recordViolation(0.8);
    }

    return sigs;
  }

  /**
   * Handle ROLE_CHANGE events
   */
  _projectRoleChange(host, event) {
    const sigs = [];

    // Detect protocol switching (client ↔ server, GW changes)
    if (host.signature.role_volatility > 0.5) {
      host.signature.addSignature('protocol_switching');
      sigs.push('protocol_switching');
      host.trust.recordAnomaly(0.3);
    }

    // GW → CLIENT = suspicious (gateway shouldn't become client)
    if (event.value?.from === 'GW' && event.value?.to === 'CLIENT') {
      host.signature.addSignature('lateral');
      sigs.push('lateral');
      host.trust.recordViolation(0.9);
    }

    return sigs;
  }

  /**
   * Handle ENTROPY_SHIFT events
   */
  _projectEntropyShift(host, event) {
    const sigs = [];

    // Track entropy trend in host
    if (event.value?.direction === 'up') {
      host.recordEntropy(Math.max(0.7, event.value?.curr_entropy ?? 0.5));
    }

    // Rising entropy on multiple interfaces = signal manipulation
    const recentEntropyShifts = this.causalStore.getEventsByTimeRange(
      Date.now() - 5000,
      Date.now()
    ).filter(e => e.type === 'ENTROPY_SHIFT' && e.value?.direction === 'up');

    if (recentEntropyShifts.length >= this.entropyTrendThreshold) {
      host.signature.addSignature('protocol_switching');
      sigs.push('protocol_switching');
    }

    return sigs;
  }

  /**
   * Handle ANOMALY_DETECTED events
   */
  _projectAnomaly(host, event) {
    const sigs = [];

    host.trust.recordAnomaly(event.value?.anomaly_score ?? 0.5);

    // High anomaly scores indicate specific attack signatures
    const score = event.value?.anomaly_score ?? 0;
    if (score > 0.8) {
      host.signature.addSignature('command');  // Command & control
      sigs.push('command');
      host.trust.recordViolation(0.95);
    } else if (score > 0.6) {
      host.signature.addSignature('dns_tunneling');
      sigs.push('dns_tunneling');
      host.trust.recordViolation(0.7);
    }

    return sigs;
  }

  /**
   * Resolve which host an event belongs to (interface → host mapping)
   */
  _resolveHostId(event) {
    const entityId = event.entity_id;

    // Direct host_id in event
    if (event.value?.host_id) {
      return event.value.host_id;
    }

    // Try to find host by interface
    const host = this.hostRegistry.getHostByInterface(entityId);
    if (host) {
      return host.host_id;
    }

    // Interface ID might contain host hint (e.g., "host-1/eth0")
    if (entityId.includes('/')) {
      const hostHint = entityId.split('/')[0];
      return hostHint;
    }

    return null;
  }

  /**
   * Get composite host profile (aggregated from all projections)
   */
  getHostProfile(host_id) {
    const host = this.hostRegistry.getHost(host_id);
    if (!host) return null;

    const metrics = host.computeAggregateMetrics();

    return {
      host_id,
      outbound_pressure: metrics.total_rx_mbps > 250 ? 'high' : (metrics.total_rx_mbps > 50 ? 'medium' : 'low'),
      entropy: host.signature.entropy_trend > 0 ? 'rising' : (host.signature.entropy_trend < 0 ? 'falling' : 'stable'),
      tunnel_bias: this._computeTunnelBias(host),
      active_interfaces: metrics.active_interfaces,
      behavioral_risk: host.signature.computeRisk(),
      trust_level: host.trust.getTrustLevel(),
      signatures: host.signature.signatures,
      interface_count: host.interfaces.size,
      last_activity_ms: Date.now() - host.last_activity,
    };
  }

  /**
   * Compute tunnel bias (how much traffic on VPN/encrypted channels)
   */
  _computeTunnelBias(host) {
    let tunnelTraffic = 0;
    let totalTraffic = 0;

    for (const [_, iface] of host.interfaces) {
      totalTraffic += iface.rx_mbps;

      // Heuristic: VPN, TUN, WG roles suggest tunneled
      if (['VPN', 'TUN', 'WG', 'PROXY'].includes(iface.role)) {
        tunnelTraffic += iface.rx_mbps;
      }
    }

    if (totalTraffic === 0) return 0;
    const bias = tunnelTraffic / totalTraffic;

    if (bias > this.tunnelBiasThreshold) {
      return 'vpn-dominant';
    } else if (bias > 0.3) {
      return 'vpn-mixed';
    } else {
      return 'direct';
    }
  }

  /**
   * Get projection statistics
   */
  getStats() {
    return {
      eventProcessedCount: this.eventProcessedCount,
      aggregationWindowMs: this.aggregationWindowMs,
      lastProjectionByHost: this.lastProjectionTime,
    };
  }

  /**
   * Export host profiles for visualization/export
   */
  exportHostProfiles() {
    const profiles = [];
    for (const host of this.hostRegistry.getAllHosts()) {
      profiles.push(this.getHostProfile(host.host_id));
    }
    return profiles;
  }
}

// Export
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    InterfaceToHostProjector,
  };
}
