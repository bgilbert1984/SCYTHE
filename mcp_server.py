"""
mcp_server.py — MCP (Model Context Protocol) Server for RF_SCYTHE.

Simplified, cleaned and structurally-correct MCP handler.
Loads a declarative registry from `mcp_registry.build_registry(engine)` if present,
otherwise falls back to built-in tool registration.

Provides JSON-RPC 2.0 handlers: initialize, tools/list, tools/call,
resources/list, resources/read and health endpoints for integration with Flask.
"""
from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from typing import Any, Dict

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_SERVER_NAME = "RF_SCYTHE"
MCP_SERVER_VERSION = "1.3.0"


class ToolDef:
    def __init__(self, name, description, input_schema, fn):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.fn = fn

    def to_mcp(self):
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


class ResourceDef:
    def __init__(self, uri, name, description, mime_type, fn):
        self.uri = uri
        self.name = name
        self.description = description
        self.mime_type = mime_type
        self.fn = fn

    def to_mcp(self):
        return {"uri": self.uri, "name": self.name, "description": self.description, "mimeType": self.mime_type}


class MCPHandler:
    """Stateless MCP JSON-RPC 2.0 handler.

    The handler is small, deterministic and avoids runtime shims — all
    helper methods are proper class methods so the control surface is
    stable and auditable.

    Can operate in two modes:
    - Standalone: direct registry-based execution (production)
    - Orchestrated: via AliasMCPOrchestrator + dual agents (graduated autonomy)
    """

    def __init__(self, engine, use_orchestrator: bool = False):
        self.engine = engine
        self._tools: Dict[str, ToolDef] = {}
        self._resources: Dict[str, ResourceDef] = {}
        self._orchestrator = None

        # Try to instantiate orchestrator if requested
        if use_orchestrator:
            try:
                from mcp_orchestrator import AliasMCPOrchestrator
                self._orchestrator = AliasMCPOrchestrator(engine)
                logger.info("[mcp] Orchestrator initialized (dual agent mode)")
            except Exception as e:
                logger.warning("[mcp] Orchestrator initialization failed: %s", e)

        # Prefer declarative registry when available
        try:
            from mcp_registry import build_registry
            built = build_registry(self.engine)
            if isinstance(built, dict):
                for k, v in built.items():
                    if k == '__registry__':
                        self._registry = v
                        continue
                    self._tools[k] = v
                logger.info("[mcp] loaded %d tools from mcp_registry", len(self._tools))
        except Exception:
            pass

        if not self._tools:
            try:
                self._register_tools()
            except Exception as e:
                logger.warning("[mcp] _register_tools failed: %s", e)

        try:
            self._register_resources()
        except Exception as e:
            logger.warning("[mcp] _register_resources failed: %s", e)

    @staticmethod
    def _rpc_ok(req_id, result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _rpc_error(req_id, code, message):
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def handle(self, request: Dict[str, Any]):
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {}) or {}

        try:
            if method == "initialize":
                return self._rpc_ok(req_id, self._handle_initialize(params))
            elif method == "tools/list":
                return self._rpc_ok(req_id, self._handle_tools_list())
            elif method == "tools/schema":
                return self._rpc_ok(req_id, self._handle_tools_schema())
            elif method == "tools/metrics":
                return self._rpc_ok(req_id, self._handle_tools_metrics())
            elif method == "tools/call":
                return self._rpc_ok(req_id, self._handle_tools_call(params))
            elif method == "resources/list":
                return self._rpc_ok(req_id, self._handle_resources_list())
            elif method == "resources/read":
                return self._rpc_ok(req_id, self._handle_resources_read(params))
            # ──────────────────────────────────────────────────────────
            # Orchestrator endpoints (graduated autonomy)
            # ──────────────────────────────────────────────────────────
            elif method == "orchestrate/propose":
                return self._rpc_ok(req_id, self._handle_propose(params))
            elif method == "orchestrate/decide":
                return self._rpc_ok(req_id, self._handle_decide(params))
            elif method == "orchestrate/execute":
                return self._rpc_ok(req_id, self._handle_execute(params))
            elif method == "orchestrate/status":
                return self._rpc_ok(req_id, self._handle_orchestrator_status(params))
            elif method == "orchestrate/phase":
                return self._rpc_ok(req_id, self._handle_set_phase(params))
            elif method == "orchestrate/connect_stream":
                return self._rpc_ok(req_id, self._handle_connect_stream(params))
            elif method == "ping":
                return self._rpc_ok(req_id, {})
            else:
                return self._rpc_error(req_id, -32601, f"Method not found: {method}")
        except Exception as e:
            logger.exception("[mcp] handler error")
            return self._rpc_error(req_id, -32603, str(e))

    # -------------------- MCP method handlers --------------------
    def _handle_initialize(self, params: Dict[str, Any]):
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}},
            "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
        }

    def _handle_tools_list(self):
        return {"tools": [t.to_mcp() for t in self._tools.values()]}

    def _handle_tools_schema(self):
        """Return full schema for all tools (for LLM introspection)."""
        schema = {}
        if hasattr(self, '_registry'):
            schema = self._registry.get_schema()
        else:
            # Fallback: build minimal schema from registered tools
            for name, tool in self._tools.items():
                schema[name] = {
                    "parameters": getattr(tool, 'input_schema', {}),
                    "returns": {"type": "object"},  # Unknown return type
                    "mutates_state": getattr(tool, 'side_effect', False),
                    "required_mode": "mutate" if getattr(tool, 'side_effect', False) else "observe",
                }
        return {"tools": schema}

    # -------------------------------------------------------------
    # Orchestrator helpers (custom stream connectivity)
    # -------------------------------------------------------------
    def _handle_connect_stream(self, params: Dict[str, Any]):
        """Establish a connection to a remote event stream.

        Parameters:
        - endpoint: URL of the remote feed (WebSocket or HTTP)
        - auth_token: optional bearer token for auth
        - type: string identifying the payload (e.g. "suricata_eve")

        Returns a simple acknowledgement.  The actual work happens in the
        background via :pydata:`stream_manager.remote_stream_manager`.
        """
        endpoint = params.get("endpoint")
        token = params.get("auth_token")
        stype = params.get("type")

        if not endpoint:
            raise ValueError("endpoint parameter is required")

        # type checking can be expanded later; currently we only support
        # websocket streams, the manager will log others as errors.
        try:
            from stream_manager import remote_stream_manager
        except ImportError:
            raise RuntimeError("stream_manager module unavailable")

        remote_stream_manager.connect(endpoint, token)
        return {"connected": True, "endpoint": endpoint, "type": stype}

    def _handle_tools_call(self, params: Dict[str, Any]):
        name = params.get("name", "")
        arguments = params.get("arguments", {}) or {}
        agent_mode = params.get("agent_mode", "observe")
        mutation_budget = params.get("mutation_budget", None)

        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")

        # Prefer registry.execute when the registry knows the tool.
        # Fall back to direct ToolDef.fn for tools registered outside the
        # registry (e.g. graphops_* tools registered by register_graphops_tools).
        if hasattr(self, '_registry') and name in self._registry._tools:
            return self._registry.execute(
                self.engine,
                name,
                arguments,
                agent_mode=agent_mode,
                mutation_budget=mutation_budget,
            )

        if hasattr(tool, 'fn'):
            result = tool.fn(arguments)
        elif callable(tool):
            result = tool(arguments)
        else:
            raise ValueError("Invalid tool object for %s" % name)

        if isinstance(result, (dict, list)):
            content = [{"type": "text", "text": json.dumps(result, default=str, indent=2)}]
        else:
            content = [{"type": "text", "text": str(result)}]

        return {"content": content, "isError": False}

    def _handle_resources_list(self):
        return {"resources": [r.to_mcp() for r in self._resources.values()]}

    def _handle_tools_metrics(self):
        """Return registry metrics (invocations, rate-limit hits, etc)."""
        if hasattr(self, '_registry'):
            return {"metrics": self._registry.get_metrics()}
        return {"metrics": {}}

    def _handle_resources_read(self, params: Dict[str, Any]):
        uri = params.get("uri", "")
        resource = self._resources.get(uri)
        if resource is None:
            raise ValueError(f"Unknown resource: {uri}")
        text = resource.fn()
        return {"contents": [{"uri": uri, "mimeType": resource.mime_type, "text": text}]}

    # ────────────────────────────────────────────────────────────────
    # Orchestrator handlers (graduated autonomy)
    # ────────────────────────────────────────────────────────────────

    def _handle_propose(self, params: Dict[str, Any]):
        """Analyst proposes an action. Returns proposal with approval status."""
        if not self._orchestrator:
            raise RuntimeError("Orchestrator not initialized")

        tool_name = params.get("tool_name", "")
        tool_params = params.get("params", {})
        confidence = params.get("confidence", 0.75)
        justification = params.get("justification", "")
        agent_id = params.get("agent_id", "analyst")

        return self._orchestrator.propose_action(
            tool_name=tool_name,
            params=tool_params,
            confidence=confidence,
            justification=justification,
            agent_id=agent_id,
        )

    def _handle_decide(self, params: Dict[str, Any]):
        """Check approval status of a proposal."""
        if not self._orchestrator:
            raise RuntimeError("Orchestrator not initialized")

        proposal_id = params.get("proposal_id", "")
        return self._orchestrator.check_proposal(proposal_id)

    def _handle_execute(self, params: Dict[str, Any]):
        """Executor executes an approved proposal."""
        if not self._orchestrator:
            raise RuntimeError("Orchestrator not initialized")

        proposal_id = params.get("proposal_id", "")
        return self._orchestrator.execute_proposal(proposal_id)

    def _handle_orchestrator_status(self, params: Dict[str, Any]):
        """Return full orchestrator and organism status."""
        if not self._orchestrator:
            return {"ok": False, "error": "Orchestrator not initialized"}

        status = self._orchestrator.get_organism_status()

        # Auto-demotion check
        status["auto_demote_triggered"] = self._orchestrator.check_should_auto_demote()

        return status

    def _handle_set_phase(self, params: Dict[str, Any]):
        """Set autonomy phase (0=observe, 1=shadow, 2=limited, 3=adaptive)."""
        if not self._orchestrator:
            raise RuntimeError("Orchestrator not initialized")

        phase = params.get("phase", 0)
        dry_run = params.get("dry_run", False)
        mutation_budget = params.get("mutation_budget", 3)

        return self._orchestrator.set_phase(
            phase=phase,
            dry_run=dry_run,
            mutation_budget=mutation_budget,
        )

    # -------------------- Default tool registry --------------------
    def _register_tools(self):
        # Minimal safe toolset — non-destructive by default where possible
        self._tools["graph_snapshot"] = ToolDef(
            name="graph_snapshot",
            description="Return full MCP envelope",
            input_schema={"type": "object", "properties": {"window_minutes": {"type": "integer"}}},
            fn=self._tool_graph_snapshot,
        )

        self._tools["graph_summary"] = ToolDef(
            name="graph_summary",
            description="Compact text summary",
            input_schema={"type": "object", "properties": {"window_minutes": {"type": "integer"}}},
            fn=self._tool_graph_summary,
        )

    def _register_resources(self):
        self._resources["graph://snapshot"] = ResourceDef(
            uri="graph://snapshot",
            name="Graph Snapshot",
            description="Full MCP envelope",
            mime_type="application/json",
            fn=self._resource_snapshot,
        )

    # -------------------- Tool implementations --------------------
    def _tool_graph_snapshot(self, window_minutes: int = 15):
        try:
            from mcp_context import MCPBuilder
            builder = MCPBuilder(self.engine)
            return builder.build(window_minutes=window_minutes)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _tool_graph_summary(self, window_minutes: int = 15):
        try:
            from mcp_context import MCPBuilder
            builder = MCPBuilder(self.engine)
            return builder.build_compact(window_minutes=window_minutes)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _resource_snapshot(self):
        return json.dumps(self._tool_graph_snapshot(), default=str)


