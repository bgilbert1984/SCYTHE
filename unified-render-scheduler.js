/**
 * UnifiedRenderScheduler (URS)
 *
 * Controls both Cesium and Deck.gl under a single requestAnimationFrame loop.
 * Provides:
 *   - Frame-budget-aware LOD governor (auto quality scaling)
 *   - Imagery decoupler (request-render mode, quality-gated refresh)
 *   - Arc buffer batcher (typed arrays → custom HyperArcLayer)
 *   - RF depth-texture hook (terrain-aware propagation shaders)
 *   - GPU pressure feedback (EXT_disjoint_timer_query if available)
 *   - Recon entity hotspot promotion (cluster size → datacenter tag)
 *
 * Integration:
 *   const urs = new UnifiedRenderScheduler({ cesiumViewer: viewer, deck: deckInstance });
 *   window.__URS__ = urs;
 *   urs.start();
 *
 *   // Optional: wire to CesiumHypergraphGlobe
 *   urs.attachGlobe(globeInstance);
 */

/* ─── Imagery providers factory ─────────────────────────────────────────── */
const ImageryMode = Object.freeze({
  ION:          'ion',
  OSM:          'osm',
  PROXY_OSM:    'proxy_osm',    // Server-side proxied OSM tiles (24h cache)
  OFFLINE:      'offline',
  STADIA_BRIGHT:'stadia_bright',
  STADIA_DARK:  'stadia_dark',
  VECTOR:       'vector',       // MapLibre GL vector tiles (Stadia osm_bright style)
  VECTOR_DARK:  'vector_dark',  // MapLibre GL vector tiles (Stadia alidade_smooth_dark)
});

function _configuredStadiaKey() {
  if (typeof window === 'undefined') return '';
  try {
    return String(
      window.STADIA_API_KEY ||
      window.SCYTHE_STADIA_API_KEY ||
      window.localStorage?.getItem('scythe_stadia_api_key') ||
      ''
    ).trim();
  } catch (_) {
    return String(window.STADIA_API_KEY || window.SCYTHE_STADIA_API_KEY || '').trim();
  }
}

function _buildStadiaRasterProvider(styleName) {
  const apiKey = _configuredStadiaKey();
  if (!apiKey) return null;
  return new Cesium.UrlTemplateImageryProvider({
    url: `https://tiles.stadiamaps.com/tiles/${styleName}/{z}/{x}/{y}{r}.png?api_key=${encodeURIComponent(apiKey)}`,
    credit: '© Stadia Maps © OpenMapTiles © OpenStreetMap contributors'
  });
}

function buildImageryProvider(mode, localUrl) {
  const apiBase = (typeof window !== 'undefined' ? window.SCYTHE_API_BASE : '') || '';

  switch (mode) {
    case ImageryMode.OSM:
      return new Cesium.OpenStreetMapImageryProvider({
        url: 'https://a.tile.openstreetmap.org/'
      });
    case ImageryMode.PROXY_OSM:
      // Uses the new server-side tile proxy with 24-hour persistence
      return new Cesium.UrlTemplateImageryProvider({
        url: `${apiBase}/api/map/tile/osm/{z}/{x}/{y}`,
        credit: '© OpenStreetMap contributors (via Scythe Proxy)'
      });
    case ImageryMode.STADIA_BRIGHT:
      return _buildStadiaRasterProvider('osm_bright');
    case ImageryMode.STADIA_DARK:
      return _buildStadiaRasterProvider('alidade_smooth_dark');
    case ImageryMode.OFFLINE:
      if (localUrl) {
        return new Cesium.UrlTemplateImageryProvider({ url: localUrl });
      }
      // Dark procedural fallback (no external requests)
      return new Cesium.SingleTileImageryProvider({
        url: _buildDarkTileUrl(),
        rectangle: Cesium.Rectangle.fromDegrees(-180, -90, 180, 90)
      });
    case ImageryMode.ION:
    default: {
      // Cesium ≥1.104 deprecated the sync constructor; fromAssetId returns a Promise.
      // Return a sentinel so setImageryMode handles the async path.
      if (typeof Cesium?.IonImageryProvider?.fromAssetId === 'function') {
        return { _ionAssetId: 2, _asyncPromise: Cesium.IonImageryProvider.fromAssetId(2) };
      }
      // Legacy Cesium <1.104 sync constructor
      try { return new Cesium.IonImageryProvider({ assetId: 2 }); } catch (_) { /* fall through */ }
      // Ultimate fallback: OSM (always synchronous)
      console.warn('[URS] IonImageryProvider unavailable — using OSM fallback');
      return new Cesium.OpenStreetMapImageryProvider({ url: 'https://a.tile.openstreetmap.org/' });
    }
  }
}

