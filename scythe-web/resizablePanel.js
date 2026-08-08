const DEFAULT_MIN_WIDTH = 320;
const DEFAULT_MIN_HEIGHT = 220;
const VIEWPORT_GUTTER = 24;

export function clampPanelSize({width, height}, {viewportWidth, viewportHeight,
  minWidth = DEFAULT_MIN_WIDTH, minHeight = DEFAULT_MIN_HEIGHT} = {}) {
  const maximumWidth = Math.max(1, Number(viewportWidth) - VIEWPORT_GUTTER);
  const maximumHeight = Math.max(1, Number(viewportHeight) - VIEWPORT_GUTTER);
  const effectiveMinWidth = Math.min(minWidth, maximumWidth);
  const effectiveMinHeight = Math.min(minHeight, maximumHeight);
  return {
    width: Math.round(Math.min(Math.max(Number(width) || effectiveMinWidth, effectiveMinWidth), maximumWidth)),
    height: Math.round(Math.min(Math.max(Number(height) || effectiveMinHeight, effectiveMinHeight), maximumHeight)),
  };
}

export class ResizablePanel {
  constructor({panel, handle, storageKey = "scythe.live-hypergraph.size", storage = null}) {
    if (!panel || !handle) throw new TypeError("resizable panel and handle are required");
    this.panel = panel; this.handle = handle; this.storageKey = storageKey;
    this.document = panel.ownerDocument ?? globalThis.document;
    this.window = this.document?.defaultView ?? globalThis;
    this.storage = storage ?? this.window.localStorage;
    this.drag = null; this.started = false;
  }

  start() {
    if (this.started) return this;
    this.started = true;
    this.onPointerDown = (event) => this.#begin(event);
    this.onPointerMove = (event) => this.#move(event);
    this.onPointerUp = (event) => this.#end(event);
    this.onKeyDown = (event) => this.#key(event);
    this.onDoubleClick = () => this.reset();
    this.onWindowResize = () => this.#constrainCurrent();
    this.handle.addEventListener("pointerdown", this.onPointerDown);
    this.handle.addEventListener("pointermove", this.onPointerMove);
    this.handle.addEventListener("pointerup", this.onPointerUp);
    this.handle.addEventListener("pointercancel", this.onPointerUp);
    this.handle.addEventListener("keydown", this.onKeyDown);
    this.handle.addEventListener("dblclick", this.onDoubleClick);
    this.window.addEventListener?.("resize", this.onWindowResize);
    this.#restore(); this.#announce();
    return this;
  }

  #viewport() {
    return {viewportWidth: this.window.innerWidth || this.document.documentElement?.clientWidth || 1024,
      viewportHeight: this.window.innerHeight || this.document.documentElement?.clientHeight || 768};
  }

  #size() {
    const bounds = this.panel.getBoundingClientRect();
    return {width: bounds.width, height: bounds.height};
  }

  #apply(size, persist = false) {
    const bounded = clampPanelSize(size, this.#viewport());
    this.panel.style.width = `${bounded.width}px`;
    this.panel.style.height = `${bounded.height}px`;
    this.panel.dataset.userResized = "true";
    this.#announce(bounded);
    if (persist) this.#store(bounded);
    return bounded;
  }

  #begin(event) {
    if (event.button !== undefined && event.button !== 0) return;
    const size = this.#size();
    this.drag = {pointerId: event.pointerId, startX: event.clientX, startY: event.clientY,
      width: size.width, height: size.height};
    this.handle.setPointerCapture?.(event.pointerId);
    this.handle.dataset.dragging = "true";
    event.preventDefault?.();
  }

  #move(event) {
    if (!this.drag || event.pointerId !== this.drag.pointerId) return;
    // The panel is anchored at bottom-left: rightward grows width and upward grows height.
    this.#apply({width: this.drag.width + event.clientX - this.drag.startX,
      height: this.drag.height + this.drag.startY - event.clientY});
    event.preventDefault?.();
  }

  #end(event) {
    if (!this.drag || event.pointerId !== this.drag.pointerId) return;
    this.handle.releasePointerCapture?.(event.pointerId);
    this.drag = null; delete this.handle.dataset.dragging;
    this.#store(this.#size());
  }

  #key(event) {
    const directions = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1]};
    const direction = directions[event.key];
    if (!direction) return;
    const step = event.shiftKey ? 50 : 10; const size = this.#size();
    this.#apply({width: size.width + direction[0] * step, height: size.height + direction[1] * step}, true);
    event.preventDefault?.();
  }

  #announce(size = this.#size()) {
    const label = `Resize live hypergraph. Current size ${Math.round(size.width)} by ${Math.round(size.height)} pixels. ` +
      "Drag right and up to enlarge; use arrow keys; double-click to reset.";
    this.handle.setAttribute("aria-label", label); this.handle.title = label;
  }

  #store(size) {
    try { this.storage?.setItem(this.storageKey, JSON.stringify(clampPanelSize(size, this.#viewport()))); } catch {}
  }

  #restore() {
    try {
      const saved = JSON.parse(this.storage?.getItem(this.storageKey) ?? "null");
      if (saved && Number.isFinite(saved.width) && Number.isFinite(saved.height)) this.#apply(saved);
    } catch {}
  }

  #constrainCurrent() {
    if (this.panel.dataset.userResized === "true") this.#apply(this.#size(), true);
  }

  reset() {
    this.panel.style.removeProperty("width"); this.panel.style.removeProperty("height");
    delete this.panel.dataset.userResized;
    try { this.storage?.removeItem(this.storageKey); } catch {}
    this.#announce();
  }

  destroy() {
    this.handle.removeEventListener("pointerdown", this.onPointerDown);
    this.handle.removeEventListener("pointermove", this.onPointerMove);
    this.handle.removeEventListener("pointerup", this.onPointerUp);
    this.handle.removeEventListener("pointercancel", this.onPointerUp);
    this.handle.removeEventListener("keydown", this.onKeyDown);
    this.handle.removeEventListener("dblclick", this.onDoubleClick);
    this.window.removeEventListener?.("resize", this.onWindowResize);
    this.started = false;
  }
}
