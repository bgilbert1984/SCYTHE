# The Globe as a Hypothesis Compiler: Inside SCYTHE's NTIA ITM Regional RF Demo

**Date:** August 16, 2026  
**Author:** SCYTHE Core Engineering Team  
**Category:** Clarktech, Geospatial Operations, GraphOps, RF Engineering, Human–Machine Teaming

---

Scientific visualization reaches its true potential when it moves beyond presenting static representations of data and begins operating as an interactive, multi-dimensional hypothesis compiler. In complex operations where physical signals, geospatial propagation, and digital network traffic converge, analysts must be able to ask deep causal questions, test counterfactual scenarios, and enforce strict boundaries between what is observed, what is inferred, and what is synthesized.

Today, we are diving deep into the architecture, capabilities, and underlying interactions of SCYTHE's master regional operational viewport: **The NTIA ITM Regional RF Demo** (`regional-rf-demo.html`). 

Accessible locally at `http://127.0.0.1:5001/scythe-web/regional-rf-demo.html`, this web interface integrates terrain-aware electromagnetic propagation modeling—utilizing the National Telecommunications and Information Administration (NTIA) Integrated Transmission Model (ITM)—with the high-density Eve network hypergraph.

```text
  [Measured RF Ingestion] ══>  /api/graphops/rf-observations
                                            ||
                                            \/
  [NTIA ITM Path-Loss Engine] ══> [GraphOps Directive Solver] <══ [Eve Live Hypergraph]
                                            ||
                                            \/
+───────────────────────────────────────────────────────────────────────────────────+
|                                  CESIUM 3D GLOBE                                  |
|  - Inferred Centroids      - Translucent Uncertainty Rings      - Geodesic Connectors  |
+───────────────────────────────────────────────────────────────────────────────────+
                                    ▲           ▲
                    ┌───────────────┘           └───────────────┐
                    │                                           │
         [Live Hypergraph Panel]                     [GraphOps Directive Panel]
      - 3D Causal Chamber (Three.js)               - Reality Prism & DSL Previews
      - 2D Accessible (SVG)                        - Local vs. Cloud Ask (Ollama)
      - Location Estimates (GeoIP)                 - Counterfactual Threshold Lenses
      - Infrastructure Lens (InfraFlow)            - Multi-World Causal Comparisons
```

---

## Dual-Panel Workspace: Visualizing the Convergence

The interface is structured as a dual-panel cockpit designed to hold state across complex investigative workflows. Both the **Live Network Hypergraph Viewport** (`#live-hypergraph`) on the left and the **GraphOps Directive Panel** (`#graphops-directive`) on the right feature independent `ResizablePanel` handles. These panels persist their dimensions via the browser’s `sessionStorage`, ensuring a customized, stable layout that remains persistent across page reloads.

At the base of the viewport, the interface presents a comprehensive **Interactive Legend**. This legend serves as an epistemic contract for the operator, mapping flow types (e.g., HTTP in cyan, DNS in yellow, Security in red, TLS in purple, Discovery in orange) and topological properties. It explicitly states that edge lengths represent endpoint separation in layout space, *never* physical latency, fiber routing, or transit speed.

---

## Nine Viewports: The Hypergraph Under Different Prisms

The Live Hypergraph Viewport provides nine distinct modes of inspection to prevent cognitive overload:

