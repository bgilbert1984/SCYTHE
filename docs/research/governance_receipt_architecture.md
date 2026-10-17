# Governance receipt architecture — a FusionAuth-backed approval layer for operator declarations

**Status: design proposal only. Not implemented. Does not change any merged protocol, any running
request, or any authorization state.** In particular it does not alter attempt
`GATED3-20261016T011954Z-7dc517`, the frozen Gate D request `GATE_D_REQUEST.PROPOSED_NOT_AUTHORIZED.md`,
or the `gate-c-operator-declaration/1` schema used by that attempt. This document captures a
2026-10-16 design discussion about a *future* identity/approval layer that could sit in front of
the operator-declaration mechanism this repository already uses — nothing here is a precondition
for, or a substitute for, the manual confirmation flow Gate C/Gate D currently require.

## 1. Motivation

Gate C and Gate D (`metamorphic_r4_gate_c_preflight.md`, `metamorphic_r4_gate_d_protocol.md`)
require an *operator declaration* — a small signed-in-intent-only JSON document
(`gate-c-operator-declaration/1`) asserting that no maintenance, reboot, or reclaim of the replay
host is planned during a stated window, plus a package digest and a host identifier. Today, the
declaration is drafted as a file, and its SHA-256 is confirmed by the operator typing the exact
digest back in conversation. That is deliberately strict (see the project's standing rule that
only a direct, first-person, present-tense statement naming the exact digest counts as
confirmation — never something the assistant infers or supplies itself) but it is also repetitive:
every attempt, and every declaration replacement within an attempt, requires the same manual
round-trip.

`docs/FusionAuthSSO.md` proposed using FusionAuth (OIDC) to authenticate the operator and capture
approval, instead of relying on chat-based digest confirmation alone. The design that follows is
the result of pressure-testing that idea: where should identity-proof live, how does it interact
with the existing digest-based custody chain, and what does it need to guarantee to be worth
building.

## 2. Four trust domains, none with unilateral authority

The core property this design preserves is that **no single component can both authenticate,
approve, validate, and execute**:

| Layer | Responsibility | Answers |
|---|---|---|
| Identity (FusionAuth, OIDC) | Authenticate the human; verify role/MFA | "Who approved this?" |
| Governance Service | Bind an approval to immutable artifact hashes and a policy version | "What exactly was approved, under which rules?" |
| Gate C Validator | Independently verify the declaration, the receipt, and host state | "Does reality satisfy the protocol?" |
| Gate D Launcher | Execute only once every prior gate has passed | "May execution proceed?" |

Concretely:
- FusionAuth cannot decide that the replay host is in the required maintenance state.
- Gate C cannot authenticate the operator — it only ever sees artifacts, never a live identity call.
- Gate D cannot manufacture authentication or validation; it still only runs after Gate C passes.
- The Governance Service cannot silently alter a declaration or request — it only ever references
  them by hash, so a reference is either exactly right or it fails verification.

```
               ┌──────────────────────┐
               │ FusionAuth (OIDC)    │
               │ Identity + MFA       │
               └──────────┬───────────┘
                          │ ID Token
                          ▼
                 ┌─────────────────┐
                 │ Governance API  │
                 │                 │
                 │ validates JWT   │
                 │ records approval│
                 │ immutable log   │
                 └───────┬─────────┘
                         │
                         │ signed receipt
                         ▼
            governance_receipt.json
                         │
                         ├──────────────┐
                         ▼              ▼
      operator_declaration.json   append-only audit log

                         │
                         ▼

                 Gate C validator
           (unchanged enforcement)
```

## 3. The declaration schema stays untouched

`gate-c-operator-declaration/1` is not modified. Adding JWT/OIDC fields directly into it would
break its existing digest identity and would conflate "what was declared" with "who is vouching
for the declarer" — two different claims with different trust models. Instead, a **separate**
document carries the identity proof and references the declaration (and, per §4, the request) by
hash:

```jsonc
// governance-receipt/1 (conceptual; signed payload, not literal file contents)
{
  "receipt_schema": "governance-receipt/1",
  "policy_version": "gate-d-rev5",
  "verification_profile": "scythe-governance-1",
  "declaration_sha256": "...",
  "request_sha256": "...",
  "approved_by_sub": "...",        // OIDC subject — immutable identity anchor
  "approved_by_display": "...",    // convenience for humans only, never authoritative
  "approval_id": "...",
  "issued_at": "...",
  "oidc_jti": "...",               // token uniqueness / replay detection
  "kid": "..."                     // signing key identifier, for rotation
}
```

