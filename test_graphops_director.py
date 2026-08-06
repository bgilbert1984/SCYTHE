import json
from pathlib import Path
import struct
import unittest

import jsonschema

from graphops_director import GraphOpsDirector
from graphops_effect_schema import DirectiveProtocolError, validate_directive_request
from graphops_evidence_resolver import EvidenceResolutionError, RFCellEvidenceResolver


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


if __name__ == "__main__":
    unittest.main()
