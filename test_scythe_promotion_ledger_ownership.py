"""Slice 6b: ownership, the coordinator composition, and restart seeding."""

import ast
import fcntl
import json
import os
import tempfile
import unittest

import scythe_promotion_ledger_ownership as ownership_module
from scythe_invariant_ledger import TransitionContract, check_transition, signature
from scythe_promotion_ledger import (
    COMMITTED, CREATED, FAILED, MODE_ARMED, MODE_SHADOW, NOT_CREATED,
    PROMOTION_FAILED, PROMOTION_RECORDED, PROMOTION_SUPPRESSED, RESERVED,
    WOULD_PROMOTE, PromotionAudit, PromotionCoordinator, WriteResult,
)
from scythe_promotion_ledger_store import AVAILABLE, read_ledger
from scythe_promotion_ledger_writer import (
    LEDGER_NOT_OWNED, OWNERSHIP_LOST, RESERVATION_NOT_DURABLE,
    LedgerWriteRefused, LedgerWriter,
)
from scythe_promotion_ledger_ownership import LedgerOwnership, sidecar_path
from scythe_promotion_lineage import generation_path
from scythe_promotion_policy import CapsuleIdentity, PromotionRequest

GOOD_MOUNTS = (("/", "ext4"),)
BAD_MOUNTS = (("/", "drvfs"),)
OWNER = {"boot_id": "boot-a", "pid": 4242, "start_ticks": 99}

CONTRACT = TransitionContract(name="T", must_preserve=("keep", "other"),
                              must_change=("move",), domain_fields=("boot",))
BASE = {"boot": "boot-a", "keep": "same", "other": "same", "move": 1}
CAPSULE = CapsuleIdentity(schema="scythe.invariant-capsule.v1",
                          digest="blake2s:aabbcc", within_bounds=True,
                          carries_samples=False)
REQUEST = PromotionRequest(requested_by="OPERATOR",
                           target_graph="scythe.graphops.evidence",
                           justification_source="OPERATOR")
NOW = 10_000 * 1_000_000_000


def _verdict(index=0):
    after = dict(BASE, move=2, keep=f"100.{index}")
    return check_transition(signature(**BASE), "T", signature(**after), CONTRACT)


class OwnershipTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = self._dir.name
        # The lineage root is an identity, not a file. Generations live beneath
        # it and the lock is derived from it (§13e F.10).
        self.root = os.path.join(self.dir, "promotion")
        self.path = generation_path(self.root, 0)

    def _devices(self, fstype="ext4"):
        """What the kernel would say about the device the tempdir is on.

        Injected rather than read, so the post-open check is exercised against a
        stated answer instead of whatever this host happens to be running.
        """
        return {os.stat(self.dir).st_dev: fstype}

    def _owner(self, mounts=GOOD_MOUNTS, devices=None):
        owner = LedgerOwnership(lineage_root=self.root, mounts=mounts,
                                refused_prefixes=(),
                                devices=self._devices() if devices is None
                                else devices)
        self.addCleanup(owner.release)
        return owner

    def _ledger(self, owner):
        return LedgerWriter(path=self.path, ownership=owner.scope,
                            refused_prefixes=())

    def _prepared(self, mounts=GOOD_MOUNTS):
        owner = self._owner(mounts)
        self.assertTrue(owner.acquire())
        ledger = self._ledger(owner)
        with ledger.owned() as session:
            session.initialize()
            session.declare_generation("gen-1", OWNER)
        return owner, ledger


