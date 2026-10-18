"""Found while reconciling the mission_id/missionId identifier mismatch
(docs/research/scythe_mission_browser_rendering.md section 4): mission_diff_stream's live SSE
scoping in rf_scythe_api_server.py passed QueryPredicate({'missionId': mission_id}) into
SubgraphDiffGenerator.generate_diff(). QueryPredicate.matches_node()/matches_edge() (below) only
ever consult parsed['where']/parsed['in_room'] -- never a 'missionId' key -- so that predicate
matched every node/edge unconditionally, and the "mission-scoped" diff stream was not actually
scoped to the mission at all, independent of the identifier-casing question.

This is a library-level test against the real SubgraphDiffGenerator/QueryPredicate (not a copy),
proving both the bug's mechanism and the fix's mechanism (a predicate object exposing
matches_node()/matches_edge() that actually inspects label/metadata values), without needing the
Flask route's operator-session/SSE-threading machinery. mission_diff_stream's own predicate
(mission_pred, wrapped in a small adapter class) is a private closure inside that route and is
exercised indirectly via test_rf_scythe_api_server_mission_identifier.py's HTTP-level test of
mission_subgraph, which shares the identical label/metadata check shape.
"""
import unittest

from subgraph_diff import QueryPredicate, SubgraphDiffGenerator


class _FakeEngine:
    def __init__(self, nodes):
        self.nodes = nodes  # id -> plain dict (already shaped like node.to_dict())


class _FakeEventBus:
    """Minimal replay() stand-in: returns one NODE_CREATE event per node, with sequence_id
    rising from 1."""

    def __init__(self, node_ids):
        self._events = [
            {"event_type": "NODE_CREATE", "entity_id": nid, "entity_type": "node",
             "sequence_id": i + 1}
            for i, nid in enumerate(node_ids)
        ]

    def replay(self, since):
        return [e for e in self._events if e["sequence_id"] > since]


class _MatchingPredicateAdapter:
    """The fix's shape: an object exposing matches_node()/matches_edge(), backed by a function
    that actually inspects the node/edge -- same interface generate_diff() calls, same shape as
    rf_scythe_api_server.py's _MissionPredicateAdapter."""

    def __init__(self, pred_fn):
        self._pred_fn = pred_fn

    def matches_node(self, node):
        return self._pred_fn(node)

    def matches_edge(self, edge):
        return self._pred_fn(edge)


def _mission_pred(mission_id):
    def pred(x):
        labels = x.get("labels") or {}
        metadata = x.get("metadata") or {}
        return (labels.get("mission_id") == mission_id or labels.get("missionId") == mission_id
                or metadata.get("mission_id") == mission_id or metadata.get("missionId") == mission_id)
    return pred


class QueryPredicateMissionIdNoOpTests(unittest.TestCase):
    """Confirms the bug's exact mechanism: a bare {'missionId': X} dict is not a WHERE-clause
    list or a room scope, so matches_node()/matches_edge() both pass unconditionally."""

    def test_missionid_dict_is_not_consulted_by_matches_node(self):
        pred = QueryPredicate({"missionId": "RFMAP-001"})
        unrelated_node = {"id": "x", "labels": {"mission_id": "SOMETHING-ELSE"}, "metadata": {}}
        self.assertTrue(pred.matches_node(unrelated_node))  # matched despite not belonging to RFMAP-001

    def test_missionid_dict_is_not_consulted_by_matches_edge(self):
        pred = QueryPredicate({"missionId": "RFMAP-001"})
        unrelated_edge = {"id": "e", "kind": "whatever", "metadata": {"mission_id": "SOMETHING-ELSE"}}
        self.assertTrue(pred.matches_edge(unrelated_edge))  # matched despite not belonging to RFMAP-001


class GenerateDiffWithTheOldPredicateIncludesEverythingTests(unittest.TestCase):
    """Reproduces the actual bug through generate_diff(), not just QueryPredicate in isolation:
    a diff "scoped" to RFMAP-001 with the old predicate shape includes a node that belongs to a
    different mission entirely."""

    def test_old_predicate_shape_includes_an_unrelated_mission_s_node(self):
        nodes = {
            "mine": {"id": "mine", "labels": {"mission_id": "RFMAP-001"}, "metadata": {}},
            "not-mine": {"id": "not-mine", "labels": {"mission_id": "RFMAP-999"}, "metadata": {}},
        }
        engine = _FakeEngine(nodes)
        bus = _FakeEventBus(list(nodes))
        gen = SubgraphDiffGenerator(engine)
        engine.event_bus = bus
        old_predicate = QueryPredicate({"missionId": "RFMAP-001"})
        diff = gen.generate_diff("mission:RFMAP-001", old_predicate, 0, 2)
        created_ids = {n["id"] for n in diff["nodes"]["created"]}
        self.assertEqual(created_ids, {"mine", "not-mine"})  # the bug: both included


class GenerateDiffWithTheFixedAdapterScopesCorrectlyTests(unittest.TestCase):
    def test_fixed_adapter_excludes_an_unrelated_mission_s_node(self):
        nodes = {
            "mine": {"id": "mine", "labels": {"mission_id": "RFMAP-001"}, "metadata": {}},
            "not-mine": {"id": "not-mine", "labels": {"mission_id": "RFMAP-999"}, "metadata": {}},
            "legacy-mine": {"id": "legacy-mine", "labels": {"missionId": "RFMAP-001"}, "metadata": {}},
        }
        engine = _FakeEngine(nodes)
        bus = _FakeEventBus(list(nodes))
        gen = SubgraphDiffGenerator(engine)
        engine.event_bus = bus
        fixed_predicate = _MatchingPredicateAdapter(_mission_pred("RFMAP-001"))
        diff = gen.generate_diff("mission:RFMAP-001", fixed_predicate, 0, 3)
        created_ids = {n["id"] for n in diff["nodes"]["created"]}
        self.assertEqual(created_ids, {"mine", "legacy-mine"})
        self.assertNotIn("not-mine", created_ids)
        # A non-matching node is reported deleted-from-this-view, not silently dropped --
        # generate_diff's own existing behavior (not something this fix changes).
        self.assertIn("not-mine", diff["nodes"]["deleted"])


if __name__ == "__main__":
    unittest.main()
