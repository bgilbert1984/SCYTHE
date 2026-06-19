"""
inference_guardrail.py — 3-stage inference guardrail for EmbeddedGemma graph output.

Implements the pipeline described in gemma_runner.md:

  Stage 1 — Canonicalization (static EDGE_KIND_MAP, already in EDGE_KIND_ALIASES)
  Stage 2 — Structural Completion (auto-heal missing src/dst from context)
  Stage 3 — Validation Feedback Loop (Gemma-assisted repair, up to N retries)

  Bonus — Training data capture: every rejection → JSONL training pair

Architecture:
  raw Gemma results
       ↓
  Stage 1: normalize_edge()        (applied pre-validator via EDGE_KIND_ALIASES)
       ↓
  validate_gemma_output()          (existing validator — kind check, zone check, src/dst)
       ↓  ← 0 valid edges?
  Stage 2: auto_heal_edge()        (fill missing src/dst from context_node_id)
       ↓  ← still bad?
  Stage 3: gemma_repair_edge()     (Gemma re-writes the malformed edge)
       ↓
  validate_gemma_output() retry   (accept if valid, else drop + log training pair)
       ↓
  recovered_results → returned to caller

Integration:
  Called from TakMlGemmaRunner.__infer_for_node_body() after validate_gemma_output()
  when total_edges == 0 and the rule did fire (i.e. "host exhausted — 0 valid edges").
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from takml_runtime_metrics import get_takml_runtime_metrics_tracker

logger = logging.getLogger(__name__)
_runtime_metrics = get_takml_runtime_metrics_tracker()

# ── Training pair storage ────────────────────────────────────────────────────
_TRAINING_DIR = os.environ.get(
    "GEMMA_TRAINING_DIR",
    str(Path(__file__).parent / "training_data"),
)
_TRAINING_FILE = os.path.join(_TRAINING_DIR, "edge_corrections.jsonl")
_training_lock = threading.Lock()

# ── Ollama config (mirrors semantic_edge_repair.py) ─────────────────────────
_OLLAMA_URL: str = os.environ.get("OLLAMA_URL", "http://localhost:11434")
_REPAIR_MODEL: str = os.environ.get("GEMMA_REPAIR_MODEL", "gemma3:1b")

# Max repair attempts per edge before we drop and capture as training pair
MAX_REPAIR_ATTEMPTS: int = 2


# ─────────────────────────────────────────────────────────────────────────────
# Training Pair Collector
# ─────────────────────────────────────────────────────────────────────────────

class TrainingPairCollector:
    """Append-only JSONL writer for edge correction training pairs.

    Each line: {"input": <bad_edge_dict>, "output": <corrected_edge_dict_or_null>,
                "context": <context_node_id>, "ts": <unix_ts>}

    output=null means the edge was dropped even after all repair attempts —
    these are useful negative examples for fine-tuning.
    """

    _instance: Optional["TrainingPairCollector"] = None
    _instance_lock = threading.Lock()

    def __init__(self, filepath: str = _TRAINING_FILE) -> None:
        self._filepath = filepath
        self._write_lock = threading.Lock()
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

    @classmethod
    def get_instance(cls) -> "TrainingPairCollector":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def capture(
        self,
        bad_edge: Dict[str, Any],
        corrected_edge: Optional[Dict[str, Any]],
        context_node_id: str = "",
        error_reason: str = "",
    ) -> None:
        """Write one training pair to the JSONL file."""
        record = {
            "input": bad_edge,
            "output": corrected_edge,
            "context_node_id": context_node_id,
            "error_reason": error_reason,
            "ts": time.time(),
        }
        try:
            with self._write_lock:
                with open(self._filepath, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
        except Exception as exc:
            logger.debug("[guardrail] Training pair write failed: %s", exc)

    def stats(self) -> Dict[str, Any]:
        """Return basic statistics about collected training pairs."""
        try:
            with open(self._filepath, encoding="utf-8") as fh:
                lines = fh.readlines()
            total = len(lines)
            positive = sum(1 for l in lines if '"output": null' not in l and '"output":null' not in l)
            return {"total": total, "positive": positive, "negative": total - positive,
                    "filepath": self._filepath}
        except FileNotFoundError:
            return {"total": 0, "positive": 0, "negative": 0, "filepath": self._filepath}
        except Exception as exc:
            return {"error": str(exc), "filepath": self._filepath}


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Explicit EDGE_KIND_MAP (gemma_runner.md canonical example)
# ─────────────────────────────────────────────────────────────────────────────
# This mirrors the static doc prescription. At runtime, EDGE_KIND_ALIASES in
# rule_prompt.py is the authoritative source (expanded superset). This map is
# used by normalize_edge() for pre-validator canonicalization with provenance.

EDGE_KIND_MAP: Dict[str, Optional[str]] = {
    "FLOW_HOST_TO_HOST":      "INFERRED_FLOW_IN_SERVICE",
    "FLOW_FROM_HOST":         "INFERRED_FLOW_IN_SERVICE",
    "FLOW_OBSERVED_HOST":     None,   # observed zone — drop
    "SESSION_BETWEEN_HOSTS":  "INFERRED_FLOW_IN_SERVICE",
    "HOST_IN_ASN":            "INFERRED_HOST_IN_ORG",
    "PORT_HUB":               "INFERRED_HOST_OFFERS_SERVICE",
}


def normalize_edge(edge: Dict[str, Any]) -> Dict[str, Any]:
    """Stage 1: Canonicalize edge kind using EDGE_KIND_MAP.

    Mutates a COPY of the edge — never modifies the original.
    Stamps ``normalized_from`` on any kind that was remapped.
    Returns None if the kind maps to a schema-policy drop.
    """
    if not isinstance(edge, dict):
        return edge
    edge = dict(edge)
    kind = (edge.get("kind") or "").strip().upper()
    if kind in EDGE_KIND_MAP:
        mapped = EDGE_KIND_MAP[kind]
        if mapped is None:
            return None   # explicit drop
        edge["kind"] = mapped
        edge["normalized_from"] = kind
    return edge


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Structural Completion (auto-heal)
# ─────────────────────────────────────────────────────────────────────────────

def auto_heal_edge(
    edge: Dict[str, Any],
    context_node_id: str = "",
    known_node_ids: Optional[set] = None,
) -> Dict[str, Any]:
    """Stage 2: Fill missing structural fields using inference context.

    Rules:
    - Missing ``src``: use context_node_id (the node being inferred about)
    - Missing ``dst``: cannot heal without more context — leave absent so
      Stage 3 (Gemma repair) can attempt a better fill
    - Missing ``rule_id``: synthesize from kind
    - Circular reference: clear ``dst`` if it equals ``src``

    Returns a healed copy; never mutates the input.
    """
    if not isinstance(edge, dict):
        return edge
    edge = dict(edge)
    healed_fields = []

    # Missing src → use context node (heuristic: model infers FROM focus node)
    if not edge.get("src") and context_node_id:
        edge["src"] = context_node_id
        edge["_healed_src"] = True
        healed_fields.append("src")

    # Circular reference guard
    if edge.get("src") and edge.get("dst") and edge["src"] == edge["dst"]:
        logger.debug("[guardrail] Auto-heal cleared circular dst for edge kind=%s", edge.get("kind"))
        del edge["dst"]
        healed_fields.append("dst_cleared_circular")

    # Missing rule_id — synthesize from kind
    if not edge.get("rule_id"):
        kind = (edge.get("kind") or "unknown").lower()
        edge["rule_id"] = f"auto_{kind}"
        healed_fields.append("rule_id")

    if healed_fields:
        logger.info("[guardrail] Auto-healed edge fields: %s (kind=%s)", healed_fields, edge.get("kind"))

    return edge


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — Gemma-assisted repair
# ─────────────────────────────────────────────────────────────────────────────

def _call_gemma_repair(
    edge: Dict[str, Any],
    error_reason: str,
    context_node_id: str,
    valid_kinds: List[str],
    timeout: float = 15.0,
) -> Optional[Dict[str, Any]]:
    """Call Ollama/Gemma3 with a targeted edge repair prompt.

    Returns the repaired edge dict, or None if the call fails or the
    response is not valid JSON with the expected shape.
    """
    kinds_str = "\n".join(f"  - {k}" for k in sorted(valid_kinds))
    prompt = (
        f"You are a graph schema enforcement co-processor.\n\n"
        f"The following edge was produced by an LLM but failed schema validation:\n"
        f"```json\n{json.dumps(edge, indent=2)}\n```\n\n"
        f"Validation failure: {error_reason}\n\n"
        f"Context: this edge was inferred about node `{context_node_id}`.\n\n"
        f"Allowed edge kinds (choose exactly one):\n{kinds_str}\n\n"
        f"Rules:\n"
        f"1. Return ONLY a single JSON object — no explanation text.\n"
        f"2. `kind` MUST be one of the allowed kinds above.\n"
        f"3. `src` and `dst` must be non-empty string node IDs.\n"
        f"4. If `src` is unknown, use `{context_node_id}`.\n"
        f"5. Preserve `confidence` if present (float 0-1).\n\n"
        f"Repaired edge JSON:"
    )
    try:
        payload = json.dumps({
            "model": _REPAIR_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0},
        }).encode()
        req = urllib.request.Request(
            f"{_OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        raw_response = data.get("response", "")
        # Strip markdown fences if present
        clean = raw_response.strip()
        if clean.startswith("```"):
            clean = "\n".join(
                l for l in clean.splitlines()
                if not l.strip().startswith("```")
            ).strip()
        repaired = json.loads(clean)
        if not isinstance(repaired, dict):
            return None
        return repaired
    except Exception as exc:
        logger.debug("[guardrail] Gemma repair call failed: %s", exc)
        return None


def gemma_repair_edge(
    edge: Dict[str, Any],
    error_reason: str = "unknown",
    context_node_id: str = "",
    valid_kinds: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Stage 3: Ask Gemma to rewrite a bad edge into schema-compliant form.

    Returns the repaired edge dict (NOT yet validated — caller must re-validate),
    or None if Gemma is unavailable or the repair is unusable.
    """
    if valid_kinds is None:
        try:
            from rule_prompt import VALID_INFERRED_KINDS
            valid_kinds = list(VALID_INFERRED_KINDS)
        except Exception:
            valid_kinds = []

    if not valid_kinds:
        return None

    repaired = _call_gemma_repair(edge, error_reason, context_node_id, valid_kinds)
    if repaired is None:
        return None

    # Stamp repair provenance
    repaired["_repaired_by"] = "inference_guardrail"
    repaired["_original_edge"] = edge.get("kind", "unknown")
    return repaired