class SidecarTests(OwnershipTestCase):
    def test_the_sidecar_is_derived_from_the_lineage_root(self):
        """§13e F.10. One lineage, one lock, whatever generation is current --
        locks on two generation files of one lineage exclude nobody who
        matters."""
        self.assertEqual(sidecar_path(self.root), self.root + ".lock")
        self.assertEqual(self._owner().sidecar, self.root + ".lock")
        self.assertNotEqual(self._owner().sidecar, self.path + ".lock")

    def test_every_generation_of_one_lineage_shares_the_lock(self):
        owner = self._owner()
        for ordinal in range(4):
            self.assertTrue(owner.covers(generation_path(self.root, ordinal)))
        self.assertEqual(owner.sidecar, self.root + ".lock")

    def test_there_is_no_per_generation_sidecar_fallback(self):
        """No compatibility path: consulting whichever of two files exists
        would manufacture the ambiguity the derivation removes."""
        source = open(ownership_module.__file__, encoding="utf-8").read()
        tree = ast.parse(source)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for absent in ("legacy", "fallback", "migrate", "compat"):
            self.assertFalse([n for n in names if absent in n.lower()], absent)
        owner = self._owner()
        owner.acquire()
        self.assertFalse(os.path.exists(self.path + ".lock"))

    def test_no_constructor_field_configures_the_lock_path(self):
        fields = set(LedgerOwnership.__dataclass_fields__)
        for name in fields:
            self.assertNotIn("lock", name)
            self.assertNotIn("sidecar", name)

    def test_two_owners_for_one_lineage_lock_the_same_file(self):
        first = LedgerOwnership(lineage_root=self.root, mounts=GOOD_MOUNTS)
        second = LedgerOwnership(lineage_root=os.path.join(self.dir, ".",
                                                           "promotion"),
                                 mounts=GOOD_MOUNTS)
        self.assertEqual(os.path.realpath(first.sidecar),
                         os.path.realpath(second.sidecar))

    def test_ownership_is_taken_without_creating_the_ledger(self):
        """Sidecar creation may accompany acquisition; ledger creation may
        not."""
        owner = self._owner()
        self.assertTrue(owner.acquire())
        self.assertTrue(os.path.exists(owner.sidecar))
        self.assertFalse(os.path.exists(self.path))

    def test_the_sidecar_says_it_is_only_diagnostic(self):
        owner = self._owner()
        owner.acquire()
        with open(owner.sidecar, "r", encoding="utf-8") as handle:
            record = json.loads(handle.read())
        self.assertIn("DIAGNOSTIC ONLY", record["note"])
        self.assertEqual(record["pid"], os.getpid())


