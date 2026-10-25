/**
 * Rooms as a workspace tool (the sixteenth tool on the lifecycle contract in lifecycle.js; moved out of command-ops-visualization.html as `RoomManager`).
 *
 * It lists the rooms the server has (`/api/rooms`) and the ones this operator has joined (`/api/rooms/my`), creates, joins and leaves rooms, keeps
 * the current room, tells whoever subscribed (`on('room_joined', ...)`, `on('current_room_changed', ...)`: the page's init, and the chat tool, which
 * follows the current room), and draws the Operators panel's three room widgets: the available rooms, the joined rooms, and the current room's name.
 *
 * What is shown is plain strings and data here (`renderAvailableRooms`, `renderJoinedRooms`, `currentRoomLabel`, `roomTypeIcon`, `indexRooms`); the page is
 * touched only through services: `httpFetch`, `operatorSession` (the token), `roomView` (the three widgets) and `roomConsole` (the page's console messages).
 * The page calls the controls as `window.RoomManager`, including from inline `onclick`s in the lists and the panel's refresh button.
 *
 * Handlers are registered with `on` / `off`, as before, and outlive an activation: the modules that subscribe do not know about the lifecycle. The rooms
 * and the current room belong to an activation.
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - Joining a room refreshes the joined list but not the available list, so a room's Join button stays a Join button until the next fetch.
 *  - Leaving the current room does not change the current room.
 *  - Making a room current updates the current room's name but does not redraw the joined list, so its active mark lags until the next redraw.
 *  - The session-less calls (`createRoom`, `joinRoom`, `leaveRoom`) answer `{success: false, message: 'Not authenticated'}`; `fetchMyRooms` answers `[]`.
 *  - `setRooms` (the WebSocket's `rooms_list`) and `reset` (logout) update the lists without telling the handlers.
 *  - A response is not checked for `ok`: a reply that is not JSON is the error.
 *
 * Deliberate changes:
 *  - Room names and ids are escaped where they reach HTML (the two lists, with ids given to the inline handlers as JSON strings) and the console. Operators
 *    choose room names; the old code put them in as they were.
 *  - A room is "joined" only if it is held under its own id: a room called `constructor` is not joined because every object has one.
 *  - Deactivating makes an answer from the server that arrives afterwards change nothing.
 *  - The page's two direct writes (`RoomManager.rooms = ...` from the WebSocket handler, the three on logout) are the `setRooms` and `reset` controls.
 *  - Gone, after a search of the repository found no caller: `getRoomDetails`, `publishToRoom`, `sendMessage`.
 *
 * Services it needs (see `requires`):
 *   httpFetch        (url, init?) => Promise<Response>
 *   operatorSession  {token() => string|null, clear()}
 *   roomView         {setAvailable(html()), setJoined(html()), setCurrent(text)}: each does nothing if the page has no such element; `html` is called only if it has
 *   roomConsole      {message(text, kind)}: text, not HTML
 */

import {escapeHtml} from "./htmlEscape.js";
import {createHandlerRegistry} from "./entitySyncPlugin.js";

export const ROOMS_ID = "rooms";

const TYPE_ICONS = {global: "🌐", mission: "🎯", team: "👥", geographic: "📍"};

/** The icon a room type is listed with; a speech bubble for any other. */
export const roomTypeIcon = (type) => (Object.hasOwn(TYPE_ICONS, type) ? TYPE_ICONS[type] : "💬");

/** The rooms as a map by id, in order (a later room with the same id replaces an earlier one). */
export const indexRooms = (list) => Object.fromEntries(list.map((room) => [room.room_id, room]));

const held = (map, id) => (Object.hasOwn(map, id) ? map[id] : undefined);
const idArgument = (id) => escapeHtml(JSON.stringify(String(id)));

export const NO_ROOMS = '<div class="empty-message">No rooms available</div>';
export const NOT_IN_ANY_ROOM = '<div class="empty-message">Not in any rooms</div>';
export const NO_ROOM_SELECTED = "No room selected";

