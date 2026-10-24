/**
 * The Propagation Analysis panel as a workspace tool (the sixth tool on the lifecycle contract in lifecycle.js; the third moved out of
 * command-ops-visualization.html only to make that file smaller).
 *
 * While active it wires the panel's four buttons and two sliders, shows the atmosphere, and runs the path-loss calculation and the
 * path visualization through RFPropagationManager. Deactivating removes every listener and the paths this tool drew; a calculation
 * that finishes afterwards is dropped (the old object had no way to stop, and nothing stopped it).
 *
 * What the panel shows is decided here as plain strings (`render*`), and the page's elements are touched only by the
 * `propagationView` service; the engine is the `propagationEngine` service.
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - Inputs are read with parseFloat and not validated: an empty box sends NaN to the engine.
 *  - `calculatePathLoss` pushes a result to the history before it displays it, so a result that cannot be displayed (a missing field)
 *    is in the history and the panel shows the error.
 *  - `visualizePath` takes the next path id before it calls the engine, so a failed or empty visualization still uses up an id.
 *  - The history keeps everything and shows the newest ten.
 *
 * Deliberate changes:
 *  - Error text (which can come from the engine or the network) is HTML-escaped. The old code put it into innerHTML as it was.
 *  - A missing panel element is skipped. The old code threw on the first one it needed, which aborted `showPanel('propagation')`.
 *  - The path counter outlives an activation, so ids stay unique when the tool is activated again, and deactivating removes the
 *    paths this tool drew (the old object had no deactivate, so the second part never mattered).
 *
 * Services it needs (see `requires`):
 *   propagationEngine  {available(), atmosphere() => {conditions, classification}, setAtmosphere(c), calculate(tx, rx),
 *                       visualize(id, tx, rx), removePath(id), clearAll()}   (RFPropagationManager)
 *   propagationView    the panel's DOM: see `workspaceBoot.js`
 *   activityLog        {metric(module, name, value), interaction(action, target)}: the metrics tool's loggers, or nothing
 */

export const PROPAGATION_ID = "propagation-ui";
const SHOWN_HISTORY = 10;
const CONDITION_COLORS = {"super-refractive": "#00aaff", standard: "#00ff88", "sub-refractive": "#ffaa00"};
const OTHER_CONDITION_COLOR = "#ff4444";

export const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const clock = (when) => new Date(when).toLocaleTimeString("en-US", {hour12: false});

export const conditionColor = (condition) => CONDITION_COLORS[condition] || OTHER_CONDITION_COLOR;

export const CALCULATING = '<div style="color: #4a9eff; text-align: center; padding: 10px;">⏳ Calculating...</div>';
export const NO_RESULT = `
                    <div style="color: #888; text-align: center; padding: 15px;">
                        Configure TX/RX parameters and click "Calculate Path Loss"
                    </div>
                `;
const NO_HISTORY = '<tr><td colspan="4" style="text-align: center; color: #888;">No calculations yet</td></tr>';

export function renderError(message) {
  return `
                    <div style="color: #ff4444; text-align: center; padding: 15px;">
                        ⚠️ Error: ${escapeHtml(message)}
                    </div>
                `;
}