Gate C's policy then becomes: require a matching governance receipt whose signature verifies,
*in addition to* the existing declaration checks it already performs — without ever modifying the
declaration schema or the existing digest-based custody chain.

## 4. Bind the declaration **and** the request together

An early version of this design signed only `declaration_sha256`. That is a weaker guarantee than
it looks: an approval of the declaration alone says "this person approved this declarer's
identity," not "this person approved this exact execution plan." A declaration could, in
principle, later be paired with a different or stale Gate D request document without
re-approval.

Signing `declaration_sha256` **and** `request_sha256` together closes that gap. The approval
becomes "this person approved this declaration *for this exact* execution plan," which is the
correct unit of approval for something that triggers real command execution on a real host.

```
Operator Declaration ──▶ SHA-256 A ─┐
                                     ├──▶ Governance Receipt (signs A + B together)
     Gate D Request    ──▶ SHA-256 B ─┘
```

## 5. Gate C stays deliberately ignorant of FusionAuth

Gate C should never call FusionAuth, or any live identity service, directly:

```
FusionAuth → Governance Service → Signed Receipt → Gate C
```

Gate C's only required inputs are:
- the receipt schema version,
- the signature algorithm,
- a trusted public key set.

This means Gate C keeps operating correctly even if FusionAuth is unavailable, as long as a
receipt was already issued — and it keeps Gate C's test surface to fixed artifacts (declaration,
request, receipt, trust store) rather than a live identity dependency.

## 6. Cryptographic specification

Rather than leaving "a signed JSON document" to each implementation's own judgment, the format is
specified precisely enough to be an interoperability target:

- **Payload canonicalization:** RFC 8785 (JSON Canonicalization Scheme, JCS) — the signature covers
  the canonicalized payload, not "the JSON" as originally serialized, so two semantically identical
  receipts with different key ordering or whitespace must still produce the same signature.
