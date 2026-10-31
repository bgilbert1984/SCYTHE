"""#217: tools/list does not advertise a tool the engine cannot run; include_unavailable shows it with
the reason.  Availability (engine capability) is separate from authorization (policy)."""
import unittest

import mcp_registry
from hypergraph_engine import HypergraphEngine
from mcp_server import MCPHandler


def _list(handler, **params):
    return handler.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": params})["result"]["tools"]


class _Engine:
    """Implements exactly the scope capabilities and nothing else."""
    degree = {}

    def create_scope(self, *a, **k): pass
    def destroy_scope(self, *a, **k): pass
    def list_scopes(self): return []
    def scrub_scope_before_time(self, *a, **k): return 0
    def set_scope_filter(self, *a, **k): pass
    def clear_scope_cache(self): pass


class DiscoveryTest(unittest.TestCase):
    def test_default_listing_omits_unavailable_tools(self):
        handler = MCPHandler(HypergraphEngine())
        names = {t["name"] for t in _list(handler)}
        engine = HypergraphEngine()
        for tool, requires in mcp_registry.TOOL_REQUIRES.items():
            if mcp_registry.missing_capabilities(engine, requires):
                self.assertNotIn(tool, names, tool)
            else:
                self.assertIn(tool, names, tool)        # get_edge_by_id: the real engine has get_edge
        self.assertNotIn("subscribe_scope", names)
        for tool in ("decay_now", "get_decay_config", "query_hot_entities", "export_graph_snapshot"):
            self.assertIn(tool, names, tool)

    def test_include_unavailable_lists_everything_with_the_reason(self):
        handler = MCPHandler(HypergraphEngine())
        listed = {t["name"]: t for t in _list(handler, include_unavailable=True)}
        self.assertEqual(set(listed), set(handler._tools))
        meta = listed["subscribe_scope"]["_meta"]["scythe/availability"]
        self.assertEqual(meta, {"available": False, "missing_capabilities": ["scope_management"]})
        self.assertEqual(listed["decay_now"]["_meta"]["scythe/availability"],
                         {"available": True, "missing_capabilities": []})

    def test_a_capable_engine_advertises_its_tools(self):
        handler = MCPHandler(_Engine())
        names = {t["name"] for t in _list(handler)}
        self.assertTrue({"subscribe_scope", "list_active_scopes", "clear_scope_cache"} <= names)
        self.assertNotIn("run_tak_ml", names)

    def test_an_unavailable_tool_is_still_registered_and_still_reports_unsupported(self):
        engine = HypergraphEngine()
        handler = MCPHandler(engine)
        self.assertIn("get_entity_neighbors", handler._tools)
        response = handler.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                   "params": {"name": "get_entity_neighbors", "arguments": {"entity_id": "n1"}}})
        self.assertEqual(response["error"]["data"]["kind"], "unsupported_capability")

    def test_declared_requirements_match_what_the_handlers_enforce(self):
        """Every tool the table marks unavailable on a bare engine raises unsupported for the same capability."""
        from test_mcp_capability_contract import UNSUPPORTED
        engine = HypergraphEngine()
        reg = mcp_registry.build_registry(engine)["__registry__"]
        for name, params in UNSUPPORTED.items():
            with self.subTest(tool=name):
                declared = mcp_registry.missing_capabilities(engine, mcp_registry.TOOL_REQUIRES[name])
                with self.assertRaises(mcp_registry.CapabilityUnsupported) as cm:
                    reg.execute(engine, name, params, agent_mode="admin")
                self.assertEqual([cm.exception.capability], declared)

    def test_every_table_entry_names_a_registered_tool(self):
        reg = mcp_registry.build_registry(HypergraphEngine())["__registry__"]
        registered = {t.name for t in reg.list_tools()}
        self.assertLessEqual(set(mcp_registry.TOOL_REQUIRES), registered)


if __name__ == "__main__":
    unittest.main()