# -------------------- Register Flask routes --------------------
def register_mcp_routes(app, engine, use_orchestrator: bool = False):
    try:
        handler = MCPHandler(engine, use_orchestrator=use_orchestrator)
    except Exception as exc:
        logger.warning("[mcp] MCPHandler instantiation failed: %s", exc)
        handler = type("DummyHandler", (), {"_tools": {}, "_resources": {}})()

    try:
        from graphops_copilot import register_graphops_tools
        register_graphops_tools(engine, handler)
        logger.info("[mcp] GraphOps Copilot tools registered")
    except Exception as exc:
        logger.warning("[mcp] GraphOps Copilot registration failed: %s", exc)

    try:
        from graphops_autopilot import register_autopilot_tools
        register_autopilot_tools(engine, handler)
        logger.info("[mcp] GraphOps Autopilot tools registered")
    except Exception as exc:
        logger.warning("[mcp] GraphOps Autopilot registration failed: %s", exc)

    from flask import request, jsonify

    @app.route('/mcp', methods=['POST'])
    def mcp_jsonrpc():
        body = request.get_json(silent=True)
        if not body:
            return jsonify({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}), 400
        if isinstance(body, list):
            responses = [handler.handle(req) for req in body]
            return jsonify(responses)
        return jsonify(handler.handle(body))

    @app.route('/mcp', methods=['GET'])
    def mcp_info():
        return jsonify({
            "name": MCP_SERVER_NAME,
            "version": MCP_SERVER_VERSION,
            "protocol_version": MCP_PROTOCOL_VERSION,
            "tools": len(getattr(handler, '_tools', {})),
            "resources": len(getattr(handler, '_resources', {})),
            "orchestrator": use_orchestrator,
        })

    mode_desc = "orchestrator (graduated autonomy)" if use_orchestrator else "standalone"
    logger.info(
        "[mcp] Registered MCP JSON-RPC endpoint at /mcp (%d tools, %d resources) — %s",
        len(getattr(handler, '_tools', {})),
        len(getattr(handler, '_resources', {})),
        mode_desc,
    )
    return handler


# -------------------- Standalone server --------------------
if __name__ == '__main__':
    import argparse
    from flask import Flask

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=3001)
    parser.add_argument('--host', type=str, default='0.0.0.0')
    args = parser.parse_args()

    try:
        from hypergraph_engine import HypergraphEngine
        engine = HypergraphEngine()
    except Exception:
        engine = type('EmptyEngine', (), {'nodes': {}, 'edges': {}, 'degree': {}})()

    app = Flask(__name__)
    register_mcp_routes(app, engine)
    print(f"RF_SCYTHE MCP Server on http://{args.host}:{args.port}/mcp")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