1. **3D Causal Chamber:** A local physical workspace rendered via **Three.js** and **OrbitControls**. When the browser supports WebGL, it simulates causal graph chains in a 3D gravity-well layout, mapping the network topology dynamically. If WebGL is unavailable, the system transparently falls back to a 2D SVG rendering without breaking the operator's workflow.
2. **2D Accessible:** A clean, standards-compliant SVG rendering pinned to the active graph revision, optimized for direct coordinate manipulation.
3. **Location Estimates:** Projected GeoIP host coordinates mapped to physical centroids. This viewport renders translucent circles representing the calculated GeoIP uncertainty radius.
4. **Infrastructure Lens:** The front-end control surface for **InfraFlow**. It displays domain cards for observed hosts and flow cards for retained graph edges, sorting evidence into data-plane, control-plane (RIPE RIS Live), and self-reported infrastructure (PeeringDB) layers.
5. **Graph Explorer:** A multi-field search index permitting the operator to filter nodes and edges by query strings (such as IP, ASN, organization, or port), protocols (`tcp` / `udp`), and precise temporal windows (`start` / `end` timestamps). It also features an exploration depth control (`0` / `1` / `2` hops) to examine local topological neighborhoods.
6. **Autopilot:** The operational viewport for GraphOps Sentinel's autonomous patrols, highlighting suggestions and low-confidence logs.
7. **Semantic:** Selection-aware similarity searches against the FAISS-backed semantic memory, identifying previously recorded behavioral analogues.
8. **Spectrum:** Real-time displays of the RF bridge state and FFT summaries.
9. **Events:** Bounded streams of tactical graph events.

```text
+----------------------- LIVE HYPERGRAPH VIEWPORTS -----------------------+
|  [3D CHAMBER] [2D VIEW] [LOCATION] [INFRASTRUCTURE] [EXPLORER] [MORE]   |
+-------------------------------------------------------------------------+
|                                                                         |
|  Viewport Mode: Infrastructure Lens (InfraFlow ACTIVE)                  |
|                                                                         |
|  +---------------------------+       +-------------------------------+  |
|  | PeeringDB (Self-Reported) |       | RIPE RIS Live (Control Plane) |  |
|  | ASN: 15169 (Google)       |       | Message ID: ris-9014          |  |
|  | Facility: Equinix Ashburn |       | Prefix: 8.8.8.0/24            |  |
|  +---------------------------+       +-------------------------------+  |
|                                                                         |
+-------------------------------------------------------------------------+
```

---

## Geolocation Vantage: Anchoring the Unlocated

Geographic visualization on a 3D globe becomes highly problematic when dealing with private addresses (RFC 1918) or multicast groups (e.g., `224.0.0.1`). In conventional systems, these are either thrown out or arbitrarily mapped to the equator or prime meridian, creating visual clutter.

SCYTHE introduces the **Measured Geolocation Vantage** (`⌖ VANTAGE`) control within the Cesium overlay details menu (`◎`). 
When clicked, the client invokes `navigator.geolocation.getCurrentPosition` with strict operational options:
* `enableHighAccuracy: true`
* `timeout: 12_000` (12 seconds)
* `maximumAge: 60_000` (1 minute cache)

```javascript
navigator.geolocation.getCurrentPosition((position) => {
  browserSensorVantage = {
    latitude: position.coords.latitude,
    longitude: position.coords.longitude,
    heightMeters: position.coords.altitude ?? 0,
    accuracyMeters: position.coords.accuracy,
    authority: "MEASURED_BROWSER_GEOLOCATION",
    evidenceClass: "MEASURED",
    sensorId: "browser-capture-vantage",
    capturedAt: new Date(position.timestamp).toISOString()
  };
  graphOverlay.setSensorVantage(browserSensorVantage);
  // Updates UI button state to show accuracy e.g. "⌖ ±12m"
}, handleError, options);
```

Once established, this vantage acts as the localized origin. All unlocated, private, and multicast flows are dynamically anchored to these coordinates on the Cesium globe, providing immediate spatial context for local captures.

---

## GraphOps Directives: Compiling Counterfactual Realities

The right-hand panel exposes the **GraphOps Directive Panel**, which interfaces directly with SCYTHE's `SelectionModel` and `InvestigationContext`. Operators can multi-select diverse evidence classes—such as an RF coverage cell on the globe, two distinct UTC time-pins, and a series of network nodes—and compile them into a unified directive request.

Through this panel, operators can execute several powerful analytical actions:

### 1. Counterfactual Path-Loss Modeling (`#apply-threshold`)
The operator can enter a path-loss threshold (e.g., `145` dB) and click **APPLY LENS**. The client compiles a reclassification directive and transmits it via `POST /api/graphops/directives/reclassify.coverage-threshold`. The server evaluates the NTIA ITM propagation values against the new threshold, and returns a modified visual plan. The client's `EffectRuntime` applies these visual modifications instantly, updating red coverage gaps on the globe without mutating the underlying measurements.

