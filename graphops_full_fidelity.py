"""Bounded, exact-evidence capsules for explicit GraphOps Cloud analysis."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib import error, request


_CLOUD_ENDPOINT = "https://ollama.com"
_DEFAULT_MODEL = "gpt-oss:20b"
_DEFAULT_CLOUD_TIMEOUT_SECONDS = 75
_DEFAULT_CLOUD_MAX_TOKENS = 1200
_SECRET_KEY = re.compile(
    r"(?:authorization|password|passwd|secret|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|cookie|set-cookie|credential|environment|packet[_-]?payload|raw[_-]?packet)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\b(?:access[_-]?token|api[_-]?key|password|passwd|secret)\s*[=:]\s*[^\s&]+|"
    r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


class OllamaCloudTimeoutError(RuntimeError):
    """The disclosed request reached Ollama Cloud but generation never started in time."""

    def __init__(self, timeout_seconds: int):
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Ollama Cloud did not start a response within {timeout_seconds} seconds; "
            "the provider chat queue or model is unavailable. No automatic model retry was attempted."
        )


def _bounded_environment_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def load_ollama_api_key() -> Optional[str]:
    """Load the credential without placing it in argv, responses, or logs."""
    direct = os.environ.get("OLLAMA_API_KEY", "").strip()
    if direct:
        return direct
    path = os.environ.get("OLLAMA_API_KEY_FILE", "").strip()
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as handle:
        contents = handle.read(16_385)
    if len(contents) > 16_384:
        raise ValueError("Ollama credential file exceeds 16 KiB")
    for line in contents.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if "=" in candidate:
            name, value = candidate.split("=", 1)
            if name.strip() not in {"OLLAMA_API_KEY", "API"}:
                continue
            candidate = value.strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "\"'":
            candidate = candidate[1:-1]
        if candidate:
            return candidate
    return None


def _safe_mapping(value: Any) -> Any:
    """Remove secret-bearing/raw fields while retaining exact analytic values."""
    if isinstance(value, dict):
        return {str(key): _safe_mapping(item) for key, item in value.items()
                if not _SECRET_KEY.search(str(key))}
    if isinstance(value, list):
        return [_safe_mapping(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_mapping(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[EXCLUDED_SECRET]", value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _entity_frame(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Retain exact graph facts but deliberately omit engine-internal metadata."""
    return _safe_mapping({key: entity.get(key) for key in
                          ("id", "kind", "labels", "enrichment", "display", "source", "target")
                          if entity.get(key) is not None})


def _trace_frame(trace: Dict[str, Any]) -> Dict[str, Any]:
    probe = trace.get("probe") or {}
    traceroute = trace.get("traceroute") or {}
    hops = []
    for hop in list(traceroute.get("hops") or [])[:30]:
        hops.append(_safe_mapping({key: hop.get(key) for key in (
            "hop", "ip", "rtt_ms", "lat", "lon", "geo", "anomaly", "physics_anomaly",
            "anomaly_evidence_class", "anomaly_interpretation", "hostname", "asn", "org", "prefix",
        ) if hop.get(key) is not None}))
    return {
        "evidenceId": trace.get("evidenceId"),
        "target": trace.get("target"),
        "capturedAt": trace.get("capturedAt"),
        "probe": _safe_mapping({key: probe.get(key) for key in (
            "status", "rtt_avg_ms", "rtt_ms", "rtt_min_ms", "rtt_max_ms", "packet_loss",
            "count", "tool_used", "reason",
        ) if probe.get(key) is not None}),
        "traceroute": _safe_mapping({
            "status": traceroute.get("status"),
            "tool_used": traceroute.get("tool_used"),
            "simulated": traceroute.get("simulated", False),
            "hops": hops,
        }),
        "evidenceClasses": _safe_mapping(trace.get("evidenceClasses") or {}),
        "measurementSummary": _safe_mapping(trace.get("measurementSummary") or {}),
        "boundary": trace.get("boundary"),
        "rawPacketsExposed": False,
    }


