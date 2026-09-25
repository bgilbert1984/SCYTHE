"""§5.27's controls: the accepted 37, one-to-one, plus what implementation earned.

`A1`-`D6` map to §5.27's accepted table exactly. `check_inventory()` asserts
that one-to-one against the section itself rather than against this file's
author, because a control set that drifts from the contract it certifies is
certifying something else. Newly discovered properties go in group `E`, never
by collapsing an accepted row into a neighbour.
"""

A = "rf_eligible_trials_artefact.py"
R = "rf_corpus_reconstruction.py"
M = "rf_corpus_manifest.py"
N = "rf_corpus_namespace.py"

_SCOPE_ANCHOR = "    def release(self) -> None:"


def _returns(body):
    """A scope method that hands something out, inserted before `release`."""
    return (N, _SCOPE_ANCHOR,
            f"    def _escaped(self):\n        {body}\n\n" + _SCOPE_ANCHOR)


CONTROLS = [
 # -- A: the finding and exact reconstruction (9) ----------------------------
 ("A1 reconstruct from the compact manifest without reading the sidecar", [(N,
  "    fd = _open_member(dir_fd, ELIGIBLE_NAME, device, ELIGIBLE_ARTEFACT_NOT_FOUND)",
  "    return []\n    fd = _open_member(dir_fd, ELIGIBLE_NAME, device, ELIGIBLE_ARTEFACT_NOT_FOUND)")]),
 ("A2 accept one eligible row changed", [(R,
  '    if allocation.eligible_trials_digest() != d["eligible_trials_digest"]:',
  "    if False:")]),
 ("A3 accept one eligible row omitted while repairing the framed count", [(A,
  "    if count != int(expected_count):", "    if count > int(expected_count):")]),
 ("A4 accept a duplicate trial key", [(A,
  "                ELIGIBLE_ORDER_NOT_CANONICAL if key != previous\n                else ELIGIBLE_KEY_REPEATED,",
  "                ELIGIBLE_ORDER_NOT_CANONICAL if key != previous\n                else ELIGIBLE_ORDER_NOT_CANONICAL,")]),
 ("A5 accept non-canonical row order", [(A,
  "        if previous is not None and key <= previous:", "        if False:")]),
 ("A6 regenerate selected trials without comparing selected_trials_digest", [(R,
  '    if allocation.selected_trials_digest() != d["selected_trials_digest"]:',
  "    if False:")]),
 ("A7 accept a caller-supplied selected tuple", [(R,
  "    unexpected = sorted(set(mapping) - allowed)", "    unexpected = []")]),
 ("A8 omit the final compact to_dict() equality", [(R,
  "    if rebuilt != dict(mapping):", "    if False:")]),
 ("A9 omit the final capture_plan_digest comparison", [(R,
  "    if plan.digest() != frozen_digest:", "    if False:")]),

 # -- B: format and binding (12) ---------------------------------------------
 ("B1 make IQE_MAGIC equal IQM_MAGIC", [(A,
  'IQE_MAGIC = b"\\x89SCYET\\r\\n"                       # exactly 8 bytes',
  'IQE_MAGIC = b"\\x89SCYMF\\r\\n"')]),
 ("B2 make IQE_MAGIC equal IQC_MAGIC", [(A,
  'IQE_MAGIC = b"\\x89SCYET\\r\\n"                       # exactly 8 bytes',
  "IQE_MAGIC = IQC_MAGIC")]),
 ("B3 allocate a record before enforcing IQE_MAX_RECORD_BYTES", [(A,
  "        if length > IQE_MAX_RECORD_BYTES:", "        if False:")]),
 ("B4 accept trailing bytes", [(A,
  "    if not stream.at_end():", "    if False:")]),
 ("B5 accept a non-canonical JSON record", [(A,
  "        if canonical_record_bytes(row) != encoded:", "        if False:")]),
 ("B6 accept a false framed count", [(A,
  "    if count != int(expected_count):", "    if count < int(expected_count):")]),
 ("B7 omit iqe_schema from the manifest's derived required set", [(M,
  '    "iqe_schema", "iqe_format_version",\n', '    "iqe_format_version",\n')]),
 ("B8 omit iqe_format_version from that set", [(M,
  '    "iqe_schema", "iqe_format_version",\n', '    "iqe_schema",\n')]),
 ("B9 add a second stored eligible-set digest", [(M,
  '        "iqe_format_version": IQE_FORMAT_VERSION,',
  '        "iqe_format_version": IQE_FORMAT_VERSION,\n'
  '        "eligible_trials_digest": "blake2s:" + "0" * 32,')]),
 ("B10 stop comparing the existing eligible_trials_digest", [(N,
  '    if recomputed != allocation["eligible_trials_digest"]:', "    if False:")]),
 ("B11 leave IQM_SCHEMA at v1", [(M,
  'IQM_SCHEMA = "scythe.iq-corpus-manifest.v2"',
  'IQM_SCHEMA = "scythe.iq-corpus-manifest.v1"')]),
 ("B12 leave IQM_FORMAT_VERSION at 1", [(M,
  "IQM_FORMAT_VERSION = 2                             # uint16, little-endian",
  "IQM_FORMAT_VERSION = 1                             # uint16, little-endian")]),

 # -- C: creation, durability and opened objects (10) ------------------------
 ("C1 create the sidecar without O_EXCL", [(N,
  "        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,",
  "        fd = os.open(name, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW,")]),
 ("C2 remove the sidecar fsync", [(N,
  "        os.fchmod(fd, CORPUS_FILE_MODE)\n        os.fsync(fd)",
  "        os.fchmod(fd, CORPUS_FILE_MODE)")]),
 ("C3 create or durably name the manifest before the sidecar is verified", [(N,
  '''            _write_file(dir_fd, ELIGIBLE_NAME, frame_records(rows))
            _read_eligible_rows(dir_fd, device,
                                body["capture_plan"]["spur_allocation"])

        _write_file(dir_fd, MANIFEST_NAME, (framed,))''',
  '''            _write_file(dir_fd, ELIGIBLE_NAME, frame_records(rows))

        _write_file(dir_fd, MANIFEST_NAME, (framed,))
        if rows:
            _read_eligible_rows(dir_fd, device,
                                body["capture_plan"]["spur_allocation"])''')]),
 ("C4 omit sidecar readback", [(N,
  '''            _read_eligible_rows(dir_fd, device,
                                body["capture_plan"]["spur_allocation"])

        _write_file(dir_fd, MANIFEST_NAME, (framed,))''',
  '''        _write_file(dir_fd, MANIFEST_NAME, (framed,))''')]),
 ("C5 open the sidecar without O_NOFOLLOW", [(N,
  "        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)",
  "        fd = os.open(name, os.O_RDONLY, dir_fd=dir_fd)")]),
 ("C6 omit the st_nlink == 1 check", [(N,
  "    if info.st_nlink != 1:", "    if False:")]),
 ("C7 omit the owner-UID check", [(N,
  '''    if info.st_uid != os.getuid():
        raise NamespaceRefused(
            NAMESPACE_OWNER_MISMATCH,
            f"the manifest is owned by uid {info.st_uid}")''',
  "    if False:\n        pass")]),
 ("C8 omit the exact 0600 mode check", [(N,
  "    if stat.S_IMODE(info.st_mode) != CORPUS_FILE_MODE:", "    if False:")]),
 ("C9 omit the same-device check", [(N,
  "    if info.st_dev != device:", "    if False:")]),
 ("C10 mint the scope before both artefacts reconstruct the nominal plan", [(N,
  '''        rebuilt_envelope, rebuilt_plan, rebuilt_lock = _reconstruct(
            read_body, read_rows)''',
  "        rebuilt_envelope = rebuilt_plan = rebuilt_lock = None")]),

 # -- D: presence and capability (6) -----------------------------------------
 ("D1 allocation present and sidecar absent is accepted", [(N,
  '''    if allocation is None:
        if present:''', '''    if allocation is None or True:
        if present:''')]),
 ("D2 allocation absent and sidecar present is ignored or adopted", [(N,
  "        if present:\n            raise EligibleSetRefused(",
  "        if False:\n            raise EligibleSetRefused(")]),
 ("D3 a scope method returns the sidecar path", [_returns("return self._live().path")]),
 ("D4 a scope method returns the sidecar descriptor", [_returns("return self._live().dir_fd")]),
 ("D5 a scope method returns the eligible rows", [_returns(
  "return [t.to_dict() for t in "
  "self._live().capture_plan.spur_allocation.eligible_trials]")]),
 ("D6 a scope method returns mutable reconstructed state", [_returns(
  'return {"lock": self._live().lock}')]),

 # -- E: earned by the implementation, not in the accepted table (6) ---------
 ("E1 an authority object is returned and outlives the scope", [_returns(
  "return self._live().lock")]),
 ("E2 the live body is returned instead of a copy", [(N,
  "        return dict(self._live().body)", "        return self._live().body")]),
 ("E3 the canonical rule switched to ensure_ascii=False", [(A,
  "                          ensure_ascii=True, allow_nan=False)",
  "                          ensure_ascii=False, allow_nan=False)")]),
 ("E4 a short read accepted as a complete one", [(A,
  """            if not block:
                raise EligibleSetRefused(
                    ELIGIBLE_FRAMING_REFUSED,
                    f"{what}: wanted {count} bytes and the artefact ended after "
                    f"{got}")""",
  """            if not block:
                break""")]),
 ("E5 the directory fsync removed", [(N,
  "        os.fsync(dir_fd)\n\n        read_body, digest = _read_manifest(dir_fd, device)",
  "        read_body, digest = _read_manifest(dir_fd, device)")]),
 ("E6 a foreign namespace entry accepted on reopen", [(N,
  "        unexpected = [entry for entry in entries\n                      if entry not in (MANIFEST_NAME, ELIGIBLE_NAME)]",
  "        unexpected = []")]),
]