### 2. Physical RF Ingestion (`#ingest-rf`)
To ground propagation models with real-world observations, the operator can input local spectral values (Sensor ID, center frequency in MHz, peak power in dBFS, and noise floor in dBFS) and click **INGEST MEASURED RF**. The client serializes the payload, calculates absolute Hz, and transmits it:

```json
{
  "sensor_id": "manual-1",
  "sequence": 1781613412000,
  "timestamp": 1781613412.000,
  "center_frequency_hz": 900000000,
  "peak_frequency_hz": 900000000,
  "sample_rate_hz": 2400000,
  "peak_dbfs": -45,
  "noise_floor_dbfs": -80
}
```

The response is posted directly to the correlation status display, explicitly reporting if raw IQ was rejected by security contracts.

### 3. Causal World Comparisons (`#compare-worlds`)
When both an RF cell and a network event are selected, the **COMPARE CAUSAL WORLDS** button is enabled. This compiles a directive asking: *What must be true for this RF coverage gap and network burst to share a cause?* 

The server evaluates terrain boundaries, clock skew, and device characteristics, presenting competing benign (e.g., terrain obstruction, clock drift) and adversarial hypotheses. It synchronizes dual Cesium cameras and displays a causal-difference overlay, exposing unresolved tensions directly on the globe.

---

## Full-Fidelity Cloud: Bounded, Checked, and Honest

When investigating a selected host, the interface provides two distinct reasoning paths:
1. **ASK OLLAMA:** An entirely local, offline, on-premises reasoning model.
2. **ASK CLOUD // FULL FIDELITY:** A path that transmits exact, bounded evidence to Ollama Cloud for high-performance interpretation.

Before transmitting anything to the cloud, SCYTHE enforces a strict transparency contract. Clicking the Cloud button prompts the operator with an explicit, dynamically updated confirmation dialog. This dialog reports the exact contents of the outbound capsule: the count of IP addresses, inferred locations, and decoded packet fields. It guarantees that raw payloads, cookies, and local execution credentials are excluded.

```text
+----------------------- FULL-FIDELITY CLOUD DISCLOSURE -----------------------+
|                                                                              |
|  Destination: Ollama Cloud                                                  |
|  Scope: Selected Flow + Packet Dissections + Endpoint Context                |
|                                                                              |
|  Exact IP Addresses: 4                                                       |
|  Exact Locations: 2 (Inferred GeoIP)                                         |
|  Decoded Packet Fields: 18                                                   |
|  Raw Payloads: 0 (EXCLUDED)                                                  |
|                                                                              |
|  Transmit this bounded evidence capsule?                                     |
|                                                                              |
|                     [ CANCEL ]             [ TRANSMIT ]                      |
+------------------------------------------------------------------------------+
```

Once transmitted, the resulting prose is subjected to **Deterministic Epistemic Validation** before rendering. This validation layer ensures the model cannot over-extrapolate its findings:
* **Distance Cap:** The model is forbidden from translating ICMP round-trip time (RTT) into physical route distance or location claims.
* **Absence of Proof:** The failure to observe a VPN or relay cannot be reported as proof of its absence.
* **Confidence Ceiling:** Uncorroborated GeoIP coordinates cap the confidence rating at `0.60`. A detected physics warning (e.g., impossible segment propagation speed) caps it at `0.50`. Any violation of these limits replaces the model's claim with an explicit warning and drops the confidence score to `0.25`.

---

## What’s Next: Unified Mission Planning

The NTIA ITM Regional RF Demo represents SCYTHE's transition from visualization to a scientific instrument. By enforcing strict separation between data layers, allowing real-time counterfactual testing, and implementing deterministic validation on cloud reasoning, we ensure that operators can investigate complex anomalies with high speed and absolute scientific integrity.