# ─────────────────────────────────────────────────────────────────────────────
# Main Guardrail: 3-stage pipeline
# ─────────────────────────────────────────────────────────────────────────────

class GemmaEdgeGuardrail:
    """Apply the 3-stage guardrail to a single inferred edge.

    Usage:
        guardrail = GemmaEdgeGuardrail()
        healed = guardrail.process_edge(edge, context_node_id, known_node_ids)
        # healed is None if the edge could not be salvaged
    """

    def __init__(
        self,
        max_repair_attempts: int = MAX_REPAIR_ATTEMPTS,
        collect_training_data: bool = True,
    ) -> None:
        self.max_repair_attempts = max_repair_attempts
        self.collect_training_data = collect_training_data
        self._collector = TrainingPairCollector.get_instance() if collect_training_data else None

    def _validate_edge(
        self,
        edge: Dict[str, Any],
        known_node_ids: Optional[set],
    ) -> Tuple[bool, str]:
        """Quick edge-level validation returning (is_valid, error_reason)."""
        kind = (edge.get("kind") or "").strip()
        if not kind:
            return False, "missing kind"

        try:
            from rule_prompt import VALID_INFERRED_KINDS, EDGE_KIND_ALIASES, EDGE_AUTHORITY, EDGE_ZONE_INFERRED
            canonical = edge.get("kind", "")
            if canonical not in VALID_INFERRED_KINDS:
                # Try alias
                alias = EDGE_KIND_ALIASES.get(canonical)
                if alias is None:
                    return False, f"invalid kind '{canonical}'"
                canonical = alias
            zone = EDGE_AUTHORITY.get(canonical)
            if zone and zone != EDGE_ZONE_INFERRED:
                return False, f"wrong zone '{zone}' for kind '{canonical}'"
        except Exception:
            pass  # can't check — let the full validator handle it

        if not edge.get("src"):
            return False, "missing src"
        if not edge.get("dst"):
            return False, "missing dst"

        if known_node_ids:
            if edge.get("src") not in known_node_ids:
                return False, f"unknown src '{edge['src']}'"
            if edge.get("dst") not in known_node_ids:
                return False, f"unknown dst '{edge['dst']}'"

        return True, ""

    def process_edge(
        self,
        edge: Dict[str, Any],
        context_node_id: str = "",
        known_node_ids: Optional[set] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run all 3 stages on a single edge.

        Returns a valid edge dict (may be modified), or None if irrecoverable.
        """
        original = dict(edge)

        # Stage 1: static canonicalization
        edge = normalize_edge(edge)
        if edge is None:
            # Explicit schema-policy drop
            if self._collector:
                self._collector.capture(original, None, context_node_id, "schema_policy_drop")
            return None

        # Quick check — if already valid, return immediately
        valid, reason = self._validate_edge(edge, known_node_ids)
        if valid:
            return edge

        # Stage 2: auto-heal structural fields
        edge = auto_heal_edge(edge, context_node_id, known_node_ids)
        valid, reason = self._validate_edge(edge, known_node_ids)
        if valid:
            logger.info("[guardrail] Stage 2 healed edge kind=%s", edge.get("kind"))
            if self._collector:
                self._collector.capture(original, edge, context_node_id, f"healed:{reason}")
            return edge

        # Stage 3: Gemma repair loop
        for attempt in range(self.max_repair_attempts):
            repaired = gemma_repair_edge(
                edge,
                error_reason=reason,
                context_node_id=context_node_id,
            )
            if repaired is None:
                break  # Gemma unavailable
            valid, new_reason = self._validate_edge(repaired, known_node_ids)
            if valid:
                logger.info(
                    "[guardrail] Stage 3 Gemma-repaired edge: '%s' → '%s' (attempt %d)",
                    original.get("kind"), repaired.get("kind"), attempt + 1,
                )
                if self._collector:
                    self._collector.capture(original, repaired, context_node_id, f"gemma_repair:{reason}")
                return repaired
            reason = new_reason
            edge = repaired  # feed repaired output back for next attempt

        # All stages failed — capture as negative training example
        logger.debug(
            "[guardrail] Edge irrecoverable: kind='%s' reason='%s' — captured as training pair",
            original.get("kind"), reason,
        )
        if self._collector:
            self._collector.capture(original, None, context_node_id, reason)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Repair pass: operate on full Gemma rule results
# ─────────────────────────────────────────────────────────────────────────────

def guardrail_repair_pass(
    raw_results: Any,
    validated_results: List[Dict],
    context_node_id: str,
    known_node_ids: Optional[set] = None,
    max_repair_attempts: int = MAX_REPAIR_ATTEMPTS,
) -> List[Dict]:
    """Apply the guardrail repair pass to recover edges from 0-valid-edge results.

    Called after validate_gemma_output() when total_edges == 0 for a rule
    that fired (i.e. the model produced output but all edges were dropped).

    For each rule result that fired but has no valid inferred_edges:
    - Pull corresponding raw edges from raw_results
    - Run each through GemmaEdgeGuardrail.process_edge()
    - Insert recovered edges back into the result

    Returns the augmented validated_results list.
    """
    # Only attempt repair if we actually had 0 output
    total_edges = sum(
        len(r.get("inferred_edges", []))
        for r in validated_results
        if r.get("should_fire")
    )
    if total_edges > 0:
        return validated_results  # nothing to repair

    # Extract raw edge candidates from raw results
    raw_list = raw_results if isinstance(raw_results, list) else ([raw_results] if isinstance(raw_results, dict) else [])
    raw_edges_by_rule: Dict[str, List[Dict]] = {}
    for raw_r in raw_list:
        if not isinstance(raw_r, dict):
            continue
        rid = raw_r.get("rule_id", "unknown")
        raw_edges_by_rule[rid] = list(raw_r.get("inferred_edges") or [])

    guardrail = GemmaEdgeGuardrail(max_repair_attempts=max_repair_attempts)
    recovered_total = 0

    for result in validated_results:
        if not result.get("should_fire"):
            continue
        existing_edges = result.get("inferred_edges") or []
        if existing_edges:
            continue  # rule already has valid edges
        rid = result.get("rule_id", "unknown")
        raw_edges = raw_edges_by_rule.get(rid, [])
        if not raw_edges:
            continue

        recovered = []
        for raw_edge in raw_edges:
            if not isinstance(raw_edge, dict):
                continue
            healed = guardrail.process_edge(
                dict(raw_edge),
                context_node_id=context_node_id,
                known_node_ids=known_node_ids,
            )
            if healed is not None:
                recovered.append(healed)

        if recovered:
            result["inferred_edges"] = recovered
            result["_guardrail_recovered"] = True
            recovered_total += len(recovered)
            logger.info(
                "[guardrail] Recovered %d edge(s) for rule '%s' on node '%s'",
                len(recovered), rid, context_node_id,
            )

    if recovered_total > 0:
        _runtime_metrics.record_guardrail_recovered(recovered_total)
        logger.info(
            "[guardrail] Repair pass complete: recovered %d edge(s) for node '%s'",
            recovered_total, context_node_id,
        )

    return validated_results


# ─────────────────────────────────────────────────────────────────────────────
# SSE Enrichment Pipeline (Bonus)
# ─────────────────────────────────────────────────────────────────────────────

class SSEInferenceEnricher:
    """Injects EmbeddedGemma canonicalization into the SSE entity stream.

    Usage (in SSE generator):
        enricher = SSEInferenceEnricher.get_instance()
        entity = enricher.enrich(raw_entity)
        yield f"data: {json.dumps(entity)}\\n\\n"

    Adds to each entity event:
        - ``_canonical_kind`` — normalized edge kind (if edge entity)
        - ``_confidence_tier`` — "auto_commit" | "review" | "shadow"
        - ``_guardrail_applied`` — True if any field was corrected
    """

    _instance: Optional["SSEInferenceEnricher"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "SSEInferenceEnricher":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def enrich(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich a raw SSE entity event with guardrail metadata."""
        if not isinstance(entity, dict):
            return entity
        entity = dict(entity)

        kind = entity.get("kind") or entity.get("edge_kind") or ""
        if not kind:
            return entity

        # Apply Stage 1 normalization
        normalized = normalize_edge({"kind": kind})
        if normalized is None:
            entity["_guardrail_dropped"] = True
            return entity

        canonical = normalized.get("kind", kind)
        if canonical != kind:
            entity["_canonical_kind"] = canonical
            entity["_guardrail_applied"] = True

        # Confidence tier annotation
        conf = float(entity.get("confidence", 0.5))
        if conf >= 0.85:
            entity["_confidence_tier"] = "auto_commit"
        elif conf >= 0.70:
            entity["_confidence_tier"] = "review"
        else:
            entity["_confidence_tier"] = "shadow"

        return entity
