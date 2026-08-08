import {validateEffectPlan} from "./directiveProtocol.js";

export const VIEW_INTENT_VERSION = "1.0";
const VIEW_EFFECTS = Object.freeze({
  "view.show-graph-provenance": {view: "provenance", title: "PROVENANCE LATTICE"},
  "view.show-graph-delta": {view: "temporal", title: "TEMPORAL WAKE"},
  "view.show-contradictions": {view: "contradictions", title: "CONTRADICTION FIELD"},
});
const VIEWS = new Set(Object.values(VIEW_EFFECTS).map((item) => item.view));

function exactKeys(value, allowed, name) {
  const unknown = Object.keys(value).filter((key) => !allowed.has(key));
  if (unknown.length) throw new Error(`${name} contains unknown fields: ${unknown.join(", ")}`);
}

export function validateViewIntent(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new TypeError("ViewIntent must be an object");
  exactKeys(input, new Set(["version", "view", "title", "planId", "directiveId", "effectType",
    "evidencePosture", "focusEntityIds", "graphRevision", "payload", "boundary"]), "ViewIntent");
  if (input.version !== VIEW_INTENT_VERSION) throw new Error("unsupported ViewIntent version");
  if (!VIEWS.has(input.view)) throw new Error("ViewIntent view is not allow-listed");
  if (VIEW_EFFECTS[input.effectType]?.view !== input.view) throw new Error("effect cannot route to requested view");
  if (typeof input.planId !== "string" || typeof input.directiveId !== "string") throw new Error("ViewIntent plan identity is required");
  if (!Array.isArray(input.focusEntityIds) || input.focusEntityIds.length > 200 ||
      !input.focusEntityIds.every((id) => typeof id === "string" && id.length <= 512)) {
    throw new Error("ViewIntent focusEntityIds are invalid");
  }
  if (!input.payload || typeof input.payload !== "object" || Array.isArray(input.payload)) throw new Error("ViewIntent payload is required");
  return Object.freeze(input);
}

export function viewIntentFromPlan(planInput) {
  const plan = validateEffectPlan(planInput);
  const effect = plan.effects.find((candidate) => VIEW_EFFECTS[candidate.type]);
  if (!effect) return null;
  const route = VIEW_EFFECTS[effect.type];
  const payload = effect.parameters;
  const graphRevision = payload.path?.graphRevision ?? payload.delta?.graphRevision ?? null;
  const focusEntityIds = [...new Set([
    ...(effect.targets ?? []).map((target) => target.id),
    ...(payload.path?.nodes ?? []).map((node) => node.id),
    ...(payload.findings ?? []).map((finding) => finding.id),
  ])].slice(0, 200);
  return validateViewIntent({version: VIEW_INTENT_VERSION, view: route.view, title: route.title,
    planId: plan.planId, directiveId: plan.directiveId, effectType: effect.type,
    evidencePosture: plan.evidencePosture ?? "mixed", focusEntityIds, graphRevision,
    payload, boundary: payload.caveat ?? "VISUALIZATION DOES NOT CHANGE EVIDENCE AUTHORITY"});
}
