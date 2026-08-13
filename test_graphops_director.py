import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

import jsonschema

from graphops_director import GraphOpsDirector
from graphops_effect_schema import DirectiveProtocolError, validate_directive_request
from graphops_evidence_resolver import EvidenceResolutionError, RFCellEvidenceResolver
from graphops_graph_resolver import GraphResolutionError, GraphSelectionResolver


ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "datasets" / "ntia-itm-sf-bay-area-v1"


def request(*, display_value=9999.0, directive="explain.coverage-cell", mode="preview"):
    metadata = json.loads((DATASET / "tile-metadata.json").read_text())
    selection = {
        "kind": "rf-cell", "datasetId": "ntia-itm-sf-bay-area-v1",
        "tileId": "regional-z0", "longitudeDegrees": -122.5994,
        "latitudeDegrees": 37.5949, "displayValue": display_value,
        "displayUnits": "dB", "displayAssetHash": metadata["tiles"][0]["sha256"],
        "coverageThreshold": {"value": 145, "units": "dB", "comparison": "LTE"},
    }
    payload = {
        "protocolVersion": "1.0", "directiveId": "dir-test", "directive": directive,
        "utterance": "fixture", "selection": [selection], "parameters": {},
        "requestedMode": mode, "idempotencyKey": f"fixture:{directive}:{mode}",
    }
    if directive == "reclassify.coverage-threshold":
        payload["parameters"] = {"threshold": 135, "units": "dB", "comparison": "LTE"}
    return payload


class _GraphEngine:
    def __init__(self):
        self.nodes = {"burst-a": {"id": "burst-a", "kind": "network_burst",
                                   "position": [37.8, -122.4, 100], "timestamp": 100.0}}
        self.edges = {"edge-a": {"id": "edge-a", "kind": "burst",
                                  "nodes": ["burst-a", "peer-b"], "timestamp": 100.4}}


class _Observations:
    def query(self, **kwargs):
        return [{"evidence_id": "rf-measured-1", "observed_at": 100.0,
                 "peak_frequency_hz": 900_000_000}]


def correlation_request(*, mode="preview"):
    payload = request(directive="correlate.rf-cell-graph", mode=mode)
    revision = GraphSelectionResolver(_GraphEngine()).revision()
    payload["selection"].append({"kind": "graph-node", "entityId": "burst-a",
                                 "graphRevision": revision, "position": [37.8, -122.4, 100]})
    return payload


def graph_request(directive, *, mode="execute", selection=None, parameters=None):
    return {"protocolVersion": "1.0", "directiveId": "dir-graph", "directive": directive,
            "utterance": "fixture", "selection": selection or [], "parameters": parameters or {},
            "requestedMode": mode, "idempotencyKey": f"fixture:{directive}:{mode}"}


