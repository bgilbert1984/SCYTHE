"""Deterministic Clarktech directive compiler for GraphOps EffectPlans."""

from __future__ import annotations

from typing import Any, Dict
import json

from graphops_effect_schema import new_effect_plan, validate_directive_request
from graphops_evidence_resolver import RFCellEvidenceResolver
from graphops_graph_resolver import GraphResolutionError, GraphSelectionResolver
from lunar_evidence_resolver import LunarEvidenceResolver


class GraphOpsDirector:
    def __init__(self, resolver: RFCellEvidenceResolver | None = None, *, engine=None,
                 rf_observation_provider=None, lunar_resolver: LunarEvidenceResolver | None = None):
        self.resolver = resolver or RFCellEvidenceResolver()
        self.engine = engine
        self.rf_observation_provider = rf_observation_provider
        self.lunar_resolver = lunar_resolver or LunarEvidenceResolver()

    @staticmethod
    def _effect(effect_id: str, effect_type: str, parameters: Dict[str, Any],
                evidence_refs: list[str], style: str, target_kind: str = "rf-cell") -> Dict[str, Any]:
        return {
            "effectId": effect_id, "type": effect_type, "phase": "preview",
            "targets": [{"kind": target_kind, "id": evidence_refs[0]}],
            "parameters": parameters, "styleToken": style,
            "evidenceRefs": evidence_refs, "authorityImpact": "none",
            "reversible": True, "ttlMilliseconds": 300000,
        }

    def compile(self, payload: Dict[str, Any], *, expected_mode: str | None = None) -> Dict[str, Any]:
        request = validate_directive_request(payload, expected_mode=expected_mode)
        if request["directive"] == "explain.lunar-location":
            return self._compile_lunar_location(request)
        if request["directive"] == "compare.graph-delta":
            return self._compile_graph_delta(request)
        if request["directive"] == "trace.provenance-impact":
            return self._compile_provenance(request)
        if request["directive"] == "expose.contradictions":
            return self._compile_contradictions(request)
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

    def _compile_lunar_location(self, request: Dict[str, Any]) -> Dict[str, Any]:
        selection = next(item for item in request["selection"] if item["kind"] == "lunar-location")
        evidence = self.lunar_resolver.resolve(selection)
        artifact_refs = [f"sha256:{item['sha256']}" for item in evidence["artifacts"]]
        evidence_ref = f"lunar:{evidence['datasetId']}:{evidence['locationId']}"
        summary = (
            f"Moon-fixed location {evidence['latitudeDegrees']:.4f}°, "
            f"{evidence['longitudeDegrees']:.4f}° is resolved on the reference ellipsoid. "
            "M0 has no registered terrain tile, so no elevation, slope, visibility, or illumination value is asserted."
        )
        effect = self._effect(
            f"{request['directiveId']}:lunar-prism", "view.show-lunar-prism", {
                "datasetId": evidence["datasetId"], "locationId": evidence["locationId"],
                "celestialBody": evidence["celestialBody"], "referenceFrame": evidence["referenceFrame"],
                "longitudeDegrees": evidence["longitudeDegrees"],
                "latitudeDegrees": evidence["latitudeDegrees"],
                "heightMeters": evidence["heightMeters"],
                "spatialAuthority": evidence["spatialAuthority"],
                "terrainAuthority": evidence["terrainAuthority"],
                "elevationMeters": evidence["elevationMeters"],
                "evidenceClass": evidence["evidenceClass"], "artifacts": evidence["artifacts"],
                "limitations": evidence["limitations"],
            }, [evidence_ref, *artifact_refs], "LUNAR_REFERENCE", target_kind="lunar-location",
        )
        return new_effect_plan(
            request, status="partially-completed", summary=summary, evidencePosture="sparse",
            effects=[effect],
            claims=[{"text": summary, "evidenceClass": "DERIVED_VISUALIZATION",
                     "authority": "REFERENCE_ELLIPSOID_ONLY"}],
            supportingEvidence=[{"evidenceRef": evidence_ref, **evidence}],
            assumptions=["The selected longitude and latitude use the declared Moon-fixed frame."],
            falsifiers=["Ingest and checksum a registered LOLA DEM tile covering this coordinate."],
            refusals=["No terrain height, slope, lighting, Earth visibility, or RF occultation was inferred from imagery."],
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
                               if item["kind"] in {"graph-node", "graph-edge", "event"})
        graph = GraphSelectionResolver(self.engine).resolve(graph_selection)
        entity = graph.get("node") or graph.get("edge")
        entity_id = entity["id"]
        position = entity.get("position") or graph_selection.get("position")
        frequency_hz = float(evidence["provenance"].get("frequencyHz") or 0.0)
        if frequency_hz <= 0:
            # Contract authority carries the RF frequency outside the authority block.
            manifest = json.loads((self.resolver.dataset_root / evidence["datasetId"] / "manifest.json").read_text())
            frequency_hz = float(manifest["physics"]["rf"]["frequencyHz"])
        frequency_label = f"{frequency_hz / 1e6:g}MHz"
        entity_literal = json.dumps(entity_id)
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
            {"dsl": dsl, "executed": executed}, [evidence_ref, f"graph:{graph['graphRevision']}:{entity_id}"],
            "INFERRED_RELATIONSHIP",
        )]
        can_render_fiber = bool(matches and position)
        if can_render_fiber:
            effects.append(self._effect(
                f"{request['directiveId']}:fibers", "view.show-correlation-fibers", {
                    "from": [evidence["selection"]["latitudeDegrees"], evidence["selection"]["longitudeDegrees"], 0.0],
                    "to": position, "matches": matches,
                    "label": "TEMPORAL CORRELATION // NOT CAUSATION",
                    "findingClass": "INFERRED",
                    "caveat": "Temporal proximity is not evidence of causality.",
                }, [evidence_ref, f"graph:{graph['graphRevision']}:{entity_id}"], "INFERRED_RELATIONSHIP",
            ))
        else:
            reason = ("Preview only; execute the bounded DSL to seek measured RF support." if not executed else
                      "No measured RF observation temporally supports an incident edge for the selected node.")
            effects.append(self._effect(
                f"{request['directiveId']}:no-data", "view.show-no-data", {
                    "reason": reason, "temporalAuthority": "ABSENT",
                    "requiredObservation": f"Collect measured RF near {frequency_label} with synchronized timestamps.",
                }, [evidence_ref, f"graph:{graph['graphRevision']}:{entity_id}"], "MISSING_DATA",
            ))
        status = "completed" if matches else "partially-completed"
        summary = (f"Found {len(matches)} measured-RF/incident-edge temporal matches for {entity_id}. "
                   "All matches are inferred correlations, not causal evidence." if matches else
                   f"No measured RF temporal support connects the solver cell to {entity_id}; the solver cell itself has statistical, not event, time semantics.")
        return new_effect_plan(
            request, status=status, summary=summary,
            evidencePosture="mixed" if matches else "no-data", effects=effects,
            queries=[{"dsl": dsl, "executed": executed, "bounded": True,
                      "rfObservationCount": len(observations), "matchCount": len(matches)}],
            claims=[{"text": summary, "evidenceClass": "INFERRED" if matches else "UNKNOWN",
                     "authority": "TEMPORAL_CORRELATION_ONLY",
                     "nullExpectation": "NOT_ESTIMATED — insufficient background event-rate model"}],
            supportingEvidence=[{"evidenceRef": evidence_ref, "evidenceClass": "SOLVER_OUTPUT"},
                                {"evidenceRef": f"graph:{graph['graphRevision']}:{entity_id}",
                                 "evidenceClass": entity["evidenceClass"], "entity": entity}],
            assumptions=["RF and graph clocks are synchronized within the two-second window.",
                         "A measured RF observation at the solver frequency is relevant to the selected modeled cell."],
            falsifiers=[f"Capture calibrated, synchronized RF at {frequency_label} and test recurrence against incident graph edges."],
            refusals=[] if matches else [{"code": "TEMPORAL_EVIDENCE_ABSENT",
                                          "message": "The solver cell cannot supply event-time evidence."}],
        )

    def _graph_resolver(self) -> GraphSelectionResolver:
        if self.engine is None:
            raise GraphResolutionError("graph engine is unavailable")
        return GraphSelectionResolver(self.engine)

    @staticmethod
    def _graph_selection(request: Dict[str, Any]) -> Dict[str, Any]:
        return next(item for item in request["selection"]
                    if item["kind"] in {"graph-node", "graph-edge", "event"})

    def _compile_graph_delta(self, request: Dict[str, Any]) -> Dict[str, Any]:
        pins = [item for item in request["selection"] if item["kind"] == "time-pin"]
        start, end = sorted((float(item["timestamp"]) for item in pins))
        limit = int(request.get("parameters", {}).get("limit", 100))
        dsl = [f"PIN from={start:.6f} clock={json.dumps(pins[0]['clockId'])}",
               f"PIN to={end:.6f} clock={json.dumps(pins[0]['clockId'])}",
               f"GRAPH_DELTA limit={limit}"]
        executed = request["requestedMode"] == "execute"
        delta = self._graph_resolver().delta(start, end, limit=limit) if executed else {
            "from": start, "to": end, "addedNodes": [], "addedEdges": [],
            "removedNodes": [], "removedEdges": [], "unknownTimeCount": 0, "bounded": True,
            "limit": limit, "temporalSemantics": "PREVIEW; NOT EXECUTED",
        }
        graph_ref = f"graph:{delta.get('graphRevision', 'current')}:delta:{start}:{end}"
        effects = [self._effect(f"{request['directiveId']}:dsl", "view.show-dsl-preview",
                                {"dsl": dsl, "executed": executed}, [graph_ref],
                                "INFERRED_RELATIONSHIP", "time-pin")]
        for index, pin in enumerate(pins):
            effects.append(self._effect(f"{request['directiveId']}:pin:{index}", "view.pin-time", {
                "timestamp": float(pin["timestamp"]), "clockId": pin["clockId"],
                "uncertaintyMilliseconds": float(pin.get("uncertaintyMilliseconds", 0)),
                "label": "FROM" if float(pin["timestamp"]) == start else "TO",
            }, [graph_ref], "UNCERTAINTY_BOUNDARY", "time-pin"))
        effects.append(self._effect(f"{request['directiveId']}:delta", "view.show-graph-delta", {
            "delta": delta, "executed": executed,
            "caveat": "Current-graph timestamp projection; removals require retained historical snapshots.",
        }, [graph_ref], "CAUSAL_DISAGREEMENT", "time-pin"))
        changes = len(delta["addedNodes"]) + len(delta["addedEdges"])
        summary = (f"GRAPH_DELTA found {changes} timestamped additions between the pins. "
                   f"{delta['unknownTimeCount']} entities have unknown time." if executed else
                   "GRAPH_DELTA preview compiled; no graph query executed.")
        return new_effect_plan(request, status="completed", summary=summary,
            evidencePosture="mixed" if changes else "sparse", effects=effects,
            queries=[{"dsl": dsl, "executed": executed, "bounded": True,
                      "resultCount": changes, "temporalSemantics": delta["temporalSemantics"]}],
            claims=[{"text": summary, "evidenceClass": "INFERRED",
                     "authority": "GRAPH_ENTITY_TIMESTAMPS_ONLY"}],
            supportingEvidence=[{"evidenceRef": graph_ref, "evidenceClass": "INFERRED", "delta": delta}],
            assumptions=["Selected clock identity and timestamp normalization are valid."],
            falsifiers=["Compare against retained immutable graph snapshots for the same pins."],
            mutations=[] if not executed else [{"target": "browser-view", "kind": "reversible-effect-plan", "authorityImpact": "none"}])

    def _compile_provenance(self, request: Dict[str, Any]) -> Dict[str, Any]:
        selection = self._graph_selection(request)
        depth = int(request.get("parameters", {}).get("depth", 3))
        limit = int(request.get("parameters", {}).get("limit", 100))
        executed = request["requestedMode"] == "execute"
        dsl = [f"FOCUS {json.dumps(selection['entityId'])}",
               f"PROVENANCE_IMPACT depth={depth} limit={limit}"]
        path = self._graph_resolver().provenance(selection, depth=depth, limit=limit) if executed else {
            "root": selection["entityId"], "nodes": [], "edges": [], "sources": [], "bounded": True,
            "depth": depth, "limit": limit,
        }
        graph_ref = f"graph:{selection.get('graphRevision', 'current')}:{selection['entityId']}"
        effects = [self._effect(f"{request['directiveId']}:dsl", "view.show-dsl-preview",
                                {"dsl": dsl, "executed": executed}, [graph_ref],
                                "INFERRED_RELATIONSHIP", selection["kind"]),
                   self._effect(f"{request['directiveId']}:provenance", "view.show-graph-provenance",
                                {"path": path, "executed": executed,
                                 "caveat": "Traversal shows graph adjacency and declared sources, not causal dependence."},
                                [graph_ref], "STATIC_SOLVER_OUTPUT", selection["kind"])]
        count = len(path["nodes"]) + len(path["edges"])
        summary = f"Bounded provenance traversal returned {count} entities and {len(path['sources'])} declared sources."
        return new_effect_plan(request, status="completed", summary=summary,
            evidencePosture="mixed" if path["sources"] else "inference-heavy", effects=effects,
            queries=[{"dsl": dsl, "executed": executed, "bounded": True, "resultCount": count}],
            claims=[{"text": summary, "evidenceClass": "INFERRED", "authority": "DECLARED_GRAPH_PROVENANCE"}],
            supportingEvidence=[{"evidenceRef": graph_ref, "evidenceClass": "INFERRED", "path": path}],
            assumptions=["Graph adjacency is relevant to the requested provenance scope."],
            falsifiers=["Inspect original source records and immutable ingestion receipts."], mutations=[])

    def _compile_contradictions(self, request: Dict[str, Any]) -> Dict[str, Any]:
        selection = self._graph_selection(request)
        limit = int(request.get("parameters", {}).get("limit", 100))
        executed = request["requestedMode"] == "execute"
        dsl = [f"FOCUS {json.dumps(selection['entityId'])}", f"CONTRADICTIONS depth=2 limit={limit}"]
        result = self._graph_resolver().contradictions(selection, limit=limit) if executed else {
            "root": selection["entityId"], "contradictions": [], "bounded": True, "limit": limit,
        }
        graph_ref = f"graph:{selection.get('graphRevision', 'current')}:{selection['entityId']}"
        findings = result["contradictions"]
        effects = [self._effect(f"{request['directiveId']}:dsl", "view.show-dsl-preview",
                                {"dsl": dsl, "executed": executed}, [graph_ref],
                                "CONTRADICTION", selection["kind"]),
                   self._effect(f"{request['directiveId']}:contradictions", "view.show-contradictions",
                                {"findings": findings, "root": result["root"], "executed": executed,
                                 "caveat": "Contradictions are retained for adjudication; no consensus is synthesized."},
                                [graph_ref], "CONTRADICTION", selection["kind"])]
        summary = (f"Exposed {len(findings)} explicit contradiction relations." if executed else
                   "Contradiction query preview compiled; no graph query executed.")
        return new_effect_plan(request, status="completed", summary=summary,
            evidencePosture="mixed" if findings else "sparse", effects=effects,
            queries=[{"dsl": dsl, "executed": executed, "bounded": True, "resultCount": len(findings)}],
            claims=[{"text": summary, "evidenceClass": "CONTRADICTION" if findings else "UNKNOWN",
                     "authority": "DECLARED_GRAPH_RELATIONS"}],
            contradictingEvidence=[{"evidenceRef": f"graph-edge:{item['id']}", **item} for item in findings],
            assumptions=[], falsifiers=["Adjudicate each relation against its original evidence sources."], mutations=[])
