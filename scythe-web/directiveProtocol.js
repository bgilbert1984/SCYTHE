export const GRAPHOPS_PROTOCOL_VERSION = "1.0";

export const EFFECT_TYPES = Object.freeze(new Set([
  "view.highlight-targets", "view.set-coverage-threshold",
  "view.show-provenance-path", "view.show-reality-prism",
]));

export const STYLE_TOKENS = Object.freeze(new Set([
  "EVIDENCE_ISOLATION", "STATIC_SOLVER_OUTPUT", "CAUSAL_DISAGREEMENT",
  "CONTRADICTION", "UNCERTAINTY_BOUNDARY", "MISSING_DATA",
  "AUTHORITY_GATE", "THRESHOLD_LENS",
]));

function object(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${name} must be an object`);
  }
  return value;
}

function exactKeys(value, allowed, name) {
  const unknown = Object.keys(value).filter((key) => !allowed.has(key));
  if (unknown.length) throw new TypeError(`${name} has unknown fields: ${unknown.join(", ")}`);
}

export function validateDirectiveRequest(input) {
  const request = object(input, "directive request");
  exactKeys(request, new Set([
    "protocolVersion", "directiveId", "directive", "utterance", "selection",
    "parameters", "viewContext", "requestedMode", "idempotencyKey",
  ]), "directive request");
  if (request.protocolVersion !== GRAPHOPS_PROTOCOL_VERSION) throw new Error("unsupported protocolVersion");
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(request.directiveId ?? "")) throw new Error("invalid directiveId");
  if (!["explain.coverage-cell", "reclassify.coverage-threshold"].includes(request.directive)) {
    throw new Error("directive is not allow-listed");
  }
  if (!Array.isArray(request.selection) || request.selection.length < 1 || request.selection.length > 16) {
    throw new Error("selection must contain 1-16 items");
  }
  if (!["preview", "execute"].includes(request.requestedMode)) throw new Error("invalid requestedMode");
  if (typeof request.idempotencyKey !== "string" || !request.idempotencyKey) throw new Error("idempotencyKey is required");
  return request;
}

export function validateEffect(effectInput) {
  const effect = object(effectInput, "effect");
  exactKeys(effect, new Set([
    "effectId", "type", "phase", "targets", "parameters", "styleToken",
    "evidenceRefs", "authorityImpact", "reversible", "ttlMilliseconds",
  ]), "effect");
  if (!EFFECT_TYPES.has(effect.type)) throw new Error(`effect type is not allow-listed: ${effect.type}`);
  if (!STYLE_TOKENS.has(effect.styleToken)) throw new Error(`style token is not allow-listed: ${effect.styleToken}`);
  if (effect.authorityImpact !== "none") throw new Error("browser effects cannot change authority");
  if (effect.reversible !== true) throw new Error("browser effects must be reversible");
  object(effect.parameters, "effect parameters");
  const parameterKeys = {
    "view.highlight-targets": [],
    "view.set-coverage-threshold": ["value", "units", "comparison"],
    "view.show-provenance-path": ["source", "lineage"],
    "view.show-reality-prism": ["datasetId", "tileId", "quantity", "units", "authoritativeValue",
      "displayValue", "displayDelta", "authorityAsset", "authorityAssetSha256", "interpolation",
      "provenance", "coverage", "threshold", "comparison"],
  };
  exactKeys(effect.parameters, new Set(parameterKeys[effect.type]), `${effect.type} parameters`);
  if (!Array.isArray(effect.targets) || !effect.targets.every((target) =>
    target && target.kind === "rf-cell" && typeof target.id === "string")) {
    throw new Error("effect targets must be typed rf-cell references");
  }
  return effect;
}

export function validateEffectPlan(input) {
  const plan = object(input, "EffectPlan");
  exactKeys(plan, new Set([
    "protocolVersion", "directiveId", "planId", "status", "summary", "evidencePosture",
    "effects", "queries", "jobs", "proposals", "claims", "supportingEvidence",
    "contradictingEvidence", "assumptions", "falsifiers", "mutations", "refusals",
    "undoToken", "expiresAt",
  ]), "EffectPlan");
  if (plan.protocolVersion !== GRAPHOPS_PROTOCOL_VERSION) throw new Error("unsupported EffectPlan version");
  if (!["completed", "partially-completed", "refused", "unavailable"].includes(plan.status)) {
    throw new Error("invalid EffectPlan status");
  }
  for (const field of ["effects", "queries", "jobs", "proposals", "claims", "supportingEvidence",
    "contradictingEvidence", "assumptions", "falsifiers", "mutations", "refusals"]) {
    if (!Array.isArray(plan[field])) throw new Error(`EffectPlan.${field} must be an array`);
  }
  plan.effects.forEach(validateEffect);
  if (plan.expiresAt && Date.parse(plan.expiresAt) <= Date.now()) throw new Error("EffectPlan has expired");
  return plan;
}