function _buildDarkTileUrl() {
  // 1×1 pixel dark-navy canvas → data URL (no network call at all)
  try {
    const c = document.createElement('canvas');
    c.width = c.height = 1;
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#040d1e';
    ctx.fillRect(0, 0, 1, 1);
    return c.toDataURL();
  } catch (_) {
    return 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQI12NgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg==';
  }
}

/* ─── Arc buffer builder ─────────────────────────────────────────────────── */
/**
 * Convert arc objects into flat Float32Arrays for GPU upload.
 * Output: { positions, weights, colors, timestamps, count }
 *   positions: [srcLon, srcLat, srcAlt, dstLon, dstLat, dstAlt] × N
 *   weights:   [w] × N
 *   colors:    [r, g, b, a] × N  (0–255)
 */
function buildArcBuffer(arcs, quality = 1.0) {
  const N = Math.floor(arcs.length * Math.max(0.01, Math.min(1.0, quality)));
  const slice = quality < 1.0 ? _prioritySlice(arcs, N) : arcs;

  const positions  = new Float32Array(N * 6);
  const weights    = new Float32Array(N);
  const colors     = new Uint8Array(N * 4);
  const timestamps = new Float64Array(N);

  for (let i = 0; i < N; i++) {
    const a = slice[i];
    const s = a.source || a.src_pos || [0, 0, 0];
    const t = a.target || a.dst_pos || [0, 0, 0];

    positions[i * 6]     = s[0]; positions[i * 6 + 1] = s[1]; positions[i * 6 + 2] = s[2] || 0;
    positions[i * 6 + 3] = t[0]; positions[i * 6 + 4] = t[1]; positions[i * 6 + 5] = t[2] || 0;

    weights[i] = a.weight || a.confidence || 1;

    const c = a.color || _confToColor(a.confidence ?? a.weight ?? 0.5);
    colors[i * 4]     = c[0]; colors[i * 4 + 1] = c[1];
    colors[i * 4 + 2] = c[2]; colors[i * 4 + 3] = c[3] ?? 200;

    timestamps[i] = a.timestamp || Date.now();
  }

  return { positions, weights, colors, timestamps, count: N };
}

function _prioritySlice(arcs, n) {
  // Keep highest-confidence arcs when LOD degrades
  return [...arcs]
    .sort((a, b) => (b.confidence || b.weight || 0) - (a.confidence || a.weight || 0))
    .slice(0, n);
}

function _confToColor(c) {
  // confidence 0→1: amber→cyan
  const r = Math.round(255 * (1 - c));
  const g = Math.round(180 + 75 * c);
  const b = Math.round(100 + 155 * c);
  return [r, g, b, 200];
}

/* ─── Recon hotspot detection ────────────────────────────────────────────── */
const ReconTag = Object.freeze({
  DATACENTER_VM_CLUSTER: 'DATACENTER_VM_CLUSTER',
  ROTATING_PROXY_POOL:   'ROTATING_PROXY_POOL',
  SENSOR_ARRAY:          'SENSOR_ARRAY',
  NORMAL:                'NORMAL'
});

