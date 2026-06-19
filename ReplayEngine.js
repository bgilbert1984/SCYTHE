/**
 * ReplayEngine.js — Deterministic Event Replay & Forensic Reconstruction
 *
 * Enables forensic replay of the causality graph:
 *   - Load events from storage (JSON, IndexedDB, API)
 *   - Replay at variable speeds
 *   - Scrub through time
 *   - Reconstruct state at any point in history
 *   - Compare live vs. replay (for validation)
 *
 * Guarantees:
 *   - Event ordering is deterministic (sorted by ts)
 *   - State reconstruction is idempotent (same events → same state)
 *   - No mutation of stored events (immutable log)
 */

class ReplayEngine {
  /**
   * @param {IngressCausalStore} causalStore - Destination for replayed events
   * @param {SimulationClock} clock - Time authority
   * @param {Object} opts
   */
  constructor(causalStore, clock, opts = {}) {
    this.causalStore = causalStore;
    this.clock = clock;

    this.eventLog = [];      // Loaded events (sorted by ts)
    this.replayState = null; // Current state snapshot during replay

    this.isReplaying = false;
    this.replayStartTime = 0;
    this.replayEndTime = 0;
    this.replaySpeed = 1.0;

    // Callbacks
    this.onStateChange = opts.onStateChange ?? (() => {});
    this.onReplayStart = opts.onReplayStart ?? (() => {});
    this.onReplayEnd = opts.onReplayEnd ?? (() => {});
    this.onSeek = opts.onSeek ?? (() => {});

    // For comparing live vs. replay
    this.comparisonMode = false;
    this.liveStateSnapshot = null;
    this.replayStateSnapshot = null;
  }

  /**
   * Load events from JSON (e.g., exported from live session)
   */
  loadEventsFromJSON(jsonData) {
    if (typeof jsonData === 'string') {
      jsonData = JSON.parse(jsonData);
    }

    const { events, edges } = jsonData;
    if (!Array.isArray(events)) {
      throw new Error('Invalid JSON: missing "events" array');
    }

    // Clear existing events
    this.eventLog = [];
    this.causalStore = new IngressCausalStore({ maxEvents: events.length + 1000 });

    // Sort events by timestamp
    events.sort((a, b) => a.ts - b.ts);

    // Add to store (immutably)
    for (const eventData of events) {
      const evt = new IngressEvent({
        ts: eventData.ts,
        entity_id: eventData.entity_id,
        type: eventData.type,
        value: eventData.value,
        confidence: eventData.confidence,
        provenance: eventData.provenance,
        causal_parents: eventData.causal_parents,
        is_forensic: true,  // Mark as replayed
      });
      this.causalStore.addEvent(evt);
      this.eventLog.push(evt);
    }

    // Reconstruct causality edges if provided
    if (Array.isArray(edges)) {
      for (const edge of edges) {
        this.causalStore.addCausalEdge(
          edge.source_id,
          edge.target_id,
          edge.relationship,
          edge.confidence
        );
      }
    }

    console.log(`[ReplayEngine] Loaded ${this.eventLog.length} events (${this.eventLog[0]?.ts} to ${this.eventLog[this.eventLog.length - 1]?.ts})`);
    return this.eventLog;
  }

  /**
   * Fetch events from API endpoint
   * @param {string} apiUrl - GET endpoint that returns { events, edges }
   */
  async loadEventsFromAPI(apiUrl) {
    try {
      const response = await fetch(apiUrl);
      if (!response.ok) {
        throw new Error(`API returned ${response.status}`);
      }
      const jsonData = await response.json();
      return this.loadEventsFromJSON(jsonData);
    } catch (err) {
      console.error('[ReplayEngine] API load failed:', err);
      throw err;
    }
  }

  /**
   * Start replay from the loaded event log
   */
  startReplay(speed = 1.0) {
    if (this.eventLog.length === 0) {
      console.warn('[ReplayEngine] No events loaded');
      return false;
    }

    if (this.isReplaying) {
      console.warn('[ReplayEngine] Already replaying');
      return false;
    }

    // Configure replay time range
    const firstEvent = this.eventLog[0];
    const lastEvent = this.eventLog[this.eventLog.length - 1];

    this.replayStartTime = firstEvent.ts;
    this.replayEndTime = lastEvent.ts;
    this.replaySpeed = Math.max(0.1, speed);

    // Initialize clock for replay
    this.clock.enterReplayMode(this.replayStartTime, this.replayEndTime);
    this.clock.setTimeScale(this.replaySpeed);
    this.clock.start();

    this.isReplaying = true;
    this.replayState = this._createStateSnapshot(this.replayStartTime);

    this.onReplayStart({
      startTime: this.replayStartTime,
      endTime: this.replayEndTime,
      eventCount: this.eventLog.length,
      speed: this.replaySpeed,
    });

    console.log(`[ReplayEngine] Replay started: ${this.eventLog.length} events, speed=${speed}x`);
    return true;
  }

  /**
   * Stop replay and return to live
   */
  stopReplay() {
    if (!this.isReplaying) return false;

    this.clock.exitReplayMode();
    this.clock.stop();
    this.isReplaying = false;

    this.onReplayEnd({
      endTime: this.clock.simTime,
    });

    console.log('[ReplayEngine] Replay stopped');
    return true;
  }

