# NerfEngine/registries/pcap_registry.py (WriteBus refactor)
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol
import hashlib
import logging
import os
import time
from collections import defaultdict

from writebus import bus, init_writebus, WriteContext, GraphOp

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graph_ids import (  # MANDATORY — no legacy fallback
    host_id as _host_id, geo_id as _geo_id, asn_id as _asn_id, org_id as _org_id,
    port_hub_id as _port_hub_id, service_id as _service_id,
    flow_id as _flow_id, flow_observed_edge_id as _flow_observed_edge_id,
    session_observed_host_edge as _sess_host_edge,
    session_observed_flow_edge as _sess_flow_edge,
    host_geo_edge as _host_geo_edge,
    host_in_asn_edge as _host_asn_edge,
    asn_org_edge as _asn_org_edge,
    flow_dst_port_edge as _flow_port_edge,
    port_implied_service_edge as _port_svc_edge,
    infer_service as _infer_service,
    is_private_ip as _is_private_ip,
    # TLS/DNS/HTTP ID generators
    dns_name_id as _dns_name_id, tls_sni_id as _tls_sni_id,
    tls_cert_id as _tls_cert_id, http_host_id as _http_host_id,
    ja3_id as _ja3_id, ja3s_id as _ja3s_id,
    flow_sni_edge as _flow_sni_edge, flow_dns_edge as _flow_dns_edge,
    flow_http_host_edge as _flow_http_host_edge,
)

# PCAP parsing libraries (graceful degradation: scapy > dpkt > simulation)
try:
    from scapy.all import rdpcap, IP, TCP, UDP
    HAS_SCAPY = True
except ImportError:
    HAS_SCAPY = False

try:
    import dpkt
    import socket
    HAS_DPKT = True
except ImportError:
    HAS_DPKT = False

# GeoIP lookup (optional, uses maxminddb if available)
try:
    import maxminddb
    HAS_MAXMINDDB = True
except ImportError:
    HAS_MAXMINDDB = False

# DNS resolver for CYMRU ASN lookup (optional)
try:
    import dns.resolver
    HAS_DNSPYTHON = True
except ImportError:
    HAS_DNSPYTHON = False

# CSV/URL fetch for threat intel blocklists
import csv
import io
import threading
import urllib.request

Json = Dict[str, Any]
logger = logging.getLogger("rf_scythe.pcap_registry")

if HAS_SCAPY:
    logger.info("✓ Scapy available for PCAP parsing")
elif HAS_DPKT:
    logger.info("✓ dpkt available for PCAP parsing (Scapy not found)")
else:
    logger.warning("⚠ No PCAP parser available (scapy/dpkt missing) - using simulation mode")

if HAS_MAXMINDDB:
    logger.info("✓ maxminddb available for GeoIP lookups")
else:
    logger.info("⚠ maxminddb not installed - GeoIP lookups disabled")

if HAS_DNSPYTHON:
    logger.info("✓ dnspython available for CYMRU ASN lookups")
else:
    logger.info("⚠ dnspython not installed - CYMRU ASN lookups disabled (pip install dnspython)")


# ── Circuit Breaker (ported from WorldMonitor utils/circuit-breaker.ts) ──────
class CircuitBreaker:
    """Fault-tolerant call wrapper: N failures → cooldown + stale-cache fallback."""
    _registry: Dict[str, 'CircuitBreaker'] = {}

    def __init__(self, name: str, max_failures: int = 2,
                 cooldown_s: int = 300, cache_ttl_s: int = 600):
        self.name = name
        self.max_failures = max_failures
        self.cooldown_s = cooldown_s
        self.cache_ttl_s = cache_ttl_s
        self._failures = 0
        self._cooldown_until: float = 0.0
        self._cache: Optional[Any] = None
        self._cache_ts: float = 0.0
        CircuitBreaker._registry[name] = self

    def is_open(self) -> bool:
        now = time.time()
        if now < self._cooldown_until:
            return True
        if self._cooldown_until > 0:
            self._failures = 0
            self._cooldown_until = 0.0
        return False

    def get_cached(self) -> Optional[Any]:
        if self._cache is not None and (time.time() - self._cache_ts) < self.cache_ttl_s:
            return self._cache
        return None

    def record_success(self, data: Any) -> None:
        self._failures = 0
        self._cooldown_until = 0.0
        self._cache = data
        self._cache_ts = time.time()

    def record_failure(self, error: str = '') -> None:
        self._failures += 1
        if self._failures >= self.max_failures:
            self._cooldown_until = time.time() + self.cooldown_s
            logger.warning(
                f"[CB:{self.name}] open {self.cooldown_s}s after "
                f"{self._failures} failures — {error[:80]}"
            )

    def call(self, fn, default: Any = None) -> Any:
        if self.is_open():
            cached = self.get_cached()
            remaining = max(0, int(self._cooldown_until - time.time()))
            logger.debug(f"[CB:{self.name}] open {remaining}s; "
                         f"{'cached' if cached is not None else 'default'}")
            return cached if cached is not None else default
        cached = self.get_cached()
        if cached is not None:
            return cached
        try:
            result = fn()
            if result is not None:
                self.record_success(result)
            return result
        except Exception as exc:
            self.record_failure(str(exc))
            return self.get_cached() if self.get_cached() is not None else default

    @classmethod
    def all_status(cls) -> Dict[str, str]:
        now = time.time()
        return {
            n: f"open ({max(0, int(cb._cooldown_until - now))}s)" if cb.is_open() else "closed"
            for n, cb in cls._registry.items()
        }


# ── Module-level threat intel blocklist cache ─────────────────────────────
# Loaded once on first use; refreshed every 6 hours in a background thread.
_BLOCKLIST_LOCK   = threading.Lock()
_FEODO_IPS: set   = set()           # Feodo Tracker botnet C2 IPs
_C2INTEL_IPS: set = set()           # C2IntelFeeds infrastructure IPs
_BLOCKLIST_LOADED_AT: float = 0.0
_BLOCKLIST_TTL      = 6 * 3600      # 6 hours

_FEODO_URL   = "https://feodotracker.abuse.ch/downloads/ipblocklist.csv"
_C2INTEL_URL = "https://raw.githubusercontent.com/drb-ra/C2IntelFeeds/master/feeds/IPs.csv"

# Circuit breakers — protect HTTP blocklist fetches and CYMRU DNS from stalling
_CB_FEODO   = CircuitBreaker('feodo-tracker',  max_failures=2, cooldown_s=300, cache_ttl_s=7200)
_CB_C2INTEL = CircuitBreaker('c2intel-feeds',  max_failures=2, cooldown_s=300, cache_ttl_s=7200)
_CB_CYMRU   = CircuitBreaker('cymru-dns',      max_failures=3, cooldown_s=120, cache_ttl_s=600)


def _fetch_text(url: str, timeout: int = 15) -> Optional[str]:
    """Fetch URL text, return None on failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug(f"[ThreatIntel] fetch failed {url}: {exc}")
        return None


def _load_blocklists_once() -> None:
    global _FEODO_IPS, _C2INTEL_IPS, _BLOCKLIST_LOADED_AT
    now = time.time()
    with _BLOCKLIST_LOCK:
        if now - _BLOCKLIST_LOADED_AT < _BLOCKLIST_TTL:
            return
        feodo_new: set = set()
        c2intel_new: set = set()

        # Feodo Tracker — CSV; IP is in the second column (dst_ip), skip comment/header lines
        text = _CB_FEODO.call(lambda: _fetch_text(_FEODO_URL))
        if text:
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(",")
                # Format: first_seen_utc, dst_ip, dst_port, c2_status, last_online, malware
                ip = parts[1].strip().strip('"') if len(parts) > 1 else parts[0].strip().strip('"')
                # Validate it looks like an IP (skip header row)
                if ip and all(c.isdigit() or c == "." for c in ip) and ip.count(".") == 3:
                    feodo_new.add(ip)
            logger.info(f"[ThreatIntel] Feodo: loaded {len(feodo_new)} C2 IPs")

        # C2IntelFeeds — CSV, first column is IP
        text = _CB_C2INTEL.call(lambda: _fetch_text(_C2INTEL_URL))
        if text:
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(",")
                ip = parts[0].strip().strip('"')
                if ip and not ip.lower().startswith("ip"):  # skip header row
                    c2intel_new.add(ip)
            logger.info(f"[ThreatIntel] C2IntelFeeds: loaded {len(c2intel_new)} IPs")

        _FEODO_IPS   = feodo_new
        _C2INTEL_IPS = c2intel_new
        _BLOCKLIST_LOADED_AT = time.time()


def _schedule_blocklist_load() -> None:
    """Fire-and-forget background load so startup isn't delayed."""
    t = threading.Thread(target=_load_blocklists_once, daemon=True, name="blocklist-load")
    t.start()


# Kick off on import — first PCAP won't have intel yet but subsequent ones will
_schedule_blocklist_load()


