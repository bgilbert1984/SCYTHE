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

Observation store wiring: measured ``RFObservation`` records (rf_bridge) become
immutable ObservationNodes.  They carry no sensor location, so a link can only
assert *frequency coincidence*, never that the measurement verifies the modelled
value; every such edge says so (``basis`` / ``verified: false``).  Observations
whose source is operator-synthetic are typed test input, not measurements, and
are never linked.

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
# Observation store wiring
# --------------------------------------------------------------------------

SYNTHETIC_OBSERVATION_SOURCES = frozenset({"operator_synthetic"})
BASIS_EXPLICIT = "EXPLICIT_CALLER_LINK"
BASIS_FREQUENCY = "FREQUENCY_COINCIDENT_ONLY"


def is_measured_observation(obs: Dict[str, Any]) -> bool:
    """True for evidence that came off a sensor rather than from an operator."""
    return (str(obs.get("source", "")).lower() not in SYNTHETIC_OBSERVATION_SOURCES
            and obs.get("evidence_class", "OBSERVED") == "OBSERVED")


def observation_node(obs: Dict[str, Any]) -> Dict[str, Any]:
    """Immutable ObservationNode for one stored RFObservation dict."""
    nid = observation_id(obs["evidence_id"])
    return {
        "node_id": nid, "type": "ObservationNode", "immutable": True,
        "observation_source": obs.get("source"),
        "evidence_class": obs.get("evidence_class"),
        "sensor_id": obs.get("sensor_id"),
        "observed_at": obs.get("observed_at"),
        "peak_frequency_hz": obs.get("peak_frequency_hz"),
        "center_frequency_hz": obs.get("center_frequency_hz"),
        "snr_db": obs.get("snr_db"),
        "mission_id": obs.get("mission_id"),
        "has_sensor_location": False,  # RFObservation carries none
    }


def resolve_observations(store: Any, evidence_ids: Iterable[str]) -> Dict[str, List[Any]]:
    """Look explicit IDs up in an observation store.

    Returns ``{"found": [obs...], "missing": [id...], "excluded": [id...]}``.
    Unknown IDs are reported, never turned into bare nodes; synthetic ones are
    excluded rather than treated as evidence.
    """
    wanted = [str(i).removeprefix("observation:") for i in evidence_ids]
    by_id = {o["evidence_id"]: o for o in store.query(limit=500)}
    found, missing, excluded = [], [], []
    for eid in dict.fromkeys(wanted):
        obs = by_id.get(eid)
        if obs is None:
            missing.append(eid)
        elif not is_measured_observation(obs):
            excluded.append(eid)
        else:
            found.append(obs)
    return {"found": found, "missing": missing, "excluded": excluded}


def discover_frequency_candidates(
    store: Any, evidence: Dict[str, Any], *, window_s: float = 3600.0,
    tolerance_hz: float = 25_000.0, limit: int = 20,
) -> List[Dict[str, Any]]:
    """Measured observations near the solver frequency and time.

    Frequency (and optionally time) coincidence only; used with
    ``basis=FREQUENCY_COINCIDENT_ONLY``.
    """
    freq = float(evidence["frequency_hz"])
    at = evidence.get("sampled_at")
    window_s = max(float(window_s), 0.0)
    since = (float(at) - window_s) if (at is not None and window_s) else None
    until = (float(at) + window_s) if (at is not None and window_s) else None
    items = store.query(frequency_hz=freq, tolerance_hz=tolerance_hz,
                        since=since, until=until, limit=max(1, min(int(limit), 100)) * 3)
    return [o for o in items if is_measured_observation(o)][:max(1, min(int(limit), 100))]


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

def build_solver_run_lineage(
    evidence: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    observation_ids: Optional[Iterable[str]] = None,
    observations: Optional[Iterable[Dict[str, Any]]] = None,
    observation_basis: str = BASIS_EXPLICIT,
    observation_links: Optional[Iterable[Any]] = None,
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

    obs_nodes: Dict[str, Dict[str, Any]] = {}
    obs_basis: Dict[str, str] = {}

    def _add(node: Dict[str, Any], basis: str) -> None:
        if node["node_id"] not in obs_nodes:
            obs_nodes[node["node_id"]] = node
            obs_basis[node["node_id"]] = basis

    for o, basis in list(observation_links or []) + [(o, observation_basis) for o in (observations or [])]:
        if is_measured_observation(o):
            _add(observation_node(o), basis)
    for raw in (observation_ids or []):
        nid = observation_id(raw)
        _add({"node_id": nid, "type": "ObservationNode", "immutable": True}, observation_basis)
    obs_ids = list(obs_nodes)
    observation_list = list(obs_nodes.values())

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
            "basis": obs_basis[o],
            "verified": False,
        })

    nodes = [env, variant, device, signal] + observation_list
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
        if edge.get("type") == "ObservationSupportsEntity":
            if nodes.get(edge.get("observation_id"), {}).get("type") != "ObservationNode":
                problems.append(f"C4: {eid} references a missing ObservationNode")
            if edge.get("entity_id") not in nodes:
                problems.append(f"C4: {eid} references a missing entity")
            if edge.get("verified") is not False:
                problems.append(f"C4: {eid} claims verification the schema cannot establish")
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
        f"OBSERVATION LINKS // {len(src['observation_ids'])} "
        + ("(none: model output, not a measurement)" if not src["observation_ids"]
           else "(" + ", ".join(sorted({e.get("basis", "?") for e in lineage["hyperedges"]
                                        if e["type"] == "ObservationSupportsEntity"}))
           + "; UNVERIFIED against the model value)"),
        f"CONSTRAINTS // {'OK' if not lineage['violations'] else '; '.join(lineage['violations'])}",
    ]
    return "\n".join(lines)
