"""
MapStateCache — SQLite-backed multi-layer spatial cache.

Survives orchestrator restarts, process crashes, and container churn.
Zero extra dependencies (uses only the stdlib sqlite3 + json modules).

Layers:
  1. Arc State       — edges with lat/lon + metadata; TTL-based expiry
  2. Geo-Path        — cached traceroute+GeoIP results; per-route TTL
  3. Camera State    — last globe camera position for warm preload
  4. Node Geo Index  — persistent node_id → {lat, lon, confidence, method} resolver.
                       Eliminates hot-path recon_system lookups on every edge persist.
                       Survives restarts — arc persistence works even before the in-memory
                       recon graph rebuilds. Supports predictive inference via neighbor
                       propagation and ASN centroid fallback.
"""

import os
import json
import math
import time
import threading
import sqlite3
import logging

logger = logging.getLogger(__name__)

_DEFAULT_DB = os.path.join(os.path.dirname(__file__), 'data', 'map_cache.db')


class MapStateCache:
    """Thread-safe SQLite-backed spatial state cache.

    Typical usage:
        cache = MapStateCache()
        cache.upsert_node_geo('hostA', 37.7, -122.4)
        cache.persist_arc('e1', 'hostA', 'hostB', 0.8, 0.5, 0, 0, 'FLOW')
        arcs = cache.restore_arcs(max_age_secs=86400)
    """

    def __init__(self, db_path: str = _DEFAULT_DB):
        self._db   = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_schema()
        logger.info(f'[MapCache] SQLite cache at {db_path}')

    # ── Connection factory (each call is a short-lived transaction) ─────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        return conn

    # ── Schema ───────────────────────────────────────────────────────────────

    def _init_schema(self):
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS arc_state (
                    edge_id       TEXT    PRIMARY KEY,
                    src_id        TEXT    NOT NULL,
                    src_lat       REAL    NOT NULL,
                    src_lon       REAL    NOT NULL,
                    dst_id        TEXT    NOT NULL,
                    dst_lat       REAL    NOT NULL,
                    dst_lon       REAL    NOT NULL,
                    conf          REAL    NOT NULL DEFAULT 0.5,
                    entropy       REAL    NOT NULL DEFAULT 0.5,
                    rf_corr       REAL    NOT NULL DEFAULT 0.0,
                    shadow        INTEGER NOT NULL DEFAULT 0,
                    kind          TEXT    NOT NULL DEFAULT 'FLOW',
                    last_seen     REAL    NOT NULL,
                    anomaly_score REAL    NOT NULL DEFAULT 0.0
                );

                CREATE INDEX IF NOT EXISTS idx_arc_last_seen
                    ON arc_state(last_seen);

                CREATE TABLE IF NOT EXISTS geo_path_cache (
                    cache_key TEXT PRIMARY KEY,
                    data      TEXT NOT NULL,
                    expires   REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_geo_expires
                    ON geo_path_cache(expires);

                CREATE TABLE IF NOT EXISTS camera_state (
                    id      INTEGER PRIMARY KEY CHECK (id = 1),
                    lat     REAL    NOT NULL DEFAULT 20.0,
                    lon     REAL    NOT NULL DEFAULT 0.0,
                    height  REAL    NOT NULL DEFAULT 15000000.0,
                    saved   REAL    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS node_geo_index (
                    node_id      TEXT    PRIMARY KEY,
                    lat          REAL    NOT NULL,
                    lon          REAL    NOT NULL,
                    alt          REAL    NOT NULL DEFAULT 0.0,
                    asn          TEXT,
                    confidence   REAL    NOT NULL DEFAULT 1.0,
                    method       TEXT    NOT NULL DEFAULT 'observed',
                    last_updated REAL    NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_node_geo_time
                    ON node_geo_index(last_updated);
                CREATE INDEX IF NOT EXISTS idx_node_geo_conf
                    ON node_geo_index(confidence);
            """)
        # ── Migration: add anomaly_score column to existing databases ────────
        with self._conn() as c:
            cols = {row[1] for row in c.execute("PRAGMA table_info(arc_state)")}
            if 'anomaly_score' not in cols:
                c.execute("ALTER TABLE arc_state ADD COLUMN anomaly_score REAL NOT NULL DEFAULT 0.0")
                logger.info('[MapCache] migrated arc_state: added anomaly_score column')

    # ── Arc State ────────────────────────────────────────────────────────────

    def persist_arc(self, edge_id: str,
                    src_id: str, src_lat: float, src_lon: float,
                    dst_id: str, dst_lat: float, dst_lon: float,
                    conf: float, entropy: float, rf_corr: float,
                    shadow: int = 0, kind: str = 'FLOW',
                    anomaly_score: float = 0.0):
        """Upsert a single arc into the cache. Thread-safe."""
        ts = time.time()
        with self._lock:
            with self._conn() as c:
                c.execute("""
                    INSERT OR REPLACE INTO arc_state
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (edge_id, src_id, src_lat, src_lon,
                      dst_id, dst_lat, dst_lon,
                      conf, entropy, rf_corr, shadow, kind, ts,
                      float(anomaly_score)))

    def persist_arc_by_ids(self, edge_id: str,
                           src_id: str, dst_id: str,
                           conf: float = 0.5, entropy: float = 0.5,
                           rf_corr: float = 0.0, shadow: int = 0,
                           kind: str = 'FLOW',
                           anomaly_score: float = 0.0) -> bool:
        """Persist arc using the node geo index for coordinate resolution.

        Returns True if coords were resolved and arc was persisted, False if
        either node is missing from the geo index (arc is dropped silently).
        This is the preferred hot-path method — no recon_system lookup needed.
        """
        src = self.get_node_geo(src_id)
        dst = self.get_node_geo(dst_id)
        if src is None or dst is None:
            return False
        self.persist_arc(edge_id,
                         src_id, src['lat'], src['lon'],
                         dst_id, dst['lat'], dst['lon'],
                         conf, entropy, rf_corr, shadow, kind,
                         anomaly_score=anomaly_score)
        return True

    def restore_arcs(self, max_age_secs: float = 86400.0) -> list:
        """Return all arcs newer than max_age_secs as dicts ready for globe edge_update."""
        cutoff = time.time() - max_age_secs
        with self._conn() as c:
            rows = c.execute("""
                SELECT edge_id, src_id, src_lat, src_lon, dst_id, dst_lat, dst_lon,
                       conf, entropy, rf_corr, shadow, kind, last_seen,
                       COALESCE(anomaly_score, 0.0) AS anomaly_score
                FROM   arc_state
                WHERE  last_seen >= ?
                ORDER  BY last_seen DESC
            """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    def arc_count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM arc_state").fetchone()[0]

    def vacuum_stale_arcs(self, max_age_secs: float = 86400.0) -> int:
        """Delete arcs older than max_age_secs. Returns deleted count."""
        cutoff = time.time() - max_age_secs
        with self._lock:
            with self._conn() as c:
                n = c.execute("DELETE FROM arc_state WHERE last_seen < ?",
                              (cutoff,)).rowcount
        if n:
            logger.debug(f'[MapCache] vacuumed {n} stale arcs')
        return n

    # ── Geo-Path Cache ───────────────────────────────────────────────────────

    def cache_geo_path(self, target: str, data: dict, ttl_secs: float = 86400.0):
        """Cache a geo-path result. Key = target IP/hostname."""
        expires = time.time() + ttl_secs
        with self._lock:
            with self._conn() as c:
                c.execute("INSERT OR REPLACE INTO geo_path_cache VALUES (?,?,?)",
                          (target, json.dumps(data), expires))

    def get_geo_path(self, target: str) -> dict | None:
        """Return cached geo-path if not expired, else None."""
        now = time.time()
        with self._conn() as c:
            row = c.execute(
                "SELECT data FROM geo_path_cache WHERE cache_key=? AND expires>?",
                (target, now)
            ).fetchone()
        return json.loads(row['data']) if row else None

    def vacuum_geo_paths(self) -> int:
        with self._lock:
            with self._conn() as c:
                n = c.execute("DELETE FROM geo_path_cache WHERE expires < ?",
                              (time.time(),)).rowcount
        return n

    # ── Camera State ─────────────────────────────────────────────────────────

    def save_camera(self, lat: float, lon: float, height: float):
        with self._lock:
            with self._conn() as c:
                c.execute("""
                    INSERT OR REPLACE INTO camera_state (id, lat, lon, height, saved)
                    VALUES (1, ?, ?, ?, ?)
                """, (lat, lon, height, time.time()))

    def get_camera(self) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT lat, lon, height FROM camera_state WHERE id=1").fetchone()
        return dict(row) if row else None

    # ── Node Geo Index ────────────────────────────────────────────────────────

    def upsert_node_geo(self, node_id: str, lat: float, lon: float,
                        alt: float = 0.0, asn: str | None = None,
                        confidence: float = 1.0, method: str = 'observed'):
        """Persist or update a node's geographic coordinates.

        Always prefer method='observed' over inferred entries — use UPDATE SET
        only when the new confidence is higher to avoid downgrading real coords.
        """
        with self._lock:
            with self._conn() as c:
                c.execute("""
                    INSERT INTO node_geo_index
                        (node_id, lat, lon, alt, asn, confidence, method, last_updated)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(node_id) DO UPDATE SET
                        lat          = CASE WHEN excluded.confidence >= confidence
                                            THEN excluded.lat ELSE lat END,
                        lon          = CASE WHEN excluded.confidence >= confidence
                                            THEN excluded.lon ELSE lon END,
                        alt          = CASE WHEN excluded.confidence >= confidence
                                            THEN excluded.alt ELSE alt END,
                        asn          = COALESCE(excluded.asn, asn),
                        confidence   = MAX(confidence, excluded.confidence),
                        method       = CASE WHEN excluded.confidence >= confidence
                                            THEN excluded.method ELSE method END,
                        last_updated = excluded.last_updated
                """, (node_id, lat, lon, alt, asn, confidence, method, time.time()))

    def get_node_geo(self, node_id: str) -> dict | None:
        """Return {lat, lon, alt, asn, confidence, method} or None.

        Fast read path — no lock (SQLite WAL handles concurrent reads).
        """
        with self._conn() as c:
            row = c.execute(
                "SELECT lat, lon, alt, asn, confidence, method FROM node_geo_index WHERE node_id=?",
                (node_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_multiple_node_geos(self, node_ids: list) -> dict:
        """Batch lookup: returns {node_id: {lat, lon, ...}} for all known nodes.

        More efficient than N individual get_node_geo calls.
        """
        if not node_ids:
            return {}
        placeholders = ','.join('?' * len(node_ids))
        with self._conn() as c:
            rows = c.execute(
                f"SELECT node_id, lat, lon, alt, asn, confidence, method "
                f"FROM node_geo_index WHERE node_id IN ({placeholders})",
                node_ids
            ).fetchall()
        return {r['node_id']: dict(r) for r in rows}

    def infer_neighbor_geo(self, node_id: str, connected_ids: list,
                           min_neighbors: int = 2) -> tuple | None:
        """Infer location from connected geo-known neighbors (centroid).

        Returns (lat, lon, confidence) if at least min_neighbors have known
        coords, else None. Confidence degrades with fewer neighbors.
        This is the neighbor-propagation predictive inference strategy.
        """
        if not connected_ids:
            return None
        known = self.get_multiple_node_geos(connected_ids)
        if len(known) < min_neighbors:
            return None
        lats = [v['lat'] for v in known.values()]
        lons = [v['lon'] for v in known.values()]
        # Simple centroid — weight by existing confidence
        weights = [v.get('confidence', 1.0) for v in known.values()]
        w_sum   = sum(weights) or 1.0
        lat = sum(la * w for la, w in zip(lats, weights)) / w_sum
        lon = sum(lo * w for lo, w in zip(lons, weights)) / w_sum
        # Confidence = base × coverage ratio, capped at 0.65 (never trust inference over reality)
        conf = min(0.65, 0.35 + 0.15 * len(known))
        return lat, lon, conf

    def infer_asn_centroid_geo(self, node_id: str, asn: str,
                                asn_centroids: dict) -> tuple | None:
        """Infer location from ASN centroid lookup table.

        asn_centroids: {asn_str: (lat, lon)} — caller provides from GeoIP data.
        Returns (lat, lon, confidence=0.4) or None.
        """
        if not asn or asn not in asn_centroids:
            return None
        lat, lon = asn_centroids[asn]
        return lat, lon, 0.4

    def node_count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM node_geo_index").fetchone()[0]

    def vacuum_stale_nodes(self, max_age_secs: float = 86400.0) -> int:
        """Remove inferred-only nodes not refreshed within max_age_secs.

        Preserves high-confidence observed entries (confidence >= 0.9).
        """
        cutoff = time.time() - max_age_secs
        with self._lock:
            with self._conn() as c:
                n = c.execute("""
                    DELETE FROM node_geo_index
                    WHERE last_updated < ? AND confidence < 0.9
                """, (cutoff,)).rowcount
        if n:
            logger.debug(f'[MapCache] vacuumed {n} stale inferred nodes')
        return n

    # ── Maintenance ──────────────────────────────────────────────────────────

    def vacuum_all(self):
        """Full vacuum: stale arcs + expired geo-paths + stale nodes + SQLite VACUUM."""
        n_arcs  = self.vacuum_stale_arcs()
        n_geo   = self.vacuum_geo_paths()
        n_nodes = self.vacuum_stale_nodes()
        with self._conn() as c:
            c.execute('VACUUM')
        logger.info(f'[MapCache] vacuum: {n_arcs} arcs, {n_geo} geo-paths, {n_nodes} nodes removed')

    def backup_db(self, backup_dir: str | None = None):
        """Create a point-in-time backup of the map cache database."""
        if backup_dir is None:
            backup_dir = os.path.join(os.path.dirname(self._db), 'backups')

        os.makedirs(backup_dir, exist_ok=True)
        filename = f"map_cache_{int(time.time())}.db"
        dest = os.path.join(backup_dir, filename)

        try:
            with self._lock:
                # Use SQLite's online backup API for a consistent snapshot
                bck = sqlite3.connect(dest)
                with self._conn() as src:
                    src.backup(bck)
                bck.close()
            logger.info(f'[MapCache] database backed up to {dest}')

            # Prune old backups (keep last 5)
            backups = sorted([os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('.db')])
            if len(backups) > 5:
                for b in backups[:-5]:
                    os.remove(b)
        except Exception as e:
            logger.error(f'[MapCache] backup failed: {e}')

    def stats(self) -> dict:
        with self._conn() as c:
            arc_count  = c.execute("SELECT COUNT(*) FROM arc_state").fetchone()[0]
            geo_count  = c.execute(
                "SELECT COUNT(*) FROM geo_path_cache WHERE expires > ?",
                (time.time(),)
            ).fetchone()[0]
            node_count = c.execute("SELECT COUNT(*) FROM node_geo_index").fetchone()[0]
            obs_nodes  = c.execute(
                "SELECT COUNT(*) FROM node_geo_index WHERE method='observed'"
            ).fetchone()[0]
            cam = c.execute("SELECT lat,lon,height FROM camera_state WHERE id=1").fetchone()
        return {
            'arc_count':        arc_count,
            'geo_path_count':   geo_count,
            'node_geo_count':   node_count,
            'node_geo_observed': obs_nodes,
            'camera':           dict(cam) if cam else None,
            'db_path':          self._db,
        }
