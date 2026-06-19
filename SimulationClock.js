/**
 * SimulationClock.js — Unified Time Authority (Layer 4)
 *
 * Provides deterministic, delta-time-normalized time for all subsystems:
 *   - Event ingestion queue (timestamp normalization)
 *   - Physics integration (fixed dt steps)
 *   - Rendering (frame-locked sampling)
 *   - Replay (time scrubbing)
 *
 * Architecture:
 *   - simTime: virtual simulation time (milliseconds), independent of wall-clock
 *   - wallTime: actual performance.now() (milliseconds)
 *   - dt: time delta since last frame (fixed when in deterministic mode)
 *   - timeScale: multiplier to speed up/slow down simulation relative to wall-clock
 *   - isReplay: boolean flag (true = time-scrubbing mode, false = live)
 *   - replayIndex: current playback position in event log (when isReplay=true)
 *
 * Usage:
 *   const clock = new SimulationClock();
 *   clock.start();
 *
 *   // Per frame:
 *   const dt = clock.update();
 *   physicsEngine.integrate(dt);
 *   eventQueue.processUpTo(clock.simTime);
 *   renderer.render(clock.simTime);
 *
 *   // Replay mode:
 *   clock.enterReplayMode();
 *   clock.seekToSimTime(1000);  // jump to t=1000ms
 *   while (clock.simTime < replayEnd) {
 *     clock.update();
 *     ... process events, render at clock.simTime
 *   }
 */

class SimulationClock {
  /**
   * @param {Object} opts
   * @param {number} opts.timeScale - Multiplier for sim time relative to wall-clock (default: 1.0)
   * @param {number} opts.fixedDt - Fixed delta-time per frame in deterministic mode (default: 0.016, ~60 Hz)
   * @param {number} opts.replayMode - Start in replay mode (default: false)
   */
  constructor(opts = {}) {
    this.timeScale = opts.timeScale ?? 1.0;
    this.fixedDt = opts.fixedDt ?? 0.016;  // 16ms = ~60 Hz
    this.fixedDtMs = this.fixedDt * 1000;

    // Simulation time (milliseconds)
    this.simTime = 0;
    this.wallTime = performance.now();
    this.dt = 0;  // Time delta since last frame (seconds)
    this.dtMs = 0; // Time delta in milliseconds

    // Playback control
    this.isRunning = false;
    this.isReplay = opts.replayMode ?? false;
    this.replayIndex = 0;
    this.replayStartTime = 0;
    this.replayEndTime = Infinity;

    // Frame tracking
    this.frameNumber = 0;
    this.lastUpdateTime = 0;

    // History (for forensic analysis)
    this.timeHistory = [];
    this.maxHistorySize = opts.maxHistorySize ?? 10000;

    // Listeners for time events
    this._onReplayEnd = [];
    this._onReplaySeek = [];
  }

  /**
   * Start the clock (must call before first update)
   */
  start() {
    if (this.isRunning) return;
    this.isRunning = true;
    this.wallTime = performance.now();
    this.simTime = 0;
    this.dt = 0;
    this.frameNumber = 0;
    console.log('[SimulationClock] Started', { timeScale: this.timeScale, replayMode: this.isReplay });
  }

  /**
   * Stop the clock
   */
  stop() {
    this.isRunning = false;
    console.log('[SimulationClock] Stopped at simTime', this.simTime);
  }

  /**
   * Main update loop — call once per frame
   * @returns {number} dt in seconds
   */
  update() {
    if (!this.isRunning) return 0;

    const now = performance.now();

    if (this.isReplay) {
      // Replay mode: dt is fixed
      this.dt = this.fixedDt;
      this.dtMs = this.fixedDtMs;
    } else {
      // Live mode: measure dt from wall-clock
      if (this.frameNumber === 0) {
        // First frame: use fixed dt as baseline
        this.dtMs = this.fixedDtMs;
      } else {
        // Subsequent frames: measure from previous frame, apply timeScale
        this.dtMs = Math.max(1, (now - this.wallTime));
      }
      this.dt = (this.dtMs / 1000) * this.timeScale;
    }

    this.simTime += this.dtMs;  // Always accumulate in milliseconds
    this.wallTime = now;
    this.frameNumber++;
    this.lastUpdateTime = now;

    // Record to history (for temporal analysis)
    this._recordHistory();

    return this.dt;
  }

