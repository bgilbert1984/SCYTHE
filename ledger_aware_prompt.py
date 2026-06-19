"""
ledger_aware_prompt.py — Ledger-Aware Prompt Template DSL (LAPT-DSL)

Binds operator prompts to ledger authority, constraining output shape and
allowable evidence.  Short-circuits inference when exhaustion or policy
blocks apply.  Makes silence a first-class response.

    ┌──────────────────────────────────────────────────────────────────────┐
    │  OPERATOR QUERY                                                     │
    │   ↓                                                                 │
    │  LAPT INTENT CLASSIFIER                                             │
    │   ├─ LEDGER_QUERY? → ledger.query() → structured JSON → DONE       │
    │   ├─ GRAPH_QUERY?  → graph lookup → answer from data → DONE        │
    │   ├─ LEDGER+LLM?   → inject ledger context → pass to Gemma         │
    │   └─ UNCLASSIFIED  → pass through (no LAPT intervention)           │
    └──────────────────────────────────────────────────────────────────────┘

Hierarchy: Ledger > Graph > Model (always).

Usage:
    from ledger_aware_prompt import get_shared_ledger, LAPTCompiler

    compiler = LAPTCompiler(hypergraph_engine, get_shared_ledger())
    result = compiler.compile(operator_message)

    if result.short_circuit:
        return result.response          # no LLM needed
    elif result.ledger_context:
        # inject result.ledger_context into user_msg before LLM call
        ...

9 Prompt Classes:
    1. EXHAUSTION_INSPECTION   — "What is exhausted?"
    2. REACTIVATION_AUDIT      — "What could reactivate?"
    3. VALIDATOR_ANALYSIS       — "Why was X rejected?"
    4. SENSOR_GAP_ANALYSIS      — "Where are sensor gaps?"
    5. SCHEDULER_SANITY         — "Is inference stuck?"
    6. COST_ACCOUNTING          — "What was wasted?"
    7. STRUCTURAL_DEBT          — "What is structurally weak?"
    8. SILENCE_COMPLIANCE       — "Why is the system silent?"
    9. EVIDENCE_REACTIVATION    — "Has new evidence arrived?"
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Authority classification — Ledger > Graph > Model (always)
# ─────────────────────────────────────────────────────────────────────────────

class Authority(str, Enum):
    """Who is authoritative for answering this class of question.

    LEDGER_ONLY        — answer is fully in the IEL, no graph/model needed
    GRAPH_ONLY         — answer is a graph traversal (FIND query OK)
    MODEL_SYNTHESIS    — answer requires LLM narrative (DSL forbidden)
    ANALYST_HEURISTIC  — educated guess: LLM allowed, but output is boxed,
                         labelled non-authoritative, cannot emit edges or
                         assert facts, must include uncertainty + next steps
    ILLEGAL_EXHAUSTED  — question is epistemically illegal under exhaustion
    PASS_THROUGH       — unclassified, no LAPT authority constraint
    """
    LEDGER_ONLY = 'LEDGER_ONLY'
    GRAPH_ONLY = 'GRAPH_ONLY'
    MODEL_SYNTHESIS = 'MODEL_SYNTHESIS'
    ANALYST_HEURISTIC = 'ANALYST_HEURISTIC'
    ILLEGAL_EXHAUSTED = 'ILLEGAL_EXHAUSTED'
    PASS_THROUGH = 'PASS_THROUGH'


# Map each LAPT intent class to its authority
INTENT_AUTHORITY: Dict[str, Authority] = {
    'EXHAUSTION_INSPECTION': Authority.LEDGER_ONLY,
    'REACTIVATION_AUDIT':    Authority.LEDGER_ONLY,
    'VALIDATOR_ANALYSIS':     Authority.LEDGER_ONLY,
    'SENSOR_GAP_ANALYSIS':   Authority.LEDGER_ONLY,
    'SCHEDULER_SANITY':      Authority.LEDGER_ONLY,
    'COST_ACCOUNTING':       Authority.LEDGER_ONLY,
    'STRUCTURAL_DEBT':       Authority.MODEL_SYNTHESIS,  # topology + LLM ok
    'SILENCE_COMPLIANCE':    Authority.LEDGER_ONLY,
    'EVIDENCE_REACTIVATION': Authority.LEDGER_ONLY,
}


# ─────────────────────────────────────────────────────────────────────────────
# UX badges — visual authority tags for responses
# ─────────────────────────────────────────────────────────────────────────────

UX_BADGES = {
    Authority.LEDGER_ONLY:       '🟣 LEDGER ANSWER',
    Authority.GRAPH_ONLY:        '🔵 GRAPH QUERY',
    Authority.MODEL_SYNTHESIS:   '🟡 MODEL SYNTHESIS',
    Authority.ANALYST_HEURISTIC: '🟠 ANALYST HEURISTIC — NOT EVIDENCE',
    Authority.ILLEGAL_EXHAUSTED: '⚫ SILENT BY DESIGN',
    Authority.PASS_THROUGH:      '',
}


# ─────────────────────────────────────────────────────────────────────────────
# Confidence decay constants
# ─────────────────────────────────────────────────────────────────────────────

DECAY_CONSTANT = 0.02      # per minute
STALE_PENALTY = 0.05       # per stale inference
EVIDENCE_THIN_THRESHOLD = 0.4
SPECULATION_DOMINANT_THRESHOLD = 0.6
STALE_DECAY_THRESHOLD = 3
EXHAUSTION_TIME_THRESHOLD_MIN = 30.0
SILENCE_EXHAUSTION_RATIO = 0.7  # exhausted > 70% of total → silence mode


# ─────────────────────────────────────────────────────────────────────────────
# Module-level IEL singleton — shared across runner + chatbot instances
# ─────────────────────────────────────────────────────────────────────────────

_shared_ledger = None


def get_shared_ledger():
    """Return the module-level IEL singleton, creating on first call.

    Both TakMlGemmaRunner and GraphOpsChatBot should use this
    so exhaustion state persists across per-request instances.
    """
    global _shared_ledger
    if _shared_ledger is None:
        from inference_exhaustion_ledger import InferenceExhaustionLedger
        _shared_ledger = InferenceExhaustionLedger()
    return _shared_ledger


def reset_shared_ledger():
    """Reset for testing."""
    global _shared_ledger
    _shared_ledger = None


# ─────────────────────────────────────────────────────────────────────────────
# Prompt Template dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PromptTemplate:
    """Canonical LAPT template structure.

    Fields mirror the spec:
        INTENT            — what this prompt is trying to answer
        SCOPE             — entity/kind/time limits
        LEDGER_GUARD      — precondition check against IEL
        DATA_REQUIREMENTS — what graph/ledger data is needed
        ALLOWED_OPERATIONS— what the output can contain
        OUTPUT_CONTRACT   — required response structure
        FAILURE_MODES     — how to handle empty/blocked states
    """
    intent: str
    scope: str
    ledger_guard: str = ""
    data_requirements: str = ""
    allowed_operations: str = ""
    output_contract: str = ""
    failure_modes: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Compiler result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LAPTResult:
    """Result from LAPT compilation.

    short_circuit : True if the answer was resolved without LLM
    response      : The answer string (if short_circuit)
    ledger_context: Ledger state to inject into LLM prompt (if not short_circuit)
    intent        : Detected intent class (or None)
    template      : Matched template (or None)
    authority     : Authority classification for this query
    forbid_dsl    : True if DSL emission must be suppressed
    ux_badge      : UX badge string for the response
    prompt_rewrites: Suggested alternative prompts (when question is illegal)
    """
    short_circuit: bool = False
    response: str = ""
    ledger_context: str = ""
    intent: Optional[str] = None
    template: Optional[PromptTemplate] = None
    authority: Authority = Authority.PASS_THROUGH
    forbid_dsl: bool = False
    ux_badge: str = ""
    prompt_rewrites: Optional[List[str]] = None


# ─────────────────────────────────────────────────────────────────────────────
# Intent detection patterns
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: (compiled_regex, intent_class)
_INTENT_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # 1. EXHAUSTION_INSPECTION
    (re.compile(
        r'(?:what|which|show|list)\s+(?:is|are)\s+(?:exhausted|depleted|blocked)'
        r'|exhausted\s+(?:entities|nodes|hosts|flows)'
        r'|exhaustion\s+(?:status|state|report|summary|ledger)'
        r'|what\s+has\s+been\s+exhausted'
        r'|show\s+(?:the\s+)?exhaustion',
        re.IGNORECASE),
     'EXHAUSTION_INSPECTION'),

    # 2. REACTIVATION_AUDIT
    (re.compile(
        r'(?:what|which)\s+(?:could|can|would|will)\s+reactivat'
        r'|reactivat(?:e|ion)\s+(?:audit|check|status|candidates)'
        r'|(?:resume|unblock|reactive)\s+(?:condition|inference)'
        r'|what\s+(?:would|will)\s+(?:unblock|resume|restart)',
        re.IGNORECASE),
     'REACTIVATION_AUDIT'),

    # 3. VALIDATOR_ANALYSIS
    (re.compile(
        r'(?:why|how)\s+(?:was|were|is|did)\s+[\w:.\-]+\s+(?:rejected|dropped|invalid)'
        r'|validator?\s+(?:report|rejection|feedback|analysis)'
        r'|(?:rejected|dropped|invalid)\s+(?:edge|inference|output)'
        r'|why\s+did\s+(?:the\s+)?validator\s+(?:reject|drop|block)',
        re.IGNORECASE),
     'VALIDATOR_ANALYSIS'),

    # 4. SENSOR_GAP_ANALYSIS
    (re.compile(
        r'(?:where|what)\s+(?:are|is)\s+(?:the\s+)?sensor\s+gap'
        r'|sensor\s+(?:gap|coverage|blind\s*spot)'
        r'|waiting\s+for\s+sensor'
        r'|(?:need|require|missing)\s+(?:sensor|pcap|capture)\s+data'
        r'|what\s+sensor\s+data\s+(?:is|are)\s+missing',
        re.IGNORECASE),
     'SENSOR_GAP_ANALYSIS'),

    # 5. SCHEDULER_SANITY
    (re.compile(
        r'(?:is|are)\s+(?:inference|the\s+scheduler)\s+(?:stuck|looping|spinning)'
        r'|scheduler\s+(?:sanity|status|health|stuck)'
        r'|inference\s+(?:stuck|loop|spinning|stalled)'
        r'|(?:runaway|infinite|unbounded)\s+(?:inference|loop|recursion)',
        re.IGNORECASE),
     'SCHEDULER_SANITY'),

    # 6. COST_ACCOUNTING
    (re.compile(
        r'(?:what|how\s+much)\s+(?:was|is|has\s+been)\s+wasted'
        r'|(?:wasted|failed|useless)\s+(?:inference|attempt|call|invocation)'
        r'|cost\s+(?:accounting|report|analysis|breakdown)'
        r'|inference\s+cost'
        r'|how\s+many\s+(?:attempts?|calls?)\s+(?:failed|wasted)',
        re.IGNORECASE),
     'COST_ACCOUNTING'),

    # 7. STRUCTURAL_DEBT
    (re.compile(
        r'structural\s+(?:debt|weakness|fragility)'
        r'|(?:weak|fragile|thin)\s+(?:point|spot|area)\s+(?:in|of|on)\s+(?:the\s+)?graph'
        r'|(?:under|un)\s*instrumented\s+(?:region|area|zone|kind)'
        r'|graph\s+(?:debt|health|integrity)',
        re.IGNORECASE),
     'STRUCTURAL_DEBT'),

    # 8. SILENCE_COMPLIANCE
    (re.compile(
        r'(?:why|explain)\s+(?:is|are|was|were)\s+(?:the\s+)?(?:system|engine|inference)\s+silent'
        r'|silence\s+(?:compliance|report|reason|explanation)'
        r'|why\s+(?:no|zero|is\s+there\s+no)\s+(?:output|result|inference|response)'
        r'|why\s+(?:did(?:n.t)?|wasn.t)\s+(?:anything|inference)\s+(?:run|produce|happen)',
        re.IGNORECASE),
     'SILENCE_COMPLIANCE'),

    # 9. EVIDENCE_REACTIVATION
    (re.compile(
        r'(?:has|have|did)\s+(?:new|fresh)\s+(?:evidence|sensor|data|pcap)\s+(?:arrive|come)'
        r'|new\s+evidence\s+(?:arrived|available|detected)'
        r'|evidence\s+(?:arrival|reactivation|update)'
        r'|what\s+(?:has|would)\s+(?:new\s+)?evidence\s+(?:change|unlock|reactivate)',
        re.IGNORECASE),
     'EVIDENCE_REACTIVATION'),
]


# ─────────────────────────────────────────────────────────────────────────────
# Heuristic intent patterns — "educated guess" questions that are NOT
# answerable from ledger/graph alone but are reasonable diagnostic /
# educational requests.  These get ANALYST_HEURISTIC authority: the LLM
# runs, but output is boxed and labelled non-authoritative.
# ─────────────────────────────────────────────────────────────────────────────

_HEURISTIC_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # "Why do I see X?" / "What causes X?" / "Why is X high?"
    # Broadened: "why" or "what causes" followed by ANY domain keyword,
    # with up to 8 intervening words to allow natural phrasing.
    (re.compile(
        r'(?:why|what\s+cause[sd]?|what\s+would\s+cause|what\s+leads?\s+to|explain)'
        r'(?:\s+\S+){0,8}\s*'
        r'(?:retransmission|packet\s*loss|latency|jitter|timeout|reset|rst|fin|'
        r'out.of.order|duplicate|fragmentation|icmp|arp|broadcast|multicast|'
        r'dns\s*(?:failure|timeout|error)|tls\s*(?:error|failure|handshake)|'
        r'connection\s*(?:refused|reset|timeout)|slow|high\s*latency|degraded|'
        r'congestion|window\s*(?:size|scaling)|mtu|ttl|throughput|bandwidth|'
        r'drop|loss|error\s*rate|fail)',
        re.IGNORECASE),
     'DIAGNOSTIC_HEURISTIC'),

    # "What does X look like?" / "What is normal for X?"
    (re.compile(
        r'what\s+(?:does|would|should)\s+(?:good|normal|healthy|typical|expected|baseline)\s+'
        r'(?:[\w\s]{0,30})(?:look\s+like|traffic|behavior|pattern)'
        r'|what\s+(?:is|are)\s+(?:normal|typical|expected|baseline)\s+'
        r'(?:[\w\s]{0,20})(?:for|in|with)?\s*'
        r'(?:dns|tls|tcp|http|udp|quic|icmp|arp|dhcp)',
        re.IGNORECASE),
     'BASELINE_HEURISTIC'),

    # "Is this suspicious?" / "Could this be X?"
    # Broadened: allow up to 6 intervening words between "this" and the
    # threat keyword ("Could this traffic pattern be a C2 beacon").
    (re.compile(
        r'(?:is|are|could|might|would)\s+(?:this|that|these|those|it)'
        r'(?:\s+\S+){0,6}\s+'
        r'(?:be\s+)?(?:suspicious|malicious|anomalous|unusual|bad|weird|'
        r'a\s+(?:scan|beacon|c2|exfil|attack|probe|flood|brute.force)|'
        r'beacon|c2|exfil|lateral\s*movement|compromised|infected|malware|'
        r'command.and.control|tunneling|covert)'
        r'|does\s+this\s+(?:look|seem)\s+(?:suspicious|malicious|unusual|bad|weird|off)',
        re.IGNORECASE),
     'SUSPICION_HEURISTIC'),

    # "What might cause X?" / "Common reasons for X"
    (re.compile(
        r'(?:common|typical|possible|likely|usual)\s+'
        r'(?:reasons?|causes?|explanations?|scenarios?)\s+(?:for|of|behind)'
        r'|what\s+(?:might|could|would)\s+(?:explain|cause|lead\s+to)',
        re.IGNORECASE),
     'EXPLANATION_HEURISTIC'),

    # "How does X work?" / "What is X protocol?" / "How does a TLS handshake work?"
    # Broadened: allows optional articles/modifiers between "how does" and
    # the protocol keyword ("how does a TLS handshake work").
    (re.compile(
        r'how\s+does\s+(?:a\s+|the\s+|an?\s+)?'
        r'(?:tcp|udp|dns|tls|quic|http|icmp|arp|dhcp|ntp|'
        r'bgp|ospf|stp|vrrp|ipsec|wireguard|ssh|ftp|smtp|imap|pop3|'
        r'radius|ldap|snmp|syslog|kerberos|ntlm|smb|rdp|vnc|mqtt|coap|'
        r'modbus|dnp3|bacnet|s7comm|profinet|ethernet|vlan|mpls|gre|'
        r'geneve|vxlan|nvgre|ppp|pppoe|l2tp)'
        r'(?:\s+\S+){0,4}\s*(?:work|operate|function|fail|break|error)'
        r'|what\s+is\s+(?:a\s+|the\s+)?(?:tcp|udp|dns|tls|quic|http)\s+'
        r'(?:handshake|exchange|negotiation|protocol|header|flag|option)',
        re.IGNORECASE),
     'PROTOCOL_EDUCATION'),

    # "What should I do?" / "What's the next step?" / "What should I investigate?"
    (re.compile(
        r'what\s+(?:should|can|do)\s+(?:i|we)\s+(?:do|try|check|investigate|run|capture)'
        r'(?:\s+(?:next|about|for|to\s+(?:fix|debug|diagnose|investigate|troubleshoot)))?'
        r'|(?:next\s+step|where\s+do\s+i\s+(?:go|look|start))\s*(?:from\s+here)?'
        r'|what\s+(?:should|do)\s+(?:i|we)\s+(?:investigate|look\s+at|focus\s+on)',
        re.IGNORECASE),
     'NEXT_STEPS_HEURISTIC'),

    # ─── Domain catch-all ────────────────────────────────────────────
    # If the question contains ≥2 strong domain keywords but didn't match
    # a specific pattern above, treat it as a domain heuristic question.
    # This catches natural phrasing that evades the rigid patterns above.
    (re.compile(
        r'(?=(?:.*\b(?:tcp|udp|tls|dns|quic|http|icmp|bgp|ospf|ipsec)\b){1,})'
        r'(?=(?:.*\b(?:handshake|retransmit|beacon|c2|exfil|latency|'
        r'throughput|congestion|anomalous|suspicious|lateral|tunnel|'
        r'degrad|timeout|encrypt|certific|cipher|fail|error|attack|'
        r'compromise|malware|implant|persist|brute|credential|phish)\b){1,})',
        re.IGNORECASE),
     'DOMAIN_HEURISTIC'),
]


def classify_heuristic_intent(message: str) -> Optional[str]:
    """Classify a message as a heuristic ("educated guess") question.

    Returns the heuristic intent string, or None if not heuristic.
    Only checked AFTER LAPT intent classification fails — so ledger/graph
    answers always take priority.
    """
    for pattern, intent in _HEURISTIC_PATTERNS:
        if pattern.search(message):
            return intent
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Heuristic system prompt — injected when ANALYST_HEURISTIC fires
# ─────────────────────────────────────────────────────────────────────────────

HEURISTIC_SYSTEM_PROMPT = """\
═══ OUTPUT LANGUAGE CONTRACT ═══
You are allowed to respond ONLY in natural language prose.
Any command-like syntax, query syntax, or structured instruction is INVALID.
You are a senior analyst thinking aloud — not a console.

