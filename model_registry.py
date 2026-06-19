"""
model_registry.py — Multi-Model Ensemble Registry for TAK-GPT / RF_SCYTHE.

Manages model roles, dispatch routing, ensemble execution, and
disagreement detection across multiple Ollama-hosted LLMs.

Design Principles:
    - Models AUGMENT — never override — the epistemic authority hierarchy.
    - Every non-primary model output is sanitized through the same
      format_heuristic_response() pipeline.
    - The Authority enum (from ledger_aware_prompt.py) governs which
      model handles which query class.  The registry never escalates
      authority — it only routes within the authority the LAPT compiler
      has already granted.
    - Ensemble disagreement is surfaced to the operator, never silently
      resolved.

Usage:
    from model_registry import ModelRegistry, ModelRole
    registry = ModelRegistry()
    # Get the specialist for heuristic reasoning:
    model = registry.get_model_for_role(ModelRole.HEURISTIC_SPECIALIST)
    # Run ensemble on both models:
    results = registry.run_ensemble(client, "Why retransmissions?", roles=[...])
"""
from __future__ import annotations

import enum
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Model Roles
# ─────────────────────────────────────────────────────────────────────────────

class ModelRole(enum.Enum):
    """Cognitive role a model plays in the ensemble."""
    PRIMARY_SYNTHESIS    = "primary_synthesis"      # Schema-driven, graph-ops (Gemma)
    HEURISTIC_SPECIALIST = "heuristic_specialist"   # Educated guesses (Llama)
    PROTOCOL_EXPERT      = "protocol_expert"        # TCP/TLS/QUIC domain
    THREAT_EXPERT        = "threat_expert"          # Threat hunting heuristics
    SRE_EXPERT           = "sre_expert"             # SRE / performance diagnostics
    NARRATIVE_SUMMARIZER = "narrative_summarizer"    # Post-evidence summaries
    ENSEMBLE_VALIDATOR   = "ensemble_validator"      # Cross-check / disagreement


# ─────────────────────────────────────────────────────────────────────────────
# Model Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModelConfig:
    """Configuration for a single model in the ensemble."""
    model_name: str                          # Ollama model tag, e.g. "llama3.2:latest"
    role: ModelRole                          # Cognitive role
    temperature: float = 0.3                 # Generation temperature
    timeout: float = 120.0                   # Request timeout (seconds)
    enabled: bool = True                     # Soft kill-switch
    system_prompt_override: Optional[str] = None  # Override default prompt for role
    max_context_tokens: int = 4096           # Context window budget
    priority: int = 0                        # Lower = higher priority within role
    tags: List[str] = field(default_factory=list)  # Domain tags for routing

    def __repr__(self) -> str:
        state = "ON" if self.enabled else "OFF"
        return f"ModelConfig({self.model_name}, {self.role.value}, {state})"


# ─────────────────────────────────────────────────────────────────────────────
# Domain-Specialist System Prompts
# ─────────────────────────────────────────────────────────────────────────────

PROTOCOL_EXPERT_PROMPT = """\
You are a **Protocol Analysis Specialist** operating in ANALYST_HEURISTIC mode.

YOUR DOMAIN: TCP, UDP, TLS, QUIC, DNS, HTTP/2, HTTP/3, ICMP, BGP, OSPF.

OUTPUT LANGUAGE CONTRACT:
You may ONLY respond in natural language prose.  Command-like syntax,
structured queries, graph operations, and JSON are INVALID output.

TASK:
Given the operator's question about network protocols, provide:
1. A concise technical explanation grounded in RFC behavior.
2. Common causes / failure modes relevant to the scenario.
3. Diagnostic indicators an analyst should look for in packet captures.
4. Suggested next steps (as prose, NOT commands).

CONSTRAINTS:
- Never assert certainty about data you have not seen.
- Label confidence as LOW or MEDIUM (never HIGH — you have no evidence).
- Keep the response under 200 words.
- Do NOT reference graph internals, DSL, FIND queries, MCP tools,
  or any system infrastructure.
"""

