"""Passive, revision-pinned context for non-unicast graph addresses."""

from __future__ import annotations

import ipaddress
import time
from collections import Counter
from typing import Any, Dict


_KNOWN_GROUPS = {
    "224.0.0.251": ("mDNS", "MULTICAST DNS SERVICE DISCOVERY"),
    "ff02::fb": ("mDNS", "MULTICAST DNS SERVICE DISCOVERY"),
    "224.0.0.252": ("LLMNR", "LINK-LOCAL MULTICAST NAME RESOLUTION"),
    "ff02::1:3": ("LLMNR", "LINK-LOCAL MULTICAST NAME RESOLUTION"),
    "239.255.255.250": ("SSDP", "SIMPLE SERVICE DISCOVERY PROTOCOL"),
    "ff02::c": ("SSDP", "SIMPLE SERVICE DISCOVERY PROTOCOL"),
}


def classify_address(value: str) -> Dict[str, Any]:
    address = ipaddress.ip_address(str(value).strip())
    if address.is_multicast:
        address_class = "MULTICAST_GROUP"
        scope = "LINK_LOCAL" if (address.version == 6 and (address.packed[1] & 0x0f) == 2) else "MULTICAST"
    elif address.is_unspecified:
        address_class = "UNSPECIFIED_ADDRESS"
        scope = "NON_ROUTABLE_SENTINEL"
    else:
        raise ValueError("address context applies only to multicast or unspecified addresses")
    service, purpose = _KNOWN_GROUPS.get(address.compressed, ("UNASSIGNED_OR_OTHER", "NO WELL-KNOWN GROUP MAPPING"))
    return {"address": address.compressed, "ipVersion": address.version,
            "addressClass": address_class, "scope": scope,
            "knownService": service, "knownPurpose": purpose}


def prepare_address_context(selection: Dict[str, Any], resolved: Dict[str, Any]) -> Dict[str, Any]:
    node = resolved.get("node") or {}
    labels = node.get("labels") or {}
    value = labels.get("ip") or str(node.get("id") or "").removeprefix("host:")
    classification = classify_address(str(value))
    edges = list(resolved.get("incidentEdges") or [])[:50]
    protocols = Counter(); classes = Counter(); senders = set(); receivers = set(); observations = []
    for edge in edges:
        edge_labels = edge.get("labels") or {}
        protocol = str(edge_labels.get("app_proto") or edge_labels.get("proto") or "unknown").lower()
        protocols[protocol] += 1
        classes[str(edge.get("evidenceClass") or "INFERRED").upper()] += 1
        source = str(edge_labels.get("src_ip") or "")
        destination = str(edge_labels.get("dest_ip") or "")
        if source and source != classification["address"]:
            senders.add(source)
        if destination and destination != classification["address"]:
            receivers.add(destination)
        observations.append({key: edge.get(key) for key in ("id", "kind", "evidenceClass", "observedAt")
                             if edge.get(key) is not None} | {
            "transport": {key: edge_labels.get(key) for key in
                          ("src_ip", "src_port", "dest_ip", "dest_port", "proto", "app_proto")
                          if edge_labels.get(key) not in (None, "")},
        })
    multicast = classification["addressClass"] == "MULTICAST_GROUP"
    return {
        "status": "prepared", "schemaVersion": "graphops.address-context.v1",
        "capturedAt": time.time(),
        "selection": {"kind": "graph-node", "entityId": selection.get("entityId"),
                      "graphRevision": resolved.get("graphRevision") or selection.get("graphRevision")},
        "address": classification,
        "passiveEvidence": {"incidentFlowCount": len(edges),
                            "protocolCounts": dict(sorted(protocols.items())),
                            "evidenceClasses": dict(sorted(classes.items())),
                            "observedSenders": sorted(senders)[:32],
                            "observedReceivers": sorted(receivers)[:32],
                            "flows": observations[:24]},
        "activeMeasurement": {
            "status": "NOT_APPLICABLE",
            "reason": ("MULTICAST HAS ZERO OR MANY INTERFACE-SCOPED RESPONDERS; A UNICAST RTT OR "
                       "TRACEROUTE WOULD MISREPRESENT THE GROUP" if multicast else
                       "THE UNSPECIFIED ADDRESS IS A WILDCARD/SENTINEL, NOT A REMOTE ENDPOINT"),
        },
        "suggestedQuestion": ((
            "Explain which observed senders use this multicast group, what the decoded protocols and "
            "cadence support, whether behavior is consistent with ordinary local discovery, and which "
            "interface-scoped passive observation would falsify that interpretation."
        ) if multicast else (
            "Explain why an unspecified address entered the graph, distinguish capture/binding semantics "
            "from remote-host activity, and identify the source record needed to resolve the ambiguity."
        )),
        "bounded": True, "rawPacketsExposed": False,
        "boundary": ("PASSIVE FLOW OBSERVATIONS ESTABLISH ADDRESS USE AT THIS SENSOR; THEY DO NOT "
                     "IDENTIFY EVERY MULTICAST MEMBER, A UNIQUE REMOTE HOST, USER INTENT, OR MALICIOUSNESS"),
    }