  /**
   * Enter replay mode (freeze dt, allow seeking)
   */
  enterReplayMode(startTime = 0, endTime = Infinity) {
    this.isReplay = true;
    this.replayStartTime = startTime;
    this.replayEndTime = endTime;
    this.simTime = startTime;
    this.replayIndex = 0;
    console.log('[SimulationClock] Entered replay mode', { start: startTime, end: endTime });
  }

  /**
   * Exit replay mode (resume live time)
   */
  exitReplayMode() {
    this.isReplay = false;
    this.wallTime = performance.now();
    this.simTime = 0;
    this.frameNumber = 0;
    console.log('[SimulationClock] Exited replay mode');
  }

  /**
   * Seek to a specific simulation time (replay mode only)
   */
  seekToSimTime(targetTime) {
    if (!this.isReplay) {
      console.warn('[SimulationClock] seekToSimTime called outside replay mode');
      return false;
    }

    if (targetTime < this.replayStartTime || targetTime > this.replayEndTime) {
      console.warn('[SimulationClock] Seek target outside replay range', {
        target: targetTime,
        range: [this.replayStartTime, this.replayEndTime]
      });
      return false;
    }

    this.simTime = targetTime;
    this._fireReplaySeek({ targetTime });
    return true;
  }

  /**
   * Seek relative to current position (replay mode)
   */
  seekRelative(offsetMs) {
    return this.seekToSimTime(this.simTime + offsetMs);
  }

  /**
   * Pause replay at current time
   */
  pause() {
    this.isRunning = false;
  }

  /**
   * Resume replay/live
   */
  resume() {
    if (!this.isRunning) {
      this.isRunning = true;
      this.wallTime = performance.now();
    }
  }

  /**
   * Set playback speed (timeScale multiplier)
   */
  setTimeScale(scale) {
    this.timeScale = Math.max(0.1, scale);  // Prevent zero/negative
  }

  /**
   * Get current time in seconds (for shader uniforms, etc.)
   */
  getTimeSeconds() {
    return this.simTime / 1000;
  }

  /**
   * Get current time in milliseconds
   */
  getTimeMs() {
    return this.simTime;
  }

  /**
   * Check if we're past the end of replay range
   */
  isReplayComplete() {
    return this.isReplay && this.simTime >= this.replayEndTime;
  }

  /**
   * Record time state to history for temporal analysis
   */
  _recordHistory() {
    this.timeHistory.push({
      frameNumber: this.frameNumber,
      simTime: this.simTime,
      wallTime: this.wallTime,
      dt: this.dt,
      dtMs: this.dtMs,
    });

    if (this.timeHistory.length > this.maxHistorySize) {
      this.timeHistory.shift();
    }
  }

  /**
   * Get time history (for analyzing time drift, etc.)
   */
  getTimeHistory(last_n_frames = 100) {
    const start = Math.max(0, this.timeHistory.length - last_n_frames);
    return this.timeHistory.slice(start);
  }

  /**
   * Compute average dt over recent frames (for perf monitoring)
   */
  getAverageDt(frames = 60) {
    const history = this.getTimeHistory(frames);
    if (history.length === 0) return 0;
    const sum = history.reduce((acc, h) => acc + h.dt, 0);
    return sum / history.length;
  }

