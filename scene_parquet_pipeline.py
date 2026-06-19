"""
scene_parquet_pipeline.py — Parquet cold-storage pipeline for tactical events.

Wraps DuckDB store with:
  - automatic block partitioning by time window
  - multi-block merge + compaction
  - time-range export for replay/analysis
  - statistics and skip-index for fast seeks
"""

import time
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.compute as pc

from scene_duckdb_store import ScytheDuckStore, TacticalEvent, get_store, EVENTS_SCHEMA

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BLOCK_SECONDS = 60          # 1-minute blocks
DEFAULT_PARQUET_DIR   = "/home/spectrcyde/NerfEngine/metrics_logs/parquet_blocks"
COMPRESSION           = "zstd"
COMPRESSION_LEVEL     = 9


# ---------------------------------------------------------------------------
# Block metadata
# ---------------------------------------------------------------------------

class BlockMeta:
    """Lightweight skip-index entry for a Parquet block."""
    __slots__ = ("block_id", "file_path", "min_ts", "max_ts", "row_count", "session_id", "size_bytes")

    def __init__(self, block_id, file_path, min_ts, max_ts, row_count, session_id, size_bytes=0):
        self.block_id   = block_id
        self.file_path  = file_path
        self.min_ts     = min_ts
        self.max_ts     = max_ts
        self.row_count  = row_count
        self.session_id = session_id
        self.size_bytes = size_bytes

    def to_dict(self) -> dict:
        return {
            "block_id":   self.block_id,
            "file_path":  self.file_path,
            "min_ts":     self.min_ts,
            "max_ts":     self.max_ts,
            "row_count":  self.row_count,
            "session_id": self.session_id,
            "size_bytes": self.size_bytes,
        }


# ---------------------------------------------------------------------------
# ParquetPipeline
# ---------------------------------------------------------------------------

