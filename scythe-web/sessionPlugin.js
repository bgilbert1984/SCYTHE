/**
 * The operator's session as a workspace tool (the eighteenth tool on the lifecycle contract in lifecycle.js; moved out of command-ops-visualization.html as
 * `OperatorSession`, step 4 of docs/operatorsession_extraction_plan.md).
 *
 * It holds who is signed in (id, callsign, role, team, email, session token, `isAuthenticated`), restores and validates a saved session at startup (`init`), logs in,
 * registers and logs out over REST, sends the server a heartbeat every 30 seconds with where the camera is looking, and lists the active operators. It tells
 * subscribers when a login, a restored session or a logout happens (`on('login' | 'restored' | 'logout', handler)`); what the page does on them (the console
 * message, the status UI, GraphOps, the streams, the rooms) is the boot module's.
 *
 * The page reads the state as `window.OperatorSession.sessionToken`, `.isAuthenticated`, `.callsign` and the rest (about fifty places, and the other tools through
 * the `operatorSession` service); the controls keep those names as read-only getters. The page is touched only through services: `httpFetch`, `sessionStore` (the
 * saved session: `SCYTHE_AUTH`, `localStorage` and the cookie) and `cameraView` (the heartbeat's view).
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - An error in an event handler reaches the caller, as an error in the old callbacks did: a failing login effect makes `login` report failure although the session is set, a failing restore
 *    effect makes the restore fail and signs the session out, and a failing logout effect makes `logout` reject after the session was cleared. The handlers after it do not run.
 *  - A session is saved in three places: `localStorage.operatorSession`, the cookie `operatorSessionToken` (seven days) and `localStorage.scythe_session_token`; each write may fail without
 *    stopping the others.
 *  - Clearing the session removes `localStorage.operatorSession` and the cookie, and leaves `scythe_session_token` and whatever `SCYTHE_AUTH` keeps (plan, finding 2; step 5 decides).
 *  - `validateSession` with an operator that has no id gives the session no id; `operatorName` is the operator's email.
 *  - `logout` with no session does nothing; with one, a failing request is only warned about and the session is cleared and the logout handlers run all the same.
 *  - The heartbeat does nothing without a session, sends no view without a viewer, and a failing request is only warned about.
 *
 * Deliberate changes:
 *  - Deactivating stops the heartbeat and forgets the session, without telling the subscribers, and an answer from the server that arrives afterwards changes nothing.
 *  - `init()` asked for before the tool is active is made when it activates.
 *  - Gone: the public cookie helpers `setCookie` and `clearCookie` (nothing outside the object called them; they live in `sessionStore`).
 *
 * Services it needs (see `requires`):
 *   httpFetch     (url, init?) => Promise<Response>
 *   sessionStore  {restoreToken() => string|null, validate(token) => Promise<{valid, operator?}>, setItem(key, value), removeItem(key), setCookie(name, value, days), clearCookie(name)}
 *   cameraView    {position() => {center: [lat, lon], altitude, heading} | null}: see `workspaceBoot.js`
 */

export const SESSION_ID = "session";
export const HEARTBEAT_INTERVAL = 30000;

const LEGACY_SESSION_KEY = "operatorSession";
const LEGACY_TOKEN_KEY = "scythe_session_token";
const LEGACY_COOKIE = "operatorSessionToken";
const COOKIE_DAYS = 7;
const JSON_HEADERS = {"Content-Type": "application/json"};

