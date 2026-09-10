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
from dataclasses import dataclass, field
import threading
from typing import Any, Callable, Deque, Dict, Optional, Sequence, Tuple

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

# Minted by slice 4, and §5 does not list it. Amendment B created the state that
# needs it: once NOT_CREATED means the adapter *definitely* attests that no
# record was created, DUPLICATE_PROMOTION becomes a false claim about that
# identity -- it says the finding is already in the graph, and it is not.
#
# The refusal is about the apparatus, not the finding: §2 says re-promotion
# after a failure is an operator action and never an automatic retry, and until
# reconciliation exists (slice 7) there is no operator action to take. The name
# states the repair, which is the question the two vocabularies are told apart
# by. PENDING_AMENDMENTS.md entry 9 carries the obligation to record it in §5.
RETRY_REQUIRES_OPERATOR = "RETRY_REQUIRES_OPERATOR"

EXECUTABILITY_REFUSALS: Tuple[str, ...] = (BUDGET_EXHAUSTED, IDENTITY_UNRESOLVED,
                                           RETRY_REQUIRES_OPERATOR)

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
    RETRY_REQUIRES_OPERATOR: (
        "THE ADAPTER ATTESTED THAT NO RECORD WAS CREATED. NOT A DUPLICATE -- "
        "NOTHING IS IN THE GRAPH -- AND NOT UNRESOLVED, BECAUSE WE KNOW. "
        "REPAIRED BY AN OPERATOR RE-PROMOTING IT DELIBERATELY; AN AUTOMATIC "
        "RETRY HERE IS A LOOP BOUNDED ONLY BY THE BUDGET"),
}

# Empty since slice 4. IDENTITY_UNRESOLVED was declared in slice 3 and became
# reachable here, which is the transition this tuple existed to make visible: a
# code nothing can return is indistinguishable from a code that is never
# returned because of a bug.
NOT_YET_REACHABLE: Tuple[str, ...] = ()

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
PROMOTION_OUTCOME_UNRESOLVED = "PROMOTION_OUTCOME_UNRESOLVED"

EVENTS: Tuple[str, ...] = (
    WOULD_PROMOTE, WOULD_BE_REFUSED, WOULD_BE_SUPPRESSED,
    PROMOTION_REFUSED, PROMOTION_SUPPRESSED,
    PROMOTION_ATTEMPTED, PROMOTION_RECORDED, PROMOTION_FAILED,
    PROMOTION_OUTCOME_UNRESOLVED,
)
# Events that mean a real graph record was pursued. Shadow may never write one.
ACTING_EVENTS: Tuple[str, ...] = (PROMOTION_ATTEMPTED, PROMOTION_RECORDED,
                                  PROMOTION_FAILED, PROMOTION_OUTCOME_UNRESOLVED)
SHADOW_EVENTS: Tuple[str, ...] = (WOULD_PROMOTE, WOULD_BE_REFUSED,
                                  WOULD_BE_SUPPRESSED)


# -- what the adapter attests (§13a B.1) ----------------------------------
CREATED = "CREATED"
NOT_CREATED = "NOT_CREATED"
UNKNOWN = "UNKNOWN"
WRITE_OUTCOMES: Tuple[str, ...] = (CREATED, NOT_CREATED, UNKNOWN)

# -- what the coordinator remembers about an identity (§13a B.6) ----------
#
# Exactly three. RESERVED with no terminal record *is* unresolved; a stored
# UNRESOLVED beside it would be a second name for one fence, with no observable
# transition between them. The same three names the durable ledger's record
# kinds use, because they are the same three states.
RESERVED = "RESERVED"
COMMITTED = "COMMITTED"
FAILED = "FAILED"
IDENTITY_STATES: Tuple[str, ...] = (RESERVED, COMMITTED, FAILED)


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
    """What an adapter reports back. No adapter exists yet.

    Three states, not a Boolean (§13a B.1). A Boolean had to carry
    CREATED / NOT_CREATED / UNKNOWN and has two values, so the old
    ``accepted=False`` collapsed *the adapter definitely rejected this* into *we
    never found out* -- discarding the one piece of knowledge the boundary
    actually has, at the only place that has it.

    NOT_CREATED requires a definite attestation. Timeout, lost acknowledgement,
    transport interruption, cancellation and any ambiguous negative answer are
    UNKNOWN. **An adapter that is unsure reports UNKNOWN**, and the direction of
    that default is the whole safety property: an author who inverts it produces
    duplicates, silently.
    """

    outcome: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in WRITE_OUTCOMES:
            raise PromotionLedgerError(
                f"unknown write outcome {str(self.outcome)[:48]!r}; expected one "
                f"of {', '.join(WRITE_OUTCOMES)}. A bool is not one of them")