/** The "available rooms" list: one row per room, with a Leave button if joined and a Join button if not. */
export function renderAvailableRooms(rooms, joined) {
  const list = Object.values(rooms);
  if (list.length === 0) return NO_ROOMS;
  let html = "";
  list.forEach((room) => {
    const isJoined = held(joined, room.room_id);
    html += `
                        <div class="room-item ${isJoined ? "joined" : ""}" data-room-id="${escapeHtml(room.room_id)}">
                            <div class="room-icon">${roomTypeIcon(room.room_type)}</div>
                            <div class="room-info">
                                <div class="room-name">${escapeHtml(room.room_name)}${room.is_private ? " 🔒" : ""}</div>
                                <div class="room-meta">${escapeHtml(room.member_count || 0)}/${escapeHtml(room.capacity)} members</div>
                            </div>
                            <div class="room-actions">
                                ${isJoined
                                    ? `<button class="small-btn danger" onclick="RoomManager.leaveRoom(${idArgument(room.room_id)})">Leave</button>`
                                    : `<button class="small-btn primary" onclick="RoomManager.joinRoom(${idArgument(room.room_id)})">Join</button>`
                                }
                            </div>
                        </div>
                    `;
  });
  return html;
}

/** The "joined rooms" list: one row per room, the current one marked active, a click making a room current. */
export function renderJoinedRooms(joined, currentRoomId) {
  const list = Object.values(joined);
  if (list.length === 0) return NOT_IN_ANY_ROOM;
  let html = "";
  list.forEach((room) => {
    const isActive = room.room_id === currentRoomId;
    html += `
                        <div class="room-item joined ${isActive ? "active" : ""}" 
                             data-room-id="${escapeHtml(room.room_id)}"
                             onclick="RoomManager.setCurrentRoom(${idArgument(room.room_id)})">
                            <div class="room-icon">${roomTypeIcon(room.room_type)}</div>
                            <div class="room-info">
                                <div class="room-name">${escapeHtml(room.room_name)}</div>
                                <div class="room-meta">${escapeHtml(room.entity_count || 0)} entities</div>
                            </div>
                        </div>
                    `;
  });
  return html;
}

/** What the "current room" line says: the room's name, or that none is selected. */
export const currentRoomLabel = (joined, currentRoomId) => {
  const room = held(joined, currentRoomId);
  return room ? room.room_name : NO_ROOM_SELECTED;
};

const NOT_AUTHENTICATED = () => ({success: false, message: "Not authenticated"});

