"""§5.20 3d's compulsion controls: the side routes to membership, each closed.

Group `D`. Entry 14 drains only when admission is not one path to corpus
membership but the only path, and a claim that broad is worth no more than the
proof that no way around it survives. Each control here is a mutation that opens
one side route -- a forged admission ticket, a direct commit, a bypassed cap, a
window from a dead ring, a member the journal never recorded, a hand-built
verified final, a committed record the disk does not support -- and each must be
killed by a test that already stands in the suite. A control that no test
catches is a route left open, which is exactly what this group exists to deny.

Mirrors `controls_3c` in shape: `CONTROLS` is a list of
`(id-and-prose, [(file, before, after), ...])`, applied to the tree before the
suite runs. `WITNESS` names, per control, the test whose failure is the kill --
read by `verify_d_series.py`, which proves each control without the full sweep.
"""

import collections

A = "rf_capture_admission.py"
N = "rf_corpus_namespace.py"
P = "rf_capture_publication.py"
R = "rf_membership_recovery.py"

ACCEPTED_IN_SCOPE = ()
ACCEPTED_DEFERRED = {}

EARNED_IDS = frozenset("D%d" % n for n in range(1, 8))

# No control here is declared broad: each opens exactly one route and is killed
# by the one test written for that route. If a mutation ever disturbs more than
# its own witness, that is a finding, not a declaration to be made in advance.
BROAD_DECLARED = {}
SUBSUMED = {}
INTRODUCES = {}

# Per control, the test whose failure proves the route is closed. The method
# name alone is unique across the suite, which is what `verify_d_series.py`
# selects on.
WITNESS = {
    "D1": ("test_rf_capture_admission.py",
           "test_a_caller_cannot_mint_a_window_admission"),
    "D2": ("test_rf_capture_admission.py",
           "test_commit_window_refuses_anything_but_an_admission"),
    "D3": ("test_rf_capture_admission.py",
           "test_the_cap_refuses_the_next_window_before_anything_is_written"),
    "D4": ("test_rf_capture_sequence.py",
           "test_admit_refuses_a_window_from_another_lifetime"),
    "D5": ("test_rf_membership_recovery.py",
           "test_a_member_no_intent_records_is_unaccounted"),
    "D6": ("test_rf_capture_publication.py",
           "test_a_verified_final_cannot_be_constructed"),
    "D7": ("test_rf_membership_recovery.py",
           "test_a_committed_intent_whose_final_is_gone_contradicts"),
}

CONTROLS = [
 # --- the capability: a caller cannot mint what commit_window requires ------
 ("D1 a caller may forge an admission ticket", [(A,
   "        if mint_key is not _ADMISSION_MINT_KEY:",
   "        if False:")]),

 # --- the gate: commit is reachable only through an admission ---------------
 ("D2 commit_window admits anything, not only an admission", [(N,
   "        if type(admission) is not WindowAdmission:",
   "        if False:")]),

 # --- the sequence the scope owns: the cap stops the next window ------------
 ("D3 the cap does not stop the next window", [(A,
   "    if sequence.accepted >= MINIMUM_WINDOWS_PER_STRATUM:",
   "    if False:")]),

 # --- single-lifetime: a window from a dead ring is not continuity ----------
 ("D4 a window from another ring lifetime is admitted", [(A,
   '    if sequence.ring_lifetime_id != metadata["ring_lifetime_id"]:',
   "    if False:")]),

 # --- recovery: a member the journal never recorded is not adopted ----------
 ("D5 a member no intent records is adopted on reopen", [(R,
   '        if entry.endswith(".iqc"):',
   "        if False:")]),

 # --- the verified final: step 8 alone mints one ---------------------------
 ("D6 a caller may construct a verified final", [(P,
   "        if mint_key is not _MINT_KEY:",
   "        if False:")]),

 # --- recovery: a COMMIT the disk does not support is refused ---------------
 ("D7 a committed window whose final is gone is adopted", [(R,
   "    if terminal == COMMIT:\n        if not outcome.verified:",
   "    if terminal == COMMIT:\n        if False:")]),
]


def check_inventory(accepted_ids=()):
    problems = []
    ids = [c[0].split()[0] for c in CONTROLS]
    counts = collections.Counter(ids)
    for cid, n in sorted(counts.items()):
        if n > 1:
            problems.append(f"{cid} appears {n} times")
    missing = sorted(EARNED_IDS - set(ids), key=lambda s: int(s[1:]))
    extra = sorted(set(ids) - EARNED_IDS, key=lambda s: int(s[1:]))
    if missing:
        problems.append(f"declared but absent: {missing}")
    if extra:
        problems.append(f"present but undeclared: {extra}")
    for cid in ids:
        if cid not in WITNESS:
            problems.append(f"{cid} names no witness test")
    return problems


try:                                                # pragma: no cover
    from controls527 import check_mutations         # noqa: E402  (shared checker)
except ImportError:                                 # standalone (verify script)
    def check_mutations(*_args, **_kwargs):
        return []
