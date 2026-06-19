"""questdb_writer.py

Async-safe QuestDB ILP writer for NerfEngine Stage 6.

Writes three streams:
  - flow_metrics   : EdgeTick counters (edge_id, packets, bytes) via ILP TCP port 9009
  - topology_alerts: DriftAlert records via HTTP /exec INSERT (low-volume)
  - rf_events      : RFUAV / structured RF detections via ILP TCP port 9009

ILP (InfluxDB Line Protocol) is the native QuestDB ingest path — zero parsing
overhead, direct WAL append, sustains millions of rows/sec on localhost.

Usage:
    writer = QuestDBWriter()
    writer.start()
    writer.write_edge_tick(edge_id, edge_hi, edge_lo, packets, bytes_)
    writer.write_alert(alert)
    writer.stop()
"""

import logging
import threading
import time
import queue
import urllib.request
import urllib.parse
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

QUESTDB_HOST   = "127.0.0.1"
QUESTDB_HTTP   = 9000
QUESTDB_ILP    = 9009   # TCP line protocol

# Flush the ILP buffer every N rows or every FLUSH_INTERVAL_S seconds
FLUSH_ROWS     = 500
FLUSH_INTERVAL = 0.1    # 100 ms


def _maybe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_ts_ns(value) -> int:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return time.time_ns()
    if ts > 10_000_000_000:
        return int(ts)
    return int(ts * 1_000_000_000)


def _escape_tag(value: str) -> str:
    return str(value).replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


@dataclass
class EdgeTickRow:
    edge_id: str
    edge_hi: int
    edge_lo: int
    packets: int
    bytes_:  int
    ts_ns:   int     # nanosecond timestamp


@dataclass
class AlertRow:
    node_id:      str
    alert_type:   str
    degree_delta: int
    in_degree:    int
    out_degree:   int
    score:        float
    ts_ns:        int


@dataclass
class RFDetectionRow:
    sensor_id: str
    rf_class: str
    rf_subtype: str
    provenance: str
    confidence: float
    center_freq_hz: Optional[float]
    bandwidth_hz: Optional[float]
    spectral_entropy: Optional[float]
    burst_period_ms: Optional[float]
    persistence_s: Optional[float]
    repeat_count: int
    ts_ns: int