class OperatorSessionManagerLike(Protocol):
    """Type protocol for OperatorSessionManager interface used by this registry."""

    def get_room_by_name(self, name: str) -> Any: ...

    # Newer canonical signature (keyword-friendly)
    def publish_to_room(
        self,
        room_id: str,
        *,
        entity_id: str,
        entity_type: str,
        entity_data: Json,
        operator: Any = None,
    ) -> Any: ...

    # Optional but used for durability/rehydration
    def get_room_entities_snapshot(self, room_id: str) -> List[Json]: ...


@dataclass
class PcapRegistryConfig:
    artifact_root: str = "assets/artifacts/pcap"
    global_room_name: str = "Global"
    persist_flows: bool = False
    persist_hosts: bool = True
    max_flow_entities: int = 2000
    emit_progress: bool = True
    enable_dpi: bool = True
    enable_geoip: bool = False
    geoip_city_mmdb: Optional[str] = None
    geoip_asn_mmdb: Optional[str] = None


class PcapRegistry:
    """
    Refactored to enforce the WriteBus chokepoint.
    This module should NOT call:
      - operator_manager.publish_to_room(...)
      - hypergraph.add_node/add_edge(...)
    Instead it constructs GraphOps and calls bus().commit(...).
    """

    def __init__(self, cfg: PcapRegistryConfig, opman: Any = None):
        self.cfg = cfg
        self.opman = opman
        os.makedirs(self.cfg.artifact_root, exist_ok=True)

        # ── GeoIP readers (lazy, opened once) ────────────────────────────
        self._geoip_city_reader = None
        self._geoip_asn_reader = None
        if HAS_MAXMINDDB and self.cfg.enable_geoip:
            if self.cfg.geoip_city_mmdb and os.path.isfile(self.cfg.geoip_city_mmdb):
                try:
                    self._geoip_city_reader = maxminddb.open_database(self.cfg.geoip_city_mmdb)
                    logger.info(f"[GeoIP] City DB loaded: {self.cfg.geoip_city_mmdb}")
                except Exception as exc:
                    logger.warning(f"[GeoIP] Failed to open City DB: {exc}")
            if self.cfg.geoip_asn_mmdb and os.path.isfile(self.cfg.geoip_asn_mmdb):
                try:
                    self._geoip_asn_reader = maxminddb.open_database(self.cfg.geoip_asn_mmdb)
                    logger.info(f"[GeoIP] ASN DB loaded: {self.cfg.geoip_asn_mmdb}")
                except Exception as exc:
                    logger.warning(f"[GeoIP] Failed to open ASN DB: {exc}")

    # ── GeoIP lookup ─────────────────────────────────────────────────────
    def _geoip_lookup(self, ip: str) -> Optional[Dict[str, Any]]:
        """
        Return { lat, lon, city, country, org } for *ip*, or None if
        no GeoIP data is available.  Private/reserved IPs always return None.
        """
        import ipaddress
        try:
            addr = ipaddress.ip_address(ip)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast:
                return None
        except ValueError:
            return None

        lat = lon = None
        city = country = org = ""

        # City / location
        if self._geoip_city_reader:
            try:
                rec = self._geoip_city_reader.get(ip)
                if rec:
                    loc = rec.get("location", {})
                    lat = loc.get("latitude")
                    lon = loc.get("longitude")
                    city_obj = rec.get("city", {})
                    city = (city_obj.get("names", {}).get("en", "")) if city_obj else ""
                    country_obj = rec.get("country", {})
                    country = (country_obj.get("iso_code", "")) if country_obj else ""
            except Exception:
                pass

        # ASN / org
        if self._geoip_asn_reader:
            try:
                asn_rec = self._geoip_asn_reader.get(ip)
                if asn_rec:
                    org = asn_rec.get("autonomous_system_organization", "")
            except Exception:
                pass

        if lat is not None and lon is not None:
            return {"lat": lat, "lon": lon, "city": city, "country": country, "org": org}
        return None

    # ── CYMRU DNS ASN lookup ──────────────────────────────────────────────
    def _cymru_asn_lookup(self, ip: str) -> Optional[Dict[str, Any]]:
        """
        Real-time ASN lookup via Team Cymru DNS — zero cost, no API key.
        Returns { asn, prefix, country, rir, allocated } or None.

        DNS query: {reversed_ip}.origin.asn.cymru.com TXT
        Example response: "15169 | 8.8.8.0/24 | US | arin | 1992-12-01"
        """
        if not HAS_DNSPYTHON:
            return None
        try:
            import ipaddress
            addr = ipaddress.ip_address(ip)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast:
                return None
            reversed_ip = ".".join(reversed(ip.split(".")))
            query = f"{reversed_ip}.origin.asn.cymru.com"

            def _dns_query():
                answers = dns.resolver.resolve(query, "TXT", lifetime=3.0)
                for rdata in answers:
                    raw = str(rdata).strip('"').strip()
                    parts = [p.strip() for p in raw.split("|")]
                    if len(parts) >= 4:
                        return {
                            "asn":       parts[0],
                            "prefix":    parts[1] if len(parts) > 1 else "",
                            "country":   parts[2] if len(parts) > 2 else "",
                            "rir":       parts[3] if len(parts) > 3 else "",
                            "allocated": parts[4] if len(parts) > 4 else "",
                            "source":    "cymru",
                        }
                return None

            return _CB_CYMRU.call(_dns_query, default=None)
        except Exception:
            pass
        return None

    # ── C2 / threat intel check ───────────────────────────────────────────
    def _threat_intel_check(self, ip: str) -> Dict[str, Any]:
        """
        Checks IP against loaded blocklists (Feodo Tracker + C2IntelFeeds).
        Returns a dict suitable for storing on a node's metadata:
          { is_c2, sources, malware_family }
        Non-blocking: if blocklists haven't been fetched yet, returns empty.
        """
        result: Dict[str, Any] = {"is_c2": False, "sources": [], "malware_family": None}
        try:
            # Refresh blocklists if stale (non-blocking; skips if TTL not exceeded)
            _load_blocklists_once()
            with _BLOCKLIST_LOCK:
                feodo_hit   = ip in _FEODO_IPS
                c2intel_hit = ip in _C2INTEL_IPS
            if feodo_hit:
                result["is_c2"] = True
                result["sources"].append("feodo_tracker")
                result["malware_family"] = "botnet_c2"
            if c2intel_hit:
                result["is_c2"] = True
                result["sources"].append("c2intel_feeds")
                if not result["malware_family"]:
                    result["malware_family"] = "c2_infrastructure"
        except Exception as exc:
            logger.debug(f"[ThreatIntel] check failed for {ip}: {exc}")
        return result

    @property
    def _room_id(self) -> Optional[str]:
        """
        Durable-first room_id resolution.

        IMPORTANT:
        - Do NOT rely only on opman.rooms (in-memory) because it may be empty after restart.
        - Prefer opman.get_room_by_name(...) which can consult SQLite-backed rooms.
        """
        if getattr(self, "_cached_room_id", None):
            return self._cached_room_id

        if not self.opman:
            return None

        # 1) Durable-first: ask the operator session manager to resolve by name.
        try:
            get_by_name = getattr(self.opman, "get_room_by_name", None)
            if callable(get_by_name):
                room = get_by_name(self.cfg.global_room_name)
                if room:
                    rid = getattr(room, "room_id", None) or getattr(room, "id", None)
                    if not rid and isinstance(room, dict):
                        rid = room.get("room_id") or room.get("id")
                    if rid:
                        self._cached_room_id = rid
                        return rid
        except Exception:
            pass

        # 2) Fallback: scan in-memory rooms map (best-effort)
        try:
            rooms = getattr(self.opman, "rooms", None)
            if isinstance(rooms, dict):
                for rid, room in rooms.items():
                    name = getattr(room, "room_name", None)
                    if not name and isinstance(room, dict):
                        name = room.get("room_name") or room.get("name")
                    if name == self.cfg.global_room_name:
                        self._cached_room_id = rid
                        return rid
        except Exception:
            pass

        return None

    def _room_snapshot(self) -> List[Json]:
        """Durable read path (DB-backed) if OperatorSessionManager supports it."""
        rid = self._room_id
        if not rid:
            return []
        snap_fn = getattr(self.opman, "get_room_entities_snapshot", None)
        if callable(snap_fn):
            try:
                return snap_fn(rid) or []
            except Exception as e:
                logger.warning(f"_room_snapshot error: {e}")
        return []

    def list_sessions(self, limit: int = 100) -> List[Json]:
        """
        Durable-first listing:
          1) OperatorSessionManager snapshot (SQLite-backed)
          2) Fallback: in-memory room entities scan (best-effort)
        """
        sessions: List[Json] = []

        # (1) Durable snapshot path
        snap = self._room_snapshot()
        if snap:
            for item in snap:
                # Snapshot items may be raw entities or wrapped records depending on implementation
                et = item.get("entity_type") or item.get("type") or item.get("entity", {}).get("entity_type")
                if et != "PCAP_SESSION":
                    continue

                data = item.get("entity_data") or item.get("data") or item.get("entity", {}).get("entity_data") or {}
                sid = data.get("session_id") or data.get("id") or data.get("entity_id") or data.get("name")
                if not sid:
                    continue

                # Normalize for UI compatibility
                display = data.get("display_name") or data.get("name") or sid
                created = data.get("created_at") or data.get("timestamp") or 0
                artifact_id = data.get("artifact_id") or (data.get("metadata") or {}).get("artifact_id")

                sessions.append({
                    "session_id": sid,
                    "id": sid,
                    "name": sid,
                    "display_name": display,
                    "created_at": created,
                    "timestamp": created,
                    "status": data.get("status", "unknown"),
                    "artifact_id": artifact_id,
                    "metadata": data.get("metadata") or {},
                })

            sessions.sort(key=lambda s: s.get("created_at", 0), reverse=True)
            return sessions[:limit]

        # (2) Fallback: in-memory room_entities path (keeps your current behavior as backup)
        if not self.opman:
            return []

        rid = self._room_id
        if not rid or not hasattr(self.opman, "room_entities") or rid not in self.opman.room_entities:
            return []

        entities = self.opman.room_entities[rid]
        for eid, entry in entities.items():
            if entry.get("type") == "PCAP_SESSION":
                data = entry.get("data", {}).copy()
                # Ensure canonical and aliases
                data["id"] = eid
                data.setdefault("session_id", eid)
                data.setdefault("name", eid)
                data.setdefault("display_name", eid)
                sessions.append(data)

        sessions.sort(key=lambda x: x.get("timestamp", 0) or 0, reverse=True)
        return sessions[:limit]

    def get_session_subgraph(self, session_id: str, depth: int = 2, *, hydrate_graph: bool = True) -> Optional[Json]:
        """
        Durable-first session subgraph.

        This does NOT require HypergraphEngine to already contain the session node.
        It reconstructs nodes/edges from SQLite snapshot entities:

          - PCAP_SESSION (session core, includes artifact_id, capture_site, sensor_id)
          - PCAP_ARTIFACT (artifact metadata)
          - PCAP_ACTIVITY (session activity; ingest_complete payload can reconstruct host/geo edges)

        If hydrate_graph=True and bus().hypergraph exists, it will also seed the in-memory graph.
        """
        sid = (session_id or "").strip()
        if not sid:
            return None

        depth_i = max(1, min(int(depth or 2), 6))

        snap = self._room_snapshot()
        if not snap:
            return None

        def _unwrap(item: Json) -> Json:
            if isinstance(item, dict) and isinstance(item.get("entity"), dict):
                return item["entity"]
            return item

        def _etype(item: Json) -> Optional[str]:
            item = _unwrap(item)
            return item.get("entity_type") or item.get("type")

        def _edata(item: Json) -> Json:
            item = _unwrap(item)
            d = item.get("entity_data") or item.get("data") or {}
            return d if isinstance(d, dict) else {}

        def _eid(item: Json) -> Optional[str]:
            item = _unwrap(item)
            return item.get("entity_id") or item.get("id")

        # ---- find the session durable record ----
        sess_data: Optional[Json] = None
        for it in snap:
            if _etype(it) != "PCAP_SESSION":
                continue
            d = _edata(it)
            candidate = d.get("id") or d.get("session_id") or _eid(it)
            if candidate == sid:
                sess_data = d
                break

        if not sess_data:
            return None

        now = time.time()
        ts = sess_data.get("timestamp") or sess_data.get("created_at") or now
        meta = sess_data.get("metadata") or {}

        artifact_id = sess_data.get("artifact_id") or meta.get("artifact_id")
        artifact_sha256 = meta.get("artifact_sha256")

        nodes_by_id: Dict[str, Json] = {}
        edges: List[Json] = []

        def _add_node(n: Json):
            nid = n.get("id")
            if nid and nid not in nodes_by_id:
                nodes_by_id[nid] = n

        def _add_edge(e: Json):
            if e and e.get("id"):
                edges.append(e)

        # ---- session node ----
        _add_node({
            "id": sid,
            "kind": "pcap_session",
            "created_at": ts,
            "labels": {
                "status": sess_data.get("status", "created"),
                "operator": meta.get("operator"),
            },
            "metadata": meta,
        })

        if depth_i == 1:
            return {"nodes": list(nodes_by_id.values()), "edges": [], "stats": {"depth": depth_i, "source": "snapshot"}}

        # ---- artifact node (prefer durable record if present, else stub) ----
        art_data: Optional[Json] = None
        if artifact_id:
            for it in snap:
                if _etype(it) != "PCAP_ARTIFACT":
                    continue
                d = _edata(it)
                if (d.get("id") or _eid(it)) == artifact_id:
                    art_data = d
                    break

            if art_data:
                _add_node({
                    "id": artifact_id,
                    "kind": "pcap_artifact",
                    "created_at": art_data.get("timestamp") or ts,
                    "labels": {
                        "sha256": (art_data.get("metadata") or {}).get("sha256") or artifact_sha256,
                        "name": art_data.get("name"),
                    },
                    "metadata": art_data.get("metadata") or {},
                })
            else:
                _add_node({
                    "id": artifact_id,
                    "kind": "pcap_artifact",
                    "created_at": ts,
                    "labels": {"sha256": artifact_sha256, "stub": True},
                    "metadata": {},
                })

            _add_edge({
                "id": f"edge_{sid}_has_{artifact_id}",
                "kind": "SESSION_HAS_ARTIFACT",
                "nodes": [sid, artifact_id],
                "timestamp": ts,
            })

        # ---- capture_site geo node (from session metadata) ----
        capture_site = meta.get("capture_site")
        if isinstance(capture_site, dict) and "lat" in capture_site and "lon" in capture_site:
            try:
                lat = float(capture_site["lat"])
                lon = float(capture_site["lon"])
                alt = float(capture_site.get("alt_m", 0))
                geo_id = f"geo_{lat:.5f}_{lon:.5f}"
                _add_node({
                    "id": geo_id,
                    "kind": "geo_point",
                    "position": [lat, lon, alt],
                    "labels": {"type": "capture_site", "label": capture_site.get("label", "PCAP Capture")},
                    "metadata": {"capture_site": capture_site},
                })
                _add_edge({
                    "id": f"edge_{sid}_at_{geo_id}",
                    "kind": "SESSION_CAPTURED_AT",
                    "nodes": [sid, geo_id],
                    "timestamp": ts,
                    "metadata": {"confidence": 1.0},
                })
            except Exception:
                pass

        # ---- sensor node (from session metadata) ----
        sensor_id = meta.get("sensor_id")
        if sensor_id:
            s_node = sensor_id if str(sensor_id).startswith("sensor:") else f"sensor:{sensor_id}"
            _add_node({"id": s_node, "kind": "sensor", "labels": {"sensor_id": sensor_id}, "metadata": {}})
            _add_edge({
                "id": f"edge_{sid}_captured_by",
                "kind": "SESSION_CAPTURED_BY_SENSOR",
                "nodes": [sid, s_node],
                "timestamp": ts,
            })

        # ---- activities (durable) ----
        activities: List[Json] = []
        for it in snap:
            if _etype(it) != "PCAP_ACTIVITY":
                continue
            d = _edata(it)
            if d.get("session_id") != sid:
                continue
            activities.append(d)

        # Add activity nodes + edges
        for a in activities:
            aid = a.get("id")
            if not aid:
                continue
            kind = a.get("kind") or "activity"
            ats = a.get("timestamp") or ts
            payload = a.get("payload") or {}
            _add_node({
                "id": aid,
                "kind": "pcap_activity",
                "created_at": ats,
                "labels": {"type": kind},
                "metadata": payload,
            })
            _add_edge({
                "id": f"edge_{sid}_had_{aid}",
                "kind": "SESSION_ACTIVITY",
                "nodes": [sid, aid],
                "timestamp": ats,
            })

        # ---- bonus: reconstruct host/geo topology from ingest_complete payload if present ----
        ingest_complete = None
        for a in reversed(sorted(activities, key=lambda x: x.get("timestamp", 0))):
            if (a.get("kind") or "") == "pcap_ingest_complete":
                ingest_complete = a
                break

        if ingest_complete:
            payload = ingest_complete.get("payload") or {}
            geo_points = payload.get("geo_points") or []
            if isinstance(geo_points, list):
                for gp in geo_points:
                    try:
                        ip = gp.get("ip")
                        if not ip:
                            continue
                        host_id = f"host_{ip}"
                        lat = float(gp.get("lat"))
                        lon = float(gp.get("lon"))
                        geo_id = f"geo_{lat:.5f}_{lon:.5f}"
                        _add_node({
                            "id": host_id,
                            "kind": "host",
                            "position": [lat, lon, 0],
                            "labels": {"ip": ip, "org": gp.get("org"), "bytes": gp.get("bytes")},
                            "metadata": {"city": gp.get("city"), "country": gp.get("country")},
                        })
                        _add_node({
                            "id": geo_id,
                            "kind": "geo_point",
                            "position": [lat, lon, 0],
                            "labels": {"city": gp.get("city"), "country": gp.get("country")},
                            "metadata": {},
                        })
                        _add_edge({
                            "id": f"e_{host_id}_geo_{geo_id}",
                            "kind": "HOST_GEO_ESTIMATE",
                            "nodes": [host_id, geo_id],
                            "timestamp": ingest_complete.get("timestamp") or ts,
                        })
                        _add_edge({
                            "id": f"e_{sid}_obs_{host_id}",
                            "kind": "SESSION_OBSERVED_HOST",
                            "nodes": [sid, host_id],
                            "timestamp": ingest_complete.get("timestamp") or ts,
                        })
                    except Exception:
                        continue

        sg = {
            "nodes": list(nodes_by_id.values()),
            "edges": edges,
            "stats": {
                "depth": depth_i,
                "source": "snapshot",
                "node_count": len(nodes_by_id),
                "edge_count": len(edges),
            },
        }

        # Optional: seed the in-memory hypergraph so future calls don't 404
        if hydrate_graph:
            try:
                hg = getattr(bus(), "hypergraph", None)
                if hg and hasattr(hg, "apply_graph_event"):
                    # nodes
                    for n in sg["nodes"]:
                        hg.apply_graph_event({"event_type": "NODE_UPSERT", "entity_id": n["id"], "entity_data": n})
                    # edges
                    for e in sg["edges"]:
                        hg.apply_graph_event({"event_type": "EDGE_UPSERT", "entity_id": e["id"], "entity_data": e})
            except Exception:
                pass

        return sg

    def _ctx(self, *, operator: Optional[str], mission_id: Optional[str], source: str, evidence_refs: Optional[List[str]] = None) -> WriteContext:
        return WriteContext(
            room_name=self.cfg.global_room_name,
            mission_id=mission_id,
            operator_id=operator,
            source=source,
            evidence_refs=list(evidence_refs or []),
        )

    def _node_op(self, node: Json) -> GraphOp:
        nid = node.get("id")
        if not nid:
            raise ValueError("node must include id")
        return GraphOp(event_type="NODE_UPDATE", entity_id=str(nid), entity_data=node)

    def _edge_op(self, edge: Json) -> GraphOp:
        eid = edge.get("id")
        if not eid:
            raise ValueError("edge must include id")
        return GraphOp(event_type="EDGE_UPDATE", entity_id=str(eid), entity_data=edge)

    def _graph_batch(self, *, batch_id: str, ops: List[GraphOp], ctx: WriteContext) -> None:
        # A graph-only commit for firehose-style updates (no room persistence)
        if not ops:
            return
        bus().commit(
            entity_id=batch_id,
            entity_type="PCAP_GRAPH_BATCH",
            entity_data={"id": batch_id, "type": "PCAP_GRAPH_BATCH", "count": len(ops), "timestamp": time.time()},
            graph_ops=ops,
            ctx=ctx,
            persist=False,
            audit=False,
        )

    # -------------------------------------------------------------------------
    # Artifact: durable + graph node
    # -------------------------------------------------------------------------
    def upsert_pcap_artifact(
        self,
        *,
        file_bytes: Optional[bytes] = None,
        file_path: Optional[str] = None,
        original_name: Optional[str] = None,
        operator: Optional[str] = None,
        mission_id: Optional[str] = None,
        sensor_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        content_type: str = "application/vnd.tcpdump.pcap",
    ) -> Json:
        if file_bytes is None and file_path is None:
            raise ValueError("Must provide either file_bytes or file_path")

        if file_bytes is None:
            with open(file_path, "rb") as f:
                file_bytes = f.read()

        sha256 = hashlib.sha256(file_bytes).hexdigest()
        size_bytes = len(file_bytes)
        artifact_id = f"ARTIFACT-PCAP-{sha256[:12].upper()}"

        # Persist bytes to artifact_root (idempotent by sha)
        out_path = os.path.join(self.cfg.artifact_root, f"{sha256}.pcap")
        if not os.path.exists(out_path):
            with open(out_path, "wb") as f:
                f.write(file_bytes)

        artifact_uri = f"/{self.cfg.artifact_root}/{sha256}.pcap".replace("//", "/")
        now = time.time()
        meta = {
            "original_name": original_name or "unknown.pcap",
            "content_type": content_type,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "uploaded_by": operator or "system",
            "tags": tags or [],
            "mission_id": mission_id,
            "sensor_id": sensor_id,
            "artifact_uri": artifact_uri,
        }

        node = {
            "id": artifact_id,
            "kind": "pcap_artifact",
            "labels": {"sha256": sha256, "size": size_bytes, "name": meta["original_name"]},
            "metadata": meta,
            "created_at": now,
        }

        durable = {
            "id": artifact_id,
            "type": "PCAP_ARTIFACT",
            "name": meta["original_name"],
            "metadata": meta,
            "uri": artifact_uri,
            "timestamp": now,
        }

        ctx = self._ctx(operator=operator, mission_id=mission_id, source="pcap_artifact", evidence_refs=[sha256, artifact_uri])

        res = bus().commit(
            entity_id=artifact_id,
            entity_type="PCAP_ARTIFACT",
            entity_data=durable,
            graph_ops=[self._node_op(node)],
            ctx=ctx,
            persist=True,
            audit=True,
        )

        return {
            "ok": res.ok,
            "artifact_id": artifact_id,
            "sha256": sha256,
            "bytes": size_bytes,
            "uri": artifact_uri,
            "content_type": content_type,
            "created_at": now,
            "persisted": res.persisted,
            "graph_applied": res.graph_applied,
            "errors": res.errors,
            "write_debug": res.debug,
        }

    # -------------------------------------------------------------------------
    # Session: durable + graph node/edges
    # -------------------------------------------------------------------------
    def create_pcap_session(
        self,
        *,
        artifact_sha256: str,
        operator: Optional[str] = None,
        mission_id: Optional[str] = None,
        sensor_id: Optional[str] = None,
        capture_site: Optional[Json] = None,  # {lat, lon, alt_m, label?}
        tags: Optional[List[str]] = None,
        ingest_plan: Optional[Json] = None,
    ) -> Json:
        now = time.time()
        session_id = f"SESSION-{int(now * 1000)}"
        artifact_id = f"ARTIFACT-PCAP-{artifact_sha256[:12].upper()}"

        session_meta = {
            "operator": operator,
            "mission_id": mission_id,
            "sensor_id": sensor_id,
            "capture_site": capture_site,
            "tags": tags or [],
            "ingest_plan": ingest_plan or {},
            "artifact_sha256": artifact_sha256,
        }

        session_node = {
            "id": session_id,
            "kind": "pcap_session",
            "created_at": now,
            "metadata": session_meta,
            "labels": {"operator": operator, "status": "created"},
        }

        edges: List[Json] = [
            {"id": f"edge_{session_id}_has_{artifact_id}", "kind": "SESSION_HAS_ARTIFACT", "nodes": [session_id, artifact_id], "timestamp": now}
        ]

        # Stub artifact node to prevent dangling edge referential integrity issues
        artifact_stub = {
            "id": artifact_id,
            "kind": "pcap_artifact",
            "labels": {"sha256": artifact_sha256, "stub": True}
        }
        graph_ops: List[GraphOp] = [self._node_op(session_node), self._node_op(artifact_stub)]

        if sensor_id:
            # Connect to the sensor node id if you're using namespaced ids elsewhere
            s_node = sensor_id if str(sensor_id).startswith("sensor:") else f"sensor:{sensor_id}"
            edges.append({"id": f"edge_{session_id}_captured_by", "kind": "SESSION_CAPTURED_BY_SENSOR", "nodes": [session_id, s_node], "timestamp": now})

        if capture_site and "lat" in capture_site and "lon" in capture_site:
            # Quantize geo coordinates to avoid floating point drift unique IDs
            lat, lon = float(capture_site["lat"]), float(capture_site["lon"])
            geo_id = f"geo_{lat:.5f}_{lon:.5f}"
            geo_node = {
                "id": geo_id,
                "kind": "geo_point",
                "position": [lat, lon, capture_site.get("alt_m", 0)],
                "labels": {"type": "capture_site"},
                "metadata": {"capture_site": capture_site},
            }
            edges.append({"id": f"edge_{session_id}_at_{geo_id}", "kind": "SESSION_CAPTURED_AT", "nodes": [session_id, geo_id], "timestamp": now, "metadata": {"confidence": 1.0}})
            graph_ops.extend([self._node_op(geo_node)] + [self._edge_op(e) for e in edges])
        else:
            graph_ops.extend([self._edge_op(e) for e in edges])

        durable = {
            "id": session_id,
            "type": "PCAP_SESSION",
            "name": f"Session {session_id}",
            "artifact_id": artifact_id,
            "metadata": session_meta,
            "timestamp": now,
            "status": "created",
        }

        ctx = self._ctx(operator=operator, mission_id=mission_id, source="pcap_session", evidence_refs=[artifact_sha256])

        res = bus().commit(
            entity_id=session_id,
            entity_type="PCAP_SESSION",
            entity_data=durable,
            graph_ops=graph_ops,
            ctx=ctx,
            persist=True,
            audit=True,
        )

        return {
            "ok": res.ok,
            "session_id": session_id,
            # UX aliases
            "id": session_id,
            "name": session_id,
            "display_name": session_id,
            "artifact_id": artifact_id,
            "persisted": res.persisted,
            "graph_applied": res.graph_applied,
            "errors": res.errors,
            "write_debug": res.debug,
        }

    # -------------------------------------------------------------------------
    # Ingest: graph firehose + optional durable progress/activity
    # -------------------------------------------------------------------------
    def ingest_pcap_session(
        self,
        *,
        session_id: str,
        mode: str = "flows",
        dpi: Optional[bool] = None,
        geoip: Optional[bool] = None,
        emit_limit: Optional[int] = None,
        time_bucket_s: int = 60,
        operator: Optional[str] = None,
        mission_id: Optional[str] = None,
    ) -> Json:
        """
        Real PCAP ingestion with fallback chain: Scapy > dpkt > simulation
          - emits host/geo nodes + edges
          - emits flow aggregate nodes + edges (bounded by cfg.max_flow_entities)
          - uses WriteBus for ALL graph and durable activity events
        """
        ctx = self._ctx(operator=operator, mission_id=mission_id, source="pcap_ingest", evidence_refs=[session_id])

        self.emit_ingest_event(session_id=session_id, kind="pcap_ingest_started", payload={"mode": mode}, operator=operator, mission_id=mission_id)

        # Resolve PCAP file path from session metadata
        pcap_path = self._resolve_pcap_path(session_id)

        summary = None
        parser_used = None

        # Try parsers in order: Scapy > dpkt > simulation
        if pcap_path and HAS_SCAPY:
            try:
                summary = self._ingest_with_scapy(session_id=session_id, pcap_path=pcap_path, mode=mode, ctx=ctx)
                parser_used = "scapy"
            except Exception as e:
                logger.warning(f"Scapy parsing failed for {session_id}: {e}")

        if not summary and pcap_path and HAS_DPKT:
            try:
                summary = self._ingest_with_dpkt(session_id=session_id, pcap_path=pcap_path, mode=mode, ctx=ctx)
                parser_used = "dpkt"
            except Exception as e:
                logger.warning(f"dpkt parsing failed for {session_id}: {e}")

        # Fallback to simulation if no parser worked or no pcap file
        if not summary:
            summary = self._ingest_simulation(session_id=session_id, mode=mode, ctx=ctx)
            parser_used = "simulation"

        summary["parser"] = parser_used

        self.emit_ingest_event(session_id=session_id, kind="pcap_ingest_complete", payload=summary, operator=operator, mission_id=mission_id)
        return summary

    def _resolve_pcap_path(self, session_id: str) -> Optional[str]:
        """Resolve PCAP file path from session metadata (durable-first)."""
        snap = self._room_snapshot()
        if not snap:
            return None

        # Find session record
        for item in snap:
            entity = item.get("entity") if isinstance(item.get("entity"), dict) else item
            etype = entity.get("entity_type") or entity.get("type")
            if etype != "PCAP_SESSION":
                continue

            data = entity.get("entity_data") or entity.get("data") or {}
            sid = data.get("id") or data.get("session_id")
            if sid == session_id:
                meta = data.get("metadata") or {}
                sha256 = meta.get("artifact_sha256")
                if sha256:
                    # Try both .pcap and .pcapng extensions
                    for ext in [".pcap", ".pcapng"]:
                        path = os.path.join(self.cfg.artifact_root, f"{sha256}{ext}")
                        if os.path.exists(path):
                            return path
        return None

    def _ingest_with_scapy(self, session_id: str, pcap_path: str, mode: str, ctx: WriteContext) -> Json:
        """Parse PCAP using Scapy and emit graph topology + TLS/DNS/HTTP enrichments."""
        logger.info(f"[Scapy] Parsing {pcap_path} for session {session_id}")

        packets = rdpcap(pcap_path)
        flows = defaultdict(lambda: {"bytes": 0, "pkts": 0, "first_ts": None, "last_ts": None})
        hosts = {}
        # DPI enrichment accumulators (keyed by flow_key)
        dns_answers: Dict[str, List[Dict]] = defaultdict(list)   # qname → [{answer, type}]
        dns_flows: Dict[tuple, set] = defaultdict(set)            # flow_key → {qnames}
        tls_snis: Dict[tuple, str] = {}                           # flow_key → SNI
        http_hosts: Dict[tuple, str] = {}                         # flow_key → Host header

        for pkt in packets:
            if not pkt.haslayer(IP):
                continue

            ip_layer = pkt[IP]
            src_ip = ip_layer.src
            dst_ip = ip_layer.dst
            proto = ip_layer.proto
            pkt_bytes = len(pkt)
            ts = float(pkt.time)

            # Track hosts
            for ip in [src_ip, dst_ip]:
                if ip not in hosts:
                    hosts[ip] = {"bytes": 0, "pkts": 0}
                hosts[ip]["bytes"] += pkt_bytes
                hosts[ip]["pkts"] += 1

            # Track flows
            sport = dport = 0
            if pkt.haslayer(TCP):
                sport, dport = pkt[TCP].sport, pkt[TCP].dport
                proto_name = "tcp"
            elif pkt.haslayer(UDP):
                sport, dport = pkt[UDP].sport, pkt[UDP].dport
                proto_name = "udp"
            else:
                proto_name = f"proto{proto}"

            flow_key = tuple(sorted([(src_ip, sport), (dst_ip, dport)]))
            flow = flows[flow_key]
            flow["bytes"] += pkt_bytes
            flow["pkts"] += 1
            flow["proto"] = proto_name
            flow["src_ip"], flow["src_port"] = src_ip, sport
            flow["dst_ip"], flow["dst_port"] = dst_ip, dport
            if flow["first_ts"] is None:
                flow["first_ts"] = ts
            flow["last_ts"] = ts

            # ── DNS extraction ──────────────────────────────────────────
            try:
                from scapy.layers.dns import DNS, DNSQR, DNSRR
                if pkt.haslayer(DNS):
                    dns = pkt[DNS]
                    if dns.qr == 0 and dns.qd:  # query
                        qname = dns.qd.qname.decode('utf-8', errors='ignore').rstrip('.')
                        if qname and len(qname) > 2:
                            dns_answers.setdefault(qname, [])
                            dns_flows[flow_key].add(qname)
                    elif dns.qr == 1 and dns.an:  # response
                        qname = dns.qd.qname.decode('utf-8', errors='ignore').rstrip('.') if dns.qd else None
                        for i in range(dns.ancount):
                            try:
                                rr = dns.an[i] if isinstance(dns.an, list) else dns.an
                                rdata = rr.rdata if hasattr(rr, 'rdata') else str(rr)
                                rtype = rr.type if hasattr(rr, 'type') else 0
                                rname = qname or (rr.rrname.decode('utf-8', errors='ignore').rstrip('.') if hasattr(rr, 'rrname') else '')
                                if rname:
                                    dns_answers[rname].append({"answer": str(rdata), "type": rtype})
                                    dns_flows[flow_key].add(rname)
                            except Exception:
                                break
            except ImportError:
                pass

            # ── TLS SNI extraction (ClientHello) ────────────────────────
            if pkt.haslayer(TCP) and dport == 443 and flow_key not in tls_snis:
                try:
                    raw = bytes(pkt[TCP].payload)
                    sni = self._extract_tls_sni(raw)
                    if sni:
                        tls_snis[flow_key] = sni
                except Exception:
                    pass

            # ── HTTP Host header extraction ─────────────────────────────
            if pkt.haslayer(TCP) and dport in (80, 8080, 8888) and flow_key not in http_hosts:
                try:
                    raw = bytes(pkt[TCP].payload)
                    host = self._extract_http_host(raw)
                    if host:
                        http_hosts[flow_key] = host
                except Exception:
                    pass

        return self._emit_parsed_results(
            session_id=session_id,
            hosts=hosts,
            flows=flows,
            ctx=ctx,
            total_bytes=sum(h["bytes"] for h in hosts.values()),
            parser="scapy",
            dns_answers=dns_answers,
            dns_flows=dns_flows,
            tls_snis=tls_snis,
            http_hosts=http_hosts,
        )

    def _ingest_with_dpkt(self, session_id: str, pcap_path: str, mode: str, ctx: WriteContext) -> Json:
        """Parse PCAP using dpkt and emit graph topology + TLS/DNS/HTTP enrichments."""
        logger.info(f"[dpkt] Parsing {pcap_path} for session {session_id}")

        flows = defaultdict(lambda: {"bytes": 0, "pkts": 0, "first_ts": None, "last_ts": None})
        hosts = {}
        dns_answers: Dict[str, List[Dict]] = defaultdict(list)
        dns_flows: Dict[tuple, set] = defaultdict(set)            # flow_key → {qnames}
        tls_snis: Dict[tuple, str] = {}
        http_hosts: Dict[tuple, str] = {}

        with open(pcap_path, "rb") as f:
            try:
                pcap = dpkt.pcap.Reader(f)
            except ValueError:
                # Try pcapng format
                f.seek(0)
                pcap = dpkt.pcapng.Reader(f)

            for ts, buf in pcap:
                try:
                    eth = dpkt.ethernet.Ethernet(buf)
                except Exception:
                    continue

                if not isinstance(eth.data, (dpkt.ip.IP, dpkt.ip6.IP6)):
                    continue

                ip = eth.data
                if isinstance(ip, dpkt.ip6.IP6):
                    src_ip = socket.inet_ntop(socket.AF_INET6, ip.src)
                    dst_ip = socket.inet_ntop(socket.AF_INET6, ip.dst)
                else:
                    src_ip = socket.inet_ntoa(ip.src)
                    dst_ip = socket.inet_ntoa(ip.dst)
                pkt_bytes = len(buf)

                # Track hosts
                for addr in [src_ip, dst_ip]:
                    if addr not in hosts:
                        hosts[addr] = {"bytes": 0, "pkts": 0}
                    hosts[addr]["bytes"] += pkt_bytes
                    hosts[addr]["pkts"] += 1

                # Track flows
                sport = dport = 0
                proto_name = f"proto{ip.p}"

                if isinstance(ip.data, dpkt.tcp.TCP):
                    tcp = ip.data
                    sport, dport = tcp.sport, tcp.dport
                    proto_name = "tcp"
                elif isinstance(ip.data, dpkt.udp.UDP):
                    udp = ip.data
                    sport, dport = udp.sport, udp.dport
                    proto_name = "udp"

                flow_key = tuple(sorted([(src_ip, sport), (dst_ip, dport)]))
                flow = flows[flow_key]
                flow["bytes"] += pkt_bytes
                flow["pkts"] += 1
                flow["proto"] = proto_name
                flow["src_ip"], flow["src_port"] = src_ip, sport
                flow["dst_ip"], flow["dst_port"] = dst_ip, dport
                if flow["first_ts"] is None:
                    flow["first_ts"] = ts
                flow["last_ts"] = ts

                # ── DNS extraction (dpkt) ───────────────────────────────
                if isinstance(ip.data, dpkt.udp.UDP) and (sport == 53 or dport == 53):
                    try:
                        dns = dpkt.dns.DNS(ip.data.data)
                        if dns.qr == dpkt.dns.DNS_R and dns.an:
                            qname = dns.qd[0].name if dns.qd else ""
                            for rr in dns.an:
                                rdata = socket.inet_ntoa(rr.rdata) if rr.type == dpkt.dns.DNS_A else str(rr.rdata)
                                dns_answers[qname].append({"answer": rdata, "type": rr.type})
                            if qname:
                                dns_flows[flow_key].add(qname)
                        elif dns.qr == dpkt.dns.DNS_Q and dns.qd:
                            qname = dns.qd[0].name
                            dns_answers.setdefault(qname, [])
                            dns_flows[flow_key].add(qname)
                    except Exception:
                        pass

                # ── TLS SNI extraction ──────────────────────────────────
                if isinstance(ip.data, dpkt.tcp.TCP) and dport == 443 and flow_key not in tls_snis:
                    try:
                        sni = self._extract_tls_sni(bytes(ip.data.data))
                        if sni:
                            tls_snis[flow_key] = sni
                    except Exception:
                        pass

                # ── HTTP Host extraction ────────────────────────────────
                if isinstance(ip.data, dpkt.tcp.TCP) and dport in (80, 8080, 8888) and flow_key not in http_hosts:
                    try:
                        raw = bytes(ip.data.data)
                        host = self._extract_http_host(raw)
                        if host:
                            http_hosts[flow_key] = host
                    except Exception:
                        pass

        return self._emit_parsed_results(
            session_id=session_id,
            hosts=hosts,
            flows=flows,
            ctx=ctx,
            total_bytes=sum(h["bytes"] for h in hosts.values()),
            parser="dpkt",
            dns_answers=dns_answers,
            dns_flows=dns_flows,
            tls_snis=tls_snis,
            http_hosts=http_hosts,
        )

    # ── DPI helper methods ───────────────────────────────────────────────────
    @staticmethod
    def _extract_tls_sni(raw: bytes) -> str | None:
        """Extract SNI from a TLS ClientHello payload (RFC 6066 §3).

        Returns the first server_name found, or None if the payload is
        not a ClientHello or contains no SNI extension.
        """
        if len(raw) < 6:
            return None
        # TLS record: content_type=0x16 (Handshake), version, length
        if raw[0] != 0x16:
            return None
        # Handshake header: msg_type=0x01 (ClientHello)
        hs_offset = 5  # skip TLS record header
        if len(raw) <= hs_offset or raw[hs_offset] != 0x01:
            return None
        # Skip handshake header (4 bytes) + client_version (2) + random (32)
        offset = hs_offset + 4 + 2 + 32
        if offset >= len(raw):
            return None
        # session_id length
        sid_len = raw[offset]
        offset += 1 + sid_len
        if offset + 2 > len(raw):
            return None
        # cipher_suites length (2 bytes)
        cs_len = int.from_bytes(raw[offset:offset + 2], "big")
        offset += 2 + cs_len
        if offset >= len(raw):
            return None
        # compression_methods length (1 byte)
        comp_len = raw[offset]
        offset += 1 + comp_len
        if offset + 2 > len(raw):
            return None
        # extensions length
        ext_len = int.from_bytes(raw[offset:offset + 2], "big")
        offset += 2
        ext_end = offset + ext_len

        while offset + 4 <= ext_end and offset + 4 <= len(raw):
            ext_type = int.from_bytes(raw[offset:offset + 2], "big")
            ext_data_len = int.from_bytes(raw[offset + 2:offset + 4], "big")
            offset += 4
            if ext_type == 0x0000:  # server_name extension
                # SNI list: total_len (2), name_type (1=host), name_len (2), name
                if offset + 5 <= len(raw):
                    name_len = int.from_bytes(raw[offset + 3:offset + 5], "big")
                    if offset + 5 + name_len <= len(raw):
                        return raw[offset + 5:offset + 5 + name_len].decode("ascii", errors="ignore")
                return None
            offset += ext_data_len
        return None

    @staticmethod
    def _extract_http_host(raw: bytes) -> str | None:
        """Extract Host header from an HTTP/1.x request."""
        try:
            text = raw[:2048].decode("ascii", errors="ignore")
        except Exception:
            return None
        # Must start with an HTTP method
        if not text[:4] in ("GET ", "POST", "PUT ", "HEAD", "DELE", "PATC", "OPTI", "CONN"):
            return None
        for line in text.split("\r\n")[1:]:
            if line.lower().startswith("host:"):
                return line.split(":", 1)[1].strip().split(":")[0]
        return None

    def _emit_parsed_results(self, session_id: str, hosts: Dict, flows: Dict,
                             ctx: WriteContext, total_bytes: int, parser: str,
                             *,
                             dns_answers: Dict[str, List[Dict]] | None = None,
                             dns_flows: Dict[tuple, set] | None = None,
                             tls_snis: Dict[tuple, str] | None = None,
                             http_hosts: Dict[tuple, str] | None = None) -> Json:
        """Emit parsed hosts, flows, geo, port-hubs, services, and DPI enrichments.

        Topology explosion: each host gets geo nodes/edges; each flow mints
        port-hub and service nodes; flows are connected via a single
        'flow_observed' hyperedge to all relevant primitives.
        DPI enrichments: DNS names, TLS SNI, JA3, HTTP hosts are emitted
        as nodes and linked to their originating flows.
        """
        dns_answers = dns_answers or {}
        dns_flows = dns_flows or {}
        tls_snis = tls_snis or {}
        http_hosts = http_hosts or {}
        ops: List[GraphOp] = []
        nodes_created = 0
        edges_created = 0
        geo_points = []
        geo_resolved = 0

        # Track which port-hub / service / asn / org nodes we've already minted
        _minted: set = set()

        def _ensure_node(nid: str, kind: str, *, labels: dict | None = None,
                         position: list | None = None, metadata: dict | None = None):
            nonlocal nodes_created
            if nid in _minted:
                return
            _minted.add(nid)
            node = {"id": nid, "kind": kind}
            if labels:
                node["labels"] = labels
            if position:
                node["position"] = position
            if metadata:
                node["metadata"] = metadata
            ops.append(self._node_op(node))
            nodes_created += 1

        def _ensure_edge(eid: str, kind: str, node_ids: list,
                         obs_class: str = "observed", confidence: float = 1.0,
                         **meta):
            nonlocal edges_created
            if eid in _minted:
                return
            _minted.add(eid)
            edge: Json = {"id": eid, "kind": kind, "nodes": node_ids, "timestamp": time.time()}
            combined = dict(meta) if meta else {}
            combined["obs_class"] = obs_class
            combined["confidence"] = confidence
            edge["metadata"] = combined
            ops.append(self._edge_op(edge))
            edges_created += 1

        # ── 1. Host nodes + GeoIP + ASN/Org lattice ──────────────────────────
        for ip, stats in hosts.items():
            hid = _host_id(ip)

            # ── Threat intel check (Feodo + C2IntelFeeds) ──
            threat = self._threat_intel_check(ip)
            host_meta = dict(stats)
            if threat["is_c2"]:
                host_meta["threat_intel"] = threat
                logger.info(f"[ThreatIntel] 🚨 C2 match: {ip} via {threat['sources']}")

            _ensure_node(hid, "host", labels={"ip": ip, "bytes": stats["bytes"], "pkts": stats["pkts"]}, metadata=host_meta)

            # Link host → session
            sess_edge = _sess_host_edge(session_id, hid)
            _ensure_edge(sess_edge, "SESSION_OBSERVED_HOST", [session_id, hid])

            # ── GeoIP enrichment → geo_point node + HOST_GEO_ESTIMATE edge ──
            geo = self._geoip_lookup(ip)
            if geo:
                lat, lon = geo["lat"], geo["lon"]
                gid = _geo_id(lat, lon)
                city = geo.get("city", "")
                country = geo.get("country", "")
                org_name = geo.get("org", "")

                _ensure_node(gid, "geo_point",
                             position=[lat, lon, 0],
                             labels={"city": city, "country": country})
                _ensure_edge(
                    _host_geo_edge(hid, gid),
                    "HOST_GEO_ESTIMATE", [hid, gid],
                    confidence=0.6, source="geoip")

                # ASN node + edge — prefer CYMRU real-time over MaxMind static
                asn_num = geo.get("asn")
                cymru   = self._cymru_asn_lookup(ip)
                if cymru and not asn_num:
                    asn_num  = cymru.get("asn")
                    org_name = org_name or ""  # CYMRU doesn't give org name; keep MaxMind's
                    country  = country or cymru.get("country", "")

                if asn_num:
                    asn_labels = {"asn": asn_num, "org": org_name}
                    if cymru:
                        asn_labels.update({
                            "prefix":    cymru.get("prefix", ""),
                            "rir":       cymru.get("rir", ""),
                            "allocated": cymru.get("allocated", ""),
                        })
                    aid = _asn_id(asn_num)
                    _ensure_node(aid, "asn", labels=asn_labels)
                    _ensure_edge(
                        _host_asn_edge(hid, aid),
                        "HOST_IN_ASN", [hid, aid],
                        confidence=0.85, source="cymru" if cymru else "geoip")

                    # Org node
                    if org_name:
                        oid = _org_id(org_name)
                        _ensure_node(oid, "org", labels={"name": org_name})
                        _ensure_edge(
                            _asn_org_edge(aid, oid),
                            "ASN_IN_ORG", [aid, oid],
                            confidence=0.80, source="geoip")

                geo_points.append({
                    "ip": ip, "bytes": stats["bytes"],
                    "lat": lat, "lon": lon,
                    "city": city, "country": country,
                    "org": org_name,
                })
                geo_resolved += 1

        logger.info(f"[GeoIP] {geo_resolved}/{len(hosts)} hosts resolved to lat/lon")

        # ── 2. Flow nodes + port-hub + service + flow_observed hyperedge ─────
        flow_list = sorted(flows.items(), key=lambda x: x[1]["bytes"], reverse=True)
        for i, (flow_key, flow_data) in enumerate(flow_list[:self.cfg.max_flow_entities]):
            src_ip = flow_data.get("src_ip", "")
            dst_ip = flow_data.get("dst_ip", "")
            src_port = int(flow_data.get("src_port", 0))
            dst_port = int(flow_data.get("dst_port", 0))
            proto = flow_data.get("proto", "ip")

            fid = _flow_id(session_id, src_ip, src_port, dst_ip, dst_port, proto)
            flow_node = {
                "id": fid,
                "kind": "flow",
                "labels": {
                    "proto": proto, "bytes": flow_data["bytes"], "pkts": flow_data["pkts"],
                    "src_ip": src_ip, "dst_ip": dst_ip, "dst_port": dst_port,
                },
                "metadata": flow_data,
            }
            _ensure_node(fid, "flow", labels=flow_node["labels"], metadata=flow_data)

            # ── Port-hub node ────────────────────────────────────────────
            pid = None
            if dst_port and dst_port > 0:
                pid = _port_hub_id(proto, dst_port)
                _ensure_node(pid, "port_hub", labels={"proto": proto, "port": dst_port})
                _ensure_edge(
                    _flow_port_edge(fid, pid),
                    "FLOW_DST_PORT", [fid, pid])

            # ── Service node (heuristic from port) ───────────────────────
            sid = None
            svc_name = _infer_service(dst_port)
            if not svc_name and dst_port in {80, 443, 22, 53, 8080}:
                svc_map = {80: "http", 443: "https", 22: "ssh", 53: "dns", 8080: "http-alt"}
                svc_name = svc_map.get(dst_port)
            if svc_name:
                sid = _service_id(svc_name)
                _ensure_node(sid, "service", labels={"name": svc_name})
                if pid:
                    _ensure_edge(
                        _port_svc_edge(pid, sid),
                        "PORT_IMPLIED_SERVICE", [pid, sid],
                        obs_class="implied", confidence=0.70)

            # ── flow_observed HYPEREDGE (connects flow to all member entities) ──
            src_hid = _host_id(src_ip)
            dst_hid = _host_id(dst_ip)
            he_members = [fid, src_hid, dst_hid]
            if pid:
                he_members.append(pid)
            if sid:
                he_members.append(sid)
            he_id = _flow_observed_edge_id(fid)
            _ensure_edge(he_id, "flow_observed", he_members)

            # Link flow to session (classic dyadic edge kept for backward compat)
            sess_flow_edge = _sess_flow_edge(session_id, fid)
            _ensure_edge(sess_flow_edge, "SESSION_OBSERVED_FLOW", [session_id, fid])

        # ── 2b. DPI enrichment nodes: DNS / TLS-SNI / HTTP-Host ──────────────
        dpi_stats = {"dns_names": 0, "tls_snis": 0, "http_hosts": 0}

        # DNS names → dns_name nodes + FLOW_QUERIED_DNS edges
        for qname, answers in dns_answers.items():
            if not qname:
                continue
            dnid = _dns_name_id(qname)
            _ensure_node(dnid, "dns_name",
                         labels={"qname": qname, "answer_count": len(answers)},
                         metadata={"answers": answers[:20]})  # cap stored answers
            dpi_stats["dns_names"] += 1

        # DNS flow→qname edges (FLOW_QUERIED_DNS)
        for flow_key, qnames in dns_flows.items():
            flow_data = flows.get(flow_key)
            if not flow_data:
                continue
            src_ip = flow_data.get("src_ip", "")
            dst_ip = flow_data.get("dst_ip", "")
            src_port = int(flow_data.get("src_port", 0))
            dst_port = int(flow_data.get("dst_port", 0))
            proto = flow_data.get("proto", "udp")
            fid = _flow_id(session_id, src_ip, src_port, dst_ip, dst_port, proto)
            for qname in qnames:
                if not qname:
                    continue
                dnid = _dns_name_id(qname)
                edge_id = _flow_dns_edge(fid, dnid)
                _ensure_edge(edge_id, "FLOW_QUERIED_DNS", [fid, dnid])

        # TLS SNIs → tls_sni nodes + FLOW_TLS_SNI edges per flow
        for flow_key, sni in tls_snis.items():
            if not sni:
                continue
            sni_nid = _tls_sni_id(sni)
            _ensure_node(sni_nid, "tls_sni", labels={"sni": sni})
            dpi_stats["tls_snis"] += 1
            # Link flow → SNI
            flow_data = flows.get(flow_key)
            if flow_data:
                src_ip = flow_data.get("src_ip", "")
                dst_ip = flow_data.get("dst_ip", "")
                src_port = int(flow_data.get("src_port", 0))
                dst_port = int(flow_data.get("dst_port", 0))
                proto = flow_data.get("proto", "tcp")
                fid = _flow_id(session_id, src_ip, src_port, dst_ip, dst_port, proto)
                edge_id = _flow_sni_edge(fid, sni_nid)
                _ensure_edge(edge_id, "FLOW_TLS_SNI", [fid, sni_nid])

        # HTTP hosts → http_host nodes + FLOW_HTTP_HOST edges per flow
        for flow_key, host in http_hosts.items():
            if not host:
                continue
            hhid = _http_host_id(host)
            _ensure_node(hhid, "http_host", labels={"host": host})
            dpi_stats["http_hosts"] += 1
            flow_data = flows.get(flow_key)
            if flow_data:
                src_ip = flow_data.get("src_ip", "")
                dst_ip = flow_data.get("dst_ip", "")
                src_port = int(flow_data.get("src_port", 0))
                dst_port = int(flow_data.get("dst_port", 0))
                proto = flow_data.get("proto", "tcp")
                fid = _flow_id(session_id, src_ip, src_port, dst_ip, dst_port, proto)
                edge_id = _flow_http_host_edge(fid, hhid)
                _ensure_edge(edge_id, "FLOW_HTTP_HOST", [fid, hhid])

        logger.info(f"[DPI] dns_names={dpi_stats['dns_names']} "
                     f"tls_snis={dpi_stats['tls_snis']} "
                     f"http_hosts={dpi_stats['http_hosts']}")

        # ── 3. Flush entire batch to hypergraph ──────────────────────────────
        self._graph_batch(batch_id=f"pcap_ingest_batch:{session_id}:{int(time.time()*1000)}", ops=ops, ctx=ctx)

        return {
            "ok": True,
            "session_id": session_id,
            "host_count": len(hosts),
            "flow_count": len(flows),
            "nodes_created": nodes_created,
            "edges_created": edges_created,
            "bytes_processed": total_bytes,
            "errors": [],
            "geo_points": geo_points,
            "parser": parser,
        }

    def _ingest_simulation(self, session_id: str, mode: str, ctx: WriteContext) -> Json:
        # Lightweight, deterministic simulation (kept from prior version)
        mock_hosts = [
            {"ip": "142.250.190.46", "lat": 37.422, "lon": -122.084, "city": "Mountain View", "country": "US", "org": "Google LLC", "bytes": 154000},
            {"ip": "1.1.1.1", "lat": -33.8688, "lon": 151.2093, "city": "Sydney", "country": "AU", "org": "Cloudflare, Inc.", "bytes": 240},
            {"ip": "140.82.112.4", "lat": 37.7749, "lon": -122.4194, "city": "San Francisco", "country": "US", "org": "GitHub, Inc.", "bytes": 4500},
            {"ip": "93.184.216.34", "lat": 42.1508, "lon": -70.8228, "city": "Norwell", "country": "US", "org": "EdgeCast", "bytes": 850000},
        ]

        nodes_created = 0
        edges_created = 0
        geo_points: List[Json] = []
        ops: List[GraphOp] = []

        for mh in mock_hosts:
            host_id = f"host_{mh['ip']}"
            # Quantize geo ID
            geo_id = f"geo_{mh['lat']:.5f}_{mh['lon']:.5f}"

            host_node = {"id": host_id, "kind": "host", "position": [mh["lat"], mh["lon"], 0], "labels": {"ip": mh["ip"], "org": mh["org"], "bytes": mh["bytes"]}}
            geo_node = {"id": geo_id, "kind": "geo_point", "position": [mh["lat"], mh["lon"], 0], "labels": {"city": mh["city"], "country": mh["country"]}}

            e_host_geo = {"id": f"e_{host_id}_geo", "kind": "HOST_GEO_ESTIMATE", "nodes": [host_id, geo_id], "timestamp": time.time()}
            e_sess_host = {"id": f"e_sess_{host_id}", "kind": "SESSION_OBSERVED_HOST", "nodes": [session_id, host_id], "timestamp": time.time()}

            ops.extend([self._node_op(host_node), self._node_op(geo_node), self._edge_op(e_host_geo), self._edge_op(e_sess_host)])
            nodes_created += 2
            edges_created += 2
            geo_points.append({"ip": mh["ip"], "lat": mh["lat"], "lon": mh["lon"], "city": mh["city"], "country": mh["country"], "org": mh["org"], "bytes": mh["bytes"]})

            # Optional: durable sensor activity (bounded rate)
            if self.cfg.emit_progress and mh["bytes"] > 1000:
                # Use unique activity ID to prevent history overwrite
                uniq_act_id = f"geo_{mh['ip']}_{int(time.time()*1000)}"
                self.emit_sensor_activity(
                    activity_id=uniq_act_id,
                    kind="geoip_resolved",
                    payload={
                        "ip": mh["ip"],
                        "geo": {"lat": mh["lat"], "lon": mh["lon"], "city": mh["city"], "country": mh["country"]},
                        "asn": {"org": mh["org"]},
                    },
                    ctx=ctx,
                )

        # Flush graph ops in one batch with ms precision to avoid collision
        self._graph_batch(batch_id=f"pcap_ingest_batch:{session_id}:{int(time.time()*1000)}", ops=ops, ctx=ctx)

        return {
            "ok": True,
            "session_id": session_id,
            "host_count": len(mock_hosts),
            "flow_count": 0,
            "nodes_created": nodes_created,
            "edges_created": edges_created,
            "bytes_processed": 0,
            "errors": [],
            "geo_points": geo_points,
            "note": "simulated",
        }

    # -------------------------------------------------------------------------
    # Activity emitters (durable, but bounded/optional)
    # -------------------------------------------------------------------------
    def emit_ingest_event(self, *, session_id: str, kind: str, payload: Json, operator: Optional[str] = None, mission_id: Optional[str] = None) -> Json:
        now = time.time()
        activity_id = f"act_{session_id}_{int(now*1000)}_{kind}"
        ctx = self._ctx(operator=operator, mission_id=mission_id, source="pcap_activity", evidence_refs=[session_id])

        node = {"id": activity_id, "kind": "pcap_activity", "labels": {"type": kind}, "metadata": payload, "created_at": now}
        edge = {"id": f"edge_{session_id}_had_{activity_id}", "kind": "SESSION_ACTIVITY", "nodes": [session_id, activity_id], "timestamp": now}

        durable = {"id": activity_id, "type": "PCAP_ACTIVITY", "session_id": session_id, "kind": kind, "payload": payload, "timestamp": now}

        # Persist only if enabled
        persist = bool(self.cfg.emit_progress)

        bus().commit(
            entity_id=activity_id,
            entity_type="PCAP_ACTIVITY",
            entity_data=durable,
            graph_ops=[self._node_op(node), self._edge_op(edge)],
            ctx=ctx,
            persist=persist,
            audit=False,  # activity spam not usually audited
        )
        return {"activity_id": activity_id}

    def emit_sensor_activity(self, *, activity_id: str, kind: str, payload: Json, ctx: WriteContext) -> None:
        # SENSOR_ACTIVITY: durable optional, plus graph hint edge if desired
        durable = {"id": activity_id, "type": "SENSOR_ACTIVITY", "kind": kind, "payload": payload, "timestamp": time.time()}
        bus().commit(
            entity_id=activity_id,
            entity_type="SENSOR_ACTIVITY",
            entity_data=durable,
            graph_ops=[],
            ctx=ctx,
            persist=True,
            audit=False,
        )


