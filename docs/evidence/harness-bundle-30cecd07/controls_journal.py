"""§5.26 3b-core's controls: the membership journal's mechanics.

Group `J`, all earned. The accepted table names the journal as a contract but
enumerates no mutation rows for it, so there is no accepted identifier to claim
and none is invented. `check_inventory` is called with an empty accepted set.
"""

import collections

J = "rf_membership_journal.py"
NS = "rf_corpus_namespace.py"

ACCEPTED_IN_SCOPE = ()
ACCEPTED_DEFERRED = {}

EARNED_IDS = frozenset("J%d" % n for n in range(1, 28))

CONTROLS = [
 # --- canonical form: the .iqe divergence reintroduced, and the three params -
 ("J1 the canonical form becomes .iqe's escaped one", [(J,
   "ensure_ascii=False, allow_nan=False,", "ensure_ascii=True, allow_nan=False,")]),
 ("J2 canonical JSON stops sorting its keys", [(J,
   "mapping, sort_keys=True, separators=(\",\", \":\"),",
   "mapping, sort_keys=False, separators=(\",\", \":\"),")]),
 ("J3 canonical JSON regains default spacing", [(J,
   "sort_keys=True, separators=(\",\", \":\"),\n            ensure_ascii=False",
   "sort_keys=True,\n            ensure_ascii=False")]),
 ("J4 canonical JSON accepts NaN and Infinity", [(J,
   "ensure_ascii=False, allow_nan=False,", "ensure_ascii=False, allow_nan=True,")]),

 # --- the schema, in both directions ----------------------------------------
 ("J5 a missing declared field is tolerated", [(J,
   "    missing = sorted(required - emitted)\n    if missing:",
   "    missing = sorted(required - emitted)\n    if False:")]),
 ("J6 an undeclared extra field is tolerated", [(J,
   "    extra = sorted(emitted - required)\n    if extra:",
   "    extra = sorted(emitted - required)\n    if False:")]),
 # BOTH enforcing paths. The first version mutated only `_check_record`'s
 # clause and discriminated nothing, because `required_fields` holds the same
 # closure one call later --- a control that applies cleanly and tests nothing,
 # which is the A3/B6 shape. A mutation leaving an equivalent guard intact does
 # not model the hazard it names.
 # J7 was a ZERO because the discriminant was closed TWICE: _check_record
 # refused an unknown record_type and then, on the very next line, called
 # required_fields, which refused the same value with the same code. Removing
 # either left the other holding, so no test could tell the difference, and the
 # two-patch control that forced a kill was papering over a production
 # redundancy rather than testing a property. The redundancy is gone; the
 # control now anchors on the single owning discriminant. The 4-space indent
 # distinguishes required_fields' refusal from the nominal gate's, which
 # carries the same message text.
 ("J7 the discriminant stops being closed", [(J,
   '    raise JournalRefused(\n        JOURNAL_RECORD_TYPE_REFUSED,\n'
   '        f"record_type {record_type!r} is not one of '
   '{\', \'.join(RECORD_TYPES)}")',
   "    return TERMINAL_FIELDS")]),
 ("J8 a terminal record may repeat the intent's bindings", [(J,
   'TERMINAL_FIELDS = frozenset(("record_type", "window_id"))',
   "TERMINAL_FIELDS = INTENT_FIELDS")]),
 ("J9 stored bytes are not re-checked for canonical form", [(J,
   "            if canonical_record_bytes(checked) != body:",
   "            if False:")]),

 # --- framing and reading ---------------------------------------------------
 ("J10 an over-bound length is read before it is refused", [(J,
   "            if length > IQJ_MAX_RECORD_BYTES:", "            if False:")]),
 ("J11 a digest disagreement anywhere is called a torn tail", [(J,
   "                if record_end >= file_size:\n                    torn = True\n"
   "                    break\n                raise JournalRefused(\n"
   "                    JOURNAL_NOT_CANONICAL,\n"
   '                    "a digest disagreement before the journal tail is "',
   "                if True:\n                    torn = True\n"
   "                    break\n                raise JournalRefused(\n"
   "                    JOURNAL_NOT_CANONICAL,\n"
   '                    "a digest disagreement before the journal tail is "')]),
 ("J12 an unparseable record anywhere is called a torn tail", [(J,
   "                if record_end >= file_size:\n                    torn = True\n"
   "                    break\n                raise JournalRefused(\n"
   "                    JOURNAL_NOT_CANONICAL,\n"
   '                    "an unparseable record before the journal tail is "',
   "                if True:\n                    torn = True\n"
   "                    break\n                raise JournalRefused(\n"
   "                    JOURNAL_NOT_CANONICAL,\n"
   '                    "an unparseable record before the journal tail is "')]),
 # J13 mutated the shared `_read_exact_or_tail` helper, so a clean EOF returned
 # b"" instead of None and every caller's `is None` check missed: n=104, a
 # detonation rather than a discrimination. The property lives at the ONE call
 # site that lacked accumulation, so this is now a verbatim revert of the
 # production defect --- `os.read` plus the short-prefix-is-torn check. Reverting
 # only the read would not reproduce the defect; it would hang the loop, because
 # `os.read` returns b"" at EOF and b"" is not None.
 ("J13 the length prefix is read without accumulation", [(J,
   '            prefix = _read_exact_or_tail(fd, IQJ_RECORD_LENGTH_BYTES)\n            if prefix is None:\n                # A clean end leaves nothing over; a partial prefix does. The\n                # helper cannot tell those apart and the file size can.\n                if valid_end != file_size:\n                    torn = True\n                break',
   '            prefix = os.read(fd, IQJ_RECORD_LENGTH_BYTES)\n            if not prefix:\n                break\n            if len(prefix) != IQJ_RECORD_LENGTH_BYTES:\n                torn = True\n                break')]),
 ("J14 a torn tail is left in place rather than truncated", [(J,
   "            os.ftruncate(fd, valid_end)\n            os.fsync(fd)",
   "            os.fsync(fd)")]),
 ("J15 the truncation is not made durable", [(J,
   "            os.ftruncate(fd, valid_end)\n            os.fsync(fd)",
   "            os.ftruncate(fd, valid_end)")]),
 ("J16 the length prefix becomes big-endian", [(J,
   'return (struct.pack("<I", len(body)) + body',
   'return (struct.pack(">I", len(body)) + body')]),
 ("J17 the record digest covers the framed bytes, not the body", [(J,
   'return (struct.pack("<I", len(body)) + body\n            + hashlib.sha256(body).digest())',
   'return (struct.pack("<I", len(body)) + body\n'
   '            + hashlib.sha256(struct.pack("<I", len(body)) + body).digest())')]),

 # --- append durability and ordering ----------------------------------------
 ("J18 the append is not fsynced", [(J,
   "        _write_all(fd, frame)\n        os.fsync(fd)",
   "        _write_all(fd, frame)")]),
 ("J19 the prospective state is validated after the write", [(J,
   "    next_state = _validated_state(\n"
   "        state.records + (checked,),\n"
   "        valid_bytes=state.valid_bytes + len(frame), torn=False,\n    )\n"
   "    fd = _open_journal(dir_fd, os.O_WRONLY | os.O_APPEND)\n    try:\n"
   "        _write_all(fd, frame)\n        os.fsync(fd)\n    finally:\n"
   "        os.close(fd)\n    return next_state",
   "    fd = _open_journal(dir_fd, os.O_WRONLY | os.O_APPEND)\n    try:\n"
   "        _write_all(fd, frame)\n        os.fsync(fd)\n    finally:\n"
   "        os.close(fd)\n    return _validated_state(\n"
   "        state.records + (checked,),\n"
   "        valid_bytes=state.valid_bytes + len(frame), torn=False,\n    )")]),
 ("J20 the append descriptor stops appending", [(J,
   "    fd = _open_journal(dir_fd, os.O_WRONLY | os.O_APPEND)",
   "    fd = _open_journal(dir_fd, os.O_WRONLY)")]),
 ("J21 a stalled write is accepted", [(J,
   "        if count <= 0:", "        if count < 0:")]),

 # --- reservation, budgets and the cap --------------------------------------
 ("J22 the terminal record is no longer reserved", [(J,
   "    if len(state.records) + 2 > IQJ_MAX_RECORDS:",
   "    if len(state.records) + 1 > IQJ_MAX_RECORDS:")]),
 ("J23 the abandonment budget stops being enforced on append", [(J,
   "    if state.abandoned_attempts >= MAX_ABANDONED_ATTEMPTS:",
   "    if False:")]),
 ("J24 commits stop being counted for the cap", [(J,
   "            committed[stratum] += 1", "            committed[stratum] += 0")]),
 # J25 first dropped the `terminals` term, which was an EQUIVALENT mutation:
 # terminals.keys() <= intents.keys() held by construction, so the clause could
 # never fire and no test could kill it. That redundancy has been removed from
 # production, so the control now anchors on the surviving `intents` clause ---
 # the one that actually owns the property. Both sites, because the append-time
 # pre-check and the prospective-state validation each refuse on their own;
 # unlike J7's, these two sit at DIFFERENT layers rather than on adjacent
 # lines, so they are defence in depth rather than duplication.
 ("J25 a window with a live intent may be re-intented", [
   (J, "    if window_id in state.intents:", "    if False:"),
   (J, "            if window_id in intents:", "            if False:")]),
 ("J26 one window may be both committed and abandoned", [(J,
   "        if previous is not None:\n            raise JournalRefused(\n"
   "                JOURNAL_CONTRADICTORY_TERMINAL,",
   "        if False:\n            raise JournalRefused(\n"
   "                JOURNAL_CONTRADICTORY_TERMINAL,")]),
 # The nominal gate that survived the J7 repair. required_fields discriminates
 # with `==`/`in`, which a str SUBCLASS satisfies, so this clause is the only
 # thing refusing one. A test with no control is as unproven as a clause with
 # no test.
 ("J27 the record_type gate stops being nominal", [(J,
   "    if type(record_type) is not str:", "    if False:")]),
]

