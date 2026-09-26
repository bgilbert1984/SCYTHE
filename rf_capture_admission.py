"""§5.25: does this window **belong** in this corpus, and what does writing it cost?

§5.24 answered a different question. `attest_window` establishes *is this
precisely the window the ring issued, and are its bytes bound for the length of
this action*. It says nothing about whether that window belongs in this corpus,
and it cannot: the ring has never heard of a `PromotionCorpusLock`.

    attested authoritative metadata
        -> captured-window admission
        -> canonical header with declared payload length
        -> complete payload action
        -> written count reconciled to declared length

Every arrow is a refusal point and none of them implies the next.

**Two regimes, two exception types.** `CaptureRefused` is a *precondition*
refusal: it happens before `create_target` is called, so nothing exists --
no file, no directory, no partial artefact. `PublicationFailed` is a *post-open*
failure, and §5.20 already governs those: no partial **final** file, and an
orphan temporary that is never a corpus member and is deliberately kept for
diagnosis. The contract must not promise "no artefact" once publication has
begun, so this module does not: it never unlinks and never closes what it did
not open.

**What this module does not do**, because §5.20's persistence slice is unbuilt
and unauthorised: it resolves no path, creates no directory, opens no file,
checks no mount or mode or ownership, does not `fsync`, does not publish by
rename, does not read anything back and computes no `file_sha256`. §5.20's
publication protocol steps 1-4 and the length reconciliation are here; steps
5-8 are not. The **creation** of step 3 is supplied by the caller as
`create_target`, invoked exactly once and only after every precondition has
passed -- which is what makes "admission before creation" a fact a test can
observe rather than a claim about the order of some lines.

There is also no corpus ownership scope here. §5.25 is explicit that it does not
create one; what these entrypoints consume are the authorities such a scope
would hold -- the frozen lock and the retention policy -- passed nominally, so
the envelope is still read out of the frozen lock and never supplied by the
caller as an answer or as a set.

**This is not yet an enforced boundary, and two blockers say why.** Recorded
here rather than left to be rediscovered, because a module that reads as
finished is how an obligation ages into fiction.

*It consumes the ownership scope, and is no longer told the set.* 3c-wire.
Admission takes the corpus ownership scope and asks it, through one action,
whether the attested chain is a declared member; the terms it binds in the
header come back as bounded facts, never as objects. The free-standing lock
and the caller's `now` are gone from every entrypoint, and a caller that
passes either gets a `TypeError` rather than a second authority. The scope
names the clock it acquired as `corpus_clock_authority`, declared by the
ownership authority alone.

*Nothing is compelled to pass through it.* There is no ownership scope, no
namespace, no production directory and no publisher, so this gate currently
refuses nothing that could otherwise become corpus -- which is §5.25's own
words for what cannot drain entry 14: *"admission without a writer refuses
nothing that could otherwise happen."*

*And the clock is the scope's, not the caller's.* The ownership scope acquires
its time source when it opens and the header names it, as the header already
names the ring's clock for the capture times. No production caller supplies a
timestamp per write, and no entrypoint accepts one.

Steps 1-4 of §5.20's protocol and the reconciliation are here. Durability,
no-replacement publication, the directory `fsync`, readback, `file_sha256` and
filename agreement, and counting only after verification are not -- so what
this produces is a temporary-file producer, not durable corpus membership.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from rf_capture_format import (
    FILE_DIGEST_FIELD_FORBIDDEN_IN_HEADER, IQC_BYTE_ORDER, IQC_FORMAT_VERSION,
    IQC_HEADER_SCHEMA, IQC_SAMPLE_DTYPE, FramingRefused, canonical_header_bytes,
    framing_prefix,
)
from rf_corpus_vocabulary import CAPTURED
from rf_iq_ring import (
    BYTES_PER_SAMPLE, WINDOW_INTERVAL_OVERLAP, AttestedIQWindowScope,
)
from rf_promotion_geometry import (
    PROMOTION_SAMPLE_RATE_HZ, PROMOTION_WINDOW_OVERLAP, PROMOTION_WINDOW_SAMPLES,
)
from rf_validation_manifest import (
    MINIMUM_WINDOWS_PER_STRATUM, STRATA_DEFINITION_REVISION, STRATUM_KEYS,
)

SCHEMA = "scythe.rf-capture-admission.v1"

# -- what §5.20 granted, named here rather than inferred --------------------
#
# "SCYTHE may atomically publish verified, ring-issued validation windows for
# exactly GAIN_STEPS, RETUNE_TRANSIENTS and RECEIVER_SPURS." These three are
# also `rf_validation_manifest.TUNER_REQUIRED`, and they are **not** that: one
# is a property of the stratum (it needs a receiver rather than a generator),
# the other is the scope of a grant. They coincide today because every stratum
# a generator cannot build is one §5.20 named, and a future stratum could be
# captured without needing a tuner, or need a tuner without being granted.
CAPTURED_STRATA: Tuple[str, ...] = (
    "GAIN_STEPS", "RETUNE_TRANSIENTS", "RECEIVER_SPURS",
)

# Checked against the strata themselves rather than trusted. A granted stratum
# that is not a stratum would be a capture nothing could ever count.
_NOT_STRATA = tuple(key for key in CAPTURED_STRATA if key not in STRATUM_KEYS)
if _NOT_STRATA:                                          # pragma: no cover
    raise ImportError(
        f"{_NOT_STRATA} are named as captured strata and are not in STRATA")

# §5.20: an absolute deadline, supplied before the corpus opens and bounded by
# `opened_at + 90 days`. The anchor is `opened_at` rather than "the first
# captured window" because the first capture has not happened when the deadline
# is supplied -- a bound that defers to a future event is a promise, not a bound.
RETENTION_MAXIMUM_DAYS = 90
RETENTION_MAXIMUM_SECONDS = float(RETENTION_MAXIMUM_DAYS * 24 * 60 * 60)

# -- precondition refusals: before any target is opened ---------------------
#
# Everything in this tuple happens before `create_target` is called, so a
# refusal carrying one of these codes leaves no file, no directory and no
# partial artefact -- not because the code says so, but because nothing has
# been created at the point it is raised.
ADMISSION_SCOPE_TYPE_WRONG = "ADMISSION_SCOPE_TYPE_WRONG"
ADMISSION_SCOPE_ENDED = "ADMISSION_SCOPE_ENDED"
ADMISSION_OWNERSHIP_SCOPE_TYPE_WRONG = "ADMISSION_OWNERSHIP_SCOPE_TYPE_WRONG"
ADMISSION_OWNERSHIP_SCOPE_RELEASED = "ADMISSION_OWNERSHIP_SCOPE_RELEASED"
ADMISSION_STRATA_DEFINITION_MOVED = "ADMISSION_STRATA_DEFINITION_MOVED"
ADMISSION_RETENTION_NOT_SUPPLIED = "ADMISSION_RETENTION_NOT_SUPPLIED"
ADMISSION_RETENTION_BEYOND_MAXIMUM = "ADMISSION_RETENTION_BEYOND_MAXIMUM"
ADMISSION_RETENTION_EXPIRED = "ADMISSION_RETENTION_EXPIRED"
ADMISSION_STRATUM_OUTSIDE_GRANT = "ADMISSION_STRATUM_OUTSIDE_GRANT"
ADMISSION_ATTESTATION_UNCONSTRUCTIBLE = "ADMISSION_ATTESTATION_UNCONSTRUCTIBLE"
ADMISSION_ATTESTATION_TYPE_WRONG = "ADMISSION_ATTESTATION_TYPE_WRONG"
ADMISSION_GEOMETRY_REFUSED = "ADMISSION_GEOMETRY_REFUSED"
ADMISSION_CHAIN_OUTSIDE_ENVELOPE = "ADMISSION_CHAIN_OUTSIDE_ENVELOPE"
ADMISSION_SEQUENCE_NOT_THIS_STRATUM = "ADMISSION_SEQUENCE_NOT_THIS_STRATUM"
ADMISSION_RING_LIFETIME_MISMATCH = "ADMISSION_RING_LIFETIME_MISMATCH"
ADMISSION_SEQUENCE_HISTORY_REFUSED = "ADMISSION_SEQUENCE_HISTORY_REFUSED"
ADMISSION_STRATUM_CAP_REACHED = "ADMISSION_STRATUM_CAP_REACHED"
ADMISSION_BINDING_CLAIMED_TWICE = "ADMISSION_BINDING_CLAIMED_TWICE"
ADMISSION_BINDING_NOT_EMITTED = "ADMISSION_BINDING_NOT_EMITTED"
ADMISSION_FIELD_NO_AUTHORITY = "ADMISSION_FIELD_NO_AUTHORITY"
ADMISSION_CANONICAL_FORM_REFUSED = "ADMISSION_CANONICAL_FORM_REFUSED"
ADMISSION_CREATOR_NOT_CALLABLE = "ADMISSION_CREATOR_NOT_CALLABLE"
ADMISSION_REFUSALS: Tuple[str, ...] = (
    ADMISSION_SCOPE_TYPE_WRONG, ADMISSION_SCOPE_ENDED,
    ADMISSION_OWNERSHIP_SCOPE_TYPE_WRONG, ADMISSION_OWNERSHIP_SCOPE_RELEASED,
    ADMISSION_STRATA_DEFINITION_MOVED, ADMISSION_RETENTION_NOT_SUPPLIED,
    ADMISSION_RETENTION_BEYOND_MAXIMUM, ADMISSION_RETENTION_EXPIRED,
    ADMISSION_STRATUM_OUTSIDE_GRANT, ADMISSION_ATTESTATION_UNCONSTRUCTIBLE,
    ADMISSION_ATTESTATION_TYPE_WRONG, ADMISSION_GEOMETRY_REFUSED,
    ADMISSION_CHAIN_OUTSIDE_ENVELOPE, ADMISSION_SEQUENCE_NOT_THIS_STRATUM,
    ADMISSION_RING_LIFETIME_MISMATCH, ADMISSION_SEQUENCE_HISTORY_REFUSED,
    ADMISSION_STRATUM_CAP_REACHED, ADMISSION_BINDING_CLAIMED_TWICE,
    ADMISSION_BINDING_NOT_EMITTED, ADMISSION_FIELD_NO_AUTHORITY,
    ADMISSION_CANONICAL_FORM_REFUSED, ADMISSION_CREATOR_NOT_CALLABLE,
    WINDOW_INTERVAL_OVERLAP,
)

# -- post-open failures: once publication has begun -------------------------
#
# Facts about the write, which cannot be known before one. §5.20 governs what
# they leave behind and this section does not contradict it.
PUBLICATION_TARGET_NOT_A_DESCRIPTOR = "PUBLICATION_TARGET_NOT_A_DESCRIPTOR"
PUBLICATION_FRAMING_WRITE_INCOMPLETE = "PUBLICATION_FRAMING_WRITE_INCOMPLETE"
PUBLICATION_LENGTH_MISMATCH = "PUBLICATION_LENGTH_MISMATCH"
PUBLICATION_FAILURES: Tuple[str, ...] = (
    PUBLICATION_TARGET_NOT_A_DESCRIPTOR, PUBLICATION_FRAMING_WRITE_INCOMPLETE,
    PUBLICATION_LENGTH_MISMATCH,
)


class CaptureRefused(RuntimeError):
    """A precondition refusal. Nothing was created, because nothing had been."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class PublicationFailed(RuntimeError):
    """A failure after the target exists. §5.20's post-open protocol governs it.

    Deliberately **not** a subclass of `CaptureRefused`. The two regimes differ
    in what they leave on disk, and a caller that caught one and silently
    handled the other would be deciding an orphan temporary does not exist.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# -- the closed attestation union, selected by stratum ----------------------
#
# §5.20: not a universal before/after field. A gain change and a retune each
# have a control action with a before and an after; a spur has neither, and a
# header that required them would force RECEIVER_SPURS to fabricate two
# declarations to satisfy a schema. The union is closed: a stratum with no
# member has no way to produce a header.


@dataclass(frozen=True)
class GainStepAttestation:
    """The control action that produced a `GAIN_STEPS` window."""

    event_id: str
    gain_db_before: float
    gain_db_after: float
    invalidation_epoch: int


@dataclass(frozen=True)
class RetuneAttestation:
    """The control action that produced a `RETUNE_TRANSIENTS` window."""

    event_id: str
    centre_hz_before: float
    centre_hz_after: float
    invalidation_epoch: int


# `RECEIVER_SPURS` maps to None rather than being absent from the mapping. An
# absent key is a stratum somebody forgot; None is a stratum with **no
# constructible member**, which is §5.20's finding and is the reason
# `record_receiver_spur` refuses by construction.
STRATUM_ATTESTATION: Dict[str, Optional[type]] = {
    "GAIN_STEPS": GainStepAttestation,
    "RETUNE_TRANSIENTS": RetuneAttestation,
    "RECEIVER_SPURS": None,
}


# -- retention, which the ownership authorities carry -----------------------


@dataclass(frozen=True)
class CapturedCorpusRetention:
    """An absolute deletion deadline, supplied before the corpus captures.

    Finiteness is checked here because it is self-contained. The **bound** --
    `delete_not_after <= opened_at + 90 days` -- is checked at admission,
    because it is a statement about a lock this object has never seen. Same
    split as the envelope: what a value can establish alone, it establishes
    alone.
    """

    delete_not_after: float

    def __post_init__(self) -> None:
        value = float(self.delete_not_after)
        if value != value or value in (float("inf"), float("-inf")):
            raise CaptureRefused(
                ADMISSION_RETENTION_NOT_SUPPLIED,
                "a deletion deadline that is not a finite instant is not a "
                "deadline")
        object.__setattr__(self, "delete_not_after", value)

    def to_dict(self) -> Dict[str, Any]:
        return {"delete_not_after": self.delete_not_after,
                "maximum_days_from_opened_at": RETENTION_MAXIMUM_DAYS}


# -- corpus sequence state --------------------------------------------------


class CapturedStratumSequence:
    """What this corpus has already counted in one stratum, for one ring.

    Process-local by default and reconstructible from reconciled membership:
    `reconstruct_stratum_sequence` restores verified history after a reopen,
    which is the only other way a sequence comes into being. The ring
    lifetime binds the indices to the ring that produced them -- a sample
    index means nothing across two rings (section 5.26 single-lifetime
    capture), so a sequence restored for one lifetime never authorises
    continuity for another lifetime's windows, and admission refuses the
    mismatch.

    **It does not advance on a write.** §5.20 counts a window at publication
    step 8, after the final file has been read back and both digests verified,
    and those steps are unbuilt. So `record_*` returns a publication and
    changes nothing here; `count_published` is the separate act a completed
    step 8 performs. A writer that never calls it will see every window report
    itself first-in-stratum, which is that writer's obligation and is visible
    rather than hidden.
    """

    __slots__ = ("corpus_id", "stratum", "ring_lifetime_id", "_accepted",
                 "_last_window_id", "_first_sample_index", "_last_sample_index")

    def __init__(self, *, corpus_id: str, stratum: str,
                 ring_lifetime_id: str) -> None:
        if stratum not in CAPTURED_STRATA:
            raise CaptureRefused(
                ADMISSION_STRATUM_OUTSIDE_GRANT,
                f"{stratum!r} is not one of the three strata §5.20 granted "
                f"capture for: {', '.join(CAPTURED_STRATA)}")
        if not isinstance(ring_lifetime_id, str) or not ring_lifetime_id:
            raise CaptureRefused(
                ADMISSION_RING_LIFETIME_MISMATCH,
                "a stratum's sequence state is bound to the ring lifetime "
                "whose indices it counts; got "
                f"{ring_lifetime_id!r}")
        self.corpus_id = str(corpus_id)
        self.stratum = stratum
        self.ring_lifetime_id = ring_lifetime_id
        self._accepted = 0
        self._last_window_id: Optional[str] = None
        # Both ends of the predecessor, because they answer different
        # questions. The **last** index is where the successor must begin at
        # the earliest, which is the refusal -- the same arithmetic
        # `rf_iq_ring.window_interval_disjoint` states, and correct for the
        # shorter windows a development configuration may take. The **first**
        # index is what the header records the interval against, so a reader
        # with only the files can check `interval >= sample_count` using two
        # fields that are both in front of them.
        self._first_sample_index: Optional[int] = None
        self._last_sample_index: Optional[int] = None

    @property
    def accepted(self) -> int:
        return self._accepted

    @property
    def previous_window_id(self) -> Optional[str]:
        return self._last_window_id

    @property
    def previous_first_sample_index(self) -> Optional[int]:
        return self._first_sample_index

    @property
    def previous_last_sample_index(self) -> Optional[int]:
        return self._last_sample_index

    def count_published(self, publication: "CapturedWindowPublication") -> int:
        """Count a window §5.20 step 8 has verified. Returns the new count."""
        if type(publication) is not CapturedWindowPublication:
            raise CaptureRefused(
                ADMISSION_SEQUENCE_NOT_THIS_STRATUM,
                "a stratum counts publications, not claims; got "
                f"{type(publication).__name__}")
        if (publication.corpus_id != self.corpus_id
                or publication.stratum != self.stratum):
            raise CaptureRefused(
                ADMISSION_SEQUENCE_NOT_THIS_STRATUM,
                f"{publication.corpus_id}/{publication.stratum} counted "
                f"against {self.corpus_id}/{self.stratum}")
        self._accepted += 1
        self._last_window_id = publication.window_id
        self._first_sample_index = publication.first_sample_index
        self._last_sample_index = publication.last_sample_index
        return self._accepted

    def to_dict(self) -> Dict[str, Any]:
        return {"corpus_id": self.corpus_id, "stratum": self.stratum,
                "ring_lifetime_id": self.ring_lifetime_id,
                "accepted": self._accepted,
                "previous_window_id": self._last_window_id,
                "previous_first_sample_index": self._first_sample_index,
                "previous_last_sample_index": self._last_sample_index,
                "cap": MINIMUM_WINDOWS_PER_STRATUM}


@dataclass(frozen=True)
class CommittedWindow:
    """One reconciled corpus member, for sequence reconstruction.

    The view 3d's recovery reconciliation produces from the journal and the
    verified final files, oldest first. The ring lifetime rides along so a
    mixed-lifetime history is refused at reconstruction rather than silently
    adopted as continuity.
    """
    window_id: str
    first_sample_index: int
    last_sample_index: int
    ring_lifetime_id: str


def reconstruct_stratum_sequence(*, corpus_id: str, stratum: str,
                                 ring_lifetime_id: str,
                                 committed_windows: Tuple[CommittedWindow, ...]
                                 ) -> CapturedStratumSequence:
    """Rebuild a stratum's sequence from its reconciled membership.

    The input is verified history, not a live claim: every window here was
    admitted, published and committed before the reopen -- or the input is
    empty, which is the creation case and yields a fresh sequence. A window
    from another ring lifetime is refused, not skipped: foreign history is
    reported, never adopted as continuity.

    This restores; it does not advance. The live path still advances only
    through `count_published`, the step-8 act.
    """
    if not isinstance(ring_lifetime_id, str) or not ring_lifetime_id:
        raise CaptureRefused(
            ADMISSION_RING_LIFETIME_MISMATCH,
            "sequence reconstruction is bound to a ring lifetime; got "
            f"{ring_lifetime_id!r}")
    sequence = CapturedStratumSequence(
        corpus_id=corpus_id, stratum=stratum,
        ring_lifetime_id=ring_lifetime_id)
    for window in committed_windows:
        if type(window) is not CommittedWindow:
            raise CaptureRefused(
                ADMISSION_SEQUENCE_HISTORY_REFUSED,
                "sequence is reconstructed from reconciled members, not "
                f"claims; got {type(window).__name__}")
        if window.ring_lifetime_id != ring_lifetime_id:
            raise CaptureRefused(
                ADMISSION_RING_LIFETIME_MISMATCH,
                f"window {window.window_id!r} belongs to ring lifetime "
                f"{window.ring_lifetime_id!r}, not {ring_lifetime_id!r}; a "
                "sample index means nothing across two rings")
        if not isinstance(window.window_id, str) or not window.window_id:
            raise CaptureRefused(
                ADMISSION_SEQUENCE_HISTORY_REFUSED,
                "a reconciled member without an id is not history")
        for field in ("first_sample_index", "last_sample_index"):
            value = getattr(window, field)
            if (not isinstance(value, int) or isinstance(value, bool)
                    or value < 0):
                raise CaptureRefused(
                    ADMISSION_SEQUENCE_HISTORY_REFUSED,
                    f"window {window.window_id!r} carries {field} {value!r}; "
                    "reconstructed history must carry indices")
        if window.last_sample_index < window.first_sample_index:
            raise CaptureRefused(
                ADMISSION_SEQUENCE_HISTORY_REFUSED,
                f"window {window.window_id!r} ends at "
                f"{window.last_sample_index} before it begins at "
                f"{window.first_sample_index}")
        # Verified history, restored directly: this is not the live path, so
        # it does not go through `count_published`, the step-8 act.
        sequence._accepted += 1
        sequence._last_window_id = window.window_id
        sequence._first_sample_index = window.first_sample_index
        sequence._last_sample_index = window.last_sample_index
    return sequence


# -- the six authorities, each declaring what it exports --------------------
#
# §5.25: do not serialise every field of every object. Adding an internal
# diagnostic field to `PromotionCorpusLock` would then silently become a
# file-format revision, which is the kind of change that has to be deliberate
# or it is not a format at all.
#
# Each authority declares the bindings it exports. The **required** header set
# is the union of those declarations, and the emitted set must EQUAL it -- not
# contain it. So a field becomes required by being exported, an internal field
# stays internal by not being, and a field no authority declared is refused
# rather than decorating the canonical identity of a corpus member.
#
# The aggregator below calls the six contributions by name. It does not loop
# over this tuple, and that is the point: dropping an authority from the
# aggregator leaves the requirement standing, so the derivation refuses instead
# of quietly narrowing the format.


@dataclass(frozen=True)
class HeaderAuthority:
    """One source of header fields, and the fields it declares it supplies."""

    name: str
    declares: Callable[[str], Tuple[str, ...]]
    describes: str


def _attested_scope_declares(stratum: str) -> Tuple[str, ...]:
    return ("window_id", "ring_digest", "configuration_epoch",
            "signal_chain_hash", "sample_count", "sample_rate_hz",
            "first_sample_index", "capture_start_time", "capture_end_time",
            "clock_authority", "ring_lifetime_id")


def _corpus_ownership_declares(stratum: str) -> Tuple[str, ...]:
    # §5.26 point 4, 3c-wire: `corpus_clock_authority` is declared here and
    # nowhere else. The scope acquired its clock when it opened, so the field
    # is a property of the ownership scope and not window metadata; the ring's
    # own `clock_authority` for the capture times stays with the attested
    # scope. Required fields 32 -> 33.
    return ("corpus_id", "configuration_digest", "envelope_digest",
            "capture_plan_digest", "delete_not_after",
            "corpus_clock_authority")


def _typed_entrypoint_declares(stratum: str) -> Tuple[str, ...]:
    """The stratum, and **every field** of its exact nominal attestation member.

    Derived from the member type rather than transcribed, so a field added to
    `GainStepAttestation` becomes a required header binding without anyone
    editing a list -- and the header stops being emittable until it is emitted.

    **The stratum, and nothing that repeats it.** An earlier revision also
    emitted `attestation_kind`, naming the member type. It was declared, so
    exclusivity permitted it, and it was still wrong: the union is closed and
    the stratum selects the member, so the field repeated an interpretation the
    header had already fixed -- inside bytes that are hashed into
    `file_sha256`. A redundant hashed discriminator can disagree with the fact
    it repeats, and two spellings of one attestation are two canonical
    identities for one window.

    The schema-version analogy that was offered for it does not carry. A schema
    version tells a parser **how to interpret** the record; `attestation_kind`
    repeated an interpretation `stratum` had already selected.
    """
    member = STRATUM_ATTESTATION.get(stratum)
    if member is None:
        return ("stratum",)
    return ("stratum",) + tuple(
        f"attestation_{field.name}" for field in dataclasses.fields(member))


def _corpus_sequence_declares(stratum: str) -> Tuple[str, ...]:
    return ("previous_window_id", "previous_window_sample_interval",
            "window_overlap")


def _payload_action_declares(stratum: str) -> Tuple[str, ...]:
    return ("payload_sha256",)


def _file_format_declares(stratum: str) -> Tuple[str, ...]:
    return ("schema", "format_version", "source", "sample_dtype", "byte_order",
            "strata_definition_revision", "declared_payload_bytes")


HEADER_AUTHORITIES: Tuple[HeaderAuthority, ...] = (
    HeaderAuthority(
        "attested_scope", _attested_scope_declares,
        "all authoritative window metadata, bound at mint time and never "
        "re-read from the attested object"),
    HeaderAuthority(
        "corpus_ownership", _corpus_ownership_declares,
        "corpus and configuration-lock identity, the envelope and capture-plan "
        "digests, the retention deadline, and the clock the scope acquired"),
    HeaderAuthority(
        "typed_entrypoint", _typed_entrypoint_declares,
        "the fixed stratum, and every field of its exact nominal attestation"),
    HeaderAuthority(
        "corpus_sequence", _corpus_sequence_declares,
        "the predecessor, the overlap declaration and the sample interval"),
    HeaderAuthority(
        "payload_action", _payload_action_declares,
        "payload_sha256, taken from the bytes while the scope is live"),
    HeaderAuthority(
        "file_format", _file_format_declares,
        "what the format declares about itself, including the declared "
        "payload length §5.24 left nothing else to answer"),
)

# The one contribution that is NOT a header field. §5.25 names the payload
# action as the authority for "payload_sha256 and the completed write count",
# and the count cannot be a header field: §5.20 writes the header first, so at
# the moment the header is serialised the write has not happened. It is
# reconciled instead, against the declaration the header already carries. The
# same non-recursion that keeps `file_sha256` out of the header, one field over.
PAYLOAD_ACTION_RECONCILES = "completed_write_count"


def required_header_fields(stratum: str) -> frozenset:
    """The union of the six declarations. Derived, never transcribed.

    `SCYTHE_VERDICT_VOCABULARIES.md` §3's repair, applied to a file format: a
    hand-written list of required fields is silent about a field that is
    missing, because it was written before the field existed. Entry 11 began as
    exactly that shape.
    """
    fields: set = set()
    for authority in HEADER_AUTHORITIES:
        fields.update(authority.declares(stratum))
    return frozenset(fields)


def _merge(header: Dict[str, Any], authority: str,
           contribution: Mapping[str, Any],
           claimed: Dict[str, str]) -> None:
    """Merge one authority's contribution, refusing a field claimed twice.

    Two authorities supplying one field is not caught by the union check --
    both declare it, one value wins, and which one depends on call order. That
    is a format whose content depends on the order of six lines.
    """
    for field, value in contribution.items():
        if field in claimed:
            raise CaptureRefused(
                ADMISSION_BINDING_CLAIMED_TWICE,
                f"{field!r} is supplied by both {claimed[field]} and "
                f"{authority}; one field has one authority")
        claimed[field] = authority
        header[field] = value


def derive_canonical_header(*, metadata: Mapping[str, Any],
                            ownership: Any,
                            stratum: str, attestation: Any,
                            sequence: CapturedStratumSequence,
                            payload_sha256: str) -> Dict[str, Any]:
    """Every header field, from the authority that owns it, and nothing else.

    Called by name rather than by looping `HEADER_AUTHORITIES`, so that
    dropping an authority here leaves its declaration standing and the
    completeness check refuses. A loop would delete the requirement and the
    contribution in one edit, which is the mutation §5.25's control 8a exists
    to catch.
    """
    header: Dict[str, Any] = {}
    claimed: Dict[str, str] = {}

    _merge(header, "attested_scope", {
        "window_id": metadata["window_id"],
        "ring_digest": metadata["digest"],
        "configuration_epoch": metadata["configuration_epoch"],
        "signal_chain_hash": metadata["signal_chain_hash"],
        "sample_count": metadata["sample_count"],
        "sample_rate_hz": metadata["sample_rate_hz"],
        "first_sample_index": metadata["first_sample_index"],
        "capture_start_time": metadata["start_time"],
        "capture_end_time": metadata["end_time"],
        "clock_authority": metadata["clock_authority"],
        # §5.26. Read from the attested scope's metadata, where attestation
        # put it, never from the window object: the object's copy is what
        # attestation checked, not what it established.
        "ring_lifetime_id": metadata["ring_lifetime_id"],
    }, claimed)

    # 3c-wire. `ownership` is the scope's answer to `admit_window`: bounded
    # facts read while the namespace was held, not the lock and not a
    # retention object a caller supplied beside it.
    _merge(header, "corpus_ownership", {
        "corpus_id": ownership.corpus_id,
        "configuration_digest": ownership.configuration_digest,
        "envelope_digest": ownership.envelope_digest,
        "capture_plan_digest": ownership.capture_plan_digest,
        "delete_not_after": ownership.delete_not_after,
        "corpus_clock_authority": ownership.corpus_clock_authority,
    }, claimed)

    entrypoint: Dict[str, Any] = {"stratum": stratum}
    member = STRATUM_ATTESTATION.get(stratum)
    if member is not None:
        for field in dataclasses.fields(member):
            entrypoint[f"attestation_{field.name}"] = getattr(
                attestation, field.name)
    _merge(header, "typed_entrypoint", entrypoint, claimed)

    # Start to start, not end to start. §5.20 correction A states the rule as
    # `current.first >= previous.first + 524_288`, and the header already
    # carries `sample_count` -- so an interval measured this way lets a reader
    # holding nothing but the files check `interval >= sample_count` and
    # conclude non-overlap. A gap measured from the predecessor's end reads as
    # zero for two perfectly adjacent windows and tells that reader nothing
    # without a length they would have to go and find.
    previous = sequence.previous_first_sample_index
    _merge(header, "corpus_sequence", {
        "previous_window_id": sequence.previous_window_id,
        # The first accepted window in a stratum has no predecessor: a null id
        # and no interval, rather than a zero that reads like an overlap.
        "previous_window_sample_interval": (
            None if previous is None
            else int(metadata["first_sample_index"]) - int(previous)),
        "window_overlap": PROMOTION_WINDOW_OVERLAP,
    }, claimed)

    _merge(header, "payload_action", {"payload_sha256": payload_sha256},
           claimed)

    _merge(header, "file_format", {
        "schema": IQC_HEADER_SCHEMA,
        "format_version": IQC_FORMAT_VERSION,
        "source": CAPTURED,
        "sample_dtype": IQC_SAMPLE_DTYPE,
        "byte_order": IQC_BYTE_ORDER,
        "strata_definition_revision": STRATA_DEFINITION_REVISION,
        "declared_payload_bytes": (
            int(metadata["sample_count"]) * BYTES_PER_SAMPLE),
    }, claimed)

    return header


def check_header_completeness(header: Mapping[str, Any], stratum: str) -> None:
    """The emitted set must **equal** the union of the declarations.

    Missing is 8a and 8b; extra is 8c. Both are refusals, and extra is a
    refusal for the same reason the verdict vocabulary refuses a caller-supplied
    label: the header is hashed into `file_sha256`, so a field no authority
    declared is caller-supplied decoration inside the canonical identity of a
    corpus member.
    """
    required = required_header_fields(stratum)
    emitted = frozenset(header)
    missing = sorted(required - emitted)
    if missing:
        raise CaptureRefused(
            ADMISSION_BINDING_NOT_EMITTED,
            f"{len(missing)} declared binding(s) absent from the header: "
            f"{', '.join(missing)}")
    extra = sorted(emitted - required)
    if extra:
        raise CaptureRefused(
            ADMISSION_FIELD_NO_AUTHORITY,
            f"{len(extra)} header field(s) no authority declared: "
            f"{', '.join(extra)}")


# -- the publication result -------------------------------------------------


@dataclass(frozen=True)
class CapturedWindowPublication:
    """What steps 1-4 established, for the unbuilt steps 5-8 to finish.

    Carries the canonical header bytes so `file_sha256` can be computed over
    the same span that was written, rather than over a re-serialisation that
    might differ by a space. Carries no samples and no view of any.
    """

    corpus_id: str
    stratum: str
    window_id: str
    first_sample_index: int
    last_sample_index: int
    declared_payload_bytes: int
    payload_bytes_written: int
    framing_bytes_written: int
    payload_sha256: str
    header_bytes: bytes
    framing_prefix_bytes: bytes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "corpus_id": self.corpus_id,
            "stratum": self.stratum,
            "window_id": self.window_id,
            "first_sample_index": self.first_sample_index,
            "last_sample_index": self.last_sample_index,
            "declared_payload_bytes": self.declared_payload_bytes,
            "payload_bytes_written": self.payload_bytes_written,
            "framing_bytes_written": self.framing_bytes_written,
            "payload_sha256": self.payload_sha256,
            "header_length": len(self.header_bytes),
            "raw_iq_exposed": False,
            "publication_steps_completed": "5.20 STEPS 1-4 AND RECONCILIATION",
        }


# -- admission --------------------------------------------------------------


def _ownership_scope_types() -> Tuple[type, type]:
    """`CorpusOwnershipScope` and `NamespaceRefused`, resolved on use.

    Not a module-level import: `rf_corpus_namespace` reaches this module
    through `rf_membership_journal` for `CAPTURED_STRATA`, so a top-level
    import here would make the cycle's outcome depend on which module the
    process imported first. Resolving at the call site keeps both orders
    working and keeps the exact nominal type check -- `type(x) is` -- intact.
    """
    from rf_corpus_namespace import CorpusOwnershipScope, NamespaceRefused
    return CorpusOwnershipScope, NamespaceRefused


def _admit(*, scope: Any, stratum: str, attestation: Any,
           corpus: Any, sequence: Any
           ) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """Every precondition, in order, before anything is created.

    §5.25's ordering requirement is narrower than "every check that can
    refuse": every **admission, authority and publication-precondition** check
    completes before creation, while failures intrinsic to writing remain
    post-open. Nothing in this function opens, creates or touches a filesystem.
    """
    # 1. the exact nominal scope type. A subclass or a stand-in carries the
    #    same attributes and is not the thing the ring minted.
    if type(scope) is not AttestedIQWindowScope:
        raise CaptureRefused(
            ADMISSION_SCOPE_TYPE_WRONG,
            "a captured window is admitted from an AttestedIQWindowScope; got "
            f"{type(scope).__name__}")

    # 2. mint provenance and active lifetime, resolved by object identity.
    #    `active` is False both for a scope that has ended and for one whose
    #    handle no longer resolves to state minted for it, which are the two
    #    ways a scope can fail to be one.
    if not scope.active:
        raise CaptureRefused(
            ADMISSION_SCOPE_ENDED,
            "the attestation scope is not live; a window cannot be admitted "
            "from a scope that has ended or was never minted for this object")
    metadata = scope.to_dict()

    # 3. the corpus ownership scope, exact and live. §5.26: admission consumes
    #    the scope and nothing beside it. The action re-establishes that the
    #    namespace is still held and answers with bounded terms; the lock's
    #    digests were verified against the manifest when the scope opened, so
    #    there is nothing here for a caller-built object to be consistent with.
    ownership_type, namespace_refused = _ownership_scope_types()
    if type(corpus) is not ownership_type:
        raise CaptureRefused(
            ADMISSION_OWNERSHIP_SCOPE_TYPE_WRONG,
            "admission consumes a CorpusOwnershipScope minted by "
            f"rf_corpus_namespace; got {type(corpus).__name__}")
    try:
        terms = corpus.admit_window(
            signal_chain_hash=metadata["signal_chain_hash"])
    except namespace_refused as exc:
        raise CaptureRefused(
            ADMISSION_OWNERSHIP_SCOPE_RELEASED,
            "the corpus ownership scope no longer holds its namespace; a "
            f"window cannot be admitted into a corpus nobody holds: {exc}"
        ) from exc
    if terms.strata_definition_revision != STRATA_DEFINITION_REVISION:
        raise CaptureRefused(
            ADMISSION_STRATA_DEFINITION_MOVED,
            f"the corpus was opened under {terms.strata_definition_revision} "
            f"and the strata now mean {STRATA_DEFINITION_REVISION}; a window "
            "captured now would be a trial of a different population")

    # 4. retention, against the terms the corpus recorded when it opened, and
    #    the scope's own clock -- no caller timestamp enters here.
    if terms.delete_not_after <= terms.opened_at:
        raise CaptureRefused(
            ADMISSION_RETENTION_EXPIRED,
            "the deletion deadline is at or before the corpus opened")
    if terms.delete_not_after > terms.opened_at + RETENTION_MAXIMUM_SECONDS:
        raise CaptureRefused(
            ADMISSION_RETENTION_BEYOND_MAXIMUM,
            f"the deletion deadline is more than {RETENTION_MAXIMUM_DAYS} days "
            "after the corpus opened")
    if terms.now >= terms.delete_not_after:
        raise CaptureRefused(
            ADMISSION_RETENTION_EXPIRED,
            "the deletion deadline has passed; capture into a corpus already "
            "due for deletion is not authorised")

    # 5. the stratum this entrypoint fixes, and its closed attestation.
    if stratum not in CAPTURED_STRATA:
        raise CaptureRefused(
            ADMISSION_STRATUM_OUTSIDE_GRANT,
            f"§5.20 granted capture for {', '.join(CAPTURED_STRATA)} and not "
            f"for {stratum!r}")
    member = STRATUM_ATTESTATION[stratum]
    if member is None:
        raise CaptureRefused(
            ADMISSION_ATTESTATION_UNCONSTRUCTIBLE,
            f"{stratum} has no constructible attestation member. A tuner "
            "operation is evidence for a gain or retune event; having a "
            "receiver attached is not evidence that a feature is an internal "
            "spur, and §5.21's identification protocol has not been run")
    if type(attestation) is not member:
        raise CaptureRefused(
            ADMISSION_ATTESTATION_TYPE_WRONG,
            f"{stratum} is attested by an exact {member.__name__}; got "
            f"{type(attestation).__name__}")

    # 6. the promotion geometry. §5.20 correction A: capture refuses any
    #    geometry but 256 ms, because that is the ring the windows come out of
    #    and the window the corpus was sized against.
    if (int(metadata["sample_count"]) != PROMOTION_WINDOW_SAMPLES
            or float(metadata["sample_rate_hz"]) != PROMOTION_SAMPLE_RATE_HZ):
        raise CaptureRefused(
            ADMISSION_GEOMETRY_REFUSED,
            f"{metadata['sample_count']} samples at "
            f"{metadata['sample_rate_hz']} Hz is not the promotion geometry "
            f"({PROMOTION_WINDOW_SAMPLES} at {PROMOTION_SAMPLE_RATE_HZ})")

    # 7. the gate entry 14 names. The ownership scope was asked whether the
    #    **attested** chain is a declared member of the envelope it holds, and
    #    answered while the namespace was held. The caller supplied neither
    #    the answer nor the set the answer is drawn from -- an `admitted: bool`
    #    or a list of chains from a caller would be the label-as-authority
    #    failure §13k L.1 refused; the scope is the authority.
    if not terms.chain_admitted:
        raise CaptureRefused(
            ADMISSION_CHAIN_OUTSIDE_ENVELOPE,
            "the attested signal chain is not a declared member of the frozen "
            "envelope. A window from another instrument is a window from "
            "another experiment: it is not re-labelled, not held aside and "
            "not counted")

    # 8. the corpus sequence state for this stratum.
    if type(sequence) is not CapturedStratumSequence:
        raise CaptureRefused(
            ADMISSION_SEQUENCE_NOT_THIS_STRATUM,
            "the sequence state is a CapturedStratumSequence; got "
            f"{type(sequence).__name__}")
    if sequence.corpus_id != terms.corpus_id or sequence.stratum != stratum:
        raise CaptureRefused(
            ADMISSION_SEQUENCE_NOT_THIS_STRATUM,
            f"sequence state for {sequence.corpus_id}/{sequence.stratum} "
            f"presented for {terms.corpus_id}/{stratum}")
    # 3d. The sequence is bound to the ring lifetime whose indices it counts.
    # A sample index means nothing across two rings (section 5.26
    # single-lifetime capture), so a sequence restored for one lifetime never
    # authorises continuity for another lifetime's windows.
    if sequence.ring_lifetime_id != metadata["ring_lifetime_id"]:
        raise CaptureRefused(
            ADMISSION_RING_LIFETIME_MISMATCH,
            f"the sequence state belongs to ring lifetime "
            f"{sequence.ring_lifetime_id!r} and this window was attested "
            f"under {metadata['ring_lifetime_id']!r}")
    if sequence.accepted >= MINIMUM_WINDOWS_PER_STRATUM:
        raise CaptureRefused(
            ADMISSION_STRATUM_CAP_REACHED,
            f"{sequence.stratum} already holds {sequence.accepted} of "
            f"{MINIMUM_WINDOWS_PER_STRATUM}. The sample is fixed before it is "
            "collected: the next window is refused before anything is written, "
            "not trimmed afterwards")
    previous_last = sequence.previous_last_sample_index
    if (previous_last is not None
            and int(metadata["first_sample_index"]) < int(previous_last)):
        raise CaptureRefused(
            WINDOW_INTERVAL_OVERLAP,
            f"this window begins at {metadata['first_sample_index']} and the "
            f"stratum's previous window ended at {previous_last}. Two freshly "
            "issued window ids over identical retained samples differ in id, "
            "digest and timestamp and not in index")

    # 9. the payload digest, taken while the scope is live and before anything
    #    is created. §5.20 writes the header first, so this cannot be a
    #    by-product of the write.
    #
    #    The empty prefix is written out rather than defaulted: the same action
    #    returns `file_sha256` when handed the framing prefix and the header,
    #    and both are 64 hex characters, so a caller that received the wrong
    #    one would carry it into the header unnoticed. The `file_sha256` caller
    #    arrives with the publication intent, which binds it before creation.
    payload_sha256 = scope._prefixed_sha256(b"")

    header = derive_canonical_header(
        metadata=metadata, ownership=terms, stratum=stratum,
        attestation=attestation, sequence=sequence,
        payload_sha256=payload_sha256)
    check_header_completeness(header, stratum)
    return metadata, header, payload_sha256


def _write_all(fd: int, data: bytes) -> int:
    """Write every byte of `data`, completing partial writes. Returns the count."""
    total = 0
    while total < len(data):
        written = os.write(fd, data[total:])
        if written <= 0:
            raise PublicationFailed(
                PUBLICATION_FRAMING_WRITE_INCOMPLETE,
                f"wrote {total} of {len(data)} framing bytes and then stalled")
        total += written
    return total


def _publish(*, fd: Any, scope: AttestedIQWindowScope, header: Mapping[str, Any],
             header_bytes: bytes, prefix: bytes, metadata: Mapping[str, Any],
             stratum: str, corpus_id: str,
             payload_sha256: str) -> CapturedWindowPublication:
    """§5.20 steps 3 (already done by the caller) and 4, and the reconciliation.

    Everything here is post-open. Nothing refuses with `CaptureRefused`, and
    nothing unlinks: an orphan temporary is never a corpus member and is kept
    deliberately, so removing it would be this module contradicting §5.20 in
    order to sound stronger.
    """
    if type(fd) is not int:
        raise PublicationFailed(
            PUBLICATION_TARGET_NOT_A_DESCRIPTOR,
            f"create_target returned {type(fd).__name__}, not a descriptor")

    framing = prefix + header_bytes
    framing_written = _write_all(fd, framing)
    if framing_written != len(framing):
        raise PublicationFailed(
            PUBLICATION_FRAMING_WRITE_INCOMPLETE,
            f"wrote {framing_written} of {len(framing)} framing bytes")

    payload_written = scope._write_payload_to_fd(fd)

    # The three quantities, from three independent places: the header's
    # declaration, the ring's authoritative record, and what the write
    # returned. Each side is independently mutable, so a change to one and not
    # the others refuses here.
    #
    # This proves FRAMING. It does not prove content identity: a window of the
    # right length carrying the wrong samples satisfies the equation exactly.
    # Content identity comes from the attested scope and its recomputed digest
    # (§5.24's nine checks) and from §5.20 step 8's readback of `payload_sha256`
    # and `file_sha256`. Both are required and neither substitutes for the other.
    declared = header["declared_payload_bytes"]
    attested = scope.sample_count * BYTES_PER_SAMPLE
    if not (declared == attested == payload_written):
        raise PublicationFailed(
            PUBLICATION_LENGTH_MISMATCH,
            f"the header declares {declared} bytes, the attested record makes "
            f"it {attested}, and the write completed {payload_written}")

    return CapturedWindowPublication(
        corpus_id=corpus_id,
        stratum=stratum,
        window_id=metadata["window_id"],
        first_sample_index=int(metadata["first_sample_index"]),
        last_sample_index=int(metadata["last_sample_index"]),
        declared_payload_bytes=int(declared),
        payload_bytes_written=int(payload_written),
        framing_bytes_written=int(framing_written),
        payload_sha256=payload_sha256,
        header_bytes=header_bytes,
        framing_prefix_bytes=prefix,
    )


def _record(*, scope: Any, stratum: str, attestation: Any, corpus: Any,
            sequence: Any, create_target: Any) -> CapturedWindowPublication:
    """Admit, then create, then write. The order is the contract.

    `create_target` is invoked **once**, after `_admit` has returned. It is the
    caller's §5.20 step 3 -- an exclusive `0600` temporary sibling -- and this
    module does not implement it, because the corpus namespace is unbuilt and
    creating one is not authorised here. Passing it in is also what makes the
    ordering observable: a test counts the calls, and an admission check moved
    below this line is caught by a creator that ran.
    """
    # A precondition, not a post-open failure: a creator that cannot be called
    # never creates anything, so the refusal belongs in the regime where
    # nothing exists. The two vocabularies stay apart even here.
    if not callable(create_target):
        raise CaptureRefused(
            ADMISSION_CREATOR_NOT_CALLABLE,
            "create_target is the caller's §5.20 step 3 and must be callable; "
            f"got {type(create_target).__name__}")

    metadata, header, payload_sha256 = _admit(
        scope=scope, stratum=stratum, attestation=attestation, corpus=corpus,
        sequence=sequence)
    try:
        header_bytes = canonical_header_bytes(header)
        prefix = framing_prefix(header_bytes)
    except FramingRefused as exc:
        raise CaptureRefused(
            ADMISSION_CANONICAL_FORM_REFUSED,
            f"the derived header cannot be framed: {exc}") from exc

    fd = create_target()

    return _publish(fd=fd, scope=scope, header=header,
                    header_bytes=header_bytes, prefix=prefix,
                    metadata=metadata, stratum=stratum,
                    corpus_id=header["corpus_id"],
                    payload_sha256=payload_sha256)


# -- the typed capture boundary ---------------------------------------------
#
# §5.20: do not expose `write_window(window, stratum)`. A generic writer with a
# label argument is the hole §13k L.1 refused in the derived-evidence producer,
# for the same reason -- the label becomes a caller's claim rather than a
# control path's attestation. Three nominal entrypoints instead, each fixing
# its own stratum and taking its event attestation from the control path that
# produced it. There is no `source` parameter either: `CAPTURED` is the file
# format's declaration, not a caller's.


def record_gain_step(*, scope: Any, attestation: Any, corpus: Any,
                     sequence: Any, create_target: Any
                     ) -> CapturedWindowPublication:
    """Publish the first complete window after a `GAIN_CHANGE`. §5.20, §5.25.

    3c-wire: `(scope, attestation, corpus, sequence, create_target)`. `scope`
    is the attested window scope the ring minted; `corpus` is the ownership
    scope the namespace minted. There is no `lock` and no `now`: the corpus
    terms and the clock are the ownership scope's, and a caller passing
    either gets a `TypeError` rather than a second authority.
    """
    return _record(scope=scope, stratum="GAIN_STEPS", attestation=attestation,
                   corpus=corpus, sequence=sequence,
                   create_target=create_target)


def record_retune_transient(*, scope: Any, attestation: Any, corpus: Any,
                            sequence: Any, create_target: Any
                            ) -> CapturedWindowPublication:
    """Publish the first complete window after a `RETUNE`. §5.20, §5.25."""
    return _record(scope=scope, stratum="RETUNE_TRANSIENTS",
                   attestation=attestation, corpus=corpus,
                   sequence=sequence, create_target=create_target)


def record_receiver_spur(*, scope: Any = None, attestation: Any = None,
                         corpus: Any = None, sequence: Any = None,
                         create_target: Any = None
                         ) -> CapturedWindowPublication:
    """Refuses by construction. `RECEIVER_SPURS` has no attestation member.

    Not a check somebody could relax: the union is closed and the stratum has
    no constructible member, so there is nothing to pass. §5.21 supplies an
    identification protocol and §5.22 its parameters, and neither has been
    run -- no catalogue exists, no feasibility check has been made against an
    actual S, and a method nobody has run identifies nothing.

    It exists rather than being absent so that calling it is a refusal with a
    reason rather than an `AttributeError`.
    """
    raise CaptureRefused(
        ADMISSION_ATTESTATION_UNCONSTRUCTIBLE,
        "RECEIVER_SPURS has no constructible attestation member, so no header "
        "can be produced for it. Having a receiver attached is not evidence "
        "that a feature is an internal spur")


def admission_status() -> Dict[str, Any]:
    """What this boundary is, for a status surface. No corpus, no filesystem."""
    return {
        "schema": SCHEMA,
        "captured_strata": list(CAPTURED_STRATA),
        "strata_with_no_attestation_member": [
            key for key, member in sorted(STRATUM_ATTESTATION.items())
            if member is None],
        "header_authorities": [a.name for a in HEADER_AUTHORITIES],
        "retention_maximum_days": RETENTION_MAXIMUM_DAYS,
        "cap_per_stratum": MINIMUM_WINDOWS_PER_STRATUM,
        "forbidden_header_field": FILE_DIGEST_FIELD_FORBIDDEN_IN_HEADER,
        "publication_steps_implemented": "5.20 STEPS 1-4 AND RECONCILIATION",
        "publication_steps_unbuilt": "5.20 STEPS 5-8",
        "creates_nothing": True,
        # 3c-wire. The entrypoints consume the ownership scope and take no
        # lock and no timestamp. Consumption is not compulsion: nothing yet
        # obliges a production path through here, which is entry 14.
        "consumes_ownership_scope": True,
        # 3d. The sequence state is bound to the ring lifetime whose indices
        # it counts; admission refuses a sequence restored for another
        # lifetime, because continuity across two rings is not continuity.
        "sequence_ring_lifetime_bound": True,
        "accepts_caller_lock": False,
        "accepts_caller_timestamp": False,
        "required_header_fields": len(required_header_fields("GAIN_STEPS")),
    }
