"""Bounded, exact-evidence capsules for explicit GraphOps Cloud analysis."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib import error, request


_CLOUD_ENDPOINT = "https://ollama.com"
_DEFAULT_MODEL = "gpt-oss:20b"
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


def build_full_fidelity_capsule(question: str, selection: Dict[str, Any],
                                resolved: Dict[str, Any], trace: Dict[str, Any]) -> Dict[str, Any]:
    """Build a deterministic-content, exact-value capsule from server-owned evidence."""
    entity = resolved.get("node") or resolved.get("edge") or {}
    capsule = {
        "schemaVersion": "graphops.full-fidelity.v1",
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
        "hostTrace": _trace_frame(trace),
        "authority": {
            "graph": "REVISION_PINNED_SERVER_RESOLVED",
            "target": "OBSERVED",
            "routeAndRtt": (trace.get("evidenceClasses") or {}),
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
    canonical = json.dumps(capsule, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    capsule["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return capsule


def disclosure_receipt(capsule: Dict[str, Any], model: str) -> Dict[str, Any]:
    hops = (capsule.get("hostTrace") or {}).get("traceroute", {}).get("hops", [])
    addresses = {str(item.get("ip")) for item in hops if item.get("ip")}
    target = (capsule.get("hostTrace") or {}).get("target")
    if target:
        addresses.add(str(target))
    locations = sum(1 for item in hops if item.get("geo") or
                    (item.get("lat") is not None and item.get("lon") is not None))
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
        },
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
corroboration. Use "could be consistent with" for hypotheses and name alternatives.

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
    if not _CONCRETE_OBSERVATION.search(normalized["direction"]):
        normalized["direction"] = (
            "Run repeated fixed-flow traceroutes or MTR, compare minimum per-hop RTTs, route "
            "stability, reverse DNS, BGP ownership, and independent geolocation sources."
        )
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
    hops = (capsule.get("hostTrace") or {}).get("traceroute", {}).get("hops", [])
    if any(item.get("physics_anomaly") for item in hops):
        confidence = min(confidence, 0.50)
        constraints.append("DERIVED_PHYSICS_WARNING_CONFIDENCE_CEILING_0.50")
    return {**normalized, "confidence": confidence, "validationConstraints": constraints}


def ask_ollama_cloud(capsule: Dict[str, Any], *, model: Optional[str] = None,
                     timeout: int = 240) -> Dict[str, Any]:
    """Transmit one explicit full-fidelity capsule to the fixed Ollama Cloud origin."""
    api_key = load_ollama_api_key()
    if not api_key:
        raise RuntimeError("Ollama Cloud API key is unavailable")
    selected_model = (model or os.environ.get("OLLAMA_CLOUD_MODEL") or _DEFAULT_MODEL).strip()
    body = json.dumps({
        "model": selected_model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(capsule, sort_keys=True, ensure_ascii=False)},
        ],
        "options": {"temperature": 0.1},
    }).encode("utf-8")
    cloud_request = request.Request(
        f"{_CLOUD_ENDPOINT}/api/chat", data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(cloud_request, timeout=timeout) as response:
            envelope = json.loads(response.read())
    except error.HTTPError as exc:
        raise RuntimeError(f"Ollama Cloud returned HTTP {exc.code}") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
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
