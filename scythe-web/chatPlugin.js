/**
 * Room chat as a workspace tool (the third pilot for the lifecycle contract in lifecycle.js).
 *
 * This is LiveChat's connection half moved behind activate/deactivate. While active it keeps one connection to the current
 * room: an EventSource, with polling as the fallback (when the stream errors, or says nothing for `fallbackMs`). Switching
 * room closes that connection and opens another. Deactivating closes it and removes the view's listeners and the room hook.
 *
 * A connection is one object, `conn`, and closing it is the only way anything stops: the stream, the poll interval and the
 * fallback timer all belong to it, and a late result (the history fetch, a poll, a stream message) for a closed connection
 * is dropped. The old code kept the poll timer on `connect._pollTimer`, never cleared the 3 s fallback timeout, and let a
 * history fetch that landed after a room switch write the old room's messages into the new room's feed.
 *
 * Rendering stays with the page (`LiveChat.addMsg` escapes everything it prints); this tool only decides what to show when.
 *
 * Timers, listeners and late results are handled by the activation's scope helpers (`ctx.interval`, `ctx.listen`, `ctx.guard`,
 * and a child `ctx.scope()` per connection) rather than by hand.
 *
 * Services it needs (see `requires`):
 *   openEventSource (url) => EventSource-like {onmessage, onerror, close()}
 *   fetchJson       (url) => Promise<json>
 *   postJson        (url, body) => Promise<json>
 *   chatView        {showConnecting(room), hideConnecting(), addMessage(msg), callsign(), messageText(), clearMessage(),
 *                    saveCallsign(text), restoreCallsign(), bind({send, callsignChanged}) => unbind}
 *   roomChanges     {subscribe(fn(roomId)) => unsubscribe}
 */

export const CHAT_ID = "chat";
export const DEFAULT_ROOM = "GLOBAL";

const messagesUrl = (room, since = "") => `/api/chat/${encodeURIComponent(room)}/messages${since ? `?since=${encodeURIComponent(since)}` : ""}`;
const streamUrl = (room) => `/api/chat/${encodeURIComponent(room)}/stream`;
const sendUrl = (room) => `/api/chat/${encodeURIComponent(room)}/send`;

export function createChatPlugin({log = console, pollMs = 2000, fallbackMs = 3000} = {}) {
  let run = null;   // null while the tool is inactive

  const tool = {
    id: CHAT_ID,
    title: "Chat",
    requires: ["openEventSource", "fetchJson", "postJson", "chatView", "roomChanges"],
    activate(ctx) {
      const openEventSource = ctx.services.get("openEventSource");
      const fetchJson = ctx.services.get("fetchJson");
      const postJson = ctx.services.get("postJson");
      const view = ctx.services.get("chatView");
      const roomChanges = ctx.services.get("roomChanges");
      const state = {room: DEFAULT_ROOM, conn: null};

      const closeConnection = () => { const conn = state.conn; state.conn = null; if (conn) conn.scope.close(); };

      const show = (conn, messages) => {
        if (!Array.isArray(messages) || !messages.length) return;
        for (const message of messages) view.addMessage(message);
        conn.lastTs = messages[messages.length - 1].timestamp || "";
      };

      function startPolling(conn) {
        if (conn.stopPoll) return;
        conn.stopPoll = conn.scope.interval(async () => {
          try {
            const body = await fetchJson(messagesUrl(conn.room, conn.lastTs));
            if (!conn.scope.signal.aborted) show(conn, body && body.messages);
          } catch (error) { /* transient: the next tick tries again */ }
        }, pollMs);
      }

      function connect(room) {
        closeConnection();
        const conn = {room: room || DEFAULT_ROOM, scope: ctx.scope(), stopPoll: null, connected: false, lastTs: ""};
        const {scope} = conn;
        state.room = conn.room;
        state.conn = conn;
        view.showConnecting(conn.room);

        fetchJson(messagesUrl(conn.room))
          .then(scope.guard((body) => { view.hideConnecting(); show(conn, body && body.messages); }))
          .catch((error) => log.debug?.("[Chat] history failed:", error));

        try {
          const es = openEventSource(streamUrl(conn.room));
          scope.onCleanup(() => es.close());
          es.onmessage = scope.guard((event) => {
            let data;
            try { data = JSON.parse(event.data); } catch (error) { return; }
            if (data.type === "connected") { conn.connected = true; conn.stopPoll?.(); conn.stopPoll = null; }
            if (data.type === "chat") view.addMessage(data);
          });
          es.onerror = scope.guard(() => startPolling(conn));
          scope.timeout(() => { if (!conn.connected) startPolling(conn); }, fallbackMs);
        } catch (error) {
          log.warn?.("[Chat] event stream unavailable, polling:", error);
          startPolling(conn);
        }
      }

      async function send() {
        const message = view.messageText();
        if (!message) return;
        const callsign = view.callsign();
        if (callsign) view.saveCallsign(callsign);
        try {
          await postJson(sendUrl(state.room), {message, callsign});
          if (!ctx.signal.aborted) view.clearMessage();
        } catch (error) {
          log.warn?.("[Chat] send failed:", error);
        }
      }

      ctx.onCleanup(closeConnection);
      view.restoreCallsign();
      ctx.onCleanup(view.bind({send, callsignChanged: () => view.saveCallsign(view.callsign())}));
      ctx.onCleanup(roomChanges.subscribe(ctx.guard(connect)));
      ctx.onCleanup(() => { run = null; });

      run = {send, switchRoom: connect, state};
      connect(DEFAULT_ROOM);
    },
  };

  /** What the page's LiveChat calls. Each does nothing while the tool is inactive. */
  const controls = {
    send: () => run?.send(),
    switchRoom: (room) => run?.switchRoom(room),
    room: () => (run ? run.state.room : null),
  };

  return {tool, controls};
}
