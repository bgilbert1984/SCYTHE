/**
 * scythe-cognition-runtime.js — Orchestration topology for ingress cognition
 *
 * Mounts Layer 1 (causal timeline) + Layer 2 (behavioral hosts) as simulation authority.
 * Rendering samples HostRegistry; telemetry is evidence, not the source of truth.
 *
 *   SimulationClock.simTime
 *     → TelemetryToEventAdapter → IngressCausalStore
 *     → InterfaceToHostProjector → HostRegistry
 *     → field emitters / Cesium projection (sampled view)
 */

/* global SimulationClock, EventIngestionQueue, IngressCausalStore, EventInferenceEngine,
          TelemetryToEventAdapter, HostRegistry, InterfaceToHostProjector, ReplayEngine,
          HostIdentity, HostCognitionGraph, AdaptiveInferenceBaselines, BehavioralFieldPhysics,
          CounterfactualReplay, HostIdentityGenealogy, ImmutableEventLog, FieldInferenceCoupling,
          CausalShockwave, ProtocolFingerprintComponent, SemanticCompressor, CognitionECS,
          ResonanceLedger, MultiBranchCompare, EpistemicConfidenceGraph, SemanticGravity,
          ReplayDeltaStore, createLesionMutators, LESION_FAMILIES, RESONANCE_TYPES,
          EVENT_TYPES, EVENT_PROVENANCE */

const ROLE_ORBIT_RADIUS_M = {
  physical: 12000,
  mesh_vpn: 18000,
  container_overlay: 24000,
  unknown: 15000,
  default: 15000,
};

const ROLE_BASE_ALTITUDE_M = {
  physical: 8000,
  mesh_vpn: 14000,
  container_overlay: 20000,
  unknown: 12000,
  default: 12000,
};