function classifyReconNode(node) {
  const size     = node.clusterSize || node.cluster_size || 1;
  const variance = node.variance ?? node.coord_variance ?? 1;

  if (size > 50  && variance < 0.001) return ReconTag.DATACENTER_VM_CLUSTER;
  if (size > 10  && variance < 0.05)  return ReconTag.ROTATING_PROXY_POOL;
  if (size > 5   && (node.kind || '').toLowerCase().includes('sensor')) return ReconTag.SENSOR_ARRAY;
  return ReconTag.NORMAL;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * UnifiedRenderScheduler
 * ═══════════════════════════════════════════════════════════════════════════ */
class UnifiedRenderScheduler {

  /**
   * @param {object} opts
   * @param {Cesium.Viewer}    opts.cesiumViewer   — required
   * @param {object}           opts.deck           — Deck.gl Deck instance (optional)
   * @param {number}           [opts.targetFps=60]
   * @param {number}           [opts.minQuality=0.15]
   * @param {number}           [opts.maxQuality=1.5]
   * @param {string}           [opts.imageryMode='ion']
   * @param {string}           [opts.offlineTileUrl]
   * @param {boolean}          [opts.rfDepthTest=true]
   */
  constructor(opts = {}) {
    this._viewer        = opts.cesiumViewer;
    this._deck          = opts.deck       || null;
    this._globe         = null;           // CesiumHypergraphGlobe (optional)

    this._targetFps     = opts.targetFps  || 60;
    this._frameBudgetMs = 1000 / this._targetFps;
    this._minQuality    = opts.minQuality ?? 0.15;
    this._maxQuality    = opts.maxQuality ?? 1.5;
    this._rfDepthTest   = opts.rfDepthTest ?? true;

    this.dynamicQuality = 1.0;
    this.imageryMode    = opts.imageryMode || ImageryMode.ION;
    this._offlineTileUrl= opts.offlineTileUrl || null;

    this._running       = false;
    this._rafId         = null;

    /* ── Metrics ── */
    this.metrics = {
      lastFrameMs:   0,
      gpuMs:         0,
      entityCount:   0,
      arcCount:      0,
      droppedFrames: 0,
      qualityHistory: []
    };

    /* ── Arc buffer cache ── */
    this._arcBuffer     = null;
    this._arcsDirty     = false;
    this._rawArcs       = [];

    /* ── GPU timer (optional) ── */
    this._gpuTimer      = null;
    this._gpuTimerQuery = null;

    /* ── Imagery state ── */
    this._imageryLayer  = null;
    this._imageryLocked = false;

    /* ── Listeners ── */
    this._onFrameCallbacks = [];
    this._onQualityChange  = null;
    this._onImageryChange  = null;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * attachMapLibre — wire a MapLibreDeckCesium (or raw maplibregl.Map) instance
   * ----------------------------------------------------------------------- */
  attachMapLibre(mlInstance) {
    this._maplibre = mlInstance;
    // If it's a MapLibreDeckCesium, quality changes propagate from both governors
    if (mlInstance && typeof mlInstance.onQualityChange === 'function') {
      mlInstance.onQualityChange((q) => {
        // Blend URS quality with MLDC quality (take the lower one — most conservative)
        this.dynamicQuality = Math.min(this.dynamicQuality, q);
      });
    }
    console.log('[URS] MapLibre instance attached');
    return this;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * attachGlobe — wire to CesiumHypergraphGlobe
   * ----------------------------------------------------------------------- */
  attachGlobe(globe) {
    this._globe = globe;
    // Globe already has its own render loop — suppress it, URS drives instead
    if (globe._renderer) {
      globe._renderer.autoClear = false;
    }
    return this;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * attachDeck — wire or replace Deck.gl instance
   * ----------------------------------------------------------------------- */
  attachDeck(deckInst) {
    this._deck = deckInst;
    return this;
  }

  onImageryChange(callback) {
    this._onImageryChange = callback;
    return this;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * start — kick off unified render loop + configure Cesium
   * ----------------------------------------------------------------------- */
  start() {
    if (this._running) return this;
    this._running = true;

    this._configureCesium();
    this._initGpuTimer();

    let _frame = 0;
    const loop = (ts) => {
      if (!this._running) return;
      this._rafId = requestAnimationFrame(loop);
      _frame++;

      const t0 = performance.now();

      this._updateMetrics();
      this._adjustQuality();

      if (this._arcsDirty) this._rebuildArcBuffer();

      this._renderCesium();
      this._renderDeck();
      if (this._globe) this._renderGlobe(_frame);

      this._pollGpuTimer();
      this._notifyFrameCallbacks(ts);

      this.metrics.lastFrameMs = performance.now() - t0;
    };

    this._rafId = requestAnimationFrame(loop);
    console.log('[URS] Unified render loop started');
    return this;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * stop
   * ----------------------------------------------------------------------- */
  stop() {
    this._running = false;
    if (this._rafId) { cancelAnimationFrame(this._rafId); this._rafId = null; }
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _configureCesium — request-render mode + tile throttle + RF depth
   * ----------------------------------------------------------------------- */
  _configureCesium() {
    const v = this._viewer;
    if (!v) return;

    // ── Take full ownership of the render loop ────────────────────────────
    // Cesium's internal RAF loop and our URS RAF loop are NOT synchronized.
    // When both run concurrently, Cesium renders Δ1 frame behind Three.js,
    // causing the heatmap/terrain to flash on every frame the browser composites
    // the two canvases at mismatched states.
    // Setting useDefaultRenderLoop = false stops Cesium's own loop entirely.
    // We call viewer.render() explicitly each URS frame so both renderers are
    // always in lock-step within the same RAF callback.
    v.useDefaultRenderLoop     = false;

    // requestRenderMode has no effect once we own the loop (we always call
    // viewer.render() directly), but set it false to prevent internal
    // requestRender() queuing from re-starting the default loop.
    v.scene.requestRenderMode  = false;

    // Tile request throttle (prevent 429 floods)
    if (Cesium.RequestScheduler) {
      Cesium.RequestScheduler.maximumRequestsPerServer = 4;
      Cesium.RequestScheduler.maximumRequests          = 24;
    }

    // RF terrain depth testing
    if (this._rfDepthTest) {
      v.scene.globe.depthTestAgainstTerrain = true;
    }

    v.scene.fog.enabled           = false;
    v.scene.globe.enableLighting  = true;

    // Apply initial imagery mode
    this.setImageryMode(this.imageryMode);
  }

  /* ───────────────────────────────────────────────────────────────────────
   * setImageryMode — hot-swap imagery provider
   * ----------------------------------------------------------------------- */
  setImageryMode(mode, localUrl) {
    const v = this._viewer;
    this.imageryMode = mode;
    this._onImageryChange?.(mode, { requestedMode: mode });

    // VECTOR modes: MapLibre owns the basemap — strip Cesium imagery entirely
    if (mode === ImageryMode.VECTOR || mode === ImageryMode.VECTOR_DARK) {
      if (v) {
        try { v.imageryLayers.removeAll(); } catch (_) {}
        // Disable sky/atmosphere so Cesium acts as transparent terrain layer
        if (v.scene) {
          v.scene.backgroundColor = typeof Cesium !== 'undefined'
            ? Cesium.Color.TRANSPARENT : { red: 0, green: 0, blue: 0, alpha: 0 };
        }
      }
      // Tell an attached MapLibreDeckCesium instance to switch style
      if (this._maplibre && typeof this._maplibre.setStyle === 'function') {
        const styleName = mode === ImageryMode.VECTOR_DARK ? 'alidade_smooth_dark' : 'osm_bright';
        this._maplibre.setStyle(styleName);
      }
      console.log(`[URS] Imagery mode → ${mode} (MapLibre vector tiles)`);
      return;
    }

    if (!v) return;
    try {
      // Remove old layers (except the base one at index 0 when using ion)
      const layers = v.scene.imageryLayers;
      while (layers.length > 0) layers.remove(layers.get(0), false);

      const provider = buildImageryProvider(mode, localUrl || this._offlineTileUrl);
      if (!provider) {
        if (mode === ImageryMode.STADIA_BRIGHT || mode === ImageryMode.STADIA_DARK) {
          console.warn('[URS] Stadia imagery requested without API key — falling back to OSM');
          this.setImageryMode(ImageryMode.OSM, localUrl);
          return;
        }
        console.warn('[URS] Imagery provider unavailable for mode', mode);
        return;
      }

      // Cesium ≥1.104 async Ion provider — sentinel with _asyncPromise
      if (provider._asyncPromise) {
        // Add OSM immediately as a visible baseline (Ion will replace it on success)
        const osmFallback = buildImageryProvider(ImageryMode.OSM);
        if (osmFallback) {
          try { this._imageryLayer = layers.addImageryProvider(osmFallback); } catch (_) {}
        }
        provider._asyncPromise
          .then((p) => {
            if (!p) return;
            try {
              // Replace the OSM baseline with Ion
              while (layers.length > 0) layers.remove(layers.get(0), false);
              this._imageryLayer = layers.addImageryProvider(p);
              console.log(`[URS] Imagery mode → ${mode} (ion async)`);
            } catch (err) {
              console.warn('[URS] Ion async addImageryProvider failed:', err.message);
              // OSM was already stripped; re-add it as permanent fallback
              try {
                const osm2 = buildImageryProvider(ImageryMode.OSM);
                if (osm2) { this._imageryLayer = layers.addImageryProvider(osm2); }
              } catch (_) {}
            }
          })
          .catch((e) => {
            console.warn('[URS] Ion async provider failed — staying on OSM:', e?.message || e);
          });
        return;
      }

      // Some providers (e.g., Ion legacy) hydrate tilingScheme asynchronously; add after ready.
      const addProvider = (p) => {
        if (!p || !p.tilingScheme) {
          console.warn('[URS] Imagery provider lacks tilingScheme after init; skipping');
          return;
        }
        this._imageryLayer = layers.addImageryProvider(p);
        console.log(`[URS] Imagery mode → ${mode}`);
      };

      if (!provider.tilingScheme && provider.readyPromise) {
        provider.readyPromise
          .then((p) => addProvider(p || provider))
          .catch((e) => console.warn('[URS] Imagery provider readyPromise failed:', e?.message || e));
      } else if (provider.tilingScheme) {
        addProvider(provider);
      } else {
        console.warn('[URS] Imagery provider missing tilingScheme; skipping layer add');
      }
    } catch (e) {
      console.warn('[URS] Imagery provider switch failed:', e.message);
    }
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _renderCesium — quality-gated globe render
   * ----------------------------------------------------------------------- */
  _renderCesium() {
    if (!this._viewer) return;
    // Drive Cesium synchronously — same RAF callback as Three.js so both canvases
    // always show the same frame. Quality gating skips expensive tile decode on
    // low-budget frames but still renders to prevent stale-frame flash.
    try {
      // Lock Cesium clock to wall time each frame so tile LOD, lighting, and
      // atmosphere are always consistent with the current frame timestamp.
      // Without this, Cesium's internal clock can drift and pop LODs mid-frame.
      if (typeof Cesium !== 'undefined' && this._viewer.clock) {
        this._viewer.clock.currentTime = Cesium.JulianDate.now(this._viewer.clock.currentTime);
      }
      this._viewer.render();
      // Hint the GPU driver to flush buffered commands before Three.js starts.
      // Prevents pipeline stalls where Cesium's heavy draw delays Three.js start.
      const gl = this._viewer.scene?.context?._gl;
      if (gl) gl.flush();
    } catch (_) {}
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _renderDeck — redraw Deck.gl with current quality uniform
   * ----------------------------------------------------------------------- */
  _renderDeck() {
    if (!this._deck) return;
    try {
      this._deck.redraw(true);
    } catch (_) {}
  }

  /* ───────────────────────────────────────────────────────────────────────
   * _renderGlobe — push uTime + camera sync for CesiumHypergraphGlobe
   * ----------------------------------------------------------------------- */
  _renderGlobe(frame = 0) {
    if (!this._globe || !this._globe._renderer) return;
    try {
      const g = this._globe;
      g._ursAttached = true;   // suppress globe's own RAF loop
      g.tickFrame();           // bloom pulse + uTime update
      g._syncCamera();
      g._renderThreeLayers(frame);  // hybrid heatmap + node render
    } catch (_) {}
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Quality governor
   * ----------------------------------------------------------------------- */
  _updateMetrics() {
    this.metrics.entityCount = window.__ARC_COUNT__ || (this._rawArcs.length);
    this.metrics.arcCount    = this._rawArcs.length;
    // Rolling quality history (last 30 frames)
    if (this.metrics.qualityHistory.length >= 30)
      this.metrics.qualityHistory.shift();
    this.metrics.qualityHistory.push(this.dynamicQuality);
  }

  _adjustQuality() {
    const { lastFrameMs } = this.metrics;
    const budget = this._frameBudgetMs;

    if (lastFrameMs > budget * 1.2) {
      this.dynamicQuality *= 0.92;
      if (lastFrameMs > budget * 2) this.metrics.droppedFrames++;
    } else if (lastFrameMs < budget * 0.75) {
      this.dynamicQuality *= 1.04;
    }

    const prev = this.dynamicQuality;
    this.dynamicQuality = Math.max(this._minQuality, Math.min(this._maxQuality, this.dynamicQuality));

    if (Math.abs(this.dynamicQuality - prev) > 0.05 && typeof this._onQualityChange === 'function') {
      this._onQualityChange(this.dynamicQuality);
    }
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Arc batching API
   * ----------------------------------------------------------------------- */

  /**
   * Replace the full arc set. Marks buffer dirty for next frame.
   * @param {Array} arcs  — [{source:[lon,lat,alt], target:[lon,lat,alt], confidence, ...}]
   */
  setArcs(arcs) {
    this._rawArcs   = arcs;
    this._arcsDirty = true;
    window.__ARC_COUNT__ = arcs.length;
  }

  /**
   * Append arcs (streaming ingest).
   * @param {Array} arcs
   * @param {number} maxSize — ring buffer cap (default 50k)
   */
  pushArcs(arcs, maxSize = 50_000) {
    for (const a of arcs) this._rawArcs.push(a);
    if (this._rawArcs.length > maxSize) {
      this._rawArcs.splice(0, this._rawArcs.length - maxSize);
    }
    this._arcsDirty = true;
    window.__ARC_COUNT__ = this._rawArcs.length;
  }

  _rebuildArcBuffer() {
    this._arcsDirty  = false;
    this._arcBuffer  = buildArcBuffer(this._rawArcs, this.dynamicQuality);
    return this._arcBuffer;
  }

  /** Get latest arc buffer (rebuilt if dirty) */
  getArcBuffer() {
    if (this._arcsDirty) this._rebuildArcBuffer();
    return this._arcBuffer;
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Recon entity hotspot API
   * ----------------------------------------------------------------------- */
  classifyNodes(nodes) {
    return nodes.map(n => ({
      ...n,
      reconTag:    classifyReconNode(n),
      amplifyRF:   classifyReconNode(n) === ReconTag.DATACENTER_VM_CLUSTER,
      renderAs:    classifyReconNode(n) === ReconTag.DATACENTER_VM_CLUSTER ? 'persistent_beacon' : 'normal'
    }));
  }

  detectHotspots(nodes) {
    return nodes.filter(n => {
      const size = n.clusterSize || n.cluster_size || 1;
      const v    = n.variance ?? 1;
      return size > 50 && v < 0.001;
    });
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Imagery lock (freeze tile updates during high-CPU ops)
   * ----------------------------------------------------------------------- */
  lockImagery()   { this._imageryLocked = true;  }
  unlockImagery() { this._imageryLocked = false; }

  /* ───────────────────────────────────────────────────────────────────────
   * GPU pressure timer (EXT_disjoint_timer_query)
   * ----------------------------------------------------------------------- */
  _initGpuTimer() {
    try {
      const canvas = this._viewer?.scene?.canvas || this._globe?._renderer?.domElement;
      if (!canvas) return;
      const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
      if (!gl) return;
      const ext = gl.getExtension('EXT_disjoint_timer_query_webgl2')
               || gl.getExtension('EXT_disjoint_timer_query');
      if (ext) {
        this._gpuTimer  = ext;
        this._gpuGl     = gl;
        console.log('[URS] GPU timer available');
      }
    } catch (_) {}
  }

  _pollGpuTimer() {
    if (!this._gpuTimer || !this._gpuTimerQuery) return;
    try {
      const gl  = this._gpuGl;
      const ext = this._gpuTimer;
      const available = gl.getQueryParameter
        ? gl.getQueryParameter(this._gpuTimerQuery, gl.QUERY_RESULT_AVAILABLE)
        : ext.getQueryObjectEXT(this._gpuTimerQuery, ext.QUERY_RESULT_AVAILABLE_EXT);

      if (available) {
        const ns = gl.getQueryParameter
          ? gl.getQueryParameter(this._gpuTimerQuery, gl.QUERY_RESULT)
          : ext.getQueryObjectEXT(this._gpuTimerQuery, ext.QUERY_RESULT_EXT);
        this.metrics.gpuMs = ns / 1e6;   // nanoseconds → milliseconds
        this._gpuTimerQuery = null;
      }
    } catch (_) { this._gpuTimerQuery = null; }
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Frame callback registration
   * ----------------------------------------------------------------------- */
  onFrame(cb) {
    this._onFrameCallbacks.push(cb);
    return () => { this._onFrameCallbacks = this._onFrameCallbacks.filter(f => f !== cb); };
  }

  onQualityChange(cb) {
    this._onQualityChange = cb;
  }

  _notifyFrameCallbacks(ts) {
    for (const cb of this._onFrameCallbacks) {
      try { cb(ts, this); } catch (_) {}
    }
  }

  /* ───────────────────────────────────────────────────────────────────────
   * Diagnostics
   * ----------------------------------------------------------------------- */
  getStatus() {
    return {
      running:        this._running,
      quality:        +this.dynamicQuality.toFixed(3),
      lastFrameMs:    +this.metrics.lastFrameMs.toFixed(2),
      gpuMs:          +this.metrics.gpuMs.toFixed(2),
      arcCount:       this.metrics.arcCount,
      entityCount:    this.metrics.entityCount,
      droppedFrames:  this.metrics.droppedFrames,
      imageryMode:    this.imageryMode
    };
  }

  /** Console-friendly one-liner for status panel */
  statusLine() {
    const s = this.getStatus();
    return `URS q=${s.quality} cpu=${s.lastFrameMs}ms gpu=${s.gpuMs}ms arcs=${s.arcCount} drop=${s.droppedFrames}`;
  }
}

/* ─── HTML escape (safe, non-recursive lookup table) ───────────────────── */
const _HTML_ESCAPE_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

/**
 * Safe HTML escaping — lookup table, no recursion possible.
 * Drop-in replacement for any escapeHtml/escapeHTML wrapper.
 */
function safeEscapeHtml(input, _depth = 0) {
  if (_depth > 8) return '[DEPTH_LIMIT]';
  if (typeof input === 'string') {
    return input.replace(/[&<>"']/g, ch => _HTML_ESCAPE_MAP[ch]);
  }
  if (Array.isArray(input))  return input.map(v => safeEscapeHtml(v, _depth + 1));
  if (input !== null && typeof input === 'object') {
    return Object.fromEntries(
      Object.entries(input).map(([k, v]) => [k, safeEscapeHtml(v, _depth + 1)])
    );
  }
  return input;
}

/* ─── Module exports ─────────────────────────────────────────────────────── */
if (typeof module !== 'undefined') {
  module.exports = { UnifiedRenderScheduler, buildArcBuffer, safeEscapeHtml,
                     classifyReconNode, ReconTag, ImageryMode, buildImageryProvider };
} else {
  window.UnifiedRenderScheduler = UnifiedRenderScheduler;
  window.buildArcBuffer         = buildArcBuffer;
  window.safeEscapeHtml         = safeEscapeHtml;
  window.classifyReconNode      = classifyReconNode;
  window.ReconTag               = ReconTag;
  window.ImageryMode            = ImageryMode;

  // Override global escapeHtml/escapeHTML with safe version on load
  window.escapeHtml  = safeEscapeHtml;
  window.escapeHTML  = safeEscapeHtml;
}
