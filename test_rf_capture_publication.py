"""§5.20 steps 5-8 over real temporary filesystems.

Every test builds a fresh temporary root and reaches production namespace
resolution nowhere. The primitive under test is private and unwired; these are
its only callers, which is what 3c-core's boundary requires.

Split by PROPERTY throughout. Steps 5 and 7 are two durability facts, the two
digests are two questions, and "the bytes are right" is not "the name is right"
--- each gets a stimulus the others cannot move, so a mutation to one is
witnessed by one test rather than by a crowd.
"""

import errno
import hashlib
import os
import pathlib
import shutil
import stat
import struct
import tempfile
import unittest
from unittest import mock

import rf_capture_publication as publication
from rf_capture_format import (
    IQC_FRAMING_PREFIX_BYTES, IQC_MAGIC, IQC_MAX_HEADER_BYTES,
    canonical_header_bytes,
    canonical_member_name, framing_prefix, FramingRefused,
)
from rf_capture_admission import PublicationFailed
from rf_capture_publication import (
    DURABILITY_FAILURES, VerifiedFinal,
    PUBLICATION_ARGUMENT_TYPE_WRONG, PUBLICATION_DECLARED_LENGTH_DISAGREES,
    PUBLICATION_DIRECTORY_FSYNC_FAILED, PUBLICATION_FILENAME_DISAGREES,
    PUBLICATION_FILE_DIGEST_MISMATCH, PUBLICATION_FILE_FSYNC_FAILED,
    PUBLICATION_FINAL_IS_A_SYMLINK, PUBLICATION_FINAL_NAME_COUNT_WRONG,
    PUBLICATION_FINAL_NAME_EXISTS, PUBLICATION_FINAL_NOT_READABLE,
    PUBLICATION_FINAL_OBJECT_REFUSED, PUBLICATION_FRAMING_UNREADABLE,
    PUBLICATION_LINK_FAILED, PUBLICATION_PAYLOAD_DIGEST_MISMATCH,
    PUBLICATION_RESULT_UNCONSTRUCTIBLE, PUBLICATION_TRAILING_BYTES,
)

MODULE_STEM = "rf_capture_publication"
MODULE_NAME = MODULE_STEM + ".py"
_REAL_FSTAT = os.fstat
_PAYLOAD_BYTES = 256
TEMPORARY = "publication.tmp"


class _Publication:
    """What steps 1-4 hand over. Only the two facts step 8 reconciles."""

    def __init__(self, declared_payload_bytes, payload_sha256):
        self.declared_payload_bytes = declared_payload_bytes
        self.payload_sha256 = payload_sha256