/** The result card. Throws if the analysis lacks a field it shows, as the old code did (the caller reports it as an error). */
export function renderResults(analysis) {
  const linkViable = analysis.linkBudget.linkViable;
  const linkColor = linkViable ? "#00ff88" : "#ff4444";
  return `
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px;">
                        <div style="background: rgba(74, 158, 255, 0.1); padding: 8px; border-radius: 4px; text-align: center;">
                            <div style="font-size: 9px; color: #888;">Distance</div>
                            <div style="font-size: 14px; color: #4a9eff; font-weight: bold;">${analysis.geometry.distance_km.toFixed(2)} km</div>
                        </div>
                        <div style="background: rgba(74, 158, 255, 0.1); padding: 8px; border-radius: 4px; text-align: center;">
                            <div style="font-size: 9px; color: #888;">Total Path Loss</div>
                            <div style="font-size: 14px; color: #4a9eff; font-weight: bold;">${analysis.losses.total_db.toFixed(1)} dB</div>
                        </div>
                    </div>

                    <div style="background: rgba(0, 0, 0, 0.3); padding: 8px; border-radius: 4px; margin-bottom: 8px;">
                        <div style="font-size: 10px; color: #888; margin-bottom: 5px;">Loss Breakdown</div>
                        <div style="display: flex; justify-content: space-between; font-size: 10px; margin-bottom: 3px;">
                            <span>Free Space:</span>
                            <span style="color: #4a9eff;">${analysis.losses.freeSpace_db.toFixed(1)} dB</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 10px; margin-bottom: 3px;">
                            <span>Diffraction:</span>
                            <span style="color: ${analysis.losses.diffraction_db > 0 ? "#ffaa00" : "#00ff88"};">${analysis.losses.diffraction_db.toFixed(1)} dB</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 10px;">
                            <span>Atmospheric:</span>
                            <span style="color: #888;">${analysis.losses.atmospheric_db.toFixed(1)} dB</span>
                        </div>
                    </div>

                    <div style="background: rgba(${linkViable ? "0, 255, 136" : "255, 68, 68"}, 0.1); padding: 10px; border-radius: 4px; border: 1px solid ${linkColor};">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <div style="font-size: 9px; color: #888;">Link Status</div>
                                <div style="font-size: 12px; color: ${linkColor}; font-weight: bold;">
                                    ${linkViable ? "✓ LINK VIABLE" : "✗ LINK FAILED"}
                                </div>
                            </div>
                            <div style="text-align: right;">
                                <div style="font-size: 9px; color: #888;">Link Margin</div>
                                <div style="font-size: 16px; color: ${linkColor}; font-weight: bold;">
                                    ${analysis.linkBudget.linkMargin_db.toFixed(1)} dB
                                </div>
                            </div>
                        </div>
                        <div style="margin-top: 8px; font-size: 10px; color: #888;">
                            RX Power: ${analysis.linkBudget.receivedPower_dbm.toFixed(1)} dBm |
                            Sensitivity: ${escapeHtml(analysis.linkBudget.rxSensitivity_dbm)} dBm
                        </div>
                    </div>

                    <div style="margin-top: 8px; font-size: 10px; color: #666;">
                        Terrain samples: ${escapeHtml(analysis.terrain.sampleCount)} |
                        Obstructions: ${escapeHtml(analysis.terrain.obstructions)} |
                        Processing: ${analysis.processingTime_ms.toFixed(1)} ms
                    </div>
                `;
}

/** The history table body: the newest ten, newest first. */
export function renderHistory(history) {
  if (history.length === 0) return NO_HISTORY;
  return history.slice(-SHOWN_HISTORY).reverse().map((item) => {
    const analysis = item.analysis;
    const marginColor = analysis.linkBudget.linkViable ? "#00ff88" : "#ff4444";
    return `
                        <tr>
                            <td class="metric-time-cell">${clock(item.timestamp)}</td>
                            <td>${analysis.geometry.distance_km.toFixed(1)} km</td>
                            <td class="metric-value-cell">${analysis.losses.total_db.toFixed(1)} dB</td>
                            <td style="color: ${marginColor}; font-weight: bold;">${analysis.linkBudget.linkMargin_db.toFixed(1)} dB</td>
                        </tr>
                    `;
  }).join("");
}

/** The path boxes as numbers; the view returns the text that is in them. parseFloat, so an empty box is NaN. */
export function readPath(raw) {
  const tx = raw.tx, rx = raw.rx;
  return {
    tx: {lat: parseFloat(tx.lat), lon: parseFloat(tx.lon), height_m: parseFloat(tx.height), frequency_mhz: parseFloat(tx.frequency), power_dbm: parseFloat(tx.power)},
    rx: {lat: parseFloat(rx.lat), lon: parseFloat(rx.lon), height_m: parseFloat(rx.height), sensitivity_dbm: parseFloat(rx.sensitivity)},
  };
}