class AcquisitionTests(OwnershipTestCase):
    def test_a_second_owner_is_refused_while_the_first_holds(self):
        first = self._owner()
        self.assertTrue(first.acquire())
        second = self._owner()
        self.assertFalse(second.acquire())
        self.assertEqual(second.status()["refusals"], [LEDGER_NOT_OWNED])
        self.assertFalse(second.holds)

    def test_a_refused_owner_mints_no_scope(self):
        self._owner().acquire()
        second = self._owner()
        second.acquire()
        ledger = self._ledger(second)
        with self.assertRaises(LedgerWriteRefused) as caught:
            with ledger.owned():
                pass
        self.assertEqual(caught.exception.code, LEDGER_NOT_OWNED)

    def test_an_unattested_mount_mints_no_scope_though_flock_would_succeed(self):
        """§13d E.2. A gate checked somewhere other than where it is relied on
        is not a gate."""
        owner = self._owner(mounts=BAD_MOUNTS)
        self.assertFalse(owner.acquire())
        self.assertFalse(owner.holds)
        self.assertIsNone(owner.scope(self.path))
        # The lock itself was available: the refusal is about the mount.
        proof = os.open(sidecar_path(self.root), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(proof, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(proof)

    def test_no_sidecar_is_created_on_an_unattested_mount(self):
        """The one artefact that would make the next reader believe someone
        owned this."""
        owner = self._owner(mounts=BAD_MOUNTS)
        owner.acquire()
        self.assertFalse(os.path.exists(owner.sidecar))

    def test_a_scope_for_another_lineage_is_not_minted(self):
        owner = self._owner()
        owner.acquire()
        self.assertIsNone(owner.scope(os.path.join(self.dir, "other.000000.jsonl")))
        self.assertFalse(owner.covers(os.path.join(self.dir, "other.000000.jsonl")))

    def test_absent_authority_and_invalidated_authority_are_different(self):
        """Never acquired is LEDGER_NOT_OWNED -- go and find who holds it.
        Acquired and since lost is OWNERSHIP_LOST, and terminal. The same
        distinction this contract already draws elsewhere."""
        never = self._owner()
        self.assertIsNone(never.scope(self.path))

        lost = self._owner()
        self.assertTrue(lost.acquire())
        lost.release()
        with self.assertRaises(LedgerWriteRefused) as caught:
            lost.scope(self.path)
        self.assertEqual(caught.exception.code, OWNERSHIP_LOST)

    def test_ownership_is_not_reacquired_after_release(self):
        owner, ledger = self._prepared()
        owner.release()
        self.assertFalse(owner.holds)
        self.assertFalse(owner.status()["reacquires"])


class DescriptorAttestationTests(OwnershipTestCase):
    """The mount can change between attesting a path and locking an object.

    A path check answers a question about a *name*, and the object reached
    through that name is resolved again at open, at flock, and at minting. The
    authoritative check is on the descriptor, which cannot be remounted out from
    under itself.
    """

    def _mismatched(self, devices):
        owner = self._owner(devices=devices)
        self.assertFalse(owner.acquire())
        return owner

    def test_a_device_that_changed_after_preflight_mints_no_scope(self):
        owner = self._owner()
        real_fstat = os.fstat

        def moved(fd):
            st = real_fstat(fd)
            return os.stat_result(tuple(st)[:2] + (st.st_dev + 1,) + tuple(st)[3:])

        os.fstat = moved
        try:
            self.assertFalse(owner.acquire())
        finally:
            os.fstat = real_fstat
        self.assertFalse(owner.holds)
        self.assertIsNone(owner.scope(self.path))

    def test_an_unattested_filesystem_behind_a_good_path_mints_no_scope(self):
        """The path says ext4 and the device says drvfs. The lock succeeded."""
        owner = self._mismatched(self._devices("drvfs"))
        self.assertFalse(owner.holds)
        self.assertIn("drvfs", owner.status()["verification_detail"])

    def test_the_flock_itself_was_available(self):
        """So the refusal is about the filesystem and not about contention."""
        self._mismatched(self._devices("drvfs"))
        proof = os.open(sidecar_path(self.root), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(proof, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(proof, fcntl.LOCK_UN)
        os.close(proof)

    def test_the_lock_is_released_and_the_owner_is_finished(self):
        owner = self._mismatched(self._devices("drvfs"))
        self.assertTrue(owner.status()["terminal"])
        self.assertFalse(owner.acquire())
        self.assertIsNone(owner.scope(self.path))

    def test_the_ledger_is_never_touched(self):
        self._mismatched(self._devices("drvfs"))
        self.assertFalse(os.path.exists(self.path))

    def test_a_sidecar_this_attempt_created_is_removed(self):
        self._mismatched(self._devices("drvfs"))
        self.assertFalse(os.path.exists(sidecar_path(self.root)))

    def test_a_sidecar_it_found_is_preserved(self):
        """Removing one we did not create would destroy another process's lock
        file on the strength of our own bad luck."""
        with open(sidecar_path(self.root), "wb") as handle:
            handle.write(b"someone else was here\n")
        self._mismatched(self._devices("drvfs"))
        self.assertTrue(os.path.exists(sidecar_path(self.root)))
        with open(sidecar_path(self.root), "rb") as handle:
            self.assertEqual(handle.read(), b"someone else was here\n")

    def test_the_refusal_names_both_claims(self):
        """One lookup, two claims -- the same rule §9 Amendment A set."""
        owner = self._mismatched(self._devices("drvfs"))
        self.assertEqual(len(owner.status()["refusals"]), 2)

    def test_status_says_the_attestation_runs_twice(self):
        self.assertTrue(self._owner().status()["attested_twice"])


class ScopeLivenessTests(OwnershipTestCase):
    """§13d E.3: liveness is derived from the owner."""

    def test_a_scope_dies_when_the_owner_loses_the_lock(self):
        owner, ledger = self._prepared()
        with self.assertRaises(LedgerWriteRefused) as caught:
            with ledger.owned() as session:
                owner.release()          # the lock goes while the session lives
                session.append_reserved({"identity": "promotion:aa"})
        self.assertEqual(caught.exception.code, OWNERSHIP_LOST)

    def test_the_loss_outranks_the_session_ending(self):
        """A closed session whose owner also lost the lock reports the more
        serious of the two facts."""
        owner, ledger = self._prepared()
        with ledger.owned() as session:
            escaped = session
        owner.release()
        self.assertEqual(escaped.scope.refusal(), OWNERSHIP_LOST)

    def test_a_scope_still_dies_at_the_end_of_its_session(self):
        owner, ledger = self._prepared()
        with ledger.owned() as session:
            escaped = session
        self.assertEqual(escaped.scope.refusal(), LEDGER_NOT_OWNED)


class DurabilityHaltTests(OwnershipTestCase):
    """§13d E.6, and the back door it must not leave open."""

    def _breaking_fsync(self):
        real = os.fsync

        def broken(fd):
            raise OSError(5, "simulated I/O error")
        return real, broken

    def test_a_failed_fsync_refuses_and_halts_the_owner(self):
        owner, ledger = self._prepared()
        real, broken = self._breaking_fsync()
        os.fsync = broken
        try:
            with self.assertRaises(LedgerWriteRefused) as caught:
                with ledger.owned() as session:
                    session.append_reserved({"identity": "promotion:aa"})
        finally:
            os.fsync = real
        self.assertEqual(caught.exception.code, RESERVATION_NOT_DURABLE)
        self.assertTrue(owner.halted)

    def test_a_new_session_after_the_halt_is_also_refused(self):
        """The back door: expiring only the active scope would let the caller
        open another session and carry on."""
        owner, ledger = self._prepared()
        real, broken = self._breaking_fsync()
        os.fsync = broken
        try:
            with self.assertRaises(LedgerWriteRefused):
                with ledger.owned() as session:
                    session.append_reserved({"identity": "promotion:aa"})
        finally:
            os.fsync = real
        with self.assertRaises(LedgerWriteRefused) as caught:
            with ledger.owned() as session:
                session.append_reserved({"identity": "promotion:bb"})
        self.assertEqual(caught.exception.code, RESERVATION_NOT_DURABLE)

    def test_the_halt_survives_a_working_fsync(self):
        """The uncertainty is about the file, not about the syscall."""
        owner, ledger = self._prepared()
        real, broken = self._breaking_fsync()
        os.fsync = broken
        try:
            with self.assertRaises(LedgerWriteRefused):
                with ledger.owned() as session:
                    session.append_reserved({"identity": "promotion:aa"})
        finally:
            os.fsync = real
        with self.assertRaises(LedgerWriteRefused):
            with ledger.owned() as session:
                session.declare_generation("gen-2", OWNER)
        self.assertTrue(owner.halted)


class CompositionTests(OwnershipTestCase):
    """The coordinator writes durably, and memory follows the file."""

    def _coordinator(self, ledger, mode=MODE_ARMED, writer=None, **kw):
        return PromotionCoordinator(
            PromotionAudit(), mode=mode, ledger=ledger,
            writer=writer or (lambda d: WriteResult(CREATED)), **kw)

    def test_a_promotion_reaches_the_ledger_and_the_reader_accepts_it(self):
        owner, ledger = self._prepared()
        coordinator = self._coordinator(ledger)
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], PROMOTION_RECORDED)
        read = read_ledger(self.path)
        self.assertEqual(read.readability, AVAILABLE)
        self.assertEqual(read.committed, coordinator.promoted_keys)

    def test_a_failed_write_is_recorded_as_failed_and_not_committed(self):
        owner, ledger = self._prepared()
        coordinator = self._coordinator(
            ledger, writer=lambda d: WriteResult(NOT_CREATED))
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], PROMOTION_FAILED)
        read = read_ledger(self.path)
        self.assertEqual(read.committed, ())
        self.assertEqual(len(read.write_failed), 1)

    def test_a_refused_durable_append_takes_no_in_memory_reservation(self):
        """§13d E.4. The alternative is a fence that exists until the next
        restart and then does not."""
        owner, ledger = self._prepared()
        coordinator = self._coordinator(ledger)
        owner.release()
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["executability_code"], OWNERSHIP_LOST)
        self.assertEqual(coordinator.fenced_keys, ())
        self.assertEqual(coordinator.status()["fenced"], 0)

    def test_a_reservation_that_could_not_be_synced_still_fences(self):
        owner, ledger = self._prepared()
        coordinator = self._coordinator(ledger)
        real = os.fsync
        os.fsync = lambda fd: (_ for _ in ()).throw(OSError(5, "io"))
        try:
            result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                          now_monotonic_ns=NOW)
        finally:
            os.fsync = real
        self.assertEqual(result["executability_code"], RESERVATION_NOT_DURABLE)
        self.assertEqual(len(coordinator.fenced_keys), 1)

    def test_shadow_writes_nothing_to_the_ledger(self):
        owner, ledger = self._prepared()
        before = _bytes(self.path)
        coordinator = PromotionCoordinator(PromotionAudit(), mode=MODE_SHADOW,
                                           ledger=ledger)
        result = coordinator.evaluate(_verdict(), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], WOULD_PROMOTE)
        self.assertEqual(_bytes(self.path), before)