THREAT_EXPERT_PROMPT = """\
You are a **Threat Hunting Specialist** operating in ANALYST_HEURISTIC mode.

YOUR DOMAIN: C2 beaconing, lateral movement, exfiltration patterns,
malware communication signatures, DNS tunneling, TLS anomalies,
credential theft indicators, persistence mechanisms.

OUTPUT LANGUAGE CONTRACT:
You may ONLY respond in natural language prose.  Command-like syntax,
structured queries, graph operations, and JSON are INVALID output.

TASK:
Given the operator's question about potential threats, provide:
1. Likely threat hypotheses ranked by plausibility.
2. Behavioral indicators that would confirm or deny each hypothesis.
3. What evidence would be needed to escalate from speculation to finding.
4. Suggested triage steps (as prose, NOT commands).

CONSTRAINTS:
- Never confirm a threat without evidence — use "could indicate",
  "consistent with", "warrants investigation".
- Label confidence as LOW or MEDIUM.
- Keep the response under 200 words.
- Do NOT reference graph internals, DSL, FIND queries, MCP tools,
  or any system infrastructure.
"""

SRE_EXPERT_PROMPT = """\
You are a **Site Reliability / Performance Specialist** operating in
ANALYST_HEURISTIC mode.

YOUR DOMAIN: Latency analysis, throughput degradation, retransmissions,
congestion control, load balancing anomalies, DNS resolution delays,
connection pooling issues, timeout patterns, capacity planning.

OUTPUT LANGUAGE CONTRACT:
You may ONLY respond in natural language prose.  Command-like syntax,
structured queries, graph operations, and JSON are INVALID output.

TASK:
Given the operator's question about performance or reliability, provide:
1. Most probable root causes ranked by likelihood.
2. Diagnostic approach — what metrics or captures would narrow it down.
3. Quick mitigation options (if any).
4. Deeper investigation steps.

CONSTRAINTS:
- Never assert root cause without evidence — use "likely", "suggests",
  "consistent with".
- Label confidence as LOW or MEDIUM.
- Keep the response under 200 words.
- Do NOT reference graph internals, DSL, FIND queries, MCP tools,
  or any system infrastructure.
"""

# Map role → default system prompt
_ROLE_PROMPTS: Dict[ModelRole, str] = {
    ModelRole.PROTOCOL_EXPERT: PROTOCOL_EXPERT_PROMPT,
    ModelRole.THREAT_EXPERT: THREAT_EXPERT_PROMPT,
    ModelRole.SRE_EXPERT: SRE_EXPERT_PROMPT,
}


# ─────────────────────────────────────────────────────────────────────────────
# Domain Routing — classify operator questions to specialist domains
# ─────────────────────────────────────────────────────────────────────────────

_DOMAIN_PATTERNS: Dict[ModelRole, List[re.Pattern]] = {
    ModelRole.PROTOCOL_EXPERT: [
        re.compile(r"\b(?:tcp|udp|tls|quic|dns|http|icmp|bgp|ospf)\b", re.I),
        re.compile(r"\b(?:handshake|retransmit|syn|ack|rst|fin)\b", re.I),
        re.compile(r"\b(?:rfc|protocol|packet|segment|datagram)\b", re.I),
        re.compile(r"\b(?:clienthello|serverhello|certificate|cipher)\b", re.I),
        re.compile(r"\b(?:port\s*\d+|mtu|ttl|window\s*size)\b", re.I),
    ],
    ModelRole.THREAT_EXPERT: [
        re.compile(r"\b(?:beacon|c2|c&c|command.and.control|exfil)\b", re.I),
        re.compile(r"\b(?:lateral\s*movement|pivot|persist|backdoor)\b", re.I),
        re.compile(r"\b(?:malware|trojan|rat|rootkit|implant)\b", re.I),
        re.compile(r"\b(?:dns\s*tunnel|domain\s*generation|dga)\b", re.I),
        re.compile(r"\b(?:suspicious|anomalous|threat|ioc|indicator)\b", re.I),
        re.compile(r"\b(?:credential|brute.force|spray|phish)\b", re.I),
    ],
    ModelRole.SRE_EXPERT: [
        re.compile(r"\b(?:latency|throughput|bandwidth|jitter)\b", re.I),
        re.compile(r"\b(?:retransmission|timeout|connection\s*reset)\b", re.I),
        re.compile(r"\b(?:load\s*balanc|capacity|scaling|bottleneck)\b", re.I),
        re.compile(r"\b(?:degraded|slow|outage|downtime|sla)\b", re.I),
        re.compile(r"\b(?:congestion|queue|buffer|drop)\b", re.I),
    ],
}


