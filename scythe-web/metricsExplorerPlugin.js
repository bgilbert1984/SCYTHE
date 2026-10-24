/**
 * The Metrics Explorer panel as a workspace tool (the fifth tool on the lifecycle contract in lifecycle.js; the second moved out of
 * command-ops-visualization.html only to make that file smaller).
 *
 * While active it fills the panel from the server's `/api/metrics/session` and `/api/metrics/query`, refreshes every 30 s while the
 * panel is showing, and refreshes on the Refresh button and the three filters. Deactivating removes the button and filter listeners
 * and the refresh interval, and a response that lands afterwards is dropped (the old object kept its listeners forever and only
 * its `destroy()` cleared the interval, which nothing called).
 *
 * What it shows is decided here as plain strings (`render*`), and the page's elements are touched only by the `explorerView` service.
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - Every interactions refresh first requests `/api/metrics/query?module=interaction&limit=20` and ignores the answer; if that
 *    request throws, the interactions list is not updated. The list itself comes from the in-memory log of `window.metricsLogger`.
 *  - A failed session request or a body that is not JSON ends the refresh there: the table and live stats are not updated.
 *    A non-OK session response only skips the summary.
 *  - A filtered query that is not OK still goes on to refresh the interactions; one that throws does not.
 *  - A zero heap or update time shows as '--' (the old code tested truthiness).
 *  - The recon-counter and browser-heap fallback in the live stats only runs when `window.metricsLogger` is absent. On the page it never
 *    is (the logger always has a `_lastMetrics`, empty at first, and an empty object is truthy), so it never ran before either.
 *
 * One deliberate change: text that came from the server or from user input (module and metric names, interaction action and
 * target) is HTML-escaped. The old code put it into innerHTML as it was, so a scan target typed as `<img onerror=...>` ran.
 *
 * Services it needs (see `requires`):
 *   httpFetch     (url, init?) => Promise<Response>
 *   explorerView  the panel's DOM: see `workspaceBoot.js`
 *   liveSources   {interactionLog() => array|null, lastMetrics() => object|null, recon() => the Auto-Recon visualization|null, heapBytes() => number|null}
 */

export const METRICS_EXPLORER_ID = "metrics-explorer";
const REFRESH_MS = 30000;
const SHOWN_INTERACTIONS = 10;
const MODULE_COLORS = {recon: "#4a9eff", browser: "#00d4ff", ais: "#ff9800", render: "#4caf50", viewshed: "#9c27b0"};

export const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const clock = (when) => new Date(when).toLocaleTimeString("en-US", {hour12: false});

export function formatDuration(seconds) {
  const duration = seconds || 0;
  return duration > 3600 ? `${Math.floor(duration / 3600)}h ${Math.floor((duration % 3600) / 60)}m`
    : duration > 60 ? `${Math.floor(duration / 60)}m ${Math.floor(duration % 60)}s`
    : `${Math.floor(duration)}s`;
}

/** Per-module metric counts and their total, from the session summary's `{ "module.metric": {count} }`. */
export function summarize(metrics) {
  const moduleCounts = {};
  let total = 0;
  for (const [key, stats] of Object.entries(metrics || {})) {
    const module = key.split(".")[0];
    total += stats.count || 0;
    moduleCounts[module] = (moduleCounts[module] || 0) + (stats.count || 0);
  }
  return {total, moduleCounts};
}

/** The metric names for the filter, from `module.metric.name` keys (a name may itself contain dots). */
export function metricNames(metrics) {
  const names = new Set();
  for (const key of Object.keys(metrics || {})) {
    const parts = key.split(".");
    if (parts.length > 1) names.add(parts.slice(1).join("."));
  }
  return [...names].sort();
}

export function renderModuleBars(moduleCounts) {
  const maxCount = Math.max(...Object.values(moduleCounts), 1);
  return Object.entries(moduleCounts).sort((a, b) => b[1] - a[1]).map(([module, count]) => {
    const pct = (count / maxCount * 100).toFixed(0);
    return `
                            <div class="module-bar">
                                <span class="module-bar-label">${escapeHtml(module)}</span>
                                <div class="module-bar-fill">
                                    <div class="module-bar-value" style="width: ${pct}%; background: ${MODULE_COLORS[module] || "#888"};"></div>
                                </div>
                                <span class="module-bar-count">${count.toLocaleString()}</span>
                            </div>
                        `;
  }).join("");
}

export function renderMetricsRows(metrics) {
  if (!metrics || metrics.length === 0) return '<tr><td colspan="4" style="text-align: center; color: #888;">No metrics found</td></tr>';
  return metrics.map((m) => {
    const value = typeof m.value === "number" ? (Number.isInteger(m.value) ? m.value : m.value.toFixed(2)) : m.value;
    return `
                        <tr>
                            <td class="metric-time-cell">${clock(m.timestamp * 1000)}</td>
                            <td class="metric-module-cell">${escapeHtml(m.module)}</td>
                            <td>${escapeHtml(m.metric_name)}</td>
                            <td class="metric-value-cell">${escapeHtml(value)}</td>
                        </tr>
                    `;
  }).join("");
}