class PublicationFixture(unittest.TestCase):
    """A real 0700 directory holding a real 0600 written temporary."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.dir_path = os.path.join(self.root, "corpus")
        os.mkdir(self.dir_path, 0o700)
        self.dir_fd = os.open(self.dir_path, os.O_RDONLY)
        self.addCleanup(self._close, self.dir_fd)

        header = {"schema": "scythe.iq-capture-window.v1",
                  "declared_payload_bytes": _PAYLOAD_BYTES}
        self.header_bytes = canonical_header_bytes(header)
        self.prefix = framing_prefix(self.header_bytes)
        self.payload = bytes(range(256)) * (_PAYLOAD_BYTES // 256)
        self.image = self.prefix + self.header_bytes + self.payload
        self.payload_sha256 = hashlib.sha256(self.payload).hexdigest()
        self.file_sha256 = hashlib.sha256(self.image).hexdigest()
        self.final_name = canonical_member_name(self.file_sha256)
        self.publication = _Publication(_PAYLOAD_BYTES, self.payload_sha256)
        self.fd = self._write_temporary(self.image)

    def _close(self, fd):
        try:
            os.close(fd)
        except OSError:
            pass

    def _write_temporary(self, image, name=TEMPORARY):
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
                     dir_fd=self.dir_fd)
        self.addCleanup(self._close, fd)
        written = 0
        while written < len(image):
            written += os.write(fd, image[written:])
        return fd

    def publish(self, **changes):
        kwargs = dict(fd=self.fd, dir_fd=self.dir_fd,
                      temporary_name=TEMPORARY, publication=self.publication,
                      intent_file_sha256=self.file_sha256)
        kwargs.update(changes)
        return publication._publish_and_verify(**kwargs)

    def names(self):
        return sorted(os.listdir(self.dir_path))


class HappyPath(PublicationFixture):
    def test_the_final_is_named_by_the_digest_of_its_own_bytes(self):
        result = self.publish()
        self.assertEqual(result.final_name,
                         canonical_member_name(self.file_sha256))

    def test_the_final_name_exists_in_the_namespace(self):
        self.publish()
        self.assertIn(self.final_name, self.names())

    def test_the_temporary_name_is_gone(self):
        self.publish()
        self.assertNotIn(TEMPORARY, self.names())

    def test_no_retained_temporary_is_reported(self):
        self.assertFalse(self.publish().temporary_name_retained)

    def test_the_bytes_behind_the_final_name_are_byte_identical(self):
        self.publish()
        self.assertEqual(
            pathlib.Path(self.dir_path, self.final_name).read_bytes(),
            self.image)

    def test_the_result_reports_the_payload_digest(self):
        self.assertEqual(self.publish().payload_sha256, self.payload_sha256)

    def test_the_result_reports_the_file_digest(self):
        self.assertEqual(self.publish().file_sha256, self.file_sha256)

    def test_the_result_reports_the_declared_length(self):
        self.assertEqual(self.publish().declared_payload_bytes, _PAYLOAD_BYTES)


class Durability(PublicationFixture):
    """Steps 5 and 7 are two facts. One control each, so a single
    "durability" assertion cannot pass while half of it is broken."""

    def _recorded(self):
        order = []
        real_fsync, real_link = os.fsync, os.link

        def watched_fsync(fd):
            info = os.fstat(fd)
            order.append("fsync-dir" if stat.S_ISDIR(info.st_mode)
                         else "fsync-file")
            return real_fsync(fd)

        def watched_link(*args, **kwargs):
            order.append("link")
            return real_link(*args, **kwargs)

        def watched_unlink(*args, **kwargs):
            order.append("unlink")
            return os.__dict__["unlink"].__wrapped__(*args, **kwargs) \
                if hasattr(os.unlink, "__wrapped__") else real_unlink(*args, **kwargs)

        real_unlink = os.unlink
        return order, watched_fsync, watched_link, watched_unlink, real_unlink

    def test_the_file_is_fsynced_before_the_link(self):
        order, wf, wl, wu, _ = self._recorded()
        with mock.patch.object(os, "fsync", wf), \
             mock.patch.object(os, "link", wl):
            self.publish()
        self.assertLess(order.index("fsync-file"), order.index("link"))

    def test_the_directory_fsync_follows_the_link(self):
        order, wf, wl, wu, _ = self._recorded()
        with mock.patch.object(os, "fsync", wf), \
             mock.patch.object(os, "link", wl):
            self.publish()
        self.assertLess(order.index("link"), order.index("fsync-dir"))

    def test_the_directory_is_fsynced_after_the_unlink_settles_both_names(self):
        order = []
        real_fsync, real_unlink = os.fsync, os.unlink

        def watched_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                order.append("fsync-dir")
            return real_fsync(fd)

        def watched_unlink(*args, **kwargs):
            order.append("unlink")
            return real_unlink(*args, **kwargs)

        with mock.patch.object(os, "fsync", watched_fsync), \
             mock.patch.object(os, "unlink", watched_unlink):
            self.publish()
        self.assertLess(order.index("unlink"), order.index("fsync-dir"))

    def test_no_directory_fsync_precedes_the_unlink(self):
        """"None before" is a different property from "one after".

        Removing the directory `fsync` entirely leaves this test GREEN --- there
        is then no fsync to precede anything --- so the control that moves the
        fsync earlier gets a witness the control that deletes it cannot take.
        """
        before_unlink = []
        real_fsync, real_unlink = os.fsync, os.unlink
        seen = []

        def watched_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                seen.append("dir")
            return real_fsync(fd)

        def watched_unlink(*args, **kwargs):
            before_unlink.append(len(seen))
            return real_unlink(*args, **kwargs)

        with mock.patch.object(os, "fsync", watched_fsync), \
             mock.patch.object(os, "unlink", watched_unlink):
            self.publish()
        self.assertEqual(before_unlink, [0])

    def test_a_file_fsync_failure_publishes_no_final_name(self):
        real_fsync = os.fsync

        def failing(fd):
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, "no")
            return real_fsync(fd)

        with mock.patch.object(os, "fsync", failing):
            with self.assertRaises(PublicationFailed) as caught:
                self.publish()
        self.assertEqual(caught.exception.code, PUBLICATION_FILE_FSYNC_FAILED)

    def test_a_file_fsync_failure_leaves_the_namespace_untouched(self):
        """Separated from the refusal: "it refused" and "it created nothing"
        are two facts, and a mutation can break the second alone."""
        real_fsync = os.fsync

        def failing(fd):
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, "no")
            return real_fsync(fd)

        with mock.patch.object(os, "fsync", failing):
            with self.assertRaises(PublicationFailed):
                self.publish()
        self.assertEqual(self.names(), [TEMPORARY])

    def test_a_directory_fsync_failure_mints_no_result(self):
        real_fsync = os.fsync

        def failing(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, "no")
            return real_fsync(fd)

        with mock.patch.object(os, "fsync", failing):
            with self.assertRaises(PublicationFailed) as caught:
                self.publish()
        self.assertEqual(caught.exception.code,
                         PUBLICATION_DIRECTORY_FSYNC_FAILED)


class NoReplacement(PublicationFixture):
    def test_an_existing_final_name_is_not_replaced(self):
        pathlib.Path(self.dir_path, self.final_name).write_bytes(b"occupied")
        with self.assertRaises(PublicationFailed) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, PUBLICATION_FINAL_NAME_EXISTS)

    def test_the_occupying_bytes_survive_the_refusal(self):
        """`rename` was measured to replace silently. This is the observation
        that distinguishes the two primitives, and it is not the refusal code."""
        pathlib.Path(self.dir_path, self.final_name).write_bytes(b"occupied")
        with self.assertRaises(PublicationFailed):
            self.publish()
        self.assertEqual(
            pathlib.Path(self.dir_path, self.final_name).read_bytes(),
            b"occupied")

    def test_a_link_failure_that_is_not_eexist_is_its_own_refusal(self):
        def failing(*args, **kwargs):
            raise OSError(errno.EXDEV, "cross-device")

        with mock.patch.object(os, "link", failing):
            with self.assertRaises(PublicationFailed) as caught:
                self.publish()
        self.assertEqual(caught.exception.code, PUBLICATION_LINK_FAILED)


class UnlinkFailureStillPublishes(PublicationFixture):
    """The successful final `link` is the point of no abandon."""

    def _unlink_fails(self):
        def failing(*args, **kwargs):
            raise OSError(errno.EPERM, "no")
        return mock.patch.object(os, "unlink", failing)

    def test_publication_succeeds(self):
        with self._unlink_fails():
            result = self.publish()
        self.assertEqual(type(result), VerifiedFinal)

    def test_the_retained_temporary_is_reported(self):
        with self._unlink_fails():
            result = self.publish()
        self.assertTrue(result.temporary_name_retained)

    def test_the_final_name_still_exists(self):
        with self._unlink_fails():
            self.publish()
        self.assertIn(self.final_name, self.names())

    def test_the_retained_name_is_the_same_inode_not_a_second_member(self):
        with self._unlink_fails():
            self.publish()
        a = os.stat(os.path.join(self.dir_path, TEMPORARY))
        b = os.stat(os.path.join(self.dir_path, self.final_name))
        self.assertEqual((a.st_dev, a.st_ino), (b.st_dev, b.st_ino))

    def test_an_unlink_failure_does_not_escape_as_an_oserror(self):
        """K22's own witness, and the reason its containment of the accounting
        control is strict rather than equal.

        A control that re-raises the `unlink` failure lets an `OSError` out. A
        control that mis-accounts the retained name raises `PublicationFailed`
        instead, so this test cannot see it --- which is the separation the two
        controls needed.
        """
        with self._unlink_fails():
            try:
                self.publish()
            except OSError as exc:
                self.fail(f"an unlink failure escaped as OSError: {exc}")
            except PublicationFailed:
                pass          # a different defect; not this property

    def test_the_directory_is_still_fsynced(self):
        """An `unlink` failure must not skip step 7."""
        seen = []
        real_fsync = os.fsync

        def watched(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                seen.append("dir")
            return real_fsync(fd)

        with self._unlink_fails(), mock.patch.object(os, "fsync", watched):
            self.publish()
        self.assertEqual(seen, ["dir"])


class Readback(PublicationFixture):
    def test_the_final_name_is_opened_afresh(self):
        """Not the descriptor already held. Verifying that one proves the bytes
        were written and nothing about which name reaches them."""
        opened = []
        real_open = os.open

        def watched(path, flags, *args, **kwargs):
            if path == self.final_name:
                opened.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(os, "open", watched):
            self.publish()
        self.assertEqual(len(opened), 1)

    def test_the_readback_refuses_to_follow_a_symlink(self):
        opened = []
        real_open = os.open

        def watched(path, flags, *args, **kwargs):
            if path == self.final_name:
                opened.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(os, "open", watched):
            self.publish()
        self.assertTrue(opened[0] & os.O_NOFOLLOW)

    def test_a_symlinked_final_is_refused(self):
        real_link = os.link

        def link_makes_a_symlink(src, dst, **kwargs):
            os.symlink(src, dst, dir_fd=kwargs.get("dst_dir_fd"))

        with mock.patch.object(os, "link", link_makes_a_symlink):
            with self.assertRaises(PublicationFailed) as caught:
                self.publish()
        self.assertEqual(caught.exception.code, PUBLICATION_FINAL_IS_A_SYMLINK)

    def test_a_missing_final_is_refused(self):
        real_unlink = os.unlink

        def unlink_removes_the_final(name, **kwargs):
            real_unlink(name, **kwargs)
            real_unlink(self.final_name, **kwargs)

        with mock.patch.object(os, "unlink", unlink_removes_the_final):
            with self.assertRaises(PublicationFailed) as caught:
                self.publish()
        self.assertEqual(caught.exception.code, PUBLICATION_FINAL_NOT_READABLE)

    def test_a_permissive_final_is_refused(self):
        real_link = os.link

        def link_then_widen(src, dst, **kwargs):
            real_link(src, dst, **kwargs)
            os.chmod(dst, 0o644, dir_fd=kwargs.get("dst_dir_fd"))

        with mock.patch.object(os, "link", link_then_widen):
            with self.assertRaises(PublicationFailed) as caught:
                self.publish()
        self.assertEqual(caught.exception.code,
                         PUBLICATION_FINAL_OBJECT_REFUSED)

    def test_a_third_name_for_the_member_is_refused(self):
        """Two names are accounted for while the temporary is retained. A third
        is a foreign hard link to a corpus member."""
        real_link = os.link

        def link_twice(src, dst, **kwargs):
            real_link(src, dst, **kwargs)
            real_link(src, dst + ".extra", **kwargs)

        with mock.patch.object(os, "link", link_twice):
            with self.assertRaises(PublicationFailed) as caught:
                self.publish()
        self.assertEqual(caught.exception.code,
                         PUBLICATION_FINAL_NAME_COUNT_WRONG)

    def test_a_member_on_another_device_is_refused(self):
        """Every ordinary caller passes the member's OWN device, so the
        comparison can never fail through the primitive and the check went
        unexercised. Called directly with a device it is not on, which is how
        the namespace's own file checks are exercised."""
        self.publish()
        fd = os.open(self.final_name, os.O_RDONLY, dir_fd=self.dir_fd)
        try:
            elsewhere = _REAL_FSTAT(fd).st_dev + 1
            with self.assertRaises(PublicationFailed) as caught:
                publication._check_member_object(fd, elsewhere,
                                                 expected_names=1)
        finally:
            os.close(fd)
        self.assertEqual(caught.exception.code,
                         PUBLICATION_FINAL_OBJECT_REFUSED)

    def test_a_member_owned_by_another_uid_is_refused(self):
        """A second owner check, distinct from the directory's. Only the
        directory had a witness, so removing this one was invisible."""
        self.publish()
        foreign = os.getuid() + 1

        def lying_fstat(descriptor):
            fields = list(_REAL_FSTAT(descriptor))
            fields[4] = foreign                        # st_uid
            return os.stat_result(tuple(fields))

        fd = os.open(self.final_name, os.O_RDONLY, dir_fd=self.dir_fd)
        try:
            device = _REAL_FSTAT(fd).st_dev
            with mock.patch.object(publication.os, "fstat", lying_fstat):
                with self.assertRaises(PublicationFailed) as caught:
                    publication._check_member_object(fd, device,
                                                     expected_names=1)
        finally:
            os.close(fd)
        self.assertEqual(caught.exception.code,
                         PUBLICATION_FINAL_OBJECT_REFUSED)

    def test_a_member_that_is_not_a_regular_file_is_refused(self):
        """The check the sweep could not see: no control and no test, so nothing
        would have reported its removal.

        A FIFO, not a directory. A directory is mode 0700, so with the
        regular-file check removed the MODE check fires and carries the same
        refusal code --- the stimulus has to pass every other check so that only
        this one can answer. A 0600 FIFO owned by this process on the corpus's
        own device, with one name, does.
        """
        os.mkfifo("pipe", 0o600, dir_fd=self.dir_fd)
        fd = os.open("pipe", os.O_RDONLY | os.O_NONBLOCK, dir_fd=self.dir_fd)
        try:
            info = _REAL_FSTAT(fd)
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            self.assertEqual(info.st_nlink, 1)
            with self.assertRaises(PublicationFailed) as caught:
                publication._check_member_object(fd, info.st_dev,
                                                 expected_names=1)
        finally:
            os.close(fd)
        self.assertEqual(caught.exception.code,
                         PUBLICATION_FINAL_OBJECT_REFUSED)

    def test_a_fragmented_read_is_accumulated(self):
        """`os.read` may return fewer bytes than asked for. A bare read here
        would disagree with every digest over an intact file."""
        real_read = os.read

        def one_byte(fd, count):
            return real_read(fd, 1)

        with mock.patch.object(os, "read", one_byte):
            result = self.publish()
        self.assertEqual(result.file_sha256, self.file_sha256)