class SeedingTests(OwnershipTestCase):
    """§13d E.5: rebuilt at startup, and not optional."""

    def _promote(self, index, outcome=CREATED):
        owner, ledger = self._prepared() if not os.path.exists(self.path) \
            else (self._reowned())
        coordinator = PromotionCoordinator(
            PromotionAudit(), mode=MODE_ARMED, ledger=ledger,
            writer=lambda d: WriteResult(outcome))
        result = coordinator.evaluate(_verdict(index), REQUEST, CAPSULE,
                                      now_monotonic_ns=NOW)
        owner.release()
        return result

    def _reowned(self):
        owner = self._owner()
        self.assertTrue(owner.acquire())
        return owner, self._ledger(owner)

    def test_an_identity_committed_before_a_restart_is_refused_after_it(self):
        """The whole point. A coordinator that seeds empty re-promotes every
        identity in the file."""
        self._promote(0)
        owner, ledger = self._reowned()
        fresh = PromotionCoordinator(PromotionAudit(), mode=MODE_ARMED,
                                     ledger=ledger,
                                     writer=lambda d: WriteResult(CREATED))
        result = fresh.evaluate(_verdict(0), REQUEST, CAPSULE,
                                now_monotonic_ns=NOW)
        self.assertIn("DUPLICATE_PROMOTION", result.get("refusals", []))

    def test_an_identity_never_promoted_is_admitted_after_a_restart(self):
        self._promote(0)
        owner, ledger = self._reowned()
        fresh = PromotionCoordinator(PromotionAudit(), mode=MODE_ARMED,
                                     ledger=ledger,
                                     writer=lambda d: WriteResult(CREATED))
        result = fresh.evaluate(_verdict(1), REQUEST, CAPSULE,
                                now_monotonic_ns=NOW)
        self.assertEqual(result["outcome"], PROMOTION_RECORDED)

    def test_an_unresolved_reservation_survives_a_restart_as_unresolved(self):
        owner, ledger = self._prepared()
        coordinator = PromotionCoordinator(
            PromotionAudit(), mode=MODE_ARMED, ledger=ledger,
            writer=lambda d: WriteResult("UNKNOWN"))
        coordinator.evaluate(_verdict(0), REQUEST, CAPSULE, now_monotonic_ns=NOW)
        owner.release()
        owner, ledger = self._reowned()
        fresh = PromotionCoordinator(PromotionAudit(), mode=MODE_ARMED,
                                     ledger=ledger,
                                     writer=lambda d: WriteResult(CREATED))
        self.assertEqual(len(fresh.unresolved_keys), 1)
        self.assertEqual(fresh.promoted_keys, ())
        self.assertEqual(fresh.write_failed_keys, ())

    def test_a_failed_identity_survives_a_restart_as_failed(self):
        self._promote(0, outcome=NOT_CREATED)
        owner, ledger = self._reowned()
        fresh = PromotionCoordinator(PromotionAudit(), mode=MODE_ARMED,
                                     ledger=ledger,
                                     writer=lambda d: WriteResult(CREATED))
        self.assertEqual(len(fresh.write_failed_keys), 1)
        self.assertEqual(fresh.promoted_keys, ())

    def test_the_window_is_not_rebuilt(self):
        """§6. A persisted monotonic_ns from a previous boot is meaningless."""
        self._promote(0)
        owner, ledger = self._reowned()
        fresh = PromotionCoordinator(PromotionAudit(), mode=MODE_ARMED,
                                     ledger=ledger,
                                     writer=lambda d: WriteResult(CREATED))
        self.assertEqual(len(fresh._real.window), 0)
        self.assertFalse(fresh.status()["budget_window_survives_restart"])

    def test_shadow_seeds_from_the_same_file(self):
        """§12: a SHADOW starting empty over-counts would-be promotions."""
        self._promote(0)
        owner, ledger = self._reowned()
        shadow = PromotionCoordinator(PromotionAudit(), mode=MODE_SHADOW,
                                      ledger=ledger)
        self.assertEqual(len(shadow.policy_keys), 1)

    def test_status_reports_what_it_was_seeded_from(self):
        owner, ledger = self._prepared()
        coordinator = PromotionCoordinator(PromotionAudit(), mode=MODE_SHADOW,
                                           ledger=ledger)
        status = coordinator.status()
        self.assertTrue(status["durable_ledger_connected"])
        self.assertEqual(status["seeded_from_ledger"], AVAILABLE)


