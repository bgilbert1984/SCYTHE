"""Bounded, versioned PeeringDB evidence for ASNs already observed by SCYTHE."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib import error, parse, request


SCHEMA_VERSION = "graphops.peeringdb.v1"
API_BASE = "https://www.peeringdb.com/api"
MAX_ASNS = 32
MAX_RECORDS = 256
CACHE_TTL_SECONDS = 6 * 60 * 60
DEFAULT_CACHE = Path(__file__).resolve().parent / "runtime" / "graphops_peeringdb_v1.json"


def load_peeringdb_api_key(path: Optional[str] = None) -> Optional[str]:
    direct = os.environ.get("PEERINGDB_API_KEY", "").strip()
    if direct:
        return direct
    secret_path = path or os.environ.get("PEERINGDB_API_KEY_FILE") or os.environ.get("OLLAMA_API_KEY_FILE")
    if not secret_path:
        return None
    with open(secret_path, "r", encoding="utf-8") as handle:
        contents = handle.read(16_385)
    if len(contents) > 16_384:
        raise ValueError("credential file exceeds 16 KiB")
    for line in contents.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        if name.strip().lower() not in {"peeringdb", "peeringdb_api_key"}:
            continue
        candidate = value.strip().strip("\"'")
        return candidate or None
    return None


def _bounded_ids(values: Iterable[Any], limit: int = MAX_ASNS) -> list[int]:
    result = set()
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < number <= 4_294_967_295:
            result.add(number)
    return sorted(result)[:limit]


def _frame(record: Dict[str, Any], fields: tuple[str, ...]) -> Dict[str, Any]:
    return {key: record.get(key) for key in fields if record.get(key) is not None}


class PeeringDbClient:
    def __init__(self, *, api_key: Optional[str] = None, cache_path: Path = DEFAULT_CACHE,
                 opener=request.urlopen, clock=time.time):
        self.api_key = api_key; self.cache_path = Path(cache_path); self.opener = opener; self.clock = clock
        self.lock = threading.RLock()

    def snapshot(self, asns: Iterable[Any], *, force: bool = False) -> Dict[str, Any]:
        scope = _bounded_ids(asns)
        if not scope:
            return self._empty("NO OBSERVED PUBLIC ASNS")
        with self.lock:
            cached = self._load_cache()
            if (not force and cached and cached.get("scope", {}).get("asns") == scope and
                    self.clock() - float(cached.get("retrievedEpoch") or 0) < CACHE_TTL_SECONDS):
                return {**cached, "cache": "FRESH"}
            try:
                result = self._fetch(scope)
                self._store_cache(result)
                return {**result, "cache": "REFRESHED"}
            except (OSError, ValueError, error.URLError, error.HTTPError, json.JSONDecodeError) as exc:
                if cached:
                    return {**cached, "status": "stale", "cache": "STALE_FALLBACK",
                            "refreshError": type(exc).__name__}
                return {**self._empty("PEERINGDB UNAVAILABLE"), "refreshError": type(exc).__name__}

    def _get(self, object_type: str, params: Dict[str, Any]) -> list[Dict[str, Any]]:
        url = f"{API_BASE}/{object_type}?{parse.urlencode(params)}"
        headers = {"Accept": "application/json", "User-Agent": "SCYTHE-GraphOps-InfraFlow/1"}
        if self.api_key:
            headers["Authorization"] = f"Api-Key {self.api_key}"
        with self.opener(request.Request(url, headers=headers), timeout=20) as response:
            body = json.loads(response.read())
        data = body.get("data")
        if not isinstance(data, list):
            raise ValueError(f"PeeringDB {object_type} response lacks data array")
        return [item for item in data[:MAX_RECORDS] if isinstance(item, dict)]

    def _fetch(self, scope: list[int]) -> Dict[str, Any]:
        joined = ",".join(map(str, scope))
        networks_raw = self._get("net", {"asn__in": joined, "depth": 0})
        network_ids = _bounded_ids((item.get("id") for item in networks_raw), MAX_RECORDS)
        net_joined = ",".join(map(str, network_ids))
        netfac_raw = self._get("netfac", {"net_id__in": net_joined, "depth": 0}) if network_ids else []
        netix_raw = self._get("netixlan", {"net_id__in": net_joined, "depth": 0}) if network_ids else []
        facility_ids = _bounded_ids((item.get("fac_id") for item in netfac_raw), MAX_RECORDS)
        ix_ids = _bounded_ids((item.get("ix_id") for item in netix_raw), MAX_RECORDS)
        facilities_raw = self._get("fac", {"id__in": ",".join(map(str, facility_ids)), "depth": 0}) if facility_ids else []
        exchanges_raw = self._get("ix", {"id__in": ",".join(map(str, ix_ids)), "depth": 0}) if ix_ids else []

        networks = [_frame(item, ("id", "asn", "name", "aka", "info_type", "info_scope",
                                          "info_traffic", "policy_general", "policy_locations",
                                          "policy_ratio", "policy_contracts", "updated", "status"))
                    for item in networks_raw if item.get("asn") in scope]
        net_ids = {int(item["id"]): int(item["asn"]) for item in networks if item.get("id") and item.get("asn")}
        memberships = []
        for item in netix_raw:
            if int(item.get("net_id") or 0) not in net_ids:
                continue
            memberships.append({**_frame(item, ("id", "net_id", "ix_id", "ixlan_id", "name", "speed",
                                                       "is_rs_peer", "operational", "updated", "status")),
                                "asn": net_ids[int(item["net_id"])],
                                "evidenceClass": "INFRASTRUCTURE_EVIDENCE",
                                "authority": "PEERINGDB_SELF_REPORTED"})
        presences = []
        for item in netfac_raw:
            if int(item.get("net_id") or 0) not in net_ids:
                continue
            presences.append({**_frame(item, ("id", "net_id", "fac_id", "local_asn", "updated", "status")),
                              "asn": net_ids[int(item["net_id"])],
                              "evidenceClass": "INFRASTRUCTURE_EVIDENCE",
                              "authority": "PEERINGDB_SELF_REPORTED"})
        facilities = [{**_frame(item, ("id", "name", "org_id", "city", "state", "country",
                                                    "latitude", "longitude", "updated", "status")),
                       "evidenceClass": "INFRASTRUCTURE_EVIDENCE",
                       "authority": "PEERINGDB_SELF_REPORTED"} for item in facilities_raw]
        exchanges = [{**_frame(item, ("id", "name", "org_id", "city", "country", "region_continent",
                                                   "latitude", "longitude", "updated", "status")),
                      "evidenceClass": "INFRASTRUCTURE_EVIDENCE",
                      "authority": "PEERINGDB_SELF_REPORTED"} for item in exchanges_raw]
        normalized_networks = [{**item, "evidenceClass": "INFRASTRUCTURE_EVIDENCE",
                                "authority": "PEERINGDB_SELF_REPORTED"} for item in networks]
        updated = sorted(str(item.get("updated")) for group in
                         (normalized_networks, memberships, presences, facilities, exchanges)
                         for item in group if item.get("updated"))
        payload = {"networks": normalized_networks, "ixMemberships": memberships,
                   "facilityPresences": presences, "facilities": facilities, "exchanges": exchanges}
        revision = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        now = self.clock()
        return {"status": "ok", "schemaVersion": SCHEMA_VERSION, "retrievedEpoch": now,
                "retrievedAt": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "datasetRevision": revision, "recordUpdatedThrough": updated[-1] if updated else None,
                "scope": {"asns": scope, "bounded": True}, **payload,
                "provenance": {"provider": "PeeringDB", "apiBase": API_BASE,
                               "apiContract": "CURRENT_API_RESPONSE", "scytheSchema": SCHEMA_VERSION,
                               "authenticated": bool(self.api_key)},
                "summary": {key: len(value) for key, value in payload.items()},
                "boundary": "PEERINGDB DATA IS SELF-REPORTED DECLARED INFRASTRUCTURE; SHARED PRESENCE DOES NOT PROVE TRAFFIC OR ROUTING"}

    def _empty(self, reason: str) -> Dict[str, Any]:
        return {"status": "empty", "schemaVersion": SCHEMA_VERSION, "scope": {"asns": [], "bounded": True},
                "networks": [], "ixMemberships": [], "facilityPresences": [], "facilities": [], "exchanges": [],
                "summary": {}, "reason": reason,
                "boundary": "NO PEERINGDB CLAIM IS AVAILABLE"}

    def _load_cache(self) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return data if data.get("schemaVersion") == SCHEMA_VERSION else None
        except (OSError, json.JSONDecodeError, AttributeError):
            return None

    def _store_cache(self, payload: Dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix="peeringdb-", suffix=".json", dir=self.cache_path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":")); handle.flush(); os.fsync(handle.fileno())
            os.replace(temp_name, self.cache_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


_CLIENT: Optional[PeeringDbClient] = None
_CLIENT_LOCK = threading.Lock()


def get_peeringdb_client() -> PeeringDbClient:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = PeeringDbClient(api_key=load_peeringdb_api_key())
        return _CLIENT
