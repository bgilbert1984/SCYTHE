"""
semantic_edge_repair.py — EmbeddingGemma-powered semantic repair for LLM-generated edge kinds.

Problem:
    Gemma 3b generates edge kinds like "FLOW_HOST_TO_HOST" or "SESSION_BETWEEN_HOSTS"
    that are semantically correct but syntactically illegal in the RF SCYTHE ontology.
    The static EDGE_KIND_ALIASES table can only enumerate known hallucination patterns;
    truly novel variants get dropped (NO_VALID_EDGES exhaustion).

Solution:
    For any kind that passes through normalize_edge_kind() as None (unknown), use
    embeddinggemma cosine similarity against pre-embedded VALID_INFERRED_KINDS to find
    the closest legal kind.  If confidence ≥ REPAIR_THRESHOLD, accept it.
    Below threshold, still drop — but log for ontology evolution analysis.

Architecture:
    LLM output → normalize_edge_kind() (static) → if None → SemanticEdgeRepair.repair()
                                                              → (canonical, score)
                                                              → score ≥ threshold → accept
                                                              → score < threshold  → drop + log

Integration:
    Import and call from rule_prompt.py's validator, at the "Truly unknown kind" branch.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Confidence threshold ─────────────────────────────────────────────────────
# 0.65 is the live observed score for known-good repairs (e.g. "flow_observed" → INFERRED_FLOW_IN_SERVICE).
# Logs showed consistent ~0.65 scores for semantically valid repairs that were being rejected at 0.82.
# Set SEMANTIC_REPAIR_THRESHOLD env var to override (e.g. "0.72" for stricter mode).
REPAIR_THRESHOLD: float = float(os.environ.get("SEMANTIC_REPAIR_THRESHOLD", "0.65"))

# Ollama URL — uses the same endpoint as the rest of the NerfEngine stack
_OLLAMA_URL: str = os.environ.get("OLLAMA_URL", "http://localhost:11434")
_EMBED_MODEL: str = "embeddinggemma"  # 768-dim; falls back to llama3.2:3b if unavailable


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Pure-Python cosine similarity — avoids numpy dependency at module load time."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _embed_text(text: str, model: str = _EMBED_MODEL, timeout: float = 10.0) -> Optional[List[float]]:
    """Synchronous embedding call to local Ollama.  Returns None on failure."""
    try:
        payload = json.dumps({"model": model, "prompt": text}).encode()
        req = urllib.request.Request(
            f"{_OLLAMA_URL}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        return data.get("embedding") or None
    except Exception as exc:
        logger.debug("[SemanticEdgeRepair] embed error for %r: %s", text, exc)
        return None


class SemanticEdgeRepair:
    """Thread-safe, lazy-initialized semantic edge kind repair layer.

    Embeds VALID_INFERRED_KINDS once (on first use) and caches them.
    Subsequent calls do only a cosine similarity scan — no network I/O
    unless the kind embedding itself is missing.

    Usage:
        repair = SemanticEdgeRepair.get_instance()
        canonical, score = repair.repair("FLOW_HOST_TO_HOST")
        if canonical:
            # score ≥ REPAIR_THRESHOLD — use canonical
        else:
            # score < threshold or embedding unavailable — drop
    """

    _instance: Optional["SemanticEdgeRepair"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        # {kind_str: embedding_vector}
        self._kind_embeddings: Dict[str, List[float]] = {}
        self._initialized = False
        self._init_lock = threading.Lock()
        # Repair log: list of {raw, canonical, score, ts} — capped at 500 entries
        self._repair_log: List[dict] = []
        self._repair_log_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "SemanticEdgeRepair":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _ensure_initialized(self) -> bool:
        """Embed all VALID_INFERRED_KINDS on first call.  Returns True if ready."""
        if self._initialized:
            return bool(self._kind_embeddings)
        with self._init_lock:
            if self._initialized:
                return bool(self._kind_embeddings)
            from rule_prompt import VALID_INFERRED_KINDS
            failed = 0
            for kind in VALID_INFERRED_KINDS:
                vec = _embed_text(kind)
                if vec:
                    self._kind_embeddings[kind] = vec
                else:
                    failed += 1
            self._initialized = True
            if self._kind_embeddings:
                logger.info(
                    "[SemanticEdgeRepair] Ready — embedded %d/%d valid kinds (model=%s)",
                    len(self._kind_embeddings), len(VALID_INFERRED_KINDS), _EMBED_MODEL,
                )
            else:
                logger.warning(
                    "[SemanticEdgeRepair] No embeddings available (Ollama down?). "
                    "Semantic repair disabled."
                )
            return bool(self._kind_embeddings)

    def repair(self, raw_kind: str) -> Tuple[Optional[str], float]:
        """Find the closest valid edge kind for a raw LLM-generated kind.

        Returns:
            (canonical_kind, score) — canonical is None if score < REPAIR_THRESHOLD
                                     or if embeddings are unavailable.
        """
        if not raw_kind:
            return None, 0.0

        if not self._ensure_initialized():
            return None, 0.0

        raw_vec = _embed_text(raw_kind)
        if raw_vec is None:
            return None, 0.0

        best_kind: Optional[str] = None
        best_score: float = 0.0
        for kind, vec in self._kind_embeddings.items():
            score = _cosine_similarity(raw_vec, vec)
            if score > best_score:
                best_score = score
                best_kind = kind

        accepted = best_score >= REPAIR_THRESHOLD
        self._log_repair(raw_kind, best_kind if accepted else None, best_score, accepted)

        if accepted:
            logger.info(
                "[SemanticEdgeRepair] %r → %r (score=%.3f ≥ %.2f)",
                raw_kind, best_kind, best_score, REPAIR_THRESHOLD,
            )
            return best_kind, best_score
        else:
            logger.debug(
                "[SemanticEdgeRepair] %r → no repair (best=%r score=%.3f < %.2f)",
                raw_kind, best_kind, best_score, REPAIR_THRESHOLD,
            )
            return None, best_score

    def _log_repair(self, raw: str, canonical: Optional[str], score: float, accepted: bool) -> None:
        """Record repair attempt for ontology evolution analysis."""
        entry = {
            "raw": raw,
            "canonical": canonical,
            "score": round(score, 4),
            "accepted": accepted,
            "ts": time.time(),
        }
        with self._repair_log_lock:
            self._repair_log.append(entry)
            if len(self._repair_log) > 500:
                self._repair_log = self._repair_log[-500:]

    def get_repair_stats(self) -> dict:
        """Return aggregated repair stats for the MCP diagnostics endpoint."""
        with self._repair_log_lock:
            log = list(self._repair_log)
        if not log:
            return {"total": 0, "accepted": 0, "rejected": 0, "top_repairs": [], "top_unknowns": []}

        accepted = [e for e in log if e["accepted"]]
        rejected = [e for e in log if not e["accepted"]]

        # Count accepted repairs by (raw → canonical)
        from collections import Counter
        repair_counts: Counter = Counter(
            f"{e['raw']} → {e['canonical']}" for e in accepted
        )
        unknown_counts: Counter = Counter(e["raw"] for e in rejected)

        return {
            "total": len(log),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "accept_rate": round(len(accepted) / len(log), 3) if log else 0.0,
            "top_repairs": [{"mapping": k, "count": v} for k, v in repair_counts.most_common(10)],
            "top_unknowns": [{"raw": k, "count": v} for k, v in unknown_counts.most_common(10)],
            "avg_score_accepted": round(
                sum(e["score"] for e in accepted) / len(accepted), 3
            ) if accepted else 0.0,
            "avg_score_rejected": round(
                sum(e["score"] for e in rejected) / len(rejected), 3
            ) if rejected else 0.0,
            "threshold": REPAIR_THRESHOLD,
        }

    def promote_candidates(self, min_count: int = 5, min_score: float = 0.70) -> List[dict]:
        """Identify rejected kinds that appear frequently with decent scores.

        These are candidates for promotion to VALID_INFERRED_KINDS or EDGE_KIND_ALIASES.
        """
        with self._repair_log_lock:
            rejected = [e for e in self._repair_log if not e["accepted"]]

        from collections import defaultdict
        by_raw: Dict[str, list] = defaultdict(list)
        for e in rejected:
            by_raw[e["raw"]].append(e)

        candidates = []
        for raw, entries in by_raw.items():
            if len(entries) < min_count:
                continue
            avg_score = sum(e["score"] for e in entries) / len(entries)
            if avg_score < min_score:
                continue
            # Best canonical mapping for this raw kind
            best = max(entries, key=lambda e: e["score"])
            candidates.append({
                "raw": raw,
                "best_canonical": best["canonical"],
                "occurrences": len(entries),
                "avg_score": round(avg_score, 3),
                "best_score": round(best["score"], 3),
                "recommendation": (
                    f"Add to EDGE_KIND_ALIASES: '{raw}': '{best['canonical']}'"
                    if best["score"] >= 0.85
                    else f"Review manually — score {avg_score:.2f} borderline"
                ),
            })
        return sorted(candidates, key=lambda c: (-c["occurrences"], -c["avg_score"]))