# Chokepoint controls, declared broad BEFORE launch rather than discovered
# broad afterwards. Each mutates a load-bearing primitive that many otherwise
# independent operations pass through, so it necessarily breaks tests belonging
# to other controls' properties. The declaration excuses it from the narrow
# unique-witness competition and NOTHING else: zeros, classification,
# suppression, completeness and the named witness below all still bind. If the
# named witness stops firing, the control has stopped testing its own property
# and the declaration is refused --- "broad" must not become "any failure will
# do". `BROAD = 40` stays an absolute tripwire and is not recalibrated to fit.
BROAD_DECLARED = {
    "J8": {
        "reason": "terminal schema declaration: TERMINAL_FIELDS is the field "
                  "set every terminal record is built and validated against, "
                  "so widening it reaches every commit, abandon and count",
        "witness": "test_terminal_records_repeat_no_intent_binding",
    },
    "J16": {
        "reason": "framing length byte order: the uint32 prefix is written by "
                  "_frame_record and read by every recovery path, so a byte "
                  "order change reaches every record round-trip",
        "witness": "test_the_length_prefix_is_little_endian_uint32",
    },
    "J17": {
        "reason": "digest domain: the record digest is computed in "
                  "_frame_record and re-checked on every read, so changing "
                  "what it covers reaches every record round-trip",
        "witness": "test_the_record_digest_is_sha256_of_the_body_alone",
    },
}

INTRODUCES = {}

SUBSUMED = {}


def check_inventory(accepted_ids):
    mine = [c.split()[0] for c, _p in CONTROLS]
    accepted = frozenset(accepted_ids)
    expected = accepted | EARNED_IDS
    problems = []
    repeated = sorted(k for k, v in collections.Counter(mine).items() if v > 1)
    if repeated:
        problems.append(f"repeated ids: {repeated}")
    if accepted & EARNED_IDS:
        problems.append(f"earned ids overlap the accepted set: "
                        f"{sorted(accepted & EARNED_IDS)}")
    missing = sorted(expected - set(mine))
    if missing:
        problems.append(f"declared ids with no control: {missing}")
    extra = sorted(set(mine) - expected)
    if extra:
        problems.append(f"controls declared nowhere: {extra}")
    if len(mine) != len(expected):
        problems.append(f"{len(mine)} controls for {len(expected)} declared ids")
    return problems


from controls527 import check_mutations                     # noqa: E402,F401
