"""BSG projection helpers: create safe, non-recursive projection views

This module provides:
- `safe_node_view(node)` - a minimal, non-referential view of a node
- `safe_bsg_view(group)` - a projection for a behavioral group using the
  canonical projection shape described in project notes
- `generate_bsg_projection(...)` - top-level envelope builder

The functions accept plain dict-like inputs and intentionally drop
references and complex objects to avoid graph-in-graph recursion.
"""
from __future__ import annotations

import datetime
import json
from typing import Any, Dict, Iterable, List, Optional


def _iso(dt: Any) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime.datetime):
        return dt.replace(microsecond=0).astimezone(datetime.timezone.utc).isoformat()
    try:
        # try parsing objects that expose isoformat
        return dt.isoformat()
    except Exception:
        return str(dt)


def _safe_scalar(v: Any) -> Any:
    """Return a safe scalar or recursively clean lists/dicts with limited depth.

    - Keep primitives (str, int, float, bool, None)
    - For lists, apply _safe_scalar to items
    - For dicts, keep only scalar or list values (no object references)
    - For other objects, return their string representation (non-recursive)
    """
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, datetime.datetime):
        return _iso(v)
    if isinstance(v, dict):
        out: Dict[str, Any] = {}
        for k, val in v.items():
            sv = _safe_scalar(val)
            if sv is not None:
                out[k] = sv
        return out
    if isinstance(v, (list, tuple)):
        return [_safe_scalar(x) for x in v]
    # fallback to string for unknown objects (avoids recursion)
    try:
        return str(v)
    except Exception:
        return None


def safe_node_view(node: Any) -> Dict[str, Any]:
    """Create a minimal, non-referential node view.

    Accepts dict-like node representations and returns a small dictionary
    containing only safe, non-recursive fields.
    """
    if node is None:
        return {}
    if isinstance(node, dict):
        candidates = [
            "node_id",
            "id",
            "node_type",
            "type",
            "labels",
            "score",
            "created_at",
        ]
        out: Dict[str, Any] = {}
        for k in candidates:
            if k in node:
                out[k] = _safe_scalar(node[k])
        # include any small scalar properties under 'attrs' if present
        if "attrs" in node and isinstance(node["attrs"], dict):
            out["attrs"] = _safe_scalar(node["attrs"])
        return out
    # fallback: represent opaque nodes by string
    return {"id": _safe_scalar(node)}


def safe_bsg_view(group: Any) -> Dict[str, Any]:
    """Project a behavioral group into the canonical BSG projection shape.

    The function is conservative: it only copies a permitted set of keys
    and applies scalar-safety to avoid embedding full graph objects.
    """
    allowed_top_keys = [
        "group_id",
        "group_type",
        "confidence",
        "evidence_level",
        "rationale",
        "session_stats",
        "network_characteristics",
        "temporal_bounds",
        "negative_assertions",
    ]

    out: Dict[str, Any] = {}
    if group is None:
        return out
    if isinstance(group, dict):
        for k in allowed_top_keys:
            if k in group:
                out[k] = _safe_scalar(group[k])

        # normalize temporal bounds to ISO where possible
        if "temporal_bounds" in out and isinstance(out["temporal_bounds"], dict):
            tb = out["temporal_bounds"]
            for tkey in ("first_seen", "last_seen"):
                if tkey in tb:
                    tb[tkey] = _iso(tb[tkey])
            out["temporal_bounds"] = tb

        # ensure some defaults
        out.setdefault("confidence", 0.0)
        out.setdefault("evidence_level", "UNKNOWN")
        if "negative_assertions" not in out:
            out["negative_assertions"] = [
                "No payload inspection performed",
                "No destination attribution performed",
            ]
        return out
    # if group is not a dict, return an id-only projection
    return {"group_id": _safe_scalar(group)}


def generate_bsg_projection(
    instance_id: str,
    groups: Iterable[Any],
    sessions_total: Optional[int] = None,
    sessions_grouped: Optional[int] = None,
    groups_total: Optional[int] = None,
    coverage_pct: Optional[float] = None,
    generated_at: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Build the top-level BSG projection envelope.

    `groups` should be an iterable of dict-like group objects; each will be
    projected with `safe_bsg_view`.
    """
    gen = generated_at or datetime.datetime.now(datetime.timezone.utc)
    g_list = [safe_bsg_view(g) for g in groups]

    # best-effort evidence summary
    evidence_summary: Dict[str, Any] = {
        "sessions_total": sessions_total or 0,
        "sessions_grouped": sessions_grouped or 0,
        "groups_total": groups_total if groups_total is not None else len(g_list),
        "coverage_pct": coverage_pct if coverage_pct is not None else 0.0,
    }

    envelope = {
        "bsg_projection_version": "1.0",
        "instance_id": instance_id,
        "generated_at": _iso(gen),
        "evidence_summary": evidence_summary,
        "groups": g_list,
        "constraints": {
            "no_geo_inference": True,
            "no_actor_attribution": True,
            "no_intent_certainty": True,
        },
    }
    return envelope


def to_json(obj: Any, *, indent: int = 2) -> str:
    return json.dumps(obj, indent=indent, sort_keys=False, default=str)


if __name__ == "__main__":
    # minimal CLI for quick sanity checks
    import sys

    if len(sys.argv) < 3:
        print("Usage: python bsg_projection.py <instance_id> <groups.json>")
        sys.exit(2)

    instance = sys.argv[1]
    fn = sys.argv[2]
    with open(fn, "r") as f:
        groups = json.load(f)
    proj = generate_bsg_projection(instance, groups)
    print(to_json(proj))
