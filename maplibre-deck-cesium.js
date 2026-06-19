/**
 * MapLibreDeckCesium
 *
 * Hybrid rendering stack:
 *   [0] MapLibre GL     — vector tile base map (roads, buildings, water as GPU geometry)
 *   [1] Deck.gl canvas  — RF arcs, node overlays, MVT feature interaction, intelligence labels
 *   [2] Cesium div      — terrain depth / 3D globe (optional, pointer-events: none)
 *
 * Vector tile sources (auto-selected):
 *   • Stadia Maps  (existing API key)  → osm_bright, alidade_smooth_dark, stamen_toner
 *   • OpenFreeMap  (no key required)   → liberty (dark), bright
 *   • Offline fallback                  → minimal dark canvas, no network
 *
 * Label system (Option 5 — Hybrid):
 *   • MapLibre symbol layers  → geographic context (city names, roads, borders)
 *   • Deck.gl TextLayer       → RF/intelligence labels with FPS-LOD, tile-batch, terrain-occlusion
 *
 * Quick start:
 *   const hybrid = new MapLibreDeckCesium({
 *     containerId: 'map',
 *     stadiaKey:   '053a8c7a-...',
 *     style:       'alidade_smooth_dark',
 *   });
 *   await hybrid.init();
 *   hybrid.setArcData(myArcs);
 *   hybrid.setLabelData([{ coordinates: [-122.4, 37.7], label: 'NODE-17', priority: 0.8 }]);
 *   hybrid.onTileLoad((tiles, ctx) => {
 *     // tiles = [{ id, bbox }] — compute trigger
 *   });
 */

/* ─── Style registries ───────────────────────────────────────────────────── */

// Stadia styles (require API key — kept as secondary/legacy option)
const ML_STADIA_STYLES = {
  osm_bright:          k => `https://tiles.stadiamaps.com/styles/osm_bright.json?api_key=${k}`,
  alidade_smooth_dark: k => `https://tiles.stadiamaps.com/styles/alidade_smooth_dark.json?api_key=${k}`,
  alidade_smooth:      k => `https://tiles.stadiamaps.com/styles/alidade_smooth.json?api_key=${k}`,
  stamen_toner:        k => `https://tiles.stadiamaps.com/styles/stamen_toner.json?api_key=${k}`,
  outdoors:            k => `https://tiles.stadiamaps.com/styles/outdoors.json?api_key=${k}`,
};

// OpenFreeMap styles (no API key, no rate limit)
// Each URL is a complete MapLibre style JSON — tiles included.
const ML_FREE_STYLES = {
  liberty:  'https://tiles.openfreemap.org/styles/liberty',   // dark, detailed
  dark:     'https://tiles.openfreemap.org/styles/liberty',   // alias
  bright:   'https://tiles.openfreemap.org/styles/bright',    // colorful OSM — best labels
  positron: 'https://tiles.openfreemap.org/styles/positron',  // light / minimal
};

// Stadia name → closest OpenFreeMap equivalent (used when key absent or 429)
const _STADIA_TO_FREE = {
  alidade_smooth_dark: 'liberty',
  osm_bright:          'bright',
  stamen_toner:        'positron',
  alidade_smooth:      'bright',
  outdoors:            'liberty',
};

/* ─── OpenFreeMap PBF endpoint — used by MVTLayer (no key, no rate limit) ── */
function _mvtUrl(_stadiaKey) {
  // Always use OpenFreeMap PBF — Stadia quota exhaustion risk eliminated
  return 'https://tiles.openfreemap.org/planet/{z}/{x}/{y}.pbf';
}

/* ─── Minimal offline style (zero network) ───────────────────────────────── */
function _offlineStyle() {
  return {
    version: 8,
    glyphs:  'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf',
    sources: {},
    layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#060d1e' } }],
  };
}

/* ═══════════════════════════════════════════════════════════════════════════
 * TileArcCache — LRU cache of arcs keyed by tile ID
 * ═══════════════════════════════════════════════════════════════════════════ */
class TileArcCache {
  constructor(maxTiles = 200) {
    this._cache   = new Map();
    this._lru     = [];
    this._maxSize = maxTiles;
  }
  get(id)    { return this._cache.get(id) || null; }
  has(id)    { return this._cache.has(id); }
  get size() { return this._cache.size; }
  clear()    { this._cache.clear(); this._lru = []; }

