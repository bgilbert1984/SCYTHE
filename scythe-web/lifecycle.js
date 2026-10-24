/**
 * The lifecycle contract for a workspace tool. A tool is registered once and then activated, deactivated and disposed;
 * it does not initialize itself when the page loads.
 *
 *   register({id, title, requires, activate(ctx), deactivate(), dispose()})
 *
 * - `activate(ctx)` wakes the tool. `ctx.services.get(name)` returns a service the workspace provides (a tool lists the
 *   ones it needs in `requires`, and activation is refused while one is missing). `ctx.onCleanup(fn)` registers undo work
 *   at the moment a resource is taken, so teardown does not depend on the tool remembering what it opened.
 * - `ctx` is also a *scope* (see makeScope): `ctx.signal` is aborted when the tool deactivates; `ctx.guard(fn)`, `ctx.listen(...)`,
 *   `ctx.interval(...)`, `ctx.timeout(...)` do the subscribe/guard/clear bookkeeping so a tool cannot forget it; `ctx.scope()` makes a
 *   child scope for something that is opened and closed on its own (a connection, a request).
 * - Teardown order: the signal is aborted first (guarded callbacks stop), then the optional `deactivate()` hook runs, then the
 *   registered cleanups, last registered first.
 * - `dispose()` is optional and runs once, after deactivation, when the tool is retired for good.
 *
 * States: inactive -> activating -> active -> deactivating -> inactive, and disposed (terminal). Activating an active tool
 * is a no-op, concurrent activations share one attempt, and a failed activation is rolled back to inactive.
 * Teardown never stops half way: every step runs, and the errors are returned (and logged) instead of thrown.
 */

const ID = /^[a-z][a-z0-9._-]*$/;

export const STATES = Object.freeze({
  INACTIVE: "inactive", ACTIVATING: "activating", ACTIVE: "active", DEACTIVATING: "deactivating", DISPOSED: "disposed",
});

function checkDefinition(def) {
  if (!def || typeof def !== "object") throw new TypeError("a tool definition is required");
  if (typeof def.id !== "string" || !ID.test(def.id)) throw new TypeError(`tool id must match ${ID}: ${String(def.id)}`);
  if (typeof def.activate !== "function") throw new TypeError(`tool ${def.id}: activate must be a function`);
  for (const hook of ["deactivate", "dispose"]) {
    if (def[hook] !== undefined && typeof def[hook] !== "function") throw new TypeError(`tool ${def.id}: ${hook} must be a function`);
  }
  const requires = def.requires === undefined ? [] : def.requires;
  if (!Array.isArray(requires) || requires.some((name) => typeof name !== "string" || !name)) {
    throw new TypeError(`tool ${def.id}: requires must be a list of service names`);
  }
  return Object.freeze({
    id: def.id, title: typeof def.title === "string" ? def.title : def.id, requires: Object.freeze([...requires]),
    activate: def.activate, deactivate: def.deactivate, dispose: def.dispose,
  });
}


/**
 * A scope owns resources and the abort signal that says "stop": the activation is the root scope and `ctx.scope()`
 * makes child scopes (a connection, a request). Closing a scope aborts its signal first, so guarded callbacks stop, then runs
 * its cleanups last-registered first and closes its children.
 */
function makeScope({timers, log}) {
  const controller = new AbortController();
  const cleanups = [];
  const scope = {
    signal: controller.signal,
    onCleanup(fn) {
      if (typeof fn !== "function") throw new TypeError("onCleanup needs a function");
      if (controller.signal.aborted) { Promise.resolve().then(fn); return; }   // taken after close: undo at once
      cleanups.push(fn);
    },
    // Subscribe and register the unsubscribe in one step. Works for on/off emitters and DOM-style targets.
    listen(target, event, fn, options) {
      const dom = typeof target.addEventListener === "function";
      dom ? target.addEventListener(event, fn, options) : target.on(event, fn);
      scope.onCleanup(() => (dom ? target.removeEventListener(event, fn, options) : target.off(event, fn)));
      return fn;
    },
    // A callback that does nothing once this scope is closed: late events, late promise continuations.
    guard: (fn) => (...args) => (controller.signal.aborted ? undefined : fn(...args)),
    interval(fn, ms) {
      const id = timers.setInterval(scope.guard(fn), ms);
      const stop = () => timers.clearInterval(id);
      scope.onCleanup(stop);
      return stop;
    },
    timeout(fn, ms) {
      const id = timers.setTimeout(scope.guard(fn), ms);
      const stop = () => timers.clearTimeout(id);
      scope.onCleanup(stop);
      return stop;
    },
    scope() {
      const child = makeScope({timers, log});
      scope.onCleanup(async () => {                // surface the child's failures through the parent's teardown
        const errors = await child.close();
        if (errors.length) throw errors.length === 1 ? errors[0] : new AggregateError(errors, "child scope cleanup failed");
      });
      return child;
    },
    abort: () => controller.abort(),
    async close() {
      controller.abort();
      const errors = [];
      for (const fn of cleanups.splice(0).reverse()) {
        try { await fn(); } catch (error) { errors.push(error); log.warn?.("[workspace] cleanup failed", error); }
      }
      return errors;
    },
  };
  return scope;
}