- **Digest:** SHA-256 (consistent with every other artifact in this project's custody chain).
- **Signature:** Ed25519.
- **Receipt schema:** `governance-receipt/1`.

Verification becomes a deterministic algorithm, specified in the protocol rather than left to each
implementation:

1. Parse JSON.
2. Validate against `governance-receipt/1`.
3. Canonicalize via RFC 8785.
4. Verify the Ed25519 signature using the public key identified by `kid`.
5. Verify `declaration_sha256` matches the declaration under review.
6. Verify `request_sha256` matches the frozen request under review.
7. Verify `policy_version` is one Gate C is configured to accept.
8. Produce PASS/FAIL with an explicit reason — never a bare boolean.

## 7. Three version axes that evolve independently

- `receipt_schema` — the wire format of the receipt itself.
- `policy_version` — which governance rules were in force at approval time (e.g. `gate-d-rev5`),
  so a protocol revision later cannot be retroactively applied to an old approval.
- `verification_profile` — which verifier implementation/configuration checked it.

Separating these means a protocol revision doesn't force a receipt-format change, a receipt-format
revision doesn't force a policy change, and a verifier upgrade doesn't force either.

## 8. Trust anchor as its own versioned artifact

Governance public keys are their own independently versioned trust store, not a FusionAuth or
Governance Service runtime detail:

```
trust_store/
    governance_keys.json
    governance_keys.sig
```

```jsonc
{
  "version": 3,
  "keys": [
    {"kid": "2026-q4-ed25519", "algorithm": "Ed25519", "public_key": "...", "status": "active"},
    {"kid": "2026-q2-ed25519", "algorithm": "Ed25519", "public_key": "...", "status": "retired"}
  ]
}
```

This gives Gate C a stable trust anchor independent of both FusionAuth's and the Governance
Service's runtime availability, and lets signing keys rotate (via `kid`) without invalidating
historical receipts.

## 9. Offline verification

Given only five static artifacts — the package, the declaration, the Gate D request, the
governance receipt, and the trusted public keys — a third party can reproduce every governance
decision entirely offline, with no access to FusionAuth, Dedirock, SCYTHE's own infrastructure,
GitHub, or any database. This is the property most worth preserving in any real implementation:
it is what makes the governance trail independently auditable years later, not just at the moment
of approval.

## 10. Future extensibility without touching Gate C's validation logic

Because the receipt is evidence over fixed hashes rather than a live decision, the same model
extends to:
- multiple independent signatures over the same declaration/request pair,
- threshold approval policies (e.g. 2-of-3 approvers, or role-based: "Research Lead" + "Operator"),
- expiration or revocation metadata, enforced by governance policy rather than Gate C itself,
- hardware-backed signing (WebAuthn / security keys),
- external auditors issuing additional attestations over the same artifacts.

Each of these is an added governance concern, not a modification to the execution validator.

## 11. Relationship to the existing protocol

This design does not replace or amend `metamorphic_r4_gate_c_preflight.md` or
`metamorphic_r4_gate_d_protocol.md`. If built, it would sit in front of the existing
`gate-c-operator-declaration/1` mechanism as an optional identity/approval layer: Gate C's
enforcement point gains one additional check (a valid, matching governance receipt) without any
change to the declaration schema, the RECHECK mechanism, the retrieval custody tooling, or any
other control those documents already define. Building it is explicitly **not required** to adopt
any of this — the current manual declaration/confirmation flow remains fully valid on its own.

## 12. Threat model — what this does not protect against

Making the receipt mechanism's security boundary explicit, rather than leaving it implied:

- It does **not** attest that the host was actually in the declared maintenance state. It attests
  only that an authenticated identity approved a declaration that *claims* that state.
- It does **not** prove execution occurred, or occurred as described. It is an approval artifact,
  not an execution record.
- It does **not** replace Gate C validation. Gate C still independently checks the declaration's
  own fields, host state, and environment; the receipt adds one more check, it does not subsume
  the others.
- It does **not** replace Gate D authorization. A valid receipt means a declaration was approved
  for a specific request — it is not itself the bounded, explicit authorization to execute that
  this project's standing practice requires (see [`scythe-gate-authorization-strictness`] in
  project memory, and the GATED3 request's own authorization-by-reference mechanism).
- It does **not** prevent an operator from approving an incorrect or false declaration. The
  signature proves *who* approved it, not that what they approved was true. A compromised or
  careless operator identity can still approve a bad declaration; the receipt makes that act
  attributable and auditable, not impossible.
- It does **not** detect or prevent a compromised signing key from issuing a valid-looking receipt
  for a declaration/request pair the real operator never saw. Key custody (§14) is a prerequisite
  this document assumes, not something the receipt format itself guarantees.

## 13. Trust assumptions

For the verification algorithm in §6 to mean what it claims to mean, the following must hold —
documented explicitly so a later security review can check each one rather than inferring them:

- The FusionAuth identity asserted by `approved_by_sub` is itself trustworthy — i.e. FusionAuth's
  own authentication (including MFA, if configured) has not been bypassed or spoofed.
- Governance signing keys are protected; a private key's confidentiality is what makes `kid`-based
  verification meaningful at all.
- The verifier (Gate C) trusts exactly the public keys listed, at the status (`active`/`retired`),
  in the trust store it is configured to read — not some other key set.
- SHA-256 remains collision-resistant for custody purposes, consistent with every other digest in
  this project's evidence chain.
- The canonicalization algorithm is fixed to RFC 8785 (JCS); any verifier that canonicalizes
  differently will compute a different signature input and reject a legitimate receipt, or (worse,
  if implemented loosely) accept a tampered one.
- `oidc_jti` is actually checked for uniqueness by the Governance Service at issuance time, to make
  the stated replay-detection property real rather than aspirational.

## 14. Future evolution — a general artifact-approval pattern, not a Gate D special case

Nothing about §§2–9 is specific to replay execution. The same binding — a signed receipt over two
(or more) artifact hashes, under a policy version, verifiable offline against a versioned trust
store — could govern approvals for evidence publication, protocol revisions, dataset freezes,
benchmark baselines, release candidates, or paper submission packages, with the same
`governance-receipt/1` schema in each case rather than a proliferation of receipt types per use
case. This document only specifies the Gate D use case; generalizing it is future work, not implied
by anything merged here.

## 15. Current status

No code or protocol changes are implied by this document. This design discussion does not
constitute, and should not be read as, a Gate D execution authorization for any attempt, including
`GATED3-20261016T011954Z-7dc517`.
