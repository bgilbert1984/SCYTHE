/**
 * Frontend metrics logging as a workspace tool (the fourth tool on the lifecycle contract in lifecycle.js, and the first one
 * moved out of command-ops-visualization.html only to make that file smaller: nothing about what it logs changed).
 *
 * This is the page's MetricsLogger. While active it batches `log(...)` calls and posts them to `/api/metrics/log`, posts each
 * `logInteraction(...)` to `/api/metrics/interaction`, collects the page's gauges every `logInterval`, and flushes when the
 * page unloads. Deactivating stops the collection interval and the unload hook, then flushes what is left. (The old logger
 * had no teardown: its interval and its `beforeunload` listener were never released.)
 *
 * The data outlives an activation: the session id, the buffer, and the in-memory interaction log (the Metrics Explorer reads
 * `_interactionLog` and `_lastMetrics`) are kept across deactivate/activate. While inactive the controls do nothing.
 *
 * Kept as it was, including two things that look unintended (see the tests that pin them):
 *  - A flush does not check the HTTP status: only a thrown error puts the batch back, so a 500 drops it.
 *  - `session_end` reports seconds since the logger was created (page load), because `lastLogTime` is never advanced.
 *
 * Services it needs (see `requires`):
 *   httpFetch     (url, init?) => Promise<Response>   the page's fetch, with its instance-relative rewriting
 *   metricsProbe  {unloadTarget, environment() => {userAgent, screenWidth, screenHeight}, gauges({performance, hypergraph}) => iterable of {module, name, value}}
 *                 `unloadTarget` is what fires `beforeunload` (the window). `gauges` reads the page's globals (recon, AIS, Cesium,
 *                 render, viewshed, hypergraph, browser memory) and may throw part way: what it yielded before that is kept
 */

export const METRICS_ID = "metrics";
const KEPT_INTERACTIONS = 100;

export function createMetricsPlugin({log = console, now = () => Date.now(), random = Math.random} = {}) {
  const sessionId = `session_${now()}_${random().toString(36).slice(2, 11)}`;
  const config = {
    enabled: true,
    logInterval: 30000,      // collect every 30 seconds
    batchSize: 50,           // most metrics per request
    logUserInteractions: true,
    logPerformance: true,
    logHypergraph: true,
  };
  const startedAt = now();
  const buffer = [];
  let interactionLog = [];   // replaced when trimmed: read it through the controls, do not hold it
  const lastMetrics = {};
  let run = null;            // null while the tool is inactive

  const tool = {
    id: METRICS_ID,
    title: "Metrics",
    requires: ["httpFetch", "metricsProbe"],
    activate(ctx) {
      const http = ctx.services.get("httpFetch");
      const probe = ctx.services.get("metricsProbe");

      function record(module, metricName, value, metadata = {}) {
        if (!config.enabled) return;
        buffer.push({module, metric_name: metricName, value, metadata, timestamp: now()});
        lastMetrics[metricName] = value;
        if (buffer.length >= config.batchSize) flush();
      }

      function recordInteraction(action, target, details = {}) {
        if (!config.enabled || !config.logUserInteractions) return;
        interactionLog.push({action, target, details, timestamp: now()});
        if (interactionLog.length > KEPT_INTERACTIONS) interactionLog = interactionLog.slice(-KEPT_INTERACTIONS);
        http("/api/metrics/interaction", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({action, target, details, session_id: sessionId}),
        }).catch((error) => log.warn?.("[MetricsLogger] Interaction log failed:", error));
      }

      async function flush() {
        if (buffer.length === 0) return;
        const batch = buffer.splice(0, config.batchSize);
        try {
          await http("/api/metrics/log", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({batch, session_id: sessionId}),
          });
          log.log?.(`[MetricsLogger] Flushed ${batch.length} metrics`);
        } catch (error) {
          log.warn?.("[MetricsLogger] Flush failed:", error);
          buffer.unshift(...batch);   // keep it for the next try
        }
      }

      async function collect() {
        if (!config.enabled) return;
        try {
          for (const {module, name, value} of probe.gauges({performance: config.logPerformance, hypergraph: config.logHypergraph})) {
            record(module, name, value);
          }
          await flush();
        } catch (error) {
          log.warn?.("[MetricsLogger] Error collecting metrics:", error);
        }
      }

      async function getJson(url) {
        const response = await http(url);
        return response.json();
      }

      // Registered first so it runs last: the interval and the unload hook are already gone when the final flush goes out.
      ctx.onCleanup(flush);
      ctx.interval(collect, config.logInterval);
      ctx.listen(probe.unloadTarget, "beforeunload", () => {
        flush();
        recordInteraction("session_end", "app", {duration_seconds: (now() - startedAt) / 1000});
      });
      ctx.onCleanup(() => { run = null; });

      run = {
        record, recordInteraction, flush, collect,
        sessionSummary: async () => {
          try { return await getJson("/api/metrics/session"); } catch (error) { log.error?.("[MetricsLogger] Error getting session summary:", error); return null; }
        },
        query: async (options = {}) => {
          try {
            const params = new URLSearchParams();
            for (const key of ["module", "metric_name", "start_time", "end_time", "limit"]) if (options[key]) params.append(key, options[key]);
            return await getJson(`/api/metrics/query?${params}`);
          } catch (error) { log.error?.("[MetricsLogger] Error querying metrics:", error); return null; }
        },
      };
      recordInteraction("session_start", "app", {...probe.environment(), timestamp: new Date(now()).toISOString()});
    },
  };

  /** What the page calls (`window.metricsLogger`, `window.logMetric`, `window.logInteraction`). Each does nothing while inactive. */
  const controls = {
    sessionId,
    config,
    get _interactionLog() { return interactionLog; },
    get _lastMetrics() { return lastMetrics; },
    log: (module, metricName, value, metadata) => run?.record(module, metricName, value, metadata),
    logInteraction: (action, target, details) => run?.recordInteraction(action, target, details),
    flush: () => run?.flush(),
    collectAndLogMetrics: () => run?.collect(),
    getSessionSummary: () => (run ? run.sessionSummary() : Promise.resolve(null)),
    queryMetrics: (options) => (run ? run.query(options) : Promise.resolve(null)),
  };

  return {tool, controls};
}