export function createRoomsPlugin({log = console} = {}) {
  const registry = createHandlerRegistry({log, label: "RoomManager"});
  let run = null;   // null while the tool is inactive

  const tool = {
    id: ROOMS_ID,
    title: "Rooms",
    requires: ["httpFetch", "operatorSession", "roomView", "roomConsole"],
    activate(ctx) {
      const http = ctx.services.get("httpFetch");
      const session = ctx.services.get("operatorSession");
      const view = ctx.services.get("roomView");
      const out = ctx.services.get("roomConsole");
      const stale = () => ctx.signal.aborted;   // deactivated while the server was answering
      const json = async (url, init) => (await http(url, init)).json();
      const authorized = (extra = {}) => ({...extra, "X-Session-Token": session.token()});

      let rooms = {};          // available rooms by id
      let joinedRooms = {};    // rooms we have joined, by id
      let currentRoomId = null;

      const updateRoomsList = () => view.setAvailable(() => renderAvailableRooms(rooms, joinedRooms));
      const updateJoinedRoomsList = () => view.setJoined(() => renderJoinedRooms(joinedRooms, currentRoomId));
      const updateRoomUI = () => view.setCurrent(currentRoomLabel(joinedRooms, currentRoomId));

      async function fetchRooms() {
        try {
          const data = await json("/api/rooms");
          if (stale()) return [];
          if (data.status === "ok") {
            rooms = indexRooms(data.rooms);
            registry.notify("rooms_updated", rooms);
            updateRoomsList();
            return data.rooms;
          }
        } catch (error) {
          if (!stale()) log.error?.("[RoomManager] Error fetching rooms:", error);
        }
        return [];
      }

      async function fetchMyRooms() {
        if (!session.token()) return [];
        try {
          const data = await json("/api/rooms/my", {headers: authorized()});
          if (stale()) return [];
          if (data.status === "ok") {
            joinedRooms = indexRooms(data.rooms);
            registry.notify("my_rooms_updated", joinedRooms);
            updateJoinedRoomsList();
            return data.rooms;
          }
        } catch (error) {
          if (!stale()) log.error?.("[RoomManager] Error fetching my rooms:", error);
        }
        return [];
      }

      async function createRoom(roomName, options = {}) {
        if (!session.token()) return NOT_AUTHENTICATED();
        try {
          const data = await json("/api/rooms/create", {
            method: "POST", headers: authorized({"Content-Type": "application/json"}),
            body: JSON.stringify({
              room_name: roomName, room_type: options.type || "custom", capacity: options.capacity || 50,
              is_private: options.isPrivate || false, password: options.password, metadata: options.metadata,
            }),
          });
          if (stale()) return {success: false, message: "Deactivated"};
          if (data.status === "ok") {
            out.message(`🏠 Created room: ${roomName}`, "response");
            await fetchRooms();
            await fetchMyRooms();
            return {success: true, room: data.room};
          }
          return {success: false, message: data.message};
        } catch (error) {
          log.error?.("[RoomManager] Error creating room:", error);
          return {success: false, message: error.message};
        }
      }

      async function joinRoom(roomId, password = null) {
        if (!session.token()) return NOT_AUTHENTICATED();
        try {
          const data = await json(`/api/rooms/${roomId}/join`, {
            method: "POST", headers: authorized({"Content-Type": "application/json"}), body: JSON.stringify({password}),
          });
          if (stale()) return {success: false, message: "Deactivated"};
          if (data.status === "ok") {
            const room = data.room || held(rooms, roomId);
            if (room) joinedRooms[roomId] = room;
            out.message(`📥 Joined room: ${room?.room_name || roomId}`, "response");
            registry.notify("room_joined", {roomId, room});
            updateJoinedRoomsList();
            return {success: true, room: data.room};
          }
          return {success: false, message: data.message};
        } catch (error) {
          log.error?.("[RoomManager] Error joining room:", error);
          return {success: false, message: error.message};
        }
      }

      async function leaveRoom(roomId) {
        if (!session.token()) return NOT_AUTHENTICATED();
        try {
          const data = await json(`/api/rooms/${roomId}/leave`, {method: "POST", headers: authorized()});
          if (stale()) return {success: false, message: "Deactivated"};
          if (data.status === "ok") {
            const room = held(joinedRooms, roomId);
            delete joinedRooms[roomId];
            out.message(`📤 Left room: ${room?.room_name || roomId}`, "response");
            registry.notify("room_left", {roomId});
            updateJoinedRoomsList();
            return {success: true};
          }
          return {success: false, message: data.message};
        } catch (error) {
          log.error?.("[RoomManager] Error leaving room:", error);
          return {success: false, message: error.message};
        }
      }

      function setCurrentRoom(roomId) {
        currentRoomId = roomId;
        registry.notify("current_room_changed", {roomId});
        updateRoomUI();
      }

      /** The WebSocket's list of rooms: replaces the available rooms and redraws them, without telling the handlers. */
      function setRooms(list) {
        rooms = indexRooms(list);
        updateRoomsList();
      }

      /** Logout: forgets the joined rooms and the current room and redraws, without telling the handlers. */
      function reset() {
        joinedRooms = {};
        currentRoomId = null;
        updateJoinedRoomsList();
        updateRoomUI();
      }

      ctx.onCleanup(() => { run = null; });
      run = {
        fetchRooms, fetchMyRooms, createRoom, joinRoom, leaveRoom, setCurrentRoom, setRooms, reset,
        state: () => ({rooms, joinedRooms, currentRoomId}),
      };
    },
  };

  /** What the page calls as `window.RoomManager`. The calls do nothing while the tool is inactive. */
  const controls = {
    get rooms() { return run ? run.state().rooms : {}; },
    get joinedRooms() { return run ? run.state().joinedRooms : {}; },
    get currentRoomId() { return run ? run.state().currentRoomId : null; },
    fetchRooms: () => (run ? run.fetchRooms() : Promise.resolve([])),
    fetchMyRooms: () => (run ? run.fetchMyRooms() : Promise.resolve([])),
    createRoom: (...args) => (run ? run.createRoom(...args) : Promise.resolve(NOT_AUTHENTICATED())),
    joinRoom: (...args) => (run ? run.joinRoom(...args) : Promise.resolve(NOT_AUTHENTICATED())),
    leaveRoom: (roomId) => (run ? run.leaveRoom(roomId) : Promise.resolve(NOT_AUTHENTICATED())),
    setCurrentRoom: (roomId) => run?.setCurrentRoom(roomId),
    setRooms: (list) => run?.setRooms(list),
    reset: () => run?.reset(),
    on(event, handler) { registry.on(event, handler); return controls; },
    off(event, handler) { registry.off(event, handler); return controls; },
  };

  return {tool, controls};
}