# ---------------------------
# Singleton convenience + init
# ---------------------------

_registry: Optional[PcapRegistry] = None


def init_pcap_registry(
    opman: Any,
    hg: Any,
    *,
    artifact_root: str = "assets/artifacts/pcap",
    global_room_name: str = "Global",
    persist_flows: bool = False,
    persist_hosts: bool = True,
    max_flow_entities: int = 2000,
    emit_progress: bool = True,
    enable_dpi: bool = True,
    enable_geoip: bool = False,
    geoip_city_mmdb: Optional[str] = None,
    geoip_asn_mmdb: Optional[str] = None,
) -> PcapRegistry:
    """
    Creates singleton registry. Must be called once at server init.
    This function may initialize WriteBus if not already initialized.
    """
    global _registry

    cfg = PcapRegistryConfig(
        artifact_root=artifact_root,
        global_room_name=global_room_name,
        persist_flows=persist_flows,
        persist_hosts=persist_hosts,
        max_flow_entities=max_flow_entities,
        emit_progress=emit_progress,
        enable_dpi=enable_dpi,
        enable_geoip=enable_geoip,
        geoip_city_mmdb=geoip_city_mmdb,
        geoip_asn_mmdb=geoip_asn_mmdb,
    )

    # Ensure WriteBus exists (do not bypass by using opman/hg directly)
    try:
        bus()
    except Exception:
        init_writebus(opman, hg, default_room=global_room_name)

    _registry = PcapRegistry(cfg, opman=opman)
    return _registry


def registry() -> PcapRegistry:
    if _registry is None:
        raise RuntimeError("PcapRegistry not initialized. Call init_pcap_registry(...) at server startup.")
    return _registry


def upsert_pcap_artifact(**kwargs) -> Json:
    return registry().upsert_pcap_artifact(**kwargs)


def create_pcap_session(**kwargs) -> Json:
    return registry().create_pcap_session(**kwargs)


def ingest_pcap_session(**kwargs) -> Json:
    return registry().ingest_pcap_session(**kwargs)
