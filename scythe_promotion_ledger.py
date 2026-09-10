"""When an eligible promotion may actually happen, and what is recorded.

The policy decides whether a verdict *may* become a graph record. This decides
whether one happens *now*, under a declared posture, and keeps the account of
what was decided. Two things live here that the policy deliberately has not:

**A simulated ledger for SHADOW.** Recovery learned this the expensive way. Its
shadow mode kept no simulated attempt ledger, so over a four-hour wedge it
reported 2688 would-be restarts against one incident where ARMED would have made
one and then suppressed -- an estimator measuring the amplification its own
breaker exists to prevent. The same failure is available here in a new place: a
finding re-evaluated every cycle would report one would-be promotion per
evaluation, which is the graph-confetti argument arriving through the audit
rather than through the graph.

**A budget.** Idempotency stops the *same* record being written twice. It does
nothing about a thousand *different* findings arriving at once, and a graph that
can be written to unboundedly fast is one nobody can read afterwards. The budget
is a ledger concern, not a policy one: the policy says a record is permissible,
the ledger says whether now is the time.

Nothing here writes. ARMED requires an injected writer and there is no adapter
yet, so ARMED cannot be constructed in production until one exists.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from scythe_promotion_policy import (
    NO_PROMOTION_REQUESTED, PROMOTION_ELIGIBLE, PROMOTION_REFUSED, REFUSALS,
    CapsuleIdentity, PromotionDecision, PromotionRequest, decide_promotion,
)
from scythe_invariant_ledger import InvariantVerdict


SCHEMA = "scythe.promotion-ledger.v1"

# Three postures, not a boolean. "Off" and "watching" are different, and a flag
# that conflates them cannot express the one this is meant to run in.
MODE_DISABLED = "DISABLED"
MODE_SHADOW = "SHADOW"
MODE_ARMED = "ARMED"
MODES: Tuple[str, ...] = (MODE_DISABLED, MODE_SHADOW, MODE_ARMED)
DEFAULT_MODE = MODE_SHADOW

# Promotions permitted inside a rolling window, across identities. Idempotency
# already stops one record being written twice; this is about a thousand
# different findings arriving at once.
PROMOTION_BUDGET = 8
PROMOTION_WINDOW_S = 600.0

# -- the two vocabularies (§5) --------------------------------------------
#
# SCYTHE_VERDICT_VOCABULARIES.md: a merit code says something about the subject,
# an executability code says whether a verdict could be reached or acted on. The
# sets are disjoint, named disjointly, counted separately, and neither is a
# fallback for the other. The discriminating question is *who repairs it, and
# how* -- which is why each code below carries its repair rather than a gloss.
#
# The merit set is bound by reference, not copied. A copy is a second answer to
# a question that has one, and it drifts silently in exactly the direction that
# makes the disjointness test pass while the vocabulary is wrong.
MERIT_REFUSALS: Tuple[str, ...] = REFUSALS

BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
EXECUTABILITY_REFUSALS: Tuple[str, ...] = (BUDGET_EXHAUSTED, IDENTITY_UNRESOLVED)

EXECUTABILITY_NOTES: Dict[str, str] = {
    BUDGET_EXHAUSTED: (
        "TOO MANY PROMOTIONS LATELY, ACROSS IDENTITIES. SAYS NOTHING ABOUT THIS "
        "FINDING, WHICH MAY BE ENTIRELY PROMOTABLE. REPAIRED BY WAITING OUT THE "
        "WINDOW OR BY RAISING A BOUND THAT WAS SET WRONG"),
    IDENTITY_UNRESOLVED: (
        "A RESERVATION FOR THIS IDENTITY HAS NO TERMINAL RECORD. WE DO NOT KNOW "
        "WHETHER IT REACHED THE GRAPH. NOT A DUPLICATE, WHICH WOULD BE A CLAIM "
        "THAT IT DID. REPAIRED BY RECONCILIATION, AND THE FINDING MAY BE "
        "PERFECTLY PROMOTABLE ONCE WE KNOW"),
}

# Declared here, reachable in slice 4. IDENTITY_UNRESOLVED needs a reservation
# that can *be* unresolved, and this slice does not build one.
#
# Declaring it early is what makes slice 4's dependency on this slice real
# rather than remembered. A code nothing can return is indistinguishable from a
# code that is never returned because of a bug, so the gap is pinned by a test
# that has to be changed when slice 4 lands, rather than left to reading.
NOT_YET_REACHABLE: Tuple[str, ...] = (IDENTITY_UNRESOLVED,)

# §5 names five more: DURABLE_CEILING_REACHED and UNRESOLVED_CEILING_REACHED
# (slice 8), and LEDGER_UNAVAILABLE, LEDGER_NOT_OWNED and LEDGER_TORN -- of
# which slice 5 already minted three in scythe_promotion_ledger_store.
#
# They are deliberately NOT imported. The store stays disconnected from the
# coordinator until slice 6, and an import taken for the sake of filling in a
# tuple is that connection arriving early in the one form nobody reviews. The
# tests check all three vocabularies for collisions; the module does not.

MAX_AUDIT_RECORDS = 64
MAX_TEXT = 240

# Outcomes. Shadow's are named apart from the real ones throughout, so a
# simulated ledger can never be counted as a real one.
NONE = "NONE"
WOULD_PROMOTE = "WOULD_PROMOTE"
WOULD_BE_REFUSED = "WOULD_BE_REFUSED"
WOULD_BE_SUPPRESSED = "WOULD_BE_SUPPRESSED"
PROMOTION_SUPPRESSED = "PROMOTION_SUPPRESSED"
PROMOTION_ATTEMPTED = "PROMOTION_ATTEMPTED"
PROMOTION_RECORDED = "PROMOTION_RECORDED"
PROMOTION_FAILED = "PROMOTION_FAILED"

EVENTS: Tuple[str, ...] = (
    WOULD_PROMOTE, WOULD_BE_REFUSED, WOULD_BE_SUPPRESSED,
    PROMOTION_REFUSED, PROMOTION_SUPPRESSED,
    PROMOTION_ATTEMPTED, PROMOTION_RECORDED, PROMOTION_FAILED,
)
# Events that mean a real graph record was pursued. Shadow may never write one.
ACTING_EVENTS: Tuple[str, ...] = (PROMOTION_ATTEMPTED, PROMOTION_RECORDED,
                                  PROMOTION_FAILED)
SHADOW_EVENTS: Tuple[str, ...] = (WOULD_PROMOTE, WOULD_BE_REFUSED,
                                  WOULD_BE_SUPPRESSED)


class UnknownPromotionEvent(ValueError):
    """Refused rather than recorded. A typo must not become a category."""


class PromotionLedgerError(ValueError):
    """A coordinator that cannot be constructed as asked."""


class PromotionAudit:
    """Bounded, closed vocabulary, and independent of any graph."""

    def __init__(self, maxlen: int = MAX_AUDIT_RECORDS) -> None:
        self._lock = threading.RLock()
        self._records: deque = deque(maxlen=maxlen)
        self._counts: Dict[str, int] = {}
        self._recorded = 0
        # Shadow judgements counted here and nowhere near the promoted set.
        self._shadow_decisions = 0

    def record(self, event: str, *, mode: str, reason: str,
               idempotency_key: Optional[str] = None,
               record_class: Optional[str] = None,
               monotonic_ns: Optional[int] = None,
               detail: Optional[str] = None) -> Dict[str, Any]:
        if event not in EVENTS:
            raise UnknownPromotionEvent(
                f"unknown promotion event {str(event)[:48]!r}; expected one of "
                f"{', '.join(EVENTS)}")
        if mode == MODE_SHADOW and event in ACTING_EVENTS:
            raise UnknownPromotionEvent(
                f"{event} cannot be recorded in SHADOW mode; shadow does not write")
        if mode != MODE_SHADOW and event in SHADOW_EVENTS:
            raise UnknownPromotionEvent(
                f"{event} is a shadow judgement and cannot be recorded in {mode}")
        entry = {
            "event": event, "mode": mode, "reason": str(reason)[:MAX_TEXT],
            "idempotency_key": idempotency_key, "record_class": record_class,
            "monotonic_ns": monotonic_ns,
            "detail": None if detail is None else str(detail)[:MAX_TEXT],
        }
        with self._lock:
            self._records.append(entry)
            self._counts[event] = self._counts.get(event, 0) + 1
            self._recorded += 1
            if event in SHADOW_EVENTS:
                self._shadow_decisions += 1
        return entry

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "schema": SCHEMA,
                "events": list(EVENTS),
                "capacity": self._records.maxlen,
                "recorded_total": self._recorded,
                "retained": len(self._records),
                "truncated": self._recorded > len(self._records),
                "counts": dict(self._counts),
                "shadow_decisions": self._shadow_decisions,
                "records": list(self._records),
            }


@dataclass(frozen=True)
class WriteResult:
    """What an adapter reports back. No adapter exists yet."""

    accepted: bool
    detail: str = ""


class PromotionCoordinator:
    """Drives decide -> budget -> act, under a declared posture.

    Holds two promoted-key sets that never mix: the real one, and shadow's
    simulation of it.
    """

    def __init__(self, audit: PromotionAudit, *, mode: str = DEFAULT_MODE,
                 writer: Optional[Callable[[PromotionDecision], WriteResult]] = None,
                 budget: int = PROMOTION_BUDGET,
                 window_s: float = PROMOTION_WINDOW_S) -> None:
        if mode not in MODES:
            raise PromotionLedgerError(f"unknown promotion mode {mode!r}")
        if mode == MODE_ARMED and writer is None:
            # No adapter exists yet, so this is how ARMED stays unreachable in
            # production rather than by anyone remembering not to select it.
            raise PromotionLedgerError(
                "ARMED requires a writer; no execution adapter exists yet")
        self.mode = mode
        self._audit = audit
        self._writer = writer
        self._budget = int(budget)
        self._window_ns = int(window_s * 1e9)
        self._promoted: list = []           # real (key, monotonic_ns)
        self._shadow_promoted: list = []    # simulated, never real
        # Counted per set and never summed (§5). A total would answer "how many
        # refusals" with a number mixing "we judged this not worth promoting"
        # and "we declined to act at all", which is the one distinction the two
        # vocabularies exist to keep.
        #
        # These increments are as unlocked as everything else here until slice
        # 4; status() publishes coordinator_lock: NOT_IMPLEMENTED rather than
        # letting the reader assume otherwise.
        self._merit_counts: Dict[str, int] = {}
        self._executability_counts: Dict[str, int] = {}

    # -- ledgers ----------------------------------------------------------

    @property
    def promoted_keys(self) -> Tuple[str, ...]:
        """Identities actually written. Shadow never appears here."""
        return tuple(key for key, _at in self._promoted)

    @property
    def policy_keys(self) -> Tuple[str, ...]:
        """What the policy should see as already promoted, for this mode.

        In SHADOW this is the simulated set. Without it the duplicate refusal
        never engages in shadow and the record reports one would-be promotion
        per evaluation for a single finding -- the amplification recovery's
        shadow mode produced before it kept a simulated ledger.
        """
        if self.mode == MODE_SHADOW:
            return tuple(key for key, _at in self._shadow_promoted)
        return self.promoted_keys

    def _count_merit(self, refusals: Sequence[str]) -> None:
        for refusal in refusals:
            self._merit_counts[refusal] = self._merit_counts.get(refusal, 0) + 1

    def _count_executability(self, code: str) -> None:
        self._executability_counts[code] = self._executability_counts.get(code, 0) + 1

    def _spent_in_window(self, now_ns: int) -> int:
        ledger = self._shadow_promoted if self.mode == MODE_SHADOW else self._promoted
        floor = now_ns - self._window_ns
        return sum(1 for _key, at in ledger if at >= floor)

    # -- the step ---------------------------------------------------------

    def evaluate(self, verdict: InvariantVerdict,
                 request: Optional[PromotionRequest],
                 capsule: Optional[CapsuleIdentity],
                 *, now_monotonic_ns: int) -> Dict[str, Any]:
        """One step. Pure of clocks: the instant arrives as an argument."""
        if self.mode == MODE_DISABLED:
            return {"mode": self.mode, "outcome": NONE,
                    "note": "PROMOTION EVALUATION IS DISABLED"}

        decision = decide_promotion(verdict, request, capsule,
                                    already_promoted=self.policy_keys)
        if decision.disposition == NO_PROMOTION_REQUESTED:
            # Nothing was asked. Recording that would fill the audit with the
            # absence of events.
            return {"mode": self.mode, "outcome": NONE,
                    "disposition": decision.disposition}

        if decision.disposition == PROMOTION_REFUSED:
            # Merit. The policy judged the finding; nothing about the apparatus
            # is being reported here.
            self._count_merit(decision.refusals)
            event = WOULD_BE_REFUSED if self.mode == MODE_SHADOW else PROMOTION_REFUSED
            self._audit.record(event, mode=self.mode,
                               reason=",".join(decision.refusals),
                               monotonic_ns=now_monotonic_ns)
            return {"mode": self.mode, "outcome": event,
                    "disposition": decision.disposition,
                    "refusals": list(decision.refusals),
                    "merit_refusals": list(decision.refusals)}

        if self._spent_in_window(now_monotonic_ns) >= self._budget:
            # Executability. This finding may be entirely promotable; we
            # declined to act, and the refusal says so.
            self._count_executability(BUDGET_EXHAUSTED)
            event = (WOULD_BE_SUPPRESSED if self.mode == MODE_SHADOW
                     else PROMOTION_SUPPRESSED)
            self._audit.record(event, mode=self.mode, reason=BUDGET_EXHAUSTED,
                               idempotency_key=decision.idempotency_key,
                               record_class=decision.record_class,
                               monotonic_ns=now_monotonic_ns)
            return {"mode": self.mode, "outcome": event,
                    "reason": BUDGET_EXHAUSTED,
                    "executability_code": BUDGET_EXHAUSTED,
                    "idempotency_key": decision.idempotency_key}

        if self.mode == MODE_SHADOW:
            # Spend the simulated budget so the next evaluation sees the
            # duplicate refusal and the window the way ARMED would. Nothing
            # here touches the real ledger.
            self._shadow_promoted.append(
                (decision.idempotency_key, now_monotonic_ns))
            self._audit.record(WOULD_PROMOTE, mode=self.mode,
                               reason="ELIGIBLE",
                               idempotency_key=decision.idempotency_key,
                               record_class=decision.record_class,
                               monotonic_ns=now_monotonic_ns)
            return {"mode": self.mode, "outcome": WOULD_PROMOTE,
                    "record_class": decision.record_class,
                    "idempotency_key": decision.idempotency_key}

        return self._act(decision, now_monotonic_ns)

    def _act(self, decision: PromotionDecision, now_ns: int) -> Dict[str, Any]:
        # Reserved BEFORE the write, and the reservation is kept whatever the
        # writer reports -- PROMOTION_FAILED consumes the identity too.
        #
        # A crash between the two leaves a reservation whose record was never
        # written: one finding is lost, and nothing duplicate reaches the
        # graph. The reverse ordering trades that for the opposite hazard, a
        # record written and never counted, which the next evaluation is then
        # free to write again. A lost finding is recoverable by re-running the
        # check against the same evidence; a duplicate already in GraphOps is
        # not, because nothing downstream can tell it from a second real one.
        self._promoted.append((decision.idempotency_key, now_ns))
        self._audit.record(PROMOTION_ATTEMPTED, mode=self.mode, reason="ELIGIBLE",
                           idempotency_key=decision.idempotency_key,
                           record_class=decision.record_class,
                           monotonic_ns=now_ns)
        result = self._writer(decision)
        event = PROMOTION_RECORDED if result.accepted else PROMOTION_FAILED
        self._audit.record(event, mode=self.mode, reason="ELIGIBLE",
                           idempotency_key=decision.idempotency_key,
                           record_class=decision.record_class,
                           monotonic_ns=now_ns, detail=result.detail)
        return {"mode": self.mode, "outcome": event,
                "record_class": decision.record_class,
                "idempotency_key": decision.idempotency_key,
                "accepted_note": (
                    "A WRITER'S ACCEPTANCE IS A RECORD CREATED, NOT A FINDING "
                    "CONFIRMED. THE VERDICT WAS ALREADY TRUE OR FALSE BEFORE "
                    "ANYTHING WAS WRITTEN")}

    def status(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "mode": self.mode,
            "modes": list(MODES),
            "default_mode": DEFAULT_MODE,
            "budget": self._budget,
            "window_s": self._window_ns / 1e9,
            "budget_note": (
                "IDEMPOTENCY STOPS ONE RECORD BEING WRITTEN TWICE. THE BUDGET "
                "IS ABOUT A THOUSAND DIFFERENT FINDINGS ARRIVING AT ONCE"),
            "promoted": len(self._promoted),
            "shadow_promoted": len(self._shadow_promoted),
            "merit_vocabulary": list(MERIT_REFUSALS),
            "executability_vocabulary": list(EXECUTABILITY_REFUSALS),
            "executability_notes": dict(EXECUTABILITY_NOTES),
            "merit_refusals": dict(sorted(self._merit_counts.items())),
            "executability_refusals": dict(sorted(self._executability_counts.items())),
            "refusal_counts_note": (
                "COUNTED PER SET AND NEVER SUMMED. A TOTAL WOULD MIX 'WE JUDGED "
                "THIS NOT WORTH PROMOTING' WITH 'WE DECLINED TO ACT AT ALL', "
                "WHICH IS THE DISTINCTION THE TWO VOCABULARIES EXIST TO KEEP"),
            "executability_not_yet_reachable": list(NOT_YET_REACHABLE),
            "coordinator_lock": "NOT_IMPLEMENTED",
            "shadow_simulates_idempotency": True,
            "shadow_note": (
                "SHADOW SPENDS A SIMULATED LEDGER SO ITS RECORD SHOWS WHAT "
                "ARMED WOULD DO. WITHOUT ONE IT REPORTS ONE WOULD-BE PROMOTION "
                "PER EVALUATION FOR A SINGLE FINDING"),
            "writer": "NOT_IMPLEMENTED" if self._writer is None else "INJECTED",
            "execution_adapter": "NOT_IMPLEMENTED",
            "armed_requires_writer": True,
        }
