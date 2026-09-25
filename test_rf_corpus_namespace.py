"""§5.26: a corpus's terms become a fact of its namespace, or they stay an argument.

**Real filesystem behaviour, not mocked.** A mocked `fsync`, exclusive create or
readback would make every durability check here a test of the mock, which is the
shape §5.20's controls exist to refuse. Every test therefore writes bytes -- and
every test writes them into its **own** temporary root, created in `setUp` and
removed in a cleanup that runs in `finally`.

**No test can reach the production namespace.** Not by convention: every call
passes an explicit `root`, a static check in this file refuses one that does
not, and `resolve_corpus_directory` refuses a root that resolves inside the
production namespace. The operator's temporary-directory authorisation covers
tests and nothing else, and these are the checks that keep it that way.
"""

import ast
import errno
import io
import json
import os
import pathlib
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from unittest import mock
import dataclasses
import time
from dataclasses import replace

import rf_corpus_namespace as namespace
from rf_corpus_manifest import (
    IQM_FORMAT_VERSION, IQM_FRAMING_PREFIX_BYTES, IQM_MAGIC,
    IQM_MAX_BODY_BYTES, MANIFEST_ALREADY_PRESENT, MANIFEST_BODY_TOO_LARGE,
    MANIFEST_DECLARATION_DISAGREES,
    MANIFEST_FIELD_NO_AUTHORITY, MANIFEST_FIELD_NOT_EMITTED,
    MANIFEST_FRAMING_REFUSED, MANIFEST_NOT_CANONICAL, MANIFEST_NOT_FOUND,
    MANIFEST_REFUSALS, MANIFEST_TRAILING_BYTES, ManifestRefused,
    canonical_body_bytes, format_declaration, frame_manifest, manifest_body,
    manifest_sha256, parse_manifest, required_manifest_fields,
)
from rf_capture_admission import CapturedCorpusRetention
from rf_capture_format import IQC_MAGIC
from rf_corpus_namespace import (
    CORPUS_DIRECTORY_MODE, CORPUS_FILE_MODE, MANIFEST_NAME,
    NAMESPACE_DEVICE_MISMATCH, NAMESPACE_HARD_LINKED, NAMESPACE_HOLDS_ENTRIES,
    NAMESPACE_MODE_PERMISSIVE, NAMESPACE_NOT_A_DIRECTORY,
    NAMESPACE_OWNED_ELSEWHERE, NAMESPACE_OWNER_MISMATCH,
    NAMESPACE_PRODUCTION_NOT_AUTHORISED, NAMESPACE_RECOVERY_UNBUILT,
    NAMESPACE_REFUSALS, NAMESPACE_ROOT_INSIDE_PRODUCTION,
    NAMESPACE_SCOPE_RELEASED, NAMESPACE_SYMLINK_REFUSED, PRODUCTION_CORPUS_ROOT,
    CorpusOwnershipScope, NamespaceRefused, create_corpus_namespace,
    namespace_status, open_corpus_namespace, production_corpus_root,
    resolve_corpus_directory,
)
from rf_membership_journal import (
    JOURNAL_NOT_FOUND, JOURNAL_REFUSALS, JournalRefused, append_intent,
    intent_record,
)
import rf_eligible_trials_artefact as artefact
from rf_eligible_trials_artefact import (
    ELIGIBLE_ARTEFACT_NOT_FOUND, ELIGIBLE_ARTEFACT_UNEXPECTED,
    ELIGIBLE_DIGEST_DISAGREES, EligibleSetRefused,
)
from rf_promotion_envelope import (
    CapturePlanDeclaration, InstrumentChainEnvelope, declaration_digest,
)
from rf_validation_manifest import (
    STRATA_DEFINITION_REVISION, PromotionCorpusLock, freeze_promotion_corpus,
)
from test_rf_promotion_envelope import _envelope, _plan

# The real syscalls, captured once at import. `rf_corpus_namespace.os` IS the
# global `os` module, so patching an attribute on it patches it for everyone --
# including a delegate that then calls through and recurses into itself. A
# call-through recorder has to hold the original, not look it up.
_REAL_FSYNC = os.fsync
_REAL_FSTAT = os.fstat
_REAL_OPEN = os.open
_REAL_CHMOD = os.chmod
_REAL_FCHMOD = os.fchmod

_PRODUCTION_ROOT_MARKER = "# production-root: refused on purpose"

OPENED_AT = 1_000_000.0
DEADLINE = OPENED_AT + 30 * 24 * 3600.0


def _lock(corpus_id="corpus-a"):
    envelope = _envelope()
    return freeze_promotion_corpus(
        corpus_id=corpus_id, method_revision="squared-envelope-cyclic.v1",
        decision_threshold=6.0, preprocessing_revision="pre.v1",
        envelope=envelope, capture_plan=_plan(envelope), opened_at=OPENED_AT)


class NamespaceFixture(unittest.TestCase):
    """A fresh temporary root per test, removed in a cleanup that always runs."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="scythe-corpus-test-")
        self.addCleanup(self._remove_root)
        self.lock = _lock()
        self.retention = CapturedCorpusRetention(delete_not_after=DEADLINE)

    def _remove_root(self):
        try:
            shutil.rmtree(self.root, ignore_errors=True)
        finally:
            # The condition the operator attached to the authorisation, checked
            # rather than trusted: nothing of this test survives it.
            if os.path.exists(self.root):                 # pragma: no cover
                raise AssertionError(f"{self.root} outlived its test")

    def create(self, corpus_id="corpus-a", **kwargs):
        fields = dict(lock=self.lock, retention=self.retention)
        fields.update(kwargs)
        root = fields.pop("root", self.root)
        # `root` is passed by name rather than through the mapping so that the
        # static check below can see it. A check that cannot read its own
        # helper is a check with a hole the size of every test that uses it.
        return create_corpus_namespace(corpus_id=corpus_id, root=root, **fields)

    def path(self, corpus_id="corpus-a"):
        return os.path.join(self.root, corpus_id)


class ProductionIsUnreachableTests(NamespaceFixture):
    """The authorisation covers tests. These are the checks that keep it so."""

    def test_production_creation_is_refused_by_a_check_not_a_convention(self):
        with self.assertRaises(NamespaceRefused) as caught:
            create_corpus_namespace(  # production-root: refused on purpose
                corpus_id="corpus-a", lock=self.lock, retention=self.retention)
        self.assertEqual(caught.exception.code,
                         NAMESPACE_PRODUCTION_NOT_AUTHORISED)
        self.assertFalse(os.path.exists(PRODUCTION_CORPUS_ROOT),
                         "the production namespace must not exist")

    def test_production_reopening_is_refused_too(self):
        with self.assertRaises(NamespaceRefused) as caught:
            open_corpus_namespace(  # production-root: refused on purpose
                corpus_id="corpus-a")
        self.assertEqual(caught.exception.code,
                         NAMESPACE_PRODUCTION_NOT_AUTHORISED)

    def test_a_root_inside_the_production_namespace_is_refused(self):
        """A seam is not a hole. The production root with a longer path is
        still the production root."""
        for candidate in (PRODUCTION_CORPUS_ROOT,
                          os.path.join(PRODUCTION_CORPUS_ROOT, "sub"),
                          PRODUCTION_CORPUS_ROOT + "/./x/.."):
            with self.subTest(candidate=candidate):
                with self.assertRaises(NamespaceRefused) as caught:
                    resolve_corpus_directory("corpus-a", root=candidate)
                self.assertEqual(caught.exception.code,
                                 NAMESPACE_ROOT_INSIDE_PRODUCTION)

    def test_a_nonexistent_child_under_the_production_root_is_refused(self):
        """The case a creation call is actually making: the directory does not
        exist yet, and the question is where it would land."""
        for candidate in (
                os.path.join(PRODUCTION_CORPUS_ROOT, "not", "there", "yet"),
                os.path.join(PRODUCTION_CORPUS_ROOT, "..", "captured-v1", "x"),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(NamespaceRefused) as caught:
                    resolve_corpus_directory("corpus-a", root=candidate)
                self.assertEqual(caught.exception.code,
                                 NAMESPACE_ROOT_INSIDE_PRODUCTION)

    def test_a_symlinked_parent_pointing_into_production_is_refused(self):
        """A symlink is not a way round the check. `realpath` resolves the
        existing part of the path, and the resolver walks to the nearest
        existing ancestor so a not-yet-created tail cannot slip past."""
        link = os.path.join(self.root, "looks-harmless")
        os.symlink(PRODUCTION_CORPUS_ROOT, link)
        for candidate in (link, os.path.join(link, "child")):
            with self.subTest(candidate=candidate):
                with self.assertRaises(NamespaceRefused) as caught:
                    resolve_corpus_directory("corpus-a", root=candidate)
                self.assertEqual(caught.exception.code,
                                 NAMESPACE_ROOT_INSIDE_PRODUCTION)

    def test_both_acts_refuse_a_production_root(self):
        """Create and reopen, not one of them."""
        inside = os.path.join(PRODUCTION_CORPUS_ROOT, "corpus-a")
        with self.assertRaises(NamespaceRefused) as created:
            create_corpus_namespace(corpus_id="corpus-a", lock=self.lock,
                                    retention=self.retention, root=inside)
        with self.assertRaises(NamespaceRefused) as opened:
            open_corpus_namespace(corpus_id="corpus-a", root=inside)
        for caught in (created, opened):
            self.assertEqual(caught.exception.code,
                             NAMESPACE_ROOT_INSIDE_PRODUCTION)
        self.assertFalse(os.path.exists(PRODUCTION_CORPUS_ROOT))

    def test_a_neighbouring_root_is_not_inside_it(self):
        """The containment check is over path components, not a prefix match:
        `captured-v1-scratch` is not inside `captured-v1`."""
        outside = PRODUCTION_CORPUS_ROOT + "-scratch"
        self.assertTrue(
            resolve_corpus_directory("corpus-a", root=outside).startswith(
                os.path.realpath(outside)))

    def test_the_production_root_has_no_parameter(self):
        import inspect
        self.assertEqual(list(inspect.signature(production_corpus_root)
                              .parameters), [])

    def test_every_call_in_this_file_passes_an_explicit_root(self):
        """The mechanical form of "production namespace resolution is
        unreachable from tests".

        A call that omits `root` resolves into the production namespace, and a
        reviewer reading two hundred lines is not a check. This one is.
        """
        tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
        exempt = self._exempt_lines
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name not in ("create_corpus_namespace", "open_corpus_namespace",
                            "resolve_corpus_directory"):
                continue
            if node.lineno in exempt:
                continue
            if not any(kw.arg == "root" for kw in node.keywords):
                offenders.append(node.lineno)
        self.assertEqual(offenders, [],
                         f"calls without an explicit root at lines {offenders}")
        self.assertEqual(len(exempt), 2,
                         "exactly two calls omit a root on purpose, and each "
                         "exists to prove the omission is refused")

    @property
    def _exempt_lines(self):
        """The calls that deliberately omit `root`, marked at the call site.

        A marker rather than a heuristic over the source text: the first
        version matched on a fragment of a line, which is a check that silently
        stops applying the moment someone reflows an argument list.
        """
        source = pathlib.Path(__file__).read_text(encoding="utf-8").splitlines()
        # Matched as a **trailing** comment, so the line that defines the
        # marker does not count itself. The first version did exactly that and
        # reported three deliberate omissions where there are two.
        return {i + 1 for i, line in enumerate(source)
                if line.rstrip().endswith(_PRODUCTION_ROOT_MARKER)}


class CreationTests(NamespaceFixture):
    """§5.26's creation sequence, on a real filesystem."""

    def test_a_corpus_is_created_and_its_terms_are_readable(self):
        with self.create() as corpus:
            self.assertEqual(corpus.corpus_id, "corpus-a")
            self.assertEqual(corpus.opened_at, OPENED_AT)
            self.assertEqual(corpus.delete_not_after, DEADLINE)
            body = corpus._verified_body()
        self.assertEqual(frozenset(body), required_manifest_fields())
        self.assertEqual(body["envelope_digest"], self.lock.envelope_digest)

    def test_the_directory_and_the_manifest_carry_5_20s_permissions(self):
        with self.create():
            pass
        directory = os.stat(self.path())
        manifest = os.stat(os.path.join(self.path(), MANIFEST_NAME))
        self.assertEqual(stat.S_IMODE(directory.st_mode), CORPUS_DIRECTORY_MODE)
        self.assertEqual(stat.S_IMODE(manifest.st_mode), CORPUS_FILE_MODE)
        self.assertEqual(manifest.st_nlink, 1)
        self.assertEqual(directory.st_uid, os.getuid())

    def test_the_bytes_on_disk_are_the_framed_manifest(self):
        with self.create() as corpus:
            digest = corpus.manifest_sha256
        framed = pathlib.Path(self.path(), MANIFEST_NAME).read_bytes()
        self.assertEqual(framed[:8], IQM_MAGIC)
        self.assertNotEqual(framed[:8], IQC_MAGIC)
        self.assertEqual(struct.unpack("<H", framed[8:10])[0], IQM_FORMAT_VERSION)
        length = struct.unpack("<I", framed[10:14])[0]
        self.assertEqual(len(framed), IQM_FRAMING_PREFIX_BYTES + length)
        self.assertEqual(manifest_sha256(framed), digest)

    def test_creating_over_an_existing_namespace_is_refused(self):
        with self.create():
            pass
        with self.assertRaises(NamespaceRefused) as caught:
            self.create()
        self.assertEqual(caught.exception.code, NAMESPACE_HOLDS_ENTRIES)

    def test_a_manifest_that_cannot_be_framed_creates_no_directory(self):
        """Framing happens before anything is created, so a body that cannot
        be serialised leaves no namespace behind."""
        broken = CapturedCorpusRetention(delete_not_after=DEADLINE)
        object.__setattr__(broken, "delete_not_after", float("nan"))
        with self.assertRaises(ManifestRefused):
            self.create(retention=broken)
        self.assertFalse(os.path.exists(self.path()))

    def test_a_lock_naming_another_corpus_is_refused(self):
        with self.assertRaises(NamespaceRefused):
            self.create(corpus_id="corpus-b")
        self.assertFalse(os.path.exists(self.path("corpus-b")))

    def test_creation_requires_an_exact_frozen_lock(self):
        with self.assertRaises(NamespaceRefused):
            self.create(lock={"corpus_id": "corpus-a"})