class FramingAndLength(PublicationFixture):
    """Each corruption is applied to the temporary BEFORE publication, so the
    file that reaches step 8 through its final name is the damaged one."""

    def _republish(self, image):
        os.close(self.fd)
        os.unlink(TEMPORARY, dir_fd=self.dir_fd)
        self.fd = self._write_temporary(image)
        self.file_sha256 = hashlib.sha256(image).hexdigest()
        self.final_name = canonical_member_name(self.file_sha256)
        return self.publish(intent_file_sha256=self.file_sha256)

    def test_a_file_shorter_than_the_framing_prefix_refuses(self):
        with self.assertRaises(PublicationFailed) as caught:
            self._republish(self.image[:4])
        self.assertEqual(caught.exception.code, PUBLICATION_FRAMING_UNREADABLE)

    def test_a_wrong_magic_refuses(self):
        with self.assertRaises(PublicationFailed) as caught:
            self._republish(b"NOTSCYIQ" + self.image[8:])
        self.assertEqual(caught.exception.code, PUBLICATION_FRAMING_UNREADABLE)

    def test_a_wrong_format_version_refuses(self):
        damaged = (self.image[:len(IQC_MAGIC)] + struct.pack("<H", 99)
                   + self.image[len(IQC_MAGIC) + 2:])
        with self.assertRaises(PublicationFailed) as caught:
            self._republish(damaged)
        self.assertEqual(caught.exception.code, PUBLICATION_FRAMING_UNREADABLE)

    def test_an_overlong_header_length_refuses(self):
        """The stimulus isolates the BOUND, not the shortfall.

        A 1 MiB declared header over a 313-byte file trips the bound and then,
        with the bound removed, trips "shorter than the header it declares" ---
        which carries the SAME refusal code, so the mutation was invisible. The
        header length here is one byte over the maximum and the file is padded
        past it, so only the bound can answer.
        """
        over = IQC_MAX_HEADER_BYTES + 1
        damaged = (self.image[:len(IQC_MAGIC) + 2] + struct.pack("<I", over)
                   + self.header_bytes
                   + bytes(over - len(self.header_bytes)) + self.payload)
        self.assertGreater(len(damaged), IQC_FRAMING_PREFIX_BYTES + over)
        with self.assertRaises(PublicationFailed) as caught:
            self._republish(damaged)
        self.assertEqual(caught.exception.code, PUBLICATION_FRAMING_UNREADABLE)

    def test_trailing_bytes_are_refused(self):
        with self.assertRaises(PublicationFailed) as caught:
            self._republish(self.image + b"\x00")
        self.assertEqual(caught.exception.code, PUBLICATION_TRAILING_BYTES)

    def test_a_short_payload_refuses_on_the_declared_length(self):
        with self.assertRaises(PublicationFailed) as caught:
            self._republish(self.image[:-1])
        self.assertEqual(caught.exception.code,
                         PUBLICATION_DECLARED_LENGTH_DISAGREES)