export function createSessionPlugin({log = console} = {}) {
  const handlers = {};   // event -> handlers, in order; they outlive an activation
  let run = null;        // null while the tool is inactive
  let earlyInit = false; // init was asked for while the tool was inactive

  /** Runs the handlers for an event in order, in the caller's place: an error in one reaches the caller and stops the rest. */
  const emit = (event) => { (handlers[event] || []).forEach((handler) => handler()); };

  const tool = {
    id: SESSION_ID,
    title: "Operator session",
    requires: ["httpFetch", "sessionStore", "cameraView"],
    activate(ctx) {
      const http = ctx.services.get("httpFetch");
      const store = ctx.services.get("sessionStore");
      const camera = ctx.services.get("cameraView");
      const stale = () => ctx.signal.aborted;   // deactivated while the server was answering

      let operatorId = null, operatorName = null, callsign = null, role = null, teamId = null, sessionToken = null, sessionId = null;
      let isAuthenticated = false;
      let stopBeat = null;   // stops the heartbeat while it runs

      function stopHeartbeat() {
        if (stopBeat) {
          stopBeat();
          stopBeat = null;
        }
      }

      function startHeartbeat() {
        stopHeartbeat();
        stopBeat = ctx.interval(async () => {
          if (!sessionToken) return;
          const view = camera.position();
          try {
            await http("/api/operator/heartbeat", {
              method: "POST", headers: {...JSON_HEADERS, "X-Session-Token": sessionToken},
              body: JSON.stringify({current_view: view}),
            });
          } catch (error) {
            log.warn?.("[OperatorSession] Heartbeat failed:", error);
          }
        }, HEARTBEAT_INTERVAL);
      }

      function clearSession() {
        stopHeartbeat();
        operatorId = operatorName = callsign = role = teamId = sessionToken = sessionId = null;
        isAuthenticated = false;
        store.removeItem(LEGACY_SESSION_KEY);
        store.clearCookie(LEGACY_COOKIE);   // so the token does not linger
      }

      function setSessionData(session, operator) {
        sessionId = session.session_id;
        sessionToken = session.session_token;
        operatorId = operator.operator_id;
        operatorName = operator.email;
        callsign = operator.callsign;
        role = operator.role;
        teamId = operator.team_id;
        isAuthenticated = true;
        try {   // for legacy consumers
          store.setItem(LEGACY_SESSION_KEY, JSON.stringify({sessionToken}));
        } catch (error) {
          log.warn?.("[OperatorSession] Could not persist operatorSession to localStorage:", error);
        }
        try {   // to support cross-tab boots
          store.setCookie(LEGACY_COOKIE, sessionToken, COOKIE_DAYS);
        } catch (error) {
          log.warn?.("[OperatorSession] Could not set session cookie:", error);
        }
        try {   // for pages that read this key directly
          store.setItem(LEGACY_TOKEN_KEY, sessionToken);
        } catch (error) { /* noop */ }
        log.log?.(`[OperatorSession] Logged in as ${callsign} (${role})`);
      }

      async function validateSession() {
        if (!sessionToken) return false;
        try {
          const {valid, operator} = await store.validate(sessionToken);
          if (stale()) return false;
          if (valid) {
            setSessionData({session_id: operator.operator_id || null, session_token: sessionToken}, operator);
            startHeartbeat();
            emit("restored");
            return true;
          }
          clearSession();
          return false;
        } catch (error) {
          if (stale()) return false;
          log.warn?.("[OperatorSession] Session validation failed:", error);
          clearSession();
          return false;
        }
      }

      function init() {
        const token = store.restoreToken();
        if (token) {
          sessionToken = token;
          validateSession();
        }
      }

      async function login(name, password) {
        try {
          const data = await (await http("/api/operator/login", {method: "POST", headers: JSON_HEADERS, body: JSON.stringify({callsign: name, password})})).json();
          if (stale()) return {success: false, message: "Deactivated"};
          if (data.status === "ok") {
            setSessionData(data.session, data.operator);
            startHeartbeat();
            emit("login");
            return {success: true, operator: data.operator};
          }
          return {success: false, message: data.message};
        } catch (error) {
          if (!stale()) log.error?.("[OperatorSession] Login error:", error);
          return {success: false, message: error.message};
        }
      }

      async function register(name, email, password, requestedRole = "operator") {
        try {
          const data = await (await http("/api/operator/register", {method: "POST", headers: JSON_HEADERS, body: JSON.stringify({callsign: name, email, password, role: requestedRole})})).json();
          if (stale()) return {success: false, message: "Deactivated"};
          if (data.status === "ok") return login(name, password);   // auto-login after registration
          return {success: false, message: data.message};
        } catch (error) {
          if (!stale()) log.error?.("[OperatorSession] Registration error:", error);
          return {success: false, message: error.message};
        }
      }

      async function logout() {
        if (!sessionToken) return;
        try {
          await http("/api/operator/logout", {method: "POST", headers: {"X-Session-Token": sessionToken}});
        } catch (error) {
          if (!stale()) log.warn?.("[OperatorSession] Logout error:", error);
        }
        if (stale()) return;
        clearSession();
        emit("logout");
      }

      async function getActiveOperators() {
        try {
          const data = await (await http("/api/operator/active")).json();
          return data.operators || [];
        } catch (error) {
          if (!stale()) log.error?.("[OperatorSession] Error getting active operators:", error);
          return [];
        }
      }

      ctx.onCleanup(() => { run = null; });
      run = {
        init, validateSession, login, register, logout, clearSession, getActiveOperators,
        state: () => ({operatorId, operatorName, callsign, role, teamId, sessionToken, sessionId, isAuthenticated}),
      };
      if (earlyInit) { earlyInit = false; init(); }
    },
  };

  const NOT_STARTED = () => ({success: false, message: "Session not started"});
  const field = (name, inactive) => ({get() { return run ? run.state()[name] : inactive; }, enumerable: true});

  /** What the page calls as `window.OperatorSession`. The calls do nothing while the tool is inactive. */
  const controls = Object.defineProperties({
    init() { if (run) run.init(); else earlyInit = true; },
    validateSession: () => (run ? run.validateSession() : Promise.resolve(false)),
    login: (...args) => (run ? run.login(...args) : Promise.resolve(NOT_STARTED())),
    register: (...args) => (run ? run.register(...args) : Promise.resolve(NOT_STARTED())),
    logout: () => (run ? run.logout() : Promise.resolve()),
    clearSession: () => run?.clearSession(),
    getActiveOperators: () => (run ? run.getActiveOperators() : Promise.resolve([])),
    on(event, handler) { (handlers[event] ||= []).push(handler); return controls; },
    off(event, handler) { if (handlers[event]) handlers[event] = handlers[event].filter((h) => h !== handler); return controls; },
  }, {
    operatorId: field("operatorId", null), operatorName: field("operatorName", null), callsign: field("callsign", null), role: field("role", null),
    teamId: field("teamId", null), sessionToken: field("sessionToken", null), sessionId: field("sessionId", null), isAuthenticated: field("isAuthenticated", false),
  });

  return {tool, controls};
}
