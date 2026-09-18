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
from unittest import mock
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
from rf_promotion_envelope import declaration_digest
from rf_validation_manifest import (
    STRATA_DEFINITION_REVISION, freeze_promotion_corpus,
)
from test_rf_promotion_envelope import _envelope, _plan

# The real syscalls, captured once at import. `rf_corpus_namespace.os` IS the
# global `os` module, so patching an attribute on it patches it for everyone --
# including a delegate that then calls through and recurses into itself. A
# call-through recorder has to hold the original, not look it up.
_REAL_FSYNC = os.fsync
_REAL_FSTAT = os.fstat
_REAL_OPEN = os.open

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
    RESTRICTED = ("_directory_fd", "_verified_body")

    def test_there_is_no_public_accessor_for_the_path_or_the_descriptor(self):
        public = sorted(name for name in dir(CorpusOwnershipScope)
                        if not name.startswith("_"))
        self.assertEqual(public, ["corpus_id", "delete_not_after",
                                  "manifest_sha256", "opened_at", "release",
                                  "to_dict"])
        # Asserted per capability as well as in aggregate: the aggregate alone
        # gave "the descriptor escaped" and "the body escaped" one witness.
        self.assertNotIn("directory_fd", public)
        self.assertNotIn("manifest", public)
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
        held = corpus._directory_fd()
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

    def test_a_directory_fsync_failure_stops_the_readback_and_the_scope(self):
        """Honestly a post-create failure: the artefact exists and the protocol
        governs it. What must not happen is a scope over an unverified corpus."""
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
            before = len(opened)
            with self.assertRaises(OSError) as caught:
                self.create()
            after = len(opened)
        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertEqual([c["type"] for c in trace.calls], ["REG", "DIR"])
        # The manifest was created and written; it was never reopened.
        self.assertEqual(after - before, 2,
                         "the directory and the manifest were opened once each; "
                         "a third open would be the readback")


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

    def test_the_race_seam_runs_between_creation_and_the_manifest(self):
        """Fails only when the seam is gone: the other two guard on it."""
        self.assertTrue(self._race()["seam_ran"],
                        "the window O_EXCL defends was never opened")

    def test_a_competing_manifest_is_not_overwritten(self):
        """Fails only when `O_EXCL` is gone.

        Guarded on the seam having run. Without the guard, deleting the seam
        also fails this -- there is no competitor to leave untouched -- and
        two different defects share one witness again.
        """
        outcome = self._race()
        if not outcome["seam_ran"]:
            self.skipTest("no competitor was created; the seam control owns this")
        self.assertIsNotNone(outcome["raised"],
                             "creation succeeded over an existing manifest")

    def test_the_competing_manifest_refusal_carries_the_declared_code(self):
        """Fails only when `EEXIST` is not mapped.

        Guarded on a refusal having happened at all, so the `O_EXCL` control --
        which produces no refusal -- does not also land here.
        """
        outcome = self._race()
        if not outcome["seam_ran"] or outcome["raised"] is None:
            self.skipTest("no refusal to classify; another control owns this")
        self.assertIsInstance(outcome["raised"], ManifestRefused)
        self.assertEqual(outcome["raised"].code, MANIFEST_ALREADY_PRESENT)
        self.assertEqual(
            pathlib.Path(self.path(), MANIFEST_NAME).read_bytes(),
            self.COMPETITOR, "the competitor's bytes were overwritten")

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
        for owed in ("MEMBERSHIP JOURNAL", "PUBLISHER", "SEQUENCE STATE",
                     "RING LIFETIME IDENTITY", "TYPED LOCK RECONSTRUCTION",
                     "ADMISSION CONSUMPTION OF THIS SCOPE"):
            self.assertIn(owed, status["not_built"], owed)
        self.assertFalse(status["consumed_by_admission"])
        self.assertFalse(status["section_implemented"],
                         "§5.26 is not implemented by this sub-slice")

    def test_the_two_vocabularies_are_disjoint(self):
        self.assertEqual(set(NAMESPACE_REFUSALS) & set(MANIFEST_REFUSALS), set())
        for code in NAMESPACE_REFUSALS:
            self.assertTrue(code.startswith("NAMESPACE_"), code)
        for code in MANIFEST_REFUSALS:
            self.assertTrue(code.startswith("MANIFEST_"), code)

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

    def test_the_manifest_mode_survives_a_hostile_umask(self):
        """Fails only when the manifest fchmod is gone."""
        self._create_under_hostile_umask()
        # Guarded on the DIRECTORY's mode, not on whether the manifest can be
        # stat'ed. A 0400 directory is readable and not executable, so
        # `stat()` on anything inside it raises PermissionError rather than
        # returning "absent" -- the guard would have failed for exactly the
        # reason it exists to defer, and did: it cost G5 its unique witness.
        if stat.S_IMODE(os.stat(self.path()).st_mode) != CORPUS_DIRECTORY_MODE:
            self.skipTest("the directory-mode control owns this case")
        self.assertEqual(
            stat.S_IMODE(os.stat(os.path.join(self.path(), MANIFEST_NAME)).st_mode),
            CORPUS_FILE_MODE)

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


if __name__ == "__main__":
    unittest.main()
