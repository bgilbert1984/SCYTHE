"""collection_tasks.py — Collection-task orchestration for RF_SCYTHE.

Turns knowledge gaps (stale inferences, unknown objects, inference-heavy
posture) into first-class graph entities so the system can *drive collection*
instead of merely analysing.

Node kind: ``collection_task``
Edge kinds:
    REQUESTS_COLLECTION_OF  — task → target node
    AIMS_TO_CONFIRM         — task → inferred macro-edge / edge
    FULFILLED_BY_SESSION    — task → pcap_session (post-ingestion)

Lifecycle: proposed → accepted → in_progress → satisfied | expired | rejected

Formal sub-objects (authoritative):
    spec        — capture contract (interface, duration, filter, sensor)
    lifecycle   — timestamp/operator bookkeeping per transition
    closure     — post-satisfaction evidence + belief delta

Usage:
    from collection_tasks import CollectionTaskManager
    mgr = CollectionTaskManager(hypergraph_engine)
    task = mgr.propose_task(...)
    mgr.satisfy_task(task_id, evidence_refs=[...])
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────

TASK_NODE_KIND = "collection_task"
EDGE_REQUESTS = "REQUESTS_COLLECTION_OF"
EDGE_CONFIRMS = "AIMS_TO_CONFIRM"
EDGE_FULFILLED = "FULFILLED_BY_SESSION"

VALID_STATUSES = {"proposed", "accepted", "in_progress", "satisfied", "expired", "rejected"}
VALID_PRIORITIES = {"critical", "high", "medium", "low"}
VALID_TARGET_TYPES = {"host", "session", "geo", "flow", "edge", "org", "unknown"}
VALID_METHODS = {
    "pcap_capture", "sensor_tasking", "geo_collection", "operator_review",
    "nmap_scan", "dns_query", "whois_lookup", "passive_rf",
}


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _task_id() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    h = hashlib.sha256(f"{ts}-{time.monotonic_ns()}".encode()).hexdigest()[:6]
    return f"COLLECT-{ts}-{h}"


# ─────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class CollectionTarget:
    """What the task is collecting against."""
    target_type: str = "unknown"     # host | session | geo | flow | edge | org
    value: str = ""                  # node ID, IP, geo cell, org name, etc.
    description: str = ""            # human-readable context

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CollectionSpec:
    """Capture contract: *how* to collect (operator/automation reads this).

    TAK-GPT never runs tcpdump. It emits machine-verifiable intent via this
    spec. External operators or automation consume the spec and execute.
    """
    interface_hint: Optional[str] = None     # e.g. "eth0", "wlan0", "any"
    duration_seconds: int = 60               # how long to capture
    filter: str = "ip or ip6"                # BPF filter expression
    sensor_hint: List[str] = field(default_factory=list)   # preferred sensor IDs
    geo_hint: List[str] = field(default_factory=list)      # region hints
    confidence_target: float = 0.7           # desired post-collection confidence

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Lifecycle:
    """Timestamp and operator bookkeeping for each status transition."""
    proposed_at: str = field(default_factory=_utc_now_iso)   # ISO 8601
    proposed_by: str = "system"                              # who/what created

    accepted_at: Optional[str] = None         # ISO 8601
    accepted_by: Optional[str] = None         # operator callsign / automation ID

    in_progress_at: Optional[str] = None      # ISO 8601

    satisfied_at: Optional[str] = None        # ISO 8601
    expired_at: Optional[str] = None          # ISO 8601
    rejected_at: Optional[str] = None         # ISO 8601
    rejected_reason: Optional[str] = None     # human-readable

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def record(self, status: str, by: Optional[str] = None,
               reason: Optional[str] = None) -> None:
        """Record a lifecycle transition timestamp."""
        now = _utc_now_iso()
        if status == "proposed":
            if not self.proposed_at:
                self.proposed_at = now
            if by:
                self.proposed_by = by
        elif status == "accepted":
            self.accepted_at = now
            self.accepted_by = by
        elif status == "in_progress":
            self.in_progress_at = now
        elif status == "satisfied":
            self.satisfied_at = now
        elif status == "expired":
            self.expired_at = now
        elif status == "rejected":
            self.rejected_at = now
            self.rejected_reason = reason


@dataclass
class Closure:
    """Post-satisfaction evidence and belief impact."""
    satisfied_by: Optional[str] = None                       # single session_id that closed it
    evidence_refs: List[str] = field(default_factory=list)   # pcap sessions, sensor IDs
    belief_delta: Optional[Dict[str, Any]] = None            # {edge_id: {before, after}}
    session_ids: List[str] = field(default_factory=list)     # pcap session IDs that fulfilled

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CollectionTask:
    """A structured request for evidence collection.

    sub-objects:
        spec        — capture contract (interface, duration, filter, sensor)
        lifecycle   — timestamp/operator bookkeeping per transition
        closure     — post-satisfaction evidence + belief delta
    """
    task_id: str = field(default_factory=_task_id)
    target: CollectionTarget = field(default_factory=CollectionTarget)
    objective: str = ""              # what we want to learn
    trigger_reason: str = ""         # why this task was created
    priority: str = "medium"         # critical | high | medium | low
    recommended_methods: List[str] = field(default_factory=list)
    geo_hint: List[str] = field(default_factory=list)
    expires_at: str = ""             # ISO 8601
    requested_by: str = "analyst_ai"
    status: str = "proposed"
    created_at: str = field(default_factory=_utc_now_iso)
    satisfied_by: List[str] = field(default_factory=list)  # legacy — use closure.satisfied_by
    related_edges: List[str] = field(default_factory=list)  # edge IDs this aims to confirm
    # ── formal sub-objects (authoritative) ──
    spec: CollectionSpec = field(default_factory=CollectionSpec)
    lifecycle: Lifecycle = field(default_factory=Lifecycle)
    closure: Closure = field(default_factory=Closure)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["target"] = self.target.to_dict()
        d["spec"] = self.spec.to_dict()
        d["lifecycle"] = self.lifecycle.to_dict()
        d["closure"] = self.closure.to_dict()
        return d

    def is_active(self) -> bool:
        return self.status in ("proposed", "accepted", "in_progress")

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at.rstrip("Z"))
            return datetime.utcnow() > exp
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────
# Manager
# ─────────────────────────────────────────────────────────────────────────

class CollectionTaskManager:
    """Creates, tracks, and closes collection tasks as graph entities.

    All tasks are stored as ``collection_task`` nodes in the hypergraph.
    Edges link tasks to their targets (``REQUESTS_COLLECTION_OF``) and
    to the inferred edges they aim to confirm (``AIMS_TO_CONFIRM``).
    """

    def __init__(self, hypergraph_engine: Any):
        self.engine = hypergraph_engine

    # ── propose ──────────────────────────────────────────────────────

    def propose_task(
        self,
        *,
        target_type: str = "unknown",
        target_value: str = "",
        target_description: str = "",
        objective: str = "",
        trigger_reason: str = "",
        priority: str = "medium",
        recommended_methods: Optional[List[str]] = None,
        geo_hint: Optional[List[str]] = None,
        ttl_hours: float = 24.0,
        requested_by: str = "analyst_ai",
        related_edges: Optional[List[str]] = None,
        # ── capture spec (capture contract) ──
        interface_hint: Optional[str] = None,
        duration_seconds: int = 60,
        bpf_filter: str = "ip or ip6",
        sensor_hint: Optional[List[str]] = None,
        confidence_target: float = 0.7,
    ) -> CollectionTask:
        """Create a collection task and commit it to the graph.

        Returns the CollectionTask dataclass (also now a graph node).
        """
        expires = (datetime.utcnow() + timedelta(hours=ttl_hours)).isoformat() + "Z"

        # Build capture contract
        spec = CollectionSpec(
            interface_hint=interface_hint,
            duration_seconds=duration_seconds,
            filter=bpf_filter,
            sensor_hint=sensor_hint or [],
            geo_hint=geo_hint or [],
            confidence_target=confidence_target,
        )

        # Lifecycle: record proposal
        lifecycle = Lifecycle()
        lifecycle.record("proposed", by=requested_by)

        task = CollectionTask(
            target=CollectionTarget(
                target_type=target_type,
                value=target_value,
                description=target_description,
            ),
            objective=objective,
            trigger_reason=trigger_reason,
            priority=priority if priority in VALID_PRIORITIES else "medium",
            recommended_methods=recommended_methods or ["pcap_capture", "sensor_tasking"],
            geo_hint=geo_hint or [],
            expires_at=expires,
            requested_by=requested_by,
            related_edges=related_edges or [],
            spec=spec,
            lifecycle=lifecycle,
            closure=Closure(),
        )

        self._commit_task(task)
        logger.info("[collection] proposed task %s → %s (%s)",
                     task.task_id, target_value, trigger_reason)
        return task

    def _commit_task(self, task: CollectionTask) -> None:
        """Write task node + relationship edges to the graph."""
        engine = self.engine

        # ── Node: collection_task ──
        task_node = {
            "event_type": "NODE_CREATE",
            "entity_id": task.task_id,
            "entity_data": {
                "id": task.task_id,
                "kind": TASK_NODE_KIND,
                "labels": {
                    "status": task.status,
                    "priority": task.priority,
                    "trigger": task.trigger_reason,
                    "requested_by": task.requested_by,
                },
                "metadata": {
                    "obs_class": "operational",
                    "task": task.to_dict(),
                    "spec": task.spec.to_dict(),
                    "lifecycle": task.lifecycle.to_dict(),
                    "closure": task.closure.to_dict(),
                    "confidence_target": task.spec.confidence_target,
                    "provenance": {
                        "source": "collection_task_manager",
                        "timestamp": task.created_at,
                    },
                },
            },
        }
        self._apply(task_node)

        # ── Edge: REQUESTS_COLLECTION_OF → target ──
        if task.target.value:
            edge_req = {
                "event_type": "EDGE_CREATE",
                "entity_id": f"{task.task_id}__requests__{task.target.value}",
                "entity_data": {
                    "id": f"{task.task_id}__requests__{task.target.value}",
                    "kind": EDGE_REQUESTS,
                    "nodes": [task.task_id, task.target.value],
                    "metadata": {
                        "obs_class": "operational",
                        "objective": task.objective,
                    },
                },
            }
            self._apply(edge_req)

        # ── Edges: AIMS_TO_CONFIRM → related inferred edges ──
        for edge_id in task.related_edges:
            edge_confirm = {
                "event_type": "EDGE_CREATE",
                "entity_id": f"{task.task_id}__confirms__{edge_id}",
                "entity_data": {
                    "id": f"{task.task_id}__confirms__{edge_id}",
                    "kind": EDGE_CONFIRMS,
                    "nodes": [task.task_id, edge_id],
                    "metadata": {"obs_class": "operational"},
                },
            }
            self._apply(edge_confirm)

    def _apply(self, graph_event: Dict[str, Any]) -> bool:
        """Apply a single graph event to the engine."""
        try:
            if hasattr(self.engine, "apply_graph_event"):
                return bool(self.engine.apply_graph_event(graph_event))
            # Fallback: direct node/edge dict insertion
            eid = graph_event["entity_id"]
            edata = graph_event["entity_data"]
            etype = graph_event["event_type"]
            if "NODE" in etype:
                if isinstance(self.engine.nodes, dict):
                    self.engine.nodes[eid] = edata
            elif "EDGE" in etype:
                if isinstance(self.engine.edges, dict):
                    self.engine.edges[eid] = edata
            return True
        except Exception as e:
            logger.error("[collection] graph apply failed: %s", e)
            return False

    # ── status transitions ───────────────────────────────────────────

    def update_status(
        self, task_id: str, new_status: str, *,
        by: str = "", reason: str = ""
    ) -> bool:
        """Transition a task to a new status and record lifecycle."""
        if new_status not in VALID_STATUSES:
            return False
        node = self._get_task_node(task_id)
        if not node:
            return False
        node["labels"]["status"] = new_status
        meta = node.get("metadata") or {}
        if "task" in meta:
            meta["task"]["status"] = new_status
        # ── lifecycle bookkeeping ──
        lifecycle_d = meta.get("lifecycle") or {}
        now = _utc_now_iso()
        if new_status == "accepted":
            lifecycle_d["accepted_at"] = now
            lifecycle_d["accepted_by"] = by
        elif new_status == "in_progress":
            lifecycle_d["in_progress_at"] = now
        elif new_status == "satisfied":
            lifecycle_d["satisfied_at"] = now
        elif new_status == "expired":
            lifecycle_d["expired_at"] = now
        elif new_status == "rejected":
            lifecycle_d["rejected_at"] = now
            lifecycle_d["rejected_reason"] = reason
        meta["lifecycle"] = lifecycle_d
        node["metadata"] = meta
        self._apply({
            "event_type": "NODE_UPDATE",
            "entity_id": task_id,
            "entity_data": node,
        })
        logger.info("[collection] %s → %s", task_id, new_status)
        return True

    def satisfy_task(
        self, task_id: str,
        evidence_refs: Optional[List[str]] = None,
        session_ids: Optional[List[str]] = None,
        belief_delta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Mark a task as satisfied and populate closure sub-object."""
        node = self._get_task_node(task_id)
        if not node:
            return False
        node["labels"]["status"] = "satisfied"
        meta = node.get("metadata") or {}
        task_data = meta.get("task") or {}
        task_data["status"] = "satisfied"
        # Legacy field
        task_data["satisfied_by"] = evidence_refs or []
        meta["task"] = task_data
        # ── closure sub-object ──
        closure_d = meta.get("closure") or {}
        closure_d["satisfied_by"] = (session_ids or [None])[0]  # single session_id
        closure_d["evidence_refs"] = evidence_refs or []
        closure_d["session_ids"] = session_ids or []
        closure_d["belief_delta"] = belief_delta
        meta["closure"] = closure_d
        # ── lifecycle: record satisfaction timestamp ──
        lifecycle_d = meta.get("lifecycle") or {}
        lifecycle_d["satisfied_at"] = _utc_now_iso()
        meta["lifecycle"] = lifecycle_d
        node["metadata"] = meta
        self._apply({
            "event_type": "NODE_UPDATE",
            "entity_id": task_id,
            "entity_data": node,
        })
        # ── FULFILLED_BY_SESSION edges ──
        for sid in (session_ids or []):
            edge_fulfilled = {
                "event_type": "EDGE_CREATE",
                "entity_id": f"{task_id}__fulfilled__{sid}",
                "entity_data": {
                    "id": f"{task_id}__fulfilled__{sid}",
                    "kind": EDGE_FULFILLED,
                    "nodes": [task_id, sid],
                    "metadata": {
                        "obs_class": "operational",
                        "evidence_refs": evidence_refs or [],
                    },
                },
            }
            self._apply(edge_fulfilled)
        logger.info("[collection] %s satisfied with %d evidence refs, %d sessions",
                     task_id, len(evidence_refs or []), len(session_ids or []))
        return True

    def expire_stale_tasks(self) -> int:
        """Scan all active tasks and expire those past their TTL."""
        count = 0
        for task_id, node in self._all_task_nodes():
            task_data = (node.get("metadata") or {}).get("task") or {}
            exp = task_data.get("expires_at", "")
            if exp:
                try:
                    exp_dt = datetime.fromisoformat(exp.rstrip("Z"))
                    if datetime.utcnow() > exp_dt:
                        self.update_status(task_id, "expired")
                        count += 1
                except Exception:
                    pass
        return count

    # ── query helpers ────────────────────────────────────────────────

    def _get_task_node(self, task_id: str) -> Optional[Dict[str, Any]]:
        nodes = self.engine.nodes if hasattr(self.engine, 'nodes') else {}
        if isinstance(nodes, dict):
            n = nodes.get(task_id)
        else:
            n = None
        if n is None:
            return None
        return n if isinstance(n, dict) else (n.to_dict() if hasattr(n, 'to_dict') else None)

    def _all_task_nodes(self) -> List[Tuple[str, Dict[str, Any]]]:
        nodes = self.engine.nodes if hasattr(self.engine, 'nodes') else {}
        result = []
        for n in (nodes.values() if isinstance(nodes, dict) else nodes):
            nd = n if isinstance(n, dict) else (n.to_dict() if hasattr(n, 'to_dict') else {})
            if nd.get("kind") == TASK_NODE_KIND:
                result.append((nd.get("id", ""), nd))
        return result

    def list_tasks(
        self,
        *,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return collection tasks, optionally filtered."""
        tasks = []
        for task_id, node in self._all_task_nodes():
            labels = node.get("labels") or {}
            if status and labels.get("status") != status:
                continue
            if priority and labels.get("priority") != priority:
                continue
            task_data = (node.get("metadata") or {}).get("task") or {}
            tasks.append({
                "task_id": task_id,
                "status": labels.get("status", "unknown"),
                "priority": labels.get("priority", "medium"),
                "trigger": labels.get("trigger", ""),
                "objective": task_data.get("objective", ""),
                "target": task_data.get("target", {}),
                "recommended_methods": task_data.get("recommended_methods", []),
                "geo_hint": task_data.get("geo_hint", []),
                "expires_at": task_data.get("expires_at", ""),
                "requested_by": task_data.get("requested_by", ""),
                "created_at": task_data.get("created_at", ""),
                "related_edges": task_data.get("related_edges", []),
                "satisfied_by": task_data.get("satisfied_by", []),
                "spec": (node.get("metadata") or {}).get("spec", {}),
                "lifecycle": (node.get("metadata") or {}).get("lifecycle", {}),
                "closure": (node.get("metadata") or {}).get("closure", {}),
                "confidence_target": (node.get("metadata") or {}).get("confidence_target", 0.7),
            })
        # Sort: active first, then by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        tasks.sort(key=lambda t: (
            0 if t["status"] in ("proposed", "accepted", "in_progress") else 1,
            priority_order.get(t["priority"], 9),
        ))
        return tasks[:limit]

    def tasks_for_node(self, node_id: str) -> List[Dict[str, Any]]:
        """Return collection tasks targeting a specific node."""
        edges = self.engine.edges if hasattr(self.engine, 'edges') else {}
        task_ids: Set[str] = set()

        for e in (edges.values() if isinstance(edges, dict) else edges):
            ed = e if isinstance(e, dict) else (e.to_dict() if hasattr(e, 'to_dict') else {})
            if ed.get("kind") in (EDGE_REQUESTS, EDGE_CONFIRMS):
                edge_nodes = ed.get("nodes") or []
                if node_id in edge_nodes:
                    # The other node in the edge is the task
                    for nid in edge_nodes:
                        if nid != node_id and nid.startswith("COLLECT-"):
                            task_ids.add(nid)

        result = []
        for task_id, node in self._all_task_nodes():
            if task_id in task_ids:
                task_data = (node.get("metadata") or {}).get("task") or {}
                result.append({
                    "task_id": task_id,
                    "status": (node.get("labels") or {}).get("status", "unknown"),
                    "objective": task_data.get("objective", ""),
                    "priority": (node.get("labels") or {}).get("priority", "medium"),
                })
        return result

    # ── gap analysis ─────────────────────────────────────────────────

    def collection_gap_summary(self, limit: int = 10) -> Dict[str, Any]:
        """Identify top beliefs lacking sensor backing.

        Scans inferred edges for stale ones (no evidence_refs) and returns
        a ranked list of collection gaps, including whether a task already
        exists for each.
        """
        edges = self.engine.edges if hasattr(self.engine, 'edges') else {}

        # Find existing task targets
        existing_targets: Set[str] = set()
        for _, node in self._all_task_nodes():
            task_data = (node.get("metadata") or {}).get("task") or {}
            target = task_data.get("target") or {}
            if target.get("value"):
                existing_targets.add(target["value"])

        gaps = []
        for e in (edges.values() if isinstance(edges, dict) else edges):
            ed = e if isinstance(e, dict) else (e.to_dict() if hasattr(e, 'to_dict') else {})
            meta = ed.get("metadata") or ed.get("meta") or {}
            obs_class = meta.get("obs_class", "")
            kind = ed.get("kind", "")

            # Only care about inferred edges
            if obs_class != "inferred" and not kind.startswith("INFERRED_"):
                continue

            # Check for evidence
            has_evidence = False
            for prov_key in ("provenance_rule", "provenance", "provenance_write"):
                prov = meta.get(prov_key) or {}
                refs = prov.get("evidence_refs") or prov.get("evidence") or []
                if refs:
                    has_evidence = True
                    break

            if has_evidence:
                continue

            edge_nodes = ed.get("nodes") or []
            edge_id = ed.get("id", "?")
            confidence = meta.get("confidence", 0)
            tier = meta.get("confidence_tier", "C")

            # Higher confidence stale edges = bigger gaps
            gaps.append({
                "edge_id": edge_id,
                "kind": kind,
                "nodes": edge_nodes,
                "confidence": confidence,
                "tier": tier,
                "has_existing_task": any(n in existing_targets for n in edge_nodes),
            })

        # Sort by confidence desc (highest-confidence unvalidated = biggest gap)
        gaps.sort(key=lambda g: -g.get("confidence", 0))
        gaps = gaps[:limit]

        return {
            "total_gaps": len(gaps),
            "gaps": gaps,
            "existing_task_coverage": sum(1 for g in gaps if g["has_existing_task"]),
        }

    # ── auto-propose from stale inferences ───────────────────────────

    def auto_propose_from_stale(
        self,
        *,
        max_tasks: int = 5,
        min_confidence: float = 0.3,
        ttl_hours: float = 24.0,
    ) -> List[CollectionTask]:
        """Scan for stale inferred edges and auto-propose collection tasks.

        Only proposes tasks for edges that don't already have an active task.
        Returns the list of newly created tasks.
        """
        gap_summary = self.collection_gap_summary(limit=max_tasks * 2)
        gaps = gap_summary.get("gaps", [])

        new_tasks = []
        for gap in gaps:
            if len(new_tasks) >= max_tasks:
                break
            if gap.get("has_existing_task"):
                continue
            if gap.get("confidence", 0) < min_confidence:
                continue

            nodes = gap.get("nodes", [])
            target_value = nodes[0] if nodes else gap.get("edge_id", "unknown")

            task = self.propose_task(
                target_type="host" if "host" in target_value.lower() else "session",
                target_value=target_value,
                target_description=f"Stale inferred edge: {gap['kind']}",
                objective=f"Confirm {gap['kind']} relationship between {', '.join(nodes)}",
                trigger_reason=f"stale_inference (conf={gap.get('confidence', 0):.2f}, tier={gap.get('tier', '?')})",
                priority="high" if gap.get("tier") in ("A", "B") else "medium",
                recommended_methods=["pcap_capture", "sensor_tasking"],
                ttl_hours=ttl_hours,
                related_edges=[gap.get("edge_id", "")],
            )
            new_tasks.append(task)

        logger.info("[collection] auto-proposed %d tasks from %d gaps",
                     len(new_tasks), len(gaps))
        return new_tasks

    # ── belief-driven closure ────────────────────────────────────────

    def check_task_satisfaction(self) -> List[str]:
        """Check if any active tasks can be satisfied by new evidence.

        Scans edges targeted by active tasks. If those edges now have
        evidence_refs, mark the task as satisfied.

        Returns list of task_ids that were closed.
        """
        edges = self.engine.edges if hasattr(self.engine, 'edges') else {}
        closed = []

        for task_id, node in self._all_task_nodes():
            labels = node.get("labels") or {}
            if labels.get("status") not in ("proposed", "accepted", "in_progress"):
                continue

            task_data = (node.get("metadata") or {}).get("task") or {}
            related = task_data.get("related_edges") or []
            target = task_data.get("target") or {}
            target_val = target.get("value", "")

            # Check if related edges now have evidence
            new_evidence: List[str] = []

            for eid in related:
                if isinstance(edges, dict):
                    ed = edges.get(eid)
                else:
                    ed = None
                if ed is None:
                    continue
                ed = ed if isinstance(ed, dict) else (ed.to_dict() if hasattr(ed, 'to_dict') else {})
                meta = ed.get("metadata") or {}
                for prov_key in ("provenance_rule", "provenance", "provenance_write"):
                    prov = meta.get(prov_key) or {}
                    refs = prov.get("evidence_refs") or prov.get("evidence") or []
                    new_evidence.extend(refs)

            # Also check if target node now exists (for unknown-object tasks)
            if target_val and not new_evidence:
                nodes = self.engine.nodes if hasattr(self.engine, 'nodes') else {}
                if isinstance(nodes, dict) and target_val in nodes:
                    new_evidence.append(f"node_appeared:{target_val}")

            if new_evidence:
                self.satisfy_task(task_id, evidence_refs=new_evidence)
                closed.append(task_id)

        if closed:
            logger.info("[collection] auto-satisfied %d tasks: %s",
                         len(closed), ", ".join(closed))
        return closed

    # ── capture command emission ─────────────────────────────────────

    def emit_capture_command(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Emit a deterministic pcap.capture command for a task.

        TAK-GPT never runs tcpdump. It emits this machine-verifiable intent
        dict which an external operator or automation agent can execute.

        Returns None if the task doesn't exist or isn't active.
        """
        node = self._get_task_node(task_id)
        if not node:
            return None

        labels = node.get("labels") or {}
        if labels.get("status") not in ("proposed", "accepted", "in_progress"):
            return None

        meta = node.get("metadata") or {}
        task_data = meta.get("task") or {}
        spec = meta.get("spec") or {}
        target = task_data.get("target") or {}

        # Build BPF filter from target if not explicitly set or is default
        bpf = spec.get("filter", "ip or ip6")
        if bpf == "ip or ip6" and target.get("target_type") == "host" and target.get("value"):
            bpf = f"host {target['value']}"

        command = {
            "action": "pcap.capture",
            "version": "1.0",
            "task_id": task_id,
            "target": {
                "type": target.get("target_type", "unknown"),
                "value": target.get("value", ""),
            },
            "interface": spec.get("interface_hint") or "any",
            "duration_seconds": spec.get("duration_seconds", 60),
            "filter": bpf,
            "sensor_hint": spec.get("sensor_hint", []),
            "confidence_target": spec.get("confidence_target", 0.7),
            "priority": labels.get("priority", "medium"),
            "objective": task_data.get("objective", ""),
            "callback": {
                "upload_url": "/api/pcap/upload",
                "task_id": task_id,
            },
            "emitted_at": _utc_now_iso(),
        }

        logger.info("[collection] emitted pcap.capture command for %s → %s",
                     task_id, target.get("value", "?"))
        return command

    def emit_capture_commands_for_active(self) -> List[Dict[str, Any]]:
        """Emit capture commands for all active tasks that recommend pcap_capture.

        Active = lifecycle.accepted_at is set AND lifecycle.satisfied_at is not.
        Returns a list of pcap.capture command dicts.
        """
        commands = []
        for task_id, node in self._all_task_nodes():
            meta = node.get("metadata") or {}
            lifecycle = meta.get("lifecycle") or {}
            # Active means accepted but not yet satisfied
            if not lifecycle.get("accepted_at") or lifecycle.get("satisfied_at"):
                continue
            task_data = meta.get("task") or {}
            methods = task_data.get("recommended_methods") or []
            if "pcap_capture" not in methods:
                continue
            cmd = self.emit_capture_command(task_id)
            if cmd:
                commands.append(cmd)
        return commands

    # ── task ↔ session linking ───────────────────────────────────────

    def link_session_to_task(
        self, task_id: str, session_id: str
    ) -> bool:
        """Create a FULFILLED_BY_SESSION edge between a task and a pcap session.

        This is called when a pcap upload is linked to a collection task
        (before ingestion). The actual satisfaction happens after ingest
        via check_task_satisfaction().
        """
        node = self._get_task_node(task_id)
        if not node:
            return False

        edge_fulfilled = {
            "event_type": "EDGE_CREATE",
            "entity_id": f"{task_id}__linked__{session_id}",
            "entity_data": {
                "id": f"{task_id}__linked__{session_id}",
                "kind": EDGE_FULFILLED,
                "nodes": [task_id, session_id],
                "metadata": {
                    "obs_class": "operational",
                    "link_type": "pre_ingest",
                    "linked_at": _utc_now_iso(),
                },
            },
        }
        self._apply(edge_fulfilled)

        # Transition task to in_progress if it was proposed/accepted
        status = (node.get("labels") or {}).get("status", "")
        if status in ("proposed", "accepted"):
            self.update_status(task_id, "in_progress")

        logger.info("[collection] linked session %s to task %s", session_id, task_id)
        return True

    def tasks_matching_session(self, session_id: str) -> List[str]:
        """Find active tasks whose target overlaps with a pcap session's hosts.

        Used during ingest to auto-discover which tasks a session satisfies.
        """
        # Get session node to find its hosts
        nodes = self.engine.nodes if hasattr(self.engine, 'nodes') else {}
        if not isinstance(nodes, dict):
            return []

        session_node = nodes.get(session_id)
        if not session_node:
            return []

        # Collect IPs from session edges
        session_ips: Set[str] = set()
        edges = self.engine.edges if hasattr(self.engine, 'edges') else {}
        for e in (edges.values() if isinstance(edges, dict) else edges):
            ed = e if isinstance(e, dict) else (e.to_dict() if hasattr(e, 'to_dict') else {})
            enodes = ed.get("nodes") or []
            if session_id in enodes:
                for nid in enodes:
                    if nid != session_id:
                        session_ips.add(nid)

        # Find active tasks whose target.value is in session_ips
        matching = []
        for task_id, node in self._all_task_nodes():
            labels = node.get("labels") or {}
            if labels.get("status") not in ("proposed", "accepted", "in_progress"):
                continue
            task_data = (node.get("metadata") or {}).get("task") or {}
            target = task_data.get("target") or {}
            tv = target.get("value", "")
            if tv and tv in session_ips:
                matching.append(task_id)
        return matching