class ReopeningIsNotCreationTests(NamespaceFixture):
    """"Rejects a non-empty namespace" is right for one act and false for the other."""

    def test_reopening_reads_the_terms_back(self):
        with self.create() as created:
            digest = created.manifest_sha256
            body = created._verified_body()
        with open_corpus_namespace(corpus_id="corpus-a", root=self.root) as corpus:
            self.assertEqual(corpus.manifest_sha256, digest)
            self.assertEqual(corpus._verified_body(), body)

    def test_reopening_a_namespace_with_no_manifest_is_refused(self):
        os.mkdir(self.path(), CORPUS_DIRECTORY_MODE)
        os.chmod(self.path(), CORPUS_DIRECTORY_MODE)
        with self.assertRaises(ManifestRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, MANIFEST_NOT_FOUND)

    def test_reopening_over_a_member_refuses_because_recovery_is_unbuilt(self):
        """Not reopened with an unexamined member count. §5.26 requires every
        candidate final parsed, validated and reconciled against the journal,
        and the journal does not exist."""
        with self.create():
            pass
        pathlib.Path(self.path(), "deadbeef.iqc").write_bytes(b"not a window")
        with self.assertRaises(NamespaceRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, NAMESPACE_RECOVERY_UNBUILT)

    def test_they_are_two_functions_and_not_one_with_a_flag(self):
        import inspect
        create = set(inspect.signature(create_corpus_namespace).parameters)
        reopen = set(inspect.signature(open_corpus_namespace).parameters)
        self.assertEqual(create, {"corpus_id", "lock", "retention", "root"})
        self.assertEqual(reopen, {"corpus_id", "root"})
        for name in ("create", "new", "exist_ok", "mode", "force"):
            self.assertNotIn(name, create | reopen, name)


class OpenedObjectTests(NamespaceFixture):
    """Checked on the descriptor, never on the path that reached it."""

    def test_a_symlinked_namespace_is_refused(self):
        with self.create():
            pass
        link_root = tempfile.mkdtemp(prefix="scythe-corpus-link-")
        self.addCleanup(shutil.rmtree, link_root, ignore_errors=True)
        os.symlink(self.path(), os.path.join(link_root, "corpus-a"))
        with self.assertRaises(NamespaceRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=link_root)
        self.assertEqual(caught.exception.code, NAMESPACE_SYMLINK_REFUSED)

    def test_a_permissive_directory_is_refused(self):
        with self.create():
            pass
        os.chmod(self.path(), 0o755)
        with self.assertRaises(NamespaceRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, NAMESPACE_MODE_PERMISSIVE)

    def test_a_permissive_manifest_is_refused(self):
        with self.create():
            pass
        os.chmod(os.path.join(self.path(), MANIFEST_NAME), 0o644)
        with self.assertRaises(NamespaceRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, NAMESPACE_MODE_PERMISSIVE)

    def test_a_hard_linked_manifest_is_refused(self):
        """A corpus's terms are not a name shared with something else."""
        with self.create():
            pass
        os.link(os.path.join(self.path(), MANIFEST_NAME),
                os.path.join(self.root, "second-name"))
        with self.assertRaises(NamespaceRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, NAMESPACE_HARD_LINKED)

    def test_a_symlinked_manifest_is_refused(self):
        with self.create():
            pass
        target = os.path.join(self.root, "elsewhere.iqm")
        shutil.move(os.path.join(self.path(), MANIFEST_NAME), target)
        os.symlink(target, os.path.join(self.path(), MANIFEST_NAME))
        with self.assertRaises(NamespaceRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, NAMESPACE_SYMLINK_REFUSED)

    def test_a_regular_file_at_the_namespace_path_is_refused_as_such(self):
        """The case that separates "no `O_NOFOLLOW`" from "classified by errno".

        Both mutations refuse a symlinked namespace, so a symlink test alone
        gives them identical failing sets. A regular file does not: dropping
        `O_NOFOLLOW` still refuses it correctly, while classifying by errno
        falls through `ELOOP` and re-raises a bare `OSError` instead of the
        declared refusal.
        """
        pathlib.Path(self.root, "corpus-a").write_bytes(b"not a directory")
        with self.assertRaises(NamespaceRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, NAMESPACE_NOT_A_DIRECTORY)

    def test_a_symlink_to_a_regular_file_is_refused_as_a_symlink(self):
        """And the symlink is reported as a symlink even when what it points at
        is not a directory -- the classification is about the object at the
        path, not about which errno the kernel chose."""
        target = pathlib.Path(self.root, "target")
        target.write_bytes(b"not a directory")
        os.symlink(target, os.path.join(self.root, "corpus-a"))
        with self.assertRaises(NamespaceRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, NAMESPACE_SYMLINK_REFUSED)

    def test_a_corpus_id_that_is_a_path_is_refused(self):
        for bad in ("../escape", "a/b", "", ".", ".."):
            with self.subTest(corpus_id=bad):
                with self.assertRaises(NamespaceRefused) as caught:
                    resolve_corpus_directory(bad, root=self.root)
                self.assertEqual(caught.exception.code,
                                 NAMESPACE_NOT_A_DIRECTORY)


