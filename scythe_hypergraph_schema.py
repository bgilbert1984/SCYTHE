"""SCYTHE Hypergraph Schema v1.0 — lineage projection for RF solver output.

Maps a contract-backed ``RFSolverEvidence`` sample (see rf_solver_evidence.py)
onto the schema's node and hyperedge vocabulary so every solver result is
traceable to an environment, variant, device and observation source.

What this module deliberately does NOT do:
  * It never invents geometry or materials.  The NTIA ITM area mode is a
    terrain-profile model; it has no faces, regions or material assignments, so
    the SolverRunHyperedge carries empty ``geometry``/``materials`` lists plus an
    explicit ``geometry_model``/``material_model`` declaration.
  * It never claims observation support.  ObservationSupportsEntity edges are
    emitted only for observation IDs the caller passes in.  Otherwise constraint
    C4 is satisfied by a *declared synthetic source*, which is stated plainly.

The output is pure data (dicts); nothing here mutates the live graph.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = "1.0"

_ID_RE = re.compile(r"^[a-z_]+:[A-Za-z0-9_.\-:]+$")
_SAFE_DATASET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_VERSION_SUFFIX_RE = re.compile(r"-v\d+$")

DATASETS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasets")


# --------------------------------------------------------------------------
# Deterministic IDs (immutable, no runtime state) — same convention as graph_ids
# --------------------------------------------------------------------------

def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", str(text).strip()).strip("_") or "unknown"


def environment_id(bounds: Optional[Iterable[float]], fallback: str) -> str:
    """One physical location = one EnvironmentNode.

    Derived from the rounded bounding box so it is independent of solver, dataset
    version and run.  Falls back to the dataset id (minus version suffix).
    """
    if bounds:
        b = [round(float(v), 4) for v in bounds]
        digest = hashlib.sha256(json.dumps(b).encode()).hexdigest()[:8]
        return f"env:bbox_{digest}"
    return f"env:{_slug(_VERSION_SUFFIX_RE.sub('', fallback))}"


def variant_id(env_node_id: str, label: str) -> str:
    return f"variant:{env_node_id.split(':', 1)[1]}.{_slug(label)}"


def device_id(transmitter_id: str) -> str:
    return f"device:{_slug(transmitter_id)}"


def signal_id(evidence_id: str) -> str:
    return f"signal:{_slug(evidence_id)}"


def observation_id(raw: str) -> str:
    raw = str(raw)
    return raw if raw.startswith("observation:") else f"observation:{_slug(raw)}"


def solver_run_edge_id(run_id: str, evidence_id: str) -> str:
    return f"edge:solver_run:{_slug(run_id)}:{_slug(evidence_id)}"


# --------------------------------------------------------------------------
# Dataset context (best effort; absence degrades to evidence-only lineage)
# --------------------------------------------------------------------------

def load_dataset_context(dataset_id: str, root: str = DATASETS_ROOT) -> Dict[str, Any]:
    """Load manifest.json and solver-inputs.json for a dataset, if present."""
    if not _SAFE_DATASET_RE.match(dataset_id or ""):
        return {}
    base = os.path.join(root, dataset_id)
    ctx: Dict[str, Any] = {}
    for key, name in (("manifest", "manifest.json"), ("inputs", "solver-inputs.json")):
        path = os.path.join(base, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                ctx[key] = json.load(fh)
        except (OSError, ValueError):
            continue
    return ctx


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

def build_solver_run_lineage(
    evidence: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    observation_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Project one solver evidence sample into schema-v1 nodes + hyperedges.

    Returns ``{schema_version, nodes, hyperedges, violations}``.  ``violations``
    is the output of :func:`validate_lineage` and is empty when C3/C4 hold.
    """
    context = context if context is not None else load_dataset_context(
        str(evidence.get("dataset_id", "")))
    manifest = context.get("manifest") or {}
    inputs = context.get("inputs") or {}
    prov = dict(evidence.get("provenance") or {})
    authority = manifest.get("authority") or {}
    spatial = manifest.get("spatialReference") or {}
    rf = (manifest.get("physics") or {}).get("rf") or {}
    temporal = manifest.get("temporal") or {}

    dataset = str(evidence["dataset_id"])
    created_at = evidence.get("sampled_at")

    env_nid = environment_id(spatial.get("boundsDegrees"), dataset)
    env = {
        "node_id": env_nid, "type": "EnvironmentNode",
        "env_id": env_nid.split(":", 1)[1],
        "geographic_extent": {
            "bounds_degrees": spatial.get("boundsDegrees"),
            "crs": spatial.get("horizontalCrs"),
        },
        "canonical_variant": None,  # filled below
    }

    t_pct = temporal.get("statisticalTimePercentage")
    variant_label = f"itm_t{int(t_pct) if t_pct is not None else 'unknown'}"
    var_nid = variant_id(env_nid, variant_label)
    env["canonical_variant"] = var_nid
    variant = {
        "node_id": var_nid, "type": "VariantNode",
        "variant_id": var_nid.split(":", 1)[1],
        "environment_id": env_nid,  # belongs to exactly one EnvironmentNode
        "lighting_state": "not_applicable",
        "weather_state": rf.get("atmosphericModel") or "unspecified",
        "time_semantics": temporal.get("timeSemantics"),
        "statistical_time_percentage": t_pct,
    }

    tx = inputs.get("transmitter") or {}
    dev_nid = device_id(evidence.get("transmitter_id") or tx.get("id") or "unknown")
    device = {
        "node_id": dev_nid, "type": "DeviceNode",
        "device_id": dev_nid.split(":", 1)[1],
        "device_class": "RF_TRANSMITTER",
        "position": ({"longitude_degrees": tx.get("longitudeDegrees"),
                      "latitude_degrees": tx.get("latitudeDegrees"),
                      "height_m": tx.get("heightMeters")} if tx else None),
    }

    sig_nid = signal_id(evidence["evidence_id"])
    signal = {
        "node_id": sig_nid, "type": "SignalNode",
        "frequency": evidence.get("frequency_hz"),
        "timestamp": created_at,
        "signal_source": f"SOLVER:{prov.get('solverName', 'unknown')}",
        "evidence_id": evidence["evidence_id"],
        "quantity": evidence.get("quantity"), "value": evidence.get("value"),
        "units": evidence.get("units"),
        "location": {"longitude_degrees": evidence.get("longitude_degrees"),
                     "latitude_degrees": evidence.get("latitude_degrees"),
                     "height_m": evidence.get("height_meters")},
    }

    obs_ids = [observation_id(o) for o in (observation_ids or [])]
    observations = [{
        "node_id": o, "type": "ObservationNode", "immutable": True,
    } for o in obs_ids]

    run_id = str(prov.get("runId", ""))
    source = {
        "kind": "SYNTHETIC_MODEL",
        "declared": True,
        "solver": prov.get("solverName"),
        "solver_version": prov.get("solverVersion") or authority.get("solverVersion"),
        "source_revision": prov.get("sourceRevision") or authority.get("sourceRevision"),
        "input_hashes": authority.get("inputHashes") or [],
        "observation_ids": obs_ids,
    }
    run_edge = {
        "edge_id": solver_run_edge_id(run_id, evidence["evidence_id"]),
        "type": "SolverRunHyperedge",
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at,
        "run_id": run_id,
        "variant_id": var_nid,
        "environment_id": env_nid,
        "geometry": [],
        "geometry_model": "TERRAIN_PROFILE",
        "materials": [],
        "material_model": "ITM_CLIMATE_CONSTANTS",
        "devices": [dev_nid],
        "signals": [sig_nid],
        "source": source,
    }

    hyperedges: List[Dict[str, Any]] = [run_edge]
    for o in obs_ids:
        hyperedges.append({
            "edge_id": f"edge:observation_supports:{_slug(o)}:{_slug(sig_nid)}",
            "type": "ObservationSupportsEntity",
            "schema_version": SCHEMA_VERSION,
            "created_at": created_at,
            "observation_id": o,
            "entity_id": sig_nid,
        })

    nodes = [env, variant, device, signal] + observations
    for n in nodes:
        n.setdefault("schema_version", SCHEMA_VERSION)
        n.setdefault("created_at", created_at)

    result = {"schema_version": SCHEMA_VERSION, "nodes": nodes,
              "hyperedges": hyperedges, "violations": []}
    result["violations"] = validate_lineage(result)
    return result


