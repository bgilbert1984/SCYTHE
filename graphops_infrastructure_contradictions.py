"""Deterministic tensions between infrastructure evidence layers; never a consensus engine."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional


SCHEMA_VERSION = "graphops.infrastructure-contradictions.v1"


def _id(kind: str, values: Any) -> str:
    digest = hashlib.blake2s(json.dumps(values, sort_keys=True, default=str).encode(), digest_size=10).hexdigest()
    return f"infra-contradiction-{kind.lower()}-{digest}"


def _epoch(value: Any) -> Optional[float]:
    if value is None: return None
    try: return float(value)
    except (TypeError, ValueError):
        try: return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError: return None


def _origins(value: Any) -> set[int]:
    values = value if isinstance(value, list) else [value]
    output = set()
    for item in values:
        if isinstance(item, list): output.update(_origins(item))
        else:
            try: output.add(int(item))
            except (TypeError, ValueError): continue
    return output


def _path_key(path: Any) -> str:
    return json.dumps(path if isinstance(path, list) else [], separators=(",", ":"))


def evaluate_infrastructure_contradictions(infrastructure: Dict[str, Any], *,
                                           since: Optional[float] = None,
                                           until: Optional[float] = None,
                                           limit: int = 100) -> Dict[str, Any]:
    limit = min(max(int(limit), 1), 200)
    ris = infrastructure.get("controlPlaneEvidence") or {}
    pdb = infrastructure.get("peeringdbEvidence") or {}
    rows = list(ris.get("controlPlanePaths") or [])
    if since is not None: rows = [row for row in rows if _epoch(row.get("collectorReceivedAt")) is not None and _epoch(row.get("collectorReceivedAt")) >= since]
    if until is not None: rows = [row for row in rows if _epoch(row.get("collectorReceivedAt")) is not None and _epoch(row.get("collectorReceivedAt")) <= until]
    domains = []
    for domain in infrastructure.get("domains") or []:
        networks = []
        for value in domain.get("prefixes") or []:
            try: networks.append(ipaddress.ip_network(str(value), strict=False))
            except ValueError: continue
        domains.append((domain, networks))

    findings, changes = [], []
    messages_by_key: Dict[tuple[str, str], list[Dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("collectorId") or ""), str(row.get("prefix") or ""))
        messages_by_key.setdefault(key, []).append(row)
        try: message_network = ipaddress.ip_network(str(row.get("prefix")), strict=False)
        except ValueError: continue
        # A broad RIS less-specific (for example 2000::/3) cannot test a more-specific
        # local prefix-to-AS claim. Compare only equal or more-specific NLRIs and once
        # per domain/message, while retaining broad observations as control-plane evidence.
        matching = [domain for domain, networks in domains
                    if any(network.version == message_network.version and
                           message_network.subnet_of(network) for network in networks)]
        for domain in matching:
            expected_asn = domain.get("asn"); observed_origins = _origins(row.get("originAsn"))
            if (row.get("messageType") == "ANNOUNCE" and expected_asn and observed_origins and
                    int(expected_asn) not in observed_origins):
                findings.append({
                    "id": _id("origin", [row.get("id"), expected_asn, sorted(observed_origins)]),
                    "kind": "ORIGIN_DISAGREEMENT", "status": "UNRESOLVED",
                    "evidenceClass": "CONTRADICTION_FINDING", "severity": "REVIEW",
                    "subject": domain.get("id"), "prefix": row.get("prefix"),
                    "claims": [
                        {"value": int(expected_asn), "authority": domain.get("authority"),
                         "evidenceClass": domain.get("evidenceClass"), "sourceRevision": infrastructure.get("graphRevision")},
                        {"value": sorted(observed_origins), "authority": "RIS_LIVE_COLLECTOR_VANTAGE",
                         "evidenceClass": "CONTROL_PLANE_OBSERVATION", "sourceRevision": ris.get("snapshotRevision"),
                         "collectorId": row.get("collectorId"), "observedAt": row.get("collectorReceivedAt")},
                    ],
                    "alternatives": ["LOCAL PREFIX-TO-AS ENRICHMENT IS STALE OR COARSE",
                                     "RIS OBSERVED A LEGITIMATE MULTI-ORIGIN OR ROUTING CHANGE",
                                     "THE PREFIX CONTAINMENT MATCH IS TOO BROAD"],
                    "falsifier": "Query an authoritative current prefix-to-origin source and compare multiple RIS collectors in the same UTC window.",
                    "boundary": "THIS IS A SOURCE DISAGREEMENT, NOT A HIJACK DETERMINATION",
                })
            if row.get("messageType") == "WITHDRAW":
                active_flows = [flow for flow in infrastructure.get("observedFlows") or []
                                if domain.get("id") in {flow.get("sourceDomain"), flow.get("targetDomain")} and
                                (since is None or (_epoch(flow.get("lastSeen")) or 0) >= since) and
                                (until is None or (_epoch(flow.get("firstSeen")) or float("inf")) <= until)]
                if active_flows:
                    findings.append({
                        "id": _id("withdrawal-traffic", [row.get("id"), [flow.get("id") for flow in active_flows]]),
                        "kind": "WITHDRAWAL_WITH_DATA_PLANE_ACTIVITY", "status": "UNRESOLVED",
                        "evidenceClass": "EVIDENCE_TENSION", "severity": "INFORMATIONAL",
                        "subject": domain.get("id"), "prefix": row.get("prefix"),
                        "claims": [
                            {"value": "WITHDRAW", "authority": "RIS_LIVE_COLLECTOR_VANTAGE",
                             "sourceRevision": ris.get("snapshotRevision"), "collectorId": row.get("collectorId"),
                             "observedAt": row.get("collectorReceivedAt")},
                            {"value": [flow.get("id") for flow in active_flows[:16]], "authority": "OBSERVED_GRAPH_EDGES",
                             "sourceRevision": infrastructure.get("graphRevision")},
                        ],
                        "alternatives": ["EXISTING SESSIONS OUTLIVED THE CONTROL-PLANE UPDATE",
                                         "ANOTHER COLLECTOR OR ROUTE RETAINED REACHABILITY",
                                         "GRAPH AND COLLECTOR WINDOWS ARE NOT SUFFICIENTLY ALIGNED"],
                        "falsifier": "Measure reachability and repeat fixed-flow traceroute while comparing concurrent announcements across multiple collectors.",
                        "boundary": "A COLLECTOR WITHDRAWAL DOES NOT ESTABLISH GLOBAL UNREACHABILITY",
                    })
        if len(findings) >= limit: break

    for (collector, prefix), messages in messages_by_key.items():
        ordered = sorted(messages, key=lambda row: _epoch(row.get("collectorReceivedAt")) or 0)
        announced = [row for row in ordered if row.get("messageType") == "ANNOUNCE"]
        origins = {json.dumps(row.get("originAsn"), separators=(",", ":"), sort_keys=True)
                   for row in announced if row.get("originAsn") is not None}
        paths = {_path_key(row.get("asPath")) for row in announced if row.get("asPath")}
        for kind, values in (("ORIGIN_CHANGE_OBSERVED", origins), ("AS_PATH_CHANGE_OBSERVED", paths)):
            if len(values) > 1:
                changes.append({"id": _id(kind, [collector, prefix, sorted(values)]), "kind": kind,
                                "prefix": prefix, "collectorId": collector, "variants": len(values),
                                "evidenceClass": "CONTROL_PLANE_OBSERVATION",
                                "sourceRevision": ris.get("snapshotRevision"),
                                "boundary": "CHANGE AT ONE COLLECTOR VANTAGE IS NOT A DATA-PLANE CAUSE"})

    requested_window = {"from": since, "to": until}
    withheld = [{
        "kind": "ABSENCE_INFERENCE_WITHHELD", "requestedWindow": requested_window,
        "reason": "CONTINUOUS COLLECTOR COVERAGE EVIDENCE IS NOT RECORDED FOR THE ENTIRE WINDOW",
        "needed": "Collector session intervals, subscription acknowledgements, disconnect gaps, and prefix-filter continuity",
    }]
    if not (infrastructure.get("referenceCatalog") or {}).get("caidaRelationships", {}).get("datasetRevision"):
        withheld.append({"kind": "PEERINGDB_CAIDA_COMPARISON_WITHHELD",
                         "reason": "VERSIONED CAIDA RELATIONSHIP DATASET IS NOT ATTACHED"})
    if not infrastructure.get("hostTrace"):
        withheld.append({"kind": "TRACEROUTE_CONTROL_PLANE_COMPARISON_WITHHELD",
                         "reason": "NO REVISION-PINNED HOST TRACE IS PRESENT IN THIS INFRASTRUCTURE SNAPSHOT"})

    revision_seed = {"graphRevision": infrastructure.get("graphRevision"),
                     "risRevision": ris.get("snapshotRevision"), "pdbRevision": pdb.get("datasetRevision"),
                     "window": requested_window, "findings": findings[:limit], "changes": changes[:limit]}
    revision = hashlib.sha256(json.dumps(revision_seed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"status": "ok", "schemaVersion": SCHEMA_VERSION, "revision": revision,
            "capturedAt": datetime.now(timezone.utc).isoformat(), "window": requested_window,
            "sourceRevisions": {"graph": infrastructure.get("graphRevision"),
                                "ris": ris.get("snapshotRevision"), "peeringdb": pdb.get("datasetRevision")},
            "findings": findings[:limit], "changes": changes[:limit], "withheld": withheld,
            "summary": {"findings": min(len(findings), limit), "changes": min(len(changes), limit),
                        "withheldTests": len(withheld)}, "bounded": True, "limit": limit,
            "boundary": "FINDINGS PRESERVE SOURCE DISAGREEMENT; THEY DO NOT SYNTHESIZE CONSENSUS, ESTABLISH HIJACK, OR PROVE DATA-PLANE CAUSALITY"}
