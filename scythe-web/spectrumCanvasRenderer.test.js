import assert from "node:assert/strict";
import test from "node:test";

import {
  DBFS_CEILING, DBFS_FLOOR, WaterfallHistory, drawTrace, frequencyToX, normalizeDbfs,
  waterfallColor,
} from "./spectrumCanvasRenderer.js";

function stubContext() {
  const calls = [];
  const record = (name) => (...args) => { calls.push([name, ...args]); };
  return {
    calls,
    clearRect: record("clearRect"), fillRect: record("fillRect"),
    beginPath: record("beginPath"), moveTo: record("moveTo"), lineTo: record("lineTo"),
    stroke: record("stroke"), setLineDash: record("setLineDash"),
    createImageData: (w, h) => ({data: new Uint8ClampedArray(w * h * 4), width: w, height: h}),
    putImageData: record("putImageData"),
    fillStyle: "", strokeStyle: "", lineWidth: 1,
  };
}

test("the amplitude scale is fixed, not normalized per frame", () => {
  // A quiet frame must stay near the floor. Per-frame normalization would draw
  // the loudest bin at full scale and make ordinary noise look like a signal.
  assert.equal(normalizeDbfs(DBFS_FLOOR), 0);
  assert.equal(normalizeDbfs(DBFS_CEILING), 1);
  assert.equal(normalizeDbfs(-60), 0.5);
  const quiet = [-99, -98, -97].map((value) => normalizeDbfs(value));
  assert.ok(Math.max(...quiet) < 0.15, "a quiet frame must not reach full scale");
});

test("out-of-range and non-finite amplitudes clamp instead of throwing", () => {
  assert.equal(normalizeDbfs(40), 1);
  assert.equal(normalizeDbfs(-300), 0);
  assert.equal(normalizeDbfs(Number.NaN), 0);
  assert.equal(normalizeDbfs(undefined), 0);
});

test("the waterfall ramp rises monotonically in luminance", () => {
  let previous = -1;
  for (let t = 0; t <= 1.0001; t += 0.05) {
    const [r, g, b] = waterfallColor(t);
    const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    assert.ok(luminance >= previous - 1e-6, `luminance fell at t=${t.toFixed(2)}`);
    previous = luminance;
  }
});

test("frequency maps across the frame's own span and refuses a degenerate one", () => {
  const span = {minHz: 99e6, maxHz: 101e6, width: 512};
  assert.equal(frequencyToX(100e6, span), 256);
  assert.equal(frequencyToX(99e6, span), 0);
  assert.equal(frequencyToX(null, span), null);
  assert.equal(frequencyToX(100e6, {minHz: 1, maxHz: 1, width: 512}), null);
});

test("waterfall history is a fixed ring that scrolls and cannot grow unbounded", () => {
  const history = new WaterfallHistory({rows: 4, bins: 3});
  const bytes = history.data.length;
  for (let index = 0; index < 50; index += 1) history.push([-100, -50, -20]);
  assert.equal(history.data.length, bytes, "the ring must not grow with frame count");
  assert.equal(history.filled, 4);
  const stride = history.bins * 4;
  const newest = history.data.slice((history.rows - 1) * stride);
  assert.equal(newest[3], 255, "the newest row must be opaque");
});

test("a bin-count change discards history rather than mixing spans", () => {
  const history = new WaterfallHistory({rows: 4, bins: 3});
  history.push([-90, -60, -30]);
  assert.equal(history.filled, 1);
  history.push([-90, -60, -30, -20]);
  assert.equal(history.bins, 4);
  assert.equal(history.filled, 1, "history from a different span must not be retained");
});

test("push refuses an empty frame", () => {
  const history = new WaterfallHistory({rows: 2, bins: 2});
  assert.equal(history.push([]), false);
  assert.equal(history.push(null), false);
  assert.equal(history.filled, 0);
});

test("the trace draws one vertex per display bin without interpolation", () => {
  const context = stubContext();
  const bins = [-90, -80, -70, -60];
  const drawn = drawTrace(context, {
    bins, minFrequencyHz: 99e6, maxFrequencyHz: 101e6, centerFrequencyHz: 100e6,
    peakFrequencyHz: 100.5e6, noiseFloorDbfs: -88,
  }, {width: 512, height: 150});
  assert.equal(drawn, true);
  const vertices = context.calls.filter(([name]) => name === "moveTo" || name === "lineTo");
  const traceVertices = vertices.filter(([, x]) => x !== undefined).length;
  assert.ok(traceVertices >= bins.length,
    "each display bin must contribute its own vertex; extra points would imply finer resolution");
});

test("a frame with no bins draws no trace", () => {
  const context = stubContext();
  assert.equal(drawTrace(context, {bins: []}, {width: 512, height: 150}), false);
});
