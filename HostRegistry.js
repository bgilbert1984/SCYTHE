/**
 * HostRegistry.js — Identity Resolution & Relationship Tracking (Layer 2)
 *
 * Maintains:
 *   - Host identity graph (IP/MAC → canonical host_id)
 *   - Role-based clustering (GW, client, server, etc.)
 *   - Relationship tracking (parent/child, peer/sibling)
 *   - Lifecycle management (birth, migration, death)
 *   - Query interface for topology queries
 */

class HostRegistry {
  constructor(opts = {}) {
    // Host storage
    this.hosts = new Map();              // host_id → BehavioralHost
    this.hostsByIface = new Map();       // iface_id → host_id
    this.hostsByIp = new Map();          // ip_address → host_id
    this.hostsByMac = new Map();         // mac_address → host_id

    // Role-based clusters
    this.hostsByRole = new Map();        // role → [host_id...]
    this.clusterByRole = new Map();      // role → role metadata

    // Relationship indices
    this.parentChildRelations = new Map(); // host_id → { parent_id, children: [id...] }

    // Lifecycle tracking
    this.birthEvents = new Map();        // host_id → creation_timestamp
    this.deathEvents = new Map();        // host_id → death_timestamp

    // Configuration
    this.maxHosts = opts.maxHosts ?? 10_000;
    this.idleTimeoutMs = opts.idleTimeoutMs ?? 300_000;  // 5 minutes

    // Callbacks
    this.onHostCreated = opts.onHostCreated ?? (() => {});
    this.onHostDied = opts.onHostDied ?? (() => {});
    this.onIdentityMerge = opts.onIdentityMerge ?? (() => {});
  }

  /**
   * Register or get a host
   */
  registerHost(host_id, opts = {}) {
    let host = this.hosts.get(host_id);

    if (!host) {
      if (this.hosts.size >= this.maxHosts) {
        console.warn('[HostRegistry] At capacity, pruning oldest host');
        this._pruneOldestHost();
      }

      host = new BehavioralHost(host_id, opts);
      if (opts.identity) {
        host.identity = opts.identity;
      }
      this.hosts.set(host_id, host);
      this.birthEvents.set(host_id, Date.now());

      this.onHostCreated({ host_id, host });
    }

    return host;
  }

  /**
   * Register an interface for a host
   */
  registerInterface(host_id, iface_id, iface_data) {
    const host = this.registerHost(host_id);

    // Update interface mapping
    const prevHostForIface = this.hostsByIface.get(iface_id);
    if (prevHostForIface && prevHostForIface !== host_id) {
      // Interface moved from one host to another (e.g., failover)
      console.log(`[HostRegistry] Interface ${iface_id} moved from ${prevHostForIface} to ${host_id}`);
      this._deregisterInterface(iface_id, prevHostForIface);
    }

    this.hostsByIface.set(iface_id, host_id);
    host.registerInterface(iface_id, iface_data);
    if (iface_data.identity && !host.identity) {
      host.identity = iface_data.identity;
    }

    // Update IP mapping (if provided)
    if (iface_data.ip_address) {
      const prevHostForIp = this.hostsByIp.get(iface_data.ip_address);
      if (prevHostForIp && prevHostForIp !== host_id) {
        // IP reuse detected (could be DHCP reassignment or spoofing)
        console.warn(`[HostRegistry] IP ${iface_data.ip_address} reassigned from ${prevHostForIp} to ${host_id}`);
        this.onIdentityMerge({
          type: 'ip_reassignment',
          old_host_id: prevHostForIp,
          new_host_id: host_id,
          ip_address: iface_data.ip_address,
        });
      }
      this.hostsByIp.set(iface_data.ip_address, host_id);
    }

    // Update MAC mapping (if provided)
    if (iface_data.mac_address) {
      const prevHostForMac = this.hostsByMac.get(iface_data.mac_address);
      if (prevHostForMac && prevHostForMac !== host_id) {
        // MAC reuse detected (suspicious)
        console.warn(`[HostRegistry] MAC ${iface_data.mac_address} moved from ${prevHostForMac} to ${host_id}`);
        this.onIdentityMerge({
          type: 'mac_spoofing',
          old_host_id: prevHostForMac,
          new_host_id: host_id,
          mac_address: iface_data.mac_address,
        });
      }
      this.hostsByMac.set(iface_data.mac_address, host_id);
    }

    // Update role index
    if (iface_data.role) {
      if (!this.hostsByRole.has(iface_data.role)) {
        this.hostsByRole.set(iface_data.role, []);
      }
      const hostList = this.hostsByRole.get(iface_data.role);
      if (!hostList.includes(host_id)) {
        hostList.push(host_id);
      }
    }
  }

  /**
   * Mark interface as inactive
   */
  deregisterInterface(host_id, iface_id) {
    this._deregisterInterface(iface_id, host_id);
  }

  /**
   * Internal: deregister interface
   */
  _deregisterInterface(iface_id, host_id) {
    this.hostsByIface.delete(iface_id);

    const host = this.hosts.get(host_id);
    if (host) {
      host.deregisterInterface(iface_id);

      // If all interfaces gone, mark host as dead
      if (host.interfaces.size === 0) {
        this._markHostDead(host_id);
      }
    }
  }

  /**
   * Mark a host as dead (no active interfaces)
   */
  _markHostDead(host_id) {
    if (this.deathEvents.has(host_id)) return;  // Already dead

    this.deathEvents.set(host_id, Date.now());

    const host = this.hosts.get(host_id);
    this.onHostDied({ host_id, host, deathReason: 'all_interfaces_down' });

    console.log(`[HostRegistry] Host ${host_id} marked dead (no active interfaces)`);
  }

