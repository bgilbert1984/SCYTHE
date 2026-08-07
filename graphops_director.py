"""Deterministic Clarktech directive compiler for GraphOps EffectPlans."""

from __future__ import annotations

from typing import Any, Dict
import json

from graphops_effect_schema import new_effect_plan, validate_directive_request
from graphops_evidence_resolver import RFCellEvidenceResolver
from graphops_graph_resolver import GraphResolutionError, GraphSelectionResolver


class GraphOpsDirector:
    def __init__(self, resolver: RFCellEvidenceResolver | None = None, *, engine=None,
                 rf_observation_provider=None):
        self.resolver = resolver or RFCellEvidenceResolver()
        self.engine = engine
        self.rf_observation_provider = rf_observation_provider

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
        rf_selection = next(item for item in request["selection"] if item["kind"] == "rf-cell")
        evidence = self.resolver.resolve(rf_selection)
        evidence_ref = f"dataset:{evidence['datasetId']}:{evidence['tileId']}:{evidence['authorityAssetSha256']}"
        if request["directive"] == "correlate.rf-cell-graph":
            return self._compile_rf_graph_correlation(request, evidence, evidence_ref)
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

    def _compile_rf_graph_correlation(self, request: Dict[str, Any], evidence: Dict[str, Any],
                                      evidence_ref: str) -> Dict[str, Any]:
        if self.engine is None:
            raise GraphResolutionError("graph engine is unavailable")
        graph_selection = next(item for item in request["selection"]
                               if item["kind"] in {"graph-node", "event"})
        graph = GraphSelectionResolver(self.engine).resolve(graph_selection)
        node = graph["node"]
        frequency_hz = float(evidence["provenance"].get("frequencyHz") or 0.0)
        if frequency_hz <= 0:
            # Contract authority carries the RF frequency outside the authority block.
            manifest = json.loads((self.resolver.dataset_root / evidence["datasetId"] / "manifest.json").read_text())
            frequency_hz = float(manifest["physics"]["rf"]["frequencyHz"])
        frequency_label = f"{frequency_hz / 1e6:g}MHz"
        entity_literal = json.dumps(node["id"])
        dsl = [
            f"FOCUS {entity_literal}", "EXPAND neighbors depth=1 limit=50",
            f"RF_CORRELATE freq={frequency_label} window=2s",
        ]
        observations = []
        matches = []
        executed = request["requestedMode"] == "execute"
        if executed and self.rf_observation_provider is not None:
            observations = self.rf_observation_provider.query(
                since=None, frequency_hz=frequency_hz, tolerance_hz=25_000.0, limit=200)
            for observation in observations:
                try:
                    observed_at = float(observation["observed_at"])
                except (KeyError, TypeError, ValueError):
                    continue
                for edge in graph["incidentEdges"]:
                    try:
                        delta = abs(float(edge["timestamp"]) - observed_at)
                    except (TypeError, ValueError):
                        continue
                    if delta <= 2.0:
                        matches.append({
                            "evidenceId": observation.get("evidence_id"), "edgeId": edge["id"],
                            "observedAt": observed_at, "edgeTimestamp": float(edge["timestamp"]),
                            "deltaMilliseconds": round(delta * 1000.0, 3),
                            "findingClass": "INFERRED",
                        })
            matches.sort(key=lambda item: item["deltaMilliseconds"])
            matches = matches[:30]

        effects = [self._effect(
            f"{request['directiveId']}:dsl", "view.show-dsl-preview",
            {"dsl": dsl, "executed": executed}, [evidence_ref, f"graph:{graph['graphRevision']}:{node['id']}"],
            "INFERRED_RELATIONSHIP",
        )]
        can_render_fiber = bool(matches and node.get("position"))
        if can_render_fiber:
            effects.append(self._effect(
                f"{request['directiveId']}:fibers", "view.show-correlation-fibers", {
                    "from": [evidence["selection"]["latitudeDegrees"], evidence["selection"]["longitudeDegrees"], 0.0],
                    "to": node["position"], "matches": matches,
                    "label": "TEMPORAL CORRELATION // NOT CAUSATION",
                    "findingClass": "INFERRED",
                    "caveat": "Temporal proximity is not evidence of causality.",
                }, [evidence_ref, f"graph:{graph['graphRevision']}:{node['id']}"], "INFERRED_RELATIONSHIP",
            ))
        else:
            reason = ("Preview only; execute the bounded DSL to seek measured RF support." if not executed else
                      "No measured RF observation temporally supports an incident edge for the selected node.")
            effects.append(self._effect(
                f"{request['directiveId']}:no-data", "view.show-no-data", {
                    "reason": reason, "temporalAuthority": "ABSENT",
                    "requiredObservation": f"Collect measured RF near {frequency_label} with synchronized timestamps.",
                }, [evidence_ref, f"graph:{graph['graphRevision']}:{node['id']}"], "MISSING_DATA",
            ))
        status = "completed" if matches else "partially-completed"
        summary = (f"Found {len(matches)} measured-RF/incident-edge temporal matches for {node['id']}. "
                   "All matches are inferred correlations, not causal evidence." if matches else
                   f"No measured RF temporal support connects the solver cell to {node['id']}; the solver cell itself has statistical, not event, time semantics.")
        return new_effect_plan(
            request, status=status, summary=summary,
            evidencePosture="mixed" if matches else "no-data", effects=effects,
            queries=[{"dsl": dsl, "executed": executed, "bounded": True,
                      "rfObservationCount": len(observations), "matchCount": len(matches)}],
            claims=[{"text": summary, "evidenceClass": "INFERRED" if matches else "UNKNOWN",
                     "authority": "TEMPORAL_CORRELATION_ONLY",
                     "nullExpectation": "NOT_ESTIMATED — insufficient background event-rate model"}],
            supportingEvidence=[{"evidenceRef": evidence_ref, "evidenceClass": "SOLVER_OUTPUT"},
                                {"evidenceRef": f"graph:{graph['graphRevision']}:{node['id']}",
                                 "evidenceClass": node["evidenceClass"], "node": node}],
            assumptions=["RF and graph clocks are synchronized within the two-second window.",
                         "A measured RF observation at the solver frequency is relevant to the selected modeled cell."],
            falsifiers=[f"Capture calibrated, synchronized RF at {frequency_label} and test recurrence against incident graph edges."],
            refusals=[] if matches else [{"code": "TEMPORAL_EVIDENCE_ABSENT",
                                          "message": "The solver cell cannot supply event-time evidence."}],
        )