class DigestAndName(PublicationFixture):
    def test_a_payload_that_does_not_match_the_header_digest_refuses(self):
        self.publication.payload_sha256 = "0" * 64
        with self.assertRaises(PublicationFailed) as caught:
            self.publish()
        self.assertEqual(caught.exception.code,
                         PUBLICATION_PAYLOAD_DIGEST_MISMATCH)

    def test_a_file_that_does_not_match_the_bound_intent_refuses(self):
        """The intent's identity, not the header's. A mutation that checks only
        the payload digest leaves this one failing."""
        wrong = hashlib.sha256(self.image + b"x").hexdigest()
        with self.assertRaises(PublicationFailed) as caught:
            self.publish(intent_file_sha256=wrong)
        self.assertEqual(caught.exception.code,
                         PUBLICATION_FILE_DIGEST_MISMATCH)

    def test_the_name_must_be_the_canonical_name_of_the_bytes(self):
        """Distinct from digest agreement: that asks whether the bytes are the
        intended bytes, this asks whether the NAME derives from them. A
        constant-name mutation satisfies the first and fails this."""
        with mock.patch.object(publication, "canonical_member_name",
                               side_effect=[self.final_name, "wrong.iqc"]):
            with self.assertRaises(PublicationFailed) as caught:
                self.publish()
        self.assertEqual(caught.exception.code, PUBLICATION_FILENAME_DISAGREES)

    def test_a_member_name_is_the_digest_and_the_suffix(self):
        self.assertEqual(canonical_member_name("a" * 64), "a" * 64 + ".iqc")

    def test_a_member_name_refuses_a_short_digest(self):
        with self.assertRaises(FramingRefused):
            canonical_member_name("abc")

    def test_a_member_name_refuses_uppercase_hex(self):
        with self.assertRaises(FramingRefused):
            canonical_member_name("A" * 64)

    def test_a_member_name_refuses_a_non_string(self):
        with self.assertRaises(FramingRefused):
            canonical_member_name(b"a" * 64)


