/**
 * Live AIS vessel streaming as a workspace tool (the second pilot for the lifecycle contract in lifecycle.js).
 *
 * This is AISStreamManager's streaming half moved behind activate/deactivate. While active it forwards `ais_update`
 * messages to the vessel renderer, asks the server to stream for the visible bounding box, and follows the camera.
 * Deactivating stops listening, stops following the camera, and stops the server's upstream stream if this tool started it
 * (the old manager had no teardown at all: its socket, WebSocketSync and camera listeners were never removed).
 *
 * The tool owns the stream, not the vessels: vessels already handed to the renderer stay where they are, because the renderer
 * also holds vessels it loaded from the REST archive and a stream-only record is not distinguishable from those.
 *
 * Behaviour kept as it was, including two things that look unintended (see the tests that pin them):
 *  - Zooming out does not stop the stream. The old guard tested `this.streamSocket`, which was declared null and never
 *    assigned, so that branch never ran. Stopping on zoom-out would also need a way to resume on zoom-in; that is a design
 *    decision, not a refactor.
 *  - The bounding box is updated by posting to the *start* endpoint again, which is how the server takes a new box.
 *
 * Services it needs (see `requires`):
 *   realtimeSocket  () => socket | null            the socket.io client, if connected yet
 *   syncEvents      {on(event, fn), off(event, fn), connected}   WebSocketSync: fires 'connected'
 *   cameraView      {altitude(), boundingBox(), onChange(fn) => remove}
 *   aisApi          {start(bbox) => {status}, stop()}
 *   vesselSink      {upsert(vessel)}                the renderer adapter
 *   notify          (text) => void                  the operator console line
 */

export const AIS_STREAM_ID = "ais-stream";
export const WORLD_BBOX = Object.freeze([[[-180, -90], [180, 90]]]);

export function createAisStreamPlugin({log = console, now = () => Date.now(), zoomThreshold = 100000, updateThrottle = 1000} = {}) {
  // Shared by the tool and the controls the page's AIS toggle calls. `run` is null while the tool is inactive.
  let run = null;

  const tool = {
    id: AIS_STREAM_ID,
    title: "AIS stream",
    requires: ["realtimeSocket", "syncEvents", "cameraView", "aisApi", "vesselSink", "notify"],
    activate(ctx) {
      const socket = ctx.services.get("realtimeSocket")();
      const sync = ctx.services.get("syncEvents");
      const camera = ctx.services.get("cameraView");
      const api = ctx.services.get("aisApi");
      const sink = ctx.services.get("vesselSink");
      const notify = ctx.services.get("notify");
      const state = {active: false, starting: false, bbox: null, lastUpdate: 0};

      const nearEnough = () => {
        const altitude = camera.altitude();
        return (Number.isFinite(altitude) ? altitude : 1000000) < zoomThreshold;
      };

      async function startStreaming() {
        if (state.active || state.starting) return;
        state.starting = true;
        try {
          const bbox = camera.boundingBox() || WORLD_BBOX;
          const data = await api.start(bbox);
          if (ctx.signal.aborted) {              // deactivated while the request was in flight: undo what it just started
            // The one place a tool must check the signal itself: the helpers can drop a late callback, but only the tool knows
            // that this late *answer* means something was started on the server and has to be undone.
            await api.stop().catch((error) => log.warn?.("[AISStream] could not stop a stream started during deactivation:", error));
            return;
          }
          if (data && data.status === "started") {
            state.active = true;
            state.bbox = bbox;
            notify("🚢 AIS real-time streaming started");
          }
        } catch (error) {
          log.error?.("[AISStream] Failed to start:", error);
        } finally {
          state.starting = false;
        }
      }

      // Throws on failure so deactivation can report it; the page-facing stop() below logs instead.
      async function stopServerStream() {
        if (!state.active) return;
        await api.stop();
        state.active = false;
        state.bbox = null;
        notify("🚢 AIS streaming stopped");
      }

      async function updateBoundingBox() {
        const bbox = camera.boundingBox();
        if (!bbox || JSON.stringify(bbox) === JSON.stringify(state.bbox)) return;
        state.bbox = bbox;
        state.lastUpdate = now();
        try { await api.start(bbox); } catch (error) { log.error?.("[AISStream] Failed to update bbox:", error); }
      }

      function onCameraChange() {
        if (!state.active) return;
        if (now() - state.lastUpdate < updateThrottle) return;
        if (nearEnough()) updateBoundingBox();
        // Zoomed out: nothing. See the header note.
      }

      // Late events after deactivation are dropped by the guards where one is needed (a vessel, a camera move); `run` is nulled
      // first, which already stops the rest. Resources are released by the helpers, last registered first: the camera listener,
      // then the socket and sync listeners, then the server's stream.
      const onVessel = ctx.guard((vessel) => sink.upsert(vessel));
      const onConnected = () => { log.log?.("[AISStream] WebSocket connected"); run?.checkAndStart(); };   // `run` is already null once deactivation starts

      ctx.onCleanup(() => stopServerStream());
      ctx.listen(sync, "connected", onConnected);
      if (socket) ctx.listen(socket, "ais_update", onVessel);
      ctx.onCleanup(camera.onChange(ctx.guard(onCameraChange)));
      ctx.onCleanup(() => { run = null; });

      run = {
        checkAndStart: () => { if (nearEnough()) return startStreaming(); },
        stop: () => stopServerStream().catch((error) => log.error?.("[AISStream] Failed to stop:", error)),
        status: () => state,
      };
      // Already connected when activated (activation after startup): the 'connected' event has been missed.
      if (sync.connected) run.checkAndStart();
    },
  };

  /** What the page's AIS toggle calls. Each does nothing while the tool is inactive. */
  const controls = {
    checkAndStart: () => run?.checkAndStart(),
    stop: () => run?.stop(),
    status: () => (run ? {...run.status()} : null),
  };

  return {tool, controls};
}