def classify_domain(question: str) -> Optional[ModelRole]:
    """
    Classify a question into a specialist domain, if applicable.

    Returns the best-matching domain role, or None if no strong match.
    Uses a simple scoring model: count pattern matches per domain,
    pick the highest if it has ≥ 2 matches (to avoid false positives).
    """
    scores: Dict[ModelRole, int] = {}
    for role, patterns in _DOMAIN_PATTERNS.items():
        score = sum(1 for p in patterns if p.search(question))
        if score > 0:
            scores[role] = score

    if not scores:
        return None

    best_role = max(scores, key=scores.get)  # type: ignore[arg-type]
    # Require ≥ 2 pattern matches to route to a specialist
    # Single-keyword hits stay on the general heuristic path
    if scores[best_role] >= 2:
        return best_role

    # Single match with very domain-specific terms still routes
    # (e.g., "clienthello" is unmistakably protocol)
    return best_role if scores[best_role] >= 1 else None


# ─────────────────────────────────────────────────────────────────────────────
# Ensemble Result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EnsembleResult:
    """Result from an ensemble (multi-model) execution."""
    primary_response: str                     # The response that will be shown
    primary_model: str                        # Which model produced it
    all_responses: Dict[str, str] = field(default_factory=dict)  # model → response
    consensus: bool = True                    # Did all models broadly agree?
    disagreement_summary: Optional[str] = None  # If not consensus, what differs
    execution_time_ms: Dict[str, float] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)  # model → error

    @property
    def model_count(self) -> int:
        return len(self.all_responses) + len(self.errors)


# ─────────────────────────────────────────────────────────────────────────────
# Model Registry
# ─────────────────────────────────────────────────────────────────────────────