class TheResult(PublicationFixture):
    def test_a_verified_final_cannot_be_constructed(self):
        with self.assertRaises(PublicationFailed) as caught:
            VerifiedFinal()
        self.assertEqual(caught.exception.code,
                         PUBLICATION_RESULT_UNCONSTRUCTIBLE)

    DECLARED = frozenset((
        "final_name", "payload_sha256", "file_sha256",
        "declared_payload_bytes", "temporary_name_retained"))

    def _public_values(self, result):
        return {name: getattr(result, name) for name in dir(result)
                if not name.startswith("_")
                and not callable(getattr(result, name))}

    def test_the_public_surface_is_exactly_the_five_declared_facts(self):
        """Set equality, not a ban on a type. An `int` is indistinguishable
        from a descriptor by type alone, so the guarantee has to be that the
        surface is *closed* --- a sixth attribute is the thing to catch."""
        self.assertEqual(frozenset(self._public_values(self.publish())),
                         self.DECLARED)

    def test_no_public_value_is_a_buffer_or_a_mutable_container(self):
        """The capability shapes. `bytes` and `memoryview` are the ones §5.24
        established outlive the scope they came from."""
        for name, value in self._public_values(self.publish()).items():
            self.assertNotIsInstance(
                value, (bytes, bytearray, memoryview, list, dict, set),
                msg=f"{name} hands out a buffer or a mutable container")

    def test_no_public_string_is_a_path(self):
        """A relative basename only. A separator would make it a path, which is
        a capability one directory short of a descriptor."""
        for name, value in self._public_values(self.publish()).items():
            if isinstance(value, str):
                self.assertNotIn(os.sep, value, msg=f"{name} looks like a path")

    def test_the_dict_form_declares_no_raw_iq(self):
        self.assertFalse(self.publish().to_dict()["raw_iq_exposed"])

    def test_the_dict_form_names_the_steps_it_completed(self):
        self.assertEqual(self.publish().to_dict()["publication_steps_completed"],
                         "5.20 STEPS 5-8")


