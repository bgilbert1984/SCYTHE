/**
 * The deep-link contract: how one SCYTHE page hands its context to another through the URL alone.
 *
 *   ?mission=<mission id>   which mission the page shows
 *   ?select=<entity id>     which object to select once it is on the page
 *
 * Nothing else travels in a link. In particular a session token or bootstrap token is never forwarded: each page
 * authenticates for itself. A value that is present but not valid is reported and ignored, never repaired or guessed.
 * Links are relative, so they keep whatever prefix the page is served under (for example /scythe/i/<instance>/).
 */

/** The pages that take part. Paths are relative to the directory the instance serves. */
export const PAGES = Object.freeze({
  ops: Object.freeze({path: "command-ops-visualization.html", label: "Mission Ops"}),
  regional: Object.freeze({path: "scythe-web/regional-rf-demo.html", label: "Regional"}),
  globe: Object.freeze({path: "cesium-hypergraph-globe.html", label: "Globe"}),
});

// The same rule the server applies (scythe_mission.is_valid_mission_id).
const MISSION_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const SELECT_MAX = 256;
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/;

export const isMissionId = (value) => typeof value === "string" && MISSION_ID.test(value);
export const isEntityId = (value) => typeof value === "string" && value.trim() !== "" && value.length <= SELECT_MAX && !CONTROL.test(value);

/**
 * The context a URL carries. `rejected` names a parameter that was present but unusable (invalid, or given more than
 * once, which is ambiguous), so a page can say so instead of silently showing something else.
 */
export function parseDeepLink(search = "") {
  const params = new URLSearchParams(search);
  const result = {mission: null, select: null, rejected: []};
  for (const [name, valid] of [["mission", isMissionId], ["select", isEntityId]]) {
    const values = params.getAll(name);
    if (!values.length) continue;
    if (values.length === 1 && valid(values[0])) result[name] = values[0];
    else result.rejected.push(name);
  }
  return result;
}

const directory = (path) => path.split("/").slice(0, -1);

/** The relative path from one page to another, e.g. scythe-web/regional-rf-demo.html -> ../cesium-hypergraph-globe.html. */
export function relativePath(from, to) {
  const a = directory(PAGES[from].path), b = directory(PAGES[to].path);
  let common = 0;
  while (common < a.length && common < b.length && a[common] === b[common]) common++;
  return [...Array(a.length - common).fill(".."), ...b.slice(common), PAGES[to].path.split("/").pop()].join("/");
}

/**
 * A link from page `from` to page `to` carrying `mission` and `select`. A value that is not valid is left out, so a bad
 * value never propagates. Returns a relative URL.
 */
export function deepLink(to, context = {}, from = to) {
  if (!PAGES[to]) throw new RangeError(`unknown page: ${to}`);
  if (!PAGES[from]) throw new RangeError(`unknown page: ${from}`);
  return relativePath(from, to) + queryFor(context);
}

/** The query string for a context, "" when it has nothing valid to carry. */
function queryFor({mission = null, select = null} = {}) {
  const params = new URLSearchParams();
  if (isMissionId(mission)) params.set("mission", mission);
  if (isEntityId(select)) params.set("select", select);
  const query = params.toString();
  return query ? `?${query}` : "";
}

const INSTANCE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

/**
 * A link from the hub (served at the orchestrator's root) to a page of one instance, e.g.
 * scythe/i/scythe-ab12/cesium-hypergraph-globe.html?mission=m1. Relative, so it follows wherever the hub is served.
 */
export function instanceDeepLink(instanceId, to, context = {}) {
  if (!INSTANCE_ID.test(String(instanceId))) throw new RangeError(`not an instance id: ${instanceId}`);
  if (!PAGES[to]) throw new RangeError(`unknown page: ${to}`);
  return `scythe/i/${instanceId}/${PAGES[to].path}${queryFor(context)}`;
}

/**
 * Select `entityId` on a globe once the node is on it, polling because the graph arrives after the page loads. Resolves
 * "selected", "not_selectable" (the node is there but hidden, so the globe refused), or "not_found" after `timeoutMs`.
 */
export function selectWhenPresent(globe, entityId, {intervalMs = 500, timeoutMs = 30000, wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))} = {}) {
  const attempts = Math.max(1, Math.ceil(timeoutMs / intervalMs));
  return (async () => {
    for (let attempt = 0; attempt < attempts; attempt++) {
      if (globe?._graph?.nodes?.has(entityId)) {
        globe.selectNode(entityId);
        return globe._selectedEntityId === entityId ? "selected" : "not_selectable";
      }
      await wait(intervalMs);
    }
    return "not_found";
  })();
}