# Declared explicitly, never inferred from a prefix. A parser that exempts
# "anything starting with E" cannot fail: the exemption grows to fit whatever
# was written, which is the opposite of an inventory check.
EARNED_IDS = frozenset(("E1", "E2", "E3", "E4", "E5", "E6"))


def check_inventory(accepted_ids):
    """One-to-one against §5.27's table, plus an explicit disjoint earned set.

        accepted_ids == implemented contract ids     # exactly A1-D6
        earned_ids   == EARNED_IDS                   # explicit, unique
        all_controls == accepted_ids | earned_ids    # and nothing else
    """
    import collections
    mine = [c.split()[0] for c, _p in CONTROLS]
    accepted = frozenset(accepted_ids)
    expected = accepted | EARNED_IDS
    counts = collections.Counter(mine)
    problems = []
    repeated = sorted(k for k, v in counts.items() if v > 1)
    if repeated:
        problems.append(f"repeated ids: {repeated}")
    if accepted & EARNED_IDS:
        problems.append(f"earned ids overlap the accepted table: "
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


# --- the mutation audit -----------------------------------------------------
#
# Added after a control ran for a whole sweep testing nothing. `A2`'s
# replacement text was `if count > int(expected_count):` --- the text written
# for `A3`, left in `A2`'s slot by a `replace(..., 1)` that matched the wrong
# occurrence. Neither name exists in `rf_corpus_reconstruction`, so the
# mutation raised NameError on every reconstruction and 76 unrelated tests
# failed. It classified TEST_FAILURE with a large `n` and looked, to every
# check then in place, like a healthy control.
#
# An anchor check cannot catch this: the anchor matched exactly once. What
# distinguishes a mutation from a wrecking ball is that a mutation only uses
# names the module already resolves.

import ast as _ast
import builtins as _builtins
import io as _io
import keyword as _keyword
import re as _re
import textwrap as _textwrap
import tokenize as _tokenize

# Names a control deliberately brings into existence. Explicit, per control,
# for the same reason `EARNED_IDS` is explicit: a blanket exemption would make
# the audit a formality.
INTRODUCES = {
    "D3": ("_escaped",),
    "D4": ("_escaped",),
    "D5": ("_escaped", "t"),          # `t` binds in the comprehension
    "D6": ("_escaped",),
    "E1": ("_escaped",),
}

_SAFE = set(_keyword.kwlist) | set(dir(_builtins)) | {"self"}
_NAME = _re.compile(r"(?<![.\w])([A-Za-z_]\w*)")


def _tokenised(text):
    """NAME tokens, minus attribute names, or None if `text` will not tokenise.

    Tokenising rather than pattern-matching is the point. A regex that strips
    string literals first will treat an apostrophe in a prose comment as an
    opening quote and swallow the code up to the next one, which made an
    earlier version of this audit report eighteen names as missing that the
    modules plainly define.
    """
    try:
        tokens = list(_tokenize.generate_tokens(_io.StringIO(text).readline))
    except (_tokenize.TokenError, IndentationError, SyntaxError):
        return None
    names, attribute = set(), False
    for token in tokens:
        if token.type == _tokenize.NAME:
            if not attribute:
                names.add(token.string)
            attribute = False
        elif token.type == _tokenize.OP:
            attribute = token.string == "."
        elif token.type not in (_tokenize.NL, _tokenize.NEWLINE,
                                _tokenize.INDENT, _tokenize.DEDENT,
                                _tokenize.COMMENT):
            attribute = False
    return names - _SAFE


def _identifiers(text):
    """Free names in a WHOLE MODULE. `None` when it will not tokenise.

    It used to accept a fragment, trying two wrappings and then falling back to
    a regex over the raw text. That fallback was the very technique `_tokenised`
    was written to replace: a regex sees no difference between code and the
    inside of a string literal, so a mutation replacing `"<I"` with `">I"`
    was reported as introducing a name `I`, and one carrying prose reported six
    English words. False positives are not harmless here --- each one invites an
    INTRODUCES entry, and a blanket exemption is how this audit becomes a
    formality.

    Fragments are no longer passed. `check_mutations` applies the patches and
    tokenises the resulting module, which it has already proved parses.
    """
    return _tokenised(text)


def check_mutations(blobs, controls=None, introduces=None):
    """Every mutation uses only names its module resolves, and still parses.

    `blobs` maps each watched path to its source AT THE CHECKPOINT, so this is
    audited against the code the sweep will actually mutate.

    `controls` and `introduces` default to this module's own, so another
    slice's control set can be audited by the same checker. Closing over the
    module globals instead would silently audit the wrong slice --- which is
    the shape of the defect this checker exists to catch.
    """
    controls = CONTROLS if controls is None else controls
    introduces = INTRODUCES if introduces is None else introduces
    problems = []
    for cid, patches in controls:
        ident = cid.split()[0]
        declared = set(introduces.get(ident, ()))
        # Every patch applied, in order, per file --- which is what the sweep
        # does. Auditing one patch at a time made a two-patch control look as
        # though it referenced a name its sibling patch supplies.
        mutated, incomplete = {}, False
        for rel, old, new in patches:
            text = blobs.get(rel)
            if text is None:
                problems.append(f"{ident}: no source given for {rel}")
                incomplete = True
                continue
            current = mutated.get(rel, text)
            found = current.count(old)
            if found != 1:
                # Reported here, PRE-FLIGHT. The comment this replaced said the
                # anchor check owned this failure --- but the only anchor check
                # is inside `run_control`, per control, DURING the sweep. So a
                # dead anchor passed the audit, cost a baseline, and only then
                # surfaced as MUTATION_DID_NOT_APPLY. Worse, `continue` skipped
                # this control's identifier and SyntaxError audits too, so one
                # dead anchor bought a control no audit at all. A stale bundle
                # aimed at a newer checkpoint is exactly how this arises: the
                # anchors name lines the repair rewrote.
                incomplete = True
                problems.append(
                    f"{ident}: its anchor in {rel} occurs {found} times, not "
                    f"once, so this patch would "
                    + ("never apply" if found == 0 else "be ambiguous")
                    + f"; first line: {old.strip().splitlines()[0][:70]!r}")
                continue
            mutated[rel] = current.replace(old, new, 1)
        if incomplete:
            continue
        seen = set()
        for rel, after in mutated.items():
            try:
                _ast.parse(after)
            except SyntaxError as exc:
                problems.append(
                    f"{ident}: mutating {rel} leaves a SyntaxError: {exc.msg}")
                continue
            before_names = _identifiers(blobs[rel])
            after_names = _identifiers(after)
            if before_names is None or after_names is None:   # pragma: no cover
                problems.append(
                    f"{ident}: {rel} does not tokenise, so its identifiers "
                    f"cannot be audited")
                continue
            introduced = after_names - before_names
            seen |= introduced
            surprising = introduced - declared
            if surprising:
                problems.append(
                    f"{ident}: {rel} introduces {sorted(surprising)}, which the "
                    f"module does not define. If deliberate, declare it in "
                    f"INTRODUCES; otherwise the mutation is testing nothing.")
        unused = declared - seen
        if unused:
            problems.append(
                f"{ident}: INTRODUCES declares {sorted(unused)}, which its "
                f"mutation does not use")
    return problems