# --------------------------------------------------------------------------
# Validation of the deterministic constraints that apply to a solver run
# --------------------------------------------------------------------------

def validate_lineage(lineage: Dict[str, Any]) -> List[str]:
    """Check ID rules, C3 (variant reference) and C4 (traceable source)."""
    problems: List[str] = []
    nodes = {n.get("node_id"): n for n in lineage.get("nodes", [])}
    if len(nodes) != len(lineage.get("nodes", [])):
        problems.append("ID: duplicate node_id")
    for nid in nodes:
        if not isinstance(nid, str) or not _ID_RE.match(nid):
            problems.append(f"ID: malformed node_id {nid!r}")

    for edge in lineage.get("hyperedges", []):
        eid = edge.get("edge_id", "?")
        if edge.get("type") != "SolverRunHyperedge":
            continue
        vid = edge.get("variant_id")
        variant = nodes.get(vid)
        if not variant or variant.get("type") != "VariantNode":
            problems.append(f"C3: {eid} does not reference an existing VariantNode")
        elif variant.get("environment_id") != edge.get("environment_id"):
            problems.append(f"C3: {eid} variant does not belong to the edge environment")
        for sid in edge.get("signals", []):
            if sid not in nodes:
                problems.append(f"C3: {eid} references missing signal {sid}")
        src = edge.get("source") or {}
        supported = {e["entity_id"] for e in lineage.get("hyperedges", [])
                     if e.get("type") == "ObservationSupportsEntity"}
        has_obs = any(s in supported for s in edge.get("signals", []))
        if not has_obs and not (src.get("declared") and src.get("kind") == "SYNTHETIC_MODEL"):
            problems.append(f"C4: {eid} has neither observation support nor a declared synthetic source")
        if edge.get("geometry") == [] and not edge.get("geometry_model"):
            problems.append(f"C4: {eid} has empty geometry without a declared geometry_model")
    return problems


def summarize_lineage(lineage: Dict[str, Any]) -> str:
    """One-screen, operator-readable lineage trace."""
    edge = next((e for e in lineage["hyperedges"] if e["type"] == "SolverRunHyperedge"), None)
    if not edge:
        return "LINEAGE // NONE"
    src = edge["source"]
    lines = [
        f"LINEAGE // SCHEMA v{lineage['schema_version']}",
        f"ENVIRONMENT // {edge['environment_id']}",
        f"VARIANT // {edge['variant_id']}",
        f"DEVICE // {', '.join(edge['devices'])}",
        f"GEOMETRY // {len(edge['geometry'])} faces // MODEL {edge['geometry_model']}",
        f"MATERIALS // {len(edge['materials'])} assigned // MODEL {edge['material_model']}",
        f"SOURCE // {src['kind']} // {src.get('solver')} {src.get('solver_version') or ''}".rstrip(),
        f"OBSERVATION SUPPORT // {len(src['observation_ids'])} "
        + ("(none: model output, not a measurement)" if not src["observation_ids"] else "linked"),
        f"CONSTRAINTS // {'OK' if not lineage['violations'] else '; '.join(lineage['violations'])}",
    ]
    return "\n".join(lines)
