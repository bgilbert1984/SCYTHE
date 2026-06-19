/**
 * TelemetryToEventAdapter.js — Bridges raw telemetry → event generation
 *
 * Sits between the WebSocket/gRPC telemetry streams and the topology engine,
 * converting telemetry deltas into semantic events that flow through the
 * causality layer.
 *
 * Usage:
 *   const adapter = new TelemetryToEventAdapter(causalStore, clock, eventInferenceEngine);
 *   adapter.on('telemetry-update', (telemetry) => {
 *     const events = adapter.processUpdate(telemetry);
 *     // Events are automatically added to causalStore; dispatch to UI as needed
 *   });
 */

class TelemetryToEventAdapter {
  /**
   * @param {IngressCausalStore} causalStore - Destination event store
   * @param {SimulationClock} clock - Time authority
   * @param {EventInferenceEngine} inferenceEngine - Event classification
   * @param {Object} opts
   */
  constructor(causalStore, clock, inferenceEngine, opts = {}) {
    this.causalStore = causalStore;
    this.clock = clock;
    this.inferenceEngine = inferenceEngine;

    // Telemetry history (to detect changes between frames)
    this.telemetryHistory = new Map();  // entity_id → previous telemetry state

    // Event emitter
    this.listeners = {
      'event-generated': [],
      'anomaly-detected': [],
      'state-changed': [],
    };

    // Configuration
    this.maxHistoryAge = opts.maxHistoryAge ?? 300000;  // 5 minutes
    this.inferenceOptions = {
      spikeThreshold: opts.spikeThreshold ?? 0.5,
      entropyShiftThreshold: opts.entropyShiftThreshold ?? 0.25,
      anomalyScoreThreshold: opts.anomalyScoreThreshold ?? 0.7,
    };
  }

  /**
   * Process a telemetry update and generate events
   * @param {Object} telemetry - Raw telemetry (iface_id, rx_mbps, role, entropy, etc.)
   * @returns {Array<IngressEvent>} — Events generated from this update
   */
  processUpdate(telemetry) {
    const { entity_id } = telemetry;
    if (!entity_id) {
      console.warn('[TelemetryAdapter] Telemetry missing entity_id, skipping');
      return [];
    }

    // Retrieve previous state
    const prev = this.telemetryHistory.get(entity_id);
    const curr = {
      is_active: telemetry.is_active ?? true,
      rx_mbps: telemetry.rx_mbps ?? telemetry.bytesPerSec ? (telemetry.bytesPerSec * 8 / 1e6) : 0,
      role: telemetry.role,
      role_confidence: telemetry.role_confidence,
      spectral_entropy: telemetry.spectral_entropy,
      anomaly_score: telemetry.anomaly_score ?? 0,
      host_id: telemetry.host_id,
      timestamp: this.clock.simTime,
    };

    // Generate events from telemetry delta
    const events = this.inferenceEngine.inferEventsFromTelemetryDelta(entity_id, prev, curr);

    // Check for anomaly (higher-level inference)
    if (curr.anomaly_score > this.inferenceOptions.anomalyScoreThreshold) {
      const anomalyEvent = this.inferenceEngine.inferAnomalyEvent(
        curr.host_id || entity_id,
        curr.anomaly_score,
        { interface: entity_id, metrics: curr }
      );
      if (anomalyEvent) {
        events.push(anomalyEvent);
        this._emit('anomaly-detected', anomalyEvent);
      }
    }

    // Store current state as history
    this.telemetryHistory.set(entity_id, curr);

    // Stamp simulation time before persistence (wall-clock is ingestion metadata only)
    for (const event of events) {
      event.ts = this.clock.simTime;
      const eventId = this.causalStore.addEvent(event);
      this._emit('event-generated', event);
    }

    // Emit state change notification
    if (events.length > 0) {
      this._emit('state-changed', {
        entity_id,
        telemetry: curr,
        events: events,
      });
    }

    return events;
  }

  /**
   * Batch-process multiple telemetry updates
   * @param {Array<Object>} telemetryBatch
   * @returns {Array<IngressEvent>} — All events generated
   */
  processBatch(telemetryBatch) {
    const allEvents = [];
    for (const telemetry of telemetryBatch) {
      const events = this.processUpdate(telemetry);
      allEvents.push(...events);
    }
    return allEvents;
  }