@dataclass
class _Posture:
    """One posture's books: §6's two structures, kept visibly two (§13a B.6).

    The identity map answers *may this identity be promoted*, has no clock, and
    becomes durable in slice 6. The window answers *have too many been promoted
    lately*, is anchored on a monotonic clock, and is never durable -- a
    persisted monotonic_ns from a previous boot is not stale, it is meaningless.

    Named for the posture rather than for either structure, because the
    alternative is a mode ternary at five call sites and that is where such a
    bug hides.

    **Every method here assumes the coordinator's lock is held.** Nothing in
    this class takes one; giving the object a second lock would make the order
    between them a question nobody asked.
    """

    identities: Dict[str, str] = field(default_factory=dict)
    window: Deque[int] = field(default_factory=deque)

    def reserve(self, identity: str, now_ns: int) -> None:
        self.identities[identity] = RESERVED
        self.window.append(now_ns)

    def resolve(self, identity: str, state: str) -> None:
        self.identities[identity] = state

    def spent_in_window(self, now_ns: int, window_ns: int) -> int:
        """Prunes as it counts.

        The deque is in order because it is appended to under the lock with a
        caller-supplied instant, so the left end is always the oldest and
        pruning never scans the middle. The old list was an O(n) scan over an
        unbounded structure, inside the critical section: §6's split shortens
        the section rather than lengthening it.
        """
        floor = now_ns - window_ns
        while self.window and self.window[0] < floor:
            self.window.popleft()
        return len(self.window)

    def in_state(self, state: str) -> Tuple[str, ...]:
        return tuple(i for i, s in self.identities.items() if s == state)


