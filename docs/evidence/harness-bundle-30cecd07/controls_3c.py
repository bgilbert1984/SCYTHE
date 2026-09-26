"""§5.20 3c-core's controls: steps 5-8 and the verified-final mint.

Group `K`, all earned. The accepted table names no mutation rows for the
persistence boundary, so there is no accepted identifier to claim and none is
invented. `check_inventory` is called with an empty accepted set.
"""

import collections

P = "rf_capture_publication.py"
F = "rf_capture_format.py"
A = "rf_capture_admission.py"
T = "test_rf_capture_publication.py"   # the boundary tests' shared scan scope

ACCEPTED_IN_SCOPE = ()
ACCEPTED_DEFERRED = {}

EARNED_IDS = frozenset("K%d" % n for n in range(1, 27))

# The readback through the already-held descriptor is the negative control the
# accepted amendment names by hand: it cannot establish filename agreement, so
# it fails nearly everything downstream of the open. Declared broad BEFORE
# launch, with the witness that must keep firing.
BROAD_DECLARED = {
    "K2": {
        "reason": "step 6 IS the publication primitive: replacing link with "
                  "rename removes the temporary as a side effect, so the unlink "
                  "then fails, the retained-name accounting refuses, and every "
                  "property established after the publish is disturbed",
        "witness": "test_the_occupying_bytes_survive_the_refusal",
    },
    "K6": {
        "reason": "the readback reaches the bytes through the descriptor step 3 "
                  "created instead of through a fresh open of the final name, "
                  "so every check downstream of the open reads a file at EOF",
        "witness": "test_the_final_name_is_opened_afresh",
    },
}

# K24 adds an import, so the name it introduces is deliberate. Declared, so the
# audit distinguishes it from a typo that would mutate nothing.
INTRODUCES = {"K24": ("rf_capture_publication", "_wire_the_publisher"),
              "K25": ("rf_capture_publication", "_wire_the_publisher")}
# K9 and K22 both manifest ONLY on the unlink-failure path, and K22 aborts that
# path strictly earlier --- it re-raises before the readback that K9 mis-accounts
# in --- so everything K9 breaks, K22 breaks. Containment became STRICT rather
# than equal once K22 gained a witness of its own: the escaping exception type.
# An OSError reaching a caller is K22's alone, because K9 raises
# PublicationFailed instead.
SUBSUMED = {
    ("K9", "K22"): ("both mutations reach only the retained-temporary path, and "
                    "K22 re-raises before the readback K9 alters, so K22's "
                    "failing set strictly contains K9's"),
}