class ModuleScopeTests(unittest.TestCase):
    def setUp(self):
        with open(ownership_module.__file__, encoding="utf-8") as handle:
            self.source = handle.read()
        self.tree = ast.parse(self.source)

    def test_the_ownership_module_does_not_write_the_ledger(self):
        """It creates and writes the sidecar, and has no way to frame a record.

        Read from the AST. An earlier version of this test scanned the source
        text for "append" and matched `halt_appends` and `O_APPEND` -- the same
        false-positive class PENDING_AMENDMENTS.md entry 5 records, written by
        the same author who had just recorded it.
        """
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(a.name for a in node.names)
        self.assertNotIn("frame_of", imported)
        self.assertNotIn("read_ledger", imported)
        self.assertNotIn("LedgerWriter", imported)

    def test_reconciliation_is_not_invented_here(self):
        """Also from the AST: "ARMED" appears in this module's prose, saying
        that failing to acquire refuses it. Prose about a thing is not the
        thing, for the sixth time in this repository."""
        defined = {n.name for n in ast.walk(self.tree)
                   if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        assigned = {t.id for n in ast.walk(self.tree)
                    if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)}
        for absent in ("RECONCILED_COMMITTED", "RECONCILED_RELEASED",
                       "reconcile", "DURABLE_CEILING_REACHED",
                       "RETRY_REQUIRES_OPERATOR", "MODE_ARMED"):
            self.assertNotIn(absent, defined | assigned)

    def test_the_coordinator_is_not_imported(self):
        modules = {n.module for n in ast.walk(self.tree)
                   if isinstance(n, ast.ImportFrom)}
        self.assertNotIn("scythe_promotion_ledger", modules)


def _bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


if __name__ == "__main__":
    unittest.main()
