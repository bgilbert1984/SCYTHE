"""Deterministic Clarktech directive compiler for GraphOps EffectPlans."""

from __future__ import annotations

from typing import Any, Dict

from graphops_effect_schema import new_effect_plan, validate_directive_request
from graphops_evidence_resolver import RFCellEvidenceResolver


class GraphOpsDirector:
    def __init__(self, resolver: RFCellEvidenceResolver | None = None):
        self.resolver = resolver or RFCellEvidenceResolver()

    @staticmethod
    def _effect(effect_id: str, effect_type: str, parameters: Dict[str, Any],
                evidence_refs: list[str], style: str) -> Dict[str, Any]:
        return {
            "effectId": effect_id, "type": effect_type, "phase": "preview",
            "targets": [{"kind": "rf-cell", "id": evidence_refs[0]}],
            "parameters": parameters, "styleToken": style,
            "evidenceRefs": evidence_refs, "authorityImpact": "none",
            "reversible": True, "ttlMilliseconds": 300000,
        }

    def compile(self, payload: Dict[str, Any], *, expected_mode: str | None = None) -> Dict[str, Any]:
        request = validate_directive_request(payload, expected_mode=expected_mode)
        evidence = self.resolver.resolve(request["selection"][0])
        evidence_ref = f"dataset:{evidence['datasetId']}:{evidence['tileId']}:{evidence['authorityAssetSha256']}"
        threshold = request.get("parameters", {}).get("threshold")
        if threshold is None:
            threshold = (request["selection"][0].get("coverageThreshold") or {}).get("value")
        comparison = request.get("parameters", {}).get("comparison") or (
            request["selection"][0].get("coverageThreshold") or {}).get("comparison", "LTE")
        units = request.get("parameters", {}).get("units") or evidence["units"]
        if units != evidence["units"]:
            raise ValueError("threshold units do not match authoritative quantity")
        covered = None if threshold is None else (
            evidence["authoritativeValue"] <= float(threshold) if comparison == "LTE"
            else evidence["authoritativeValue"] >= float(threshold)
        )
        effects = [
            self._effect(f"{request['directiveId']}:prism", "view.show-reality-prism", {
                "datasetId": evidence["datasetId"], "tileId": evidence["tileId"],
                "quantity": evidence["quantity"], "units": evidence["units"],
                "authoritativeValue": evidence["authoritativeValue"],
                "displayValue": evidence["displayValue"], "displayDelta": evidence["displayDelta"],
                "authorityAsset": evidence["authorityAsset"],
                "authorityAssetSha256": evidence["authorityAssetSha256"],
                "interpolation": evidence["interpolation"], "provenance": evidence["provenance"],
                "coverage": covered, "threshold": threshold, "comparison": comparison,
            }, [evidence_ref], "STATIC_SOLVER_OUTPUT"),
            self._effect(f"{request['directiveId']}:provenance", "view.show-provenance-path", {
                "source": evidence["authorityAsset"], "lineage": evidence["lineage"],
            }, [evidence_ref], "STATIC_SOLVER_OUTPUT"),
            self._effect(f"{request['directiveId']}:highlight", "view.highlight-targets", {}, [evidence_ref], "THRESHOLD_LENS"),
        ]
        if threshold is not None:
            effects.append(self._effect(f"{request['directiveId']}:threshold", "view.set-coverage-threshold", {
                "value": float(threshold), "units": units, "comparison": comparison,
            }, [evidence_ref], "THRESHOLD_LENS"))
        summary = (
            f"Authoritative {evidence['quantity']} is {evidence['authoritativeValue']:.4f} {evidence['units']}."
            + (f" At {float(threshold):.2f} {units} ({comparison}), this cell is "
               f"{'covered' if covered else 'a coverage gap'}." if threshold is not None else "")
        )
        return new_effect_plan(
            request, summary=summary, effects=effects,
            claims=[{"text": summary, "evidenceClass": "SOLVER_OUTPUT", "authority": "AUTHORITATIVE_VALUES"}],
            supportingEvidence=[{"evidenceRef": evidence_ref, **evidence}],
            assumptions=["The selected threshold is the intended classification rule."],
            falsifiers=["Collect a calibrated field measurement at the selected location."],
            mutations=[] if request["requestedMode"] == "preview" else [{
                "target": "browser-view", "kind": "reversible-effect-plan", "authorityImpact": "none",
            }],
            undoToken=None if request["requestedMode"] == "preview" else f"undo:{request['directiveId']}",
        )