def evaluate_evidence_compatibility(question: str, infrastructure: Optional[Dict[str, Any]] = None,
                                    flow_evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Declare whether this capsule contains the evidence classes a question requires."""
    lower = question.lower()
    requirements = []
    checks = (
        (("stale", "freshness", "claim time", "analysis window"), "TEMPORAL_FRESHNESS",
         "claim timestamps, source freshness, and an explicit analysis window"),
        (("absence", "missing evidence", "inference made from absence"), "SENSOR_NEGATIVE_EVIDENCE",
         "sensor capability, active status, coverage, position, and temporal alignment"),
        (("quantization", "quantized"), "QUANTIZATION_PROVENANCE",
         "encoding scale/offset and neighboring authoritative raw samples"),
        (("interpolation", "interpolated"), "INTERPOLATION_PROVENANCE",
         "interpolation algorithm and neighboring authoritative raw samples"),
        (("infrastructure", "cable"), "INFRASTRUCTURE",
         "observed flows plus inference/model provenance"),
        (("bgp", "ris", "control-plane", "control plane", "as path"), "CONTROL_PLANE",
         "prefix-relevant RIS messages with collector vantage and timestamps"),
        (("peeringdb", "exchange point", " ix ", "facility", "peering policy"), "PEERINGDB_DECLARED",
         "ASN-scoped, versioned PeeringDB declarations"),
        (("caida", "as relationship"), "CAIDA_RELATIONSHIPS",
         "versioned CAIDA relationship research inference"),
        (("contradiction", "source disagreement", "evidence tension", "origin change"),
         "INFRASTRUCTURE_CONTRADICTIONS",
         "revision-pinned contradiction findings, alternatives, falsifiers, and withheld tests"),
        (("activity", "application", "packet", "dissection", "protocol", "malicious"),
         "FLOW_DISSECTION",
         "allow-listed decoded flow fields with counters, direction, and capture provenance"),
        (("cadence", "sequence", "temporal dissection", "event timing"),
         "FLOW_TEMPORAL_DISSECTION",
         "at least two ordered decoded events inside the bounded temporal ring"),
    )
    for keywords, name, needed in checks:
        if any(keyword in lower for keyword in keywords):
            requirements.append({"class": name, "needed": needed})
    available = {"PINNED_GRAPH_ENTITY"}
    if flow_evidence:
        available.add("FLOW_TRANSPORT_SUMMARY")
        if flow_evidence.get("packetDissections"):
            available.add("FLOW_DISSECTION")
        if len(flow_evidence.get("packetDissections") or []) >= 2:
            available.add("FLOW_TEMPORAL_DISSECTION")
    else:
        available.add("HOST_TRACE")
    if infrastructure and infrastructure.get("schemaVersion") == "graphops.infrastructure.v1":
        available.add("INFRASTRUCTURE")
        if (infrastructure.get("peeringdbEvidence") or {}).get("networks"):
            available.add("PEERINGDB_DECLARED")
        if (infrastructure.get("controlPlaneEvidence") or {}).get("controlPlanePaths"):
            available.add("CONTROL_PLANE")
        if (infrastructure.get("infrastructureContradictions") or {}).get("schemaVersion") == \
                "graphops.infrastructure-contradictions.v1":
            available.add("INFRASTRUCTURE_CONTRADICTIONS")
    # A host trace has capture time, but it does not establish source freshness or an analysis window.
    missing = [item for item in requirements if item["class"] not in available]
    return {"compatible": not missing, "required": requirements, "available": sorted(available),
            "missing": missing,
            "boundary": "FULL FIDELITY PRESERVES VALUES; IT DOES NOT CREATE ABSENT EVIDENCE"}


def _origin_asns(value: Any) -> set[int]:
    values = value if isinstance(value, list) else [value]; result = set()
    for item in values:
        if isinstance(item, list): result.update(_origin_asns(item))
        else:
            try: result.add(int(item))
            except (TypeError, ValueError): pass
    return result


def _prefix_overlaps(value: Any, prefixes: list[str]) -> bool:
    try: observed = ipaddress.ip_network(str(value), strict=False)
    except ValueError: return False
    for prefix in prefixes:
        try: scoped = ipaddress.ip_network(str(prefix), strict=False)
        except ValueError: continue
        if observed.version == scoped.version and observed.overlaps(scoped): return True
    return False


def _focused_infrastructure_frame(infrastructure: Optional[Dict[str, Any]],
                                  selection: Dict[str, Any]) -> Dict[str, Any]:
    """Retain exact selection-relevant records with an auditable omission receipt."""
    source = infrastructure or {"status": "unavailable", "domains": [], "observedFlows": []}
    entity_id = str(selection.get("entityId") or "")
    all_domains = list(source.get("domains") or [])
    focus_domains = [row for row in all_domains if entity_id in (row.get("observedHostIds") or [])]
    focus_ids = {row.get("id") for row in focus_domains if row.get("id")}
    all_flows = list(source.get("observedFlows") or [])
    incident_flows = [row for row in all_flows if not focus_ids or
                      focus_ids.intersection({row.get("sourceDomain"), row.get("targetDomain")})][:32]
    relevant_ids = set(focus_ids)
    for row in incident_flows:
        relevant_ids.update(filter(None, (row.get("sourceDomain"), row.get("targetDomain"))))
    domains = ([row for row in all_domains if row.get("id") in relevant_ids] if relevant_ids
               else all_domains[:16])[:16]
    relevant_asns = {int(row["asn"]) for row in domains if row.get("asn") is not None}
    focus_prefixes = [prefix for row in (focus_domains or domains) for prefix in row.get("prefixes") or []]

    pdb_source = source.get("peeringdbEvidence") or {}
    networks = [row for row in pdb_source.get("networks") or []
                if not relevant_asns or int(row.get("asn") or 0) in relevant_asns][:16]
    ix_memberships = [row for row in pdb_source.get("ixMemberships") or []
                      if not relevant_asns or int(row.get("asn") or 0) in relevant_asns][:32]
    facility_presences = [row for row in pdb_source.get("facilityPresences") or []
                          if not relevant_asns or int(row.get("asn") or 0) in relevant_asns][:32]
    ix_ids = {row.get("ix_id") for row in ix_memberships}; facility_ids = {
        row.get("fac_id") for row in facility_presences}
    exchanges = [row for row in pdb_source.get("exchanges") or [] if row.get("id") in ix_ids][:32]
    facilities = [row for row in pdb_source.get("facilities") or [] if row.get("id") in facility_ids][:32]
    pdb = {key: pdb_source.get(key) for key in ("status", "schemaVersion", "datasetRevision",
           "retrievedAt", "retrievedEpoch", "recordUpdatedThrough", "provenance", "boundary", "scope")
           if pdb_source.get(key) is not None}
    pdb.update({"networks": networks, "ixMemberships": ix_memberships,
                "facilityPresences": facility_presences, "exchanges": exchanges,
                "facilities": facilities})

    ris_source = source.get("controlPlaneEvidence") or {}
    all_paths = list(ris_source.get("controlPlanePaths") or [])
    relevant_paths = [row for row in all_paths if
                      _origin_asns(row.get("originAsn")).intersection(relevant_asns) or
                      _prefix_overlaps(row.get("prefix"), focus_prefixes)]
    # Never fill a focused capsule with unrelated control-plane observations.
    # Absence of a relevant row remains absence, not permission to sample globally.
    paths = relevant_paths[-32:]
    ris = {key: ris_source.get(key) for key in ("status", "schemaVersion", "snapshotRevision",
           "capturedAt", "observationWindow", "scope", "collectors", "provenance", "boundary")
           if ris_source.get(key) is not None}
    ris["controlPlanePaths"] = paths

    contradiction_source = source.get("infrastructureContradictions") or {}
    contradictions = {key: contradiction_source.get(key) for key in ("status", "schemaVersion",
        "revision", "capturedAt", "window", "sourceRevisions", "boundary")
        if contradiction_source.get(key) is not None}
    contradictions["findings"] = [row for row in contradiction_source.get("findings") or []
        if not relevant_ids or row.get("subject") in relevant_ids][:32]
    path_prefixes = {row.get("prefix") for row in paths}
    contradictions["changes"] = ([row for row in contradiction_source.get("changes") or []
        if row.get("prefix") in path_prefixes][:32] if path_prefixes else [])
    contradictions["withheld"] = list(contradiction_source.get("withheld") or [])[:16]

    canonical_source = json.dumps(_safe_mapping(source), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    source_counts = {"domains": len(all_domains), "observedFlows": len(all_flows),
        "peeringdbNetworks": len(pdb_source.get("networks") or []),
        "ixMemberships": len(pdb_source.get("ixMemberships") or []),
        "facilityPresences": len(pdb_source.get("facilityPresences") or []),
        "facilities": len(pdb_source.get("facilities") or []),
        "exchanges": len(pdb_source.get("exchanges") or []), "controlPlanePaths": len(all_paths),
        "contradictionFindings": len(contradiction_source.get("findings") or []),
        "controlPlaneChanges": len(contradiction_source.get("changes") or [])}
    included_counts = {"domains": len(domains), "observedFlows": len(incident_flows),
        "peeringdbNetworks": len(networks), "ixMemberships": len(ix_memberships),
        "facilityPresences": len(facility_presences), "facilities": len(facilities),
        "exchanges": len(exchanges), "controlPlanePaths": len(paths),
        "contradictionFindings": len(contradictions["findings"]),
        "controlPlaneChanges": len(contradictions["changes"])}
    return _safe_mapping({key: source.get(key) for key in ("status", "schemaVersion", "graphRevision",
        "capturedAt", "focus", "authority", "sourceFreshness", "referenceCatalog", "boundary", "bounded")
        if source.get(key) is not None} | {"domains": domains, "observedFlows": incident_flows,
        "modeledPathCandidates": list(source.get("modeledPathCandidates") or [])[:16],
        "declaredSharedIxCandidates": list(source.get("declaredSharedIxCandidates") or [])[:16],
        "peeringdbEvidence": pdb, "controlPlaneEvidence": ris,
        "infrastructureContradictions": contradictions, "capsuleProjection": {
            "mode": "SELECTION_FOCUSED_EXACT_RECORDS", "selectedEntity": entity_id,
            "sourceSnapshotSha256": hashlib.sha256(canonical_source.encode()).hexdigest(),
            "sourceCounts": source_counts, "includedCounts": included_counts,
            "omittedCounts": {key: max(source_counts[key] - included_counts[key], 0)
                              for key in source_counts},
            "boundary": "INCLUDED RECORDS RETAIN EXACT VALUES; OMITTED ENVIRONMENT RECORDS ARE COUNTED AND HASH-BOUND, NOT SUMMARIZED OR DISCLOSED",
        }})


def build_full_fidelity_capsule(question: str, selection: Dict[str, Any],
                                resolved: Dict[str, Any], trace: Dict[str, Any],
                                infrastructure: Optional[Dict[str, Any]] = None,
                                flow_evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a deterministic-content, exact-value capsule from server-owned evidence."""
    entity = resolved.get("node") or resolved.get("edge") or {}
    capsule = {
        "schemaVersion": "graphops.full-fidelity.v2",
        "capsuleId": f"ffc-{uuid.uuid4().hex}",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "operatorQuestion": _safe_mapping(question),
        "selection": {
            "kind": selection.get("kind"),
            "entityId": selection.get("entityId"),
            "graphRevision": resolved.get("graphRevision") or selection.get("graphRevision"),
        },
        "selectedEntity": _entity_frame(entity),
        "incidentEdges": [_entity_frame(item) for item in
                          list(resolved.get("incidentEdges") or [])[:24]],
        "memberNodes": [_entity_frame(item) for item in
                        list(resolved.get("memberNodes") or [])[:24]],
        "hostTrace": _trace_frame(trace) if trace else None,
        "flowEvidence": _safe_mapping(flow_evidence) if flow_evidence else None,
        "infrastructureEvidence": _focused_infrastructure_frame(infrastructure, selection),
        "authority": {
            "graph": "REVISION_PINNED_SERVER_RESOLVED",
            "target": "OBSERVED" if trace else None,
            "routeAndRtt": (trace.get("evidenceClasses") or {}),
            "flowDissection": ((flow_evidence or {}).get("flow") or {}).get("evidenceClass", "UNAVAILABLE"),
            "model": "INTERPRETIVE_ONLY",
        },
        "exclusions": [
            "CREDENTIALS", "AUTHORIZATION_HEADERS", "COOKIES", "PROCESS_ENVIRONMENT",
            "RAW_PACKET_PAYLOADS", "UNRELATED_LOCAL_FILES", "DIRECTIVE_EXECUTION_AUTHORITY",
        ],
        "boundary": (
            "EXACT BOUNDED OPERATIONAL EVIDENCE IS DISCLOSED TO OLLAMA CLOUD; GEOIP REMAINS "
            "INFERRED; GRAPH ADJACENCY AND MODEL OUTPUT DO NOT ESTABLISH CAUSALITY"
        ),
    }
    capsule["evidenceCompatibility"] = evaluate_evidence_compatibility(
        question, capsule["infrastructureEvidence"], flow_evidence)
    canonical = json.dumps(capsule, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    capsule["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return capsule


def disclosure_receipt(capsule: Dict[str, Any], model: str) -> Dict[str, Any]:
    hops = (capsule.get("hostTrace") or {}).get("traceroute", {}).get("hops", [])
    addresses = {str(item.get("ip")) for item in hops if item.get("ip")}
    target = (capsule.get("hostTrace") or {}).get("target")
    if target:
        addresses.add(str(target))
    flow_evidence = capsule.get("flowEvidence") or {}
    for endpoint in (flow_evidence.get("flow") or {}).get("endpoints") or []:
        if endpoint.get("ip"):
            addresses.add(str(endpoint["ip"]))
    dissection_fields = sum(len(item.get("fields") or {}) for item in
                             flow_evidence.get("packetDissections") or [])
    locations = sum(1 for item in hops if item.get("geo") or
                    (item.get("lat") is not None and item.get("lon") is not None))
    locations += sum(1 for endpoint in (flow_evidence.get("flow") or {}).get("endpoints") or []
                     if (endpoint.get("geoip") or {}).get("latitude") is not None and
                     (endpoint.get("geoip") or {}).get("longitude") is not None)
    infrastructure = capsule.get("infrastructureEvidence") or {}
    projection = infrastructure.get("capsuleProjection") or {}
    return {
        "capsuleId": capsule["capsuleId"],
        "capsuleSha256": capsule["sha256"],
        "destination": "OLLAMA_CLOUD",
        "endpoint": _CLOUD_ENDPOINT,
        "model": model,
        "route": "OLLAMA_CLOUD_FULL_FIDELITY",
        "disclosed": {
            "exactIpAddresses": len(addresses),
            "exactLocations": locations,
            "exactTimestamps": True,
            "selectedEntities": 1,
            "incidentEdges": len(capsule.get("incidentEdges") or []),
            "memberNodes": len(capsule.get("memberNodes") or []),
            "infrastructureDomains": len(infrastructure.get("domains") or []),
            "observedInfrastructureFlows": len(infrastructure.get("observedFlows") or []),
            "modeledPathCandidates": len(infrastructure.get("modeledPathCandidates") or []),
            "peeringdbNetworks": len((infrastructure.get("peeringdbEvidence") or {}).get("networks") or []),
            "declaredIxMemberships": len((infrastructure.get("peeringdbEvidence") or {}).get("ixMemberships") or []),
            "controlPlaneObservations": len((infrastructure.get("controlPlaneEvidence") or {}).get("controlPlanePaths") or []),
            "infrastructureContradictions": len((infrastructure.get("infrastructureContradictions") or {}).get("findings") or []),
            "controlPlaneChanges": len((infrastructure.get("infrastructureContradictions") or {}).get("changes") or []),
            "withheldInfrastructureTests": len((infrastructure.get("infrastructureContradictions") or {}).get("withheld") or []),
            "packetDissections": len(flow_evidence.get("packetDissections") or []),
            "decodedPacketFields": dissection_fields,
            "temporalRingLimit": int((flow_evidence.get("temporalDissection") or {}).get("ringLimit") or 0),
            "temporalEventsOmitted": int((flow_evidence.get("temporalDissection") or {}).get("eventsOmittedBeforeRing") or 0),
            "rawPacketPayloads": 0,
        },
        "capsuleProjection": {key: projection.get(key) for key in
                              ("mode", "sourceSnapshotSha256", "sourceCounts", "includedCounts", "omittedCounts", "boundary")},
        "excluded": capsule["exclusions"],
        "modelAuthority": "INTERPRETIVE_ONLY",
        "directiveExecution": False,
    }


_SYSTEM_PROMPT = """You are GraphOps Cloud, an evidence-disciplined network analyst.
Analyze only the supplied full-fidelity evidence capsule. Exact IPs, timestamps, coordinates,
RTTs, and graph identities may be cited. Preserve every evidence class: MEASURED, OBSERVED,
INFERRED, SYNTHETIC, or UNAVAILABLE. Never describe GEOIP as physical device location, never
claim graph adjacency proves causality, and never invent missing measurements. Identify route
anomalies, distinguish measured latency from inferred geography, and give the single next
observation most capable of falsifying your interpretation.

ICMP RTTs at different TTLs are independent control-plane response samples, not additive
segment delays. A slower intermediate response followed by a faster destination is common
and does not establish a route change, load balancing, congestion, or misidentified target.
Physics anomalies are DERIVED_INFERENCE consistency warnings combining GeoIP with differential
ICMP RTT; they never prove physical distance, a long-haul leg, relay, or VPN. A city sequence
from interface GeoIP must never be narrated as a physical itinerary without independent
corroboration. RTT magnitude alone cannot classify a path as local, nearby, short-haul, or
long-haul. A traceroute's last responding hop is not evidence that the packet path ended there.
Do not infer the absence of a VPN, relay, distant leg, or route merely because it was not observed.
Use "could be consistent with" for hypotheses and name alternatives.

InfrastructureEvidence has strict partitions. observedFlows are observed graph traffic between
endpoints whose ASN ownership and locations remain INFERRED. modeledPathCandidates are reference-
model candidates, never observed BGP paths. Cesium arcs are DISPLAY_ONLY and never routes. Consult
evidenceCompatibility before answering: when compatible is false, identify the missing evidence
and refuse conclusions that require it. Full fidelity preserves disclosed values; it does not make
the capsule complete for every question.

PeeringDB evidence is self-reported declared infrastructure. Shared IX or facility presence does
not establish adjacency, traffic, or routing. RIS Live paths are CONTROL_PLANE_OBSERVATION at the
named collector vantage and are NON_AUTHORITATIVE for data-plane inference. Never merge either
source into the traceroute hop graph. CAIDA relationship evidence is absent unless explicitly
present with a dataset version and provenance. infrastructureContradictions contains deterministic
UNRESOLVED source disagreements or evidence tensions, not security verdicts. Preserve its alternatives,
falsifier, boundary, time window, source revisions, and withheld tests. Never translate an origin
disagreement into a route hijack claim or a collector withdrawal into global unreachability.
capsuleProjection declares the exact records retained for this selected investigation, the counts
omitted from the wider environment, and a SHA-256 binding to the complete source snapshot. Never
infer anything from an omitted record. "Full fidelity" means included values are exact; it does not
mean the entire unrelated environment was disclosed.

When flowEvidence is present, classify only what its allow-listed packetDissections, temporalDissection,
and flow counters
support. Transport tuples, byte/packet counters, DNS names, HTTP fields, TLS SNI/version/fingerprints,
and Suricata alert labels are OBSERVED decoded summaries at the named sensor boundary. They can support
candidate activity classes, but application purpose, user identity or intent, compromise, malware,
and maliciousness remain INFERRED. A port number alone is not an application identification. TLS SNI
does not reveal encrypted content. An alert signature is a sensor observation, not a verdict. Never
claim that absent decoded fields were absent on the wire. A zero directional counter describes only
the retained flow summary; it does not establish that no response, error, timeout, or retransmission
occurred outside that boundary. Suricata app_proto="failed" means decoder classification was
unsuccessful, not that the application or flow failed. Raw payloads and a complete packet sequence are
excluded. temporalDissection is an ordered tail ring of at most 32 decoded Eve summaries, not a complete
packet capture; reason about cadence only inside its declared window and account for omitted earlier events.
For 239.255.255.250:1900, SSDP is a strong protocol hypothesis, but normal unicast discovery
responses do not falsify it and an unrelated packet on another port is not a falsifier. Prefer a bounded
header/signature observation that can distinguish SSDP M-SEARCH/NOTIFY/HTTP response syntax from an
alternative protocol. State at least one benign and one adverse alternative when the evidence permits both.

Return one JSON object with exactly these string fields plus numeric confidence:
{"situation":"...","anomalies":"...","measuredVsInferred":"...","assessment":"...",
 "falsifier":"...","direction":"...","confidence":0.0}
Use capsule evidence paths such as hostTrace.traceroute.hops[3] inline when supporting a claim.
Confidence must reflect evidence coverage and cannot establish causality."""


_FORBIDDEN_ROUTE_ASSERTIONS = re.compile(
    r"\b(?:actually|likely|appears to)\s+(?:routes?|follows?|traverses?|crosses?|returns?)\b.{0,90}"
    r"\b(?:geoip|city|seattle|virginia|everett|location|long[- ]haul)\b|"
    r"\b(?:reveal|indicate|prove|confirm)s?\b.{0,80}\b(?:actual routing|physical (?:path|distance)|long[- ]haul link)\b",
    re.IGNORECASE | re.DOTALL,
)
_CONCRETE_OBSERVATION = re.compile(
    r"\b(?:repeat|run|collect|capture|measure|traceroute|mtr|ping|probe|compare|observe|resolve|query)\b",
    re.IGNORECASE,
)
_UNCORROBORATED_TIMING_CAUSE = re.compile(
    r"\b(?:spike|non[- ]monotonic|lower rtt|rtt variation).{0,100}"
    r"\b(?:indicates?|suggests?|reveals?|confirms?)\b.{0,80}"
    r"\b(?:congestion|routing change|load[- ]balanc|different path|misidentified)\b|"
    r"\b(?:congestion|routing change|load[- ]balanc|different path).{0,80}"
    r"\b(?:caused?|explains?|indicates?|suggests?)\b",
    re.IGNORECASE | re.DOTALL,
)
_RTT_DISTANCE_PROMOTION = re.compile(
    r"\b(?:rtt|latenc(?:y|ies)|milliseconds?|\bms\b).{0,100}"
    r"\b(?:indicat|suggest|imply|show|consistent with).{0,80}"
    r"\b(?:short[- ]haul|long[- ]haul|local path|nearby|physical distance|distant)\b|"
    r"\b(?:short[- ]haul|long[- ]haul|local path|nearby).{0,80}"
    r"\b(?:because|given|based on|from).{0,40}\b(?:rtt|latency)\b",
    re.IGNORECASE | re.DOTALL,
)
_PATH_END_PROMOTION = re.compile(
    r"\b(?:path|route)\s+(?:ends?|terminates?|stops?)\s+(?:at|with|there)|"
    r"\b(?:reaches?|enters?)\s+(?:an?\s+)?(?:amazon|comcast|isp|edge)\s+(?:edge|network|node)\b",
    re.IGNORECASE,
)
_ABSENCE_TOPOLOGY_PROMOTION = re.compile(
    r"\b(?:no|without|lacks?)\s+(?:direct\s+)?evidence\s+(?:of|for)\s+(?:an?\s+)?"
    r"(?:long[- ]haul|vpn|relay|tunnel|distant leg|route)|"
    r"\b(?:no|not)\s+(?:long[- ]haul|vpn|relay|tunnel)\b",
    re.IGNORECASE,
)
_FLOW_VERDICT_PROMOTION = re.compile(
    r"\b(?:this|the)\s+(?:flow|traffic|activity)\s+(?:is|was|confirms?|proves?|demonstrates?)\s+"
    r"(?:malicious|malware|command[- ]and[- ]control|c2|exfiltration|compromise)|"
    r"\b(?:confirms?|proves?|demonstrates?)\s+(?:maliciousness|malware|compromise|exfiltration)\b",
    re.IGNORECASE,
)
_FLOW_UNBOUNDED_ABSENCE = re.compile(
    r"\bno\s+(?:return traffic|responses?|errors?|retransmissions?|timeouts?)\b|"
    r"\bwithout\s+(?:return traffic|responses?|errors?|retransmissions?|timeouts?)\b",
    re.IGNORECASE,
)
_SSDP_WEAK_FALSIFIER = re.compile(
    r"\bdifferent\s+(?:destination\s+)?port\b|"
    r"\bresponse packet\b.{0,100}\b(?:challenge|falsif|contradict)\b|"
    r"\b(?:challenge|falsif|contradict)\b.{0,100}\bresponse packet\b",
    re.IGNORECASE | re.DOTALL,
)


def validate_cloud_report(report: Dict[str, Any], capsule: Dict[str, Any]) -> Dict[str, Any]:
    """Apply deterministic epistemic ceilings after model generation."""
    normalized = {key: str(report.get(key) or "").strip() for key in (
        "situation", "anomalies", "measuredVsInferred", "assessment", "falsifier", "direction")}
    if not all(normalized.values()):
        raise RuntimeError("Ollama Cloud report contains empty required findings")
    route = (capsule.get("hostTrace") or {}).get("evidenceClasses", {}).get("route")
    geography = (capsule.get("hostTrace") or {}).get("evidenceClasses", {}).get("geography")
    confidence = min(max(float(report.get("confidence")), 0.0), 1.0)
    constraints = []
    compatibility = capsule.get("evidenceCompatibility") or {}
    missing = compatibility.get("missing") or []
    if missing:
        names = ", ".join(str(item.get("class") or "UNKNOWN") for item in missing)
        details = "; ".join(str(item.get("needed") or "unspecified evidence") for item in missing)
        normalized["situation"] = f"QUESTION-EVIDENCE MISMATCH — required evidence is absent: {names}."
        normalized["anomalies"] = "No anomaly conclusion is supported for the missing evidence classes."
        normalized["measuredVsInferred"] = (
            "The capsule retains its host-trace and graph evidence, but those classes do not answer "
            f"the requested test. Missing: {details}."
        )
        normalized["assessment"] = "INSUFFICIENT COMPATIBLE EVIDENCE — the requested conclusion is refused."
        normalized["falsifier"] = f"Collect the missing evidence before re-querying: {details}."
        normalized["direction"] = f"Instrument and capture: {details}."
        confidence = min(confidence, 0.10)
        constraints.append(f"QUESTION_EVIDENCE_INCOMPATIBLE:{names}")
    if not _CONCRETE_OBSERVATION.search(normalized["direction"]):
        normalized["direction"] = ((
            "Capture the next bounded bidirectional flow window with Suricata application decoding; "
            "compare directional counters, protocol transitions, DNS/TLS/HTTP fields, and alert provenance."
        ) if capsule.get("flowEvidence") else (
            "Run repeated fixed-flow traceroutes or MTR, compare minimum per-hop RTTs, route "
            "stability, reverse DNS, BGP ownership, and independent geolocation sources."
        ))
        constraints.append("NON_ACTIONABLE_DIRECTION_REPLACED")
    if geography == "INFERRED":
        confidence = min(confidence, 0.60)
        constraints.append("GEOIP_UNCORROBORATED_CONFIDENCE_CEILING_0.60")
    if route == "MEASURED" and geography == "INFERRED":
        removed = []
        for key in ("situation", "assessment"):
            if _FORBIDDEN_ROUTE_ASSERTIONS.search(normalized[key]):
                normalized[key] = (
                    "UNSUPPORTED PHYSICAL-ROUTE CLAIM REMOVED — measured RTT/TTL responses do "
                    "not corroborate the interface GeoIP itinerary; retain multiple hypotheses."
                )
                removed.append(key.upper())
        if removed:
            confidence = min(confidence, 0.25)
            constraints.append(f"GEOIP_ROUTE_PROMOTION_REMOVED:{','.join(removed)}")
    timing_removed = []
    for key in ("situation", "assessment"):
        if _UNCORROBORATED_TIMING_CAUSE.search(normalized[key]):
            normalized[key] = (
                "UNCORROBORATED TIMING-CAUSE CLAIM REMOVED — single-trace intermediate ICMP "
                "responses support a timing observation, not congestion, path-change, or "
                "load-balancing attribution."
            )
            timing_removed.append(key.upper())
    if timing_removed:
        confidence = min(confidence, 0.35)
        constraints.append(f"SINGLE_TRACE_CAUSAL_ATTRIBUTION_REMOVED:{','.join(timing_removed)}")
    distance_removed = []
    for key in ("situation", "measuredVsInferred", "assessment", "falsifier"):
        if _RTT_DISTANCE_PROMOTION.search(normalized[key]):
            normalized[key] = (
                "RTT-TO-DISTANCE PROMOTION REMOVED — measured response time does not establish "
                "physical path length, locality, or a short/long-haul classification."
            )
            distance_removed.append(key.upper())
    if distance_removed:
        confidence = min(confidence, 0.25)
        constraints.append(f"RTT_DISTANCE_PROMOTION_REMOVED:{','.join(distance_removed)}")
    path_end_removed = []
    for key in ("situation", "assessment", "falsifier"):
        if _PATH_END_PROMOTION.search(normalized[key]):
            normalized[key] = (
                "TRACEROUTE-TERMINATION CLAIM REMOVED — the last responding TTL is observed; "
                "it does not establish that the packet path ended at that interface or network."
            )
            path_end_removed.append(key.upper())
    if path_end_removed:
        confidence = min(confidence, 0.25)
        constraints.append(f"PATH_TERMINATION_PROMOTION_REMOVED:{','.join(path_end_removed)}")
    absence_removed = []
    for key in ("situation", "assessment"):
        if _ABSENCE_TOPOLOGY_PROMOTION.search(normalized[key]):
            normalized[key] = (
                "TOPOLOGY-ABSENCE CLAIM WITHHELD — an unobserved VPN, relay, distant leg, or "
                "route cannot be excluded without compatible coverage and negative evidence."
            )
            absence_removed.append(key.upper())
    if absence_removed:
        confidence = min(confidence, 0.20)
        constraints.append(f"TOPOLOGY_ABSENCE_INFERENCE_WITHHELD:{','.join(absence_removed)}")
    if capsule.get("flowEvidence"):
        absence_reframed = []
        for key in ("situation", "anomalies", "assessment"):
            if _FLOW_UNBOUNDED_ABSENCE.search(normalized[key]):
                normalized[key] = (
                    "BOUNDED FLOW ABSENCE REFRAMED — the retained directional counters contain no "
                    "packets-to-client; responses, errors, retransmissions, timeouts, and activity "
                    "outside this summarized flow window remain unmeasured."
                )
                absence_reframed.append(key.upper())
        if absence_reframed:
            confidence = min(confidence, 0.45)
            constraints.append(f"FLOW_ABSENCE_BOUNDARY_ENFORCED:{','.join(absence_reframed)}")
        promoted = []
        for key in ("situation", "assessment"):
            if _FLOW_VERDICT_PROMOTION.search(normalized[key]):
                normalized[key] = (
                    "UNSUPPORTED FLOW VERDICT REMOVED — decoded metadata can support candidate "
                    "activity classes, not user intent, compromise, or maliciousness as fact."
                )
                promoted.append(key.upper())
        if promoted:
            confidence = min(confidence, 0.35)
            constraints.append(f"FLOW_VERDICT_PROMOTION_REMOVED:{','.join(promoted)}")
        transport = ((capsule.get("flowEvidence") or {}).get("flow") or {}).get("transport") or {}
        if (str(transport.get("dest_ip")) == "239.255.255.250" and
                str(transport.get("dest_port")) == "1900" and
                _SSDP_WEAK_FALSIFIER.search(normalized["falsifier"])):
            normalized["falsifier"] = (
                "Capture one bounded payload-signature or decoder result for this flow and test for "
                "SSDP M-SEARCH, NOTIFY, or HTTP response start-lines plus HOST/ST/NT/USN headers; a "
                "non-SSDP signature in this same flow would falsify the leading protocol hypothesis."
            )
            confidence = min(confidence, 0.45)
            constraints.append("SSDP_FALSIFIER_REPAIRED")
    hops = (capsule.get("hostTrace") or {}).get("traceroute", {}).get("hops", [])
    if any(item.get("physics_anomaly") for item in hops):
        confidence = min(confidence, 0.50)
        constraints.append("DERIVED_PHYSICS_WARNING_CONFIDENCE_CEILING_0.50")
    return {**normalized, "confidence": confidence, "validationConstraints": constraints}


def ask_ollama_cloud(capsule: Dict[str, Any], *, model: Optional[str] = None,
                     timeout: Optional[int] = None) -> Dict[str, Any]:
    """Transmit one explicit full-fidelity capsule to the fixed Ollama Cloud origin."""
    api_key = load_ollama_api_key()
    if not api_key:
        raise RuntimeError("Ollama Cloud API key is unavailable")
    selected_model = (model or os.environ.get("OLLAMA_CLOUD_MODEL") or _DEFAULT_MODEL).strip()
    timeout_seconds = min(max(int(timeout), 5), 120) if timeout is not None else \
        _bounded_environment_integer("OLLAMA_CLOUD_TIMEOUT_SECONDS", _DEFAULT_CLOUD_TIMEOUT_SECONDS, 15, 120)
    max_tokens = _bounded_environment_integer(
        "OLLAMA_CLOUD_MAX_TOKENS", _DEFAULT_CLOUD_MAX_TOKENS, 256, 2048)
    body = json.dumps({
        "model": selected_model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(capsule, sort_keys=True, ensure_ascii=False)},
        ],
        "options": {"temperature": 0.1, "num_predict": max_tokens},
    }).encode("utf-8")
    cloud_request = request.Request(
        f"{_CLOUD_ENDPOINT}/api/chat", data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(cloud_request, timeout=timeout_seconds) as response:
            envelope = json.loads(response.read())
    except error.HTTPError as exc:
        try:
            provider_error = str(json.loads(exc.read(4096)).get("error") or "")
        except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
            provider_error = ""
        if "prompt is too long" in provider_error.lower():
            reason = "request exceeded the model context window"
        elif exc.code in {401, 403}:
            reason = "credential was rejected"
        elif "model" in provider_error.lower() and ("not found" in provider_error.lower() or
                                                      "unknown" in provider_error.lower()):
            reason = "configured model is unavailable"
        else:
            reason = "request was rejected"
        raise RuntimeError(f"Ollama Cloud {reason} (HTTP {exc.code})") from exc
    except TimeoutError as exc:
        raise OllamaCloudTimeoutError(timeout_seconds) from exc
    except error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise OllamaCloudTimeoutError(timeout_seconds) from exc
        raise RuntimeError("Ollama Cloud request failed") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Ollama Cloud request failed") from exc
    content = str((envelope.get("message") or {}).get("content") or "").strip()
    try:
        report = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Ollama Cloud returned an invalid evidence report") from exc
    required = {"situation", "anomalies", "measuredVsInferred", "assessment",
                "falsifier", "direction", "confidence"}
    if not isinstance(report, dict) or not required.issubset(report):
        raise RuntimeError("Ollama Cloud report violated the response contract")
    return {"model": selected_model, "report": validate_cloud_report(report, capsule)}
