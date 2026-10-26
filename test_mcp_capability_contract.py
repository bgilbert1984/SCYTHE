"""#217: a registry tool reports a missing engine capability as unsupported, never as an empty or
successful result, and a misspelled parameter is rejected rather than defaulted."""
import unittest

import mcp_registry
from hypergraph_engine import HypergraphEngine

# tool -> minimal valid params, for every handler that needs an engine method HypergraphEngine lacks
UNSUPPORTED = {
    "get_entity_neighbors": {"entity_id": "n1"},
    "subscribe_scope": {},
    "unsubscribe_scope": {"scope_id": "s1"},
    "scrub_scope_time": {"scope_id": "s1", "timestamp": 1},
    "set_scope_filter": {"scope_id": "s1", "min_weight": 0.5},
    "list_active_scopes": {},
    "set_decay_lambda": {"lambda": 0.5},
    "reload_rules": {},
    "clear_scope_cache": {},
    "reinforce_edge": {"src": "a", "dst": "b"},
    "prune_below_weight": {"threshold": 0.2},
}


def _registry(engine):
    return mcp_registry.build_registry(engine)["__registry__"]


class UnsupportedCapabilityTest(unittest.TestCase):
    def test_bare_engine_lacks_every_method_these_tests_rely_on(self):
        engine = HypergraphEngine()
        for method in ("get_neighbors", "create_scope", "destroy_scope", "list_scopes", "set_decay_lambda",
                       "reload_rules", "clear_scope_cache", "reinforce_edge", "prune_below_weight",
                       "scrub_scope_before_time", "set_scope_filter"):
            self.assertFalse(hasattr(engine, method), method)

    def test_each_tool_raises_unsupported_instead_of_a_default(self):
        engine = HypergraphEngine()
        reg = _registry(engine)
        for name, params in UNSUPPORTED.items():
            with self.subTest(tool=name):
                with self.assertRaises(mcp_registry.CapabilityUnsupported) as cm:
                    reg.execute(engine, name, params, agent_mode="admin")
                self.assertEqual(cm.exception.tool, name)
                self.assertTrue(cm.exception.capability)

    def test_set_decay_lambda_does_not_claim_the_new_value(self):
        engine = HypergraphEngine()
        with self.assertRaises(mcp_registry.CapabilityUnsupported):
            _registry(engine).execute(engine, "set_decay_lambda", {"lambda": 0.5}, agent_mode="admin")
        self.assertEqual(engine.decay_lambda, 0.0)

    def test_a_present_capability_still_executes(self):
        class Engine:
            created = None

            def create_scope(self, scope_id, kind, min_weight):
                Engine.created = (scope_id, kind, min_weight)

            def list_scopes(self):
                return [Engine.created[0]] if Engine.created else []

            def get_neighbors(self, entity_id, limit):
                return []     # supported, no matching data: this one is a legitimate empty

        engine = Engine()
        reg = _registry(engine)
        scope = reg.execute(engine, "subscribe_scope", {}, agent_mode="admin")["scope_id"]
        self.assertEqual(reg.execute(engine, "list_active_scopes", {})["scopes"], [scope])
        self.assertEqual(reg.execute(engine, "get_entity_neighbors", {"entity_id": "x"})["neighbors"], [])

    def test_unsupported_is_a_runtime_error_for_existing_callers(self):
        self.assertTrue(issubclass(mcp_registry.CapabilityUnsupported, RuntimeError))


class UnknownKeyTest(unittest.TestCase):
    def test_every_registry_tool_schema_is_closed(self):
        reg = _registry(HypergraphEngine())
        for tool in reg.list_tools():
            with self.subTest(tool=tool.name):
                self.assertIs(tool.parameters.get("additionalProperties"), False)

    def test_misspelled_key_is_rejected_not_defaulted(self):
        engine = HypergraphEngine()
        reg = _registry(engine)
        with self.assertRaises(ValueError):
            reg.execute(engine, "set_decay_lambda", {"decay_lambda": 0.5}, agent_mode="admin")
        self.assertIn("decay_lambda", reg.check_params("set_decay_lambda", {"decay_lambda": 0.5}))
        self.assertIsNone(reg.check_params("set_decay_lambda", {"lambda": 0.5}))


if __name__ == "__main__":
    unittest.main()
