"""questdb_query.py

QuestDB query helpers for NerfEngine Stage 6 analytics.

Provides the SQL-path detection complement to the in-memory detectors:
  - fanin_last_window()    : top fan-in destinations in last N ms
  - top_talkers()          : heaviest edges by bytes in last window
  - recent_alerts()        : latest topology_alerts + fan_in_events
  - edge_rate()            : edges per second over a time range

These run against the QuestDB HTTP /exec endpoint.  Results are returned
as lists of dicts with column names as keys.

Usage:
    from questdb_query import fanin_last_window, recent_alerts
    hot = fanin_last_window(window_ms=200, limit=20)
    for row in hot:
        print(row['dst_ip'], row['unique_srcs'])
"""

import urllib.request
import urllib.parse
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

QUESTDB_HTTP = "http://127.0.0.1:9000"


# ── core query helper ─────────────────────────────────────────────────────────

def _query(sql: str, base: str = QUESTDB_HTTP) -> List[Dict[str, Any]]:
    """Execute a SQL query against QuestDB, return rows as list of dicts."""
    url = f"{base}/exec?query=" + urllib.parse.quote(sql)
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        data = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("QuestDB query failed: %s\n  SQL: %s", exc, sql)
        return []

    if "error" in data:
        logger.warning("QuestDB error: %s\n  SQL: %s", data["error"], sql)
        return []

    columns = [c["name"] for c in data.get("columns", [])]
    return [dict(zip(columns, row)) for row in data.get("dataset", [])]


def _recent_ts_expr(window_ms: int) -> str:
    micros = max(0, int(window_ms)) * 1000
    return f"now() - {micros}"


# ── fan-in detector (SQL path) ────────────────────────────────────────────────

def fanin_last_window(window_ms: int = 200, limit: int = 20) -> List[Dict[str, Any]]:
    """Return destinations with the most unique source IPs in the last window_ms.

    This is the QuestDB complement to TemporalFanInDetector — useful for
    post-hoc analysis or when the in-memory detector is not running.

    Returns rows: {edge_id (as dst proxy), unique_srcs, total_packets, total_bytes}
    """
    sql = f"""
SELECT
    edge_id,
    count()            AS edge_count,
    sum(packets)       AS total_packets,
    sum(bytes)         AS total_bytes
FROM flow_metrics
WHERE ts > {_recent_ts_expr(window_ms)}
GROUP BY edge_id
ORDER BY edge_count DESC
LIMIT {limit}
""".strip()
    return _query(sql)


def fanin_by_dst(window_ms: int = 200, limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent fan_in_events ordered by unique source count.

    Uses the fan_in_events table written by TemporalFanInDetector.
    """
    sql = f"""
SELECT
    ts,
    dst_node,
    unique_src_count,
    ip_entropy,
    timing_entropy,
    verdict
FROM fan_in_events
WHERE ts > {_recent_ts_expr(window_ms * 10)}
ORDER BY unique_src_count DESC
LIMIT {limit}
""".strip()
    return _query(sql)


# ── top talkers ───────────────────────────────────────────────────────────────

def top_talkers(window_ms: int = 1000, limit: int = 20) -> List[Dict[str, Any]]:
    """Heaviest edges (by bytes) in the last window_ms."""
    sql = f"""
SELECT
    edge_id,
    sum(bytes)   AS total_bytes,
    sum(packets) AS total_packets,
    count()      AS tick_count
FROM flow_metrics
WHERE ts > {_recent_ts_expr(window_ms)}
GROUP BY edge_id
ORDER BY total_bytes DESC
LIMIT {limit}
""".strip()
    return _query(sql)


# ── alert feed ────────────────────────────────────────────────────────────────

def recent_alerts(limit: int = 20) -> List[Dict[str, Any]]:
    """Most recent topology_alerts (degree drift) and fan_in_events combined."""
    drift = _query(f"""
SELECT
    ts,
    node_id        AS node,
    alert_type     AS kind,
    degree_delta   AS delta,
    score
FROM topology_alerts
ORDER BY ts DESC
LIMIT {limit}
""".strip())

    fanin = _query(f"""
SELECT
    ts,
    dst_node       AS node,
    verdict        AS kind,
    unique_src_count AS delta,
    ip_entropy     AS score
FROM fan_in_events
ORDER BY ts DESC
LIMIT {limit}
""".strip())

    combined = drift + fanin
    combined.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return combined[:limit]


# ── edge rate ─────────────────────────────────────────────────────────────────

def edge_rate(window_ms: int = 1000) -> float:
    """Approximate edges/sec over the last window_ms."""
    rows = _query(f"""
SELECT count() AS n
FROM flow_metrics
WHERE ts > {_recent_ts_expr(window_ms)}
""".strip())
    if rows:
        return rows[0].get("n", 0) / (window_ms / 1000.0)
    return 0.0


# ── CLI convenience ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"

    if cmd == "fanin":
        rows = fanin_by_dst()
        if rows:
            print(f"{'dst_node':<20} {'srcs':>6} {'ip_H':>6} {'timing_H':>9} {'verdict'}")
            print("-" * 60)
            for r in rows:
                print(f"{r['dst_node']:<20} {r['unique_src_count']:>6} "
                      f"{r['ip_entropy']:>6.2f} {r['timing_entropy']:>9.2f} {r['verdict']}")
        else:
            print("No fan-in events yet.")

    elif cmd == "talkers":
        rows = top_talkers()
        if rows:
            print(f"{'edge_id':<36} {'bytes':>12} {'packets':>10}")
            print("-" * 62)
            for r in rows:
                print(f"{r['edge_id']:<36} {r['total_bytes']:>12,} {r['total_packets']:>10,}")
        else:
            print("No flow_metrics yet.")

    elif cmd == "alerts":
        rows = recent_alerts()
        if rows:
            for r in rows:
                print(r)
        else:
            print("No alerts yet.")

    else:  # summary
        rate = edge_rate(window_ms=5000)
        print(f"Edge rate (last 5s): {rate:.0f} edges/sec")
        alerts = recent_alerts(limit=5)
        print(f"Recent alerts ({len(alerts)}):")
        for a in alerts:
            print(f"  {a}")
