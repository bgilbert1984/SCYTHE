"""Pins the assumptions behind the READ_ONLY declarations on the GraphOps tools
(graphops_dsl_exec, graphops_investigate, graphops_directive, graphops_suggest).

The declarations were audited from the code; these tests fail when the audited
facts stop being true, so a change that widens what a tool can do forces a new audit
instead of leaving a stale declaration behind.
"""
import inspect
import logging
import re
import unittest
from unittest import mock

from graphops_copilot import InvestigativeDSLExecutor, register_graphops_tools
from hypergraph_engine import HypergraphEngine
from mcp_server import MCPHandler

# Every verb InvestigativeDSLExecutor._dispatch handles.  A new verb must be audited
# for side effects and added here deliberately.
AUDITED_VERBS = {
    "FOCUS", "EXPAND", "TRACE", "FILTER", "BEHAVIOR_QUERY", "WINDOW", "ANALYZE",
    "CLUSTER", "VECTOR_SEARCH", "CLUSTER_SIMILAR", "TEMPORAL_ENTROPY",
    "STITCH_IDENTITIES", "COMPUTE", "GRAPH_DELTA", "RF_CORRELATE", "BSG_MAP",
    "SUMMARIZE", "ASSESS",
}

# The only engine attributes the verbs may touch.  All are reads.
READ_ACCESSORS = {"nodes", "edges", "edges_for_node", "get_node", "degree",
                  "nodes_with_label", "view"}

PLAN = [
    "FOCUS all_nodes", "EXPAND depth=1", "TRACE a", "FILTER weight>0",
    "BEHAVIOR_QUERY utility=true top=5", "WINDOW 5m", "ANALYZE degree",
    "CLUSTER timing", 'VECTOR_SEARCH "beacon"', "CLUSTER_SIMILAR",
    "TEMPORAL_ENTROPY window=5m top=5", "STITCH_IDENTITIES field=tls_ja3",
    "STITCH_IDENTITIES field=embedding", "COMPUTE k_core k=2",
    "GRAPH_DELTA slices=2 window=5m", "RF_CORRELATE", "BSG_MAP", "SUMMARIZE", "ASSESS",
]


class _RecordingEngine:
    """Delegates to a real engine and records every attribute read through it."""

    def __init__(self, engine):
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "accessed", set())

    def __getattr__(self, name):
        self.accessed.add(name)
        return getattr(self._engine, name)

    def __setattr__(self, name, value):
        raise AssertionError(f"verb wrote engine.{name}")


def _seeded_engine():
    engine = HypergraphEngine()
    for nid, ip in (("a", "10.0.0.1"), ("b", "10.0.0.2"), ("c", "10.0.0.3")):
        engine.add_node({"id": nid, "kind": "host", "labels": {"ip": ip, "tls_ja3": "j1"}})
    for eid, nodes in (("e1", ["a", "b"]), ("e2", ["b", "c"])):
        engine.add_edge({"id": eid, "kind": "flow", "nodes": nodes, "weight": 1.0,
                         "timestamp": 1000.0})
    return engine


def _state(engine):
    return (sorted(engine.nodes), sorted((k, e.weight) for k, e in engine.edges.items()))


class DslExecIsReadOnlyTest(unittest.TestCase):
    def test_the_verb_set_is_the_audited_one(self):
        source = inspect.getsource(InvestigativeDSLExecutor._dispatch)
        self.assertEqual(set(re.findall(r'verb == "([A-Z_]+)"', source)), AUDITED_VERBS)

    def test_every_verb_only_reads_the_engine(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        engine = _seeded_engine()
        before = _state(engine)
        recorder = _RecordingEngine(engine)
        executor = InvestigativeDSLExecutor(engine=recorder)
        result = executor.run(PLAN)
        dispatched = {step["verb"].split()[0].upper() for step in result["steps"]
                      if "unknown verb" not in str(step["result"].get("warning", ""))}
        self.assertEqual(dispatched, AUDITED_VERBS)
        self.assertLessEqual(recorder.accessed, READ_ACCESSORS)
        self.assertEqual(_state(engine), before)


class InvestigateAndSuggestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.addClassCleanup(logging.disable, logging.NOTSET)
        cls.engine = _seeded_engine()
        cls.handler = MCPHandler(cls.engine, use_orchestrator=True)
        register_graphops_tools(cls.engine, cls.handler)

    def test_the_registered_agent_cannot_ground_through_the_sensor_stream(self):
        # Reflexive grounding calls sensor_stream_tool, which injects into the graph.
        self.assertIs(self.handler._graphops_agent._allow_sensor_mutation, False)

    def test_the_four_tools_are_declared_read_only(self):
        for name in ("graphops_investigate", "graphops_dsl_exec",
                     "graphops_directive", "graphops_suggest"):
            tool = self.handler._tools[name]
            self.assertEqual((tool.effect, tool.effect_declared, tool.mutates_state),
                             ("READ_ONLY", True, False), name)

    def test_suggest_with_auto_execute_leaves_the_graph_unchanged(self):
        before = _state(self.engine)
        self.handler._tools["graphops_suggest"].fn({"top_n": 5, "auto_execute": True})
        self.assertEqual(_state(self.engine), before)

    def test_dsl_exec_leaves_the_graph_unchanged_and_refuses_sensor_stream(self):
        before = _state(self.engine)
        tool = self.handler._tools["graphops_dsl_exec"]
        tool.fn({"plan": ["FOCUS all_nodes", "ASSESS"]})
        refused = tool.fn({"plan": ["SENSOR_STREAM window=5"]})
        self.assertIn("SENSOR_STREAM", refused["error"])
        self.assertEqual(_state(self.engine), before)


class DirectiveIsEngineLessTest(unittest.TestCase):
    def test_the_tool_builds_the_director_without_an_engine(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        engine = HypergraphEngine()
        handler = MCPHandler(engine, use_orchestrator=True)
        register_graphops_tools(engine, handler)
        with mock.patch("graphops_director.GraphOpsDirector") as director:
            handler._tools["graphops_directive"].fn({})
        director.assert_called_once_with()

    def test_an_engine_less_director_cannot_reach_the_graph_resolver(self):
        from graphops_director import GraphOpsDirector
        from graphops_graph_resolver import GraphResolutionError
        with self.assertRaises(GraphResolutionError):
            GraphOpsDirector()._graph_resolver()


if __name__ == "__main__":
    unittest.main()