CONTROLS = [
 # --- step 5: the BYTES survive --------------------------------------------
 ("K1 the file is never fsynced", [(P,
   "    try:\n        os.fsync(fd)\n    except OSError as exc:",
   "    try:\n        pass\n    except OSError as exc:")]),

 # --- step 6: publish WITHOUT replacement ----------------------------------
 ("K2 publication replaces silently, by rename", [(P,
   "        os.link(temporary_name, final_name,\n"
   "                src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)",
   "        os.rename(temporary_name, final_name,\n"
   "                  src_dir_fd=dir_fd, dst_dir_fd=dir_fd)")]),
 ("K3 an occupied final name is an ordinary link failure", [(P,
   "        if exc.errno == errno.EEXIST:\n            raise PublicationFailed(\n"
   "                PUBLICATION_FINAL_NAME_EXISTS,",
   "        if False:\n            raise PublicationFailed(\n"
   "                PUBLICATION_FINAL_NAME_EXISTS,")]),
 ("K22 a failed unlink becomes a publication failure", [(P,
   "    except OSError:\n        temporary_name_retained = True",
   "    except OSError:\n        raise")]),

 # --- step 7: the NAMES survive --------------------------------------------
 ("K4 the directory is never fsynced", [(P,
   "    try:\n        os.fsync(dir_fd)\n    except OSError as exc:",
   "    try:\n        pass\n    except OSError as exc:")]),
 ("K5 the directory fsync precedes the unlink", [(P,
   "    temporary_name_retained = False\n"
   "    try:\n        os.unlink(temporary_name, dir_fd=dir_fd)",
   "    os.fsync(dir_fd)\n    temporary_name_retained = False\n"
   "    try:\n        os.unlink(temporary_name, dir_fd=dir_fd)")]),

 # --- step 8: the readback -------------------------------------------------
 ("K6 the readback uses the descriptor already held", [(P,
   "        final_fd = os.open(final_name, os.O_RDONLY | os.O_NOFOLLOW,\n"
   "                           dir_fd=dir_fd)",
   "        final_fd = os.dup(fd)")]),
 ("K7 the readback follows a symbolic link", [(P,
   "os.O_RDONLY | os.O_NOFOLLOW,", "os.O_RDONLY,")]),
 ("K8 a foreign hard link to a member is tolerated", [(P,
   "    if info.st_nlink != expected_names:",
   "    if False:")]),
 ("K9 the retained temporary is not accounted for", [(P,
   "            expected_names=2 if temporary_name_retained else 1)",
   "            expected_names=1)")]),
 ("K10 a permissive member is tolerated", [(P,
   "    if stat.S_IMODE(info.st_mode) != CORPUS_FILE_MODE:",
   "    if False:")]),
 ("K11 a member on another device is tolerated", [(P,
   "    if info.st_dev != directory_device:", "    if False:")]),
 ("K12 a member owned by another uid is tolerated", [(P,
   "    if info.st_uid != os.getuid():", "    if False:")]),
 # The first version changed the CHUNK SIZES and left the loop accumulating, so
 # it discriminated nothing: a probe confirmed n=0 while a single bare read gives
 # n=1. The loop itself has to go. Second time this property has been mis-aimed
 # --- in the journal the same control was too broad, here too weak.
 ("K13 a fragmented read is accepted as a complete one", [(P,
   '    chunks = []\n    received = 0\n    while received < limit:\n'
   '        block = os.read(fd, limit - received)\n'
   '        if not block:\n            break\n'
   '        chunks.append(block)\n        received += len(block)\n'
   '    return b"".join(chunks)',
   "    return os.read(fd, limit)")]),

 # --- framing, length, digests, name --------------------------------------
 ("K14 the magic is not checked", [(P,
   "    if image[:len(IQC_MAGIC)] != IQC_MAGIC:", "    if False:")]),
 ("K15 the format version is not checked", [(P,
   "    if version != IQC_FORMAT_VERSION:", "    if False:")]),
 ("K16 the header length bound is not checked", [(P,
   "    if header_length > IQC_MAX_HEADER_BYTES:", "    if False:")]),
 ("K17 trailing bytes are called a length disagreement", [(P,
   "        if len(payload) > declared:\n            raise PublicationFailed(\n"
   "                PUBLICATION_TRAILING_BYTES,",
   "        if False:\n            raise PublicationFailed(\n"
   "                PUBLICATION_TRAILING_BYTES,")]),
 ("K18 the payload digest is not reconciled", [(P,
   "    if payload_sha256 != publication.payload_sha256:", "    if False:")]),
 ("K19 the file digest is not reconciled against the intent", [(P,
   "    if file_sha256 != intent_file_sha256:", "    if False:")]),
 ("K20 filename agreement is not checked", [(P,
   "    if final_name != canonical_member_name(file_sha256):", "    if False:")]),

 # --- the mint, and the name's own shape ----------------------------------
 ("K21 a verified final may be constructed by a caller", [(P,
   "        if mint_key is not _MINT_KEY:", "        if False:")]),
 # 3c-core's boundary is a property of the code, so it gets a control like any
 # other. Without one, the test asserting the publisher is unwired would be a
 # test nothing can fail --- the same gap as a clause with no control.
 # RE-ANCHORED for 3c-wire: `_record` lost its `lock` parameter when the typed
 # entrypoints began consuming the ownership scope, so the old anchor went dead
 # and this control would never have applied. The PRE-FLIGHT anchor audit
 # caught it before a sweep was spent, which is the whole reason it reports
 # instead of deferring to `run_control`.
 # A MODULE-level import here is circular --- the publisher imports
 # PublicationFailed from admission --- so it detonated at import time and
 # collected 2174 of 2248 tests. A degraded row is not evidence. A function-level
 # import is what a real workaround would look like, is never executed because
 # nothing calls it, and the AST scan sees it just the same.
 ("K24 admission is wired to the publisher", [(A,
   "def _record(*, scope: Any, stratum: str, attestation: Any, corpus: Any,",
   "def _wire_the_publisher():\n    import rf_capture_publication\n\n\n"
   "def _record(*, scope: Any, stratum: str, attestation: Any, corpus: Any,")]),
 # The scan's BREADTH, witnessed. Run 1 at 64f5837 measured K25 as "a second
 # module is wired" alone, which the prohibition catches exactly as it catches
 # K24: one witness, two controls. The breadth test is the one that must see a
 # scan narrowed back to one file, so this control narrows the shared scope
 # to admission alone WHILE a second module wires the publisher. The
 # prohibition, reading the narrowed scope, stays green; only the breadth
 # test can answer.
 ("K25 the scan narrowed to one file while a second module is wired to the publisher", [
  (T,
   "    return sorted(p for p in pathlib.Path(\".\").glob(\"*.py\")\n"
   "                  if not p.name.startswith(\"test_\") and p.name != MODULE_NAME)",
   "    return [pathlib.Path(\"rf_capture_admission.py\")]"),
  (F,
   "def framing_declaration() -> Dict[str, Any]:",
   "def _wire_the_publisher():\n    import rf_capture_publication\n\n\n"
   "def framing_declaration() -> Dict[str, Any]:")]),
 ("K26 a member need not be a regular file", [(P,
   "    if not stat.S_ISREG(info.st_mode):", "    if False:")]),
 ("K23 the member name accepts any digest-shaped string", [(F,
   '    if any(c not in "0123456789abcdef" for c in file_sha256):',
   "    if False:")]),
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
    for cid in BROAD_DECLARED:
        if cid not in ids:
            problems.append(f"{cid} is declared broad but is not a control")
    return problems


from controls527 import check_mutations            # noqa: E402  (shared checker)
