import {instanceDeepLink} from "./deepLink.js";

const TARGETS = [["globe", "Globe"], ["regional", "Regional"], ["ops", "Mission Ops"]];

/**
 * What the palette can take the operator to. Only things that exist: the hub's own actions, a view of each instance that is
 * running, and a mission only when the hub has already loaded that instance's list (the palette never fetches missions, which
 * can number in the thousands). An instance that is not running offers nothing, and `skipped` says how many were left out.
 *
 *   actions:  [{id, label, run}]           what the page itself can do
 *   missions: Map<instanceId, {summary: {recent: [{id, name}]}}>   the hub's already-loaded lists
 */
export function paletteEntries({instances = [], actions = [], missions = new Map()} = {}) {
  const entries = actions.map((action) => ({kind: "action", id: `action:${action.id}`, label: action.label, hint: "Hub", run: action.run}));
  let skipped = 0;
  for (const instance of Array.isArray(instances) ? instances : []) {
    if (!instance || typeof instance.id !== "string") continue;
    if (instance.status !== "running") { skipped++; continue; }
    const name = instance.name || instance.id;
    try { instanceDeepLink(instance.id, "globe"); } catch { skipped++; continue; }   // an id that cannot be put in a URL has no destination
    for (const [target, label] of TARGETS) {
      entries.push({kind: "view", id: `view:${instance.id}:${target}`, label: `${name} — ${label}`, hint: "Open", href: instanceDeepLink(instance.id, target)});
    }
    for (const mission of missions.get(instance.id)?.summary?.recent ?? []) {
      for (const [target, label] of TARGETS) {
        entries.push({kind: "mission", id: `mission:${instance.id}:${mission.id}:${target}`, label: `${mission.name} (${mission.id}) — ${label}`, hint: `Mission on ${name}`,
          href: instanceDeepLink(instance.id, target, {mission: mission.id})});
      }
    }
  }
  return {entries, skipped};
}

/**
 * The entries that match `query`, best first. Every word of the query must appear in the label (case-insensitive); a label
 * that starts with the first word, then one with a word that does, ranks above one that merely contains it. An empty query
 * returns everything in its given order. Order is otherwise stable.
 */
export function filterEntries(entries, query = "", limit = 50) {
  const words = String(query).toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return entries.slice(0, limit);
  const scored = [];
  entries.forEach((entry, index) => {
    const label = entry.label.toLowerCase();
    if (!words.every((word) => label.includes(word))) return;
    const rank = label.startsWith(words[0]) ? 0 : label.split(/[^a-z0-9]+/).some((part) => part.startsWith(words[0])) ? 1 : 2;
    scored.push({entry, rank, index});
  });
  scored.sort((a, b) => a.rank - b.rank || a.index - b.index);
  return scored.slice(0, limit).map((item) => item.entry);
}