  set(id, arcs) {
    if (!this._cache.has(id)) {
      this._lru.push(id);
      if (this._lru.length > this._maxSize) this._cache.delete(this._lru.shift());
    }
    this._cache.set(id, arcs);
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * RFArcLayer — Deck.gl ArcLayer with exponential signal-decay fragment shader
 * ═══════════════════════════════════════════════════════════════════════════ */
class RFArcLayer extends (typeof deck !== 'undefined' ? deck.ArcLayer : Object) {
  getShaders() {
    const base = super.getShaders ? super.getShaders() : {};
    return {
      ...base,
      inject: {
        // Arc progress ∈ [0,1] — apply free-space path loss decay + glow at peak
        'fs:DECKGL_FILTER_COLOR': `
          float p    = geometry.uv.x;
          float loss = exp(-p * 2.5 * uniforms.rfDecay);
          float peak = sin(p * 3.14159265);
          color.rgb *= mix(1.0, loss, 0.65);
          color.rgb += vec3(0.0, 0.04, 0.12) * peak * uniforms.rfGlow;
          color.a   *= mix(0.35, 1.0, peak);
        `,
      },
    };
  }

  draw(opts) {
    try {
      this.state?.model?.setUniforms({
        rfDecay: this.props.rfDecay ?? 0.8,
        rfGlow:  this.props.rfGlow  ?? 0.4,
      });
    } catch (_) {}
    super.draw(opts);
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * AdaptiveTextLayer — GPU-driven SDF text with priority-based fade + thickness
 *
 * Signal flow:
 *   getColor([r,g,b, priority×255])
 *   → Deck.gl TextLayer (SDF mode): color.a = vColor.a × sdfGlyphAlpha
 *   → DECKGL_FILTER_COLOR: fade = smoothstep(gpuThreshold, ±gpuSmoothBand)
 *
 * The priority × sdfAlpha product is the thickness engine:
 *   priority=1.0 × edge=0.5 → 0.50 → above threshold → VISIBLE  (bold)
 *   priority=0.3 × edge=0.5 → 0.15 → below threshold → ERODED   (hairline)
 *   priority=0.3 × core=1.0 → 0.30 → at threshold    → FRAGILE  (thin)
 *
 * gpuThreshold: FPS governor — raises to suppress low-priority labels at load
 * gpuSmoothBand: edge transition width — wider = softer fade, narrower = crisp
 *
 * Terrain occlusion is folded into the priority signal:
 *   occluded labels → priority × 0.15 → naturally invisible at normal threshold
 *   when FPS excellent (threshold → 0.10) → occluded labels appear as faint hazes
 * ═══════════════════════════════════════════════════════════════════════════ */
class AdaptiveTextLayer extends (typeof deck !== 'undefined' ? deck.TextLayer : class {}) {
  getShaders() {
    if (!super.getShaders) return {};
    const shaders = super.getShaders();
    shaders.inject = Object.assign(shaders.inject || {}, {
      'fs:#decl': `
        uniform float gpuThreshold;
        uniform float gpuSmoothBand;
      `,
      'fs:DECKGL_FILTER_COLOR': `
        // color.a = priority × sdfGlyphAlpha (SDF already resolved by TextLayer)
        // Glyph edges naturally erode first as priority decreases:
        //   high-priority: core(1.0×p) + edges(0.5×p) both visible  → bold, thick
        //   low-priority:  core(1.0×p) fragile, edges(0.5×p) gone   → hairline, thin
        float signal = color.a;
        float lo     = max(0.0, gpuThreshold - gpuSmoothBand);
        float hi     = gpuThreshold + gpuSmoothBand;
        float fade   = smoothstep(lo, hi, signal);
        color.a = fade;
        if (fade < 0.01) discard;
      `,
    });
    return shaders;
  }

  draw(opts) {
    if (!super.draw) return;
    super.draw({
      ...opts,
      uniforms: Object.assign({}, opts.uniforms, {
        gpuThreshold:  this.props.gpuThreshold  ?? 0.3,
        gpuSmoothBand: this.props.gpuSmoothBand ?? 0.2,
      }),
    });
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * MapLibreDeckCesium
 * ═══════════════════════════════════════════════════════════════════════════ */
class MapLibreDeckCesium {

  /**
   * @param {object} opts
   * @param {string}  opts.containerId          — DOM element id (required)
   * @param {number}  [opts.longitude=-95.37]
   * @param {number}  [opts.latitude=29.76]
   * @param {number}  [opts.zoom=10]
   * @param {string}  [opts.stadiaKey='']       — Stadia API key; enables Stadia vector styles
   * @param {string}  [opts.style='dark']       — named style OR full URL
   * @param {boolean} [opts.cesiumTerrain=false]— mount Cesium for terrain depth
   * @param {string}  [opts.cesiumToken='']     — Cesium Ion access token
   * @param {number}  [opts.targetFps=60]
   * @param {boolean} [opts.rfArcs=true]        — show RF arc layer
   */
  constructor(opts = {}) {
    this._o = {
      containerId:   opts.containerId   || 'map',
      longitude:     opts.longitude     ?? -95.37,
      latitude:      opts.latitude      ?? 29.76,
      zoom:          opts.zoom          ?? 10,
      stadiaKey:     opts.stadiaKey     || '',
      style:         opts.style         || 'dark',
      cesiumTerrain: opts.cesiumTerrain ?? false,
      cesiumToken:   opts.cesiumToken   || '',
      targetFps:     opts.targetFps     ?? 60,
      rfArcs:        opts.rfArcs        ?? true,
    };

    this._map    = null;   // maplibregl.Map
    this._deck   = null;   // deck.Deck
    this._viewer = null;   // Cesium.Viewer (optional)

    this._arcData      = [];
    this._tileCache    = new TileArcCache(200);
    this._activeTiles  = [];
    this._quality      = 1.0;
    this._fps          = 60;
    this._lastTick     = performance.now();

    // ── Label system — GPU-driven SDF (AdaptiveTextLayer) ─────────────────
    this._labelData       = [];        // merged RF/intelligence label set
    this._gpuThreshold    = 0.3;       // shader visibility threshold (0=all, 1=none)
    this._gpuSmoothBand   = 0.20;      // SDF edge transition width (narrow=crisp)
    this._gpuThresholdPinned = false;
    this._labelTileMap    = new Map(); // tileId → label[] (tile-batched cache)
    this._labelGhostMap   = new Map(); // id → ghosted composite signal (temporal decay)
    this._ghostDecay      = 0.12;      // 0=instant, 1=never (EMA blend rate)

    this._onTileLoadCb = null;
    this._onQualityCb  = null;
    this._layersDirty  = false;
  }

  /* ─────────────────────────────────────────────────────────────────────────
   * init — build all engines, returns Promise<this>
   * ----------------------------------------------------------------------- */
  async init() {
    const container = document.getElementById(this._o.containerId);
    if (!container) throw new Error(`[MLDC] #${this._o.containerId} not found`);

    this._buildDOM(container);
    this._initMapLibre();
    this._initDeck();
    if (this._o.cesiumTerrain) this._initCesium();
    this._startQualityMonitor();

    await new Promise(res => {
      if (this._map.loaded()) { res(); return; }
      this._map.once('load', res);
    });

    // Inject explicit geographic label layers after style loads.
    // Ensures city/town/road names are visible even if the chosen style
    // has weak or missing symbol layers (e.g. liberty dark variant).
    this._injectMapLibreLabels();

    // Expose map globally for DevTools debugging: window.__mldc_map
    if (typeof window !== 'undefined') window.__mldc_map = this._map;

    this._redrawLayers();
    console.log('[MLDC] Vector tile hybrid ready');
    return this;
  }

  /* ─────────────────────────────────────────────────────────────────────────
   * DOM scaffolding — three stacked canvases
   * ----------------------------------------------------------------------- */
  _buildDOM(container) {
    container.style.position = 'relative';
    container.style.overflow = 'hidden';

    this._mapDiv = document.createElement('div');
    this._mapDiv.id = `${this._o.containerId}-ml`;
    this._mapDiv.style.cssText = 'position:absolute;inset:0;';
    container.appendChild(this._mapDiv);

    this._deckCanvas = document.createElement('canvas');
    this._deckCanvas.id = `${this._o.containerId}-deck`;
    this._deckCanvas.style.cssText = 'position:absolute;inset:0;pointer-events:none;';
    container.appendChild(this._deckCanvas);

    if (this._o.cesiumTerrain) {
      this._cesiumDiv = document.createElement('div');
      this._cesiumDiv.id = `${this._o.containerId}-cesium`;
      // Semi-transparent terrain overlay; pointer-events off so MapLibre gets clicks
      this._cesiumDiv.style.cssText = 'position:absolute;inset:0;pointer-events:none;opacity:0.3;';
      container.appendChild(this._cesiumDiv);
    }
  }

  /* ─────────────────────────────────────────────────────────────────────────
   * MapLibre GL
   * ----------------------------------------------------------------------- */
  _initMapLibre() {
    if (typeof maplibregl === 'undefined') {
      console.error('[MLDC] maplibregl not loaded');
      return;
    }
    this._map = new maplibregl.Map({
      container:        this._mapDiv,
      style:            this._resolveStyle(),
      center:           [this._o.longitude, this._o.latitude],
      zoom:             this._o.zoom,
      antialias:        true,
      maxTileCacheSize: 150,
    });

    this._map.on('move',   () => this._syncDeckCamera());
    this._map.on('zoom',   () => this._syncDeckCamera());
    this._map.on('rotate', () => this._syncDeckCamera());
    this._map.on('pitch',  () => this._syncDeckCamera());
  }

  _resolveStyle() {
    const { stadiaKey, style } = this._o;
    // Full URL or inline style object — use as-is
    if (style.startsWith('http') || style.startsWith('/') || style.startsWith('{')) return style;
    // Stadia named style with valid key
    if (stadiaKey && ML_STADIA_STYLES[style]) return ML_STADIA_STYLES[style](stadiaKey);
    // Stadia style requested but no key (or quota exhausted) → map to free equivalent
    if (ML_STADIA_STYLES[style]) {
      const free = _STADIA_TO_FREE[style] || 'liberty';
      console.log(`[MLDC] Stadia key absent — using OpenFreeMap "${free}" for "${style}"`);
      return ML_FREE_STYLES[free];
    }
    // Free style by name
    if (ML_FREE_STYLES[style]) return ML_FREE_STYLES[style];
    return _offlineStyle();
  }

  /* ─────────────────────────────────────────────────────────────────────────
   * Deck.gl
   * ----------------------------------------------------------------------- */
  _initDeck() {
    if (typeof deck === 'undefined') { console.error('[MLDC] deck.gl not loaded'); return; }
    this._deck = new deck.Deck({
      canvas:     this._deckCanvas,
      width:      '100%',
      height:     '100%',
      viewState:  this._currentViewState(),
      controller: false,    // MapLibre owns the camera
      layers:     [],
      parameters: { depthTest: false, blend: true },
    });
  }

  _currentViewState() {
    if (!this._map) return {
      longitude: this._o.longitude, latitude: this._o.latitude, zoom: this._o.zoom,
      bearing: 0, pitch: 0,
    };
    const c = this._map.getCenter();
    return {
      longitude: c.lng,
      latitude:  c.lat,
      zoom:      this._map.getZoom(),
      bearing:   this._map.getBearing(),
      pitch:     this._map.getPitch(),
    };
  }

  _syncDeckCamera() {
    this._deck?.setProps({ viewState: this._currentViewState() });
  }

  /* ─────────────────────────────────────────────────────────────────────────
   * MapLibre geographic label injection
   * Adds explicit symbol layers for city/town names if the loaded style has
   * fewer than 3 symbol layers — covers liberty-dark and offline fallback.
   * ----------------------------------------------------------------------- */
  _injectMapLibreLabels() {
    if (!this._map) return;
    const style = this._map.getStyle();
    if (!style) return;

    const existing = (style.layers || []).filter(l => l.type === 'symbol').length;
    if (existing >= 3) return; // style already has adequate labels

    // Ensure the source exists (PBF from OpenFreeMap includes 'place' layer)
    if (!this._map.getSource('openmaptiles')) {
      // Some styles use 'openmaptiles', others 'maptiles' — find the PBF source
      const src = Object.keys(style.sources || {}).find(k => {
        const s = style.sources[k];
        return s.type === 'vector' && (s.url || '').includes('openfreemap');
      });
      if (!src) return; // Can't determine source — skip injection
    }
    const srcId = Object.keys(style.sources || {}).find(k => {
      const s = style.sources[k];
      return s.type === 'vector';
    });
    if (!srcId) return;

    const LABEL_LAYERS = [
      {
        id: 'mldc-city-labels',
        type: 'symbol',
        source: srcId,
        'source-layer': 'place',
        filter: ['in', 'class', 'city', 'capital'],
        minzoom: 3,
        layout: {
          'text-field': '{name:en}',
          'text-font': ['Noto Sans Regular'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 4, 11, 10, 16],
          'text-anchor': 'center',
          'text-allow-overlap': false,
          'symbol-sort-key': ['get', 'rank'],
        },
        paint: {
          'text-color': '#ddeeff',
          'text-halo-color': '#060d1e',
          'text-halo-width': 2,
          'text-opacity': 0.92,
        },
      },
      {
        id: 'mldc-town-labels',
        type: 'symbol',
        source: srcId,
        'source-layer': 'place',
        filter: ['in', 'class', 'town', 'village'],
        minzoom: 7,
        layout: {
          'text-field': '{name:en}',
          'text-font': ['Noto Sans Regular'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 7, 9, 14, 13],
          'text-anchor': 'center',
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': '#aac4dd',
          'text-halo-color': '#060d1e',
          'text-halo-width': 1.5,
          'text-opacity': 0.85,
        },
      },
      {
        id: 'mldc-country-labels',
        type: 'symbol',
        source: srcId,
        'source-layer': 'place',
        filter: ['==', 'class', 'country'],
        maxzoom: 8,
        layout: {
          'text-field': '{name:en}',
          'text-font': ['Noto Sans Bold'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 2, 10, 7, 14],
          'text-anchor': 'center',
          'text-allow-overlap': false,
          'text-transform': 'uppercase',
          'text-letter-spacing': 0.1,
        },
        paint: {
          'text-color': '#7fa8c9',
          'text-halo-color': '#060d1e',
          'text-halo-width': 2,
          'text-opacity': 0.8,
        },
      },
    ];

    for (const layer of LABEL_LAYERS) {
      try {
        if (!this._map.getLayer(layer.id)) this._map.addLayer(layer);
      } catch (e) {
        // source-layer mismatch is non-fatal — style uses different schema
      }
    }
    console.log(`[MLDC] Injected ${LABEL_LAYERS.length} geographic label layers (style had ${existing})`);
  }

  /* ─────────────────────────────────────────────────────────────────────────
   * Deck.gl TextLayer — RF / intelligence labels
   * FPS-adaptive LOD + screen-space declutter + terrain occlusion
   * ----------------------------------------------------------------------- */

  /**
   * Screen-space declutter: grid-bucket labels and keep highest-priority per cell.
   * gridSize is in map units; approximate screen pixels depend on zoom.
   */
  _declusterLabels(labels, zoom) {
    // ~40 CSS pixels at zoom 0, halves each zoom level
    const gridDeg = 40 / (256 * Math.pow(2, zoom)) * 360;
    const grid = new Map();
    for (const l of labels) {
      const gx = Math.floor(l.coordinates[0] / gridDeg);
      const gy = Math.floor(l.coordinates[1] / gridDeg);
      const key = `${gx}:${gy}`;
      const prev = grid.get(key);
      if (!prev || (l.priority || 0) > (prev.priority || 0)) grid.set(key, l);
    }
    return Array.from(grid.values());
  }

  /**
   * Terrain occlusion: mark labels below visible horizon when Cesium viewer active.
   * Returns labels array with `occluded: true` on items behind terrain.
   */
  _applyTerrainOcclusion(labels) {
    if (!this._viewer || typeof Cesium === 'undefined') return labels;
    const globe = this._viewer.scene.globe;
    return labels.map(l => {
      try {
        const carto = Cesium.Cartographic.fromDegrees(l.coordinates[0], l.coordinates[1]);
        const h = globe.getHeight(carto);
        const camH = this._viewer.camera.positionCartographic.height;
        return h !== undefined && h > camH * 0.85
          ? { ...l, occluded: true }
          : l;
      } catch (_) { return l; }
    });
  }

  /**
   * Map RF frequency (Hz) to an RGB hint color for the label.
   * Returns undefined when frequency is unknown — caller uses default color.
   *
   * Band assignments mirror common SIGINT conventions:
   *   HF (3-30 MHz)      → amber / intel yellow
   *   VHF (30-300 MHz)   → lime green (FM/aircraft/naval)
   *   UHF (300-3000 MHz) → cyan     (cellular, WiFi, military UHF)
   *   SHF (3-30 GHz)     → magenta  (radar, satellite, 5G mmWave)
   *   EHF (30+ GHz)      → red      (directed energy / mmWave)
   */
  static _freqToRgb(hz) {
    if (!hz || hz <= 0) return undefined;
    const mhz = hz / 1e6;
    if (mhz < 3)       return [180, 100, 255]; // VLF/LF — purple
    if (mhz < 30)      return [255, 185, 40];  // HF    — amber
    if (mhz < 300)     return [80,  230, 80];  // VHF   — lime
    if (mhz < 3000)    return [0,   220, 240]; // UHF   — cyan
    if (mhz < 30000)   return [220, 60,  220]; // SHF   — magenta
    return               [255, 60,  60];       // EHF+  — red
  }

  /**
   * Compute the composite signal for a label, applying:
   *   1. Multi-channel product:  priority × trust × recency × rfConfidence
   *   2. Terrain attenuation:    ×0.15 when occluded
   *   3. Temporal ghosting:      EMA blend with prior frame (prevents popping)
   *
   * Temporal ghosting: when a node disappears from the data feed, its ghost
   * signal decays at _ghostDecay per frame — creating afterimage trails.
   */
  _compositeSignal(d) {
    const p    = d.priority      ?? 0.5;
    const t    = d.trust         ?? 1.0;
    const r    = d.recency       ?? 1.0;
    const rf   = d.rfConfidence  ?? 1.0;
    const raw  = p * t * r * rf;
    const eff  = d.occluded ? raw * 0.15 : raw;

    // Temporal ghosting: blend with the previous frame's signal
    const id   = d.id || `${(d.coordinates[0] ?? 0).toFixed(5)},${(d.coordinates[1] ?? 0).toFixed(5)}`;
    const prev = this._labelGhostMap.get(id) ?? eff;
    const blended = prev + (eff - prev) * (1.0 - this._ghostDecay);
    this._labelGhostMap.set(id, blended);
    return Math.min(1, Math.max(0, blended));
  }

  /**
   * Evict ghost entries for labels that have completely faded.
   * Called after each layer build to prevent map unbounded growth.
   */
  _pruneGhostMap(activeIds) {
    if (this._labelGhostMap.size < 500) return; // skip if small
    const FADE_THRESHOLD = 0.01;
    for (const [id, sig] of this._labelGhostMap) {
      if (!activeIds.has(id) && sig < FADE_THRESHOLD) this._labelGhostMap.delete(id);
    }
  }

  /**
   * Build the GPU-adaptive SDF label layer.
   *
   * CPU role  : spatial declutter, composite signal (priority×trust×recency×rfConf),
   *             terrain fold, temporal ghost blend
   * GPU role  : SDF edge reconstruction, composite×sdfAlpha → emergent thickness,
   *             visibility fade via gpuThreshold + gpuSmoothBand uniforms
   *
   * Signal composition:  composite = priority × trust × recency × rfConfidence
   *   • Occluded nodes   → ×0.15 → cool haze, diffused through terrain
   *   • Temporal ghosting → EMA blend with prior frame signal (afterimage on loss)
   *   • Frequency hint   → color hue encodes RF band (HF=amber, UHF=cyan, SHF=magenta)
   *
   * Thickness is emergent: composite × sdfGlyphAlpha drives pixel survival.
   *   composite=1.0 → bold;  composite=0.3 → hairline;  composite=0.05 → haze
   */
  _buildLabelLayer() {
    if (typeof deck === 'undefined' || !deck.TextLayer) return null;
    if (!this._labelData.length) return null;

    const MAX_LABELS = 50_000;
    const zoom = this._map ? this._map.getZoom() : 8;

    let labels = this._declusterLabels(this._labelData, zoom);
    if (this._viewer) labels = this._applyTerrainOcclusion(labels);
    if (labels.length > MAX_LABELS) labels = labels.slice(0, MAX_LABELS);

    // Build active-id set for ghost map pruning after render
    const activeIds = new Set(labels.map(d =>
      d.id || `${(d.coordinates[0] ?? 0).toFixed(5)},${(d.coordinates[1] ?? 0).toFixed(5)}`
    ));

    const LayerCtor = (typeof AdaptiveTextLayer !== 'undefined' &&
                       AdaptiveTextLayer.prototype.getShaders)
      ? AdaptiveTextLayer
      : deck.TextLayer;

    const layer = new LayerCtor({
      id:   'rf-labels',
      data: labels,

      fontSettings: {
        sdf:       true,
        smoothing: 0.2,
        radius:    12,
        buffer:    4,
      },

      getPosition: d => d.coordinates,
      getText:     d => d.label || '',
      // Size tracks raw priority (not ghost-blended) — no jitter on slow decay
      getSize:     d => Math.round(Math.max(10, Math.min(18, 10 + (d.priority ?? 0.5) * 8))),
      sizeUnits:   'pixels',
      getAngle:    0,
      getTextAnchor:        'middle',
      getAlignmentBaseline: 'center',
      fontFamily:  '"Courier New", "Courier", monospace',
      fontWeight:  'bold',
      outlineWidth: 2,
      outlineColor: [0, 0, 0, 220],

      // Multi-channel composite signal packed into alpha.
      // Frequency-band hue encoded in RGB (overrides disposition color when available).
      // Temporal ghost blend: disappearing nodes linger as fading afterimages.
      getColor: d => {
        const composite = this._compositeSignal(d);

        // Frequency-band color takes priority when known
        const freqRgb = d.frequencyHz
          ? MapLibreDeckCesium._freqToRgb(d.frequencyHz)
          : undefined;

        const rgb = d.occluded
          ? [100, 155, 210]                               // terrain haze
          : (freqRgb || (d.color ? d.color.slice(0, 3) : [220, 240, 255]));

        return [...rgb, Math.round(composite * 255)];
      },

      pickable:   true,
      parameters: { depthTest: false, blend: true },
      updateTriggers: { getColor: [this._gpuThreshold, this._ghostDecay] },

      gpuThreshold:  this._gpuThreshold,
      gpuSmoothBand: this._gpuSmoothBand,
    });

    // Prune ghost entries that have fully decayed and left the viewport
    // (deferred so ghost signals are already written before prune)
    setTimeout(() => this._pruneGhostMap(activeIds), 0);
    return layer;
  }


  _initCesium() {
    if (typeof Cesium === 'undefined') { console.warn('[MLDC] Cesium not available'); return; }
    if (this._o.cesiumToken) Cesium.Ion.defaultAccessToken = this._o.cesiumToken;

    this._viewer = new Cesium.Viewer(this._cesiumDiv, {
      baseLayerPicker:      false, navigationHelpButton: false,
      animation:            false, timeline:             false,
      geocoder:             false, homeButton:           false,
      sceneModePicker:      false, infoBox:              false,
      selectionIndicator:   false,
      imageryProvider:      false,        // MapLibre drives visuals
      useDefaultRenderLoop: false,        // Manual render via map.on('render')
    });
    this._viewer.imageryLayers.removeAll();
    this._viewer.scene.backgroundColor      = Cesium.Color.TRANSPARENT;
    this._viewer.scene.skyBox.show          = false;
    this._viewer.scene.sun.show             = false;
    this._viewer.scene.moon.show            = false;
    this._viewer.scene.skyAtmosphere.show   = false;

    // Tick Cesium once per MapLibre render frame
    this._map.on('render', () => {
      if (this._viewer && !this._viewer.isDestroyed()) this._viewer.render();
    });
    console.log('[MLDC] Cesium terrain layer mounted');
  }

  /* ─────────────────────────────────────────────────────────────────────────
   * Layer construction
   * ----------------------------------------------------------------------- */
  _redrawLayers() {
    if (!this._deck) return;

    const layers = [];

    // MVT vector feature layer (tile-driven compute trigger)
    if (typeof deck.MVTLayer !== 'undefined') {
      layers.push(new deck.MVTLayer({
        id:                 'vector-features',
        data:               _mvtUrl(this._o.stadiaKey),
        pickable:           true,
        getFillColor:       [20, 35, 65, 180],
        getLineColor:       [70, 110, 180, 200],
        lineWidthMinPixels: 1,
        extruded:           false,
        onViewportLoad:     (tiles) => this._onTileViewportLoad(tiles),
      }));
    }

    // RF arc layer
    if (this._o.rfArcs && this._arcData.length > 0) {
      const arcs = this._qualitySlice(this._arcData);
      const w    = Math.max(0.5, 1.5 * this._quality);
      layers.push(new deck.ArcLayer({
        id:                'rf-arcs',
        data:              arcs,
        getSourcePosition: d => d.source || d.src_pos || [this._o.longitude, this._o.latitude],
        getTargetPosition: d => d.target || d.dst_pos || [this._o.longitude, this._o.latitude],
        getSourceColor:    d => d.sourceColor || [0, 210, 255, 200],
        getTargetColor:    d => d.targetColor  || [255, 50, 140, 200],
        getWidth:          d => w * (d.weight || d.confidence || 0.5),
        widthMinPixels:    0.5,
        greatCircle:       true,
        parameters:        { depthTest: false, blend: true },
      }));
    }

    // RF/intelligence TextLayer labels (FPS-LOD, decluttered, terrain-occluded)
    const labelLayer = this._buildLabelLayer();
    if (labelLayer) layers.push(labelLayer);

    this._deck.setProps({ layers });
  }

  _qualitySlice(data) {
    if (this._quality >= 1.0) return data;
    const n = Math.max(1, Math.floor(data.length * this._quality));
    return [...data]
      .sort((a, b) => (b.confidence || b.weight || 0) - (a.confidence || a.weight || 0))
      .slice(0, n);
  }

  _onTileViewportLoad(tiles) {
    this._activeTiles = (tiles || []).map(t => ({
      id:   `${t.index?.z ?? 0}-${t.index?.x ?? 0}-${t.index?.y ?? 0}`,
      bbox: t.bbox || null,
    }));
    // Rebuild tile-batched labels when viewport changes
    if (this._labelTileMap.size > 0) this._rebuildTileLabels();
    if (typeof this._onTileLoadCb === 'function') this._onTileLoadCb(this._activeTiles, this);
  }

  /* ─────────────────────────────────────────────────────────────────────────
   * Public API — RF/intelligence labels
   *
   * Label format:
   *   { coordinates: [lon, lat], label: string, priority?: 0-1, color?: [r,g,b,a] }
   * ----------------------------------------------------------------------- */

  /** Replace all RF labels (full redraw). */
  setLabelData(labels) {
    this._labelData = Array.isArray(labels) ? [...labels] : [];
    this._redrawLayers();
    return this;
  }

  /** Append a single label or array of labels. */
  pushLabelData(labels, maxSize = 50_000) {
    const arr = Array.isArray(labels) ? labels : [labels];
    for (const l of arr) this._labelData.push(l);
    if (this._labelData.length > maxSize)
      this._labelData.splice(0, this._labelData.length - maxSize);
    this._redrawLayers();
    return this;
  }

  /** Remove all RF labels from the overlay. */
  clearLabels() {
    this._labelData = [];
    this._redrawLayers();
    return this;
  }

  /**
   * Associate labels with a specific tile so they're evicted when the tile
   * leaves the viewport.  tile-batched labels appear only when their tile
   * is in the active viewport set.
   */
  setTileLabels(tileId, labels) {
    this._labelTileMap.set(tileId, labels);
    this._rebuildTileLabels();
    return this;
  }

  _rebuildTileLabels() {
    const activeIds = new Set(this._activeTiles.map(t => t.id));
    const tileLabels = [];
    for (const [id, labels] of this._labelTileMap) {
      if (activeIds.has(id)) tileLabels.push(...labels);
    }
    this._labelData = tileLabels;
    this._redrawLayers();
  }

  /* ─────────────────────────────────────────────────────────────────────────
   * Public API — arc data
   * ----------------------------------------------------------------------- */

  /**
   * Manually pin the GPU visibility threshold (0 = show all, 1 = hide all).
   * Overrides the FPS-adaptive governor for diagnostic/demo use.
   * Pass null to re-enable automatic FPS control.
   */
  setGpuThreshold(t) {
    if (t === null || t === undefined) {
      this._gpuThresholdPinned = false;
    } else {
      this._gpuThresholdPinned = true;
      this._gpuThreshold = Math.max(0, Math.min(1, t));
    }
    this._redrawLayers();
    return this;
  }

  /**
   * Set the temporal ghost decay rate (0 = instant snap, 1 = never fades).
   * Default 0.12: a disappeared node's signal reaches <1% in ~38 frames (~0.6s @60fps).
   */
  setGhostDecay(rate) {
    this._ghostDecay = Math.max(0, Math.min(0.99, rate));
    return this;
  }

  /** Clear all ghost memory — use when the data feed resets or changes context. */
  clearGhosts() {
    this._labelGhostMap.clear();
    this._redrawLayers();
    return this;
  }

  setArcData(arcs) {
    this._arcData = arcs;
    this._redrawLayers();
    return this;
  }

  pushArcData(arcs, maxSize = 100_000) {
    for (const a of arcs) this._arcData.push(a);
    if (this._arcData.length > maxSize) this._arcData.splice(0, this._arcData.length - maxSize);
    this._redrawLayers();
    return this;
  }

  /** Replace the full Deck.gl layer stack (advanced override) */
  setLayers(layers) {
    this._deck?.setProps({ layers });
    return this;
  }

  /* ─────────────────────────────────────────────────────────────────────────
   * Public API — map control
   * ----------------------------------------------------------------------- */
  flyTo(longitude, latitude, zoom = 12, pitch = 0) {
    this._map?.flyTo({ center: [longitude, latitude], zoom, pitch });
    return this;
  }

  /** Hot-swap map style at runtime */
  setStyle(nameOrUrl) {
    this._o.style = nameOrUrl;
    this._map?.setStyle(this._resolveStyle());
    return this;
  }

  getActiveTileBounds() { return this._activeTiles.map(t => t.bbox).filter(Boolean); }
  getDeck()             { return this._deck;   }
  getMap()              { return this._map;    }
  getViewer()           { return this._viewer; }
  getQuality()          { return this._quality; }

  /* ─────────────────────────────────────────────────────────────────────────
   * Callbacks
   * ----------------------------------------------------------------------- */
  onTileLoad(cb)      { this._onTileLoadCb = cb; return this; }
  onQualityChange(cb) { this._onQualityCb  = cb; return this; }

  /* ─────────────────────────────────────────────────────────────────────────
   * GPU-adaptive quality governor (FPS-based EMA)
   * ----------------------------------------------------------------------- */
  _startQualityMonitor() {
    const tick = () => {
      const now = performance.now();
      const dt  = Math.max(1, now - this._lastTick);
      this._lastTick = now;

      const inst = 1000 / dt;
      this._fps  = 0.9 * this._fps + 0.1 * inst;

      // Mesh quality (arc detail, imagery switching)
      const prevQ = this._quality;
      if (this._fps < 28)      this._quality = Math.max(0.15, this._quality * 0.92);
      else if (this._fps > 55) this._quality = Math.min(1.5,  this._quality * 1.03);

      // GPU label threshold — FPS controls label visibility, not array slicing.
      // Raise threshold when FPS is low  → low-priority labels fade out.
      // Lower threshold when FPS is high → more labels become visible.
      // Skip when threshold is manually pinned (setGpuThreshold).
      const prevT = this._gpuThreshold;
      if (!this._gpuThresholdPinned) {
        if      (this._fps < 30) this._gpuThreshold = Math.min(0.85, this._gpuThreshold + 0.015);
        else if (this._fps > 55) this._gpuThreshold = Math.max(0.10, this._gpuThreshold - 0.008);
      }

      // SDF smooth band — widen when FPS drops (softer, less GPU work per edge),
      // narrow when FPS is excellent (crisp, sharp SDF edges).
      const prevB = this._gpuSmoothBand;
      if (!this._gpuThresholdPinned) {
        if      (this._fps < 30) this._gpuSmoothBand = Math.min(0.40, this._gpuSmoothBand + 0.01);
        else if (this._fps > 55) this._gpuSmoothBand = Math.max(0.08, this._gpuSmoothBand - 0.005);
      }

      const meshChanged   = Math.abs(this._quality      - prevQ) > 0.04;
      const threshChanged = Math.abs(this._gpuThreshold - prevT) > 0.01;
      const bandChanged   = Math.abs(this._gpuSmoothBand - prevB) > 0.01;

      if (meshChanged || threshChanged || bandChanged) {
        if (meshChanged && typeof this._onQualityCb === 'function')
          this._onQualityCb(this._quality, this._fps);
        this._redrawLayers();
      }

      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  /* ─────────────────────────────────────────────────────────────────────────
   * Diagnostics
   * ----------------------------------------------------------------------- */
  statusLine() {
    return `MLDC q=${this._quality.toFixed(2)} fps=${Math.round(this._fps)} thr=${this._gpuThreshold.toFixed(2)} band=${this._gpuSmoothBand.toFixed(2)} arcs=${this._arcData.length} labels=${this._labelData.length} tiles=${this._activeTiles.length}`;
  }

  getStatus() {
    return {
      quality:       +this._quality.toFixed(3),
      fps:           +this._fps.toFixed(1),
      gpuThreshold:  +this._gpuThreshold.toFixed(3),
      gpuSmoothBand: +this._gpuSmoothBand.toFixed(3),
      arcCount:      this._arcData.length,
      labelCount:    this._labelData.length,
      tileCount:     this._activeTiles.length,
      cacheSize:     this._tileCache.size,
      style:         this._o.style,
    };
  }

  /* ─────────────────────────────────────────────────────────────────────────
   * Destroy
   * ----------------------------------------------------------------------- */
  destroy() {
    this._deck?.finalize();
    this._map?.remove();
    if (this._viewer && !this._viewer.isDestroyed()) this._viewer.destroy();
  }
}

/* ─── Module / global exports ────────────────────────────────────────────── */
if (typeof module !== 'undefined') {
  module.exports = { MapLibreDeckCesium, AdaptiveTextLayer, RFArcLayer, TileArcCache, ML_STADIA_STYLES, ML_FREE_STYLES };
} else {
  window.MapLibreDeckCesium = MapLibreDeckCesium;
  window.AdaptiveTextLayer  = AdaptiveTextLayer;
  window.RFArcLayer         = RFArcLayer;
  window.TileArcCache       = TileArcCache;
  window.ML_STADIA_STYLES   = ML_STADIA_STYLES;
  window.ML_FREE_STYLES     = ML_FREE_STYLES;
}