class ExclusivityTests(NamespaceFixture):
    """OS-backed, and the case that matters is a second process."""

    def test_a_second_process_cannot_hold_the_same_corpus(self):
        """An in-process registry would pass against the very mechanism it
        exists to refuse, so the control is a real second process."""
        with self.create():
            result = subprocess.run(
                [sys.executable, "-c", _SECOND_PROCESS, self.root],
                capture_output=True, text=True,
                cwd=os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(result.stdout.strip(), NAMESPACE_OWNED_ELSEWHERE,
                         result.stderr[-2000:])

    def test_the_corpus_is_openable_once_the_holder_releases(self):
        with self.create():
            pass
        with open_corpus_namespace(corpus_id="corpus-a", root=self.root) as first:
            self.assertEqual(first.corpus_id, "corpus-a")
        with open_corpus_namespace(corpus_id="corpus-a", root=self.root) as again:
            self.assertEqual(again.corpus_id, "corpus-a")


_SECOND_PROCESS = """
import sys
sys.path.insert(0, ".")
from rf_corpus_namespace import NamespaceRefused, open_corpus_namespace
try:
    open_corpus_namespace(corpus_id="corpus-a", root=sys.argv[1])
    print("HELD")
except NamespaceRefused as exc:
    print(exc.code)
"""


class ScopeTests(NamespaceFixture):
    """The descriptor and the path do not leave. §5.24's lesson, one layer out."""

    # The verified body is restricted alongside the descriptor. A caller
    # holding it would hold the envelope and the capture plan as **mappings**,
    # and admission consuming a mapping is the caller-supplied set §5.25
    # refused, wearing a different shape.
    # 3c-wire, §5.26 control A4: the test factory's clock seam is not the
    # interface. Production code reaches its clock only through the public
    # acts, which install `time.time` and read `POSIX_REALTIME`.
    RESTRICTED = ("_verified_body", "_create_corpus_namespace_with_clock",
                  "_open_corpus_namespace_with_clock")

    def test_there_is_no_public_accessor_for_the_path_or_the_descriptor(self):
        public = sorted(name for name in dir(CorpusOwnershipScope)
                        if not name.startswith("_"))
        self.assertEqual(public, ["admit_window", "corpus_clock_authority",
                                  "corpus_id", "delete_not_after",
                                  "manifest_sha256", "opened_at", "release",
                                  "to_dict"])
        # Asserted per capability as well as in aggregate: the aggregate alone
        # gave "the descriptor escaped" and "the body escaped" one witness.
        self.assertNotIn("directory_fd", public)
        self.assertNotIn("manifest", public)
        # §5.27: the reconstructed authority does not leave either.
        self.assertNotIn("lock", public)
        self.assertNotIn("capture_plan", public)
        self.assertNotIn("envelope", public)
        self.assertNotIn("eligible_trials", public)
        with self.create() as corpus:
            rendered = json.dumps(corpus.to_dict())
            self.assertNotIn(self.root, rendered)
            self.assertNotIn("path", corpus._verified_body())
            self.assertNotIn(self.root, repr(corpus))

    def test_the_restricted_descriptor_accessor_is_statically_restricted(self):
        """The instrument §5.24 already uses, applied to the capability §5.26
        says must not leave the scope."""
        offenders = {}
        for path in sorted(pathlib.Path(".").glob("*.py")):
            if path.name.startswith("test_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            hits = {node.attr for node in ast.walk(tree)
                    if isinstance(node, ast.Attribute)
                    and node.attr in self.RESTRICTED}
            hits |= {node.name for node in ast.walk(tree)
                     if isinstance(node, ast.FunctionDef)
                     and node.name in self.RESTRICTED}
            if hits and path.name != "rf_corpus_namespace.py":
                offenders[path.name] = sorted(hits)
        self.assertEqual(offenders, {})

    def test_a_released_scope_answers_nothing(self):
        corpus = self.create()
        corpus.release()
        with self.assertRaises(NamespaceRefused) as caught:
            corpus.corpus_id
        self.assertEqual(caught.exception.code, NAMESPACE_SCOPE_RELEASED)

    def test_a_released_scope_holds_no_descriptor(self):
        """Separated from the refusal above so that "the scope still answers"
        and "the descriptor leaked" cannot share one witness."""
        corpus = self.create()
        # Reached through the opaque state, not through an accessor: §5.27
        # deleted `_directory_fd()` because it had no production caller and
        # handed out a capability that outlived the ownership it came from.
        held = corpus._live().dir_fd
        corpus.release()
        with self.assertRaises(OSError) as closed:
            os.fstat(held)
        self.assertEqual(closed.exception.errno, errno.EBADF)

    def test_releasing_twice_is_not_an_error(self):
        corpus = self.create()
        corpus.release()
        corpus.release()

    def test_a_scope_cannot_be_constructed(self):
        with self.assertRaises(NamespaceRefused):
            CorpusOwnershipScope(None)

    def test_the_scope_says_what_is_not_built(self):
        with self.create() as corpus:
            data = corpus.to_dict()
        self.assertEqual(data["membership_journal"], "NOT BUILT")
        self.assertEqual(data["publisher"], "NOT BUILT")
        self.assertFalse(data["path_exposed"])
        self.assertFalse(data["descriptor_exposed"])

    def test_the_verified_body_is_not_public_authority(self):
        """The next slice must not take these dictionaries and rebuild types
        inside each entrypoint. One factory reconstructs once, binds the exact
        nominal objects in opaque state, and admission consumes only that."""
        with self.create() as corpus:
            self.assertFalse(hasattr(corpus, "manifest"))
            body = corpus._verified_body()
            self.assertEqual(type(body["envelope"]), dict)
            body["envelope"] = "tampered"
            self.assertNotEqual(corpus._verified_body()["envelope"],
                                "tampered")


class _SyscallTrace:
    """A call-through recorder over one syscall boundary.

    **Not a mock filesystem.** The real call runs; this records what it was
    asked to do. Identity is `(st_dev, st_ino, type)` and never the descriptor
    number, because descriptors are reused -- `fd == 4` twice is not the same
    object twice, and an ordering proof built on the integer would be proving
    something about allocation.

    What a trace establishes: the required durability protocol was **issued**,
    against the expected opened objects, in the required order, before anything
    read the result. What it does not establish: survival after power loss.
    That rests on the declared local-ext4 and operating-system contract, and no
    test in this repository can exercise it.
    """

    def __init__(self):
        self.calls = []
        self._seq = 0

    def _identify(self, fd, kind):
        self._seq += 1
        try:
            info = _REAL_FSTAT(fd)
            ident = (info.st_dev, info.st_ino,
                     "DIR" if stat.S_ISDIR(info.st_mode) else
                     "REG" if stat.S_ISREG(info.st_mode) else "OTHER")
        except OSError:                                    # pragma: no cover
            ident = (None, None, "UNKNOWN")
        self.calls.append({"seq": self._seq, "kind": kind,
                           "dev": ident[0], "ino": ident[1], "type": ident[2]})
        return self.calls[-1]

    def fsync(self, fd):
        self._identify(fd, "fsync")
        return _REAL_FSYNC(fd)

    def opened(self, fd):
        self._identify(fd, "open")
        return fd

    def seq_of(self, kind, ident, occurrence=0):
        hits = [c["seq"] for c in self.calls
                if c["kind"] == kind and (c["dev"], c["ino"]) == ident]
        return hits[occurrence] if len(hits) > occurrence else None


class DurabilityProtocolTests(NamespaceFixture):
    """The protocol was issued, in order. Not a power-loss experiment."""

    def _trace_a_creation(self):
        trace = _SyscallTrace()

        def recording_open(*args, **kwargs):
            fd = _REAL_OPEN(*args, **kwargs)
            trace.opened(fd)
            return fd

        with mock.patch.object(namespace.os, "fsync", trace.fsync), \
                mock.patch.object(namespace.os, "open", recording_open):
            with self.create():
                pass
        directory = os.stat(self.path())
        manifest = os.stat(os.path.join(self.path(), MANIFEST_NAME))
        return (trace, (directory.st_dev, directory.st_ino),
                (manifest.st_dev, manifest.st_ino))

    def test_the_manifest_fsync_precedes_the_directory_fsync_and_the_readback(self):
        trace, dir_id, man_id = self._trace_a_creation()
        file_sync = trace.seq_of("fsync", man_id)
        dir_sync = trace.seq_of("fsync", dir_id)
        # The readback is the SECOND open of the manifest object: the first
        # created it.
        readback = trace.seq_of("open", man_id, occurrence=1)
        self.assertIsNotNone(file_sync, "no fsync reached the manifest")
        self.assertIsNotNone(dir_sync, "no fsync reached the namespace directory")
        self.assertIsNotNone(readback, "the manifest was never read back")
        self.assertLess(file_sync, dir_sync,
                        "the file fsync must precede the directory fsync: one "
                        "makes the bytes survive, the other makes the name "
                        "survive, and neither implies the other")
        self.assertLess(dir_sync, readback,
                        "nothing may read the result before both syncs")

    def test_an_fsync_reaches_the_manifest_itself(self):
        """Its own test, not half of a set comparison: removing the file sync
        and removing the directory sync are different defects."""
        trace, _dir_id, man_id = self._trace_a_creation()
        self.assertIsNotNone(trace.seq_of("fsync", man_id),
                             "no fsync reached the manifest; its bytes are not "
                             "durable")

    def test_an_fsync_reaches_the_namespace_directory(self):
        trace, dir_id, _man_id = self._trace_a_creation()
        self.assertIsNotNone(trace.seq_of("fsync", dir_id),
                             "no fsync reached the directory; the manifest's "
                             "NAME is not durable")

    def test_a_manifest_fsync_failure_stops_everything_after_it(self):
        """Not swallowed, and not followed by the directory sync, the readback
        or a minted scope."""
        trace = _SyscallTrace()

        def failing_fsync(fd):
            record = trace._identify(fd, "fsync")
            if record["type"] == "REG":
                raise OSError(errno.EIO, "injected manifest fsync failure")
            return _REAL_FSYNC(fd)

        with mock.patch.object(namespace.os, "fsync", failing_fsync):
            with self.assertRaises(OSError) as caught:
                self.create()
        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertEqual([c["type"] for c in trace.calls], ["REG"],
                         "the directory fsync ran after the file fsync failed")

    def _directory_fsync_failure(self):
        """One injected failure, three independent assertions below.

        This was a single test asserting the errno, the sync ordering AND the
        open count, which handed `remove the sidecar fsync` and `omit sidecar
        readback` the same witness.
        """
        trace = _SyscallTrace()

        def failing_fsync(fd):
            record = trace._identify(fd, "fsync")
            if record["type"] == "DIR":
                raise OSError(errno.EIO, "injected directory fsync failure")
            return _REAL_FSYNC(fd)

        opened = []

        def recording_open(*args, **kwargs):
            fd = _REAL_OPEN(*args, **kwargs)
            opened.append(fd)
            return fd

        with mock.patch.object(namespace.os, "fsync", failing_fsync), \
                mock.patch.object(namespace.os, "open", recording_open):
            with self.assertRaises(OSError) as caught:
                self.create()
        return trace, len(opened), caught.exception

    def test_a_directory_fsync_failure_is_not_swallowed(self):
        """Honestly a post-create failure: the artefact exists and the protocol
        governs it. What must not happen is a scope over an unverified corpus."""
        _, _, error = self._directory_fsync_failure()
        self.assertEqual(error.errno, errno.EIO)

    def test_the_directory_fsync_is_attempted_last_and_exactly_once(self):
        """The sidecar, journal and manifest are durable before their names."""
        trace, _, _ = self._directory_fsync_failure()
        types = [call["type"] for call in trace.calls]
        self.assertEqual(types[-2:], ["REG", "DIR"])
        self.assertEqual(types.count("DIR"), 1)
        self.assertEqual(types.count("REG"), 3,
                         "the sidecar, journal and manifest are each synced")

    def test_the_readback_does_not_run_after_a_directory_fsync_failure(self):
        """The directory, sidecar create/read, journal create/read, manifest.
        A seventh open would be the post-fsync readback."""
        _, opens, _ = self._directory_fsync_failure()
        self.assertEqual(opens, 6,
                         "an extra open would be the readback the failed "
                         "directory fsync must have stopped")


class ForeignOwnershipTests(NamespaceFixture):
    """The owner check, which an unprivileged test cannot reach any other way."""

    def test_a_directory_owned_by_another_uid_is_refused(self):
        """`fstat` is delegated and its `st_uid` substituted. The filesystem is
        real; one field of one syscall's answer is not, because a test that
        could genuinely produce a foreign-owned directory would need to be
        root, and a control nobody can run is not a control."""
        foreign = os.getuid() + 1

        def lying_fstat(fd):
            info = _REAL_FSTAT(fd)
            fields = list(info)
            fields[4] = foreign                # st_uid
            return os.stat_result(tuple(fields))

        with mock.patch.object(namespace.os, "fstat", lying_fstat):
            with self.assertRaises(NamespaceRefused) as caught:
                self.create()
        self.assertEqual(caught.exception.code, NAMESPACE_OWNER_MISMATCH)
        self.assertFalse(
            os.path.exists(os.path.join(self.path(), MANIFEST_NAME)),
            "the refusal must precede the manifest being created")


class RaceOnTheManifestTests(NamespaceFixture):
    """`O_EXCL` defends one window, so the window is what the test reproduces."""

    COMPETITOR = b"another writer got here first"

    def _race(self):
        """Create a competing manifest in the one window `O_EXCL` defends."""
        state = {"ran": False}

        def interpose():
            state["ran"] = True
            with open(os.path.join(self.path(), MANIFEST_NAME), "wb") as handle:
                handle.write(self.COMPETITOR)

        outcome = {}
        with mock.patch.object(namespace, "_between_directory_and_manifest",
                               interpose):
            try:
                self.create().release()
                outcome["raised"] = None
            except BaseException as exc:                   # noqa: BLE001
                outcome["raised"] = exc
        outcome["seam_ran"] = state["ran"]
        return outcome

    # Three assertions that were one test. Three different defects -- O_EXCL
    # removed, EEXIST unmapped, the seam never called -- landed on it together
    # and produced identical failing sets, so it could not tell them apart.

    def test_the_race_seam_is_invoked_during_creation(self):
        """Seam use, recorded directly. No competitor, no refusal, no skip."""
        calls = []
        with mock.patch.object(namespace, "_between_directory_and_manifest",
                               lambda: calls.append(1)):
            self.create().release()
        self.assertEqual(len(calls), 1,
                         "the window O_EXCL defends was never opened")

    def test_the_manifest_is_created_exclusively(self):
        """`O_EXCL` read off the open flags, not inferred from an outcome.

        Testing this through "a competitor survived" routes it through the
        seam and the EEXIST mapping, so removing any of the three lands on one
        witness. The flag is a fact about the call and is observable on the
        ordinary path.
        """
        seen = []
        real_open = _REAL_OPEN

        def recording_open(path, flags, *args, **kwargs):
            if path == MANIFEST_NAME:
                seen.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(namespace.os, "open", recording_open):
            self.create().release()
        self.assertTrue(seen, "the manifest was never opened by name")
        self.assertTrue(seen[0] & os.O_EXCL,
                        "the manifest is created without O_EXCL")
        self.assertFalse(seen[0] & os.O_TRUNC,
                         "the manifest is created with O_TRUNC")

    def test_an_existing_manifest_is_reported_as_the_declared_refusal(self):
        """Classification, stimulated directly.

        The delegate raises `EEXIST` where the exclusive create would, so this
        exercises the mapping without needing a competitor to exist -- and so
        without depending on the seam that would create one.
        """
        real_open = _REAL_OPEN

        def refusing_open(path, flags, *args, **kwargs):
            if path == MANIFEST_NAME and flags & os.O_CREAT:
                raise FileExistsError(errno.EEXIST, "injected", path)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(namespace.os, "open", refusing_open):
            with self.assertRaises(ManifestRefused) as caught:
                self.create()
        self.assertEqual(caught.exception.code, MANIFEST_ALREADY_PRESENT)

    def test_a_real_competing_manifest_is_not_overwritten(self):
        """The behavioural case the three above decompose. All three
        mutations fail this, which is the honest shared consequence."""
        outcome = self._race()
        self.assertTrue(outcome["seam_ran"])
        self.assertIsInstance(outcome["raised"], ManifestRefused)
        self.assertEqual(
            pathlib.Path(self.path(), MANIFEST_NAME).read_bytes(),
            self.COMPETITOR)

    def test_the_seam_is_internal_and_not_a_parameter(self):
        import inspect
        for act in (create_corpus_namespace, open_corpus_namespace):
            for name in inspect.signature(act).parameters:
                self.assertNotIn("hook", name)
                self.assertNotIn("callback", name)
        self.assertIsNone(namespace._between_directory_and_manifest())


class MagicCollisionTests(unittest.TestCase):
    """B7's isolated witness: the detonation is coarse, this is the discriminator."""

    def test_the_module_refuses_colliding_magic_values(self):
        """Imported in a subprocess from a copy with the collision introduced,
        because the invariant is enforced at import and an import that has
        already happened cannot be re-run in this interpreter."""
        source = pathlib.Path("rf_corpus_manifest.py").read_text(encoding="utf-8")
        # Rewritten by locating the assignment line, not by matching the byte
        # literal: a test that has to escape `\x89` correctly to do its job is
        # a test that silently stops doing it the first time someone gets the
        # escaping wrong.
        lines = source.splitlines()
        hits = [i for i, line in enumerate(lines)
                if line.startswith("IQM_MAGIC = ")]
        self.assertEqual(len(hits), 1, "IQM_MAGIC is not assigned exactly once")
        lines[hits[0]] = "IQM_MAGIC = IQC_MAGIC"
        collided = "\n".join(lines)
        self.assertNotEqual(collided, source)
        scratch = tempfile.mkdtemp(prefix="scythe-magic-")
        try:
            pathlib.Path(scratch, "colliding_manifest.py").write_text(
                collided, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, sys.argv[1]); "
                 "sys.path.insert(0, sys.argv[2]); "
                 "import colliding_manifest", scratch,
                 os.path.dirname(os.path.abspath(__file__))],
                capture_output=True, text=True, timeout=120)
            self.assertNotEqual(result.returncode, 0,
                                "a colliding magic imported cleanly")
            self.assertIn("readable as an IQ window", result.stderr)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_the_invariant_is_not_an_assert(self):
        """`python -O` strips `assert`, so an invariant that must hold in every
        interpreter cannot be one."""
        source = pathlib.Path("rf_corpus_manifest.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        asserts = [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]
        self.assertEqual(asserts, [], "a module-level invariant left as assert")


class ManifestFormatTests(unittest.TestCase):
    """The artefact, exercised without a filesystem."""

    def setUp(self):
        self.lock = _lock()
        self.retention = CapturedCorpusRetention(delete_not_after=DEADLINE)
        self.body = manifest_body(lock=self.lock, retention=self.retention)

    def test_a_window_is_not_readable_as_a_manifest(self):
        framed = IQC_MAGIC + struct.pack("<H", 1) + struct.pack("<I", 2) + b"{}"
        with self.assertRaises(ManifestRefused) as caught:
            parse_manifest(framed)
        self.assertEqual(caught.exception.code, MANIFEST_FRAMING_REFUSED)
        self.assertIn(".iqc", caught.exception.detail)

    def test_trailing_bytes_are_refused_not_ignored(self):
        with self.assertRaises(ManifestRefused) as caught:
            parse_manifest(frame_manifest(self.body) + b"\x00")
        self.assertEqual(caught.exception.code, MANIFEST_TRAILING_BYTES)

    def test_a_length_field_over_the_bound_refuses_before_allocating(self):
        framed = frame_manifest(self.body)
        lying = (framed[:10] + struct.pack("<I", IQM_MAX_BODY_BYTES + 1)
                 + framed[14:])
        with self.assertRaises(ManifestRefused) as caught:
            parse_manifest(lying)
        self.assertEqual(caught.exception.code, MANIFEST_BODY_TOO_LARGE)

    def test_a_respaced_body_is_refused(self):
        """Two spellings of one manifest would be two digests and two
        identities for one corpus."""
        loose = json.dumps(self.body, sort_keys=True, indent=1).encode("utf-8")
        framed = (IQM_MAGIC + struct.pack("<H", IQM_FORMAT_VERSION)
                  + struct.pack("<I", len(loose)) + loose)
        with self.assertRaises(ManifestRefused) as caught:
            parse_manifest(framed)
        self.assertEqual(caught.exception.code, MANIFEST_NOT_CANONICAL)

    def test_a_bare_nan_is_refused_at_write_and_at_parse(self):
        with self.assertRaises(ManifestRefused):
            canonical_body_bytes(dict(self.body, opened_at=float("nan")))
        raw = b'{"opened_at":NaN}'
        framed = (IQM_MAGIC + struct.pack("<H", IQM_FORMAT_VERSION)
                  + struct.pack("<I", len(raw)) + raw)
        with self.assertRaises(ManifestRefused) as caught:
            parse_manifest(framed)
        self.assertEqual(caught.exception.code, MANIFEST_NOT_CANONICAL)

    def test_a_missing_declared_field_is_refused(self):
        short = dict(self.body)
        short.pop("capture_plan_digest")
        with self.assertRaises(ManifestRefused) as caught:
            frame_manifest(short)
        self.assertEqual(caught.exception.code, MANIFEST_FIELD_NOT_EMITTED)

    def test_a_field_no_authority_declared_is_refused(self):
        with self.assertRaises(ManifestRefused) as caught:
            frame_manifest(dict(self.body, operator_note="looked fine"))
        self.assertEqual(caught.exception.code, MANIFEST_FIELD_NO_AUTHORITY)

    def test_the_digest_is_not_in_the_body(self):
        """A digest cannot cover the bytes that carry it."""
        self.assertNotIn("manifest_sha256", required_manifest_fields())
        self.assertFalse(format_declaration()["digest_in_body"])
        self.assertTrue(format_declaration()["distinct_from_iqc_magic"])

    def test_the_required_set_is_derived_from_the_lock_not_transcribed(self):
        from rf_validation_manifest import PromotionCorpusLock
        for field in PromotionCorpusLock.__dataclass_fields__:
            self.assertIn(field, required_manifest_fields(), field)

    def test_the_declaration_digest_has_one_implementation(self):
        """The envelope and the plan held byte-identical copies of this rule
        before §5.26 needed a third."""
        self.assertEqual(self.lock.envelope.digest(),
                         declaration_digest(self.lock.envelope.to_dict()))
        self.assertEqual(self.lock.capture_plan.digest(),
                         declaration_digest(self.lock.capture_plan.to_dict()))


class DeclarationsMustAgreeTests(NamespaceFixture):
    """A manifest that disagrees with itself is refused; who wrote it is not
    a question a file in a writable directory can answer."""

    def test_a_stored_declaration_that_does_not_digest_to_its_frozen_value(self):
        with self.create():
            pass
        path = pathlib.Path(self.path(), MANIFEST_NAME)
        framed = path.read_bytes()
        body, _digest = parse_manifest(framed)
        body["envelope_digest"] = "blake2s:" + "0" * 32
        os.chmod(path, 0o600)
        path.write_bytes(frame_manifest(body))
        with self.assertRaises(ManifestRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, MANIFEST_DECLARATION_DISAGREES)

    def test_a_corpus_opened_under_other_strata_definitions_is_refused(self):
        with self.create():
            pass
        path = pathlib.Path(self.path(), MANIFEST_NAME)
        body, _digest = parse_manifest(path.read_bytes())
        body["strata_definition_revision"] = "rf-null-strata.v9"
        path.write_bytes(frame_manifest(body))
        with self.assertRaises(ManifestRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, MANIFEST_DECLARATION_DISAGREES)
        self.assertEqual(self.lock.strata_definition_revision,
                         STRATA_DEFINITION_REVISION)


class BoundedSliceTests(NamespaceFixture):
    """What this slice does not claim, asserted so it cannot be assumed."""

    def test_the_status_names_what_is_not_built(self):
        status = namespace_status()
        self.assertFalse(status["production_creation_authorised"])
        for owed in ("FINAL-DEPENDENT JOURNAL RECOVERY", "PUBLISHER", "SEQUENCE STATE",
                     "RING LIFETIME IDENTITY"):
            self.assertIn(owed, status["not_built"], owed)
        # 3c-wire built these two; consumption is still not compulsion.
        self.assertIn("CLOCK PROVIDER", status["built"])
        self.assertIn("ADMISSION CONSUMPTION OF THIS SCOPE", status["built"])
        self.assertTrue(status["consumed_by_admission"])
        self.assertFalse(status["compelled_path_to_membership"])
        # §5.27 built it, so it is no longer owed -- and the section it belongs
        # to is still not implemented, which is a different claim.
        self.assertIn("TYPED RECONSTRUCTION AND OPAQUE BINDING", status["built"])
        self.assertIn("MEMBERSHIP JOURNAL CORE", status["built"])
        self.assertNotIn("TYPED LOCK RECONSTRUCTION", status["not_built"])
        self.assertFalse(status["section_implemented"],
                         "§5.26 is not implemented by this sub-slice")

    def test_the_two_vocabularies_are_disjoint(self):
        self.assertEqual(set(NAMESPACE_REFUSALS) & set(MANIFEST_REFUSALS), set())
        self.assertEqual(set(NAMESPACE_REFUSALS) & set(JOURNAL_REFUSALS), set())
        self.assertEqual(set(MANIFEST_REFUSALS) & set(JOURNAL_REFUSALS), set())
        for code in NAMESPACE_REFUSALS:
            self.assertTrue(code.startswith("NAMESPACE_"), code)
        for code in MANIFEST_REFUSALS:
            self.assertTrue(code.startswith("MANIFEST_"), code)
        for code in JOURNAL_REFUSALS:
            self.assertTrue(code.startswith("JOURNAL_"), code)

    def test_the_temporary_root_is_gone_after_a_test(self):
        """The operator's condition, exercised rather than promised."""
        scratch = tempfile.mkdtemp(prefix="scythe-corpus-lifecycle-")
        try:
            lock = _lock("corpus-x")
            with create_corpus_namespace(corpus_id="corpus-x", lock=lock,
                                         retention=self.retention,
                                         root=scratch):
                pass
            self.assertTrue(os.path.exists(os.path.join(scratch, "corpus-x")))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        self.assertFalse(os.path.exists(scratch))


class DirectCheckTests(NamespaceFixture):
    """Witnesses that do not reopen a corpus.

    `C2` -- making reopening refuse a namespace holding only its manifest --
    breaks every test that reopens, and so subsumed the only witnesses B8, D6,
    D7 and G2 had. A check that can only be observed through one code path has
    exactly one witness, and the broadest mutation on that path owns it.
    """

    def _body(self):
        return manifest_body(lock=self.lock, retention=self.retention)

    def test_a_declaration_that_does_not_digest_to_its_frozen_value(self):
        body = dict(self._body(), envelope_digest="blake2s:" + "0" * 32)
        with self.assertRaises(ManifestRefused) as caught:
            namespace._check_declarations(body)
        self.assertEqual(caught.exception.code, MANIFEST_DECLARATION_DISAGREES)

    def test_a_body_opened_under_other_strata_definitions(self):
        body = dict(self._body(), strata_definition_revision="rf-null-strata.v9")
        with self.assertRaises(ManifestRefused) as caught:
            namespace._check_declarations(body)
        self.assertEqual(caught.exception.code, MANIFEST_DECLARATION_DISAGREES)

    def test_a_permissive_manifest_descriptor(self):
        with self.create():
            pass
        target = os.path.join(self.path(), MANIFEST_NAME)
        os.chmod(target, 0o644)
        fd = os.open(target, os.O_RDONLY)
        try:
            with self.assertRaises(NamespaceRefused) as caught:
                namespace._check_manifest_file(fd, os.fstat(fd).st_dev)
        finally:
            os.close(fd)
        self.assertEqual(caught.exception.code, NAMESPACE_MODE_PERMISSIVE)

    def test_a_hard_linked_manifest_descriptor(self):
        with self.create():
            pass
        target = os.path.join(self.path(), MANIFEST_NAME)
        os.link(target, os.path.join(self.root, "second-name"))
        fd = os.open(target, os.O_RDONLY)
        try:
            with self.assertRaises(NamespaceRefused) as caught:
                namespace._check_manifest_file(fd, os.fstat(fd).st_dev)
        finally:
            os.close(fd)
        self.assertEqual(caught.exception.code, NAMESPACE_HARD_LINKED)

    def test_a_symlinked_namespace_does_not_silently_succeed(self):
        """`D4`'s own witness. Dropping `O_NOFOLLOW` follows the link and
        succeeds; classifying by errno still refuses, just with the wrong
        reason -- so "it refused at all" separates the two."""
        with self.create():
            pass
        link_root = tempfile.mkdtemp(prefix="scythe-corpus-link2-")
        self.addCleanup(shutil.rmtree, link_root, ignore_errors=True)
        os.symlink(self.path(), os.path.join(link_root, "corpus-a"))
        with self.assertRaises(Exception):
            open_corpus_namespace(corpus_id="corpus-a", root=link_root).release()

    def _create_under_hostile_umask(self, mask=0o300):
        """Create with a umask that strips owner bits, restoring it always.

        Measured: under umask 0300 a requested 0700 arrives as 0400, and so
        does a requested 0600. At an ordinary umask both explicit mode-setting
        calls are no-ops, which is why removing either failed nothing.
        """
        previous = os.umask(mask)
        try:
            try:
                self.create().release()
                return True
            except BaseException:                          # noqa: BLE001
                return False
        finally:
            os.umask(previous)

    def test_the_directory_mode_survives_a_hostile_umask(self):
        """Fails only when the directory chmod is gone.

        Separated from the manifest's mode by **what exists at the point of
        failure**: both mutations make creation abort at a mode check, so a
        test that asserted both modes gave them one witness. Without the
        directory chmod the directory itself is 0400 and no manifest is ever
        created; without the manifest's fchmod the directory is still 0700.
        """
        self._create_under_hostile_umask()
        self.assertEqual(stat.S_IMODE(os.stat(self.path()).st_mode),
                         CORPUS_DIRECTORY_MODE)

    def test_the_directory_mode_is_set_explicitly(self):
        """Recorded, not inferred from a hostile umask.

        The umask test below is behavioural and both mode mutations fail it.
        This one is a fact about the call, so it distinguishes them without a
        guard that another mutation can route into a skip.
        """
        seen = []
        with mock.patch.object(namespace.os, "chmod",
                               lambda p, m: seen.append((p, m)) or _REAL_CHMOD(p, m)):
            self.create().release()
        self.assertIn((self.path(), CORPUS_DIRECTORY_MODE), seen,
                      "the corpus directory's mode is left to the umask")

    def test_the_manifest_mode_is_set_explicitly(self):
        seen = []
        with mock.patch.object(namespace.os, "fchmod",
                               lambda fd, m: seen.append(m) or _REAL_FCHMOD(fd, m)):
            self.create().release()
        self.assertIn(CORPUS_FILE_MODE, seen,
                      "the manifest's mode is left to the umask")

    def test_a_corpus_is_creatable_under_a_hostile_umask(self):
        """Both mutations fail this, and that is correct: either one makes a
        corpus uncreatable in an environment nobody chose."""
        self.assertTrue(self._create_under_hostile_umask(),
                        "creation failed under a restrictive umask")


class DigestStabilityTests(unittest.TestCase):
    """`G1`'s witness: self-consistency is not stability.

    Changing the canonical separators inside `declaration_digest` fails nothing,
    because the frozen digest and the recomputed one both come from that one
    function and still agree. A format whose value nothing pins can be changed
    silently, so the value is pinned.
    """

    # The canonical form this pin covers, written out so a reviewer can verify
    # the literal below by hand rather than by running the code it constrains:
    #     {"members":[1,2],"schema":"scythe.test","z":null}
    # BLAKE2s-128 of those UTF-8 bytes, prefixed "blake2s:".
    DECLARATION = {"schema": "scythe.test", "members": [1, 2], "z": None}
    EXPECTED = "blake2s:25c3cbfaf4e0000c582978305bdab65b"

    def test_the_declaration_digest_is_pinned(self):
        self.assertEqual(declaration_digest(self.DECLARATION), self.EXPECTED)

    def test_the_pin_is_over_the_canonical_form(self):
        """Key order must not change the digest; spacing must not either,
        because there is only one spelling."""
        reordered = {"z": None, "members": [1, 2], "schema": "scythe.test"}
        self.assertEqual(declaration_digest(reordered), self.EXPECTED)


class EligibleSidecarTests(NamespaceFixture):
    """§5.27: the dependency is written first and the manifest records it."""

    def _names(self):
        return sorted(os.listdir(self.path()))

    def test_a_created_corpus_carries_both_declarations_and_the_journal(self):
        with self.create():
            pass
        self.assertEqual(self._names(),
                         [namespace.ELIGIBLE_NAME, MANIFEST_NAME,
                          namespace.JOURNAL_NAME])

    def test_the_sidecar_is_written_before_the_manifest(self):
        """Ordering read off the calls, not inferred from the outcome."""
        created = []
        real_open = _REAL_OPEN

        def recording_open(path, flags, *args, **kwargs):
            if flags & os.O_CREAT:
                created.append(path)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(namespace.os, "open", recording_open):
            self.create().release()
        self.assertEqual(created, [namespace.ELIGIBLE_NAME,
                                   namespace.JOURNAL_NAME, MANIFEST_NAME],
                         "the manifest must be the last creation record")

    def test_the_sidecar_is_read_back_before_the_manifest_is_created(self):
        """Creation verifies its own dependency before recording it.

        Distinct from "the sidecar is created first": a creation that wrote
        both and only verified at the end would publish a manifest naming an
        artefact nobody had read.
        """
        events = []
        real_open = _REAL_OPEN

        def recording_open(path, flags, *args, **kwargs):
            if path == namespace.ELIGIBLE_NAME and not flags & os.O_CREAT:
                events.append("SIDECAR_READBACK")
            if path == MANIFEST_NAME and flags & os.O_CREAT:
                events.append("MANIFEST_CREATED")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(namespace.os, "open", recording_open):
            self.create().release()
        self.assertIn("SIDECAR_READBACK", events)
        self.assertIn("MANIFEST_CREATED", events)
        self.assertLess(events.index("SIDECAR_READBACK"),
                        events.index("MANIFEST_CREATED"),
                        "the manifest was created before its dependency had "
                        "been read back")

    def test_the_sidecar_is_opened_for_reading_during_creation(self):
        """The readback itself, independent of ordering and of any fsync.

        Removing the readback previously showed up only in tests that other
        mutations also broke, so it had no witness of its own.
        """
        reads = []
        real_open = _REAL_OPEN

        def recording_open(path, flags, *args, **kwargs):
            if path == namespace.ELIGIBLE_NAME and not flags & os.O_CREAT:
                reads.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(namespace.os, "open", recording_open):
            self.create().release()
        # Twice, and both are required: the readback that must precede the
        # manifest, and the final verification pass after the directory fsync.
        # Dropping the first leaves one, which is what this witnesses.
        self.assertEqual(len(reads), 2,
                         "creation reads the sidecar back before the manifest "
                         "and again in the closing verification")

    def test_the_manifest_is_created_after_the_sidecar_is_durable(self):
        """Ordered against the sidecar's fsync rather than against the
        readback, so that removing the readback does not also break this."""
        events = []
        real_open, real_fsync = _REAL_OPEN, _REAL_FSYNC

        def recording_open(path, flags, *args, **kwargs):
            if path == MANIFEST_NAME and flags & os.O_CREAT:
                events.append("MANIFEST_CREATED")
            return real_open(path, flags, *args, **kwargs)

        def recording_fsync(fd):
            try:
                regular = stat.S_ISREG(_REAL_FSTAT(fd).st_mode)
            except OSError:                                # pragma: no cover
                regular = False
            if regular and "SIDECAR_FSYNC" not in events:
                events.append("SIDECAR_FSYNC")
            return real_fsync(fd)

        with mock.patch.object(namespace.os, "open", recording_open), \
                mock.patch.object(namespace.os, "fsync", recording_fsync):
            self.create().release()
        self.assertEqual(events[:2], ["SIDECAR_FSYNC", "MANIFEST_CREATED"],
                         "the manifest was created before its dependency was "
                         "durable")

    def test_no_sidecar_read_falls_between_the_manifest_and_its_verification(self):
        """Separates `the manifest is named before the sidecar is verified`
        from `the readback is omitted entirely`.

        Relocating the readback to after the manifest puts a sidecar read in
        that window; removing the readback puts nothing there. Every test that
        asserts the readback PRECEDES the manifest is broken by both, so
        neither had a witness of its own. The closing verification read, which
        happens after the directory sync, is deliberately outside the window.
        """
        events = []
        real_open = _REAL_OPEN

        def recording_open(path, flags, *args, **kwargs):
            if path == namespace.ELIGIBLE_NAME and not flags & os.O_CREAT:
                events.append("SIDECAR_READ")
            if path == MANIFEST_NAME:
                events.append("MANIFEST_CREATED" if flags & os.O_CREAT
                              else "MANIFEST_VERIFIED")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(namespace.os, "open", recording_open):
            self.create().release()
        # The window is bounded by the manifest's own two opens, not by the
        # directory fsync. Bounding it by the fsync made `the directory fsync
        # removed` break this test on the boundary's ABSENCE rather than on
        # the ordering, which left the relocation with no witness of its own.
        self.assertIn("MANIFEST_CREATED", events)
        self.assertIn("MANIFEST_VERIFIED", events)
        created = events.index("MANIFEST_CREATED")
        verified = events.index("MANIFEST_VERIFIED")
        self.assertLess(created, verified)
        self.assertNotIn("SIDECAR_READ", events[created:verified],
                         "the sidecar was verified after its manifest had "
                         "already been created")

    def test_the_directory_fsync_follows_both_names(self):
        trace = _SyscallTrace()
        with mock.patch.object(namespace.os, "fsync", trace.fsync):
            self.create().release()
        kinds = [c["type"] for c in trace.calls]
        self.assertEqual(kinds, ["REG", "REG", "REG", "DIR"],
                         "the directory fsync must follow all file syncs")

    def test_both_declarations_carry_5_20s_permissions(self):
        with self.create():
            pass
        for name in (namespace.ELIGIBLE_NAME, namespace.JOURNAL_NAME,
                     MANIFEST_NAME):
            info = os.stat(os.path.join(self.path(), name))
            self.assertEqual(stat.S_IMODE(info.st_mode), CORPUS_FILE_MODE, name)
            self.assertEqual(info.st_nlink, 1, name)

    def test_the_sidecar_mode_is_set_explicitly(self):
        seen = []
        with mock.patch.object(namespace.os, "fchmod",
                               lambda fd, m: seen.append(m) or _REAL_FCHMOD(fd, m)):
            self.create().release()
        self.assertEqual(seen, [CORPUS_FILE_MODE, CORPUS_FILE_MODE,
                                CORPUS_FILE_MODE],
                         "all files' modes are set explicitly, not left to "
                         "the umask")

    def test_a_missing_sidecar_refuses_to_reopen(self):
        with self.create():
            pass
        os.unlink(os.path.join(self.path(), namespace.ELIGIBLE_NAME))
        with self.assertRaises(EligibleSetRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, ELIGIBLE_ARTEFACT_NOT_FOUND)

    def test_a_missing_journal_under_a_v2_manifest_refuses_to_reopen(self):
        with self.create():
            pass
        os.unlink(os.path.join(self.path(), namespace.JOURNAL_NAME))
        with self.assertRaises(JournalRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, JOURNAL_NOT_FOUND)

    def test_a_nonempty_journal_waits_for_final_dependent_recovery(self):
        with self.create():
            pass
        dir_fd = os.open(self.path(), os.O_RDONLY | os.O_DIRECTORY)
        try:
            append_intent(dir_fd, intent_record(
                manifest_sha256="a" * 64,
                corpus_id="corpus-a",
                stratum="GAIN_STEPS",
                window_id="window-a",
                ring_lifetime_id="ring-a",
                expected_final_filename="b" * 64 + ".iqc",
                file_sha256="b" * 64,
                payload_sha256="c" * 64,
                previous_window_id=None,
                first_sample_index=0,
                envelope_digest="blake2s:" + "d" * 32,
                capture_plan_digest="blake2s:" + "e" * 32,
            ))
        finally:
            os.close(dir_fd)
        with self.assertRaises(NamespaceRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, NAMESPACE_RECOVERY_UNBUILT)

    def test_an_unaccounted_sidecar_refuses(self):
        """Reached directly: with the accepted contracts every valid corpus
        has a spur allocation, so this state is unreachable through a lock.
        The code that refuses it is still reached, by passing the allocation
        the plan would have declared -- `None`."""
        with self.create():
            pass
        fd = os.open(self.path(), os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(EligibleSetRefused) as caught:
                namespace._read_eligible_rows(fd, os.fstat(fd).st_dev, None)
        finally:
            os.close(fd)
        self.assertEqual(caught.exception.code, ELIGIBLE_ARTEFACT_UNEXPECTED)

    def test_a_tampered_sidecar_refuses(self):
        with self.create():
            pass
        target = pathlib.Path(self.path(), namespace.ELIGIBLE_NAME)
        blob = bytearray(target.read_bytes())
        blob[-2:] = b"0}"
        os.chmod(target, 0o600)
        target.write_bytes(bytes(blob))
        with self.assertRaises(EligibleSetRefused):
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)

    def test_a_symlinked_sidecar_is_refused(self):
        with self.create():
            pass
        target = os.path.join(self.path(), namespace.ELIGIBLE_NAME)
        moved = os.path.join(self.root, "elsewhere.iqe")
        shutil.move(target, moved)
        os.symlink(moved, target)
        with self.assertRaises(NamespaceRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, NAMESPACE_SYMLINK_REFUSED)

    def test_a_hard_linked_sidecar_is_refused(self):
        with self.create():
            pass
        os.link(os.path.join(self.path(), namespace.ELIGIBLE_NAME),
                os.path.join(self.root, "second-name.iqe"))
        with self.assertRaises(NamespaceRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, NAMESPACE_HARD_LINKED)


class InterruptedCreationTests(NamespaceFixture):
    """A durable sidecar with no manifest is an incomplete namespace."""

    def _interrupt_after_the_sidecar(self):
        real = namespace._write_file
        state = {}

        def once(dir_fd, name, chunks):
            if name == MANIFEST_NAME:
                raise RuntimeError("interrupted before the manifest")
            state["written"] = real(dir_fd, name, chunks)
            return state["written"]

        with mock.patch.object(namespace, "_write_file", once):
            with self.assertRaises(RuntimeError):
                self.create()
        return state

    def test_the_sidecar_survives_and_is_byte_identical(self):
        self._interrupt_after_the_sidecar()
        interrupted = pathlib.Path(self.path(),
                                   namespace.ELIGIBLE_NAME).read_bytes()
        scratch = tempfile.mkdtemp(prefix="scythe-corpus-complete-")
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        create_corpus_namespace(corpus_id="corpus-a", lock=self.lock,
                                retention=self.retention,
                                root=scratch).release()
        complete = pathlib.Path(scratch, "corpus-a",
                                namespace.ELIGIBLE_NAME).read_bytes()
        self.assertEqual(interrupted, complete,
                         "an interrupted creation's sidecar must be the bytes a "
                         "complete one writes")

    def test_it_cannot_be_opened(self):
        self._interrupt_after_the_sidecar()
        with self.assertRaises(ManifestRefused) as caught:
            open_corpus_namespace(corpus_id="corpus-a", root=self.root)
        self.assertEqual(caught.exception.code, MANIFEST_NOT_FOUND)

    def test_it_is_not_adopted_by_a_retry(self):
        """Creation does not overwrite or adopt it. The namespace is not
        empty, so creation refuses rather than completing someone else's."""
        self._interrupt_after_the_sidecar()
        with self.assertRaises(NamespaceRefused) as caught:
            self.create()
        self.assertEqual(caught.exception.code, NAMESPACE_HOLDS_ENTRIES)

    def test_it_mints_no_scope_and_no_manifest_exists(self):
        self._interrupt_after_the_sidecar()
        self.assertFalse(os.path.exists(os.path.join(self.path(),
                                                     MANIFEST_NAME)))
        self.assertEqual(sorted(os.listdir(self.path())),
                         [namespace.ELIGIBLE_NAME, namespace.JOURNAL_NAME])


class ClockAndAdmissionActionTests(NamespaceFixture):
    """3c-wire: the scope's internally acquired clock, and the one action
    through which admission consumes the scope."""

    def test_the_public_acts_install_the_real_clock_and_name_it(self):
        with self.create() as corpus:
            self.assertEqual(corpus.corpus_clock_authority, "POSIX_REALTIME")
            self.assertEqual(corpus.to_dict()["corpus_clock_authority"],
                             "POSIX_REALTIME")
        with open_corpus_namespace(corpus_id="corpus-a",
                                   root=self.root) as reopened:
            self.assertEqual(reopened.corpus_clock_authority, "POSIX_REALTIME")

    def test_an_injected_clock_is_undeclared_even_when_it_reads_time_time(self):
        """Identity, not behaviour. §5.26 point 5."""
        for clock in (lambda: OPENED_AT, lambda: time.time()):
            root = tempfile.mkdtemp(prefix="scythe-clock-")
            self.addCleanup(shutil.rmtree, root, ignore_errors=True)
            with namespace._create_corpus_namespace_with_clock(
                    corpus_id="corpus-a", lock=self.lock,
                    retention=self.retention, root=root, clock=clock) as corpus:
                self.assertEqual(corpus.corpus_clock_authority, "UNDECLARED")
            with namespace._open_corpus_namespace_with_clock(
                    corpus_id="corpus-a", root=root, clock=clock) as reopened:
                self.assertEqual(reopened.corpus_clock_authority, "UNDECLARED")
        with namespace._create_corpus_namespace_with_clock(
                corpus_id="corpus-b", lock=_lock("corpus-b"),
                retention=self.retention, root=self.root,
                clock=time.time) as exact:
            self.assertEqual(exact.corpus_clock_authority, "POSIX_REALTIME")

    def test_the_public_acts_take_no_clock(self):
        """§5.26 control A3 at the namespace: production creation and reopening
        have no clock parameter, so the seam is the test factory and only the
        test factory."""
        import inspect
        for act in (create_corpus_namespace, open_corpus_namespace):
            self.assertNotIn("clock", inspect.signature(act).parameters,
                             act.__name__)
            self.assertNotIn("now", inspect.signature(act).parameters,
                             act.__name__)

    def test_the_admission_action_answers_from_the_bound_envelope(self):
        chain = sorted(self.lock.envelope.admissible_chain_hashes())[0]
        with self.create() as corpus:
            admitted = corpus.admit_window(signal_chain_hash=chain)
            foreign = corpus.admit_window(signal_chain_hash="blake2s:" + "0" * 32)
        self.assertTrue(admitted.chain_admitted)
        self.assertFalse(foreign.chain_admitted)
        self.assertEqual(admitted.corpus_id, "corpus-a")
        self.assertEqual(admitted.envelope_digest, self.lock.envelope_digest)
        self.assertEqual(admitted.capture_plan_digest,
                         self.lock.capture_plan_digest)
        self.assertEqual(admitted.delete_not_after, DEADLINE)
        self.assertEqual(admitted.opened_at, OPENED_AT)
        self.assertEqual(admitted.corpus_clock_authority, "POSIX_REALTIME")

    def test_the_action_reads_now_from_the_scope_clock(self):
        root = tempfile.mkdtemp(prefix="scythe-clock-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        with namespace._create_corpus_namespace_with_clock(
                corpus_id="corpus-a", lock=self.lock, retention=self.retention,
                root=root, clock=lambda: OPENED_AT + 5.0) as corpus:
            terms = corpus.admit_window(signal_chain_hash="x")
        self.assertEqual(terms.now, OPENED_AT + 5.0)

    def test_the_action_hands_out_no_authority(self):
        """Classified by the surface walk's own rule, which skips methods that
        need an argument: the terms are strings, floats and one boolean."""
        with self.create() as corpus:
            terms = corpus.admit_window(signal_chain_hash="x")
            state = corpus._live()
            why = BoundNominalObjectTests._classify(
                BoundNominalObjectTests(), terms, state)
            for value in terms.__dict__.values():
                why.extend(BoundNominalObjectTests._classify(
                    BoundNominalObjectTests(), value, state))
        self.assertEqual(why, [])
        self.assertEqual(type(terms).__name__, "CorpusAdmissionTerms")
        self.assertTrue(dataclasses.is_dataclass(terms))
        self.assertTrue(type(terms).__dataclass_params__.frozen)

    def test_a_released_scope_refuses_the_action(self):
        corpus = self.create()
        corpus.release()
        with self.assertRaises(NamespaceRefused) as caught:
            corpus.admit_window(signal_chain_hash="x")
        self.assertEqual(caught.exception.code, NAMESPACE_SCOPE_RELEASED)


class BoundNominalObjectTests(NamespaceFixture):
    """Reconstructed once by the factory, bound opaquely, and never returned.

    Establishing reconstruction through DIAGNOSTICS rather than by receiving
    the objects is the point. An accessor returning the lock was written first
    and removed: the escaped object outlived the ownership it was supposed to
    prove, which is §5.24's returned-view defect one layer out.
    """

    def test_reconstruction_is_reported_without_handing_out_the_objects(self):
        with self.create() as corpus:
            data = corpus.to_dict()
        self.assertTrue(data["capture_plan_reconstructed"])
        self.assertEqual(data["reconstructed_types"],
                         ["CapturePlanDeclaration", "InstrumentChainEnvelope",
                          "PromotionCorpusLock"])
        self.assertEqual(data["reconstructed_envelope_digest"],
                         self.lock.envelope_digest)
        self.assertEqual(data["reconstructed_capture_plan_digest"],
                         self.lock.capture_plan_digest)
        self.assertEqual(
            data["eligible_trials_bound"],
            len(self.lock.capture_plan.spur_allocation.eligible_trials))
        # Everything reported is a string, a bool or an int.
        for key, value in data.items():
            self.assertIn(type(value).__name__,
                          ("str", "bool", "int", "list", "NoneType"), key)

    def test_reopening_reconstructs_to_the_same_digests(self):
        with self.create() as created:
            first = created.to_dict()["reconstructed_capture_plan_digest"]
        with open_corpus_namespace(corpus_id="corpus-a",
                                   root=self.root) as reopened:
            self.assertEqual(
                reopened.to_dict()["reconstructed_capture_plan_digest"], first)

    ELIGIBLE_ROW_KEYS = frozenset(
        ("spur_id", "tuning_id", "epoch_id", "chain_hash", "stability_class",
         "signed_baseband_hz", "confidence"))

    # BOUND_STATE was one class covering two different escapes --- handing
    # back a live bound object, and handing back a mutable container holding
    # authority --- so `a scope method returns mutable reconstructed state`
    # and `the live body is returned instead of a copy` shared a witness.
    ESCAPES = ("PATH", "DESCRIPTOR", "ELIGIBLE_ROWS", "AUTHORITY_OBJECT",
               "AUTHORITY_CONTAINER", "LIVE_OBJECT", "MUTABLE_STATE")

    def _fresh_scope(self):
        """One corpus per probe, in its own root.

        The walk must not invalidate the scope it is inspecting. Probing one
        scope with every zero-argument method lets `release()` -- or any other
        lifecycle action, including one added later -- make every subsequent
        accessor refuse and manufacture a clean result. A fresh scope per
        candidate removes that entirely, and needs no exclusion list to be
        trusted, which is the other way this could have been written.
        """
        root = tempfile.mkdtemp(prefix="scythe-surface-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return create_corpus_namespace(corpus_id="corpus-a", lock=self.lock,
                                       retention=self.retention, root=root)

    def _classify(self, value, state):
        authority = (PromotionCorpusLock, InstrumentChainEnvelope,
                     CapturePlanDeclaration)
        try:
            held = _REAL_FSTAT(state.dir_fd)
            held_id = (held.st_dev, held.st_ino)
        except OSError:                                    # pragma: no cover
            held_id = None
        why = []
        if isinstance(value, authority):
            why.append("AUTHORITY_OBJECT")
        if isinstance(value, str) and value == state.path:
            why.append("PATH")
        # Identified by the object it names, so a DUPLICATED descriptor -- a
        # different integer for the same open directory -- is caught too, and
        # an unrelated small integer is not.
        if (isinstance(value, int) and not isinstance(value, bool)
                and held_id is not None and 0 <= value < 1 << 20):
            try:
                info = _REAL_FSTAT(value)
                if (info.st_dev, info.st_ino) == held_id:
                    why.append("DESCRIPTOR")
            except (OSError, OverflowError, ValueError):
                pass
        if isinstance(value, (list, tuple, set, frozenset)):
            items = list(value)
            if any(isinstance(item, authority) for item in items):
                why.append("AUTHORITY_CONTAINER")
            if any((isinstance(item, Mapping)
                    and self.ELIGIBLE_ROW_KEYS <= set(item))
                   or type(item).__name__ == "EligibleSpurTrial"
                   for item in items):
                why.append("ELIGIBLE_ROWS")
        live = {id(state.body), id(state.envelope), id(state.capture_plan),
                id(state.lock)}
        if id(value) in live:
            why.append("LIVE_OBJECT")
        elif isinstance(value, dict) and any(
                isinstance(v, authority) for v in value.values()):
            why.append("MUTABLE_STATE")
        return why

    _WALK = None

    def _walk_once(self):
        """Walk every zero-argument member once, with a fresh scope per
        candidate, and cache the offences for the six class-specific tests.

        One walk, six independent assertions. The split is the point: this was
        a single test carrying all six escape classes, so five properties
        shared one witness and deleting it un-tested all of them at once --
        the combined-assertion defect, one layer out from where §5.24 found it.
        """
        cls = BoundNominalObjectTests
        if cls._WALK is not None:
            return cls._WALK
        names = [n for n in sorted(dir(CorpusOwnershipScope))
                 if n not in ("__class__", "__getattribute__", "__init__",
                              "__init_subclass__", "__subclasshook__")]
        offences = []
        probed = 0
        for name in names:
            corpus = self._fresh_scope()
            try:
                state = corpus._live()
                try:
                    attribute = getattr(corpus, name)
                except Exception:                          # noqa: BLE001
                    continue
                value = attribute
                if callable(attribute):
                    try:
                        value = attribute()
                    except TypeError:
                        continue
                    except Exception:                      # noqa: BLE001
                        continue
                probed += 1
                offences.extend((name, why)
                                for why in self._classify(value, state))
            finally:
                corpus.release()
        cls._WALK = {"names": names, "probed": probed, "offences": offences}
        return cls._WALK

    def _assert_no_escape(self, kind):
        walk = self._walk_once()
        self.assertGreater(len(walk["names"]), 8,
                           "the class was not actually walked")
        self.assertGreater(walk["probed"], 5, "nothing was actually invoked")
        self.assertEqual([n for n, why in walk["offences"] if why == kind], [],
                         f"a scope method hands out {kind}")

    def test_no_scope_method_hands_out_the_sidecar_path(self):
        self._assert_no_escape("PATH")

    def test_no_scope_method_hands_out_a_directory_descriptor(self):
        self._assert_no_escape("DESCRIPTOR")

    def test_no_scope_method_hands_out_the_eligible_rows(self):
        self._assert_no_escape("ELIGIBLE_ROWS")

    def test_no_scope_method_hands_out_an_authority_object(self):
        self._assert_no_escape("AUTHORITY_OBJECT")

    def test_no_scope_method_hands_out_an_authority_container(self):
        self._assert_no_escape("AUTHORITY_CONTAINER")

    def test_no_scope_method_hands_out_a_live_bound_object(self):
        self._assert_no_escape("LIVE_OBJECT")

    def test_no_scope_method_hands_out_mutable_reconstructed_state(self):
        self._assert_no_escape("MUTABLE_STATE")

    _CLASSIFIER = None

    def _classifier_once(self):
        """The walk's own control, computed once and asserted six times."""
        cls = BoundNominalObjectTests
        if cls._CLASSIFIER is not None:
            return cls._CLASSIFIER
        corpus = self._fresh_scope()
        try:
            state = corpus._live()
            rows = [t.to_dict() for t in
                    state.capture_plan.spur_allocation.eligible_trials[:2]]
            duplicated = os.dup(state.dir_fd)
            try:
                cases = {
                    "PATH": state.path,
                    "DESCRIPTOR": state.dir_fd,
                    "ELIGIBLE_ROWS": rows,
                    "AUTHORITY_OBJECT": state.lock,
                    "AUTHORITY_CONTAINER": [state.envelope],
                    "LIVE_OBJECT": state.body,
                    "MUTABLE_STATE": {"lock": state.lock},
                }
                result = {k: self._classify(v, state) for k, v in cases.items()}
                result["DESCRIPTOR/duplicated"] = self._classify(duplicated,
                                                                 state)
                result["_distinct_fd"] = duplicated != state.dir_fd
                result["_cases"] = sorted(cases)
                benign = {}
                for value in ("corpus-a", 3, 0, [], {}, None, 1.5,
                              state.manifest_sha256):
                    if value == state.dir_fd or value == state.path:
                        continue
                    benign[repr(value)[:24]] = self._classify(value, state)
                result["_benign"] = benign
            finally:
                os.close(duplicated)
        finally:
            corpus.release()
        cls._CLASSIFIER = result
        return result

    def test_the_walk_detects_a_path(self):
        self.assertIn("PATH", self._classifier_once()["PATH"])

    def test_the_walk_detects_a_descriptor(self):
        self.assertIn("DESCRIPTOR", self._classifier_once()["DESCRIPTOR"])

    def test_the_walk_detects_a_duplicated_descriptor(self):
        result = self._classifier_once()
        self.assertTrue(result["_distinct_fd"])
        self.assertIn("DESCRIPTOR", result["DESCRIPTOR/duplicated"],
                      "a duplicated descriptor is a different integer naming "
                      "the same open directory, and must be caught by "
                      "opened-object identity")

    def test_the_walk_detects_eligible_rows(self):
        self.assertIn("ELIGIBLE_ROWS", self._classifier_once()["ELIGIBLE_ROWS"])

    def test_the_walk_detects_an_authority_object(self):
        self.assertIn("AUTHORITY_OBJECT",
                      self._classifier_once()["AUTHORITY_OBJECT"])

    def test_the_walk_detects_an_authority_container(self):
        self.assertIn("AUTHORITY_CONTAINER",
                      self._classifier_once()["AUTHORITY_CONTAINER"])

    def test_the_walk_detects_a_live_bound_object(self):
        self.assertIn("LIVE_OBJECT", self._classifier_once()["LIVE_OBJECT"])

    def test_the_walk_detects_mutable_state(self):
        self.assertIn("MUTABLE_STATE", self._classifier_once()["MUTABLE_STATE"])

    def test_the_six_escape_classes_are_all_exercised(self):
        self.assertEqual(self._classifier_once()["_cases"],
                         sorted(self.ESCAPES))

    def test_the_walk_does_not_fire_on_ordinary_values(self):
        for label, why in self._classifier_once()["_benign"].items():
            with self.subTest(benign=label):
                self.assertEqual(why, [],
                                 "the walk fires on an ordinary value")


class UnwitnessedChecksTests(NamespaceFixture):
    """Three properties the §5.27 mutation sweep found had no test at all.

    Each was a check that existed in production and that no test reached, so
    deleting it changed nothing anywhere in the suite. A check nothing
    witnesses is indistinguishable from a comment.
    """

    def _reframe_sidecar(self, mutate):
        """Rewrite the sidecar in place, well-formed, with `mutate` applied to
        the parsed rows. Framing, count and order stay canonical so that the
        digest comparison is what the reopen reaches."""
        target = os.path.join(self.path(), namespace.ELIGIBLE_NAME)
        with open(target, "rb") as handle:
            blob = handle.read()
        declared = struct.unpack("<Q", blob[10:18])[0]
        stream = io.BytesIO(blob)
        records = list(artefact.read_records(stream.read,
                                             expected_count=declared))
        rows = [dict(r) for r in artefact.parse_rows(records)]
        mutate(rows)
        os.chmod(target, 0o600)
        with open(target, "wb") as handle:
            handle.write(b"".join(artefact.frame_records(rows)))
        os.chmod(target, 0o600)

    def test_a_sidecar_that_does_not_digest_to_the_frozen_value_is_refused(self):
        """The binding between the sidecar and the envelope the lock froze.
        Without this, a well-formed artefact holding DIFFERENT observations
        reopens as though it were the catalogue the plan was built on."""
        with self.create():
            pass
        original = None

        def bump(rows):
            nonlocal original
            original = rows[0]["signed_baseband_hz"]
            # Not a sort key, so order and count stay canonical and the digest
            # comparison is the check the reopen must reach.
            rows[0]["signed_baseband_hz"] = original + 1.0

        self._reframe_sidecar(bump)
        with self.assertRaises(EligibleSetRefused) as caught:
            with open_corpus_namespace(corpus_id="corpus-a", root=self.root):
                pass
        self.assertEqual(caught.exception.code, ELIGIBLE_DIGEST_DISAGREES)

    def test_a_manifest_owned_by_another_uid_is_refused(self):
        """A SECOND owner check, distinct from the directory's.

        `_check_directory` and `_check_manifest_file` each test `st_uid`. Only
        the directory one had a witness, so removing the file one was invisible.
        """
        with self.create():
            pass
        foreign = os.getuid() + 1

        def lying_fstat(descriptor):
            info = _REAL_FSTAT(descriptor)
            fields = list(info)
            fields[4] = foreign                # st_uid
            return os.stat_result(tuple(fields))

        fd = os.open(os.path.join(self.path(), MANIFEST_NAME), os.O_RDONLY)
        try:
            device = _REAL_FSTAT(fd).st_dev
            with mock.patch.object(namespace.os, "fstat", lying_fstat):
                with self.assertRaises(NamespaceRefused) as caught:
                    namespace._check_manifest_file(fd, device)
        finally:
            os.close(fd)
        self.assertEqual(caught.exception.code, NAMESPACE_OWNER_MISMATCH)

    def test_a_manifest_on_another_device_is_refused(self):
        """Every existing caller passed the manifest's OWN device, so the
        comparison could never fail and the check was never exercised."""
        with self.create():
            pass
        fd = os.open(os.path.join(self.path(), MANIFEST_NAME), os.O_RDONLY)
        try:
            elsewhere = _REAL_FSTAT(fd).st_dev + 1
            with self.assertRaises(NamespaceRefused) as caught:
                namespace._check_manifest_file(fd, elsewhere)
        finally:
            os.close(fd)
        self.assertEqual(caught.exception.code, NAMESPACE_DEVICE_MISMATCH)


if __name__ == "__main__":
    unittest.main()
