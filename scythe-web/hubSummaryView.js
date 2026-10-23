import {instanceDeepLink} from "./deepLink.js";
import {STATE_MARK, countsLine, instanceCounts, instanceHealth, summarizeMissions} from "./hubSummary.js";

const TARGETS = [["globe", "Globe"], ["regional", "Regional"], ["ops", "Mission Ops"]];

/**
 * The hub's instrument panel: one card per instance with what it reports about itself, links into its views, and, on request,
 * its missions with links that carry the mission. It only reads. Missions load when a card is expanded, not on every poll: a
 * long-lived instance can hold thousands and GET /api/missions has no limit.
 */
export function mountHub({root, base = "", fetchImpl = globalThis.fetch?.bind(globalThis), now = () => Date.now(), pollMs = 10000,
                          missionPollMs = 60000, setTimer = globalThis.setInterval?.bind(globalThis), document: doc = globalThis.document} = {}) {
  const state = {instances: null, error: null, expanded: new Set(), missions: new Map()};

  const el = (tag, className, text) => {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const getJson = async (path) => {
    const response = await fetchImpl(`${base}${path}`, {cache: "no-store", credentials: "same-origin"});
    if (!response.ok) throw new Error(`${response.status}`);
    return response.json();
  };

  async function loadMissions(id) {
    const cached = state.missions.get(id);
    if (cached?.loading) return;
    state.missions.set(id, {...cached, loading: true});
    try {
      const data = await getJson(`/scythe/i/${encodeURIComponent(id)}/api/missions`);
      state.missions.set(id, {summary: summarizeMissions(data.missions), at: now()});
    } catch (error) {
      state.missions.set(id, {error: error.message, at: now()});
    }
    render();
  }

  async function refresh() {
    try {
      state.instances = (await getJson("/api/scythe/instances")).instances || [];
      state.error = null;
    } catch (error) {
      state.error = error.message;
    }
    render();
  }

  function links(instanceId, context, className) {
    const nav = el("span", className);
    for (const [target, label] of TARGETS) {
      const anchor = el("a", "hub__link", label);
      anchor.href = instanceDeepLink(instanceId, target, context);
      nav.append(anchor);
    }
    return nav;
  }

  function missionsBlock(instance) {
    const block = el("div", "hub__missions");
    const entry = state.missions.get(instance.id);
    if (!entry || (entry.loading && !entry.summary && !entry.error)) { block.append(el("div", "hub__note", "Loading missions…")); return block; }
    if (entry.error) { block.append(el("div", "hub__note hub__note--bad", `Missions unavailable (${entry.error})`)); return block; }
    const summary = entry.summary;
    block.append(el("div", "hub__note", `${summary.total} mission${summary.total === 1 ? "" : "s"}: ${summary.open} open, ${summary.closed} closed${summary.other ? `, ${summary.other} other` : ""}`));
    if (summary.total > summary.recent.length) block.append(el("div", "hub__note", `Showing the ${summary.recent.length} most recently updated.`));
    for (const mission of summary.recent) {
      const row = el("div", "hub__mission");
      row.dataset.mission = mission.id;
      row.append(el("span", `hub__status hub__status--${["open", "closed"].includes(mission.status) ? mission.status : "other"}`, mission.status), el("span", "hub__mission-name", mission.name),
        links(instance.id, {mission: mission.id}, "hub__links"));
      block.append(row);
    }
    return block;
  }

  function card(instance) {
    const node = el("article", "hub__card");
    node.dataset.instance = instance.id;
    const head = el("header", "hub__head");
    head.append(el("span", `hub__dot hub__dot--${instance.status || "unknown"}`), el("strong", "hub__name", instance.name || instance.id),
      el("span", "hub__port", instance.port ? `port ${instance.port}` : ""));
    node.append(head, el("div", "hub__counts", countsLine(instanceCounts(instance))));
    const health = el("ul", "hub__health");
    for (const row of instanceHealth(instance, now())) {
      const item = el("li", `hub__chip hub__chip--${row.state}`);
      item.title = `${row.label}: ${row.detail}`;
      item.dataset.key = row.key;
      item.dataset.state = row.state;
      item.append(el("span", "hub__mark", STATE_MARK[row.state]), el("span", "hub__label", row.label), el("span", "hub__detail", row.detail));
      health.append(item);
    }
    node.append(health, links(instance.id, {}, "hub__links"));
    if (instance.status === "running") {
      const toggle = el("button", "hub__toggle", state.expanded.has(instance.id) ? "Hide missions" : "Missions");
      toggle.type = "button";
      toggle.addEventListener("click", () => {
        if (state.expanded.has(instance.id)) state.expanded.delete(instance.id);
        else { state.expanded.add(instance.id); loadMissions(instance.id); }
        render();
      });
      node.append(toggle);
      if (state.expanded.has(instance.id)) node.append(missionsBlock(instance));
    }
    return node;
  }

  function render() {
    root.textContent = "";
    if (state.error && !state.instances) { root.append(el("div", "hub__note hub__note--bad", `Orchestrator unreachable (${state.error})`)); return; }
    if (state.error) root.append(el("div", "hub__note hub__note--bad", `Showing the last report; the orchestrator did not answer (${state.error})`));
    if (!state.instances) { root.append(el("div", "hub__note", "Loading instances…")); return; }
    if (!state.instances.length) { root.append(el("div", "hub__note", "No instances. Launch one to begin.")); return; }
    for (const instance of state.instances) root.append(card(instance));
  }

  render();
  refresh();
  if (setTimer) {
    if (pollMs > 0) setTimer(() => { if (!doc?.hidden) refresh(); }, pollMs);
    if (missionPollMs > 0) setTimer(() => { if (!doc?.hidden) for (const id of state.expanded) loadMissions(id); }, missionPollMs);
  }
  return {refresh, loadMissions, state};
}