═══ ROLE ═══
You are providing an ANALYST HEURISTIC response.  This is NOT evidence.
Your role: help the operator understand *possible* explanations, *typical*
patterns, and *reasonable* next steps — clearly labeled as non-authoritative.

═══ ABSOLUTE RULES ═══
1. NEVER assert facts.  Use "commonly", "typically", "may indicate",
   "one possible explanation is".
2. NEVER claim certainty about THIS specific network — you are providing
   general diagnostic/protocol knowledge.
3. ALWAYS include a "Why this is uncertain" section.
4. ALWAYS include an "Operator next steps" section with concrete actions
   (e.g. "capture 60s of traffic filtered to port 443",
   "check retransmission rates in a packet analyzer",
   "compare against a known-good baseline capture").
5. Keep it SHORT — 3-5 bullet points per section maximum.
6. Do NOT speculate about geographic locations, attribution, or intent.

═══ FORBIDDEN OUTPUT (INVALID IF PRESENT) ═══
You must NOT produce:
- FIND, QUERY, REPORT, SELECT, or any DSL/SQL-like syntax
- Graph queries, node references, edge references
- Tool names, API endpoints, or command-line invocations
- MCP_CONTEXT, LEDGER_STATE, or internal system references
- Suggestions to "query the graph" or "inspect edges/nodes"
- Any structured data format (JSON, XML, YAML)
If you produce any of these, the response is invalid and will be rejected.