class ParquetPipeline:
    """
    Manages Parquet-based cold storage for the tactical event log.

    Typical flow:
        1.  Events arrive → DuckDB hot store via ScytheDuckStore.append_batch()
        2.  Pipeline.flush_block() → writes a Parquet file for a time window
        3.  Old hot-store events can be pruned after flushing
        4.  Pipeline.merge_blocks() → compact multiple small blocks
        5.  Pipeline.read_timerange() → load events from Parquet back into memory
    """

    def __init__(self,
                 store: Optional[ScytheDuckStore] = None,
                 parquet_dir: str = DEFAULT_PARQUET_DIR,
                 block_seconds: int = DEFAULT_BLOCK_SECONDS):
        self._store = store or get_store()
        self._parquet_dir = Path(parquet_dir)
        self._parquet_dir.mkdir(parents=True, exist_ok=True)
        self._block_sec = block_seconds

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def flush_block(self, t0_ms: int, t1_ms: int,
                    session_id: Optional[str] = None) -> Optional[BlockMeta]:
        """
        Export [t0_ms, t1_ms] from the hot DuckDB store to a Parquet file.
        Returns BlockMeta or None if no events in range.
        """
        events = self._store.events_in_range(t0_ms, t1_ms, session_id)
        if not events:
            return None
        return self._write_events(events, session_id or "all")

    def flush_auto_blocks(self, session_id: Optional[str] = None) -> List[BlockMeta]:
        """
        Partition all events in the hot store into fixed-duration Parquet blocks.
        Non-destructive — hot store events are not deleted.
        """
        stats = self._store.stats()
        if not stats["total_events"]:
            return []

        t0 = stats["earliest_ts"]
        t1 = stats["latest_ts"]
        step_ms = self._block_sec * 1000

        blocks: List[BlockMeta] = []
        cur = t0
        while cur <= t1:
            meta = self.flush_block(cur, cur + step_ms - 1, session_id)
            if meta:
                blocks.append(meta)
            cur += step_ms

        return blocks

    def _write_events(self, events: List[TacticalEvent], sid_tag: str) -> BlockMeta:
        rows   = [e.to_row() for e in events]
        table  = pa.Table.from_pylist(rows, schema=EVENTS_SCHEMA)
        min_ts = min(e.timestamp for e in events)
        max_ts = max(e.timestamp for e in events)

        fname  = f"block_{sid_tag[:16]}_{min_ts}_{max_ts}.parquet"
        fpath  = self._parquet_dir / fname

        pq.write_table(
            table, fpath,
            compression=COMPRESSION,
            compression_level=COMPRESSION_LEVEL,
            use_dictionary=True,
            write_statistics=True,
            row_group_size=50_000,
        )

        size = fpath.stat().st_size
        meta = BlockMeta(
            block_id   = f"{min_ts}_{max_ts}",
            file_path  = str(fpath),
            min_ts     = min_ts,
            max_ts     = max_ts,
            row_count  = len(events),
            session_id = sid_tag,
            size_bytes = size,
        )
        return meta

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_timerange(self, t0_ms: int, t1_ms: int,
                       session_id: Optional[str] = None) -> List[TacticalEvent]:
        """
        Load events from Parquet files covering [t0_ms, t1_ms].
        Uses skip-index to avoid reading blocks outside the window.
        """
        matching = self._blocks_for_range(t0_ms, t1_ms, session_id)
        if not matching:
            return []

        tables = []
        for meta in matching:
            tbl = pq.read_table(meta.file_path)
            tables.append(tbl)

        combined = pa.concat_tables(tables)
        # Filter to exact range
        mask = pc.and_(
            pc.greater_equal(combined["timestamp"], t0_ms),
            pc.less_equal(combined["timestamp"], t1_ms)
        )
        if session_id:
            mask = pc.and_(mask, pc.equal(combined["session_id"], session_id))
        filtered = combined.filter(mask)
        filtered = filtered.sort_by([("timestamp", "ascending"), ("seq", "ascending")])

        return [TacticalEvent.from_row(row) for row in filtered.to_pylist()]

    def _blocks_for_range(self, t0_ms: int, t1_ms: int,
                          session_id: Optional[str]) -> List[BlockMeta]:
        """Return block metadata for files that overlap [t0_ms, t1_ms]."""
        blocks = self._scan_parquet_dir()
        result = []
        for b in blocks:
            if b.max_ts < t0_ms or b.min_ts > t1_ms:
                continue
            if session_id and b.session_id not in (session_id, "all"):
                continue
            result.append(b)
        return result

    def _scan_parquet_dir(self) -> List[BlockMeta]:
        """Read Parquet file metadata from disk (lightweight — uses file-level stats)."""
        metas = []
        for fpath in sorted(self._parquet_dir.glob("block_*.parquet")):
            try:
                pf = pq.ParquetFile(fpath)
                md = pf.metadata
                # Pull min/max from first row group statistics
                rg  = md.row_group(0)
                col_names = [rg.column(i).path_in_schema for i in range(rg.num_columns)]
                ts_idx = col_names.index("timestamp") if "timestamp" in col_names else None
                if ts_idx is not None:
                    stats = rg.column(ts_idx).statistics
                    min_ts = stats.min if stats else 0
                    max_ts = stats.max if stats else 0
                else:
                    min_ts = max_ts = 0
                parts = fpath.stem.split("_")  # block_<sid>_<t0>_<t1>
                sid = parts[1] if len(parts) > 1 else "unknown"
                metas.append(BlockMeta(
                    block_id   = fpath.stem,
                    file_path  = str(fpath),
                    min_ts     = min_ts,
                    max_ts     = max_ts,
                    row_count  = md.num_rows,
                    session_id = sid,
                    size_bytes = fpath.stat().st_size,
                ))
            except Exception:
                pass
        return metas

    # ------------------------------------------------------------------
    # Merge / compact
    # ------------------------------------------------------------------

    def merge_blocks(self, block_paths: List[str],
                     output_name: Optional[str] = None) -> Optional[BlockMeta]:
        """
        Merge multiple Parquet block files into one.
        Useful for compacting many small blocks into fewer large ones.
        """
        if not block_paths:
            return None

        tables = [pq.read_table(p) for p in block_paths]
        combined = pa.concat_tables(tables)
        combined = combined.sort_by([("timestamp", "ascending"), ("seq", "ascending")])

        min_ts = pc.min(combined["timestamp"]).as_py()
        max_ts = pc.max(combined["timestamp"]).as_py()
        fname  = output_name or f"merged_{min_ts}_{max_ts}.parquet"
        fpath  = self._parquet_dir / fname

        pq.write_table(
            combined, fpath,
            compression=COMPRESSION,
            compression_level=COMPRESSION_LEVEL,
            use_dictionary=True,
            write_statistics=True,
        )

        return BlockMeta(
            block_id   = fpath.stem,
            file_path  = str(fpath),
            min_ts     = min_ts,
            max_ts     = max_ts,
            row_count  = combined.num_rows,
            session_id = "merged",
            size_bytes = fpath.stat().st_size,
        )

    # ------------------------------------------------------------------
    # Inventory / diagnostics
    # ------------------------------------------------------------------

    def inventory(self) -> Dict[str, Any]:
        blocks = self._scan_parquet_dir()
        total_bytes = sum(b.size_bytes for b in blocks)
        total_rows  = sum(b.row_count  for b in blocks)
        return {
            "block_count":   len(blocks),
            "total_rows":    total_rows,
            "total_bytes":   total_bytes,
            "total_kb":      round(total_bytes / 1024, 1),
            "blocks":        [b.to_dict() for b in blocks],
        }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import math, shutil

    PARQUET_TEST_DIR = "/tmp/parquet_pipeline_test"
    shutil.rmtree(PARQUET_TEST_DIR, ignore_errors=True)
    Path(PARQUET_TEST_DIR).mkdir()

    print("=== ParquetPipeline self-test ===")
    store = ScytheDuckStore(db_path=":memory:", parquet_dir=PARQUET_TEST_DIR)
    pipe  = ParquetPipeline(store=store, parquet_dir=PARQUET_TEST_DIR, block_seconds=10)

    # Generate 3 minutes of events across 2 sessions
    N_EVENTS = 10_000
    t0 = int(time.time() * 1000)
    events = []
    for i in range(N_EVENTS):
        session = "alpha" if i < N_EVENTS // 2 else "bravo"
        events.append(TacticalEvent(
            timestamp  = t0 + i * 18,   # ~18ms apart → spans ~3 minutes
            event_type = "entity.move" if i % 5 else "rf.detection",
            entity_id  = f"uav_{i % 30}",
            session_id = session,
            lat        = 38.1 + math.sin(i * 0.01) * 0.1,
            lon        = -77.1 + math.cos(i * 0.01) * 0.1,
            alt        = 150.0,
            payload    = {"snr": 25.0, "freq_mhz": 433.0},
        ))

    t_ins = time.perf_counter()
    store.append_batch(events)
    ins_ms = (time.perf_counter() - t_ins) * 1000
    print(f"Inserted {N_EVENTS:,} events  [{ins_ms:.0f}ms]")

    # Auto-partition into 10s blocks
    t_flush = time.perf_counter()
    blocks = pipe.flush_auto_blocks()
    flush_ms = (time.perf_counter() - t_flush) * 1000
    total_kb = sum(b.size_bytes for b in blocks) / 1024
    raw_est  = N_EVENTS * 80 / 1024
    print(f"Flushed {len(blocks)} blocks  [{flush_ms:.0f}ms]")
    print(f"Parquet size: {total_kb:.1f} KB  (raw ~{raw_est:.0f} KB)  ratio={raw_est/total_kb:.1f}×")

    # Read back a time slice
    mid_t0 = t0 + 60_000
    mid_t1 = t0 + 90_000
    t_read = time.perf_counter()
    slice_events = pipe.read_timerange(mid_t0, mid_t1)
    read_ms = (time.perf_counter() - t_read) * 1000
    print(f"Read time-range [{mid_t0}..{mid_t1}]: {len(slice_events)} events  [{read_ms:.1f}ms]")

    assert all(mid_t0 <= e.timestamp <= mid_t1 for e in slice_events), "Timestamp filter failed"
    print("Timestamp filter: OK ✅")

    # Merge all blocks into one
    block_paths = [b.file_path for b in blocks]
    merged = pipe.merge_blocks(block_paths, "full_session_merged.parquet")
    print(f"Merged → {merged.row_count:,} rows, {merged.size_bytes/1024:.1f} KB ✅")

    # Inventory
    inv = pipe.inventory()
    print(f"Inventory: {inv['block_count']} blocks, {inv['total_rows']:,} rows, {inv['total_kb']:.1f} KB")

    store.close()
    shutil.rmtree(PARQUET_TEST_DIR, ignore_errors=True)
    print("ALL TESTS PASSED ✅")
