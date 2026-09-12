"""The execution boundary: one reservation, one idempotent GraphOps mutation.

§17 slice 9, implementing PROMOTION_EXECUTION_CONTRACT.md §13g Amendment H.

    GraphOps success is a claim requiring evidence.
    GraphOps failure is not proof that nothing happened.

Everything here follows from those being different shapes. The first needs a
validated receipt; the second needs positive evidence that nothing was
submitted, and *looking like* nothing happened is not that evidence.

Three things this module is built around:

  **Submission state is monotonic and observed, never inferred.**
  NOT_STARTED -> SUBMISSION_BEGAN -> RESPONSE_RECEIVED. Only an explicit
  NOT_STARTED from a transport that attests the boundary can support
  NOT_CREATED. A transport that does not instrument the boundary cannot infer
  innocence from an empty response, so its silence is UNKNOWN.

  **Classification reads the validated receipt and the transport state, and
  nothing else.** Not an HTTP status, not an exception type. A tidy 200 with the
  wrong operation identity is UNKNOWN; an ugly rejection is NOT_CREATED only
  when the conformance contract proves it mutation-free.

  **One reservation addresses one operation identity, forever.** No fresh
  identity on retry, and no second call after UNKNOWN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Callable, Dict, Optional, Tuple

SCHEMA = "scythe.graphops-adapter.v1"
COMMAND_SCHEMA = "scythe.graphops-promotion-command.v1"

# -- the three outcomes (§13a B.1) ----------------------------------------
CREATED = "CREATED"
NOT_CREATED = "NOT_CREATED"
UNKNOWN = "UNKNOWN"
OUTCOMES: Tuple[str, ...] = (CREATED, NOT_CREATED, UNKNOWN)

# -- transport submission state, monotonic --------------------------------
NOT_STARTED = "NOT_STARTED"
SUBMISSION_BEGAN = "SUBMISSION_BEGAN"
RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
SUBMISSION_STATES: Tuple[str, ...] = (NOT_STARTED, SUBMISSION_BEGAN,
                                      RESPONSE_RECEIVED)

# -- evidence: what the outcome was concluded *from* ----------------------
CREATION_RECEIPT_VALID = "CREATION_RECEIPT_VALID"
IDEMPOTENT_MATCH = "IDEMPOTENT_MATCH"
IDEMPOTENT_DIGEST_CONFLICT = "IDEMPOTENT_DIGEST_CONFLICT"
LOCAL_VALIDATION_REFUSED = "LOCAL_VALIDATION_REFUSED"
SUBMISSION_NEVER_BEGAN = "SUBMISSION_NEVER_BEGAN"
MUTATION_FREE_REJECTION = "MUTATION_FREE_REJECTION"
AUTHORITATIVE_ABSENCE = "AUTHORITATIVE_ABSENCE"
SUBMISSION_BOUNDARY_CROSSED = "SUBMISSION_BOUNDARY_CROSSED"
RECEIPT_MALFORMED = "RECEIPT_MALFORMED"
RECEIPT_UNAUTHENTICATED = "RECEIPT_UNAUTHENTICATED"
RECEIPT_IDENTITY_MISMATCH = "RECEIPT_IDENTITY_MISMATCH"
LOOKUP_INCONCLUSIVE = "LOOKUP_INCONCLUSIVE"
TRANSPORT_INTERRUPTED = "TRANSPORT_INTERRUPTED"
EVIDENCE_CODES: Tuple[str, ...] = (
    CREATION_RECEIPT_VALID, IDEMPOTENT_MATCH, IDEMPOTENT_DIGEST_CONFLICT,
    LOCAL_VALIDATION_REFUSED, SUBMISSION_NEVER_BEGAN, MUTATION_FREE_REJECTION,
    AUTHORITATIVE_ABSENCE, SUBMISSION_BOUNDARY_CROSSED, RECEIPT_MALFORMED,
    RECEIPT_UNAUTHENTICATED, RECEIPT_IDENTITY_MISMATCH, LOOKUP_INCONCLUSIVE,
    TRANSPORT_INTERRUPTED,
)

# Executability refusal (§5).
ADAPTER_NOT_CONFORMANT = "ADAPTER_NOT_CONFORMANT"
ADAPTER_HALTED = "ADAPTER_HALTED"

STRONG = "STRONG"
WEAK = "WEAK"


class AdapterError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# -- identity and encoding (§13g H.1, H.2) --------------------------------

def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def payload_digest(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()


def operation_id(*, schema_version: str, lineage_id: str, generation_id: str,
                 reservation_seq: int, subject_identity: str,
                 canonical_payload_digest: str) -> str:
    """Derived from immutable ledger facts, and never re-minted.

    SHA-256 rather than blake2s, which everything internal here uses. This one
    crosses a boundary and must be computable by a system that did not choose
    our hash (§13g H.1) -- recorded so a tidy-up for consistency finds the
    reason before the edit.

    The parts are length-prefixed. Concatenating them raw would let two
    different fact sets produce one identity by moving a character across a
    boundary, which is the collision the whole fence is built to make
    impossible.
    """
    parts = (schema_version, lineage_id, generation_id, str(int(reservation_seq)),
             subject_identity, canonical_payload_digest)
    material = b"".join(
        f"{len(part.encode('utf-8'))}:".encode("ascii") + part.encode("utf-8")
        for part in parts)
    return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class ReservationFacts:
    """The immutable ledger facts one reservation yields."""

    lineage_id: str
    generation_id: str
    reservation_seq: int
    subject_identity: str
    payload: Any

    def digest(self) -> str:
        return payload_digest(self.payload)

    def operation_id(self) -> str:
        return operation_id(
            schema_version=COMMAND_SCHEMA, lineage_id=self.lineage_id,
            generation_id=self.generation_id,
            reservation_seq=self.reservation_seq,
            subject_identity=self.subject_identity,
            canonical_payload_digest=self.digest())


def encode_command(facts: ReservationFacts, configuration_identity: str) -> bytes:
    """One closed, versioned structure, deterministically serialized.

    Only fields GraphOps has agreed to accept. No credentials, no headers, no
    URLs: those belong to the transport and never to the record of what was
    sent.
    """
    return _canonical({
        "schema": COMMAND_SCHEMA,
        "promotion_operation_id": facts.operation_id(),
        "lineage_id": facts.lineage_id,
        "generation_id": facts.generation_id,
        "reservation_seq": facts.reservation_seq,
        "subject_identity": facts.subject_identity,
        "payload": facts.payload,
        "payload_digest": facts.digest(),
        "configuration_identity": configuration_identity,
    })


# -- the conformance declaration (§13g H.7) -------------------------------

@dataclass(frozen=True)
class GraphOpsConformance:
    """What GraphOps has been declared to guarantee. An endpoint is not this.

    Hashed into the adapter's configuration identity, so a changed GraphOps
    contract requires a new reviewed identity rather than a quiet edit -- the
    rule §13f G.4 applies to the ceilings, at a boundary where the other party
    can change without telling us.
    """

    version: str
    atomic_idempotency: bool
    stable_operation_lookup: bool
    receipt_authentication: bool
    payload_digest_echo: bool
    consistency: str
    mutation_free_rejection_codes: Tuple[str, ...]
    max_request_bytes: int
    max_response_bytes: int
    timeout_behaviour: str
    cancellation_behaviour: str

    def refusals(self) -> Tuple[str, ...]:
        """Why ARMED is not supportable, or empty.

        Atomic idempotency first: without it, *this reservation twice* and *two
        reservations* are indistinguishable at the far end, and the fence stops
        at our side of the wire.
        """
        missing = []
        if not self.atomic_idempotency:
            missing.append("atomic_idempotency")
        if not self.stable_operation_lookup:
            missing.append("stable_operation_lookup")
        if not self.receipt_authentication:
            missing.append("receipt_authentication")
        if not self.payload_digest_echo:
            missing.append("payload_digest_echo")
        if self.consistency not in (STRONG, WEAK):
            missing.append("consistency")
        for numeric in ("max_request_bytes", "max_response_bytes"):
            if not isinstance(getattr(self, numeric), int) \
                    or getattr(self, numeric) < 1:
                missing.append(numeric)
        for text in ("version", "timeout_behaviour", "cancellation_behaviour"):
            if not isinstance(getattr(self, text), str) or not getattr(self, text):
                missing.append(text)
        return tuple(missing)

    @property
    def conformant(self) -> bool:
        return not self.refusals()

    def identity(self) -> str:
        material = _canonical({
            "version": self.version,
            "atomic_idempotency": self.atomic_idempotency,
            "stable_operation_lookup": self.stable_operation_lookup,
            "receipt_authentication": self.receipt_authentication,
            "payload_digest_echo": self.payload_digest_echo,
            "consistency": self.consistency,
            "mutation_free_rejection_codes": list(self.mutation_free_rejection_codes),
            "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
            "timeout_behaviour": self.timeout_behaviour,
            "cancellation_behaviour": self.cancellation_behaviour,
        })
        return "sha256:" + hashlib.sha256(material).hexdigest()


# -- the transport seam (§13g H.4) ----------------------------------------

class SubmissionState:
    """Monotonic, and it only moves forward.

    The one question the classifier cannot answer for itself: did any request
    byte leave. A transport that knows must say so; a transport that does not
    know must not be read as saying no.
    """

    __slots__ = ("_state",)

    def __init__(self) -> None:
        self._state = NOT_STARTED

    @property
    def state(self) -> str:
        return self._state

    def began(self) -> None:
        if self._state == RESPONSE_RECEIVED:
            raise AdapterError(TRANSPORT_INTERRUPTED,
                               "submission cannot begin after a response")
        self._state = SUBMISSION_BEGAN

    def received(self) -> None:
        if self._state == NOT_STARTED:
            raise AdapterError(TRANSPORT_INTERRUPTED,
                               "a response cannot arrive before submission")
        self._state = RESPONSE_RECEIVED

    @property
    def crossed(self) -> bool:
        return self._state != NOT_STARTED


class Transport:
    """What the adapter is handed. Real ones do I/O; this one refuses.

    `attests_submission_boundary` is the declaration that NOT_STARTED means
    *nothing left this process* rather than *nothing was recorded*. A transport
    that does not set it cannot support NOT_CREATED, however innocent its
    failure looks.
    """

    attests_submission_boundary = False

    def submit(self, command: bytes, state: SubmissionState) -> bytes:
        raise AdapterError(
            TRANSPORT_INTERRUPTED,
            "no transport is configured; this slice adds no endpoint")


# -- receipts (§13g H.3) ---------------------------------------------------

RECEIPT_FIELDS = frozenset(("operation_id", "payload_digest", "result",
                            "authentication", "stored_digest",
                            "rejection_code"))
RESULT_CREATED = "CREATED"
RESULT_ALREADY_EXISTS = "ALREADY_EXISTS"
RESULT_REJECTED = "REJECTED"
# OBJECT_NOT_FOUND, not ABSENT: `ABSENT` is a COORDINATE_KIND in
# scythe_invariant_ledger -- a coordinate that is not there -- and a GraphOps
# lookup result is a different subject. Renamed rather than judged, for the
# reason §13f G.8 gives: a judgement should record a resemblance worth keeping,
# and a second meaning for a merit-side word is not one.
RESULT_ABSENT = "OBJECT_NOT_FOUND"


@dataclass(frozen=True)
class Receipt:
    operation_id: str
    payload_digest: str
    result: str
    authentication: str
    stored_digest: Optional[str]
    rejection_code: Optional[str]


def validate_receipt(raw: bytes, *, expected_operation_id: str,
                     expected_digest: str, conformance: GraphOpsConformance,
                     authenticator: Callable[[bytes, Receipt], bool]
                     ) -> Tuple[Optional[Receipt], Optional[str]]:
    """Parse and check, or say why not. Never partially trusted.

    Returns (receipt, None) or (None, evidence_code). Every failure here is
    UNKNOWN territory: a receipt we cannot read is not a receipt saying no.
    """
    if len(raw) > conformance.max_response_bytes:
        return None, RECEIPT_MALFORMED
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, RECEIPT_MALFORMED
    if not isinstance(payload, dict) or set(payload) != RECEIPT_FIELDS:
        return None, RECEIPT_MALFORMED
    if payload["result"] not in (RESULT_CREATED, RESULT_ALREADY_EXISTS,
                                 RESULT_REJECTED, RESULT_ABSENT):
        return None, RECEIPT_MALFORMED
    receipt = Receipt(**payload)
    if not authenticator(raw, receipt):
        return None, RECEIPT_UNAUTHENTICATED
    if receipt.operation_id != expected_operation_id:
        return None, RECEIPT_IDENTITY_MISMATCH
    if receipt.payload_digest != expected_digest:
        return None, RECEIPT_IDENTITY_MISMATCH
    return receipt, None


# -- classification (§13g H.4) --------------------------------------------

@dataclass(frozen=True)
class WriteResult:
    """Closed. No free-text detail and no raw body (§13g H.3)."""

    outcome: str
    operation_id: str
    payload_digest: str
    evidence_code: str
    receipt_digest: Optional[str] = None

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise AdapterError(TRANSPORT_INTERRUPTED,
                               f"{str(self.outcome)[:32]!r} is not an outcome")
        if self.evidence_code not in EVIDENCE_CODES:
            raise AdapterError(TRANSPORT_INTERRUPTED,
                               f"{str(self.evidence_code)[:32]!r} is not evidence")


def classify(*, state: SubmissionState, attests_boundary: bool,
             receipt: Optional[Receipt], receipt_failure: Optional[str],
             conformance: GraphOpsConformance, expected_digest: str,
             operation: str, interrupted: bool) -> WriteResult:
    """The validated receipt and the transport state. Nothing else.

    No HTTP status is visible here, and no exception type reaches it: the
    transport returns bytes or raises, and what the classifier sees is a
    receipt that validated or a reason it did not. A tidy 200 carrying the
    wrong operation identity is UNKNOWN, and it is UNKNOWN for the same reason
    an ugly rejection is: the evidence did not establish what happened.
    """
    def result(outcome, evidence, receipt_digest=None):
        return WriteResult(outcome=outcome, operation_id=operation,
                           payload_digest=expected_digest,
                           evidence_code=evidence, receipt_digest=receipt_digest)

    if interrupted:
        if state.crossed:
            return result(UNKNOWN, SUBMISSION_BOUNDARY_CROSSED)
        if attests_boundary:
            # The only innocence this contract accepts: a transport that knows
            # no byte left, and says so.
            return result(NOT_CREATED, SUBMISSION_NEVER_BEGAN)
        # It looks like nothing happened, which is not evidence that nothing
        # happened. An uninstrumented transport cannot infer innocence from an
        # empty response.
        return result(UNKNOWN, TRANSPORT_INTERRUPTED)

    if receipt_failure is not None:
        return result(UNKNOWN, receipt_failure)
    if receipt is None:
        return result(UNKNOWN, TRANSPORT_INTERRUPTED)

    if receipt.result == RESULT_CREATED:
        return result(CREATED, CREATION_RECEIPT_VALID, receipt.payload_digest)

    if receipt.result == RESULT_ALREADY_EXISTS:
        if receipt.stored_digest == expected_digest:
            return result(CREATED, IDEMPOTENT_MATCH, receipt.stored_digest)
        # Something under our operation identity is not what we sent. No
        # further attempt can improve that, and calling it success would be
        # asserting the graph holds a record we did not write.
        return result(UNKNOWN, IDEMPOTENT_DIGEST_CONFLICT, receipt.stored_digest)

    if receipt.result == RESULT_REJECTED:
        if receipt.rejection_code in conformance.mutation_free_rejection_codes:
            return result(NOT_CREATED, MUTATION_FREE_REJECTION)
        return result(UNKNOWN, TRANSPORT_INTERRUPTED)

    # RESULT_ABSENT: a lookup said the object is not there. Authoritative only
    # where the contract guarantees absence is, which is what STRONG means.
    if conformance.consistency == STRONG and conformance.stable_operation_lookup:
        return result(NOT_CREATED, AUTHORITATIVE_ABSENCE)
    return result(UNKNOWN, LOOKUP_INCONCLUSIVE)


# -- the adapter -----------------------------------------------------------

@dataclass
class GraphOpsAdapter:
    """One reservation, one attempt, one classification.

    Holds no credentials and opens no endpoint: the transport is injected, and
    the default one refuses. Slice 9 adds no production path to a real GraphOps.
    """

    conformance: GraphOpsConformance
    transport: Transport = field(default_factory=Transport)
    authenticator: Callable[[bytes, Receipt], bool] = \
        field(default=lambda raw, receipt: receipt.authentication == "VALID")
    accepts_reservation_facts = True

    def __post_init__(self) -> None:
        self._attempted: Dict[str, str] = {}
        self._halted: Optional[str] = None

    @property
    def configuration_identity(self) -> str:
        return self.conformance.identity()

    @property
    def halted(self) -> bool:
        return self._halted is not None

    def refusals(self) -> Tuple[str, ...]:
        codes = []
        if not self.conformance.conformant:
            codes.append(ADAPTER_NOT_CONFORMANT)
        if self._halted is not None:
            codes.append(ADAPTER_HALTED)
        return tuple(codes)

    def execute(self, facts: ReservationFacts) -> WriteResult:
        """Exactly one attempt for this reservation. Never a second.

        A second call for one operation identity is refused rather than retried,
        because the first attempt's uncertainty is not improved by repeating it
        -- and repeating it is how one reservation becomes two graph records.
        """
        operation = facts.operation_id()
        digest = facts.digest()
        if not self.conformance.conformant:
            raise AdapterError(
                ADAPTER_NOT_CONFORMANT,
                f"declared guarantees missing: "
                f"{', '.join(self.conformance.refusals())}")
        if self._halted is not None:
            raise AdapterError(ADAPTER_HALTED, self._halted)
        if operation in self._attempted:
            raise AdapterError(
                ADAPTER_HALTED,
                "this reservation has already addressed its operation; a second "
                "call is a retry, and retry after ambiguity is the operator's "
                "(§13e)")
        self._attempted[operation] = UNKNOWN

        command = encode_command(facts, self.configuration_identity)
        if len(command) > self.conformance.max_request_bytes:
            self._attempted[operation] = NOT_CREATED
            return WriteResult(outcome=NOT_CREATED, operation_id=operation,
                               payload_digest=digest,
                               evidence_code=LOCAL_VALIDATION_REFUSED)

        state = SubmissionState()
        raw, interrupted = None, False
        try:
            raw = self.transport.submit(command, state)
        except Exception:            # noqa: BLE001 -- the type is not evidence
            interrupted = True

        receipt, failure = None, None
        if not interrupted and raw is not None:
            receipt, failure = validate_receipt(
                raw, expected_operation_id=operation, expected_digest=digest,
                conformance=self.conformance, authenticator=self.authenticator)

        outcome = classify(
            state=state,
            attests_boundary=bool(self.transport.attests_submission_boundary),
            receipt=receipt, receipt_failure=failure,
            conformance=self.conformance, expected_digest=digest,
            operation=operation, interrupted=interrupted)
        self._attempted[operation] = outcome.outcome
        if outcome.evidence_code == IDEMPOTENT_DIGEST_CONFLICT:
            self._halted = ("an object under this operation identity carries a "
                            "different payload digest")
        return outcome

    def __call__(self, facts: ReservationFacts) -> WriteResult:
        """The coordinator's writer protocol.

        Delegates rather than aliasing `execute`, so a subclass overriding
        `execute` is still the thing that runs -- an alias bound at class
        definition would call the base method and the override would be dead
        code nobody noticed.
        """
        return self.execute(facts)

    def status(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "command_schema": COMMAND_SCHEMA,
            "configuration_identity": self.configuration_identity,
            "conformant": self.conformance.conformant,
            "conformance_refusals": list(self.conformance.refusals()),
            "refusals": list(self.refusals()),
            "attempts": len(self._attempted),
            "halted": self.halted,
            "transport_attests_submission_boundary":
                bool(self.transport.attests_submission_boundary),
            "endpoint": "NOT_CONFIGURED",
            "credentials": "NOT_CONFIGURED",
            "outcomes": list(OUTCOMES),
            "evidence_codes": list(EVIDENCE_CODES),
            "submission_states": list(SUBMISSION_STATES),
            "retries_after_unknown": False,
        }