The next stages of our front-end deployment will introduce:
1. **Dynamic Antenna Profiling:** Interactively modifying transmitter patterns directly on Cesium using the GraphOps panel.
2. **Multi-Sensor Time Warping:** Sweeping across multi-day SQLite WAL logs to trace signal drift across regional sectors.
3. **Automated Falsifier Placement:** Dragging visual "falsifier targets" onto the globe to suggest the exact physical coordinates where a mobile sensor must be placed to resolve a causal disagreement.


Architectural Critique of @SCYTHE/scythe-web/regional-rf-demo.html

  The NTIA ITM Regional RF Demo represents a world-class visual instrument that successfully prioritizes epistemic
  modesty. By treating the globe as a hypothesis compiler rather than a flat visualization dashboard, it enforces
  critical boundaries between observed physical phenomena, self-reported declarations, and inferred model claims.

  ---

  1. Structural & Architectural Strengths

   * Epistemic Separation of Concerns: The interface separates measurements (Suricata observed data-plane flows,
     active traceroutes), control-plane reports (RIPE RIS Live), self-reported infrastructure (PeeringDB), and
     calculated projections (GeoIP coordinates, AS paths).
   * Decoupled State & Interaction Models: Leveraging dedicated instances of SelectionModel, InvestigationStore,
     and InvestigationContext ensures that visual assets do not mutate the underlying analytical state. State
     transitions and active selections remain highly repeatable.
   * Unified Workspace Portability: The offline snapshoting capability (📦 BUNDLE) provides a robust path for
     exporting self-contained hypergraphs, facilitating standalone analysis in restricted or air-gapped
     environments.
   * Resilient WebGL Fallback: Dual layout engines (Three.js 3D Causal Chamber with automatic SVG 2D fallback)
     guarantee high accessibility across low-resource machines or restricted VM sandboxes.

  ---

  2. Current Technical Limitations & Anti-Patterns

   * Monolithic Inline Script Block: The inline module script in regional-rf-demo.html exceeds 500 lines of complex
     orchestrator glue code, mixing UI bindings, REST fetch requests, Cesium initialization, and state tracking.
     This severely limits testability.
   * Namespace Pollution via Global Binding: The codebase binds instances directly to the global window object
     (window.viewer, window.scytheWebClient, window.scytheInvestigationContext, etc.). This introduces tight
     coupling and makes embedding the viewport into broader operational dashboards prone to collision.
   * Direct UI-to-API Fetch Calls: REST requests (such as submitDirective and the /api/graphops/rf-observations
     ingestion path) are hardcoded directly into the event listeners. They bypass a structured API wrapper, which
     complicates mocking, authentication injection, and unified offline/retry policies.
   * Fragile Geolocation Fallbacks: The #live-geo-vantage system relies exclusively on
     navigator.geolocation.getCurrentPosition. In secure operations centers (SOCs) or remote field setups where
     browser-based location services are blocked, the client fails to provide manual fallback inputs or NMEA
     hardware overrides.

  ---

  3. Structural Roadmap: Where To Go From Here

  To transition the demo from a powerful prototype to an enterprise-grade scientific instrument, the system should
  evolve across the following four phases:

  Phase 1: Controller & API Layer Extraction (Refactoring)
   * Decouple the Controller: Extract the inline module script into a dedicated regionalRfController.js script to
     improve code hygiene.
   * Implement ScytheApiClient: Create a single, promise-based API abstraction class. This class should encapsulate
     all endpoints under /api/graphops/*, handling headers, error handling, and offline-queue logic, and
     integrating with the existing graphTransportNotice channel.
   * Remove Global Bindings: Refactor components to use explicit dependency injection or a localized singleton
     registry rather than polluting the window namespace.

  Phase 2: Hardware-Bound Vantage Integration (Operational Depth)
   * WebSocket-Streamed NMEA GPS: Support reading real-time coordinate data from local GPS rigs via WebSockets
     instead of relying solely on browser geolocators.
   * Dynamic Geodesic Uncertainty Bounds: Render dynamic physical boundary overlays on Cesium that expand and
     contract based on the incoming sensor’s horizontal dilution of precision (HDOP).

  Phase 3: Dynamic Antenna Profiling & Propagation Sandboxing (Counterfactual Power)
   * Interactive Transmitter Manipulation: Allow operators to drag transmitter entities across the Cesium globe,
     adjusting variables like antenna height, pattern (omnidirectional vs. sectorizedized), and gain directly
     within the GraphOps panel.
   * Real-Time Path-Loss Re-solving: Integrate the /api/graphops/directives/reclassify.coverage-threshold handler
     with a visual diff engine, immediately coloring regions green or red based on the compiled NTIA ITM
     propagation results.

  Phase 4: SQLite WAL Temporal Scrubbing (Historical Deep-Dive)
   * Synchronized Timeline Scrubbing: Bind the Cesium timeline widget directly to the since and until boundaries of
     the backend runtime/graphops_ris_live.sqlite database.
   * Causal "Falsifier" Overlay: Allow operators to place mock target points on the globe, generating
     recommendations for where physical sensors must be deployed to resolve competing causal worlds.

     To successfully transition the SCYTHE NTIA ITM Regional RF Demo from a high-fidelity demonstration into an
  enterprise-grade scientific instrument, the suggested roadmap is expanded below with concrete architectural
  patterns, API contracts, class designs, and database schemas.

  ---

  Phase 1: Controller Extraction & API Contract Standardization

  To eliminate the monolithic 500+ line inline script, we isolate visual concerns, state machines, and remote API
  transactions into highly testable, decoupled modules.

    1 [regional-rf-demo.html View]
    2           │
    3           ▼ (User Events / Selection)
    4 [RegionalRfController] ═══════════> [ScytheSelectionModel]
    5           │                                 │
    6           ▼ (API Request)                   ▼ (Refreshes Context)
    7 [ScytheApiClient] <═════════════════[ScytheInvestigationContext]
    8           │
    9           ▼ (gRPC / JSON REST)
   10 [SCYTHE Orchestrator Backend]

  1.1 Implementation of ScytheApiClient (scytheApiClient.js)
  This class provides a unified, promise-based transport layer. It encapsulates all network interactions, injection
  of authentication headers, and implements an offline queuing/retry mechanism synchronized with the global
  graphTransportNotice.

    1 /**
    2  * Unified API Client for SCYTHE GraphOps and RF Operations.
    3  */
    4 export class ScytheApiClient {
    5   constructor({ baseUrl = "", credentials = "same-origin" } = {}) {
    6     this.baseUrl = baseUrl;
    7     this.credentials = credentials;
    8   }
    9
   10   async _request(path, { method = "GET", body = null, signal = null } = {}) {
   11     const options = {
   12       method,
   13       credentials: this.credentials,
   14       headers: { "Content-Type": "application/json" },
   15       signal
   16     };
   17     if (body) options.body = JSON.stringify(body);
   18
   19     const response = await fetch(`${this.baseUrl}${path}`, options);
   20     const data = await response.json();
   21     if (!response.ok) {
   22       throw new Error(data.error ?? `HTTP Error ${response.status}: ${response.statusText}`);
   23     }
   24     return data;
   25   }
   26
   27   async submitDirective(requestedMode, directivePayload) {
   28     return this._request(`/api/graphops/directives/${requestedMode}`, {
   29       method: "POST",
   30       body: directivePayload
   31     });
   32   }
   33
   34   async ingestRfObservation(payload) {
   35     return this._request("/api/graphops/rf-observations", {
   36       method: "POST",
   37       body: payload
   38     });
   39   }
   40
   41   async queryInfrastructureSnapshot() {
   42     return this._request("/api/graphops/infrastructure/snapshot");
   43   }
   44 }

  1.2 The Controller Class (RegionalRfController.js)
  This controller initializes the sub-viewports, binds to the ScytheApiClient, and listens to selection changes.

    1 export class RegionalRfController {
    2   constructor({ view, client, selectionModel, investigationContext }) {
    3     this.view = view; // DOM element references
    4     this.client = client; // ScytheApiClient instance
    5     this.selections = selectionModel;
    6     this.context = investigationContext;
    7   }
    8
    9   init() {
   10     this._bindDomEvents();
   11     this._setupSelectionSubscription();
   12   }
   13
   14   async handleRfIngestion() {
   15     const payload = this.view.getRfIngestionFields();
   16     this.view.setIngestButtonState("working");
   17     try {
   18       const result = await this.client.ingestRfObservation(payload);
   19       this.view.renderIngestSuccess(result);
   20     } catch (error) {
   21       this.view.renderIngestError(error);
   22     }
   23   }
   24
   25   _setupSelectionSubscription() {
   26     this.selections.subscribe((selection) => {
   27       // Coordinate focus across 3D, Location, and Infra views without globals
   28       this.view.updateFocus(selection.entityId);
   29     });
   30   }
   31 }

  ---

  Phase 2: Hardware-Bound Vantage & Edge Integration

  Field operations in degraded or denied environments require alternatives to standard browser geolocation. We
  integrate standard hardware NMEA data streaming and dynamic uncertainty visualization.

   1 [Local GPS/SDR Hardware Rig] 
   2           │ (USB Serial / Bluetooth)
   3           ▼
   4 [SDRPP Edge Bridge / NMEA Feed]
   5           │ (WebSocket Stream)
   6           ▼
   7 [scythe-web Client] ════> [Vantage State Machine] ════> [Cesium Ellipsoid Primitive]
   8                                                         (Dynamic Uncertainty Bounds)

  2.1 Vantage State Machine (VantageManager.js)
  We introduce a robust manager capable of switching between different telemetry providers:

    1 export const VantageSource = {
    2   BROWSER: "BROWSER_GEOLOCATION",
    3   GPSD_NMEA: "GPSD_NMEA_STREAM",
    4   MANUAL: "MANUAL_OPERATOR_INPUT"
    5 };
    6
    7 export class VantageManager {
    8   constructor({ overlayLayer, webSocketUrl = "ws://127.0.0.1:8080/gps" }) {
    9     this.overlay = overlayLayer;
   10     this.wsUrl = webSocketUrl;
   11     this.activeSource = VantageSource.BROWSER;
   12     this.socket = null;
   13   }
   14
   15   setSource(source, manualCoordinates = null) {
   16     this.activeSource = source;
   17     this._disconnectSocket();
   18
   19     switch (source) {
   20       case VantageSource.GPSD_NMEA:
   21         this._connectGpsdSocket();
   22         break;
   23       case VantageSource.MANUAL:
   24         this._updateOverlay(manualCoordinates);
   25         break;
   26       case VantageSource.BROWSER:
   27       default:
   28         this._useBrowserLocation();
   29     }
   30   }
   31
   32   _connectGpsdSocket() {
   33     this.socket = new WebSocket(this.wsUrl);
   34     this.socket.onmessage = (event) => {
   35       const gpsData = JSON.parse(event.data); // Expects class TPV or normalized NMEA
   36       if (gpsData.class === "TPV" && gpsData.mode >= 2) {
   37         this._updateOverlay({
   38           latitude: gpsData.lat,
   39           longitude: gpsData.lon,
   40           heightMeters: gpsData.alt ?? 0,
   41           accuracyMeters: gpsData.epx ?? gpsData.eph ?? 10,
   42           authority: "HARDWARE_GPS_NMEA",
   43           evidenceClass: "MEASURED"
   44         });
   45       }
   46     };
   47   }
   48
   49   _updateOverlay(vantage) {
   50     this.overlay.setSensorVantage(vantage);
   51   }
   52 }

  2.2 Visualizing Dilution of Precision (HDOP)
  Instead of a simple point pin, Cesium displays a translucent uncertainty ellipsoid that reflects real-time
  satellite configuration precision.

    1 function drawUncertaintyBound(viewer, Cesium, vantage) {
    2   const center = Cesium.Cartesian3.fromDegrees(vantage.longitude, vantage.latitude, vantage.heightMeters);
    3   const accuracy = vantage.accuracyMeters;
    4
    5   viewer.entities.add({
    6     id: "scythe-web:vantage:uncertainty",
    7     position: center,
    8     ellipse: {
    9       semiMajorAxis: accuracy,
   10       semiMinorAxis: accuracy,
   11       height: vantage.heightMeters,
   12       material: new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString("rgba(0, 212, 255, 0.12)")),
   13       outline: true,
   14       outlineColor: Cesium.Color.fromCssColorString("#00d4ff"),
   15       outlineWidth: 2
   16     }
   17   });
   18 }

  ---

  Phase 3: Interactive Counterfactual & Propagation Sandbox

  Instead of executing static propagation requests, we turn the Cesium viewport into a real-time signal simulator.
  Operators can position transmitters, modify physical traits, and preview coverage shifts.

    1 1. Operator Drags Emitter ═══> Cesium ScreenSpaceEventHandler
    2                                            │
    3 2. Client Compiles Payload <═══════════════┘
    4    { id: "tx-45", lat: 38.89, lon: -77.03, antenna: "sectorized" }
    5                                            │
    6 3. ScytheApiClient dispatches to /api/graphops/directives/reclassify
    7                                            │
    8 4. Python Backend runs NTIA ITM Solver ════┘
    9                                            │
   10 5. Modified Plan Returns ═══> EffectRuntime updates display layers on Cesium

  3.1 Screen-Space Event Handler for Transmitters

    1 export class CounterfactualTxSandbox {
    2   constructor({ viewer, Cesium, apiClient }) {
    3     this.viewer = viewer;
    4     this.Cesium = Cesium;
    5     this.client = apiClient;
    6     this.handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
    7     this.selectedTx = null;
    8   }
    9
   10   enableDragAndDrop() {
   11     this.handler.setInputAction((click) => {
   12       const picked = this.viewer.scene.pick(click.position);
   13       if (Cesium.defined(picked) && picked.id?.id?.startsWith("scythe:transmitter:")) {
   14         this.selectedTx = picked.id;
   15         this.viewer.scene.screenSpaceCameraController.enableRotate = false;
   16       }
   17     }, Cesium.ScreenSpaceEventType.LEFT_DOWN);
   18
   19     this.handler.setInputAction((movement) => {
   20       if (!this.selectedTx) return;
   21       const cartesian = this.viewer.camera.pickEllipsoid(movement.endPosition,
      this.viewer.scene.globe.ellipsoid);
   22       if (cartesian) {
   23         this.selectedTx.position = cartesian;
   24       }
   25     }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
   26
   27     this.handler.setInputAction(() => {
   28       if (this.selectedTx) {
   29         this._triggerRecomputation(this.selectedTx);
   30         this.selectedTx = null;
   31         this.viewer.scene.screenSpaceCameraController.enableRotate = true;
   32       }
   33     }, Cesium.ScreenSpaceEventType.LEFT_UP);
   34   }
   35
   36   async _triggerRecomputation(entity) {
   37     const cartographic =
      this.Cesium.Cartographic.fromCartesian(entity.position.getValue(this.viewer.clock.currentTime));
   38     const payload = {
   39       directive: "reclassify.coverage-threshold",
   40       utterance: "Recalculate path-loss on move",
   41       mode: "execute",
   42       parameters: {
   43         transmitterId: entity.id,
   44         latitudeDegrees: this.Cesium.Math.toDegrees(cartographic.latitude),
   45         longitudeDegrees: this.Cesium.Math.toDegrees(cartographic.longitude),
   46         frequencyMhz: 900.0,
   47         antennaHeightMeters: 15.0
   48       }
   49     };
   50     const plan = await this.client.submitDirective("reclassify.coverage-threshold", payload);
   51     // Visual update applied immediately by the visual effect runtime
   52   }
   53 }

  ---

  Phase 4: SQLite WAL Temporal Scrubbing & Hypothesis Compilation
   1 [RIPE RIS Live Stream] ──┐
   2                          ▼
   3    [Suricata EVE TCP] ═══════> [sqlite WAL Database] ═══════> [since / until API Query]
   4                          ▲
   5  [PeeringDB Daily Job] ──┘
   6                                                                      │
   7                                                                      ▼
   8 [Causal World A (Observed)] <═══ [Causal State Machine] ═══> [Causal World B (Inferred)]

  4.1 SQLite Schema for RIS and Flow Persistence (graphops_ris_live.sqlite)

    1 PRAGMA journal_mode=WAL;
    2 PRAGMA synchronous=NORMAL;
    3
    4 -- Persisted control plane observations
    5 CREATE TABLE IF NOT EXISTS control_plane_observations (
    6     message_id TEXT PRIMARY KEY,
    7     collector_id TEXT NOT NULL,
    8     peer_asn INTEGER NOT NULL,
    9     prefix TEXT NOT NULL,
   10     as_path TEXT NOT NULL,
   11     origin_asn INTEGER NOT NULL,
   12     receive_timestamp REAL NOT NULL,
   13     inserted_at TEXT DEFAULT CURRENT_TIMESTAMP
   14 );
   15
   16 CREATE INDEX IF NOT EXISTS idx_ris_prefix ON control_plane_observations(prefix);
   17 CREATE INDEX IF NOT EXISTS idx_ris_time ON control_plane_observations(receive_timestamp);
   18
   19 -- Persisted structural conflicts
   20 CREATE TABLE IF NOT EXISTS infrastructure_contradictions (
   21     contradiction_id TEXT PRIMARY KEY,
   22     kind TEXT NOT NULL, -- 'ORIGIN_DISAGREEMENT', 'WITHDRAWAL_WITH_DATA_PLANE_ACTIVITY'
   23     target_prefix TEXT NOT NULL,
   24     evidence_source_a TEXT NOT NULL, -- e.g., 'local_prefix_database'
   25     evidence_source_b TEXT NOT NULL, -- e.g., 'ripe_ris_live_collector'
   26     timestamp_start REAL NOT NULL,
   27     timestamp_end REAL NOT NULL,
   28     falsifier_description TEXT NOT NULL
   29 );

  4.2 Multi-World Delta State Machine (CausalDifferenceEngine.js)
  We leverage two separate time-pins to establish the precise boundary when observed data plane flows began
  contradicting declared control plane paths.

    1 export class CausalDifferenceEngine {
    2   constructor({ api }) {
    3     this.api = api;
    4   }
    5
    6   async compileCausalDeltas(pinFromEpoch, pinToEpoch) {
    7     const query = {
    8       since: pinFromEpoch,
    9       until: pinToEpoch
   10     };
   11
   12     const contradictions = await this.api._request(
   13       `/api/graphops/infrastructure/contradictions/v1?since=${query.since}&until=${query.until}`
   14     );
   15
   16     return this.partitionHypothesisWorlds(contradictions);
   17   }
   18
   19   partitionHypothesisWorlds(findings) {
   20     const worldBenign = {
   21       name: "Benign Explanatory Model",
   22       assumptions: ["Equipment failure", "Local Clock drift"],
   23       supportingEvidence: [],
   24       falsifiers: []
   25     };
   26
   27     const worldAdversarial = {
   28       name: "Adversarial Explanatory Model",
   29       assumptions: ["BGP Route Hijack", "Coordinated coverage spoofing"],
   30       supportingEvidence: [],
   31       falsifiers: []
   32     };
   33
   34     for (const finding of findings) {
   35       if (finding.kind === "ORIGIN_DISAGREEMENT") {
   36         worldAdversarial.supportingEvidence.push(finding);
   37         worldAdversarial.falsifiers.push({
   38           targetCoordinates: finding.falsifier_description,
   39           action: "Deploy field monitoring node to capture raw spectrum signature"
   40         });
   41       } else {
   42         worldBenign.supportingEvidence.push(finding);
   43       }
   44     }
   45
   46     return { worldBenign, worldAdversarial };
   47   }
   48 }