"""
takml_runtime_metrics.py — shared runtime counters for tak-ml / GraphOps chat.

Tracks explicit event counts and recent-window rates for:
  - commit rate
  - exhaustion rate
  - repair rate
  - skipped-item rate
  - rejected-kind recurrence
  - shadow-promotion overrides / blocks
"""
from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict, deque
from typing import Any, Deque, DefaultDict, Dict, List, Optional, Tuple


class TakMlRuntimeMetrics:
    """Thread-safe in-memory metrics tracker for tak-ml runtime events."""

    def __init__(self, max_recent_events: int = 10000):
        self._lock = threading.Lock()
        self._max_recent_events = max_recent_events
        self._totals: Counter[str] = Counter()
        self._recent: DefaultDict[str, Deque[Tuple[float, int]]] = defaultdict(deque)
        self._rejected_kind_totals: Counter[str] = Counter()
        self._rejected_kind_recent: Deque[Tuple[float, str]] = deque()
        self._shadow_block_reasons: Counter[str] = Counter()
        self._shadow_block_recent: Deque[Tuple[float, str]] = deque()
        self._validation_skip_reasons: Counter[str] = Counter()

    def _record(self, name: str, count: int = 1) -> None:
        now = time.time()
        with self._lock:
            self._totals[name] += count
            dq = self._recent[name]
            dq.append((now, count))
            while len(dq) > self._max_recent_events:
                dq.popleft()

    def record_inference_attempt(self) -> None:
        self._record("inference_attempts")

    def record_inference_success(self, *, edges_produced: int = 0) -> None:
        self._record("successful_inferences")
        if edges_produced > 0:
            self._record("validated_edges", edges_produced)

    def record_exhaustion(self) -> None:
        self._record("exhausted_inferences")

    def record_error(self) -> None:
        self._record("error_inferences")

    def record_commit(self, op_count: int, mode: str = "") -> None:
        self._record("commit_batches")
        self._record("ops_committed", op_count)
        if mode:
            self._record(f"commit_mode:{mode}")

    def record_structured_output_hard_failure(self, reason: str = "") -> None:
        self._record("structured_output_hard_failures")
        if reason:
            self._record(f"structured_output_reason:{reason}")

    def record_validation_items(self, count: int) -> None:
        if count > 0:
            self._record("validation_items_seen", count)

    def record_validation_skip(self, reason: str = "") -> None:
        self._record("validation_skipped_items")
        if reason:
            with self._lock:
                self._validation_skip_reasons[reason] += 1

    def record_semantic_repair(self) -> None:
        self._record("semantic_repairs")

    def record_guardrail_recovered(self, count: int) -> None:
        if count > 0:
            self._record("guardrail_recovered_edges", count)

    def record_rejected_kind(self, raw_kind: str) -> None:
        normalized = (raw_kind or "").strip() or "<empty>"
        now = time.time()
        with self._lock:
            self._totals["rejected_kind_events"] += 1
            dq = self._recent["rejected_kind_events"]
            dq.append((now, 1))
            while len(dq) > self._max_recent_events:
                dq.popleft()
            self._rejected_kind_totals[normalized] += 1
            self._rejected_kind_recent.append((now, normalized))
            while len(self._rejected_kind_recent) > self._max_recent_events:
                self._rejected_kind_recent.popleft()

    def record_shadow_promotion_attempt(self) -> None:
        self._record("shadow_promotion_attempts")

    def record_shadow_promotion_success(self) -> None:
        self._record("shadow_promotions")

    def record_shadow_promotion_block(self, reason: str) -> None:
        normalized = (reason or "unknown").strip() or "unknown"
        now = time.time()
        with self._lock:
            self._totals["shadow_promotion_blocks"] += 1
            dq = self._recent["shadow_promotion_blocks"]
            dq.append((now, 1))
            while len(dq) > self._max_recent_events:
                dq.popleft()
            self._shadow_block_reasons[normalized] += 1
            self._shadow_block_recent.append((now, normalized))
            while len(self._shadow_block_recent) > self._max_recent_events:
                self._shadow_block_recent.popleft()

    def snapshot(self, window_seconds: int = 900) -> Dict[str, Any]:
        cutoff = time.time() - max(1, int(window_seconds))
        with self._lock:
            recent_counts = {
                name: sum(count for ts, count in dq if ts >= cutoff)
                for name, dq in self._recent.items()
            }
            rejected_recent_counter = Counter(
                kind for ts, kind in self._rejected_kind_recent if ts >= cutoff
            )
            shadow_recent_counter = Counter(
                reason for ts, reason in self._shadow_block_recent if ts >= cutoff
            )
            totals = dict(self._totals)
            validation_skip_reasons = dict(self._validation_skip_reasons)
            rejected_kind_totals = self._rejected_kind_totals.copy()
            shadow_block_reasons = self._shadow_block_reasons.copy()

        return {
            "window_seconds": window_seconds,
            "totals": totals,
            "recent": recent_counts,
            "rates": {
                "recent": self._build_rate_block(recent_counts),
                "cumulative": self._build_rate_block(totals),
            },
            "rejected_kind_recurrence": {
                "total_rejections": totals.get("rejected_kind_events", 0),
                "unique_kinds": len(rejected_kind_totals),
                "recurring_kinds": sum(1 for count in rejected_kind_totals.values() if count > 1),
                "top_kinds_total": [
                    {"kind": kind, "count": count}
                    for kind, count in rejected_kind_totals.most_common(10)
                ],
                "top_kinds_recent": [
                    {"kind": kind, "count": count}
                    for kind, count in rejected_recent_counter.most_common(10)
                ],
            },
            "shadow_promotion": {
                "block_reasons_total": dict(shadow_block_reasons),
                "block_reasons_recent": dict(shadow_recent_counter),
            },
            "validation": {
                "skip_reasons_total": validation_skip_reasons,
            },
        }

    @staticmethod
    def _build_rate_block(counts: Dict[str, int]) -> Dict[str, float]:
        completed = (
            counts.get("successful_inferences", 0)
            + counts.get("exhausted_inferences", 0)
            + counts.get("error_inferences", 0)
        )
        repairs = counts.get("semantic_repairs", 0) + counts.get("guardrail_recovered_edges", 0)
        skipped = counts.get("validation_skipped_items", 0)
        validation_items = counts.get("validation_items_seen", 0)
        shadow_attempts = counts.get("shadow_promotion_attempts", 0)
        return {
            "commit_rate": round(counts.get("successful_inferences", 0) / completed, 4) if completed else 0.0,
            "exhaustion_rate": round(counts.get("exhausted_inferences", 0) / completed, 4) if completed else 0.0,
            "repair_rate": round(repairs / completed, 4) if completed else 0.0,
            "skipped_item_rate": round(skipped / validation_items, 4) if validation_items else 0.0,
            "shadow_promotion_override_rate": round(
                counts.get("shadow_promotion_blocks", 0) / shadow_attempts, 4
            ) if shadow_attempts else 0.0,
        }

    def reset_for_tests(self) -> None:
        with self._lock:
            self._totals.clear()
            self._recent.clear()
            self._rejected_kind_totals.clear()
            self._rejected_kind_recent.clear()
            self._shadow_block_reasons.clear()
            self._shadow_block_recent.clear()
            self._validation_skip_reasons.clear()


_shared_metrics: Optional[TakMlRuntimeMetrics] = None
_shared_lock = threading.Lock()


def get_takml_runtime_metrics_tracker() -> TakMlRuntimeMetrics:
    global _shared_metrics
    if _shared_metrics is None:
        with _shared_lock:
            if _shared_metrics is None:
                _shared_metrics = TakMlRuntimeMetrics()
    return _shared_metrics


def get_takml_runtime_metrics(window_seconds: int = 900) -> Dict[str, Any]:
    return get_takml_runtime_metrics_tracker().snapshot(window_seconds=window_seconds)


def reset_takml_runtime_metrics() -> None:
    get_takml_runtime_metrics_tracker().reset_for_tests()