export function renderInteractions(interactions) {
  return interactions.map((i) => `
                                    <div class="interaction-item">
                                        <span class="interaction-time">${clock(i.timestamp)}</span>
                                        <span class="interaction-action">
                                            <strong>${escapeHtml(i.action)}</strong>
                                            ${i.target ? `<span class="interaction-target">→ ${escapeHtml(i.target)}</span>` : ""}
                                        </span>
                                    </div>
                                `).reverse().join("");
}

const NO_INTERACTIONS = '<div style="color: #888; text-align: center; padding: 10px; font-size: 10px;">Interactions logged to SQLite - check server logs</div>';

export function createMetricsExplorerPlugin({log = console} = {}) {
  let run = null;   // null while the tool is inactive

  const tool = {
    id: METRICS_EXPLORER_ID,
    title: "Metrics Explorer",
    requires: ["httpFetch", "explorerView", "liveSources"],
    activate(ctx) {
      const http = ctx.services.get("httpFetch");
      const view = ctx.services.get("explorerView");
      const live = ctx.services.get("liveSources");
      const stale = () => ctx.signal.aborted;   // deactivated while a request was in flight

      function showSession(data) {
        view.setText("session-duration", formatDuration(data.session_duration_seconds));
        const metrics = data.metrics || {};
        const {total, moduleCounts} = summarize(metrics);
        view.setText("total-metrics-count", total.toLocaleString());
        view.setHtml("module-bars", renderModuleBars(moduleCounts));
        view.setMetricNames(metricNames(metrics));
      }

      async function showInteractions() {
        try {
          await http("/api/metrics/query?module=interaction&limit=20");   // the answer is not used; see the header
          if (stale()) return;
          const logged = live.interactionLog();
          if (logged) {
            const recent = logged.slice(-SHOWN_INTERACTIONS);
            if (recent.length > 0) {
              if (!view.setHtml("interactions-list", renderInteractions(recent))) return;
              view.setText("total-interactions-count", logged.length.toString());
              return;
            }
          }
          if (!view.setHtml("interactions-list", NO_INTERACTIONS)) return;
          view.setText("total-interactions-count", "--");
        } catch (error) {
          log.error?.("[MetricsExplorer] Error fetching interactions:", error);
        }
      }

      async function fetchMetrics() {
        try {
          const {module, name, limit} = view.filters();
          let url = `/api/metrics/query?limit=${limit}`;
          if (module) url += `&module=${encodeURIComponent(module)}`;
          if (name) url += `&metric_name=${encodeURIComponent(name)}`;
          const response = await http(url);
          const data = response.ok ? await response.json() : undefined;
          if (stale()) return;
          if (response.ok) {
            view.setHtml("metrics-table-body", renderMetricsRows(data.metrics || data));
            if (data.metrics) view.setText("total-metrics-count", (data.total_count || data.metrics.length).toLocaleString());
          }
          await showInteractions();
        } catch (error) {
          log.error?.("[MetricsExplorer] Error fetching filtered metrics:", error);
        }
      }

      function showLiveStats() {
        const last = live.lastMetrics();
        if (last) {
          view.setText("live-heap-used", last.heap_used_mb ? `${last.heap_used_mb.toFixed(1)} MB` : "--");
          view.setText("live-entity-count", String(last.entity_count ?? "--"));
          view.setText("live-vessel-count", String(last.vessel_count ?? "--"));
          view.setText("live-update-rate", last.avg_update_ms ? `${last.avg_update_ms.toFixed(1)} ms` : "--");
          return;
        }
        const recon = live.recon();
        if (recon) {
          view.setText("live-entity-count", String(recon.perfMetrics?.entityUpdates ?? "--"));
          view.setText("live-update-rate", recon.perfMetrics?.avgUpdateMs ? `${recon.perfMetrics.avgUpdateMs.toFixed(1)} ms` : "--");
        }
        const heap = live.heapBytes();
        if (heap !== null) view.setText("live-heap-used", `${(heap / 1024 / 1024).toFixed(1)} MB`);
      }

      async function fetchAll() {
        try {
          const response = await http("/api/metrics/session");
          const data = response.ok ? await response.json() : undefined;
          if (stale()) return;
          if (response.ok) showSession(data);
          await fetchMetrics();
          if (stale()) return;
          showLiveStats();
        } catch (error) {
          log.error?.("[MetricsExplorer] Error fetching metrics:", error);
        }
      }

      ctx.onCleanup(view.bind({refresh: fetchAll, filtersChanged: fetchMetrics}));
      ctx.interval(() => { if (view.panelActive()) fetchAll(); }, REFRESH_MS);
      ctx.onCleanup(() => { run = null; });
      run = {fetchAll, fetchMetrics};
      fetchAll();
    },
  };

  /** What the page calls (`showPanel('metrics')`). Each does nothing while the tool is inactive. */
  const controls = {
    fetchAllMetrics: () => run?.fetchAll(),
    fetchMetrics: () => run?.fetchMetrics(),
  };

  return {tool, controls};
}
