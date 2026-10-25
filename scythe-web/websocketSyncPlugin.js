/**
 * The realtime socket as a workspace tool (the seventeenth tool on the lifecycle contract in lifecycle.js; moved out of command-ops-visualization.html as
 * `WebSocketSync`).
 *
 * `init()` asks the server whether it offers WebSockets (`/api/status`, `components.rooms.websocket_available`). `connect()` opens a socket.io connection
 * authenticated with the operator's session token, or, when WebSockets are not on offer, there is no session or socket.io did not load, falls back to
 * EntitySync's server-sent-events stream. Once connected it passes the server's entity events to EntitySync (`ingest`, the one place an event from either transport
 * updates the cache) and to its own handlers, keeps the room lists in step with the server's room events, and tells subscribers when it connects and disconnects.
 * `socket` is the socket.io client, which the ADS-B and AIS tools use directly.
 *
 * Handlers are registered with `on` / `off`, as before (the AIS tool listens for `connected`), and outlive an activation: the modules that subscribe do not know
 * about the lifecycle. The socket belongs to an activation; whether the server offers WebSockets is a fact about the server and survives one.
 *
 * Services it needs (see `requires`):
 *   httpFetch        (url, init?) => Promise<Response>
 *   operatorSession  {token() => string|null, clear()}
 *   socketClient     {available() => whether socket.io loaded, open(url, options) => socket}
 *   realtimeEnv      {origin() => the page's origin, bootstrap() => the page's bootstrap record, if any}
 *   entityFeed       {connect(), ingest(eventType, data)}: EntitySync's controls
 *   roomSync         {refreshMine(), refreshAll(), setRooms(list)}: RoomManager's controls
 *   realtimeConsole  {message(text, kind)}: the page's console messages
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - With no WebSockets on offer, or no session, or no socket.io, `connect()` asks EntitySync to connect (the operator session does the same when `useWebSocket` is
 *    false, so EntitySync may be asked twice).
 *  - A second `connect()` closes the first socket (which tells the subscribers it disconnected) and opens another.
 *  - `disconnect()` closes the socket and, through the socket's own disconnect event, tells the subscribers.
 *  - An entity event without an `event_type` reaches EntitySync and then throws, so its own handlers are not told.
 *  - A rooms list with no `rooms` is ignored; the room events refresh whichever list they concern.
 *
 * Deliberate changes:
 *  - Deactivating closes the socket without telling the subscribers, and a late event from it does nothing.
 *  - `init()` is made once the tool is active if it was asked for earlier; an answer that arrives after deactivation changes nothing.
 *  - The do-nothing `heartbeat_ack` handler is not registered.
 *  - Gone, after a search of the repository found no caller: `send`, `joinRoom`, `leaveRoom`, `createRoom`, `publishEntity`, `sendMessage`, `sendHeartbeat`,
 *    and the write-only `reconnectAttempts`, `maxReconnectAttempts`, `reconnectDelay` (socket.io reconnects by itself).
 */

import {createHandlerRegistry} from "./entitySyncPlugin.js";

export const WEBSOCKET_SYNC_ID = "websocket-sync";