export function createWorkspace({log = console, timers = globalThis} = {}) {
  const services = new Map();
  const tools = new Map();   // id -> record, in registration order

  const record = (id) => {
    const rec = tools.get(id);
    if (!rec) throw new Error(`unknown tool: ${id}`);
    return rec;
  };

  // Run every step even if one throws; hand back what failed.
  async function runAll(id, steps) {
    const errors = [];
    for (const step of steps) {
      try { await step(); } catch (error) { errors.push(error); log.warn?.(`[workspace] ${id}: teardown step failed`, error); }
    }
    return errors;
  }

  async function teardown(rec) {
    const scope = rec.scope;
    if (!scope) return [];
    rec.scope = null;
    scope.abort();                                              // stop guarded callbacks before anything is undone
    const errors = rec.def.deactivate ? await runAll(rec.def.id, [() => rec.def.deactivate()]) : [];
    return [...errors, ...await scope.close()];
  }

  async function doActivate(rec) {
    const {def} = rec;
    const missing = def.requires.filter((name) => !services.has(name));
    if (missing.length) throw new Error(`tool ${def.id} needs services that are not provided: ${missing.join(", ")}`);
    rec.state = STATES.ACTIVATING;
    rec.error = null;
    const scope = makeScope({timers, log});
    rec.scope = scope;
    const ctx = Object.freeze({
      ...scope,
      id: def.id,
      services: Object.freeze({get: (name) => {
        if (!def.requires.includes(name)) throw new Error(`tool ${def.id} did not declare service ${name} in requires`);
        return services.get(name);
      }}),
    });
    try {
      await def.activate(ctx);
    } catch (error) {
      await teardown(rec);   // roll back whatever the failed activation had already taken
      rec.state = STATES.INACTIVE;
      rec.error = error;
      throw error;
    }
    rec.state = STATES.ACTIVE;
  }

  function settle(rec, promise) {
    rec.pending = promise;
    const clear = () => { if (rec.pending === promise) rec.pending = null; };
    promise.then(clear, clear);
    return promise;
  }

  async function activate(id) {
    const rec = record(id);
    if (rec.state === STATES.DISPOSED) throw new Error(`tool ${id} is disposed`);
    if (rec.state === STATES.ACTIVE) return;
    if (rec.state === STATES.ACTIVATING) return rec.pending;
    if (rec.state === STATES.DEACTIVATING) await rec.pending;
    if (rec.state === STATES.ACTIVE) return;
    if (rec.state === STATES.ACTIVATING) return rec.pending;   // another caller got there while this one waited
    if (rec.state === STATES.DISPOSED) throw new Error(`tool ${id} is disposed`);
    return settle(rec, doActivate(rec));
  }

  async function deactivate(id) {
    const rec = record(id);
    if (rec.state === STATES.ACTIVATING) await rec.pending.catch(() => {});
    if (rec.state === STATES.DEACTIVATING) return rec.pending;
    if (rec.state !== STATES.ACTIVE) return [];
    rec.state = STATES.DEACTIVATING;
    return settle(rec, (async () => {
      const errors = await teardown(rec);
      rec.state = STATES.INACTIVE;
      return errors;
    })());
  }

  async function dispose(id) {
    const rec = record(id);
    if (rec.state === STATES.DISPOSED) return [];
    const errors = [...await deactivate(id)];
    if (rec.def.dispose) errors.push(...await runAll(id, [() => rec.def.dispose()]));
    rec.state = STATES.DISPOSED;
    return errors;
  }

  return {
    provide(name, value) {
      if (typeof name !== "string" || !name) throw new TypeError("a service needs a name");
      if (services.has(name)) throw new Error(`service already provided: ${name}`);
      services.set(name, value);
    },
    register(definition) {
      const def = checkDefinition(definition);
      if (tools.has(def.id)) throw new Error(`tool already registered: ${def.id}`);
      tools.set(def.id, {def, state: STATES.INACTIVE, scope: null, pending: null, error: null});
    },
    activate,
    deactivate,
    dispose,
    /** Retire every tool, last registered first. Returns the teardown errors by tool id. */
    async disposeAll() {
      const failures = {};
      for (const id of [...tools.keys()].reverse()) {
        const errors = await dispose(id);
        if (errors.length) failures[id] = errors;
      }
      return failures;
    },
    state: (id) => tools.get(id)?.state,
    error: (id) => tools.get(id)?.error ?? null,
    list: () => [...tools.values()].map(({def, state, error}) => ({id: def.id, title: def.title, requires: [...def.requires], state, error})),
  };
}