  /**
   * Seek to a specific time during replay
   */
  seekToTime(targetTime) {
    if (!this.isReplaying) {
      console.warn('[ReplayEngine] Cannot seek outside replay mode');
      return false;
    }

    if (targetTime < this.replayStartTime || targetTime > this.replayEndTime) {
      console.warn('[ReplayEngine] Seek target outside range');
      return false;
    }

    const seeked = this.clock.seekToSimTime(targetTime);
    if (seeked) {
      this.replayState = this._createStateSnapshot(targetTime);
      this.onSeek({ targetTime, state: this.replayState });
    }

    return seeked;
  }

  /**
   * Seek relative to current position
   */
  seekRelative(offsetMs) {
    return this.seekToTime(this.clock.simTime + offsetMs);
  }

  /**
   * Set replay speed
   */
  setReplaySpeed(speed) {
    this.replaySpeed = Math.max(0.1, speed);
    this.clock.setTimeScale(this.replaySpeed);
  }

  /**
   * Get events in a time range
   */
  getEventsInRange(startTime, endTime) {
    return this.eventLog.filter(e => e.ts >= startTime && e.ts <= endTime);
  }

  /**
   * Get events for a specific entity
   */
  getEventsForEntity(entityId) {
    return this.eventLog.filter(e => e.entity_id === entityId);
  }

  /**
   * Create a state snapshot at a specific time
   * (Reconstruct what the system state was at this moment)
   */
  _createStateSnapshot(atTime) {
    const snapshot = {
      timestamp: atTime,
      events: [],
      entityStates: {},
      eventTypes: {},
    };

    // Collect all events up to this time
    for (const evt of this.eventLog) {
      if (evt.ts > atTime) break;

      snapshot.events.push(evt);

      // Track per-entity state
      if (!snapshot.entityStates[evt.entity_id]) {
        snapshot.entityStates[evt.entity_id] = {
          entity_id: evt.entity_id,
          lastEvent: null,
          eventCount: 0,
          eventTypes: [],
        };
      }

      const entity = snapshot.entityStates[evt.entity_id];
      entity.lastEvent = evt;
      entity.eventCount++;
      if (!entity.eventTypes.includes(evt.type)) {
        entity.eventTypes.push(evt.type);
      }

      // Track event type frequencies
      if (!snapshot.eventTypes[evt.type]) {
        snapshot.eventTypes[evt.type] = 0;
      }
      snapshot.eventTypes[evt.type]++;
    }

    return snapshot;
  }

  /**
   * Get current replay state
   */
  getCurrentState() {
    if (!this.isReplaying) return null;
    return this._createStateSnapshot(this.clock.simTime);
  }

  /**
   * Export events for transfer/backup
   */
  exportEvents() {
    return {
      metadata: {
        exportTime: new Date().toISOString(),
        eventCount: this.eventLog.length,
        timeRange: this.eventLog.length > 0 ? {
          start: this.eventLog[0].ts,
          end: this.eventLog[this.eventLog.length - 1].ts,
        } : null,
      },
      data: this.causalStore.exportCausalityGraph(),
    };
  }

  /**
   * Compare replay state with a live snapshot (for validation)
   */
  compareWithLiveSnapshot(liveSnapshot) {
    if (!this.replayState) return null;

    const comparison = {
      replayTime: this.replayState.timestamp,
      replayEventCount: this.replayState.events.length,
      liveEventCount: liveSnapshot.events?.length ?? 0,
      entitiesInReplay: Object.keys(this.replayState.entityStates).length,
      entitiesInLive: Object.keys(liveSnapshot.entityStates ?? {}).length,
      divergences: [],
    };

    // Find entities that diverge
    for (const entityId in this.replayState.entityStates) {
      const replayEntity = this.replayState.entityStates[entityId];
      const liveEntity = liveSnapshot.entityStates?.[entityId];

      if (!liveEntity) {
        comparison.divergences.push({
          type: 'missing_in_live',
          entityId,
          replayEventCount: replayEntity.eventCount,
        });
      } else if (replayEntity.eventCount !== liveEntity.eventCount) {
        comparison.divergences.push({
          type: 'event_count_mismatch',
          entityId,
          replayCount: replayEntity.eventCount,
          liveCount: liveEntity.eventCount,
        });
      }
    }

    return comparison;
  }

  /**
   * Get replay progress (0-1)
   */
  getProgress() {
    if (!this.isReplaying) return 0;
    const range = this.replayEndTime - this.replayStartTime;
    if (range === 0) return 0;
    return (this.clock.simTime - this.replayStartTime) / range;
  }

  /**
   * Get detailed status
   */
  getStatus() {
    return {
      isReplaying: this.isReplaying,
      eventCount: this.eventLog.length,
      timeRange: this.eventLog.length > 0 ? {
        start: this.eventLog[0].ts,
        end: this.eventLog[this.eventLog.length - 1].ts,
        duration: this.eventLog[this.eventLog.length - 1]?.ts - this.eventLog[0]?.ts,
      } : null,
      currentTime: this.clock.simTime,
      progress: this.getProgress(),
      speed: this.replaySpeed,
      clockStatus: this.clock.getStatus(),
    };
  }
}

// Export
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    ReplayEngine,
  };
}