export function createWebSocketSyncPlugin({log = console} = {}) {
  const registry = createHandlerRegistry({log, label: "WebSocketSync"});
  let useWebSocket = false;   // what the server offers: set by init, kept across activations
  let run = null;             // null while the tool is inactive
  let earlyInit = false;      // init was asked for while the tool was inactive

  const tool = {
    id: WEBSOCKET_SYNC_ID,
    title: "Realtime socket",
    requires: ["httpFetch", "operatorSession", "socketClient", "realtimeEnv", "entityFeed", "roomSync", "realtimeConsole"],
    activate(ctx) {
      const http = ctx.services.get("httpFetch");
      const session = ctx.services.get("operatorSession");
      const sockets = ctx.services.get("socketClient");
      const env = ctx.services.get("realtimeEnv");
      const feed = ctx.services.get("entityFeed");
      const rooms = ctx.services.get("roomSync");
      const out = ctx.services.get("realtimeConsole");
      const stale = () => ctx.signal.aborted;   // deactivated while something was in flight

      let socket = null;
      let connected = false;

      async function init() {
        try {
          const response = await http("/api/status");
          const data = await response.json();
          if (stale()) return;
          useWebSocket = data.components?.rooms?.websocket_available || false;
          log.log?.(`[WebSocketSync] WebSocket available: ${useWebSocket}`);
        } catch (error) {
          if (stale()) return;
          log.warn?.("[WebSocketSync] Could not detect WebSocket support:", error);
          useWebSocket = false;
        }
      }

      function handleEntityEvent(data) {
        const eventType = data.event_type;
        // EntitySync updates its cache and notifies its handlers (the one place an event from either transport does that)
        feed.ingest(eventType, data);
        // this tool's own handlers
        registry.notify(eventType.toLowerCase(), data);
        registry.notify("entity", {eventType, ...data});
      }

      function disconnect() {
        if (socket) {
          socket.disconnect();   // the socket's own disconnect event tells the subscribers
          socket = null;
        }
        connected = false;
      }

      function connect() {
        if (!useWebSocket || !session.token()) {
          log.log?.("[WebSocketSync] Using SSE fallback");
          feed.connect();
          return;
        }
        if (!sockets.available()) {
          log.warn?.("[WebSocketSync] socket.io not loaded, falling back to SSE");
          feed.connect();
          return;
        }
        if (socket) disconnect();

        const token = session.token();
        const bootstrap = env.bootstrap();
        const proxied = Boolean(bootstrap?.path_prefix);
        const current = sockets.open(`${env.origin()}?token=${encodeURIComponent(token)}`, {
          path: bootstrap?.socketio_path || "/socket.io",
          transports: proxied ? ["polling"] : ["websocket", "polling"],
          upgrade: !proxied,
          query: {token},
        });
        socket = current;
        const live = (fn) => (...args) => { if (!stale()) fn(...args); };

        current.on("connect", live(() => {
          connected = true;
          log.log?.("[WebSocketSync] Connected");
          out.message("🔌 WebSocket connected", "response");
          registry.notify("connected", {});
        }));
        current.on("disconnect", live(() => {
          connected = false;
          log.log?.("[WebSocketSync] Disconnected");
          registry.notify("disconnected", {});
        }));
        current.on("error", live((error) => {
          log.error?.("[WebSocketSync] Error:", error);
          registry.notify("error", {error});
        }));
        current.on("connected", live((data) => { log.log?.("[WebSocketSync] Authenticated:", data); }));
        current.on("entity_event", live((data) => handleEntityEvent(data)));
        current.on("room_joined", live((data) => { log.log?.("[WebSocketSync] Room joined:", data); rooms.refreshMine(); }));
        current.on("room_left", live((data) => { log.log?.("[WebSocketSync] Room left:", data); rooms.refreshMine(); }));
        current.on("room_created", live((data) => { log.log?.("[WebSocketSync] Room created:", data); rooms.refreshAll(); }));
        current.on("rooms_list", live((data) => { if (data.rooms) rooms.setRooms(data.rooms); }));
      }

      ctx.onCleanup(() => { run = null; });
      ctx.onCleanup(() => { socket?.disconnect(); });   // quietly: its disconnect event finds the tool inactive
      run = {init, connect, disconnect, state: () => ({socket, connected})};
      if (earlyInit) { earlyInit = false; init(); }
    },
  };

  /** What the page calls as `window.WebSocketSync`: its init, the operator session's login, restore and logout, and (through the boot module's services) the ADS-B and AIS tools. */
  const controls = {
    get socket() { return run ? run.state().socket : null; },
    get connected() { return run ? run.state().connected : false; },
    get useWebSocket() { return useWebSocket; },
    init() { if (run) return run.init(); earlyInit = true; return Promise.resolve(); },
    connect: () => run?.connect(),
    disconnect: () => run?.disconnect(),
    on(event, handler) { registry.on(event, handler); return controls; },
    off(event, handler) { registry.off(event, handler); return controls; },
  };

  return {tool, controls};
}
