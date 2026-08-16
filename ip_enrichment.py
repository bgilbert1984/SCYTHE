"""Bounded, local-only IP enrichment for GraphOps display surfaces.

The returned claims are intentionally separate from observed graph evidence.
GeoIP coordinates are estimates and must never be promoted to graph position.
"""

from __future__ import annotations

import hashlib
import ipaddress
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional


_ASSETS = Path(__file__).resolve().parent / "assets"
_ASN_PATH = _ASSETS / "GeoLite2-ASN.mmdb"
_CITY_PATH = _ASSETS / "GeoLite2-City.mmdb"


def _scope(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if address.is_loopback:
        return "LOOPBACK"
    if address.is_link_local:
        return "LINK_LOCAL"
    if address.is_multicast:
        return "MULTICAST"
    if address.is_unspecified:
        return "RESERVED"
    if address.is_private:
        return "PRIVATE"
    if address.is_reserved or not address.is_global:
        return "RESERVED"
    return "PUBLIC"


def _english(record: Dict[str, Any], key: str) -> str:
    names = (record.get(key) or {}).get("names") or {}
    return str(names.get("en") or "")


class IpEnrichmentResolver:
    """Resolve public addresses from local MMDB files with a bounded cache."""

    def __init__(self, *, asn_path: Path = _ASN_PATH, city_path: Path = _CITY_PATH,
                 cache_limit: int = 10_000):
        self.asn_path = Path(asn_path)
        self.city_path = Path(city_path)
        self.cache_limit = min(max(int(cache_limit), 1), 50_000)
        self._readers: Dict[str, Any] = {}
        self._sources: Dict[str, Dict[str, Any]] = {}
        self._cache: "OrderedDict[str, Optional[Dict[str, Any]]]" = OrderedDict()
        self._lock = RLock()

    def _open(self, name: str, path: Path):
        if name in self._readers:
            return self._readers[name]
        try:
            import maxminddb
            reader = maxminddb.open_database(str(path))
            metadata = reader.metadata()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self._sources[name] = {
                "database": str(metadata.database_type),
                "buildEpoch": int(metadata.build_epoch),
                "sha256": digest,
                "localOnly": True,
            }
            self._readers[name] = reader
        except Exception:
            self._readers[name] = None
        return self._readers[name]

    def resolve(self, value: str) -> Optional[Dict[str, Any]]:
        try:
            address = ipaddress.ip_address(str(value).strip())
        except ValueError:
            return None
        ip = address.compressed
        with self._lock:
            if ip in self._cache:
                result = self._cache.pop(ip)
                self._cache[ip] = result
                return result

            result: Dict[str, Any] = {
                "schemaVersion": "1.0",
                "ip": ip,
                "ipVersion": address.version,
                "scope": _scope(address),
            }
            if result["scope"] == "PUBLIC":
                self._resolve_public(ip, address, result)
            self._cache[ip] = result
            while len(self._cache) > self.cache_limit:
                self._cache.popitem(last=False)
            return result

    def _resolve_public(self, ip: str, address: Any, result: Dict[str, Any]) -> None:
        asn_reader = self._open("asn", self.asn_path)
        if asn_reader:
            try:
                record, prefix_length = asn_reader.get_with_prefix_len(ip)
                record = record or {}
                asn = record.get("autonomous_system_number")
                if asn:
                    network = ipaddress.ip_network(f"{address}/{prefix_length}", strict=False)
                    result["network"] = {
                        "evidenceClass": "INFERRED",
                        "authority": "LOCAL_DATABASE_LOOKUP",
                        "asn": int(asn),
                        "organization": str(record.get("autonomous_system_organization") or ""),
                        "prefix": str(network),
                        "source": dict(self._sources["asn"]),
                    }
            except Exception:
                pass

        city_reader = self._open("city", self.city_path)
        if city_reader:
            try:
                record = city_reader.get(ip) or {}
                location = record.get("location") or {}
                latitude = location.get("latitude")
                longitude = location.get("longitude")
                country = record.get("country") or record.get("registered_country") or {}
                geo: Dict[str, Any] = {
                    "evidenceClass": "INFERRED",
                    "authority": "GEOIP_ESTIMATE",
                    "countryCode": str(country.get("iso_code") or ""),
                    "country": str((country.get("names") or {}).get("en") or ""),
                    "region": _english({"region": ((record.get("subdivisions") or [{}])[0])}, "region"),
                    "city": _english(record, "city"),
                    "source": dict(self._sources["city"]),
                }
                if latitude is not None and longitude is not None:
                    geo["latitude"] = float(latitude)
                    geo["longitude"] = float(longitude)
                    if location.get("accuracy_radius") is not None:
                        geo["uncertaintyRadiusKm"] = float(location["accuracy_radius"])
                result["geo"] = geo
            except Exception:
                pass


_DEFAULT_RESOLVER = IpEnrichmentResolver()


def enrich_graph_node(node: Dict[str, Any], resolver: IpEnrichmentResolver = _DEFAULT_RESOLVER) -> Dict[str, Any]:
    """Return a shallow node copy carrying non-authoritative display enrichment."""
    if str(node.get("kind") or "").lower() not in {
            "network_host", "network_multicast_group", "network_unspecified_address"}:
        return node
    labels = node.get("labels") or {}
    ip = labels.get("ip")
    if not ip and str(node.get("id") or "").startswith("host:"):
        ip = str(node["id"])[5:]
    enrichment = resolver.resolve(str(ip or ""))
    return {**node, **({"enrichment": enrichment} if enrichment else {})}