  /**
   * Detect if dt is jittering significantly (sign of performance issues)
   */
  detectDtJitter(window = 60, threshold = 0.005) {
    const history = this.getTimeHistory(window);
    if (history.length < 2) return false;

    const dts = history.map(h => h.dt);
    const mean = dts.reduce((a, b) => a + b) / dts.length;
    const variance = dts.reduce((a, d) => a + Math.pow(d - mean, 2)) / dts.length;
    const stdDev = Math.sqrt(variance);

    return stdDev > threshold;
  }

  /**
   * Register callback for replay end event
   */
  onReplayEnd(callback) {
    this._onReplayEnd.push(callback);
  }

  /**
   * Register callback for seek event
   */
  onReplaySeek(callback) {
    this._onReplaySeek.push(callback);
  }

  /**
   * Fire replay-end callbacks
   */
  _fireReplayEnd(event) {
    for (const cb of this._onReplayEnd) {
      try {
        cb(event);
      } catch (err) {
        console.error('[SimulationClock] Replay end callback failed:', err);
      }
    }
  }

  /**
   * Fire replay-seek callbacks
   */
  _fireReplaySeek(event) {
    for (const cb of this._onReplaySeek) {
      try {
        cb(event);
      } catch (err) {
        console.error('[SimulationClock] Replay seek callback failed:', err);
      }
    }
  }

  /**
   * Get detailed status for monitoring
   */
  getStatus() {
    return {
      simTime: this.simTime,
      wallTime: this.wallTime,
      dt: this.dt,
      frameNumber: this.frameNumber,
      timeScale: this.timeScale,
      isRunning: this.isRunning,
      isReplay: this.isReplay,
      replayProgress: this.isReplay ? (this.simTime - this.replayStartTime) / (this.replayEndTime - this.replayStartTime) : null,
      averageDt: this.getAverageDt(60),
      hasJitter: this.detectDtJitter(),
    };
  }
}

/**
 * EventIngestionQueue — Buffers telemetry events keyed to simulation time
 *
 * Decouples telemetry acquisition from event processing:
 *   - Raw telemetry arrives at wall-clock time
 *   - Events are buffered in simTime order
 *   - Processing happens when clock reaches event time (deterministic)
 */
class EventIngestionQueue {
  constructor(clock, opts = {}) {
    this.clock = clock;
    this.queue = [];  // Sorted by ts (simTime)
    this.maxQueueSize = opts.maxQueueSize ?? 100_000;
    this.processedCount = 0;
  }

  /**
   * Enqueue an event for processing at its simTime
   */
  enqueue(event) {
    if (this.queue.length >= this.maxQueueSize) {
      console.warn('[EventIngestionQueue] Queue full, dropping oldest event');
      this.queue.shift();
    }

    // Insert maintaining sort order (find correct position)
    let insertIdx = 0;
    for (let i = 0; i < this.queue.length; i++) {
      if (event.ts < this.queue[i].ts) {
        insertIdx = i;
        break;
      }
      insertIdx = i + 1;
    }
    this.queue.splice(insertIdx, 0, event);
  }

  /**
   * Process all events up to currentTime, return processed events
   * @param {number} currentTime - simTime (milliseconds)
   * @returns {Array<Object>} Events ready for processing
   */
  processUpTo(currentTime) {
    const readyEvents = [];

    while (this.queue.length > 0 && this.queue[0].ts <= currentTime) {
      const event = this.queue.shift();
      readyEvents.push(event);
      this.processedCount++;
    }

    return readyEvents;
  }

  /**
   * Get queue statistics
   */
  getStats() {
    return {
      queuedEvents: this.queue.length,
      processedTotal: this.processedCount,
      oldestEventTs: this.queue.length > 0 ? this.queue[0].ts : null,
      newestEventTs: this.queue.length > 0 ? this.queue[this.queue.length - 1].ts : null,
    };
  }

  /**
   * Clear the queue (for reset/cleanup)
   */
  clear() {
    this.queue = [];
  }
}

// Export for use in browser/Node.js
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    SimulationClock,
    EventIngestionQueue,
  };
}
