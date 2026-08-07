"""Validation and construction for the Clarktech directive/effect protocol."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any, Dict


PROTOCOL_VERSION = "1.0"
DIRECTIVES = {"explain.coverage-cell", "reclassify.coverage-threshold", "correlate.rf-cell-graph"}
EFFECT_TYPES = {
    "view.highlight-targets", "view.set-coverage-threshold",
    "view.show-provenance-path", "view.show-reality-prism",
    "view.show-dsl-preview", "view.show-correlation-fibers", "view.show-no-data",
}
STYLE_TOKENS = {
    "EVIDENCE_ISOLATION", "STATIC_SOLVER_OUTPUT", "CAUSAL_DISAGREEMENT",
    "CONTRADICTION", "UNCERTAINTY_BOUNDARY", "MISSING_DATA",
    "AUTHORITY_GATE", "THRESHOLD_LENS", "INFERRED_RELATIONSHIP",
}
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_DATASET = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_REQUEST_KEYS = {
    "protocolVersion", "directiveId", "directive", "utterance", "selection",
    "parameters", "viewContext", "requestedMode", "idempotencyKey",
}
_SELECTION_KEYS = {
    "kind", "datasetId", "tileId", "longitudeDegrees", "latitudeDegrees",
    "displayValue", "displayUnits", "displayAssetHash", "coverageThreshold",
    "entityId", "graphRevision", "position", "observedAt",
}


class DirectiveProtocolError(ValueError):
    pass


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DirectiveProtocolError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise DirectiveProtocolError(f"{name} must be finite")
    return number


def validate_directive_request(payload: Any, *, expected_mode: str | None = None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise DirectiveProtocolError("directive request must be an object")
    unknown = set(payload) - _REQUEST_KEYS
    if unknown:
        raise DirectiveProtocolError(f"unknown directive request fields: {sorted(unknown)}")
    if payload.get("protocolVersion") != PROTOCOL_VERSION:
        raise DirectiveProtocolError("unsupported protocolVersion")
    if not _ID.fullmatch(str(payload.get("directiveId", ""))):
        raise DirectiveProtocolError("directiveId is invalid")
    if payload.get("directive") not in DIRECTIVES:
        raise DirectiveProtocolError("directive is not allow-listed")
    mode = payload.get("requestedMode")
    if mode not in {"preview", "execute"} or (expected_mode and mode != expected_mode):
        raise DirectiveProtocolError(f"requestedMode must be {expected_mode or 'preview or execute'}")
    if not isinstance(payload.get("idempotencyKey"), str) or not 1 <= len(payload["idempotencyKey"]) <= 256:
        raise DirectiveProtocolError("idempotencyKey is required")
    selections = payload.get("selection")
    if not isinstance(selections, list) or not 1 <= len(selections) <= 16:
        raise DirectiveProtocolError("selection must contain 1-16 items")
    normalized = dict(payload)
    normalized_selections = []
    for index, selection in enumerate(selections):
        if not isinstance(selection, dict) or set(selection) - _SELECTION_KEYS:
            raise DirectiveProtocolError(f"selection[{index}] contains unknown fields")
        kind = selection.get("kind")
        if kind not in {"rf-cell", "graph-node", "event"}:
            raise DirectiveProtocolError("selection kind is not supported")
        item = dict(selection)
        if kind == "rf-cell":
            if not _DATASET.fullmatch(str(selection.get("datasetId", ""))):
                raise DirectiveProtocolError("selection datasetId is invalid")
            if not isinstance(selection.get("tileId"), str) or not selection["tileId"]:
                raise DirectiveProtocolError("selection tileId is required")
            item["longitudeDegrees"] = _finite(selection.get("longitudeDegrees"), "longitudeDegrees")
            item["latitudeDegrees"] = _finite(selection.get("latitudeDegrees"), "latitudeDegrees")
            if not -180 <= item["longitudeDegrees"] <= 180 or not -90 <= item["latitudeDegrees"] <= 90:
                raise DirectiveProtocolError("selection coordinates are out of range")
            if "displayValue" in item:
                item["displayValue"] = _finite(item["displayValue"], "displayValue")
        elif not isinstance(selection.get("entityId"), str) or not selection["entityId"]:
            raise DirectiveProtocolError("graph selection entityId is required")
        normalized_selections.append(item)
    normalized["selection"] = normalized_selections
    parameters = payload.get("parameters") or {}
    if not isinstance(parameters, dict) or set(parameters) - {"threshold", "units", "comparison"}:
        raise DirectiveProtocolError("parameters contains unknown fields")
    if payload["directive"] == "reclassify.coverage-threshold":
        if "threshold" not in parameters:
            raise DirectiveProtocolError("threshold is required for reclassification")
        parameters = dict(parameters)
        parameters["threshold"] = _finite(parameters["threshold"], "threshold")
        if parameters.get("comparison") not in {"LTE", "GTE"} or not parameters.get("units"):
            raise DirectiveProtocolError("reclassification requires units and LTE/GTE comparison")
    normalized["parameters"] = parameters
    if payload["directive"] == "correlate.rf-cell-graph":
        kinds = {item["kind"] for item in normalized_selections}
        if "rf-cell" not in kinds or not kinds.intersection({"graph-node", "event"}):
            raise DirectiveProtocolError("RF/graph correlation requires an rf-cell and graph-node/event selection")
    return normalized


def validate_effect(effect: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(effect, dict) or effect.get("type") not in EFFECT_TYPES:
        raise DirectiveProtocolError("effect type is not allow-listed")
    if effect.get("styleToken") not in STYLE_TOKENS:
        raise DirectiveProtocolError("effect styleToken is not allow-listed")
    if effect.get("authorityImpact") != "none":
        raise DirectiveProtocolError("browser effects cannot change authority")
    if effect.get("reversible") is not True:
        raise DirectiveProtocolError("browser effects must be reversible")
    return effect


def new_effect_plan(request: Dict[str, Any], **values: Any) -> Dict[str, Any]:
    seed = json.dumps({"id": request["directiveId"], "key": request["idempotencyKey"]}, sort_keys=True)
    plan_id = "plan-" + hashlib.blake2s(seed.encode(), digest_size=8).hexdigest()
    plan = {
        "protocolVersion": PROTOCOL_VERSION, "directiveId": request["directiveId"],
        "planId": plan_id, "status": "completed", "summary": "",
        "evidencePosture": "solver-backed", "effects": [], "queries": [],
        "jobs": [], "proposals": [], "claims": [], "supportingEvidence": [],
        "contradictingEvidence": [], "assumptions": [], "falsifiers": [],
        "mutations": [], "refusals": [], "undoToken": None,
        "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    plan.update(values)
    for effect in plan["effects"]:
        validate_effect(effect)
    return plan