class QuestDBWriter:
    """Thread-safe writer that batches EdgeTick rows over ILP and sends alerts over HTTP."""

    def __init__(self,
                 host: str = QUESTDB_HOST,
                 ilp_port: int = QUESTDB_ILP,
                 http_port: int = QUESTDB_HTTP):
        self._host      = host
        self._ilp_port  = ilp_port
        self._http_base = f"http://{host}:{http_port}"

        self._ilp_queue:   queue.Queue = queue.Queue(maxsize=100_000)
        self._alert_queue: queue.Queue = queue.Queue(maxsize=10_000)

        self._running   = False
        self._ilp_thread: Optional[threading.Thread] = None
        self._alert_thread: Optional[threading.Thread] = None

        # stats
        self.rows_written  = 0
        self.rows_dropped  = 0
        self.alerts_sent   = 0

    # ── public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._ilp_thread = threading.Thread(
            target=self._ilp_loop, name="qdb-ilp", daemon=True)
        self._alert_thread = threading.Thread(
            target=self._alert_loop, name="qdb-alert", daemon=True)
        self._ilp_thread.start()
        self._alert_thread.start()
        logger.info("QuestDBWriter started [ilp=%s:%d http=%s]",
                    self._host, self._ilp_port, self._http_base)

    def stop(self, timeout: float = 5.0) -> None:
        self._running = False
        if self._ilp_thread:
            self._ilp_thread.join(timeout=timeout)
        if self._alert_thread:
            self._alert_thread.join(timeout=timeout)
        logger.info("QuestDBWriter stopped [rows=%d alerts=%d dropped=%d]",
                    self.rows_written, self.alerts_sent, self.rows_dropped)

    def write_edge_tick(self,
                        edge_id: str,
                        edge_hi: int,
                        edge_lo: int,
                        packets: int,
                        bytes_: int,
                        ts_ns: Optional[int] = None) -> bool:
        row = EdgeTickRow(
            edge_id=edge_id,
            edge_hi=edge_hi,
            edge_lo=edge_lo,
            packets=packets,
            bytes_=bytes_,
            ts_ns=ts_ns or time.time_ns(),
        )
        try:
            self._ilp_queue.put_nowait(self._edge_tick_to_ilp(row))
            return True
        except queue.Full:
            self.rows_dropped += 1
            return False

    def write_rfuav_detection(self, event: dict, ts_ns: Optional[int] = None) -> bool:
        rf = dict(event.get("rf") or {})
        signal = dict(event.get("signal") or {})
        temporal = dict(event.get("temporal") or {})
        row = RFDetectionRow(
            sensor_id=str(event.get("sensor_id") or "unknown"),
            rf_class=str(rf.get("class") or "unknown"),
            rf_subtype=str(rf.get("subtype") or "unknown"),
            provenance=str(event.get("provenance") or "unknown"),
            confidence=float(rf.get("confidence") or 0.0),
            center_freq_hz=_maybe_float(signal.get("center_freq")),
            bandwidth_hz=_maybe_float(signal.get("bandwidth")),
            spectral_entropy=_maybe_float(signal.get("spectral_entropy")),
            burst_period_ms=_maybe_float(signal.get("burst_period_ms")),
            persistence_s=_maybe_float(temporal.get("persistence_s")),
            repeat_count=int(_maybe_float(temporal.get("repeat_count")) or 0),
            ts_ns=ts_ns or _event_ts_ns(event.get("timestamp")),
        )
        try:
            self._ilp_queue.put_nowait(self._rf_detection_to_ilp(row))
            return True
        except queue.Full:
            self.rows_dropped += 1
            return False

    def write_alert(self, alert: AlertRow) -> bool:
        try:
            self._alert_queue.put_nowait(alert)
            return True
        except queue.Full:
            return False

    # ── ILP writer loop ────────────────────────────────────────────────────────

    def _ilp_loop(self) -> None:
        import socket
        sock = None

        def connect() -> socket.socket:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((self._host, self._ilp_port))
            return s

        buf: list[str] = []
        last_flush = time.monotonic()

        while self._running or not self._ilp_queue.empty():
            try:
                line = self._ilp_queue.get(timeout=0.05)
                buf.append(line)
            except queue.Empty:
                pass

            now = time.monotonic()
            if buf and (len(buf) >= FLUSH_ROWS or (now - last_flush) >= FLUSH_INTERVAL):
                payload = "\n".join(buf) + "\n"
                try:
                    if sock is None:
                        sock = connect()
                    sock.sendall(payload.encode())
                    self.rows_written += len(buf)
                    buf.clear()
                    last_flush = now
                except Exception as exc:
                    logger.warning("ILP send failed, reconnecting: %s", exc)
                    try:
                        sock.close()
                    except Exception:
                        pass
                    sock = None
                    # keep buf — retry next iteration

        if sock:
            try:
                sock.close()
            except Exception:
                pass

    @staticmethod
    def _edge_tick_to_ilp(row: EdgeTickRow) -> str:
        # ILP format: measurement,tags fields timestamp
        # edge_id is a tag (indexed symbol), counters are fields
        safe_id = _escape_tag(row.edge_id)
        return (
            f"flow_metrics,edge_id={safe_id} "
            f"edge_hi={row.edge_hi}i,"
            f"edge_lo={row.edge_lo}i,"
            f"packets={row.packets}i,"
            f"bytes={row.bytes_}i "
            f"{row.ts_ns}"
        )

    @staticmethod
    def _rf_detection_to_ilp(row: RFDetectionRow) -> str:
        tags = ",".join(
            [
                f"sensor_id={_escape_tag(row.sensor_id)}",
                f"rf_class={_escape_tag(row.rf_class)}",
                f"rf_subtype={_escape_tag(row.rf_subtype)}",
                f"provenance={_escape_tag(row.provenance)}",
            ]
        )
        fields = [
            f"confidence={row.confidence:.6f}",
            f"repeat_count={row.repeat_count}i",
        ]
        if row.center_freq_hz is not None:
            fields.append(f"center_freq={row.center_freq_hz:.6f}")
        if row.bandwidth_hz is not None:
            fields.append(f"bandwidth={row.bandwidth_hz:.6f}")
        if row.spectral_entropy is not None:
            fields.append(f"spectral_entropy={row.spectral_entropy:.6f}")
        if row.burst_period_ms is not None:
            fields.append(f"burst_period_ms={row.burst_period_ms:.6f}")
        if row.persistence_s is not None:
            fields.append(f"persistence_s={row.persistence_s:.6f}")
        return f"rf_events,{tags} {','.join(fields)} {row.ts_ns}"

    # ── Alert HTTP loop ────────────────────────────────────────────────────────

    def _alert_loop(self) -> None:
        while self._running or not self._alert_queue.empty():
            try:
                alert: AlertRow = self._alert_queue.get(timeout=0.1)
                self._http_insert_alert(alert)
            except queue.Empty:
                continue
            except Exception as exc:
                logger.warning("alert insert failed: %s", exc)

    def _http_insert_alert(self, a: AlertRow) -> None:
        import datetime
        ts_str = datetime.datetime.fromtimestamp(
            a.ts_ns / 1e9, tz=datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        sql = (
            f"INSERT INTO topology_alerts VALUES ("
            f"'{ts_str}',"
            f"'{a.node_id}',"
            f"'{a.alert_type}',"
            f"{a.degree_delta},"
            f"{a.in_degree},"
            f"{a.out_degree},"
            f"{a.score:.6f}"
            f")"
        )
        url = f"{self._http_base}/exec?query=" + urllib.parse.quote(sql)
        req = urllib.request.urlopen(url, timeout=5)
        req.read()
        self.alerts_sent += 1


# module-level singleton — imported by topology_drift and stage6 entry point
_writer: Optional[QuestDBWriter] = None


def get_writer() -> QuestDBWriter:
    global _writer
    if _writer is None:
        _writer = QuestDBWriter()
        _writer.start()
    return _writer
