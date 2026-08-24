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
import hmac
import os
import threading
from collections import defaultdict
from contextlib import nullcontext
from typing import Any, Dict

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_SERVER_NAME = "RF_SCYTHE"
MCP_SERVER_VERSION = "1.3.0"


class ToolDef:
    def __init__(self, name, description, input_schema, fn, mutates_state=False):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.fn = fn
        self.mutates_state = mutates_state
        self.side_effect = mutates_state

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
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")

        # Prefer registry.execute when the registry knows the tool.
        # Fall back to direct ToolDef.fn for tools registered outside the
        # registry (e.g. graphops_* tools registered by register_graphops_tools).
        if hasattr(self, '_registry') and name in self._registry._tools:
            registered = self._registry._tools[name]
            if registered.mutates_state:
                raise RuntimeError(
                    f"Direct execution of mutating tool {name} is disabled; "
                    "use orchestrate/propose, orchestrate/decide, and orchestrate/execute"
                )
            return self._registry.execute(
                self.engine,
                name,
                arguments,
                agent_mode="observe",
            )

        if getattr(tool, 'mutates_state', False) or getattr(tool, 'side_effect', False):
            raise RuntimeError(
                f"Direct execution of mutating tool {name} is disabled; use the orchestrator proposal flow"
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
        if os.getenv("MCP_ALLOW_REMOTE_PHASE_CONTROL", "").lower() not in {"1", "true", "yes"}:
            raise RuntimeError(
                "Remote autonomy phase changes are disabled; an operator must explicitly enable them"
            )

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
def register_mcp_routes(app, engine, use_orchestrator: bool = False, auth_validator=None,
                        selection_engine=None):
    graph_selection_engine = selection_engine or engine
    conversation_lock = threading.Lock()
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

    try:
        from rf_mcp import register_rf_tools
        register_rf_tools(engine, handler)
        logger.info("[mcp] RF evidence and guarded control tools registered")
    except Exception as exc:
        logger.warning("[mcp] RF tool registration failed: %s", exc)

    from flask import request, jsonify

    def _authorized():
        if auth_validator is not None:
            try:
                return bool(auth_validator())
            except Exception:
                logger.exception("[mcp] auth validator failed")
                return False
        configured = os.getenv("MCP_INTERNAL_TOKEN", "")
        supplied = request.headers.get("X-Internal-Token", "")
        if configured:
            return bool(supplied) and hmac.compare_digest(configured, supplied)
        return request.remote_addr in {"127.0.0.1", "::1"}

    def _unauthorized():
        return jsonify({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32001, "message": "MCP authentication required"}}), 401

    @app.route('/mcp', methods=['POST'])
    def mcp_jsonrpc():
        if not _authorized():
            return _unauthorized()
        body = request.get_json(silent=True)
        if not body:
            return jsonify({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}), 400
        if isinstance(body, list):
            responses = [handler.handle(req) for req in body]
            return jsonify(responses)
        return jsonify(handler.handle(body))

    @app.route('/mcp', methods=['GET'])
    def mcp_info():
        if not _authorized():
            return _unauthorized()
        return jsonify({
            "name": MCP_SERVER_NAME,
            "version": MCP_SERVER_VERSION,
            "protocol_version": MCP_PROTOCOL_VERSION,
            "tools": len(getattr(handler, '_tools', {})),
            "resources": len(getattr(handler, '_resources', {})),
            "orchestrator": use_orchestrator,
        })

    @app.route('/api/graphops/selection/graph', methods=['GET'])
    def graphops_selection_graph():
        if not _authorized():
            return _unauthorized()
        try:
            from graphops_graph_resolver import GraphSelectionResolver
            return jsonify(GraphSelectionResolver(graph_selection_engine).snapshot(
                node_limit=request.args.get('node_limit', 200),
                edge_limit=request.args.get('edge_limit', 300),
                focus_id=request.args.get('focus_id', ''),
            ))
        except (TypeError, ValueError) as exc:
            return jsonify({'status': 'error', 'message': str(exc), 'nodes': [], 'edges': []}), 400

    @app.route('/api/graphops/infrastructure/snapshot', methods=['GET'])
    def graphops_infrastructure_snapshot():
        """Project bounded live graph evidence into explicit infrastructure classes."""
        if not _authorized():
            return _unauthorized()
        try:
            from graphops_graph_resolver import GraphSelectionResolver
            from graphops_infrastructure import (attach_external_infrastructure_evidence,
                                                 build_infrastructure_snapshot)
            from graphops_peeringdb import get_peeringdb_client
            from graphops_ris_live import get_ris_live_collector
            focus_id = request.args.get('focus_id', '')
            graph = GraphSelectionResolver(graph_selection_engine).snapshot(
                node_limit=request.args.get('node_limit', 500),
                edge_limit=request.args.get('edge_limit', 1000), focus_id=focus_id)
            base = build_infrastructure_snapshot(graph, focus_id)
            asns = [item.get('asn') for item in base.get('domains', []) if item.get('asn')]
            prefixes = [prefix for item in base.get('domains', []) for prefix in item.get('prefixes', [])]
            peeringdb = get_peeringdb_client().snapshot(asns)
            collector = get_ris_live_collector(); collector.update_scope(prefixes, asns)
            since = float(request.args['since']) if request.args.get('since') else None
            until = float(request.args['until']) if request.args.get('until') else None
            if since is not None and until is not None and (until <= since or until - since > 604800):
                raise ValueError('infrastructure time window must be increasing and at most 7 days')
            fused = attach_external_infrastructure_evidence(
                base, peeringdb, collector.snapshot(since=since, until=until))
            from graphops_infrastructure_contradictions import evaluate_infrastructure_contradictions
            fused['infrastructureContradictions'] = evaluate_infrastructure_contradictions(
                fused, since=since, until=until, limit=request.args.get('contradiction_limit', 100))
            return jsonify(fused)
        except (TypeError, ValueError) as exc:
            return jsonify({'status': 'error', 'message': str(exc), 'domains': [],
                            'observedFlows': [], 'modeledPathCandidates': [], 'bounded': True}), 400

    @app.route('/api/graphops/infrastructure/peeringdb/v1/snapshot', methods=['GET'])
    def graphops_peeringdb_snapshot():
        if not _authorized():
            return _unauthorized()
        try:
            from graphops_graph_resolver import GraphSelectionResolver
            from graphops_infrastructure import build_infrastructure_snapshot
            from graphops_peeringdb import get_peeringdb_client
            graph = GraphSelectionResolver(graph_selection_engine).snapshot(node_limit=500, edge_limit=1000)
            base = build_infrastructure_snapshot(graph)
            asns = [item.get('asn') for item in base.get('domains', []) if item.get('asn')]
            return jsonify(get_peeringdb_client().snapshot(asns, force=request.args.get('refresh') == '1'))
        except (TypeError, ValueError) as exc:
            return jsonify({'status': 'error', 'message': str(exc), 'networks': [], 'bounded': True}), 400

    @app.route('/api/graphops/infrastructure/control-plane/v1/snapshot', methods=['GET'])
    def graphops_control_plane_snapshot():
        if not _authorized():
            return _unauthorized()
        try:
            from graphops_graph_resolver import GraphSelectionResolver
            from graphops_infrastructure import build_infrastructure_snapshot
            from graphops_ris_live import get_ris_live_collector
            graph = GraphSelectionResolver(graph_selection_engine).snapshot(node_limit=500, edge_limit=1000)
            base = build_infrastructure_snapshot(graph)
            asns = [item.get('asn') for item in base.get('domains', []) if item.get('asn')]
            prefixes = [prefix for item in base.get('domains', []) for prefix in item.get('prefixes', [])]
            collector = get_ris_live_collector(); collector.update_scope(prefixes, asns)
            since = float(request.args['since']) if request.args.get('since') else None
            until = float(request.args['until']) if request.args.get('until') else None
            return jsonify(collector.snapshot(since=since, until=until,
                                               limit=request.args.get('limit', 128)))
        except (TypeError, ValueError) as exc:
            return jsonify({'status': 'error', 'message': str(exc), 'controlPlanePaths': [],
                            'bounded': True}), 400

    @app.route('/api/graphops/infrastructure/contradictions/v1', methods=['GET'])
    def graphops_infrastructure_contradictions():
        if not _authorized():
            return _unauthorized()
        try:
            from graphops_graph_resolver import GraphSelectionResolver
            from graphops_infrastructure import (attach_external_infrastructure_evidence,
                                                 build_infrastructure_snapshot)
            from graphops_infrastructure_contradictions import evaluate_infrastructure_contradictions
            from graphops_peeringdb import get_peeringdb_client
            from graphops_ris_live import get_ris_live_collector
            graph = GraphSelectionResolver(graph_selection_engine).snapshot(node_limit=500, edge_limit=1000)
            base = build_infrastructure_snapshot(graph)
            asns = [item.get('asn') for item in base.get('domains', []) if item.get('asn')]
            prefixes = [prefix for item in base.get('domains', []) for prefix in item.get('prefixes', [])]
            since = float(request.args['since']) if request.args.get('since') else None
            until = float(request.args['until']) if request.args.get('until') else None
            if since is not None and until is not None and (until <= since or until - since > 604800):
                raise ValueError('contradiction time window must be increasing and at most 7 days')
            collector = get_ris_live_collector(); collector.update_scope(prefixes, asns)
            fused = attach_external_infrastructure_evidence(
                base, get_peeringdb_client().snapshot(asns), collector.snapshot(since=since, until=until, limit=256))
            return jsonify(evaluate_infrastructure_contradictions(
                fused, since=since, until=until, limit=request.args.get('limit', 100)))
        except (TypeError, ValueError) as exc:
            return jsonify({'status': 'error', 'message': str(exc), 'findings': [],
                            'changes': [], 'bounded': True}), 400

    @app.route('/api/graphops/selection/resolve', methods=['POST'])
    def graphops_selection_resolve():
        """Resolve a typed selection against its retained immutable revision."""
        if not _authorized():
            return _unauthorized()
        try:
            from graphops_graph_resolver import GraphSelectionResolver
            payload = request.get_json(silent=True) or {}
            return jsonify(GraphSelectionResolver(graph_selection_engine).resolve(payload))
        except (TypeError, ValueError) as exc:
            return jsonify({'status': 'refused', 'error': str(exc)}), 400

    @app.route('/api/graphops/conversation', methods=['POST'])
    def graphops_conversation():
        """Run a bounded, read-only Copilot investigation around a pinned selection."""
        if not _authorized():
            return _unauthorized()
        try:
            payload = request.get_json(silent=True) or {}
            if not isinstance(payload, dict):
                raise ValueError('JSON object required')
            unknown = set(payload) - {'mode', 'question', 'selection', 'maxSteps'}
            if unknown:
                raise ValueError(f'unknown conversation fields: {", ".join(sorted(unknown))}')
            if payload.get('mode', 'ask') != 'ask':
                raise ValueError('conversation mode must be ask; directives use the allow-listed directive API')
            question = str(payload.get('question') or '').strip()
            if not question or len(question) > 2000:
                raise ValueError('question must contain 1 through 2000 characters')
            selection = payload.get('selection')
            if not isinstance(selection, dict):
                raise ValueError('selection is required')
            selection_unknown = set(selection) - {'kind', 'entityId', 'graphRevision'}
            if selection_unknown:
                raise ValueError(f'unknown selection fields: {", ".join(sorted(selection_unknown))}')
            if selection.get('kind') not in {'graph-node', 'graph-edge', 'event'}:
                raise ValueError('selection kind must be graph-node, graph-edge, or event')
            if not selection.get('entityId') or not selection.get('graphRevision'):
                raise ValueError('selection entityId and graphRevision are required')
            max_steps = min(max(int(payload.get('maxSteps', 3)), 1), 4)

            from graphops_graph_resolver import GraphResolutionError, GraphSelectionResolver
            resolver = GraphSelectionResolver(graph_selection_engine)
            requested_revision = str(selection.get('graphRevision'))
            configured_mode = str(app.config.get(
                'SCYTHE_GRAPHOPS_RETRIEVAL_MODE',
                os.getenv('SCYTHE_GRAPHOPS_RETRIEVAL_MODE', 'pinned_fused'))).lower()
            aliases = {'baseline': 'legacy', 'graph': 'pinned_graph',
                       'fused': 'pinned_fused'}
            retrieval_mode = aliases.get(configured_mode, configured_mode)
            if retrieval_mode not in {'legacy', 'pinned_legacy',
                                       'pinned_graph', 'pinned_fused'}:
                raise ValueError('invalid SCYTHE_GRAPHOPS_RETRIEVAL_MODE')
            pinned_view = None
            if retrieval_mode == 'legacy':
                selection_rebased = False
                try:
                    resolved = resolver.resolve(selection)
                except GraphResolutionError as exc:
                    if 'retained snapshot is unavailable' not in str(exc):
                        raise
                    current = resolver.snapshot(node_limit=500, edge_limit=1000)
                    entity_id = str(selection.get('entityId'))
                    collection = (current.get('edges', []) if selection.get('kind') == 'graph-edge'
                                  else current.get('nodes', []))
                    if not any(str(item.get('id')) == entity_id for item in collection):
                        raise
                    resolved = resolver.resolve({**selection,
                                                 'graphRevision': current['graphRevision']})
                    selection_rebased = True
            else:
                pinned_view = resolver.pin_selection(selection, allow_rebase=True)
                resolved = pinned_view.resolve_selection()
                selection_rebased = pinned_view.selection_rebased
            entity = resolved.get('node') or resolved.get('edge') or {}
            evidence_context = {
                'graphRevision': resolved.get('graphRevision'),
                'selectionKind': resolved.get('selectionKind'),
                'entity': entity,
                'incidentEdges': (resolved.get('incidentEdges') or [])[:12],
                'memberNodes': (resolved.get('memberNodes') or [])[:12],
                'authority': 'RETAINED_IMMUTABLE_GRAPH_STATE_WITH_READ_TIME_ENRICHMENT',
            }
            grounded_question = (
                f"OPERATOR QUESTION:\n{question}\n\n"
                "REVISION-PINNED SELECTED ENTITY (JSON):\n" +
                json.dumps(evidence_context, sort_keys=True, default=str) +
                "\n\nTreat OBSERVED/MEASURED facts as evidence. Treat enrichment and geography as "
                "INFERRED estimates. Do not claim adjacency proves causality. Identify a falsifier."
            )
            tool = getattr(handler, '_tools', {}).get('graphops_investigate')
            if tool is None:
                return jsonify({'status': 'unavailable',
                                'error': 'GraphOps Copilot is not registered'}), 503
            ollama_route = 'TEST'
            if not app.config.get('TESTING'):
                # The agent has a deterministic no-model fallback for automation, but this
                # endpoint explicitly promises an Ollama conversation. Fail quickly instead
                # of holding the browser open on an unreachable remote workstation.
                agent = getattr(handler, '_graphops_agent', None)
                probe = agent.probe_ollama(timeout=3) if agent is not None else {
                    'available': False, 'route': 'UNAVAILABLE'}
                if not probe.get('available'):
                    logger.warning('[mcp] GraphOps conversation Ollama endpoint pool unavailable')
                    return jsonify({'status': 'unavailable',
                                    'error': 'Ollama is unreachable; start it and retry'}), 503
                ollama_route = probe.get('route', 'UNAVAILABLE')
            effective_steps = (min(max_steps, 1) if ollama_route in {
                'LOCAL_FALLBACK', 'CONFIGURED_LOCAL'} else max_steps)
            # GraphOpsAgent owns mutable executor state; serialize conversations.
            with conversation_lock:
                agent = getattr(handler, '_graphops_agent', None)
                engine_context = (agent.bound_engine(pinned_view.engine_adapter())
                                  if agent is not None and pinned_view is not None
                                  else nullcontext())
                retrieval_policy_context = (agent.bounded_retrieval_policy(structured_semantic=True)
                                            if agent is not None and retrieval_mode in {
                                                'pinned_graph', 'pinned_fused'}
                                            else nullcontext())
                with engine_context, retrieval_policy_context:
                    retrieval = None
                    retrieval_context = None
                    legacy_rag = retrieval_mode in {'legacy', 'pinned_legacy'}
                    if pinned_view is not None and retrieval_mode in {
                            'pinned_graph', 'pinned_fused'}:
                        from graphops.evidence_fabric import (
                            GraphFusionEvidenceFabric, SemanticSeedProvider)
                        semantic_seeds = []
                        if retrieval_mode == 'pinned_fused' and agent is not None:
                            semantic_seeds = SemanticSeedProvider(
                                executor=agent.executor,
                                embedding_engine=getattr(agent, '_embedding_engine', None),
                            ).search(question, limit=6,
                                     projection_ids={item['id'] for item in pinned_view.nodes})
                        fabric = GraphFusionEvidenceFabric()
                        retrieval = fabric.build(
                            question=question, view=pinned_view, mode=retrieval_mode,
                            semantic_seeds=semantic_seeds)
                        retrieval_context = fabric.render_context(retrieval)
                    elif pinned_view is not None:
                        retrieval = {
                            'mode': retrieval_mode, 'version': 'graphfusion.pin.v1',
                            'graph': {'revision': pinned_view.graph_revision,
                                      'detectedNodes': pinned_view.detected_node_count,
                                      'detectedEdges': pinned_view.detected_edge_count},
                            'projection': pinned_view.to_receipt(),
                            'traversal': None, 'paths': [],
                            'boundary': 'PINNED LEGACY MODE; MANDATORY TRAVERSAL DISABLED',
                        }
                    result = tool.fn({
                        'question': grounded_question, 'max_steps': effective_steps,
                        '_retrieval_context': retrieval_context,
                        '_legacy_rag': legacy_rag,
                    })
            if isinstance(result, dict) and result.get('error'):
                return jsonify({'status': 'unavailable', 'error': result['error']}), 503
            return jsonify({
                'status': 'completed', 'mode': 'ask', 'question': question,
                'selection': {**selection, 'graphRevision': resolved.get('graphRevision')},
                'requestedGraphRevision': requested_revision,
                'selectionRebased': selection_rebased,
                'result': result, 'bounded': True, 'modelAuthority': 'INTERPRETIVE_ONLY',
                'ollamaRoute': ollama_route,
                'maxSteps': effective_steps,
                'retrieval': retrieval,
                'directiveExecution': False,
                'boundary': ('OLLAMA INTERPRETS BOUNDED GRAPH EVIDENCE; IT DOES NOT EXECUTE '
                             'DIRECTIVES OR ESTABLISH CAUSALITY'),
            })
        except (TypeError, ValueError, KeyError) as exc:
            return jsonify({'status': 'refused', 'error': str(exc)}), 400
        except (OSError, RuntimeError) as exc:
            logger.warning('[mcp] GraphOps conversation unavailable: %s', exc)
            return jsonify({'status': 'unavailable', 'error': str(exc)}), 503

    @app.route('/api/graphops/explorer', methods=['GET'])
    def graphops_explorer():
        if not _authorized():
            return _unauthorized()
        try:
            from graphops_graph_resolver import GraphSelectionResolver
            return jsonify(GraphSelectionResolver(graph_selection_engine).explore(
                query=request.args.get('q', ''), protocol=request.args.get('protocol', ''),
                start=request.args.get('start'), end=request.args.get('end'),
                focus_id=request.args.get('focus_id', ''), depth=request.args.get('depth', 1),
                node_limit=request.args.get('node_limit', 100),
                edge_limit=request.args.get('edge_limit', 150),
                node_offset=request.args.get('node_offset', 0),
                edge_offset=request.args.get('edge_offset', 0),
            ))
        except (TypeError, ValueError) as exc:
            return jsonify({'status': 'refused', 'error': str(exc), 'nodes': [], 'edges': []}), 400

    @app.route('/api/graphops/rf-observations', methods=['POST'])
    def graphops_rf_observation_ingest():
        if not _authorized():
            return _unauthorized()
        try:
            from graphops_rf_ingest import ingest_measured_rf
            from rf_bridge import get_rf_observation_store
            result = ingest_measured_rf(get_rf_observation_store(), request.get_json(silent=True))
            return jsonify(result), 201 if result['status'] == 'accepted' else 202
        except (TypeError, ValueError, KeyError) as exc:
            return jsonify({'status': 'rejected', 'error': str(exc), 'rawIqAccepted': False}), 400

    @app.route('/api/graphops/rf-observations/status', methods=['GET'])
    def graphops_rf_observation_status():
        if not _authorized():
            return _unauthorized()
        from rf_bridge import get_rf_observation_store
        return jsonify({'status': 'ok', **get_rf_observation_store().stats(),
                        'authority': 'MEASURED_SPECTRAL_SUMMARY', 'rawIqAccepted': False})

    @app.route('/api/graphops/eve/events', methods=['POST'])
    def graphops_eve_events():
        if not _authorized():
            return _unauthorized()
        try:
            from eve_graph_ingest import commit_eve_events, validate_eve_batch
            import writebus
            events = validate_eve_batch(request.get_json(silent=True))
            graph_session = str(getattr(graph_selection_engine, 'session_id', '') or
                                app.config.get('SCYTHE_INSTANCE_ID') or 'default')
            return jsonify(commit_eve_events(events, writebus.bus(),
                                             idempotency_scope=graph_session))
        except (TypeError, ValueError, KeyError) as exc:
            return jsonify({'status': 'rejected', 'error': str(exc),
                            'rawPacketsAccepted': False}), 400

    @app.route('/api/graphops/eve/status', methods=['GET'])
    def graphops_eve_status():
        if not _authorized():
            return _unauthorized()
        from eve_graph_ingest import STATS
        return jsonify({'status': 'ok', **STATS.snapshot()})

    def _directive_response(expected_mode):
        if not _authorized():
            return _unauthorized()
        try:
            from graphops_director import GraphOpsDirector
            try:
                from rf_bridge import get_rf_observation_store
                provider = get_rf_observation_store()
            except Exception:
                provider = None
            return jsonify(GraphOpsDirector(
                engine=graph_selection_engine, rf_observation_provider=provider,
            ).compile(request.get_json(silent=True) or {}, expected_mode=expected_mode))
        except (TypeError, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            return jsonify({'error': str(exc)}), 400

    @app.route('/api/graphops/directives/preview', methods=['POST'])
    def graphops_directive_preview():
        return _directive_response('preview')

    @app.route('/api/graphops/directives/execute', methods=['POST'])
    def graphops_directive_execute():
        return _directive_response('execute')

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