class PromotionCoordinator:
    """Drives decide -> budget -> reserve -> write -> resolve, under a posture.

    Holds two postures that never mix: the real one, and shadow's simulation of
    it. One plain lock covers the decision, the budget check and the
    reservation; the writer is called with it released.
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
        # A plain Lock, never an RLock (§4). Nothing inside the critical
        # section may call out, so re-entry is a bug that should be impossible
        # by construction -- and a plain lock makes it a deadlock rather than a
        # wrong answer arriving under the interleaving hardest to reproduce.
        self._lock = threading.Lock()
        self._real = _Posture()
        self._shadow = _Posture()
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
    #
    # Public accessors acquire; the critical section uses the _unlocked forms
    # only (§13a B.7). This is §4's *nothing inside the critical section may
    # call out*, applied to the object's own surface -- which is the direction a
    # later refactor reintroduces it from, because calling your own property
    # does not look like calling out. With a plain Lock the mistake is a
    # deadlock rather than a wrong answer.

    def _posture_unlocked(self) -> _Posture:
        return self._shadow if self.mode == MODE_SHADOW else self._real

    def _policy_keys_unlocked(self) -> Tuple[str, ...]:
        return self._posture_unlocked().in_state(COMMITTED)

    @property
    def promoted_keys(self) -> Tuple[str, ...]:
        """Identities actually written. Shadow never appears here."""
        with self._lock:
            return self._real.in_state(COMMITTED)

    @property
    def policy_keys(self) -> Tuple[str, ...]:
        """What the policy should see as already promoted, for this mode.

        **Committed only.** A reserved identity is fenced by this coordinator
        and not by the policy: handing it over would produce DUPLICATE_PROMOTION,
        which claims the finding is already in the graph when the whole point of
        the state is that we do not know (§13a B.4). A failed one would get the
        same false claim for the opposite reason -- we know it is not there.

        In SHADOW this is the simulated set. Without it the duplicate refusal
        never engages in shadow and the record reports one would-be promotion
        per evaluation for a single finding -- the amplification recovery's
        shadow mode produced before it kept a simulated ledger.
        """
        with self._lock:
            return self._policy_keys_unlocked()

    @property
    def unresolved_keys(self) -> Tuple[str, ...]:
        """Reserved with no terminal record. Read from the identity map and
        never from the audit ring, which is bounded and drops them (§13a B.5)."""
        with self._lock:
            return self._real.in_state(RESERVED)

    @property
    def write_failed_keys(self) -> Tuple[str, ...]:
        with self._lock:
            return self._real.in_state(FAILED)

    @property
    def fenced_keys(self) -> Tuple[str, ...]:
        """Every identity this coordinator refuses. All three states fence, for
        three different reasons (§13a B.3)."""
        with self._lock:
            return tuple(sorted(self._real.identities))

    def _count_merit(self, refusals: Sequence[str]) -> None:
        for refusal in refusals:
            self._merit_counts[refusal] = self._merit_counts.get(refusal, 0) + 1

    def _count_executability(self, code: str) -> None:
        self._executability_counts[code] = self._executability_counts.get(code, 0) + 1

    # -- the step ---------------------------------------------------------
    #
    #   +-- coordinator lock held ---------------------------------------+
    #   |  1. snapshot the identity set                                  |
    #   |  2. decide_promotion(..., already_promoted=committed)          |
    #   |  3. executability: unresolved, failed, budget                  |
    #   |  4. reserve the identity                                       |
    #   |  5. spend the window                                           |
    #   |  6. audit PROMOTION_ATTEMPTED                                  |
    #   +-- release -----------------------------------------------------+
    #      7. call the writer                     <- outside the lock
    #      8. reacquire, resolve to COMMITTED or FAILED
    #      9. audit the terminal event
    #
    # 1-3 must be atomic with 4-5 or the checks decide against state that has
    # already moved. Two races close together and not one: two evaluations of
    # the *same* identity both passing step 2, and evaluations of *distinct*
    # identities all passing step 3 against a nearly-spent budget.
    #
    # CPython does not help. list.append and dict assignment are individually
    # atomic under the GIL, so nothing here corrupts -- what is unprotected is
    # read-decide-write, and two threads both reading before either writes is
    # exactly the interleaving the GIL permits.

    def evaluate(self, verdict: InvariantVerdict,
                 request: Optional[PromotionRequest],
                 capsule: Optional[CapsuleIdentity],
                 *, now_monotonic_ns: int) -> Dict[str, Any]:
        """One step. Pure of clocks: the instant arrives as an argument."""
        if self.mode == MODE_DISABLED:
            return {"mode": self.mode, "outcome": NONE,
                    "note": "PROMOTION EVALUATION IS DISABLED"}

        with self._lock:
            result, decision = self._reserve_unlocked(
                verdict, request, capsule, now_monotonic_ns)
        if decision is None:
            return result

        # Step 7, with the lock released. The writer is unbounded external I/O:
        # holding across it would serialize every evaluation behind the slowest
        # bus call, and a writer that re-entered the coordinator would deadlock
        # while holding a half-made reservation.
        outcome, exception_type, detail = self._call_writer(decision)

        with self._lock:
            return self._resolve_unlocked(decision, outcome, exception_type,
                                          detail, now_monotonic_ns)

    # -- inside the lock --------------------------------------------------

    def _reserve_unlocked(self, verdict: InvariantVerdict,
                          request: Optional[PromotionRequest],
                          capsule: Optional[CapsuleIdentity], now_ns: int
                          ) -> Tuple[Dict[str, Any], Optional[PromotionDecision]]:
        """Steps 1-6. Returns (result, decision).

        A decision means a reservation was made and the writer must be called;
        None means the step is already finished.
        """
        posture = self._posture_unlocked()

        decision = decide_promotion(verdict, request, capsule,
                                    already_promoted=self._policy_keys_unlocked())
        if decision.disposition == NO_PROMOTION_REQUESTED:
            # Nothing was asked. Recording that would fill the audit with the
            # absence of events.
            return ({"mode": self.mode, "outcome": NONE,
                     "disposition": decision.disposition}, None)

        if decision.disposition == PROMOTION_REFUSED:
            # Merit. The policy judged the finding; nothing about the apparatus
            # is being reported here.
            self._count_merit(decision.refusals)
            event = WOULD_BE_REFUSED if self.mode == MODE_SHADOW else PROMOTION_REFUSED
            self._audit.record(event, mode=self.mode,
                               reason=",".join(decision.refusals),
                               monotonic_ns=now_ns)
            return ({"mode": self.mode, "outcome": event,
                     "disposition": decision.disposition,
                     "refusals": list(decision.refusals),
                     "merit_refusals": list(decision.refusals)}, None)

        # The two states the policy was deliberately not shown. Both fence, and
        # neither is a duplicate -- for opposite reasons.
        state = posture.identities.get(decision.idempotency_key)
        if state == RESERVED:
            return (self._refuse_unlocked(IDENTITY_UNRESOLVED, decision, now_ns),
                    None)
        if state == FAILED:
            return (self._refuse_unlocked(RETRY_REQUIRES_OPERATOR, decision, now_ns),
                    None)

        if posture.spent_in_window(now_ns, self._window_ns) >= self._budget:
            # This finding may be entirely promotable; we declined to act.
            return (self._refuse_unlocked(BUDGET_EXHAUSTED, decision, now_ns), None)

        if self.mode == MODE_SHADOW:
            # Spend the simulated budget so the next evaluation sees the
            # duplicate refusal and the window the way ARMED would.
            #
            # Recorded COMMITTED rather than left RESERVED because shadow has no
            # writer to be uncertain about: leaving it reserved would simulate
            # an adapter failure that did not happen, and every later evaluation
            # of that identity would report IDENTITY_UNRESOLVED for a write
            # nobody attempted. Nothing here touches the real posture.
            posture.reserve(decision.idempotency_key, now_ns)
            posture.resolve(decision.idempotency_key, COMMITTED)
            self._audit.record(WOULD_PROMOTE, mode=self.mode, reason="ELIGIBLE",
                               idempotency_key=decision.idempotency_key,
                               record_class=decision.record_class,
                               monotonic_ns=now_ns)
            return ({"mode": self.mode, "outcome": WOULD_PROMOTE,
                     "record_class": decision.record_class,
                     "idempotency_key": decision.idempotency_key}, None)

        # Reserved BEFORE the write, and kept whatever the writer reports.
        #
        # A crash between the two leaves a reservation whose record was never
        # written: one finding is lost, and nothing duplicate reaches the graph.
        # The reverse ordering trades that for the opposite hazard, a record
        # written and never counted, which the next evaluation is then free to
        # write again. A lost finding is recoverable by re-running the check
        # against the same evidence; a duplicate already in GraphOps is not,
        # because nothing downstream can tell it from a second real one.
        posture.reserve(decision.idempotency_key, now_ns)
        self._audit.record(PROMOTION_ATTEMPTED, mode=self.mode, reason="ELIGIBLE",
                           idempotency_key=decision.idempotency_key,
                           record_class=decision.record_class,
                           monotonic_ns=now_ns)
        return ({}, decision)

    def _refuse_unlocked(self, code: str, decision: PromotionDecision,
                         now_ns: int) -> Dict[str, Any]:
        """An executability refusal: we declined to act, and the finding may be
        entirely promotable."""
        self._count_executability(code)
        event = (WOULD_BE_SUPPRESSED if self.mode == MODE_SHADOW
                 else PROMOTION_SUPPRESSED)
        self._audit.record(event, mode=self.mode, reason=code,
                           idempotency_key=decision.idempotency_key,
                           record_class=decision.record_class,
                           monotonic_ns=now_ns)
        return {"mode": self.mode, "outcome": event, "reason": code,
                "executability_code": code,
                "idempotency_key": decision.idempotency_key}

    def _resolve_unlocked(self, decision: PromotionDecision, outcome: str,
                          exception_type: Optional[str], detail: str,
                          now_ns: int) -> Dict[str, Any]:
        """Step 8, with the lock reacquired.

        The window is deliberately not touched: it was spent at reservation, and
        spending it again would measure the adapter's latency as promotion rate.
        """
        if outcome == UNKNOWN:
            # No terminal record. The identity stays RESERVED, which *is* the
            # in-memory representation of unresolved -- the write may have
            # landed and may not have, and a FAILED here would be a claim the
            # apparatus cannot support.
            self._count_executability(IDENTITY_UNRESOLVED)
            self._audit.record(PROMOTION_OUTCOME_UNRESOLVED, mode=self.mode,
                               reason=IDENTITY_UNRESOLVED,
                               idempotency_key=decision.idempotency_key,
                               record_class=decision.record_class,
                               monotonic_ns=now_ns,
                               detail=exception_type or UNKNOWN)
            return {"mode": self.mode, "outcome": PROMOTION_OUTCOME_UNRESOLVED,
                    "executability_code": IDENTITY_UNRESOLVED,
                    "record_class": decision.record_class,
                    "idempotency_key": decision.idempotency_key,
                    # Class name only. Adapter exception text is arbitrary and
                    # carries endpoints, payload fragments and credentials; the
                    # class is the part that describes the apparatus. Discarded
                    # rather than truncated, because truncation is a length
                    # policy applied to content that should not be here at all.
                    "detail": ({"exception_type": exception_type}
                               if exception_type else {"write_outcome": UNKNOWN})}

        self._real.resolve(decision.idempotency_key,
                           COMMITTED if outcome == CREATED else FAILED)
        event = PROMOTION_RECORDED if outcome == CREATED else PROMOTION_FAILED
        self._audit.record(event, mode=self.mode, reason="ELIGIBLE",
                           idempotency_key=decision.idempotency_key,
                           record_class=decision.record_class,
                           monotonic_ns=now_ns, detail=detail)
        return {"mode": self.mode, "outcome": event,
                "write_outcome": outcome,
                "record_class": decision.record_class,
                "idempotency_key": decision.idempotency_key,
                "accepted_note": (
                    "A WRITER'S ACCEPTANCE IS A RECORD CREATED, NOT A FINDING "
                    "CONFIRMED. THE VERDICT WAS ALREADY TRUE OR FALSE BEFORE "
                    "ANYTHING WAS WRITTEN")}

    # -- outside the lock -------------------------------------------------

    def _call_writer(self, decision: PromotionDecision
                     ) -> Tuple[str, Optional[str], str]:
        """Step 7. Returns (outcome, exception class name or None, detail).

        An exception is UNKNOWN and not a failure: a socket that raised on read
        may have delivered its request. It is caught rather than propagated
        because ``evaluate()`` raising hands the caller no idempotency key, and
        the key is the only handle on a reservation that now exists and fences
        an identity.

        BaseException is deliberately not caught. A KeyboardInterrupt or a
        SystemExit here leaves the reservation in place, which is the fencing
        this contract requires; swallowing an interrupt to record a terminal
        event that was never going to be written is the worse trade.
        """
        try:
            result = self._writer(decision)
        except Exception as exc:            # noqa: BLE001 -- see the docstring
            return UNKNOWN, type(exc).__name__, ""
        if not isinstance(result, WriteResult):
            # An adapter that returned something else has told us nothing, and
            # nothing is UNKNOWN. Never NOT_CREATED, which would be a claim.
            return UNKNOWN, type(result).__name__, ""
        return result.outcome, None, result.detail

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return self._status_unlocked()

    def _status_unlocked(self) -> Dict[str, Any]:
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
            "promoted": len(self._real.in_state(COMMITTED)),
            "unresolved": len(self._real.in_state(RESERVED)),
            "write_failed": len(self._real.in_state(FAILED)),
            "fenced": len(self._real.identities),
            "identity_states": list(IDENTITY_STATES),
            "unresolved_keys": sorted(self._real.in_state(RESERVED)),
            "unresolved_source": (
                "THE IDENTITY MAP, NEVER THE AUDIT RING. THE RING IS BOUNDED BY "
                "DESIGN AND DROPS AN UNRESOLVED RESERVATION WHILE THE "
                "RESERVATION ITSELF PERSISTS"),
            "shadow_promoted": len(self._shadow.in_state(COMMITTED)),
            "budget_window_survives_restart": False,
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
            "write_outcomes": list(WRITE_OUTCOMES),
            "coordinator_lock": "PLAIN_LOCK",
            "lock_note": (
                "DECISION, BUDGET AND RESERVATION ARE ATOMIC. THE WRITER IS "
                "CALLED WITH THE LOCK RELEASED, AND THE TERMINAL UPDATE "
                "REACQUIRES IT"),
            "durable_ledger": "NOT_IMPLEMENTED",
            "shadow_simulates_idempotency": True,
            "shadow_note": (
                "SHADOW SPENDS A SIMULATED LEDGER SO ITS RECORD SHOWS WHAT "
                "ARMED WOULD DO. WITHOUT ONE IT REPORTS ONE WOULD-BE PROMOTION "
                "PER EVALUATION FOR A SINGLE FINDING"),
            "writer": "NOT_IMPLEMENTED" if self._writer is None else "INJECTED",
            "execution_adapter": "NOT_IMPLEMENTED",
            "armed_requires_writer": True,
        }