class Arguments(PublicationFixture):
    def test_a_non_integer_descriptor_refuses(self):
        with self.assertRaises(PublicationFailed) as caught:
            self.publish(fd="3")
        self.assertEqual(caught.exception.code, PUBLICATION_ARGUMENT_TYPE_WRONG)

    def test_a_non_integer_directory_descriptor_refuses(self):
        with self.assertRaises(PublicationFailed) as caught:
            self.publish(dir_fd=None)
        self.assertEqual(caught.exception.code, PUBLICATION_ARGUMENT_TYPE_WRONG)

    def test_a_non_string_temporary_name_refuses(self):
        with self.assertRaises(PublicationFailed) as caught:
            self.publish(temporary_name=b"t")
        self.assertEqual(caught.exception.code, PUBLICATION_ARGUMENT_TYPE_WRONG)

    def test_a_non_string_intent_digest_refuses(self):
        with self.assertRaises(PublicationFailed) as caught:
            self.publish(intent_file_sha256=None)
        self.assertEqual(caught.exception.code, PUBLICATION_ARGUMENT_TYPE_WRONG)


class Vocabulary(unittest.TestCase):
    def test_the_failure_vocabulary_has_no_duplicates(self):
        self.assertEqual(len(DURABILITY_FAILURES),
                         len(set(DURABILITY_FAILURES)))

    def test_every_declared_failure_is_a_module_constant(self):
        for code in DURABILITY_FAILURES:
            self.assertEqual(getattr(publication, code), code)

    def test_no_production_module_is_wired_to_the_publisher(self):
        """3c-core's boundary, over EVERY production module rather than one.

        The first version parsed `rf_capture_admission.py` alone --- one file of
        163 --- so any other production module could have wired the publisher
        without moving this test. That is the same defect as a checker closing
        over one slice's identifiers instead of deriving them: the scope has to
        come from the tree, not from a name typed here.

        The publisher itself is excluded, and nothing else is.
        """
        import ast
        offenders = {}
        for path in sorted(pathlib.Path(".").glob("*.py")):
            if path.name.startswith("test_") or path.name == MODULE_NAME:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
                elif isinstance(node, ast.Import):
                    imported |= {a.name for a in node.names}
            if MODULE_STEM in imported:
                offenders[path.name] = sorted(imported & {MODULE_STEM})
        self.assertEqual(offenders, {})

    def test_the_boundary_scan_reads_every_production_module(self):
        """The breadth itself, witnessed. A scan that silently narrowed back to
        one file would still pass the test above while a second module wired the
        publisher, so the count is asserted separately."""
        scanned = [p for p in pathlib.Path(".").glob("*.py")
                   if not p.name.startswith("test_") and p.name != MODULE_NAME]
        self.assertGreater(len(scanned), 100)


if __name__ == "__main__":                            # pragma: no cover
    unittest.main()