  /**
   * Query the causality graph for an entity
   */
  getEntityHistory(entity_id) {
    return this.causalStore.getEventsByEntity(entity_id);
  }

  /**
   * Get all events of a specific type
   */
  getEventsByType(type) {
    return this.causalStore.getEventsByType(type);
  }

  /**
   * Dump current telemetry state (for debugging)
   */
  getTelemetrySnapshot() {
    const snapshot = {};
    for (const [entity_id, telemetry] of this.telemetryHistory) {
      snapshot[entity_id] = telemetry;
    }
    return snapshot;
  }

  /**
   * Register event listener
   */
  on(eventName, callback) {
    if (!this.listeners[eventName]) {
      this.listeners[eventName] = [];
    }
    this.listeners[eventName].push(callback);
  }

  /**
   * Unregister event listener
   */
  off(eventName, callback) {
    if (!this.listeners[eventName]) return;
    const idx = this.listeners[eventName].indexOf(callback);
    if (idx >= 0) {
      this.listeners[eventName].splice(idx, 1);
    }
  }

  /**
   * Internal: emit event to listeners
   */
  _emit(eventName, data) {
    if (!this.listeners[eventName]) return;
    for (const callback of this.listeners[eventName]) {
      try {
        callback(data);
      } catch (err) {
        console.error(`[TelemetryAdapter] Listener error for '${eventName}':`, err);
      }
    }
  }

  /**
   * Get adapter status (for monitoring)
   */
  getStatus() {
    return {
      storeSize: this.causalStore.getStats(),
      telemetryHistorySize: this.telemetryHistory.size,
      activeEntities: Array.from(this.telemetryHistory.keys()),
    };
  }
}

/**
 * CesiumClockAdapter — Integrates SimulationClock into Cesium rendering loop
 *
 * Manages the connection between:
 *   - requestAnimationFrame (Cesium viewer tick)
 *   - SimulationClock (deterministic time)
 *   - Event ingestion queue (time-keyed processing)
 */
class CesiumClockAdapter {
  constructor(clock, eventQueue, opts = {}) {
    this.clock = clock;
    this.eventQueue = eventQueue;
    this.onUpdate = opts.onUpdate ?? (() => {});
    this.animationId = null;
    this.frameCount = 0;
    this.isRunning = false;
  }

  /**
   * Start the animation loop
   */
  start() {
    if (this.isRunning) return;
    this.isRunning = true;
    this.frameCount = 0;
    this.clock.start();
    this._tick();
    console.log('[CesiumClockAdapter] Started');
  }

  /**
   * Stop the animation loop
   */
  stop() {
    if (!this.isRunning) return;
    this.isRunning = false;
    if (this.animationId) {
      cancelAnimationFrame(this.animationId);
    }
    this.clock.stop();
    console.log('[CesiumClockAdapter] Stopped');
  }

  /**
   * Internal: animation frame tick
   */
  _tick() {
    if (!this.isRunning) return;

    // Update simulation time
    const dt = this.clock.update();

    // Process any events that are ready (time has advanced past their ts)
    const readyEvents = this.eventQueue.processUpTo(this.clock.simTime);
    if (readyEvents.length > 0) {
      this.onUpdate({
        type: 'events-ready',
        events: readyEvents,
        simTime: this.clock.simTime,
      });
    }

    // Notify caller (topology update, render, etc.)
    this.onUpdate({
      type: 'frame',
      dt: dt,
      simTime: this.clock.simTime,
      frameNumber: this.clock.frameNumber,
    });

    // Check for replay end
    if (this.clock.isReplay && this.clock.isReplayComplete()) {
      this.onUpdate({
        type: 'replay-complete',
        endTime: this.clock.simTime,
      });
    }

    this.frameCount++;
    this.animationId = requestAnimationFrame(() => this._tick());
  }

  /**
   * Get status
   */
  getStatus() {
    return {
      isRunning: this.isRunning,
      frameCount: this.frameCount,
      clockStatus: this.clock.getStatus(),
      eventQueueStatus: this.eventQueue.getStats(),
    };
  }
}

// Export for use in browser/Node.js
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    TelemetryToEventAdapter,
    CesiumClockAdapter,
  };
}
