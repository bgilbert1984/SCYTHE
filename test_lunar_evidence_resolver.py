import unittest

from graphops_director import GraphOpsDirector
from lunar_evidence_resolver import LunarEvidenceError, LunarEvidenceResolver


def selection():
    return {"kind": "lunar-location", "datasetId": "lunar-south-pole-reference-m0",
            "locationId": "moon:-89.0000:0.0000", "celestialBody": "MOON",
            "referenceFrame": "MOON_ME_DE421", "longitudeDegrees": 0.0,
            "latitudeDegrees": -89.0, "heightMeters": 0.0,
            "spatialAuthority": "REFERENCE_ELLIPSOID_ONLY"}


class LunarEvidenceTests(unittest.TestCase):
    def test_resolver_verifies_references_without_inventing_terrain(self):
        result = LunarEvidenceResolver().resolve(selection())
        self.assertEqual(result["terrainAuthority"], "ABSENT_M0")
        self.assertIsNone(result["elevationMeters"])
        self.assertEqual(len(result["artifacts"]), 3)

    def test_wrong_body_or_frame_is_rejected(self):
        with self.assertRaisesRegex(LunarEvidenceError, "body or reference frame"):
            LunarEvidenceResolver().resolve({**selection(), "referenceFrame": "EPSG:4326"})

    def test_director_emits_sparse_lunar_prism(self):
        request = {"protocolVersion": "1.0", "directiveId": "lunar-test",
                   "directive": "explain.lunar-location", "utterance": "Explain this location",
                   "selection": [selection()], "parameters": {}, "requestedMode": "preview",
                   "idempotencyKey": "lunar-test:preview"}
        plan = GraphOpsDirector().compile(request, expected_mode="preview")
        self.assertEqual(plan["status"], "partially-completed")
        self.assertEqual(plan["evidencePosture"], "sparse")
        self.assertEqual(plan["effects"][0]["parameters"]["terrainAuthority"], "ABSENT_M0")
        self.assertTrue(plan["refusals"])


if __name__ == "__main__":
    unittest.main()
