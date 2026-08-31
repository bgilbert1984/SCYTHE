/**
 * Canvas 2D renderer for the NESDR FFT trace and rolling waterfall.
 *
 * The amplitude scale is FIXED and calibrated, never per-frame normalized:
 * auto-normalizing each frame makes an empty noise floor look like a signal,
 * because the loudest thing present is always drawn as full scale.
 *
 * Bins are drawn one display bin per column. The trace is never smoothed or
 * interpolated across bins — that would draw frequency resolution the 512-bin
 * peak-downsampled product does not have.
 */

export const DBFS_FLOOR = -110;
export const DBFS_CEILING = -10;
export const HISTORY_ROWS = 180;

export const PALETTE = Object.freeze({
  measured: "#38d6ff",      // cyan   — bounded spectral observation
  sparse: "#ffb347",        // amber  — derived estimator result
  digital: "#b388ff",       // violet — derived signal-family inference
  analogue: "#5fd08a",      // green  — derived signal-family inference
  unclassified: "#8a97a3",  // gray   — evidence exists, family unsupported
  nullOutcome: "#4a6fa5",   // muted blue — analyzer ran, found no support
  stale: "#ff5f56",         // red    — product unavailable or expired
  noiseFloor: "#2f6d7d",
  grid: "#16333f",
  axis: "#7d95a3",
});

/** Clamp a dBFS value onto the fixed scale and return 0 (floor) .. 1 (ceiling). */
export function normalizeDbfs(dbfs, {floor = DBFS_FLOOR, ceiling = DBFS_CEILING} = {}) {
  const value = Number(dbfs);
  if (!Number.isFinite(value)) return 0;
  if (ceiling <= floor) return 0;
  return Math.min(1, Math.max(0, (value - floor) / (ceiling - floor)));
}

/** Waterfall colour ramp: dark blue floor -> cyan -> amber -> white peak. */
export function waterfallColor(intensity) {
  const t = Math.min(1, Math.max(0, Number(intensity) || 0));
  if (t < 0.35) {                       // floor .. low
    const k = t / 0.35;
    return [Math.round(4 + k * 10), Math.round(12 + k * 54), Math.round(28 + k * 82)];
  }
  if (t < 0.7) {                        // low .. mid (cyan)
    const k = (t - 0.35) / 0.35;
    return [Math.round(14 + k * 42), Math.round(66 + k * 148), Math.round(110 + k * 90)];
  }
  if (t < 0.9) {                        // mid .. hot (amber)
    const k = (t - 0.7) / 0.2;
    return [Math.round(56 + k * 199), Math.round(214 - k * 35), Math.round(200 - k * 130)];
  }
  const k = (t - 0.9) / 0.1;            // hot .. peak (white)
  return [255, Math.round(179 + k * 76), Math.round(70 + k * 185)];
}

/** Map a frequency to an x pixel across the frame's own span. */
export function frequencyToX(hz, {minHz, maxHz, width}) {
  // Number(null) is 0, so an absent frequency would otherwise be drawn as a
  // marker at a real position. An undeclared frequency gets no marker at all.
  if (hz === null || hz === undefined || hz === "") return null;
  const low = Number(minHz); const high = Number(maxHz); const span = high - low;
  if (!Number.isFinite(span) || span <= 0 || !Number.isFinite(Number(hz))) return null;
  return ((Number(hz) - low) / span) * width;
}

/**
 * Rolling waterfall history. Rows are pushed newest-last; the buffer is a fixed
 * ring so a long session cannot grow memory without bound.
 */
export class WaterfallHistory {
  constructor({rows = HISTORY_ROWS, bins = 512} = {}) {
    this.rows = Math.max(1, rows | 0);
    this.bins = Math.max(1, bins | 0);
    this.data = new Uint8ClampedArray(this.rows * this.bins * 4);
    this.filled = 0;
  }

  /** Resize on a bin-count change; history for a different span is discarded. */
  resize(bins) {
    const width = Math.max(1, bins | 0);
    if (width === this.bins) return false;
    this.bins = width;
    this.data = new Uint8ClampedArray(this.rows * this.bins * 4);
    this.filled = 0;
    return true;
  }

  push(bins) {
    if (!Array.isArray(bins) || !bins.length) return false;
    if (bins.length !== this.bins) this.resize(bins.length);
    const stride = this.bins * 4;
    this.data.copyWithin(0, stride);                 // scroll up by one row
    const offset = (this.rows - 1) * stride;
    for (let index = 0; index < this.bins; index += 1) {
      const [r, g, b] = waterfallColor(normalizeDbfs(bins[index]));
      const pixel = offset + index * 4;
      this.data[pixel] = r; this.data[pixel + 1] = g; this.data[pixel + 2] = b;
      this.data[pixel + 3] = 255;
    }
    this.filled = Math.min(this.rows, this.filled + 1);
    return true;
  }

  clear() {
    this.data.fill(0);
    this.filled = 0;
  }
}

/** Draw the FFT trace, noise floor, centre marker and peak marker. */
export function drawTrace(context, frame, {width, height, cursorHz = null} = {}) {
  if (!context || !width || !height) return false;
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#04121a";
  context.fillRect(0, 0, width, height);

  const y = (dbfs) => height - normalizeDbfs(dbfs) * height;

  context.strokeStyle = PALETTE.grid; context.lineWidth = 1;
  for (let dbfs = DBFS_CEILING; dbfs >= DBFS_FLOOR; dbfs -= 20) {
    const line = Math.round(y(dbfs)) + 0.5;
    context.beginPath(); context.moveTo(0, line); context.lineTo(width, line); context.stroke();
  }

  const bins = frame?.bins ?? [];
  if (!bins.length) return false;

  if (Number.isFinite(frame.noiseFloorDbfs)) {
    context.strokeStyle = PALETTE.noiseFloor;
    context.setLineDash([4, 4]);
    const line = Math.round(y(frame.noiseFloorDbfs)) + 0.5;
    context.beginPath(); context.moveTo(0, line); context.lineTo(width, line); context.stroke();
    context.setLineDash([]);
  }

  // One column per display bin. No smoothing: the product is peak-downsampled.
  context.strokeStyle = frame.stale ? PALETTE.stale : PALETTE.measured;
  context.lineWidth = 1;
  context.beginPath();
  const step = width / bins.length;
  for (let index = 0; index < bins.length; index += 1) {
    const px = index * step;
    const py = y(bins[index]);
    if (index === 0) context.moveTo(px, py); else context.lineTo(px, py);
  }
  context.stroke();

  const markers = [
    [frame.centerFrequencyHz, PALETTE.axis],
    [frame.peakFrequencyHz, PALETTE.sparse],
    [cursorHz, "#ffffff"],
  ];
  for (const [hz, color] of markers) {
    const x = frequencyToX(hz, {minHz: frame.minFrequencyHz, maxHz: frame.maxFrequencyHz, width});
    if (x === null) continue;
    context.strokeStyle = color; context.lineWidth = 1;
    context.beginPath();
    context.moveTo(Math.round(x) + 0.5, 0);
    context.lineTo(Math.round(x) + 0.5, height);
    context.stroke();
  }
  return true;
}

/** Blit the waterfall history into a canvas context. */
export function drawWaterfall(context, history, {width, height} = {}) {
  if (!context || !history || !width || !height) return false;
  const image = context.createImageData(history.bins, history.rows);
  image.data.set(history.data);
  if (typeof context.putImageData === "function") context.putImageData(image, 0, 0);
  return true;
}