═══ OUTPUT FORMAT (mandatory) ═══
[ANALYST HEURISTIC — NOT EVIDENCE]

What this might indicate (non-authoritative):
• ...

Why this is uncertain:
• ...

Operator next steps:
1. ...
2. ...
3. ...

Confidence: LOW | MEDIUM (never HIGH)
"""


def format_heuristic_response(raw_llm_output: str, heuristic_intent: str) -> str:
    """Wrap and sanitize an LLM heuristic response.

    Even if the LLM doesn't follow the template perfectly, this function
    ensures the output is properly boxed with the heuristic label and all
    command-like / DSL-like syntax is stripped.
    """
    badge = UX_BADGES[Authority.ANALYST_HEURISTIC]

    import re as _re
    sanitized = raw_llm_output

    # ── Strip any line containing DSL keywords followed by DSL targets ──
    # Catches standalone ("FIND NODES WHERE ...") and inline
    # ("try running FIND EDGES WHERE ...") patterns.
    sanitized = _re.sub(
        r'^.*(?:FIND|QUERY|SELECT|REPORT)\s+'
        r'(?:NODES|EDGES|NEIGHBORS|SUBGRAPH|FROM|INTO|WHERE|ALL)'
        r'\s+.+$',
        '',
        sanitized,
        flags=_re.MULTILINE | _re.IGNORECASE,
    )

    # ── Strip lines suggesting graph/DSL actions ──
    sanitized = _re.sub(
        r'^.*(?:query the graph|inspect (?:the )?(?:edges|nodes)|'
        r'search (?:the )?(?:graph|edges|nodes)|'
        r'look up (?:the )?(?:graph|edges|nodes)|'
        r'run (?:a |the )?(?:graph |DSL )?query).*$',
        '',
        sanitized,
        flags=_re.MULTILINE | _re.IGNORECASE,
    )

    # ── Strip internal system references ──
    sanitized = _re.sub(
        r'(?:MCP_CONTEXT|LEDGER_STATE|GRAPH_CONTEXT|WRITE_SUMMARY|'
        r'MCP_FOCUS|BELIEF_DRIFT|edge_kind_index|node_to_edges|'
        r'hypergraph|HypergraphEngine)',
        '[internal reference removed]',
        sanitized,
        flags=_re.IGNORECASE,
    )

    # ── Strip JSON/structured blocks ──
    sanitized = _re.sub(
        r'^\s*[{\[].*[}\]]\s*$',
        '',
        sanitized,
        flags=_re.MULTILINE,
    )

    # ── Clean up blank lines from stripping ──
    sanitized = _re.sub(r'\n{3,}', '\n\n', sanitized).strip()

    # Ensure the response has the heuristic header
    if '[ANALYST HEURISTIC' not in sanitized.upper():
        sanitized = (
            "[ANALYST HEURISTIC — NOT EVIDENCE]\n\n"
            + sanitized
        )

    # Ensure a confidence footer exists
    if 'confidence:' not in sanitized.lower():
        sanitized += "\n\nConfidence: LOW"

    return f"{badge}\n\n{sanitized}"

TEMPLATE_REGISTRY: Dict[str, PromptTemplate] = {

    'EXHAUSTION_INSPECTION': PromptTemplate(
        intent='EXHAUSTION_INSPECTION',
        scope='all entities with exhaustion records',
        ledger_guard='ledger.get_exhausted_entities()',
        data_requirements='exhaustion records, resume conditions, timestamps',
        allowed_operations='READ ledger — no inference, no graph mutation',
        output_contract=(
            'JSON list of {entity_id, entity_kind, rule_id, evidence_epoch, '
            'exhausted_ts, resume_condition, attempt_count}. '
            'If empty: "No entities are currently exhausted."'
        ),
        failure_modes='EMPTY → report silence with reason',
    ),

    'REACTIVATION_AUDIT': PromptTemplate(
        intent='REACTIVATION_AUDIT',
        scope='exhausted entities with resume_condition.type == NEW_SENSOR',
        ledger_guard='ledger.waiting_for_sensor()',
        data_requirements='resume conditions, entity kinds, sensor dependencies',
        allowed_operations='READ ledger + graph topology — no inference',
        output_contract=(
            'List of {entity_id, resume_condition, recommended_action}. '
            'recommended_action is one of: CAPTURE, RESCAN, MANUAL_CONFIRM, WAIT. '
            'If no reactivation candidates: "All entities are active or permanently blocked."'
        ),
        failure_modes='EMPTY → "No entities waiting for sensor reactivation."',
    ),

    'VALIDATOR_ANALYSIS': PromptTemplate(
        intent='VALIDATOR_ANALYSIS',
        scope='recent inference attempts with POLICY_BLOCKED or NO_VALID_EDGES',
        ledger_guard='ledger stats by_result',
        data_requirements='exhaustion records with blocked_reason, policy table',
        allowed_operations='READ ledger + policy table — no inference',
        output_contract=(
            'For each blocked entity: {entity_id, rule_id, last_result, '
            'blocked_reason, policy_citation}. '
            'Distinguish: schema rejection vs policy block vs empty evidence.'
        ),
        failure_modes='EMPTY → "No validator rejections recorded in current epoch."',
    ),

    'SENSOR_GAP_ANALYSIS': PromptTemplate(
        intent='SENSOR_GAP_ANALYSIS',
        scope='entities exhausted due to missing sensor data',
        ledger_guard='ledger.waiting_for_sensor()',
        data_requirements='sensor gap list, entity neighborhoods, collection tasks',
        allowed_operations='READ ledger + graph — propose CollectionTask if absent',
        output_contract=(
            'List of {entity_id, sensor_type_needed, existing_coverage, '
            'recommended_capture_spec}. Rank by expected belief delta.'
        ),
        failure_modes='EMPTY → "Sensor coverage is complete for all active entities."',
    ),

    'SCHEDULER_SANITY': PromptTemplate(
        intent='SCHEDULER_SANITY',
        scope='all inference state — ledger + run history',
        ledger_guard='ledger.stats()',
        data_requirements='ledger stats, run history, synthetic node counts',
        allowed_operations='READ ledger + run history — no inference',
        output_contract=(
            '{total_records, exhausted_count, active_count, by_result, '
            'is_stuck: bool, stuck_reason: str|null}. '
            'Definition: stuck = exhausted_count > 0.7 * total_records AND active_count == 0'
        ),
        failure_modes='No records → "Scheduler has no inference history — system idle."',
    ),

    'COST_ACCOUNTING': PromptTemplate(
        intent='COST_ACCOUNTING',
        scope='all inference attempts across all epochs',
        ledger_guard='ledger.stats()',
        data_requirements='attempt counts by result, entity kind breakdown',
        allowed_operations='READ ledger — aggregation only',
        output_contract=(
            '{total_attempts, successful, wasted (NO_VALID_EDGES + POLICY_BLOCKED), '
            'error_retryable, waste_ratio: float, top_wasted_entities: list}'
        ),
        failure_modes='No records → "No inference cost data available."',
    ),

    'STRUCTURAL_DEBT': PromptTemplate(
        intent='STRUCTURAL_DEBT',
        scope='graph topology + exhaustion coverage',
        ledger_guard='ledger.stats() + graph node/edge counts',
        data_requirements='node kind distribution, edge coverage, exhaustion map',
        allowed_operations='READ graph + ledger — topology analysis only',
        output_contract=(
            '{under_instrumented_kinds: list, single_point_entities: list, '
            'exhaustion_hotspots: list, recommended_actions: list}. '
            'LLM may synthesize if data supports it.'
        ),
        failure_modes='Empty graph → "No graph structure to analyze."',
    ),

    'SILENCE_COMPLIANCE': PromptTemplate(
        intent='SILENCE_COMPLIANCE',
        scope='system output state + ledger + policy',
        ledger_guard='ledger.stats() + ledger.get_exhausted_entities()',
        data_requirements='exhaustion stats, policy blocks, run history',
        allowed_operations='READ all state — explain silence, do not break it',
        output_contract=(
            '{reason: str, exhausted_entities: int, policy_blocks: int, '
            'empty_evidence: int, system_idle: bool}. '
            'Reason must cite specific ledger/policy state. '
            'NEVER recommend "just try again" — that violates exhaustion.'
        ),
        failure_modes='System is not silent → "System is active — no silence to explain."',
    ),

    'EVIDENCE_REACTIVATION': PromptTemplate(
        intent='EVIDENCE_REACTIVATION',
        scope='entities with stale epochs vs current graph state',
        ledger_guard='compare stored epochs against live evidence_epoch',
        data_requirements='exhausted entities + current evidence epochs',
        allowed_operations='READ ledger + compute epochs — flag changed ones',
        output_contract=(
            'List of {entity_id, stored_epoch, current_epoch, changed: bool, '
            'action: REACTIVATE|STILL_WAITING}. '
            'If entity epoch changed → exhaustion auto-clears on next inference.'
        ),
        failure_modes='No exhausted entities → "No entities pending reactivation."',
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Intent classifier
# ─────────────────────────────────────────────────────────────────────────────

def classify_intent(message: str) -> Optional[str]:
    """Classify an operator message into one of the 9 LAPT intent classes.

    Returns the intent string or None if no match.
    """
    for pattern, intent in _INTENT_PATTERNS:
        if pattern.search(message):
            return intent
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Ledger query executors — one per intent class
# ─────────────────────────────────────────────────────────────────────────────

def _exec_exhaustion_inspection(ledger, engine) -> str:
    """Execute EXHAUSTION_INSPECTION from ledger alone."""
    exhausted = ledger.get_exhausted_entities()
    if not exhausted:
        return (
            "**Exhaustion Ledger: CLEAR**\n\n"
            "No entities are currently exhausted. All inference targets "
            "are eligible for processing if new queries arrive."
        )

    lines = ["**Exhaustion Ledger Report**\n"]
    lines.append(f"**{len(exhausted)} entities exhausted:**\n")
    for rec in exhausted:
        resume = rec.get('resume_condition') or {}
        resume_str = resume.get('detail', 'unknown')
        ts = rec.get('exhausted_ts', 0)
        age = f"{(time.time() - ts) / 60:.1f}min ago" if ts else "unknown"
        lines.append(
            f"- **{rec['entity_id']}** ({rec.get('entity_kind', '?')}) "
            f"| rule: {rec.get('rule_id', '?')} "
            f"| result: {rec.get('last_result', '?')} "
            f"| attempts: {rec.get('attempt_count', 0)} "
            f"| exhausted: {age} "
            f"| resume: {resume_str}"
        )

    stats = ledger.stats()
    lines.append(f"\n**Summary:** {stats['total_records']} total records, "
                 f"{stats['exhausted_count']} exhausted, "
                 f"{stats['active_count']} active.")
    return "\n".join(lines)


def _exec_reactivation_audit(ledger, engine) -> str:
    """Execute REACTIVATION_AUDIT — entities waiting for sensor."""
    waiting = ledger.waiting_for_sensor()
    if not waiting:
        return (
            "**Reactivation Audit: CLEAR**\n\n"
            "No entities waiting for sensor reactivation. "
            "All exhausted entities (if any) are blocked by policy, not missing data."
        )

    lines = ["**Reactivation Candidates**\n"]
    lines.append(f"**{len(waiting)} entities waiting for sensor data:**\n")
    for rec in waiting:
        resume = rec.get('resume_condition') or {}
        entity_kind = rec.get('entity_kind', 'unknown')
        # Recommend action based on kind
        if entity_kind in ('host', 'flow'):
            action = "CAPTURE — pcap or active scan recommended"
        elif entity_kind == 'pcap_session':
            action = "RESCAN — re-process existing pcap with updated rules"
        else:
            action = "MANUAL_CONFIRM — operator verification required"

        lines.append(
            f"- **{rec['entity_id']}** ({entity_kind}) "
            f"| resume: {resume.get('detail', 'new sensor data')} "
            f"| recommended: {action}"
        )
    return "\n".join(lines)


def _exec_validator_analysis(ledger, engine) -> str:
    """Execute VALIDATOR_ANALYSIS — rejection/block report."""
    stats = ledger.stats()
    by_result = stats.get('by_result', {})

    blocked = by_result.get('POLICY_BLOCKED', 0)
    no_valid = by_result.get('NO_VALID_EDGES', 0)
    total_waste = blocked + no_valid

    if total_waste == 0:
        return (
            "**Validator Analysis: CLEAN**\n\n"
            "No validator rejections or policy blocks recorded in the current epoch. "
            "All inference attempts either succeeded or encountered transient errors."
        )

    lines = ["**Validator Analysis Report**\n"]
    lines.append(f"**Rejection breakdown:**")
    lines.append(f"- POLICY_BLOCKED: {blocked}")
    lines.append(f"- NO_VALID_EDGES (schema/validator rejection): {no_valid}")
    lines.append(f"- Total wasteful attempts: {total_waste}")
    lines.append("")

    # List entities that were blocked
    exhausted = ledger.get_exhausted_entities()
    policy_blocked = [r for r in exhausted if r.get('last_result') == 'POLICY_BLOCKED']
    schema_rejected = [r for r in exhausted if r.get('last_result') == 'NO_VALID_EDGES']

    if policy_blocked:
        lines.append("**Policy-blocked entities:**")
        for rec in policy_blocked:
            lines.append(
                f"  - {rec['entity_id']} | reason: {rec.get('blocked_reason', 'unknown')}"
            )

    if schema_rejected:
        lines.append("\n**Schema/validator-rejected entities:**")
        for rec in schema_rejected:
            lines.append(
                f"  - {rec['entity_id']} | attempts: {rec.get('attempt_count', 0)} "
                f"| all produced 0 valid edges"
            )

    return "\n".join(lines)


def _exec_sensor_gap_analysis(ledger, engine) -> str:
    """Execute SENSOR_GAP_ANALYSIS — combined waiting_for_sensor + graph topology."""
    waiting = ledger.waiting_for_sensor()

    # Also check graph coverage
    node_count = 0
    edge_count = 0
    kinds = {}
    if hasattr(engine, 'nodes') and isinstance(engine.nodes, dict):
        node_count = len(engine.nodes)
        for n in engine.nodes.values():
            nd = n if isinstance(n, dict) else (n.to_dict() if hasattr(n, 'to_dict') else {})
            k = nd.get('kind', 'unknown')
            kinds[k] = kinds.get(k, 0) + 1
    if hasattr(engine, 'edges') and isinstance(engine.edges, dict):
        edge_count = len(engine.edges)

    lines = ["**Sensor Gap Analysis**\n"]
    lines.append(f"Graph: {node_count} nodes, {edge_count} edges across {len(kinds)} kinds.\n")

    if waiting:
        lines.append(f"**{len(waiting)} entities waiting for sensor data:**\n")
        for rec in waiting:
            lines.append(
                f"- **{rec['entity_id']}** ({rec.get('entity_kind', '?')}) "
                f"| needs: new sensor observation "
                f"| stale since: {rec.get('attempt_count', 0)} attempts"
            )
    else:
        lines.append("No entities waiting for sensor data.\n")

    # Check for under-instrumented kinds
    if kinds:
        # kinds with few nodes relative to graph size might be under-instrumented
        avg = node_count / len(kinds) if kinds else 0
        thin = {k: c for k, c in kinds.items() if c < max(2, avg * 0.3)}
        if thin:
            lines.append("\n**Under-instrumented kinds** (low node count):")
            for k, c in sorted(thin.items(), key=lambda x: x[1]):
                lines.append(f"  - {k}: {c} node{'s' if c != 1 else ''}")

    return "\n".join(lines)


def _exec_scheduler_sanity(ledger, engine) -> str:
    """Execute SCHEDULER_SANITY — is inference stuck?"""
    stats = ledger.stats()
    total = stats['total_records']
    exhausted = stats['exhausted_count']
    active = stats['active_count']

    if total == 0:
        return (
            "**Scheduler Sanity: IDLE**\n\n"
            "No inference records in the ledger. The scheduler has not run, "
            "or all records have been evicted. System is idle."
        )

    # Stuck heuristic: >70% exhausted AND 0 active
    is_stuck = (exhausted > 0.7 * total) and (active == 0) and (total > 2)
    stuck_reason = None
    if is_stuck:
        by_result = stats.get('by_result', {})
        if by_result.get('POLICY_BLOCKED', 0) > by_result.get('NO_VALID_EDGES', 0):
            stuck_reason = "Majority of entities blocked by policy constraints"
        else:
            stuck_reason = "Majority of entities produced 0 valid edges — waiting for new evidence"

    lines = ["**Scheduler Sanity Report**\n"]
    lines.append(f"- Total records: {total}")
    lines.append(f"- Exhausted: {exhausted}")
    lines.append(f"- Active: {active}")
    lines.append(f"- By result: {stats.get('by_result', {})}")
    lines.append(f"- **Stuck: {'YES' if is_stuck else 'NO'}**")
    if stuck_reason:
        lines.append(f"- Stuck reason: {stuck_reason}")
        lines.append("\n*Remediation: supply new sensor data to change evidence epochs, "
                     "or adjust materialization policy to unblock eligible kinds.*")
    return "\n".join(lines)


def _exec_cost_accounting(ledger, engine) -> str:
    """Execute COST_ACCOUNTING — wasted inference attempts."""
    stats = ledger.stats()
    total = stats['total_records']

    if total == 0:
        return "**Cost Accounting: NO DATA**\n\nNo inference attempts recorded."

    by_result = stats.get('by_result', {})
    success = by_result.get('SUCCESS', 0)
    no_valid = by_result.get('NO_VALID_EDGES', 0)
    policy = by_result.get('POLICY_BLOCKED', 0)
    error = by_result.get('ERROR', 0)
    wasted = no_valid + policy
    waste_ratio = wasted / total if total else 0.0

    # Find top wasted entities
    exhausted = ledger.get_exhausted_entities()
    top_wasted = sorted(exhausted, key=lambda r: r.get('attempt_count', 0), reverse=True)[:5]

    lines = ["**Cost Accounting Report**\n"]
    lines.append(f"- Total inference records: {total}")
    lines.append(f"- Successful: {success}")
    lines.append(f"- Wasted (NO_VALID_EDGES + POLICY_BLOCKED): {wasted}")
    lines.append(f"- Error (retryable): {error}")
    lines.append(f"- **Waste ratio: {waste_ratio:.1%}**")

    if top_wasted:
        lines.append(f"\n**Top wasted entities:**")
        for rec in top_wasted:
            lines.append(
                f"  - {rec['entity_id']} | {rec.get('attempt_count', 0)} attempts "
                f"| {rec.get('last_result', '?')}"
            )

    return "\n".join(lines)


def _exec_structural_debt(ledger, engine) -> str:
    """Execute STRUCTURAL_DEBT — topology + exhaustion analysis.

    This one may benefit from LLM synthesis, so we return data +
    mark that LLM enhancement is welcome.
    """
    stats = ledger.stats()

    # Graph topology
    node_count = 0
    edge_count = 0
    kinds: Dict[str, int] = {}
    degree: Dict[str, int] = {}

    if hasattr(engine, 'nodes') and isinstance(engine.nodes, dict):
        node_count = len(engine.nodes)
        for nid, n in engine.nodes.items():
            nd = n if isinstance(n, dict) else (n.to_dict() if hasattr(n, 'to_dict') else {})
            k = nd.get('kind', 'unknown')
            kinds[k] = kinds.get(k, 0) + 1

    if hasattr(engine, 'edges') and isinstance(engine.edges, dict):
        edge_count = len(engine.edges)

    if hasattr(engine, 'degree') and isinstance(engine.degree, dict):
        degree = dict(engine.degree)

    # Single-point entities (degree ≤ 1)
    single_point = [nid for nid, d in degree.items() if d <= 1]

    # Exhaustion hotspots (kinds with most exhausted entities)
    exhausted = ledger.get_exhausted_entities()
    kind_exhausted: Dict[str, int] = {}
    for rec in exhausted:
        k = rec.get('entity_kind', 'unknown')
        kind_exhausted[k] = kind_exhausted.get(k, 0) + 1

    lines = ["**Structural Debt Analysis**\n"]
    lines.append(f"Graph: {node_count} nodes, {edge_count} edges.\n")

    if kinds:
        lines.append("**Node kind distribution:**")
        for k, c in sorted(kinds.items(), key=lambda x: -x[1]):
            lines.append(f"  - {k}: {c}")

    if single_point:
        sp_count = len(single_point)
        lines.append(f"\n**Single-point entities (degree ≤ 1): {sp_count}**")
        for nid in single_point[:10]:
            lines.append(f"  - {nid}")
        if sp_count > 10:
            lines.append(f"  ... and {sp_count - 10} more")

    if kind_exhausted:
        lines.append(f"\n**Exhaustion hotspots by kind:**")
        for k, c in sorted(kind_exhausted.items(), key=lambda x: -x[1]):
            lines.append(f"  - {k}: {c} exhausted")

    return "\n".join(lines)


def _exec_silence_compliance(ledger, engine) -> str:
    """Execute SILENCE_COMPLIANCE — explain why system is silent."""
    stats = ledger.stats()
    exhausted = stats['exhausted_count']
    total = stats['total_records']
    by_result = stats.get('by_result', {})

    lines = ["**Silence Compliance Report**\n"]

    if total == 0:
        lines.append("**Reason: SYSTEM IDLE** — no inference has been attempted.")
        lines.append("The system has no records in the exhaustion ledger.")
        lines.append("This is normal before the first inference batch.")
        return "\n".join(lines)

    policy_blocks = by_result.get('POLICY_BLOCKED', 0)
    empty_evidence = by_result.get('NO_VALID_EDGES', 0)
    system_idle = exhausted == total and total > 0

    reasons = []
    if system_idle:
        reasons.append("ALL entities are exhausted — system is correctly waiting for new evidence")
    if policy_blocks > 0:
        reasons.append(f"{policy_blocks} entities blocked by materialization policy")
    if empty_evidence > 0:
        reasons.append(f"{empty_evidence} entities produced 0 valid edges (model had nothing to say)")

    if not reasons:
        lines.append("**System is NOT silent** — active inference targets exist.")
        lines.append(f"Active: {stats['active_count']}, Exhausted: {exhausted}")
        return "\n".join(lines)

    lines.append(f"**Silence is COMPLIANT** — the system is correctly quiet.\n")
    lines.append("**Reasons:**")
    for r in reasons:
        lines.append(f"  - {r}")

    lines.append(f"\n**Ledger state:** {total} records, {exhausted} exhausted, "
                 f"{stats['active_count']} active.")
    lines.append("\n*Silence is preferable to hallucination. "
                 "Supply new sensor data to reactivate inference.*")
    return "\n".join(lines)


def _exec_evidence_reactivation(ledger, engine) -> str:
    """Execute EVIDENCE_REACTIVATION — check if evidence epochs changed."""
    exhausted = ledger.get_exhausted_entities()
    if not exhausted:
        return (
            "**Evidence Reactivation: NO PENDING**\n\n"
            "No exhausted entities — nothing pending reactivation."
        )

    from inference_exhaustion_ledger import InferenceExhaustionLedger

    changed = []
    still_waiting = []

    for rec in exhausted:
        entity_id = rec['entity_id']
        stored_epoch = rec['evidence_epoch']
        try:
            current_epoch = InferenceExhaustionLedger.compute_evidence_epoch(
                engine, entity_id
            )
        except Exception:
            current_epoch = stored_epoch  # can't compute → assume unchanged

        if current_epoch != stored_epoch:
            changed.append({
                'entity_id': entity_id,
                'stored_epoch': stored_epoch[:8],
                'current_epoch': current_epoch[:8],
                'action': 'REACTIVATE',
            })
        else:
            still_waiting.append({
                'entity_id': entity_id,
                'epoch': stored_epoch[:8],
                'action': 'STILL_WAITING',
            })

    lines = ["**Evidence Reactivation Check**\n"]

    if changed:
        lines.append(f"**{len(changed)} entities have NEW evidence — will reactivate:**\n")
        for c in changed:
            lines.append(
                f"- **{c['entity_id']}** | epoch {c['stored_epoch']}→{c['current_epoch']} "
                f"| action: REACTIVATE"
            )

    if still_waiting:
        lines.append(f"\n**{len(still_waiting)} entities still waiting:**")
        for w in still_waiting:
            lines.append(f"- {w['entity_id']} | epoch {w['epoch']} (unchanged)")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Executor dispatch table
# ─────────────────────────────────────────────────────────────────────────────

_EXECUTORS = {
    'EXHAUSTION_INSPECTION': _exec_exhaustion_inspection,
    'REACTIVATION_AUDIT': _exec_reactivation_audit,
    'VALIDATOR_ANALYSIS': _exec_validator_analysis,
    'SENSOR_GAP_ANALYSIS': _exec_sensor_gap_analysis,
    'SCHEDULER_SANITY': _exec_scheduler_sanity,
    'COST_ACCOUNTING': _exec_cost_accounting,
    'STRUCTURAL_DEBT': _exec_structural_debt,
    'SILENCE_COMPLIANCE': _exec_silence_compliance,
    'EVIDENCE_REACTIVATION': _exec_evidence_reactivation,
}


# ─────────────────────────────────────────────────────────────────────────────
# Confidence decay math
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EpistemicState:
    """Live epistemic state snapshot for a query scope."""
    sensor_fraction: float = 0.0
    inference_fraction: float = 0.0
    evidence_coverage: float = 0.0
    stale_inference_count: int = 0
    exhaustion_ratio: float = 0.0
    time_to_below_threshold_min: float = float('inf')
    hallucination_risk: str = 'LOW'   # LOW | MEDIUM | HIGH

    def to_dict(self) -> Dict[str, Any]:
        return {
            'sensor_fraction': round(self.sensor_fraction, 3),
            'inference_fraction': round(self.inference_fraction, 3),
            'evidence_coverage': round(self.evidence_coverage, 3),
            'stale_inference_count': self.stale_inference_count,
            'exhaustion_ratio': round(self.exhaustion_ratio, 3),
            'time_to_below_threshold_min': (
                round(self.time_to_below_threshold_min, 1)
                if self.time_to_below_threshold_min < 1e6 else None
            ),
            'hallucination_risk': self.hallucination_risk,
        }


def compute_epistemic_state(ledger, engine) -> EpistemicState:
    """Compute a live epistemic state snapshot.

    Uses ledger stats + graph edge provenance to determine:
      - sensor vs inference fraction
      - evidence coverage
      - confidence decay timeline
      - hallucination risk
    """
    stats = ledger.stats()
    total = stats['total_records']
    exhausted = stats['exhausted_count']
    by_result = stats.get('by_result', {})

    # Edge provenance census
    sensor_count = 0
    inference_count = 0
    total_edges = 0
    stale_count = 0
    now = time.time()

    if hasattr(engine, 'edges') and isinstance(engine.edges, dict):
        for eid, e in engine.edges.items():
            ed = e if isinstance(e, dict) else (
                e.to_dict() if hasattr(e, 'to_dict') else {}
            )
            total_edges += 1
            prov = ed.get('provenance', {}) or {}
            source = prov.get('source', '') or ed.get('source_system', '')
            if source in ('pcap_ingest', 'sensor', 'dns_passive', 'netflow'):
                sensor_count += 1
            else:
                inference_count += 1
            # Check staleness (> 30 min since last update)
            ts = ed.get('timestamp', 0) or ed.get('updated_at', 0)
            if ts and isinstance(ts, (int, float)) and (now - ts) > 1800:
                stale_count += 1

    sensor_frac = sensor_count / total_edges if total_edges else 0.0
    inference_frac = inference_count / total_edges if total_edges else 0.0
    evidence_coverage = sensor_count / max(total_edges, 1)
    exhaustion_ratio = exhausted / total if total else 0.0

    # Confidence decay: time to below threshold
    if inference_frac > 0 and total_edges > 0:
        # Each minute decays by inference_fraction × avg_depth × DECAY_CONSTANT
        avg_depth = 2  # conservative estimate
        decay_per_min = inference_frac * avg_depth * DECAY_CONSTANT
        stale_drain = stale_count * STALE_PENALTY
        current_conf = max(0.0, 1.0 - inference_frac - stale_drain)
        threshold = 0.5  # below this = unacceptably speculative
        if current_conf > threshold and decay_per_min > 0:
            time_to_threshold = (current_conf - threshold) / decay_per_min
        elif current_conf <= threshold:
            time_to_threshold = 0.0
        else:
            time_to_threshold = float('inf')
    else:
        time_to_threshold = float('inf')

    # Hallucination risk
    if (inference_frac > SPECULATION_DOMINANT_THRESHOLD
            or evidence_coverage < EVIDENCE_THIN_THRESHOLD
            or exhaustion_ratio > SILENCE_EXHAUSTION_RATIO):
        risk = 'HIGH'
    elif (inference_frac > 0.4
          or stale_count >= STALE_DECAY_THRESHOLD
          or exhaustion_ratio > 0.5):
        risk = 'MEDIUM'
    else:
        risk = 'LOW'

    return EpistemicState(
        sensor_fraction=sensor_frac,
        inference_fraction=inference_frac,
        evidence_coverage=evidence_coverage,
        stale_inference_count=stale_count,
        exhaustion_ratio=exhaustion_ratio,
        time_to_below_threshold_min=time_to_threshold,
        hallucination_risk=risk,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Trigger conditions — when IEL should interrupt
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TriggerAlert:
    """An epistemic trigger condition that fired."""
    code: str       # EVIDENCE_THIN | SPECULATION_DOMINANT | DECAY_IMMINENT | EXHAUSTION
    message: str
    severity: str   # WARNING | CRITICAL


def evaluate_triggers(state: EpistemicState) -> List[TriggerAlert]:
    """Evaluate epistemic trigger conditions.

    Returns list of alerts (empty if all clear).
    """
    alerts: List[TriggerAlert] = []

    if state.evidence_coverage < EVIDENCE_THIN_THRESHOLD:
        alerts.append(TriggerAlert(
            code='EVIDENCE_THIN',
            message=(
                f'Evidence coverage {state.evidence_coverage:.0%} — '
                f'below threshold ({EVIDENCE_THIN_THRESHOLD:.0%}). '
                f'Many claims lack sensor backing.'
            ),
            severity='WARNING',
        ))

    if state.inference_fraction > SPECULATION_DOMINANT_THRESHOLD:
        alerts.append(TriggerAlert(
            code='SPECULATION_DOMINANT',
            message=(
                f'Inference fraction {state.inference_fraction:.0%} — '
                f'speculation dominates ({SPECULATION_DOMINANT_THRESHOLD:.0%} threshold). '
                f'Hedge all claims.'
            ),
            severity='WARNING',
        ))

    if state.stale_inference_count >= STALE_DECAY_THRESHOLD:
        alerts.append(TriggerAlert(
            code='DECAY_IMMINENT',
            message=(
                f'{state.stale_inference_count} stale inferences detected. '
                f'Confidence decaying. '
                f'Time to threshold: ~{state.time_to_below_threshold_min:.0f}min.'
            ),
            severity='WARNING',
        ))

    if state.exhaustion_ratio > SILENCE_EXHAUSTION_RATIO:
        alerts.append(TriggerAlert(
            code='EXHAUSTION',
            message=(
                f'Exhaustion ratio {state.exhaustion_ratio:.0%} — '
                f'silence is the correct response. '
                f'No new inference possible without fresh sensor data.'
            ),
            severity='CRITICAL',
        ))

    if state.time_to_below_threshold_min < EXHAUSTION_TIME_THRESHOLD_MIN:
        alerts.append(TriggerAlert(
            code='THRESHOLD_BREACH',
            message=(
                f'Confidence will drop below threshold in '
                f'~{state.time_to_below_threshold_min:.0f}min. '
                f'Recommend immediate sensor collection.'
            ),
            severity='CRITICAL',
        ))

    return alerts


# ─────────────────────────────────────────────────────────────────────────────
# Silence Template — forced structure when exhaustion requires silence
# ─────────────────────────────────────────────────────────────────────────────

def build_silence_template(state: EpistemicState, ledger) -> str:
    """Build a forced silence response when exhaustion conditions are met.

    No freeform prose. No geography. No vibes.
    """
    stats = ledger.stats()
    exhausted = stats['exhausted_count']
    waiting = ledger.waiting_for_sensor()

    return (
        f"⚫ SILENT BY DESIGN\n\n"
        f"Credibility posture: SILENT BY DESIGN\n"
        f"Evidence coverage: {state.evidence_coverage:.0%}\n"
        f"Exhausted entities: {exhausted}\n"
        f"Hallucination risk: {state.hallucination_risk}\n\n"
        f"SITUATION:\n"
        f"Insufficient qualifying evidence exists to support analysis.\n\n"
        f"ASSESSMENT:\n"
        f"Any narrative would be inference-only and violates confidence policy.\n"
        f"Inference fraction: {state.inference_fraction:.0%}. "
        f"Sensor fraction: {state.sensor_fraction:.0%}.\n\n"
        f"DIRECTION:\n"
        f"No further analysis warranted without new sensor evidence.\n"
        + (f"Entities waiting for sensor data: {len(waiting)}.\n"
           if waiting else "")
        + f"Supply new pcap, DNS passive, or netflow data to reactivate."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Auto-Suggest Prompt Rewrites (Picard Adapter)
# ─────────────────────────────────────────────────────────────────────────────

# Patterns that indicate epistemically illegal questions under exhaustion
_ILLEGAL_PATTERNS: List[Tuple[re.Pattern, str, List[str]]] = [
    # (pattern, reason, suggested_rewrites)
    (re.compile(
        r'(?:is|are)\s+(?:[\w:.\-]+\s+){1,3}(?:malicious|suspicious|bad|evil|hostile|compromised)',
        re.IGNORECASE),
     'binary attribution unsupported by sensor evidence',
     [
         'What behaviors from {target} are directly sensor-confirmed?',
         'Which claims about {target} rely only on inference?',
         'What evidence would be required to assess intent?',
     ]),
    (re.compile(
        r'(?:summarize|summary|overview|recap|describe|tell me about)\s+'
        r'(?:SESSION|session|host|flow|node)[\-:\w]+',
        re.IGNORECASE),
     'direct imperative without authority selection',
     [
         'What is the ledger status for {target}?',
         'What sensor-backed evidence exists for {target}?',
         'What is the inference delta for {target}?',
         'Why is no summary possible for {target}?',
     ]),
    (re.compile(
        r'what\s+(?:is|are)\s+(?:happening|going\s+on)\s+(?:in|at|near|around)\s+'
        r'(?:Brazil|Europe|Asia|Africa|Middle\s+East|[A-Z][a-z]+)',
        re.IGNORECASE),
     'geographic narrative requires sensor grounding',
     [
         'What sensor-confirmed activity exists in {target}?',
         'What is the evidence coverage for {target}?',
         'Why is silence the correct response for {target}?',
     ]),
    (re.compile(
        r'(?:predict|forecast|will|expect)\s+.*(?:attack|breach|incident|threat)',
        re.IGNORECASE),
     'predictive attribution exceeds sensor evidence',
     [
         'What observable patterns could indicate future activity?',
         'What sensor gaps would need to be closed to assess risk?',
         'What is the current confidence level for threat claims?',
     ]),
]


def check_epistemic_legality(
    message: str,
    state: EpistemicState,
) -> Optional[Dict[str, Any]]:
    """Check if a prompt is epistemically illegal under current exhaustion.

    Returns None if legal, or a dict with rewrite suggestions if illegal.
    Only fires when hallucination_risk is HIGH.
    """
    if state.hallucination_risk != 'HIGH':
        return None

    for pattern, reason, rewrites in _ILLEGAL_PATTERNS:
        m = pattern.search(message)
        if m:
            # Extract target from the match
            target = m.group(0).split()[-1] if m.group(0) else 'this entity'
            suggested = [r.replace('{target}', target) for r in rewrites]
            return {
                'illegal': True,
                'reason': reason,
                'original': message,
                'suggested_prompts': suggested,
            }
    return None


def format_prompt_rewrite_response(rewrite_info: Dict[str, Any]) -> str:
    """Format a prompt rewrite suggestion for the operator."""
    lines = [
        f"⚫ EPISTEMIC BOUNDARY\n",
        f"This question cannot be reliably answered under current conditions.",
        f"**Reason:** {rewrite_info['reason']}\n",
        f"**Suggested alternatives:**",
    ]
    for i, prompt in enumerate(rewrite_info['suggested_prompts'], 1):
        lines.append(f"  ({chr(96 + i)}) {prompt}")
    lines.append(f"\n*These alternatives are grounded in what the system can ")
    lines.append(f"actually answer from sensor evidence and ledger state.*")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# LAPT Compiler
# ─────────────────────────────────────────────────────────────────────────────

class LAPTCompiler:
    """Ledger-Aware Prompt Template compiler + executor.

    Sits between operator queries and Gemma.  For ledger-answerable
    questions, returns structured data without invoking the LLM.
    For questions that need LLM synthesis, injects ledger context
    so the model is grounded in exhaustion state.

    Hierarchy: Ledger > Graph > Model (always).
    """

    def __init__(self, engine: Any, ledger):
        """
        Parameters
        ----------
        engine : HypergraphEngine
            The live graph (for node/edge queries + epoch computation).
        ledger : InferenceExhaustionLedger
            The shared exhaustion ledger.
        """
        self.engine = engine
        self.ledger = ledger

    def compile(self, message: str) -> LAPTResult:
        """Compile an operator message through the LAPT pipeline.

        Steps:
            1. Compute epistemic state
            2. Check epistemic legality (auto-rewrite illegal prompts)
            3. Evaluate trigger conditions
            4. Classify intent
            5. If LAPT intent → execute ledger query → short-circuit
            6. If exhaustion triggers silence → force silence template
            7. Otherwise → build ledger context for LLM injection

        Returns
        -------
        LAPTResult
            .short_circuit = True if answer was resolved from ledger/graph
            .response = the answer (if short_circuit)
            .ledger_context = context block to inject into LLM prompt
            .intent = detected intent class
            .template = matched template
            .authority = Authority classification
            .forbid_dsl = True if DSL must be suppressed
            .ux_badge = visual authority tag
            .prompt_rewrites = suggested alternatives (if illegal)
        """
        # ── Step 1: Epistemic state snapshot ──
        ep_state = compute_epistemic_state(self.ledger, self.engine)
        triggers = evaluate_triggers(ep_state)

        # ── Step 2: Classify intent ──
        intent = classify_intent(message)
        authority = INTENT_AUTHORITY.get(intent, Authority.PASS_THROUGH) if intent else Authority.PASS_THROUGH

        # ── Step 3: Check epistemic legality (Picard Adapter) ──
        #    Only fires for unclassified prompts under HIGH risk.
        #    EXCEPTION: if the prompt matches a heuristic pattern, route to
        #    ANALYST_HEURISTIC instead of blocking — the operator gets an
        #    educated guess (boxed, labelled) rather than total silence.
        if not intent:
            heuristic_intent = classify_heuristic_intent(message)
            legality = check_epistemic_legality(message, ep_state)
            if legality:
                if heuristic_intent:
                    # ── Heuristic rescue: prompt is "illegal" but matches a
                    #    diagnostic/educational pattern → let through as
                    #    ANALYST_HEURISTIC with strict output boxing ──
                    logger.info(
                        "[LAPT] HEURISTIC rescue: %s (was illegal: %s)",
                        heuristic_intent, legality['reason'],
                    )
                    ledger_ctx = self._build_ledger_context(ep_state, triggers)
                    return LAPTResult(
                        short_circuit=False,
                        ledger_context=ledger_ctx,
                        intent=heuristic_intent,
                        authority=Authority.ANALYST_HEURISTIC,
                        forbid_dsl=True,
                        ux_badge=UX_BADGES[Authority.ANALYST_HEURISTIC],
                    )
                else:
                    # Truly illegal — block with rewrite suggestions
                    logger.info("[LAPT] ILLEGAL prompt under exhaustion: %s",
                                legality['reason'])
                    rewrite_response = format_prompt_rewrite_response(legality)
                    return LAPTResult(
                        short_circuit=True,
                        response=rewrite_response,
                        intent=None,
                        authority=Authority.ILLEGAL_EXHAUSTED,
                        forbid_dsl=True,
                        ux_badge=UX_BADGES[Authority.ILLEGAL_EXHAUSTED],
                        prompt_rewrites=legality['suggested_prompts'],
                    )

            # ── Step 3b: Heuristic classification (non-illegal path) ──
            #    If the prompt matches a heuristic pattern and didn't match
            #    any LAPT intent, route to ANALYST_HEURISTIC.
            if heuristic_intent:
                logger.info("[LAPT] HEURISTIC intent: %s", heuristic_intent)
                ledger_ctx = self._build_ledger_context(ep_state, triggers)
                return LAPTResult(
                    short_circuit=False,
                    ledger_context=ledger_ctx,
                    intent=heuristic_intent,
                    authority=Authority.ANALYST_HEURISTIC,
                    forbid_dsl=True,
                    ux_badge=UX_BADGES[Authority.ANALYST_HEURISTIC],
                )

        # ── Step 4: Execute LAPT intent if classified ──
        if intent and intent in _EXECUTORS:
            template = TEMPLATE_REGISTRY.get(intent)
            executor = _EXECUTORS[intent]
            badge = UX_BADGES.get(authority, '')
            # DSL is forbidden for LEDGER_ONLY and MODEL_SYNTHESIS intents
            dsl_forbidden = authority in (
                Authority.LEDGER_ONLY, Authority.MODEL_SYNTHESIS,
                Authority.ILLEGAL_EXHAUSTED,
            )

            logger.info("[LAPT] intent=%s authority=%s forbid_dsl=%s",
                        intent, authority.value, dsl_forbidden)

            try:
                response = executor(self.ledger, self.engine)
            except Exception as e:
                logger.error("[LAPT] executor error for %s: %s", intent, e)
                return LAPTResult(
                    short_circuit=False,
                    ledger_context=self._build_ledger_context(ep_state, triggers),
                    intent=intent,
                    template=template,
                    authority=authority,
                    forbid_dsl=dsl_forbidden,
                    ux_badge=badge,
                )

            # Prepend UX badge
            if badge:
                response = f"{badge}\n\n{response}"

            return LAPTResult(
                short_circuit=True,
                response=response,
                intent=intent,
                template=template,
                authority=authority,
                forbid_dsl=dsl_forbidden,
                ux_badge=badge,
            )

        # ── Step 5: Silence enforcement ──
        #    If exhaustion triggers fired AND no LAPT intent matched,
        #    force silence template instead of passing to LLM.
        #    EXCEPTION 1: queries with concrete graph targets (IP, hash, MAC)
        #    are GRAPH_ONLY — they can pass through to DSL even under
        #    exhaustion, because lookups are reads, not inferences.
        #    EXCEPTION 2: heuristic intents already classified in Step 3b
        #    would have returned before reaching here — but as a safety net,
        #    re-check heuristic patterns before silencing.
        has_exhaustion_trigger = any(t.code == 'EXHAUSTION' for t in triggers)
        has_concrete_target = bool(re.search(
            r'\b(?:\d{1,3}\.){3}\d{1,3}\b'             # IPv4
            r'|(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F:]*'  # IPv6 (loose match)
            r'|(?:[0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}'  # MAC
            r'|[0-9a-fA-F]{32,64}\b',                   # Hash
            message
        ))
        if has_exhaustion_trigger and not intent and not has_concrete_target:
            # Last-chance heuristic rescue before silence
            heuristic_rescue = classify_heuristic_intent(message)
            if heuristic_rescue:
                logger.info(
                    "[LAPT] HEURISTIC rescue from silence: %s",
                    heuristic_rescue,
                )
                ledger_ctx = self._build_ledger_context(ep_state, triggers)
                return LAPTResult(
                    short_circuit=False,
                    ledger_context=ledger_ctx,
                    intent=heuristic_rescue,
                    authority=Authority.ANALYST_HEURISTIC,
                    forbid_dsl=True,
                    ux_badge=UX_BADGES[Authority.ANALYST_HEURISTIC],
                )

            logger.info("[LAPT] SILENCE enforced — exhaustion ratio %.0f%%",
                        ep_state.exhaustion_ratio * 100)
            silence_response = build_silence_template(ep_state, self.ledger)
            return LAPTResult(
                short_circuit=True,
                response=silence_response,
                intent=None,
                authority=Authority.ILLEGAL_EXHAUSTED,
                forbid_dsl=True,
                ux_badge=UX_BADGES[Authority.ILLEGAL_EXHAUSTED],
            )

        # ── Step 6: Pass through to LLM with ledger context ──
        ledger_ctx = self._build_ledger_context(ep_state, triggers)
        return LAPTResult(
            short_circuit=False,
            ledger_context=ledger_ctx,
            intent=intent,
            authority=authority,
            forbid_dsl=authority in (
                Authority.LEDGER_ONLY, Authority.MODEL_SYNTHESIS,
                Authority.ILLEGAL_EXHAUSTED, Authority.ANALYST_HEURISTIC,
            ),
        )

    def _build_ledger_context(
        self,
        ep_state: Optional[EpistemicState] = None,
        triggers: Optional[List[TriggerAlert]] = None,
    ) -> str:
        """Build a LEDGER_STATE block to inject into the LLM prompt.

        Always injected when the ledger has records, so the LLM
        never hallucinates around exhaustion state.

        Now includes epistemic state + trigger alerts.
        """
        stats = self.ledger.stats()
        if stats['total_records'] == 0 and ep_state is None:
            return ""

        exhausted = self.ledger.get_exhausted_entities()
        waiting = self.ledger.waiting_for_sensor()

        lines = ["\nLEDGER_STATE:"]
        lines.append(f"  total_records: {stats['total_records']}")
        lines.append(f"  exhausted: {stats['exhausted_count']}")
        lines.append(f"  active: {stats['active_count']}")
        lines.append(f"  by_result: {stats.get('by_result', {})}")

        # Epistemic state
        if ep_state:
            lines.append(f"  epistemic_state:")
            lines.append(f"    sensor_fraction: {ep_state.sensor_fraction:.0%}")
            lines.append(f"    inference_fraction: {ep_state.inference_fraction:.0%}")
            lines.append(f"    evidence_coverage: {ep_state.evidence_coverage:.0%}")
            lines.append(f"    stale_inferences: {ep_state.stale_inference_count}")
            lines.append(f"    hallucination_risk: {ep_state.hallucination_risk}")
            if ep_state.time_to_below_threshold_min < 1e6:
                lines.append(
                    f"    time_to_confidence_decay: "
                    f"~{ep_state.time_to_below_threshold_min:.0f}min"
                )

        # Trigger alerts
        if triggers:
            lines.append(f"  ALERTS:")
            for t in triggers:
                lines.append(f"    [{t.severity}] {t.code}: {t.message}")

        if exhausted:
            lines.append(f"  exhausted_entities:")
            for rec in exhausted[:10]:  # Cap at 10 for prompt size
                resume = rec.get('resume_condition') or {}
                lines.append(
                    f"    - {rec['entity_id']} ({rec.get('entity_kind', '?')}) "
                    f"| {rec.get('last_result', '?')} "
                    f"| resume: {resume.get('detail', '?')}"
                )
            if len(exhausted) > 10:
                lines.append(f"    ... and {len(exhausted) - 10} more")

        if waiting:
            lines.append(f"  waiting_for_sensor: {len(waiting)} entities")

        lines.append("  RULE: Do NOT recommend re-inference on exhausted entities.")
        lines.append("  RULE: Silence is correct when all targets are exhausted.")
        lines.append("  RULE: Cite LEDGER_STATE when discussing inference capability.")
        if ep_state and ep_state.hallucination_risk == 'HIGH':
            lines.append("  RULE: Hallucination risk is HIGH — avoid attribution or intent claims.")
            lines.append("  RULE: Label every claim: SENSOR, INFERRED, or UNSUPPORTED.")
            lines.append("  RULE: Do NOT fill silence with narrative.")

        return "\n".join(lines)