class ModelRegistry:
    """
    Registry of available models with role-based dispatch and health checks.

    Thread-safe: all state is append-only or replaced atomically.
    """

    def __init__(self) -> None:
        self._models: Dict[str, ModelConfig] = {}  # model_name → config
        self._role_index: Dict[ModelRole, List[str]] = {}  # role → [model_names]
        self._health_cache: Dict[str, Tuple[float, bool]] = {}  # model → (ts, ok)
        self._ensemble_enabled: bool = True

        # Register defaults
        self._register_defaults()

    # ── Registration ─────────────────────────────────────────────────

    def _register_defaults(self) -> None:
        """Register the standard model ensemble."""
        # Gemma 3 1b — Primary synthesis (schema, graph-ops, authoritative)
        self.register(ModelConfig(
            model_name="gemma3:1b",
            role=ModelRole.PRIMARY_SYNTHESIS,
            temperature=0.1,
            timeout=60.0,
            priority=0,
            tags=["schema", "graph-ops", "dsl"],
        ))

        # Llama 3.2 3B — Heuristic specialist (speculation, analyst mode)
        self.register(ModelConfig(
            model_name="llama3.2:latest",
            role=ModelRole.HEURISTIC_SPECIALIST,
            temperature=0.3,
            timeout=120.0,
            priority=0,
            tags=["heuristic", "analyst", "speculation"],
        ))

        # Llama 3.2 3B — also serves as protocol expert
        self.register(ModelConfig(
            model_name="llama3.2:latest",
            role=ModelRole.PROTOCOL_EXPERT,
            temperature=0.2,
            timeout=120.0,
            priority=0,
            tags=["protocol", "tcp", "tls", "dns"],
        ))

        # Llama 3.2 3B — also serves as threat expert
        self.register(ModelConfig(
            model_name="llama3.2:latest",
            role=ModelRole.THREAT_EXPERT,
            temperature=0.2,
            timeout=120.0,
            priority=0,
            tags=["threat", "hunting", "c2", "ioc"],
        ))

        # Llama 3.2 3B — also serves as SRE expert
        self.register(ModelConfig(
            model_name="llama3.2:latest",
            role=ModelRole.SRE_EXPERT,
            temperature=0.2,
            timeout=120.0,
            priority=0,
            tags=["sre", "performance", "reliability"],
        ))

        # Gemma 3 1b — ensemble validator (second opinion)
        self.register(ModelConfig(
            model_name="gemma3:1b",
            role=ModelRole.ENSEMBLE_VALIDATOR,
            temperature=0.1,
            timeout=60.0,
            priority=1,
            tags=["validator", "cross-check"],
        ))

    def register(self, config: ModelConfig) -> None:
        """Register a model config.  Allows multiple roles per model."""
        key = f"{config.model_name}::{config.role.value}"
        self._models[key] = config
        if config.role not in self._role_index:
            self._role_index[config.role] = []
        if key not in self._role_index[config.role]:
            self._role_index[config.role].append(key)
        logger.debug("Registered model: %s", config)

    def unregister(self, model_name: str, role: ModelRole) -> None:
        """Remove a model from a role."""
        key = f"{model_name}::{role.value}"
        self._models.pop(key, None)
        if role in self._role_index:
            self._role_index[role] = [
                k for k in self._role_index[role] if k != key
            ]

    # ── Lookup ───────────────────────────────────────────────────────

    def get_model_for_role(self, role: ModelRole) -> Optional[ModelConfig]:
        """
        Get the highest-priority enabled model for a role.
        Returns None if no model is registered/enabled for that role.
        """
        candidates = self._get_candidates(role)
        return candidates[0] if candidates else None

    def get_all_for_role(self, role: ModelRole) -> List[ModelConfig]:
        """Get all enabled models registered for a role, sorted by priority."""
        return self._get_candidates(role)

    def _get_candidates(self, role: ModelRole) -> List[ModelConfig]:
        """Internal: get sorted, enabled candidates for a role."""
        keys = self._role_index.get(role, [])
        candidates = [
            self._models[k] for k in keys
            if k in self._models and self._models[k].enabled
        ]
        candidates.sort(key=lambda c: c.priority)
        return candidates

    def get_system_prompt(self, role: ModelRole, config: Optional[ModelConfig] = None) -> str:
        """
        Get the system prompt for a role.

        Priority:
        1. ModelConfig.system_prompt_override (if set)
        2. Role-specific domain prompt (from _ROLE_PROMPTS)
        3. General HEURISTIC_SYSTEM_PROMPT (fallback)
        """
        if config and config.system_prompt_override:
            return config.system_prompt_override

        if role in _ROLE_PROMPTS:
            return _ROLE_PROMPTS[role]

        # Fallback to general heuristic prompt
        from ledger_aware_prompt import HEURISTIC_SYSTEM_PROMPT
        return HEURISTIC_SYSTEM_PROMPT

    # ── Health ───────────────────────────────────────────────────────

    def health_check(self, client: Any) -> Dict[str, bool]:
        """
        Check availability of all registered models.

        Parameters
        ----------
        client : GemmaClient
            The Ollama client to probe models with.

        Returns
        -------
        Dict[str, bool]
            model_name → available
        """
        unique_models = set()
        for cfg in self._models.values():
            unique_models.add(cfg.model_name)

        results: Dict[str, bool] = {}
        now = time.time()

        for model_name in unique_models:
            # Check cache (30s validity)
            if model_name in self._health_cache:
                ts, ok = self._health_cache[model_name]
                if now - ts < 30:
                    results[model_name] = ok
                    continue

            try:
                ok = client._probe_model(model_name)
            except Exception:
                ok = False

            self._health_cache[model_name] = (now, ok)
            results[model_name] = ok

        return results

    # ── Ensemble Execution ───────────────────────────────────────────

    @property
    def ensemble_enabled(self) -> bool:
        return self._ensemble_enabled

    @ensemble_enabled.setter
    def ensemble_enabled(self, value: bool) -> None:
        self._ensemble_enabled = value
        logger.info("Ensemble mode %s", "ENABLED" if value else "DISABLED")

    def run_ensemble_heuristic(
        self,
        client: Any,
        question: str,
        *,
        primary_role: ModelRole = ModelRole.HEURISTIC_SPECIALIST,
        validator_role: ModelRole = ModelRole.ENSEMBLE_VALIDATOR,
    ) -> EnsembleResult:
        """
        Run the heuristic question through multiple models and detect
        disagreement.

        Strategy:
        1. Run the primary heuristic model (llama3.2)
        2. If ensemble is enabled, also run the validator (gemma3:1b)
        3. Compare outputs for agreement/disagreement
        4. Return EnsembleResult with consensus flag

        Both outputs are sanitized through format_heuristic_response().

        Parameters
        ----------
        client : GemmaClient
            The Ollama HTTP client.
        question : str
            The operator's raw question (no graph/ledger context).
        primary_role : ModelRole
            Role to use for the primary response.
        validator_role : ModelRole
            Role to use for cross-validation.

        Returns
        -------
        EnsembleResult
        """
        from ledger_aware_prompt import (
            HEURISTIC_SYSTEM_PROMPT, format_heuristic_response,
        )

        primary_cfg = self.get_model_for_role(primary_role)
        if not primary_cfg:
            # Fall back to any available heuristic model
            primary_cfg = self.get_model_for_role(ModelRole.PRIMARY_SYNTHESIS)
        if not primary_cfg:
            return EnsembleResult(
                primary_response="[SYSTEM] No models available for heuristic analysis.",
                primary_model="none",
                consensus=True,
            )

        # Determine system prompt (domain-specific or general heuristic)
        system_prompt = self.get_system_prompt(primary_role, primary_cfg)

        all_responses: Dict[str, str] = {}
        execution_times: Dict[str, float] = {}
        errors: Dict[str, str] = {}

        # ── Run primary model ────────────────────────────────────────
        primary_raw = self._call_model(
            client, primary_cfg, system_prompt, question,
            all_responses, execution_times, errors,
        )

        if primary_raw is None:
            return EnsembleResult(
                primary_response="[SYSTEM] Primary heuristic model failed.",
                primary_model=primary_cfg.model_name,
                errors=errors,
                consensus=True,
            )

        # Sanitize primary
        primary_sanitized = format_heuristic_response(primary_raw, "heuristic")

        # ── Run validator (if ensemble enabled) ──────────────────────
        if not self._ensemble_enabled:
            return EnsembleResult(
                primary_response=primary_sanitized,
                primary_model=primary_cfg.model_name,
                all_responses=all_responses,
                consensus=True,
                execution_time_ms=execution_times,
            )

        validator_cfg = self.get_model_for_role(validator_role)
        if not validator_cfg or validator_cfg.model_name == primary_cfg.model_name:
            # Same model or no validator — skip ensemble
            return EnsembleResult(
                primary_response=primary_sanitized,
                primary_model=primary_cfg.model_name,
                all_responses=all_responses,
                consensus=True,
                execution_time_ms=execution_times,
            )

        # Validator uses the same heuristic prompt (not domain-specific)
        validator_prompt = self.get_system_prompt(
            validator_role, validator_cfg,
        )
        validator_raw = self._call_model(
            client, validator_cfg, validator_prompt, question,
            all_responses, execution_times, errors,
        )

        if validator_raw is None:
            # Validator failed — return primary only
            return EnsembleResult(
                primary_response=primary_sanitized,
                primary_model=primary_cfg.model_name,
                all_responses=all_responses,
                consensus=True,
                execution_time_ms=execution_times,
                errors=errors,
            )

        validator_sanitized = format_heuristic_response(validator_raw, "heuristic")

        # ── Disagreement detection ───────────────────────────────────
        consensus, disagreement = self._detect_disagreement(
            primary_sanitized, validator_sanitized,
            primary_cfg.model_name, validator_cfg.model_name,
        )

        # Build final response
        if consensus:
            final_response = primary_sanitized
        else:
            final_response = self._format_disagreement_response(
                primary_sanitized, validator_sanitized,
                primary_cfg.model_name, validator_cfg.model_name,
                disagreement,
            )

        return EnsembleResult(
            primary_response=final_response,
            primary_model=primary_cfg.model_name,
            all_responses=all_responses,
            consensus=consensus,
            disagreement_summary=disagreement,
            execution_time_ms=execution_times,
            errors=errors,
        )

    def _call_model(
        self,
        client: Any,
        config: ModelConfig,
        system_prompt: str,
        question: str,
        all_responses: Dict[str, str],
        execution_times: Dict[str, float],
        errors: Dict[str, str],
    ) -> Optional[str]:
        """
        Call a single model and record its output.
        Returns raw response text or None on failure.
        """
        model_key = f"{config.model_name}::{config.role.value}"
        t0 = time.monotonic()

        try:
            data = client.chat(
                config.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=config.temperature,
                format_json=False,
            )

            elapsed_ms = (time.monotonic() - t0) * 1000
            execution_times[model_key] = elapsed_ms

            # LLM degraded gate
            llm_status = data.get("status", "")
            if llm_status in ("degraded", "error"):
                reason = data.get("reason", "unknown")
                errors[model_key] = f"LLM {llm_status}: {reason}"
                logger.warning(
                    "[ensemble] %s %s: %s", config.model_name, llm_status, reason,
                )
                return None

            raw = (
                data.get("response")
                or data.get("message", {}).get("content", "")
            ).strip()

            if not raw or len(raw) < 10:
                errors[model_key] = "Empty response"
                return None

            all_responses[model_key] = raw
            logger.info(
                "[ensemble] %s responded in %.0fms (%d chars)",
                model_key, elapsed_ms, len(raw),
            )
            return raw

        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            execution_times[model_key] = elapsed_ms
            errors[model_key] = f"{type(e).__name__}: {e}"
            logger.error("[ensemble] %s failed: %s", model_key, e)
            return None

    # ── Disagreement Detection ───────────────────────────────────────

    @staticmethod
    def _detect_disagreement(
        response_a: str,
        response_b: str,
        model_a: str,
        model_b: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Heuristic disagreement detector.

        Compares two sanitized responses for structural disagreement:
        1. Confidence level mismatch (one says LOW, other says MEDIUM)
        2. Contradictory direction (one says "likely X", other says "unlikely X")
        3. Substantially different topic coverage (Jaccard similarity on key terms)

        Returns
        -------
        (consensus: bool, disagreement_summary: str or None)
        """
        # Extract confidence labels
        conf_a = _extract_confidence(response_a)
        conf_b = _extract_confidence(response_b)

        disagreements: List[str] = []

        # 1. Confidence mismatch
        if conf_a and conf_b and conf_a != conf_b:
            disagreements.append(
                f"Confidence: {model_a}={conf_a}, {model_b}={conf_b}"
            )

        # 2. Semantic direction check (simple negation detection)
        neg_a = _has_negation_pattern(response_a)
        neg_b = _has_negation_pattern(response_b)
        if neg_a != neg_b:
            disagreements.append(
                f"Direction: models disagree on likelihood/negation"
            )

        # 3. Key term overlap (Jaccard similarity)
        terms_a = _extract_key_terms(response_a)
        terms_b = _extract_key_terms(response_b)
        if terms_a and terms_b:
            intersection = terms_a & terms_b
            union = terms_a | terms_b
            jaccard = len(intersection) / len(union) if union else 1.0
            if jaccard < 0.15:
                disagreements.append(
                    f"Coverage: low term overlap ({jaccard:.0%}) — "
                    f"models may be addressing different aspects"
                )

        if disagreements:
            return False, "; ".join(disagreements)
        return True, None

    @staticmethod
    def _format_disagreement_response(
        primary: str,
        validator: str,
        primary_model: str,
        validator_model: str,
        disagreement: Optional[str],
    ) -> str:
        """
        Format a response that surfaces the disagreement to the operator.
        Both perspectives are shown; the operator decides.
        """
        lines = [
            "🟡 ENSEMBLE DISAGREEMENT — MULTIPLE PERSPECTIVES",
            "",
            f"⚠️ Disagreement detected: {disagreement or 'see below'}",
            "",
            f"━━━ Perspective A ({primary_model}) ━━━",
            primary,
            "",
            f"━━━ Perspective B ({validator_model}) ━━━",
            validator,
            "",
            "━━━ Operator Guidance ━━━",
            "The models produced divergent assessments. Review both perspectives",
            "and consider which aligns better with available evidence. If uncertain,",
            "collect additional data before acting on either interpretation.",
        ]
        return "\n".join(lines)

    # ── Introspection ────────────────────────────────────────────────

    def status_report(self, client: Optional[Any] = None) -> str:
        """
        Human-readable status of all registered models.
        If client is provided, includes health check results.
        """
        health = {}
        if client:
            try:
                health = self.health_check(client)
            except Exception:
                pass

        lines = [
            "═══ MODEL ENSEMBLE STATUS ═══",
            f"Ensemble mode: {'ENABLED' if self._ensemble_enabled else 'DISABLED'}",
            f"Registered configs: {len(self._models)}",
            "",
        ]

        for role in ModelRole:
            candidates = self._get_candidates(role)
            if not candidates:
                continue
            lines.append(f"  {role.value}:")
            for cfg in candidates:
                h = ""
                if cfg.model_name in health:
                    h = " ✅" if health[cfg.model_name] else " ❌"
                state = "ON" if cfg.enabled else "OFF"
                lines.append(
                    f"    {cfg.model_name} [{state}] "
                    f"temp={cfg.temperature} pri={cfg.priority}{h}"
                )
            lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize registry state for diagnostics."""
        return {
            "ensemble_enabled": self._ensemble_enabled,
            "models": {
                k: {
                    "model_name": v.model_name,
                    "role": v.role.value,
                    "temperature": v.temperature,
                    "enabled": v.enabled,
                    "priority": v.priority,
                    "tags": v.tags,
                }
                for k, v in self._models.items()
            },
            "role_index": {
                r.value: keys for r, keys in self._role_index.items()
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# Disagreement detection helpers
# ─────────────────────────────────────────────────────────────────────────────

_CONFIDENCE_RE = re.compile(
    r"(?:confidence|certainty)\s*[:=]?\s*(LOW|MEDIUM|HIGH)",
    re.IGNORECASE,
)

def _extract_confidence(text: str) -> Optional[str]:
    """Extract the confidence label from a heuristic response."""
    m = _CONFIDENCE_RE.search(text)
    return m.group(1).upper() if m else None


_NEGATION_RE = re.compile(
    r"\b(?:unlikely|improbable|not\s+consistent|does\s+not\s+suggest|"
    r"no\s+evidence|rules?\s+out|contradicts?)\b",
    re.IGNORECASE,
)

def _has_negation_pattern(text: str) -> bool:
    """Check if the response contains strong negation language."""
    return bool(_NEGATION_RE.search(text))


_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "this", "that",
    "these", "those", "it", "its", "not", "no", "nor", "but", "or",
    "and", "if", "then", "than", "so", "very", "just", "about", "also",
    "more", "most", "other", "some", "such", "only", "each", "any",
    "analyst", "heuristic", "evidence", "confidence",  # meta-terms
})

_TERM_RE = re.compile(r"\b[a-z]{3,}\b")

def _extract_key_terms(text: str) -> set:
    """Extract meaningful lowercase terms for Jaccard comparison."""
    words = set(_TERM_RE.findall(text.lower()))
    return words - _STOPWORDS