function fnv1a32(str) {
  let hash = 2166136261;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function normalizeRole(role) {
  return String(role || 'unknown').toLowerCase().replace(/\s+/g, '_');
}

class ScytheCognitionRuntime {
  constructor(opts = {}) {
    this.opts = opts;
    this._initialized = false;
    this._pollTimer = null;
    this._ursUnsub = null;
    this._hostEntities = new Map();

    this.anchor = {
      lon: opts.anchorLon ?? -122.4194,
      lat: opts.anchorLat ?? 37.7749,
      height: opts.anchorHeight ?? 0,
    };

    this.heartbeatHz = opts.heartbeatHz ?? 0.35;
    this.pollIntervalMs = opts.pollIntervalMs ?? 5000;
  }

  /**
   * Establish simulation authority (Layer 1 + Layer 2).
   */
  init() {
    if (this._initialized) return this;

    this.clock = new SimulationClock({
      timeScale: this.opts.timeScale ?? 1.0,
      fixedDt: this.opts.fixedDt ?? 0.016,
    });
    this.eventQueue = new EventIngestionQueue(this.clock, {
      maxQueueSize: this.opts.maxQueueSize ?? 100_000,
    });

    this.causalStore = new IngressCausalStore({
      maxEvents: this.opts.maxEvents ?? 100_000,
    });
    this.adaptiveBaselines = new AdaptiveInferenceBaselines();
    this.cognitionGraph = new HostCognitionGraph();
    this.replayEngine = new ReplayEngine(this.causalStore, this.clock);
    this.eventLog = new ImmutableEventLog({
      persistToIdb: this.opts.persistToIdb ?? true,
    });
    this.genealogy = new HostIdentityGenealogy();
    this.semanticCompressor = new SemanticCompressor();
    this.ecs = new CognitionECS();
    this.resonanceLedger = new ResonanceLedger();
    this.epistemics = new EpistemicConfidenceGraph();
    this.semanticGravity = new SemanticGravity();
    this.replayDeltaStore = new ReplayDeltaStore();
    this.lesionMutators = typeof createLesionMutators === 'function' ? createLesionMutators() : {};
    this._identityByHost = new Map();
    this._fieldCouplingByHost = new Map();
    this._persistCounter = 0;
    
    // Routing ecology and evolutionary forecasting engines
    this.routeNicheRegistry = typeof RouteNicheRegistry !== 'undefined' ? new RouteNicheRegistry() : null;
    this.routeClimateField = typeof RouteClimateField !== 'undefined' ? new RouteClimateField() : null;
    this.lineageGenomeMemory = typeof LineageGenomeMemory !== 'undefined' ? new LineageGenomeMemory() : null;
    this.fitnessLandscapeEngine = typeof FitnessLandscapeEngine !== 'undefined' ? new FitnessLandscapeEngine() : null;
    this.nicheSuccessionEngine = typeof NicheSuccessionEngine !== 'undefined' ? new NicheSuccessionEngine() : null;
    this.routeForecastEngine = typeof RouteForecastEngine !== 'undefined' ? new RouteForecastEngine() : null;
    this.ecologicalShockEngine = typeof EcologicalShockEngine !== 'undefined' && this.routeNicheRegistry && this.fitnessLandscapeEngine ? new EcologicalShockEngine(this.routeNicheRegistry, this.fitnessLandscapeEngine) : null;
    this.counterfactualUniverseEngine = typeof CounterfactualUniverseEngine !== 'undefined' && this.routeForecastEngine && this.ecologicalShockEngine && this.routeClimateField && this.routeNicheRegistry ? new CounterfactualUniverseEngine(this.routeForecastEngine, this.ecologicalShockEngine, this.routeClimateField, this.routeNicheRegistry) : null;
    
    this.routePaleontologyEngine = typeof RoutePaleontologyEngine !== 'undefined' ? new RoutePaleontologyEngine() : null;
    this.routePhylogeneticEngine = typeof RoutePhylogeneticEngine !== 'undefined' ? new RoutePhylogeneticEngine(this.routePaleontologyEngine) : null;

    this.inferenceEngine = new EventInferenceEngine(this.causalStore, {
      spikeThreshold: this.opts.spikeThreshold ?? 0.5,
      entropyShiftThreshold: this.opts.entropyShiftThreshold ?? 0.25,
      anomalyScoreThreshold: this.opts.anomalyScoreThreshold ?? 0.7,
      baselineProvider: this.adaptiveBaselines,
    });
    this.telemetryAdapter = new TelemetryToEventAdapter(
      this.causalStore,
      this.clock,
      this.inferenceEngine,
      { maxHistoryAge: this.opts.maxHistoryAge ?? 300_000 }
    );

    this.hostRegistry = new HostRegistry({
      maxHosts: this.opts.maxHosts ?? 10_000,
    });
    this.hostProjector = new InterfaceToHostProjector(this.hostRegistry, this.causalStore, {
      aggregationWindowMs: this.opts.aggregationWindowMs ?? 10_000,
    });

    this.telemetryAdapter.on('event-generated', (event) => {
      this._onSemanticEvent(event);
      this.hostProjector.projectEvent(event);
    });

    this.hostProjector.onHostBehaviorChanged = (payload) => {
      this._lastBehaviorChange = payload;
    };

    this.hostProjector.onPatternDetected = (pattern) => {
      this._linkCoordinatedHosts(pattern);
    };

    this.clock.start();
    this._initialized = true;
    this.counterfactual = new CounterfactualReplay(this);
    this.multiBranch = new MultiBranchCompare(this);
    this._installDeprecatedIngressShim();

    this._waitForURS();
    this._startIngressPolling();

    console.log('[ScytheCognition] Simulation authority online', {
      simTime: this.clock.simTime,
      pollIntervalMs: this.pollIntervalMs,
    });

    return this;
  }

  /**
   * Drive clock from URS (single animation authority). Idempotent — safe to call after URS boots.
   */
  attachToURS() {
    return this._attachRenderLoop();
  }

  _attachRenderLoop() {
    if (this._ursUnsub) return true;

    const urs = window.__URS__;
    if (!urs || typeof urs.onFrame !== 'function') return false;

    this._ursUnsub = urs.onFrame(() => this._onSimulationFrame());
    if (this._rafId) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    console.log('[ScytheCognition] Clock bound to UnifiedRenderScheduler');
    return true;
  }

  _waitForURS() {
    if (this._attachRenderLoop()) return;

    let attempts = 0;
    const wait = setInterval(() => {
      attempts++;
      if (this._attachRenderLoop() || attempts > 240) {
        clearInterval(wait);
        if (!this._ursUnsub) {
          console.warn('[ScytheCognition] URS not found; using internal rAF for sim clock');
          this._fallbackRaf();
        }
      }
    }, 250);
  }

  _fallbackRaf() {
    const tick = () => {
      if (!this._initialized) return;
      this._onSimulationFrame();
      this._rafId = requestAnimationFrame(tick);
    };
    tick();
  }

  _onSimulationFrame() {
    if (!this.clock?.isRunning) return;

    const dt = this.clock.update();
    const ready = this.eventQueue.processUpTo(this.clock.simTime);
    if (ready.length > 0) {
      for (const evt of ready) {
        this.hostProjector.projectEvent(evt);
      }
    }

    this._syncFieldEmitters();
    this._sampleCesiumProjection();

    this._frameStatus = {
      simTime: this.clock.simTime,
      dt,
      frameNumber: this.clock.frameNumber,
      hostCount: this.hostRegistry.hosts.size,
    };
  }

  _startIngressPolling() {
    if (this._pollTimer) clearInterval(this._pollTimer);
    const poll = () => this.pollIngressInterfaces().catch((err) => {
      console.warn('[ScytheCognition] Ingress poll failed:', err);
    });
    poll();
    this._pollTimer = setInterval(poll, this.pollIntervalMs);
  }

  /**
   * Poll API → eventize → host registry (canonical path).
   */
  async pollIngressInterfaces() {
    const apiBase = window.SCYTHE_API_BASE || '';
    const res = await fetch(`${apiBase}/api/network/ingress/interfaces`);
    if (!res.ok) throw new Error(`ingress HTTP ${res.status}`);
    const data = await res.json();
    const interfaces = data.interfaces || [];
    return this.ingestIngressInterfaces(interfaces, {
      wallIngestTs: data.timestamp,
    });
  }

  /**
   * Ingest a batch of raw interface records at current simTime.
   */
  ingestIngressInterfaces(interfaces, meta = {}) {
    if (!this._initialized) this.init();

    const batch = [];
    for (const iface of interfaces) {
      const telemetry = this._interfaceToTelemetry(iface);
      if (telemetry) batch.push(telemetry);
    }

    const events = this.telemetryAdapter.processBatch(batch);

    this._persistCounter++;
    if (this._persistCounter % 6 === 0) {
      this.persistCausalitySnapshot();
    }

    this._runGenealogyPass();
    this.resonanceLedger?.decay(this.clock.simTime);

    if (events.length > 0) {
      console.log(
        `[ScytheCognition] t=${this.clock.simTime.toFixed(0)}ms ` +
        `${events.length} events from ${batch.length} interfaces`,
        meta.wallIngestTs ? `(wall ${meta.wallIngestTs})` : ''
      );
    }

    return { events, hosts: this.getHostProfiles(), simTime: this.clock.simTime };
  }

  _interfaceToTelemetry(iface) {
    const entityId = iface.name || iface.interface_id || iface.id;
    if (!entityId) return null;

    const hostId = this._resolveHostId(iface);
    const role = normalizeRole(iface.role);
    const identity = this._getOrCreateIdentity(hostId);

    identity.mutateFromEvidence({
      simTime: this.clock.simTime,
      iface: {
        role,
        mac_address: iface.mac_address,
      },
    });

    this.hostRegistry.registerInterface(hostId, entityId, {
      role: role.toUpperCase(),
      rx_mbps: Number(iface.rx_mbps) || 0,
      status: iface.state === 'down' ? 'inactive' : 'active',
      ip_address: iface.addresses?.[0]?.addr,
      identity,
    });

    const telemetry = {
      entity_id: entityId,
      host_id: hostId,
      is_active: iface.state !== 'down',
      rx_mbps: Number(iface.rx_mbps) || 0,
      role: role.toUpperCase(),
      role_confidence: iface.role_confidence,
      spectral_entropy: iface.spectral_entropy,
      anomaly_score: iface.anomaly_score,
    };

    this.adaptiveBaselines.observe(entityId, telemetry);
    this.adaptiveBaselines.registerCohort(`role:${role}`, [entityId]);
    identity.mutateFromEvidence({ simTime: this.clock.simTime, telemetry });
    return telemetry;
  }

  _resolveHostId(iface) {
    const inst = window.SCYTHE_INSTANCE_ID || window.SCYTHE_INSTANCE?.id || 'local';
    const ip = iface.addresses?.find((a) => a.family === 'IPv4')?.addr
      || iface.addresses?.[0]?.addr;
    if (ip && !ip.startsWith('127.')) {
      return `host-${inst}-${ip.replace(/\./g, '-')}`;
    }
    return `host-${inst}`;
  }

  _getOrCreateIdentity(hostId) {
    const canon = this.genealogy?.canonicalId(hostId) ?? hostId;
    if (!this._identityByHost.has(canon)) {
      this._identityByHost.set(
        canon,
        new HostIdentity(canon, { first_seen_simTime: this.clock.simTime })
      );
    }
    const identity = this._identityByHost.get(canon);
    this.genealogy?.register(canon, identity, this.clock.simTime);
    return identity;
  }

  _runGenealogyPass() {
    if (!this.genealogy) return;
    this.genealogy.decay(this.clock.simTime);
    const entries = Array.from(this._identityByHost.entries()).map(([host_id, identity]) => ({
      host_id,
      identity: identity.toJSON(),
    }));
    this.genealogy.proposeMerges(entries);
  }

  _resolveHostIdForEvent(event) {
    const host = this.hostRegistry.getHostByInterface(event.entity_id);
    if (host) return host.host_id;
    if (event.value?.host_id) return event.value.host_id;
    if (event.entity_id?.includes('/')) return event.entity_id.split('/')[0];
    return null;
  }

  _onSemanticEvent(event) {
    const hostId = this._resolveHostIdForEvent(event);
    if (!hostId) return;

    if (this.eventLog && event.toJSON) {
      this.eventLog.append(event.toJSON());
    }

    const identity = this._getOrCreateIdentity(hostId);
    identity.mutateFromEvidence({ simTime: this.clock.simTime, event });

    let coupling = this._fieldCouplingByHost.get(hostId);
    let confidence = event.confidence ?? 1;
    if (coupling) {
      confidence *= coupling.confidenceMultiplier ?? 1;
    }
    if (this.semanticGravity) {
      const pull = this.semanticGravity.attraction(hostId, { ambiguous: confidence < 0.7 });
      confidence = Math.min(1, confidence + pull * 0.1);
      confidence = this.semanticGravity.applyCounterGravity(hostId, confidence);
    }
    const adjusted = coupling
      ? FieldInferenceCoupling.adjustEventConfidence({ ...event, confidence }, coupling)
      : { ...event, confidence };

    const host = this.hostRegistry.getHost(hostId);
    const metrics = host?.computeAggregateMetrics?.() ?? {};

    this.cognitionGraph.recordHostEvent(
      hostId,
      identity,
      adjusted,
      this.clock.simTime,
      {
        trust: host?.trust?.score,
        pressure: metrics.total_rx_mbps,
        entropy: metrics.entropy_avg,
        volatility: host?.signature?.role_volatility ?? 0,
      }
    );

    if (host) host.identity = identity;

    const compression = this.semanticCompressor.compress(hostId, adjusted, this.clock.simTime);
    if (compression.motifs.includes('covert_tunneling')) {
      identity.speciation_subtype = 'tunnel_lineage';
    } else if (compression.motifs.includes('coordinated_surge')) {
      identity.speciation_subtype = 'coordinated_subtype';
    }

    this.ecs.attachComponent(hostId, 'IdentityComponent', identity, this.clock.simTime);
    this.ecs.attachComponent(hostId, 'ProtocolFingerprintComponent', identity.protocol_fingerprints, this.clock.simTime);
    this.ecs.attachComponent(hostId, 'TrustComponent', host?.trust, this.clock.simTime);
    this.ecs.attachComponent(hostId, 'SemanticNarrativeComponent', compression, this.clock.simTime);
    if (coupling) {
      this.ecs.attachComponent(hostId, 'FieldComponent', coupling, this.clock.simTime);
    }
  }

  _linkCoordinatedHosts(pattern) {
    const hosts = pattern.hosts || [];
    for (let i = 0; i < hosts.length; i++) {
      for (let j = i + 1; j < hosts.length; j++) {
        this.cognitionGraph.linkHosts(
          hosts[i],
          hosts[j],
          pattern.signature || 'coordinated_behavior',
          0.75,
          this.clock.simTime,
          { pattern_type: pattern.type }
        );
      }
    }
  }

  _installDeprecatedIngressShim() {
    if (window.__scytheIngressShimInstalled) return;
    window.__scytheIngressShimInstalled = true;
    let warned = false;
    Object.defineProperty(window, 'networkIngressMap', {
      configurable: true,
      enumerable: true,
      get() {
        if (!warned) {
          warned = true;
          console.warn(
            '[ScytheCognition] networkIngressMap is deprecated; use ScytheCognition.getHostProfiles()'
          );
        }
        return window.ScytheCognition?.getHostProfiles?.() ?? [];
      },
      set() {
        console.warn('[ScytheCognition] Ignoring write to deprecated networkIngressMap');
      },
    });
  }

  persistCausalitySnapshot() {
    const snapshot = {
      simTime: this.clock.simTime,
      causal: this.causalStore.exportCausalityGraph(),
      cognition: this.cognitionGraph.exportGraph(),
      genealogy: this.genealogy?.exportGenealogy(),
      event_log_meta: this.eventLog?.exportAll()?.metadata,
      exported_at: Date.now(),
    };
    window.__scytheCausalitySnapshot__ = snapshot;
    try {
      const key = `scythe:${window.SCYTHE_INSTANCE_ID || 'local'}:causality`;
      localStorage.setItem(key, JSON.stringify(snapshot));
    } catch (_) { /* storage quota */ }
    return snapshot;
  }

  exportCausalityForReplay() {
    return this.causalStore.exportCausalityGraph();
  }

  startReplay(opts = {}) {
    if (!this._initialized) this.init();
    const data = opts.data || this.exportCausalityForReplay();
    if (!data.events?.length) {
      console.warn('[ScytheCognition] No events to replay');
      return false;
    }
    this._livePollWasActive = !!this._pollTimer;
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
    this.replayEngine.loadEventsFromJSON(data);
    return this.replayEngine.startReplay(opts.speed ?? 1.0);
  }

  stopReplay() {
    const ok = this.replayEngine.stopReplay();
    if (this._livePollWasActive) this._startIngressPolling();
    return ok;
  }

  seekToSimTime(targetMs) {
    if (this.replayEngine.isReplaying) {
      const ok = this.replayEngine.seekToTime(targetMs);
      if (ok) this._reconstructStateAtSimTime(targetMs);
      return ok;
    }
    return false;
  }

  _reconstructStateAtSimTime(simTime) {
    const events = this.causalStore.getEventsByTimeRange(0, simTime);
    for (const evt of events) {
      this._onSemanticEvent(evt);
      this.hostProjector.projectEvent(evt);
    }
    this._syncFieldEmitters();
    this._sampleCesiumProjection();
  }

  getCognitionGraph() {
    return this.cognitionGraph.exportGraph();
  }

  getHostIdentities() {
    return Array.from(this._identityByHost.values()).map((id) => id.toJSON());
  }

  getECS() {
    return this.ecs?.export();
  }

  getOperationalNarratives() {
    const out = [];
    for (const ent of this.ecs?.entities?.values() ?? []) {
      const narrative = ent.get('SemanticNarrativeComponent');
      if (narrative) {
        out.push({ entityId: ent.entityId, ...narrative });
      }
    }
    return out;
  }

  /**
   * Counterfactual branch: mutate events from fork point and measure divergence.
   * @see CounterfactualReplay.branchReplay
   */
  branchReplay(opts) {
    if (!this.counterfactual) this.counterfactual = new CounterfactualReplay(this);
    const result = this.counterfactual.branchReplay(opts);
    const topo = result?.divergence?.shockwave_topology;
    if (topo && this.resonanceLedger) {
      this.resonanceLedger.ingestShockwave(topo, {
        label: opts.label,
        simTime: this.clock.simTime,
        lesion_family: opts.lesion_family ?? 'event',
      });
    }
    this.replayDeltaStore?.recordBranch(result, {
      lesion_family: opts.lesion_family ?? 'event',
    });
    return result;
  }

  /**
   * Comparative counterfactual analysis across multiple branches.
   * @param {Array<Object>} branchSpecs - each spec → branchReplay()
   */
  compareBranches(branchSpecs) {
    if (!this.multiBranch) this.multiBranch = new MultiBranchCompare(this);
    const comparison = this.multiBranch.compareBranches(branchSpecs);
    if (comparison.overlap_topology?.shared_affinity_pairs && this.resonanceLedger) {
      for (const p of comparison.overlap_topology.shared_affinity_pairs) {
        this.resonanceLedger.record({
          host_a: p.host_a,
          host_b: p.host_b,
          type: RESONANCE_TYPES.SUPPRESSED,
          coherence: p.branch_overlap / (comparison.branch_count || 1),
          simTime: this.clock.simTime,
          lesion_label: 'multi_branch_overlap',
        });
      }
    }
    this.epistemics?.ingestBranchComparison(comparison, this.clock.simTime);
    this.epistemics?.applyUncertaintyPressure();
    this.semanticGravity?.ingestComparison(comparison, this.resonanceLedger);
    this.replayDeltaStore?.recordComparison(comparison, { lesion_family: 'multi' });
    return comparison;
  }

  getEpistemics() {
    return this.epistemics?.export();
  }

  getSemanticGravity() {
    return this.semanticGravity?.export();
  }

  getReplayDeltaTree() {
    return this.replayDeltaStore?.getTree();
  }

  exportReplayDeltas() {
    return this.replayDeltaStore?.exportParquetReady();
  }

  getOperationalAffinities(minScore) {
    return this.resonanceLedger?.getOperationalAffinities(minScore) ?? [];
  }

  getResonanceLedger() {
    return this.resonanceLedger?.export();
  }

  assessHostDeviation(entityId, telemetry) {
    return this.adaptiveBaselines.assessDeviation(entityId, telemetry);
  }

  getGenealogy() {
    return this.genealogy?.exportGenealogy();
  }

  exportImmutableEventLog() {
    return this.eventLog?.exportAll();
  }

  applyAutoMerges(minScore) {
    const threshold = minScore ?? this.genealogy.mergeThreshold;
    const applied = [];
    for (const c of this.genealogy.mergeCandidates) {
      if (c.score >= threshold) {
        this.genealogy.merge(c.host_a, c.host_b, c.score);
        applied.push(c);
      }
    }
    return applied;
  }

  getHostProfiles() {
    if (!this.hostProjector) return [];
    return this.hostProjector.exportHostProfiles().map((profile) => {
      const node = this.cognitionGraph?.getNode(profile.host_id);
      const identity = this._identityByHost.get(profile.host_id);
      return {
        ...profile,
        identity: identity?.toJSON() ?? node?.identity?.toJSON?.(),
        cognition_state: node?.state ?? null,
        event_count: node?.events?.length ?? 0,
      };
    });
  }

  getHosts() {
    return this.hostRegistry ? this.hostRegistry.getAllHosts() : [];
  }

  getStatus() {
    return {
      initialized: this._initialized,
      simTime: this.clock?.simTime ?? 0,
      frame: this._frameStatus ?? null,
      causal: this.causalStore?.getStats(),
      telemetry: this.telemetryAdapter?.getStatus(),
      hosts: this.hostRegistry?.getStats(),
      projector: this.hostProjector?.getStats(),
      cognition: {
        nodes: this.cognitionGraph?.nodes?.size ?? 0,
        cross_host_edges: this.cognitionGraph?.crossHostEdges?.length ?? 0,
      },
      replay: {
        isReplaying: this.replayEngine?.isReplaying ?? false,
      },
      event_log: {
        total: this.eventLog?.totalEvents ?? 0,
      },
      genealogy: {
        merge_candidates: this.genealogy?.mergeCandidates?.length ?? 0,
      },
      resonance: {
        pair_count: this.resonanceLedger?.export()?.pair_count ?? 0,
        affinities: this.resonanceLedger?.getOperationalAffinities()?.length ?? 0,
      },
    };
  }

  /**
   * Map behavioral hosts → HyperField emitters (continuous cognition sampling).
   */
  _syncFieldEmitters() {
    const globe = window._globe || window.scytheGlobe;
    if (!globe || typeof globe.updateRFEmitters !== 'function') return;

    const hosts = this.hostRegistry.getAllHosts();
    if (!hosts.length) return;

    const simTimeSec = this.clock.simTime / 1000;
    const emitters = [];
    const neighborMeta = hosts.map((h) => ({
      host_id: h.host_id,
      risk: h.signature.computeRisk(),
      phase: (fnv1a32(h.host_id) % 360) * (Math.PI / 180),
    }));

    for (const host of hosts) {
      const profile = this.hostProjector.getHostProfile(host.host_id);
      if (!profile) continue;

      const pos = this._hostWorldPosition(host, profile, simTimeSec);
      if (!pos) continue;

      const identity = host.identity || this._identityByHost.get(host.host_id);
      const neighbors = neighborMeta.filter((n) => n.host_id !== host.host_id);
      const field = BehavioralFieldPhysics.sample(
        host,
        identity,
        profile,
        simTimeSec,
        neighbors
      );

      const coupling = FieldInferenceCoupling.couple(host, identity, field, neighbors);
      this._fieldCouplingByHost.set(host.host_id, coupling);
      for (const edge of coupling.inferred_edges) {
        this.cognitionGraph.linkHosts(
          host.host_id,
          edge.target,
          edge.relationship,
          edge.confidence,
          this.clock.simTime,
          { source: 'field_inference' }
        );
      }

      emitters.push({
        pos: { x: pos.x, y: pos.y, z: pos.z },
        intensity: field.intensity,
        anomaly: field.anomaly,
        radius: field.radius,
        phase: field.phase,
        meta: field.meta,
      });
    }

    globe.updateRFEmitters(emitters.slice(0, 256));
  }

  _hostWorldPosition(host, profile, simTimeSec) {
    const Cesium = window.Cesium;
    if (!Cesium) return null;

    const primaryIface = host.interfaces.keys().next().value || host.host_id;
    const role = normalizeRole(
      host.interfaces.get(primaryIface)?.role || 'unknown'
    );
    const hash = fnv1a32(`${host.host_id}:${role}`);
    const angle = (hash % 360) * (Math.PI / 180);
    const radiusM = ROLE_ORBIT_RADIUS_M[role] ?? ROLE_ORBIT_RADIUS_M.default;

    const metrics = host.computeAggregateMetrics();
    const risk = host.signature.computeRisk();
    const amp =
      Math.min(8000, metrics.total_rx_mbps * 8) +
      Math.min(4000, metrics.entropy_avg * 2000) +
      host.signature.role_volatility * 3000 +
      risk * 5000;

    const baseAlt = ROLE_BASE_ALTITUDE_M[role] ?? ROLE_BASE_ALTITUDE_M.default;
    const altitude =
      baseAlt + Math.sin(simTimeSec * this.heartbeatHz * Math.PI * 2 + hash * 0.001) * amp;

    const lat = this.anchor.lat + (Math.cos(angle) * radiusM) / 111320;
    const lon = this.anchor.lon + (Math.sin(angle) * radiusM) / (111320 * Math.cos(this.anchor.lat * Math.PI / 180));

    return Cesium.Cartesian3.fromDegrees(lon, lat, altitude);
  }

  /**
   * Sample HostRegistry into Cesium entities (view layer only).
   */
  _sampleCesiumProjection() {
    const viewer = window.viewer;
    const Cesium = window.Cesium;
    if (!viewer || !Cesium || !this.hostRegistry) return;

    const simTimeSec = this.clock.simTime / 1000;
    const activeIds = new Set();

    for (const host of this.hostRegistry.getAllHosts()) {
      if (host.interfaces.size === 0) continue;

      const profile = this.hostProjector.getHostProfile(host.host_id);
      const pos = this._hostWorldPosition(host, profile, simTimeSec);
      if (!pos) continue;

      activeIds.add(host.host_id);
      const risk = host.signature.computeRisk();
      const trust = host.trust.score;
      const color = risk > 0.7
        ? Cesium.Color.RED.withAlpha(0.85)
        : trust < 0.4
          ? Cesium.Color.ORANGE.withAlpha(0.8)
          : Cesium.Color.CYAN.withAlpha(0.75);

      let ent = this._hostEntities.get(host.host_id);
      if (!ent) {
        ent = viewer.entities.add({
          id: `cognition-host-${host.host_id}`,
          name: host.host_id,
          position: pos,
          point: {
            pixelSize: 10,
            color,
            outlineColor: Cesium.Color.WHITE.withAlpha(0.5),
            outlineWidth: 1,
          },
          label: {
            text: host.host_id,
            font: '11px monospace',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
            pixelOffset: new Cesium.Cartesian2(0, -12),
            show: true,
            scale: 0.85,
          },
        });
        this._hostEntities.set(host.host_id, ent);
      } else {
        ent.position = pos;
        ent.point.color = color;
        const idHash = host.identity?.host_hash?.slice(0, 10) ?? host.host_id.slice(0, 12);
        ent.label.text = `${idHash}\n${profile?.trust_level ?? ''} · ${profile?.outbound_pressure ?? ''}`;
      }
    }

    for (const [hostId, ent] of this._hostEntities) {
      if (!activeIds.has(hostId)) {
        try { viewer.entities.remove(ent); } catch (_) {}
        this._hostEntities.delete(hostId);
      }
    }
  }

  destroy() {
    if (this._pollTimer) clearInterval(this._pollTimer);
    if (this._ursUnsub) this._ursUnsub();
    if (this._rafId) cancelAnimationFrame(this._rafId);
    this.clock?.stop();
    this._initialized = false;
  }
}

const ScytheCognition = new ScytheCognitionRuntime();

if (typeof window !== 'undefined') {
  window.ScytheCognitionRuntime = ScytheCognitionRuntime;
  window.ScytheCognition = ScytheCognition;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => ScytheCognition.init());
  } else {
    ScytheCognition.init();
  }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { ScytheCognitionRuntime, ScytheCognition };
}
