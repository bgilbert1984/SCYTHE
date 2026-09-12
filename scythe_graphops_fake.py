"""A deterministic GraphOps, for proving the adapter against a known one.

§17 slice 9. Not a production component and never imported by one -- a test
asserts that. It exists so the conformance declaration (§13g H.7) can be
*executed* rather than believed: if this fake can satisfy the declaration while
behaving non-idempotently, the declaration is describing something weaker than
it claims.

So the conformance suite is adversarial. Each mutant below breaks one guarantee
the declaration makes, and the suite must fail for every one:

  creates twice for one operation identity
  accepts one operation identity with a different payload digest
  returns a receipt for an object it did not store
  stores an object and returns an unauthenticated or mismatched receipt
  reports authoritative absence through a weakly consistent lookup
  claims a mutation-free rejection after submission crossed the boundary

A fake that only behaves correctly tests that the adapter accepts correctness.
The mutants are what test that it refuses everything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Dict, Optional, Tuple

from scythe_graphops_adapter import (
    RESULT_ABSENT, RESULT_ALREADY_EXISTS, RESULT_CREATED, RESULT_REJECTED,
    SubmissionState, Transport,
)

# Each mutant names the guarantee it breaks.
CREATES_TWICE = "CREATES_TWICE"
ACCEPTS_DIGEST_CHANGE = "ACCEPTS_DIGEST_CHANGE"
RECEIPT_WITHOUT_STORING = "RECEIPT_WITHOUT_STORING"
UNAUTHENTICATED_RECEIPT = "UNAUTHENTICATED_RECEIPT"
MISMATCHED_RECEIPT = "MISMATCHED_RECEIPT"
WEAK_ABSENCE_AS_AUTHORITATIVE = "WEAK_ABSENCE_AS_AUTHORITATIVE"
REJECTION_AFTER_SUBMISSION = "REJECTION_AFTER_SUBMISSION"
MUTANTS: Tuple[str, ...] = (
    CREATES_TWICE, ACCEPTS_DIGEST_CHANGE, RECEIPT_WITHOUT_STORING,
    UNAUTHENTICATED_RECEIPT, MISMATCHED_RECEIPT,
    WEAK_ABSENCE_AS_AUTHORITATIVE, REJECTION_AFTER_SUBMISSION,
)


@dataclass
class FakeGraphOps:
    """Objects by operation identity, and receipts about them.

    `objects` is the whole graph. A receipt this fake returns without a
    corresponding entry is the RECEIPT_WITHOUT_STORING mutant, and the test that
    catches it reads this dict rather than the receipt.
    """

    mutant: Optional[str] = None
    reject_code: Optional[str] = None
    objects: Dict[str, str] = field(default_factory=dict)
    creations: Dict[str, int] = field(default_factory=dict)

    def submit(self, command: Dict[str, Any]) -> Dict[str, Any]:
        operation = command["promotion_operation_id"]
        digest = command["payload_digest"]

        if self.reject_code is not None:
            return self._receipt(operation, digest, RESULT_REJECTED,
                                 rejection_code=self.reject_code)

        if operation in self.objects:
            stored = self.objects[operation]
            if self.mutant == ACCEPTS_DIGEST_CHANGE:
                self.objects[operation] = digest
                stored = digest
            if self.mutant == CREATES_TWICE:
                self.creations[operation] = self.creations.get(operation, 0) + 1
                return self._receipt(operation, digest, RESULT_CREATED)
            return self._receipt(operation, digest, RESULT_ALREADY_EXISTS,
                                 stored_digest=stored)

        if self.mutant == RECEIPT_WITHOUT_STORING:
            return self._receipt(operation, digest, RESULT_CREATED)
        if self.mutant == WEAK_ABSENCE_AS_AUTHORITATIVE:
            return self._receipt(operation, digest, RESULT_ABSENT)

        self.objects[operation] = digest
        self.creations[operation] = self.creations.get(operation, 0) + 1
        return self._receipt(operation, digest, RESULT_CREATED)

    def _receipt(self, operation, digest, result, *, stored_digest=None,
                 rejection_code=None) -> Dict[str, Any]:
        if self.mutant == MISMATCHED_RECEIPT:
            operation = "0" * 64
        return {
            "operation_id": operation,
            "payload_digest": digest,
            "result": result,
            "authentication": ("INVALID" if self.mutant == UNAUTHENTICATED_RECEIPT
                               else "VALID"),
            "stored_digest": stored_digest,
            "rejection_code": rejection_code,
        }

    def stored_count(self, operation: str) -> int:
        return self.creations.get(operation, 0)


@dataclass
class FakeTransport(Transport):
    """Carries the command to the fake and records the boundary honestly."""

    graph: FakeGraphOps = field(default_factory=FakeGraphOps)
    attests_submission_boundary: bool = True
    fail_before_submission: bool = False
    fail_after_submission: bool = False
    fail_after_response: bool = False
    oversized_response: bool = False

    def submit(self, command: bytes, state: SubmissionState) -> bytes:
        if self.fail_before_submission:
            # No byte left. The only innocence the contract accepts, and only
            # because this transport attests the boundary.
            raise ConnectionRefusedError("refused before submission")
        state.began()
        if self.fail_after_submission:
            raise TimeoutError("no answer after submission began")
        payload = self.graph.submit(json.loads(command.decode("utf-8")))
        state.received()
        if self.fail_after_response:
            raise ConnectionResetError("lost after the response")
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        if self.oversized_response:
            raw = raw + b" " * 100_000
        return raw


class UninstrumentedTransport(Transport):
    """Fails without saying whether anything left.

    Its `attests_submission_boundary` stays False, so its silence is UNKNOWN.
    A transport that does not instrument the boundary cannot infer innocence
    from an empty response.
    """

    attests_submission_boundary = False

    def submit(self, command: bytes, state: SubmissionState) -> bytes:
        raise ConnectionRefusedError("refused, and nothing was recorded")