class GraphOpsDirectorTests(unittest.TestCase):
    def test_resolver_reads_verified_float64_authority_not_display_claim(self):
        evidence = RFCellEvidenceResolver().resolve(request()["selection"][0])
        expected = struct.unpack("<d", (DATASET / "path-loss.float64le").read_bytes()[:8])[0]
        self.assertAlmostEqual(evidence["authoritativeValue"], expected)
        self.assertEqual(evidence["displayValue"], 9999.0)
        self.assertAlmostEqual(evidence["displayDelta"], 9999.0 - expected)

    def test_resolver_rejects_forged_display_asset_hash(self):
        payload = request()
        payload["selection"][0]["displayAssetHash"] = "0" * 64
        with self.assertRaisesRegex(EvidenceResolutionError, "display asset hash"):
            RFCellEvidenceResolver().resolve(payload["selection"][0])

    def test_director_returns_allow_listed_reversible_effect_plan(self):
        plan = GraphOpsDirector().compile(request(), expected_mode="preview")
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(plan["evidencePosture"], "solver-backed")
        self.assertTrue(all(effect["reversible"] for effect in plan["effects"]))
        self.assertTrue(all(effect["authorityImpact"] == "none" for effect in plan["effects"]))
        self.assertEqual(plan["mutations"], [])
        self.assertIn("AUTHORITATIVE_VALUES", plan["claims"][0]["authority"])
        schema = json.loads((ROOT / "schemas" / "graphops-effect-plan-v1.schema.json").read_text())
        jsonschema.validate(plan, schema)

    def test_threshold_execution_declares_only_browser_view_mutation(self):
        plan = GraphOpsDirector().compile(
            request(directive="reclassify.coverage-threshold", mode="execute"),
            expected_mode="execute",
        )
        threshold = next(effect for effect in plan["effects"] if effect["type"] == "view.set-coverage-threshold")
        self.assertEqual(threshold["parameters"]["value"], 135.0)
        self.assertEqual(plan["mutations"][0]["target"], "browser-view")

    def test_protocol_rejects_unknown_or_mismatched_execution(self):
        with self.assertRaisesRegex(DirectiveProtocolError, "unknown"):
            validate_directive_request({**request(), "javascript": "alert(1)"})
        with self.assertRaisesRegex(DirectiveProtocolError, "requestedMode"):
            validate_directive_request(request(), expected_mode="execute")

    def test_graph_snapshot_is_bounded_and_revision_pinned(self):
        resolver = GraphSelectionResolver(_GraphEngine())
        snapshot = resolver.snapshot(node_limit=1, edge_limit=1)
        self.assertTrue(snapshot["bounded"])
        self.assertEqual(snapshot["nodeCount"], 1)
        self.assertEqual(snapshot["displayedNodeCount"], 1)
        self.assertEqual(snapshot["detectedNodeCount"], 1)
        self.assertEqual(snapshot["detectedEdgeCount"], 1)
        stale = {"kind": "graph-node", "entityId": "burst-a", "graphRevision": "stale"}
        with self.assertRaisesRegex(GraphResolutionError, "stale"):
            resolver.resolve(stale)

    def test_graph_snapshot_separates_detected_counts_from_display_limits(self):
        engine = _GraphEngine()
        engine.nodes.update({f"node-{index}": {"id": f"node-{index}"}
                             for index in range(8)})
        engine.edges.update({f"edge-{index}": {"id": f"edge-{index}",
                                                "nodes": ["burst-a", f"node-{index}"]}
                             for index in range(8)})
        snapshot = GraphSelectionResolver(engine).snapshot(node_limit=3, edge_limit=2)
        self.assertEqual(snapshot["detectedNodeCount"], 9)
        self.assertEqual(snapshot["detectedEdgeCount"], 9)
        self.assertEqual(snapshot["displayedNodeCount"], 3)
        self.assertLessEqual(snapshot["displayedEdgeCount"], 2)
        self.assertEqual(snapshot["nodeCount"], snapshot["displayedNodeCount"])
        self.assertEqual(snapshot["edgeCount"], snapshot["displayedEdgeCount"])
        self.assertEqual(snapshot["ranking"]["lens"], "ADAPTIVE_RELEVANCE")
        self.assertTrue(all(node["display"]["selectionPurpose"] for node in snapshot["nodes"]))

    def test_adaptive_ranking_pins_focus_and_preserves_purpose_mix(self):
        engine = _GraphEngine()
        engine.nodes = {f"host:{index}": {"id": f"host:{index}", "kind": "network_host",
                                                   "labels": {"ip": f"10.0.{index // 255}.{index % 255}"},
                                                   "observed_at": 100 + index}
                        for index in range(60)}
        engine.edges = {f"flow:{index}": {"id": f"flow:{index}", "kind": "network_flow",
                                                   "nodes": ["host:0", f"host:{index}"],
                                                   "observed_at": 100 + index,
                                                   "labels": {"proto": "tcp"}}
                        for index in range(1, 60)}
        snapshot = GraphSelectionResolver(engine).snapshot(
            node_limit=20, edge_limit=30, focus_id="host:59")
        self.assertEqual(snapshot["nodes"][0]["id"], "host:59")
        self.assertEqual(snapshot["nodes"][0]["display"]["selectionPurpose"],
                         "SELECTED_CONTEXT")
        purposes = snapshot["ranking"]["purposeCounts"]
        self.assertGreater(purposes["MOST_ACTIVE"], 0)
        self.assertGreater(purposes["NEW_ARRIVAL"], 0)
        self.assertGreater(purposes["NETWORK_DIVERSITY"], 0)
        self.assertGreater(purposes["STABLE_CONTEXT"], 0)

    def test_recent_rendered_snapshot_remains_selectable_while_live_graph_advances(self):
        engine = _GraphEngine()
        snapshot = GraphSelectionResolver(engine).snapshot(node_limit=10, edge_limit=10)
        engine.nodes["later"] = {"id": "later", "kind": "network_burst", "timestamp": 101.0}
        resolved = GraphSelectionResolver(engine).resolve({
            "kind": "graph-node", "entityId": "burst-a",
            "graphRevision": snapshot["graphRevision"],
        })
        self.assertEqual(resolved["graphRevision"], snapshot["graphRevision"])
        self.assertEqual(resolved["node"]["id"], "burst-a")

    def test_generated_graph_nodes_remain_synthetic(self):
        engine = _GraphEngine()
        engine.nodes["burst-a"]["metadata"] = {"source": "test_generator"}
        node = GraphSelectionResolver(engine).snapshot()["nodes"][0]
        self.assertEqual(node["evidenceClass"], "SYNTHETIC")

    def test_correlation_preview_exposes_dsl_without_claiming_execution(self):
        plan = GraphOpsDirector(engine=_GraphEngine()).compile(correlation_request(), expected_mode="preview")
        self.assertEqual(plan["status"], "partially-completed")
        self.assertFalse(plan["queries"][0]["executed"])
        self.assertIn("RF_CORRELATE", "\n".join(plan["queries"][0]["dsl"]))
        self.assertEqual(plan["refusals"][0]["code"], "TEMPORAL_EVIDENCE_ABSENT")

    def test_correlation_execute_uses_measured_rf_and_labels_inference(self):
        plan = GraphOpsDirector(engine=_GraphEngine(), rf_observation_provider=_Observations()).compile(
            correlation_request(mode="execute"), expected_mode="execute")
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(plan["queries"][0]["matchCount"], 1)
        fiber = next(effect for effect in plan["effects"] if effect["type"] == "view.show-correlation-fibers")
        self.assertEqual(fiber["parameters"]["findingClass"], "INFERRED")
        self.assertIn("not evidence of causality", fiber["parameters"]["caveat"])

    def test_causal_world_comparison_withholds_verdict_and_carries_falsifiers(self):
        payload = correlation_request(mode="execute")
        payload["directive"] = "compare.causal-worlds"
        payload["idempotencyKey"] = "fixture:compare.causal-worlds:execute"
        payload["selection"].extend([
            {"kind": "time-pin", "timestamp": 99, "clockId": "UTC", "uncertaintyMilliseconds": 2},
            {"kind": "time-pin", "timestamp": 101, "clockId": "UTC", "uncertaintyMilliseconds": 2},
        ])
        plan = GraphOpsDirector(engine=_GraphEngine(), rf_observation_provider=_Observations()).compile(
            payload, expected_mode="execute")
        effect = next(item for item in plan["effects"] if item["type"] == "view.show-causal-worlds")
        self.assertEqual(4, len(effect["parameters"]["worlds"]))
        self.assertEqual("CAUSAL_VERDICT_WITHHELD", plan["claims"][0]["authority"])
        self.assertEqual("COUNTERFACTUAL", effect["parameters"]["worlds"][0]["evidenceClass"])
        self.assertTrue(all(world["falsifier"] for world in effect["parameters"]["worlds"]))
        self.assertGreaterEqual(plan["queries"][0]["matchCount"], 1)

    def test_graph_edge_selection_is_revision_pinned(self):
        resolver = GraphSelectionResolver(_GraphEngine())
        resolved = resolver.resolve({"kind": "graph-edge", "entityId": "edge-a",
                                     "graphRevision": resolver.revision()})
        self.assertEqual(resolved["edge"]["id"], "edge-a")
        self.assertEqual(resolved["selectionKind"], "graph-edge")

    def test_graph_delta_uses_retained_immutable_snapshots(self):
        engine = _GraphEngine()
        engine.nodes["history-seed"] = {"id": "history-seed", "kind": "host", "timestamp": 98.0}
        with patch("graphops_graph_resolver.time.time", return_value=99.0):
            GraphSelectionResolver(engine).snapshot()
        engine.nodes["later"] = {"id": "later", "kind": "event", "timestamp": 100.5}
        engine.edges["later-edge"] = {"id": "later-edge", "kind": "flow",
                                      "nodes": ["burst-a", "later"], "timestamp": 100.5}
        with patch("graphops_graph_resolver.time.time", return_value=101.0):
            GraphSelectionResolver(engine).snapshot()
        payload = graph_request("compare.graph-delta", selection=[
            {"kind": "time-pin", "timestamp": 99, "clockId": "UTC"},
            {"kind": "time-pin", "timestamp": 101, "clockId": "UTC"},
        ], parameters={"limit": 100})
        with patch("graphops_graph_resolver.time.time", return_value=101.0):
            plan = GraphOpsDirector(engine=engine).compile(payload, expected_mode="execute")
        self.assertIn("GRAPH_DELTA", "\n".join(plan["queries"][0]["dsl"]))
        delta = next(effect for effect in plan["effects"] if effect["type"] == "view.show-graph-delta")
        self.assertEqual("RETAINED_IMMUTABLE_SNAPSHOT_DIFF",
                         delta["parameters"]["delta"]["temporalSemantics"])
        self.assertEqual(["later"], [item["id"] for item in delta["parameters"]["delta"]["addedNodes"]])
        self.assertFalse(delta["parameters"]["delta"]["windowCoverage"]["clamped"])

    def test_provenance_and_contradictions_are_bounded_read_only_plans(self):
        engine = _GraphEngine()
        engine.edges["edge-a"]["metadata"] = {"source": "pcap:fixture"}
        engine.edges["edge-c"] = {"id": "edge-c", "kind": "contradicts",
                                   "nodes": ["burst-a", "claim-b"], "timestamp": 100.5}
        revision = GraphSelectionResolver(engine).revision()
        selection = [{"kind": "graph-node", "entityId": "burst-a", "graphRevision": revision}]
        provenance = GraphOpsDirector(engine=engine).compile(graph_request(
            "trace.provenance-impact", selection=selection, parameters={"depth": 2, "limit": 50}),
            expected_mode="execute")
        self.assertEqual(provenance["mutations"], [])
        self.assertEqual(provenance["supportingEvidence"][0]["path"]["sources"][0]["source"], "pcap:fixture")
        contradictions = GraphOpsDirector(engine=engine).compile(graph_request(
            "expose.contradictions", selection=selection, parameters={"limit": 50}),
            expected_mode="execute")
        self.assertEqual(len(contradictions["contradictingEvidence"]), 1)
        self.assertEqual(contradictions["contradictingEvidence"][0]["findingClass"], "CONTRADICTION")


if __name__ == "__main__":
    unittest.main()