export function createPropagationPlugin({log = console} = {}) {
  let run = null;   // null while the tool is inactive
  let pathCounter = 0;   // outlives an activation: path ids stay unique in the engine

  const tool = {
    id: PROPAGATION_ID,
    title: "Propagation Analysis",
    requires: ["propagationEngine", "propagationView", "activityLog"],
    activate(ctx) {
      const engine = ctx.services.get("propagationEngine");
      const view = ctx.services.get("propagationView");
      const activity = ctx.services.get("activityLog");
      const stale = () => ctx.signal.aborted;   // deactivated while the engine was working
      const history = [];
      const drawn = [];   // ids of the paths this activation visualized

      const showError = (message) => { view.setHtml("path-analysis-results", renderError(message)); };
      const showHistory = () => { view.setHtml("propagation-history-body", renderHistory(history)); };

      function updateAtmosphericDisplay() {
        if (!engine.available()) return;
        try {
          const {conditions, classification} = engine.atmosphere();
          view.setText("atm-k-factor", conditions.kFactor.toFixed(3));
          view.setText("atm-refractivity", `${conditions.surfaceRefractivity} N`);
          view.setText("atm-condition", classification.condition);
          view.setColor("atm-condition", conditionColor(classification.condition));
        } catch (error) {
          log.error?.("[PropagationUI] Could not show the atmosphere:", error);
        }
      }

      function applyAtmosphericSettings() {
        if (!engine.available()) {
          log.warn?.("[PropagationUI] RFPropagationManager not available");
          return;
        }
        const raw = view.atmosphereInputs();
        engine.setAtmosphere({humidity: parseFloat(raw.humidity), temperature: parseFloat(raw.temperature), refractivityGradient: parseFloat(raw.gradient)});
        updateAtmosphericDisplay();
        activity.metric("propagation", "atmosphere_updated", 1);
      }

      async function calculatePathLoss() {
        if (!engine.available()) return showError("RFPropagationManager not initialized");
        view.setHtml("path-analysis-results", CALCULATING);
        const params = readPath(view.pathInputs());
        try {
          const analysis = await engine.calculate(params.tx, params.rx);
          if (stale()) return;
          if (analysis.error) return showError(analysis.error);
          history.push({timestamp: Date.now(), params, analysis});
          view.setHtml("path-analysis-results", renderResults(analysis));
          showHistory();
          activity.interaction("calculate_path_loss", `${analysis.geometry.distance_km.toFixed(1)} km`);
        } catch (error) {
          if (!stale()) showError(error.message);
        }
      }

      async function visualizePath() {
        if (!engine.available()) return showError("RFPropagationManager not initialized");
        const params = readPath(view.pathInputs());
        const pathId = `path_${++pathCounter}`;
        try {
          const analysis = await engine.visualize(pathId, params.tx, params.rx);
          if (stale()) {   // the engine drew it after we were deactivated: take it back down
            if (analysis) engine.removePath(pathId);
            return;
          }
          if (!analysis) return;
          drawn.push(pathId);
          view.setHtml("path-analysis-results", renderResults(analysis));
          history.push({timestamp: Date.now(), params, analysis, pathId});
          showHistory();
          activity.interaction("visualize_path", pathId);
        } catch (error) {
          if (!stale()) showError(error.message);
        }
      }

      function clearAllPaths() {
        if (engine.available()) engine.clearAll();
        history.length = 0;
        drawn.length = 0;
        showHistory();
        view.setHtml("path-analysis-results", NO_RESULT);
        activity.interaction("clear_paths", "all");
      }

      ctx.onCleanup(() => { for (const id of drawn.splice(0)) engine.removePath(id); });   // registered first, so it runs last
      ctx.onCleanup(view.bind({apply: applyAtmosphericSettings, calculate: calculatePathLoss, visualize: visualizePath, clear: clearAllPaths}));
      ctx.onCleanup(() => { run = null; });
      run = {updateAtmosphericDisplay};
      updateAtmosphericDisplay();
    },
  };

  /** What the page calls (`showPanel('propagation')`). Does nothing while the tool is inactive. */
  const controls = {updateAtmosphericDisplay: () => run?.updateAtmosphericDisplay()};

  return {tool, controls};
}