  /**
   * Prune oldest host (LRU eviction)
   */
  _pruneOldestHost() {
    let oldestId = null;
    let oldestTime = Infinity;

    for (const [host_id, host] of this.hosts) {
      if (host.last_activity < oldestTime) {
        oldestTime = host.last_activity;
        oldestId = host_id;
      }
    }

    if (oldestId) {
      this.hosts.delete(oldestId);
      console.log(`[HostRegistry] Pruned idle host: ${oldestId}`);
    }
  }

  /**
   * Get host by ID
   */
  getHost(host_id) {
    return this.hosts.get(host_id);
  }

  /**
   * Get host by IP address
   */
  getHostByIp(ip_address) {
    const host_id = this.hostsByIp.get(ip_address);
    return host_id ? this.hosts.get(host_id) : null;
  }

  /**
   * Get host by MAC address
   */
  getHostByMac(mac_address) {
    const host_id = this.hostsByMac.get(mac_address);
    return host_id ? this.hosts.get(host_id) : null;
  }

  /**
   * Get host by interface ID
   */
  getHostByInterface(iface_id) {
    const host_id = this.hostsByIface.get(iface_id);
    return host_id ? this.hosts.get(host_id) : null;
  }

  /**
   * Get all hosts with a specific role
   */
  getHostsByRole(role) {
    const hostIds = this.hostsByRole.get(role) ?? [];
    return hostIds.map(id => this.hosts.get(id)).filter(h => h);
  }

  /**
   * Set parent-child relationship
   */
  setParentChild(parent_id, child_id) {
    const child = this.registerHost(child_id);
    child.parent_host = parent_id;

    if (!this.parentChildRelations.has(parent_id)) {
      this.parentChildRelations.set(parent_id, { parent_id, children: [] });
    }

    const parent = this.getHost(parent_id);
    if (parent) {
      parent.addChild(child_id);
    }

    const relation = this.parentChildRelations.get(parent_id);
    if (!relation.children.includes(child_id)) {
      relation.children.push(child_id);
    }
  }

  /**
   * Add peer relationship
   */
  addPeerRelation(host_id_1, host_id_2) {
    const h1 = this.registerHost(host_id_1);
    const h2 = this.registerHost(host_id_2);

    h1.addPeer(host_id_2);
    h2.addPeer(host_id_1);
  }

  /**
   * Query hosts by behavioral criteria
   */
  findHostsByBehavior(criteria) {
    const results = [];

    for (const [_, host] of this.hosts) {
      let matches = true;

      // Trust level filter
      if (criteria.trustLevel) {
        if (host.trust.getTrustLevel() !== criteria.trustLevel) {
          matches = false;
        }
      }

      // Signature filter
      if (criteria.hasSignature) {
        if (!host.signature.signatures.includes(criteria.hasSignature)) {
          matches = false;
        }
      }

      // Risk threshold
      if (criteria.minRisk !== undefined) {
        if (host.signature.computeRisk() < criteria.minRisk) {
          matches = false;
        }
      }

      // Active interface count
      if (criteria.minInterfaces !== undefined) {
        if (host.interfaces.size < criteria.minInterfaces) {
          matches = false;
        }
      }

      if (matches) {
        results.push(host);
      }
    }

    return results;
  }

  /**
   * Get all hosts
   */
  getAllHosts() {
    return Array.from(this.hosts.values());
  }

  /**
   * Get hosts sorted by trust score (ascending = least trusted first)
   */
  getHostsByTrust(ascending = false) {
    const hosts = Array.from(this.hosts.values());
    hosts.sort((a, b) => {
      if (ascending) {
        return a.trust.score - b.trust.score;
      } else {
        return b.trust.score - a.trust.score;
      }
    });
    return hosts;
  }

  /**
   * Get hosts sorted by risk score (descending = most risky first)
   */
  getHostsByRisk(descending = true) {
    const hosts = Array.from(this.hosts.values());
    hosts.sort((a, b) => {
      const riskA = a.signature.computeRisk();
      const riskB = b.signature.computeRisk();
      if (descending) {
        return riskB - riskA;
      } else {
        return riskA - riskB;
      }
    });
    return hosts;
  }

  /**
   * Get registry statistics
   */
  getStats() {
    const allHosts = Array.from(this.hosts.values());
    const activeHosts = allHosts.filter(h => h.interfaces.size > 0);

    return {
      totalHosts: this.hosts.size,
      activeHosts: activeHosts.length,
      totalInterfaces: this.hostsByIface.size,
      roleDistribution: Object.fromEntries(
        Array.from(this.hostsByRole.entries()).map(([role, ids]) => [role, ids.length])
      ),
      trustLevelCounts: {
        TRUSTED: allHosts.filter(h => h.trust.getTrustLevel() === 'TRUSTED').length,
        NORMAL: allHosts.filter(h => h.trust.getTrustLevel() === 'NORMAL').length,
        SUSPICIOUS: allHosts.filter(h => h.trust.getTrustLevel() === 'SUSPICIOUS').length,
        COMPROMISED: allHosts.filter(h => h.trust.getTrustLevel() === 'COMPROMISED').length,
        FLAGGED: allHosts.filter(h => h.trust.is_flagged).length,
      },
    };
  }

  /**
   * Export registry as JSON
   */
  exportHosts() {
    return {
      hosts: Array.from(this.hosts.values()).map(h => h.toJSON()),
      timestamp: Date.now(),
      stats: this.getStats(),
    };
  }
}

// Export
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    HostRegistry,
  };
}
