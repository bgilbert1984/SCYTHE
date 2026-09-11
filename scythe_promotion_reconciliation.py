"""What an operator may do about a reservation nobody can settle.

§17 slice 7, implementing PROMOTION_EXECUTION_CONTRACT.md §13e F.1-F.3 and F.7.
`RETRY_REQUIRES_OPERATOR` has named a repair since Amendment C; this is the
repair.

Two operations, and the difference between them is whether the *ledger* is
healthy:

  **Reconciling an identity** appends to the current generation. One reservation
  was uncertain and the ledger is fine.

  **Closing a generation** is for when the ledger cannot be used -- torn,
  unreadable, or at a ceiling. It publishes a successor and never touches the
  predecessor, which falls out of §13c D.2 rather than being chosen: a torn
  ledger cannot be appended to, so supersession is the only path that exists.

Nothing here promotes anything or calls an adapter. `RECONCILED_RELEASED` makes
an identity promotable again; whether it is promoted is a later evaluation's
decision, under the budget like any other.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Dict, Optional, Tuple

from scythe_promotion_ledger_store import (
    COMMITTED, FAILED, FRAME_VERSION, HEADER, LEDGER_SCHEMA,
    RECONCILED_COMMITTED, RECONCILED_RELEASED, RESERVED, frame_of,
)
from scythe_promotion_lineage import Generation, Lineage, LineageError

SCHEMA = "scythe.promotion-reconciliation.v1"

NOT_RECONCILABLE = "NOT_RECONCILABLE"

# -- what an operator may say, and nothing else (§13e F.7) ----------------
#
# A closed set. There is no notes field, and there will not be one: this is the
# record of a human decision about evidence, and free text is where the
# reasoning goes to stop being checkable. The same rule §13a B.2 applies to
# adapter output, for a stronger reason.
GRAPH_RECORD_FOUND = "GRAPH_RECORD_FOUND"
GRAPH_RECORD_NOT_FOUND = "GRAPH_RECORD_NOT_FOUND"
ADAPTER_DENIED_CREATION = "ADAPTER_DENIED_CREATION"
OPERATOR_EVIDENCE: Tuple[str, ...] = (GRAPH_RECORD_FOUND,
                                      GRAPH_RECORD_NOT_FOUND,
                                      ADAPTER_DENIED_CREATION)

LEDGER_TORN = "LEDGER_TORN"
LEDGER_UNREADABLE = "LEDGER_UNREADABLE"
CEILING_REACHED = "CEILING_REACHED"
CLOSURE_REASONS: Tuple[str, ...] = (LEDGER_TORN, LEDGER_UNREADABLE,
                                    CEILING_REACHED)

GENERATION_CLOSED = "GENERATION_CLOSED"


class ReconciliationRefused(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class OperatorRequest:
    """Who is asking, and which asking this is.

    `request_id` exists so a repeat can be *recognised* rather than merely
    refused. An operator who lost a response and retried should be told *this
    already happened*.
    """

    operator: str
    request_id: str

    def __post_init__(self) -> None:
        for field_name in ("operator", "request_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or len(value) > 64:
                raise ReconciliationRefused(
                    NOT_RECONCILABLE,
                    f"{field_name} must be a non-empty string of at most 64 "
                    f"characters; it is an identifier, not a place to explain")


def reconcile_identity(session, read, identity: str, *, evidence: str,
                       request: OperatorRequest) -> Dict[str, Any]:
    """Append a reconciliation record for one identity (§13e F.1, F.2).

    The authority split is enforced here rather than described:

    - a `FAILED` reservation is released on the adapter's own attestation
      (`ADAPTER_DENIED_CREATION`), because `NOT_CREATED` already means no record
      was created;
    - an unresolved one is released only on an operator's inspection of the
      graph, because nothing in this system knows and no amount of ledger
      reasoning will produce the answer.

    Passing the wrong evidence for the state is refused, not accepted with a
    note. A release recorded on the wrong authority is indistinguishable from a
    correct one later, which is exactly when it matters.
    """
    if evidence not in OPERATOR_EVIDENCE:
        raise ReconciliationRefused(
            NOT_RECONCILABLE,
            f"{str(evidence)[:32]!r} is not operator evidence; the set is closed")

    state, seq = _state_of(read, identity)
    if state is None:
        raise ReconciliationRefused(
            NOT_RECONCILABLE,
            "this generation holds no reservation for that identity")
    if state == COMMITTED:
        # §13e F.1. Not an error of authority but of subject: there is no
        # uncertainty to resolve, and a ledger that let this proceed would be
        # recording a decision about a settled fact.
        raise ReconciliationRefused(
            NOT_RECONCILABLE,
            "the record exists; there is nothing for an operator to discover")

    if state == FAILED and evidence != ADAPTER_DENIED_CREATION:
        raise ReconciliationRefused(
            NOT_RECONCILABLE,
            "a definitely-failed reservation is released on the adapter's "
            "NOT_CREATED attestation, which the ledger already holds")
    if state == RESERVED and evidence == ADAPTER_DENIED_CREATION:
        raise ReconciliationRefused(
            NOT_RECONCILABLE,
            "the adapter never attested anything about this reservation; "
            "releasing it requires an operator's inspection of the graph")

    kind = (RECONCILED_COMMITTED if evidence == GRAPH_RECORD_FOUND
            else RECONCILED_RELEASED)
    written = session.append_reconciliation(kind, seq, {
        "evidence": evidence,
        "operator": request.operator,
        "request_id": request.request_id,
    })
    return {"schema": SCHEMA, "kind": kind, "identity": identity,
            "reserves": seq, "seq": written, "evidence": evidence,
            "operator": request.operator, "request_id": request.request_id}


def successor_header(generation: str, owner: Dict[str, Any],
                     predecessor: Generation, *, reason: str,
                     request: OperatorRequest) -> bytes:
    """The one record that makes a successor real (§13e F.4).

    Carries the predecessor's length and digest, so the predecessor is not
    merely preserved by convention: a later reader can prove it has not changed,
    and one that *has* is detectable rather than quietly authoritative.
    """
    if reason not in CLOSURE_REASONS:
        raise ReconciliationRefused(
            NOT_RECONCILABLE,
            f"{str(reason)[:32]!r} is not a closure reason; the set is closed")
    return frame_of({
        "kind": HEADER, "seq": 0, "schema": LEDGER_SCHEMA,
        "frame_version": FRAME_VERSION, "generation": generation,
        "owner": dict(owner),
        "supersedes": {
            "generation": predecessor.identifier,
            "path": os.path.basename(predecessor.path),
            "bytes": os.path.getsize(predecessor.path),
            "digest": digest_of(predecessor.path),
            "closed_because": reason,
            "closed_by": request.operator,
            "request_id": request.request_id,
        },
    })


def digest_of(path: str) -> str:
    import hashlib

    handle = hashlib.blake2s(digest_size=16)
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            handle.update(block)
    return f"blake2s:{handle.hexdigest()}"


def close_generation(lineage: Lineage, owner: Dict[str, Any], *,
                     generation: str, reason: str, request: OperatorRequest,
                     nonce: str) -> Dict[str, Any]:
    """Publish a successor, or report the one this request already published.

    Rediscovery comes first and creates nothing (§13e F.6). The predecessor is
    never opened for writing, whatever its state.
    """
    existing = lineage.find_by_request(request.request_id)
    if existing is not None:
        return {"schema": SCHEMA, "outcome": GENERATION_CLOSED,
                "rediscovered": True, "path": existing.path,
                "generation": existing.identifier,
                "request_id": request.request_id}
    predecessor = lineage.authoritative()
    lineage.find_successor(predecessor, request.request_id)
    header = successor_header(generation, owner, predecessor, reason=reason,
                              request=request)
    path = lineage.publish(predecessor, header,
                           request_id=request.request_id, nonce=nonce)
    return {"schema": SCHEMA, "outcome": GENERATION_CLOSED,
            "rediscovered": False, "path": path, "generation": generation,
            "closed": predecessor.identifier, "closed_because": reason,
            "request_id": request.request_id}


def _state_of(read, identity: str):
    """The identity's stored state in this generation, and its reservation seq."""
    from scythe_promotion_ledger_store import parse_frame

    seq = None
    with open(read.path, "rb") as handle:
        data = handle.read()
    lines = data.split(b"\n")
    complete = lines[:-1]
    for line in complete:
        try:
            payload = parse_frame(line)
        except Exception:
            break
        if payload.get("kind") == RESERVED and payload.get("identity") == identity:
            seq = payload.get("seq")
    if seq is None:
        return None, None
    if identity in read.committed:
        return COMMITTED, seq
    if identity in read.write_failed:
        return FAILED, seq
    if identity in read.released:
        return RECONCILED_RELEASED, seq
    return RESERVED, seq
