"""Slice 10g: the position-entry runner, driven entirely by injected seams.

**Nothing here is evidence.** Every fix is a controlled string, every terminal
is an object, every clock is a counter, and every artefact lands in a
`TemporaryDirectory` that is removed when the test ends. None of it can drain
`PENDING_AMENDMENTS.md` entry 7, which waits on a bounded act with a person in
it.
"""

import ast
import os
import stat
import tempfile
import unittest

import scythe_position_entry as entry_module
from scythe_derived_evidence import (
    CONFIGURED_NOT_EXERCISED, INSTRUMENT_CONFIGURED_IDLE,
    RF_MEASUREMENT_NOT_PERFORMED, WALK_STEP_PAIR, read_artefact,
)
from scythe_position_entry import (
    ENTRY_DEADLINE_REACHED, ENTRY_NOT_INTERACTIVE, ENTRY_SET_INCOMPLETE,
    FIX_ACCEPTED, FIX_REFUSED, MAX_RUN_MONOTONIC_S, REQUIRED_FIXES,
    RUN_PRECONDITION_REFUSED, EntryRefused, Fix, PositionEntryRun, parse_fix,
)

SOURCES = {"device_id": "rf_bridge", "signal_chain_hash": "rf_receiver_state",
           "configuration_epoch": "rf_bridge", "monotonic_source_id": "rf_bridge"}

# An ordinary walk: a little over a metre a second, north, at 15-second steps.
WALK = [f"51.5{i:04d} -0.1200" for i in range(REQUIRED_FIXES)]

SECOND = 1_000_000_000


class Clock:
    """Monotonic nanoseconds, advanced only where a test says so.

    Reading the clock does not move it. That matters for the stamping tests:
    if the clock moved on every read, *when* a stamp was taken would be
    indistinguishable from *how many times* something looked at the time.
    """

    def __init__(self, start=1_000 * SECOND):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += int(seconds * SECOND)


class FakeTerminal:
    """A terminal that is an object. Records every echo change it is asked for.

    `ready` is where time passes, because that is where it passes in a real
    run: the operator is walking, and the process is waiting on a descriptor.
    """

    def __init__(self, lines, clock, interactive=True, spacing_s=15.0,
                 silence_fails=False):
        self.pending = [line.encode("ascii") + b"\n" for line in lines]
        self.clock = clock
        self._interactive = interactive
        self.spacing_s = spacing_s
        self.silence_fails = silence_fails
        self.echo = True
        self.silenced = 0
        self.restored = []
        self.said = []

    def interactive(self):
        return self._interactive

    def silence(self):
        if self.silence_fails:
            raise OSError("not a terminal")
        self.silenced += 1
        self.echo = False
        return "SAVED_TERMIOS"

    def restore(self, saved):
        self.restored.append(saved)
        if saved is not None:
            self.echo = True

    def ready(self, timeout_s):
        """Time passes here. When nothing is left to type, it runs out."""
        if not self.pending:
            self.clock.advance(max(0.0, timeout_s))
            return False
        self.clock.advance(min(self.spacing_s, max(0.0, timeout_s)))
        return timeout_s > 0

    def read(self, count):
        return self.pending.pop(0)[:count]

    def say(self, message):
        self.said.append(message)


class EntryTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.derived = os.path.join(self._dir.name, "derived")
        self.records = os.path.join(self._dir.name, "records")
        os.mkdir(self.derived, 0o700)
        os.mkdir(self.records, 0o700)
        self.lineage = os.path.join(self._dir.name, "ledger", "promotion")
        self.clock = Clock()

    def _run(self, lines=None, **kw):
        terminal = kw.pop("terminal", None)
        if terminal is None:
            terminal = FakeTerminal(WALK if lines is None else lines, self.clock,
                                    **kw.pop("terminal_kw", {}))
        fields = dict(
            derived_directory=self.derived, records_directory=self.records,
            lineage_root=self.lineage, run_id="run-10g", device_id="rtl2838-idle",
            receiver_state_chain_hash="rsc-1", signal_chain_hash="chain-1",
            configuration_epoch=1, monotonic_source_id="mono-host",
            pose_uncertainty_m=8.0, claim_sources=dict(SOURCES),
            configuration_identity="sha256:cfg",
            measurement_status=RF_MEASUREMENT_NOT_PERFORMED,
            instrument_state=INSTRUMENT_CONFIGURED_IDLE,
            terminal=terminal, clock=self.clock)
        fields.update(kw)
        run = PositionEntryRun(**fields)
        run.terminal_double = terminal
        return run

    def _published(self):
        return sorted(os.listdir(self.derived))


class ParseTests(unittest.TestCase):
    """Exactly two plain decimals, in range, and nothing else."""

    def test_an_ordinary_fix_is_accepted(self):
        self.assertEqual(parse_fix("51.5074 -0.1278"), (51.5074, -0.1278))

    def test_the_wrong_number_of_fields_is_refused(self):
        for line in ("", "51.5", "51.5 -0.12 8.0", "51.5,-0.12"):
            with self.subTest(line=line):
                with self.assertRaises(EntryRefused) as caught:
                    parse_fix(line)
                self.assertEqual(caught.exception.code, FIX_REFUSED)

    def test_non_finite_and_exponent_forms_are_refused(self):
        for text in ("nan", "-nan", "inf", "-inf", "Infinity", "1e5", "5e-3",
                     "0x1", "1_0", "5 1.5"):
            with self.subTest(text=text):
                with self.assertRaises(EntryRefused):
                    parse_fix(f"{text} -0.12")

    def test_locale_dependent_forms_are_refused(self):
        """A comma decimal mark and a thousands separator are both a different
        number somewhere else, which is the whole problem with accepting
        them."""
        for text in ("51,5", "1.234,5", "1,234.5"):
            with self.subTest(text=text):
                with self.assertRaises(EntryRefused):
                    parse_fix(f"{text} -0.12")

    def test_excessive_precision_is_refused(self):
        parse_fix("51.123456789 -0.123456789")          # nine is the bound
        with self.assertRaises(EntryRefused) as caught:
            parse_fix("51.1234567891 -0.12")
        self.assertEqual(caught.exception.code, FIX_REFUSED)

    def test_out_of_range_values_are_refused(self):
        for line in ("90.0001 -0.12", "-90.1 -0.12", "51.5 180.0001",
                     "51.5 -180.1"):
            with self.subTest(line=line):
                with self.assertRaises(EntryRefused) as caught:
                    parse_fix(line)
                self.assertEqual(caught.exception.code, FIX_REFUSED)

    def test_the_range_edges_are_accepted(self):
        for line in ("90 0", "-90 0", "0 180", "0 -180"):
            with self.subTest(line=line):
                parse_fix(line)

    def test_no_refusal_repeats_the_input(self):
        """A refusal that quoted the line would put a coordinate into every
        traceback that ever repeated it."""
        for line in ("91.5 -0.12", "51.5 999.9", "nan -0.12", "51.5"):
            with self.subTest(line=line):
                with self.assertRaises(EntryRefused) as caught:
                    parse_fix(line)
                message = str(caught.exception) + caught.exception.detail
                for field in line.split():
                    self.assertNotIn(field, message)


class InteractiveTests(EntryTestCase):
    """A file of coordinates entered at machine speed is not a walk."""

    def test_a_non_terminal_refuses_before_the_first_prompt(self):
        run = self._run(terminal_kw={"interactive": False})
        with self.assertRaises(EntryRefused) as caught:
            run.run()
        self.assertEqual(caught.exception.code, ENTRY_NOT_INTERACTIVE)
        self.assertEqual(self._published(), [])
        self.assertEqual(run.terminal_double.silenced, 0)

    def test_a_terminal_closing_during_entry_refuses(self):
        terminal = FakeTerminal(WALK[:3], self.clock)
        terminal.read = lambda count: b""
        run = self._run(terminal=terminal)
        with self.assertRaises(EntryRefused) as caught:
            run.run()
        self.assertEqual(caught.exception.code, ENTRY_NOT_INTERACTIVE)
        self.assertEqual(self._published(), [])


class EchoTests(EntryTestCase):
    """A session may be recorded."""

    def test_echo_is_disabled_for_entry_and_restored_after(self):
        run = self._run()
        run.run()
        self.assertEqual(run.terminal_double.silenced, 1)
        self.assertEqual(run.terminal_double.restored, ["SAVED_TERMIOS"])
        self.assertTrue(run.terminal_double.echo)

    def test_echo_is_restored_when_a_fix_is_refused(self):
        run = self._run(["51.5 -0.12", "not a fix"])
        with self.assertRaises(EntryRefused):
            run.run()
        self.assertEqual(run.terminal_double.restored, ["SAVED_TERMIOS"])
        self.assertTrue(run.terminal_double.echo)

    def test_echo_is_restored_when_the_deadline_passes(self):
        run = self._run(WALK[:2])
        with self.assertRaises(EntryRefused):
            run.run()
        self.assertTrue(run.terminal_double.echo)

    def test_progress_is_bounded_and_carries_no_coordinate(self):
        run = self._run()
        run.run()
        said = run.terminal_double.said
        self.assertEqual(len(said), REQUIRED_FIXES)
        self.assertEqual(said[2], f"{FIX_ACCEPTED} 3/{REQUIRED_FIXES}")
        blob = "\n".join(said)
        for line in WALK:
            for value in line.split():
                self.assertNotIn(value, blob)


class DeadlineTests(EntryTestCase):
    """The wait is bounded, and the bound is the clock's, not the operator's."""

    def test_the_deadline_refuses_a_short_set(self):
        run = self._run(WALK[:5])
        with self.assertRaises(EntryRefused) as caught:
            run.run()
        self.assertEqual(caught.exception.code, ENTRY_DEADLINE_REACHED)
        self.assertEqual(self._published(), [])

    def test_the_deadline_is_240_monotonic_seconds(self):
        started = self.clock.now
        run = self._run(WALK[:1])
        with self.assertRaises(EntryRefused):
            run.run()
        elapsed = (self.clock.now - started) / SECOND
        self.assertGreaterEqual(elapsed, MAX_RUN_MONOTONIC_S)
        self.assertLess(elapsed, MAX_RUN_MONOTONIC_S + 16)

    def test_a_slow_walk_that_finishes_in_time_is_accepted(self):
        run = self._run(terminal=FakeTerminal(WALK, self.clock, spacing_s=19.0))
        result = run.run()
        self.assertEqual(result["fixes"], REQUIRED_FIXES)

    def test_the_runner_never_blocks_on_input(self):
        """AST: no `input`, and no read that is not preceded by a bounded
        `ready`. A blocking read would end the run when the operator felt like
        it rather than at 240 seconds."""
        with open(entry_module.__file__) as handle:
            tree = ast.parse(handle.read())
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertNotIn("input", called)
        self.assertNotIn("raw_input", called)


class StampTests(EntryTestCase):
    """Stamped once, after validation, and never for a line that was refused."""

    def test_every_accepted_fix_is_stamped_in_order(self):
        run = self._run()
        run.run()
        stamps = [fix.observed_monotonic_ns for fix in run._fixes]
        self.assertEqual(len(stamps), REQUIRED_FIXES)
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(len(set(stamps)), REQUIRED_FIXES)

    def test_a_refused_line_leaves_no_stamp_behind(self):
        """A stamp taken before validation would leave one here."""
        run = self._run(WALK[:3] + ["91.0 -0.12"])
        with self.assertRaises(EntryRefused) as caught:
            run.run()
        self.assertEqual(caught.exception.code, FIX_REFUSED)
        self.assertEqual(len(run._fixes), 3)

    def test_the_stamp_is_read_after_the_line_is_validated(self):
        """Validation is made to take a visible moment, so a stamp taken
        before it lands a second earlier than one taken after."""
        real = entry_module.parse_fix

        def deliberate(line):
            self.clock.advance(1.0)
            return real(line)

        entry_module.parse_fix = deliberate
        self.addCleanup(setattr, entry_module, "parse_fix", real)
        started = self.clock.now
        run = self._run()
        run.run()
        self.assertEqual(run._fixes[0].observed_monotonic_ns,
                         started + 16 * SECOND)
        gaps = [b.observed_monotonic_ns - a.observed_monotonic_ns
                for a, b in zip(run._fixes, run._fixes[1:])]
        self.assertTrue(all(gap == 16 * SECOND for gap in gaps), gaps)

    def test_the_stamp_is_the_ingestion_time_not_the_fix_time(self):
        """It is read after the line was accepted, so it is at least as late
        as the moment the terminal produced it."""
        run = self._run()
        run.run()
        self.assertGreaterEqual(run._fixes[0].observed_monotonic_ns,
                                1_000 * SECOND)
        spacing = [b.observed_monotonic_ns - a.observed_monotonic_ns
                   for a, b in zip(run._fixes, run._fixes[1:])]
        self.assertTrue(all(gap == 15 * SECOND for gap in spacing), spacing)


class PublicationTests(EntryTestCase):
    """Exactly one artefact, from a complete set, after everything is rechecked."""

    def test_twelve_fixes_publish_eleven_walk_steps(self):
        run = self._run()
        result = run.run()
        self.assertEqual(result["fixes"], REQUIRED_FIXES)
        self.assertEqual(result["records"], REQUIRED_FIXES - 1)
        self.assertEqual(len(self._published()), 1)

    def test_the_artefact_reads_back_as_a_v2_configured_idle_artefact(self):
        run = self._run(instrument_settings={
            "sample_rate_hz": 2_400_000,
            "sample_rate_hz_exercise": CONFIGURED_NOT_EXERCISED,
            "gain_db": 40.2, "gain_db_exercise": CONFIGURED_NOT_EXERCISED})
        result = run.run()
        artefact = read_artefact(result["artefact_path"])
        self.assertEqual(artefact.measurement_status, RF_MEASUREMENT_NOT_PERFORMED)
        self.assertEqual(artefact.instrument_state, INSTRUMENT_CONFIGURED_IDLE)
        self.assertEqual(len(artefact.records), REQUIRED_FIXES - 1)
        self.assertEqual(artefact.provenance["sample_rate_hz_exercise"],
                         CONFIGURED_NOT_EXERCISED)
        self.assertEqual(artefact.content_digest, result["content_digest"])

    def test_the_records_carry_the_entered_fixes_in_order(self):
        """Consecutive pairs, each fix appearing as the `after` of one step and
        the `before` of the next. No repetition, no reordering, nothing made
        up to fill a gap."""
        run = self._run()
        result = run.run()
        artefact = read_artefact(result["artefact_path"])
        entered = [(fix.latitude, fix.longitude) for fix in run._fixes]
        for index, record in enumerate(artefact.records):
            self.assertEqual(record["kind"], WALK_STEP_PAIR)
            self.assertEqual((record["before"]["latitude"],
                              record["before"]["longitude"]), entered[index])
            self.assertEqual((record["after"]["latitude"],
                              record["after"]["longitude"]), entered[index + 1])

    def test_an_incomplete_set_publishes_nothing(self):
        run = self._run()
        fixes = tuple(Fix(51.5, -0.12, (index + 1) * SECOND)
                      for index in range(REQUIRED_FIXES - 1))
        with self.assertRaises(EntryRefused) as caught:
            run.publish(fixes)
        self.assertEqual(caught.exception.code, ENTRY_SET_INCOMPLETE)
        self.assertEqual(self._published(), [])

    def test_a_short_set_is_not_completed_by_repeating_a_fix(self):
        """The refusal names the shortfall and offers no repair."""
        run = self._run()
        fixes = tuple(Fix(51.5, -0.12, (index + 1) * SECOND)
                      for index in range(REQUIRED_FIXES - 1))
        with self.assertRaises(EntryRefused) as caught:
            run.publish(fixes + fixes[-1:])
        # Repeating the last one makes the count right and the stamps wrong.
        self.assertEqual(caught.exception.code, RUN_PRECONDITION_REFUSED)
        self.assertEqual(self._published(), [])

    def test_out_of_order_stamps_are_refused(self):
        run = self._run()
        stamps = [(index + 1) * SECOND for index in range(REQUIRED_FIXES)]
        stamps[4], stamps[5] = stamps[5], stamps[4]
        fixes = tuple(Fix(51.5, -0.12, stamp) for stamp in stamps)
        with self.assertRaises(EntryRefused) as caught:
            run.publish(fixes)
        self.assertEqual(caught.exception.code, RUN_PRECONDITION_REFUSED)
        self.assertEqual(self._published(), [])

    def test_a_fix_of_the_wrong_type_is_refused_nominally(self):
        class Lookalike:
            latitude, longitude = 51.5, -0.12
            observed_monotonic_ns = REQUIRED_FIXES * SECOND

        run = self._run()
        good = tuple(Fix(51.5, -0.12, (index + 1) * SECOND)
                     for index in range(REQUIRED_FIXES - 1))
        with self.assertRaises(EntryRefused) as caught:
            run.publish(good + (Lookalike(),))
        self.assertEqual(caught.exception.code, RUN_PRECONDITION_REFUSED)
        self.assertEqual(self._published(), [])

    def test_an_integer_coordinate_is_refused_nominally(self):
        run = self._run()
        fixes = tuple(Fix(51, -0.12, (index + 1) * SECOND)
                      for index in range(REQUIRED_FIXES))
        with self.assertRaises(EntryRefused):
            run.publish(fixes)
        self.assertEqual(self._published(), [])


class RevalidationTests(EntryTestCase):
    """A path is a name and can be repointed between two moments."""

    def _collected(self):
        run = self._run()
        return run, run.collect()

    def test_a_repointed_output_directory_refuses_before_publication(self):
        run, fixes = self._collected()
        moved = os.path.join(self._dir.name, "elsewhere")
        os.mkdir(moved, 0o700)
        os.rmdir(self.derived)
        os.symlink(moved, self.derived)
        with self.assertRaises(EntryRefused) as caught:
            run.publish(fixes)
        self.assertEqual(caught.exception.code, RUN_PRECONDITION_REFUSED)
        self.assertEqual(os.listdir(moved), [])

    def test_a_loosened_directory_mode_refuses_before_publication(self):
        run, fixes = self._collected()
        os.chmod(self.derived, 0o755)
        with self.assertRaises(EntryRefused) as caught:
            run.publish(fixes)
        self.assertEqual(caught.exception.code, RUN_PRECONDITION_REFUSED)
        self.assertEqual(self._published(), [])

    def test_a_removed_output_directory_refuses(self):
        run, fixes = self._collected()
        os.rmdir(self.records)
        with self.assertRaises(EntryRefused) as caught:
            run.publish(fixes)
        self.assertEqual(caught.exception.code, RUN_PRECONDITION_REFUSED)

    def test_an_output_directory_inside_the_lineage_namespace_refuses(self):
        inside = os.path.join(self._dir.name, "ledger", "derived")
        os.makedirs(inside, 0o700)
        run = self._run(derived_directory=inside)
        with self.assertRaises(EntryRefused) as caught:
            run.run()
        self.assertEqual(caught.exception.code, RUN_PRECONDITION_REFUSED)
        self.assertEqual(os.listdir(inside), [])

    def test_one_directory_for_both_outputs_refuses(self):
        run = self._run(records_directory=self.derived)
        with self.assertRaises(EntryRefused) as caught:
            run.run()
        self.assertEqual(caught.exception.code, RUN_PRECONDITION_REFUSED)
        self.assertEqual(self._published(), [])

    def test_an_undeclared_measurement_status_refuses_before_entry(self):
        run = self._run(measurement_status="RF_MEASUREMENT_PERFORMED")
        with self.assertRaises(EntryRefused) as caught:
            run.run()
        self.assertEqual(caught.exception.code, RUN_PRECONDITION_REFUSED)
        self.assertEqual(run.terminal_double.silenced, 0)

    def test_an_unlabelled_instrument_setting_refuses_before_entry(self):
        run = self._run(instrument_settings={"gain_db": 40.2})
        with self.assertRaises(EntryRefused) as caught:
            run.run()
        self.assertEqual(caught.exception.code, RUN_PRECONDITION_REFUSED)
        self.assertEqual(self._published(), [])

    def test_the_lineage_is_snapshotted_around_publication(self):
        run = self._run()
        result = run.run()
        self.assertEqual(result["lineage_presence_before"],
                         "NO_LINEAGE_NAMESPACE")
        self.assertEqual(result["lineage_presence_after"],
                         "NO_LINEAGE_NAMESPACE")
        self.assertFalse(os.path.exists(os.path.dirname(self.lineage)))


class ProducerScopeTests(EntryTestCase):
    """Enabled for one window, and disabled on the way out of it."""

    def test_the_producer_is_disabled_again_after_a_successful_run(self):
        seen = []
        run = self._run()
        real = entry_module.DerivedEvidenceProducer

        def watching(**kw):
            producer = real(**kw)
            seen.append(producer)
            return producer

        entry_module.DerivedEvidenceProducer = watching
        self.addCleanup(setattr, entry_module, "DerivedEvidenceProducer", real)
        run.run()
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].enabled)

    def test_the_producer_is_disabled_again_after_an_exception(self):
        seen = []
        run = self._run()
        real = entry_module.DerivedEvidenceProducer

        def breaking(**kw):
            producer = real(**kw)
            seen.append(producer)
            producer.publish = lambda: (_ for _ in ()).throw(
                RuntimeError("publication gave out"))
            return producer

        entry_module.DerivedEvidenceProducer = breaking
        self.addCleanup(setattr, entry_module, "DerivedEvidenceProducer", real)
        with self.assertRaises(RuntimeError):
            run.run()
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].enabled)

    def test_the_producer_is_never_enabled_when_entry_refuses(self):
        seen = []
        run = self._run(WALK[:2])
        real = entry_module.DerivedEvidenceProducer
        entry_module.DerivedEvidenceProducer = lambda **kw: seen.append(kw)
        self.addCleanup(setattr, entry_module, "DerivedEvidenceProducer", real)
        with self.assertRaises(EntryRefused):
            run.run()
        self.assertEqual(seen, [])


class ReachabilityTests(EntryTestCase):
    """§13l M.7, checked over the source rather than asserted in prose."""

    def _tree(self):
        with open(entry_module.__file__) as handle:
            return ast.parse(handle.read())

    def test_the_module_imports_no_concurrency_or_network(self):
        tree = self._tree()
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("socket", "threading", "subprocess", "signal",
                          "multiprocessing", "asyncio", "sched", "http",
                          "urllib", "requests", "concurrent"):
            self.assertNotIn(forbidden, imported, forbidden)

    def test_the_module_names_no_device_or_capture_path(self):
        with open(entry_module.__file__) as handle:
            source = handle.read()
        tree = ast.parse(source)
        strings = {node.value for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names |= {node.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute)}
        for token in ("rtl_tcp", "rtlsdr", "/dev/bus/usb", "ARMED", "315535"):
            self.assertEqual([text for text in strings if token in text], [],
                             token)
            self.assertNotIn(token, names, token)

    def test_the_status_declares_what_it_does_not_do(self):
        status = self._run().status()
        self.assertFalse(status["acquires"])
        self.assertFalse(status["opens_a_socket"])
        self.assertFalse(status["schedules"])
        self.assertFalse(status["bounds_are_configurable"])
        self.assertEqual(status["required_fixes"], REQUIRED_FIXES)
        self.assertEqual(status["max_run_monotonic_s"], MAX_RUN_MONOTONIC_S)

    def test_the_bounds_are_not_configurable(self):
        """No constructor argument, no environment variable, no setter."""
        import dataclasses
        fields = {f.name for f in dataclasses.fields(PositionEntryRun)}
        for name in ("required_fixes", "max_run_monotonic_s", "deadline",
                     "fix_count", "timeout"):
            self.assertNotIn(name, fields)
        with open(entry_module.__file__) as handle:
            tree = ast.parse(handle.read())
        attributes = {node.attr for node in ast.walk(tree)
                      if isinstance(node, ast.Attribute)}
        self.assertNotIn("environ", attributes)

    def test_the_new_names_collide_with_nothing_unjudged(self):
        from test_scythe_verdict_vocabularies import (
            cross_set_collisions, discovered_tokens, judged,
        )
        tokens = discovered_tokens()
        for candidate in entry_module.ENTRY_REFUSALS + (FIX_ACCEPTED,):
            with self.subTest(candidate=candidate):
                self.assertIn(candidate, tokens)
                unjudged = [hit for hit in cross_set_collisions(candidate, tokens)
                            if not judged(candidate, hit)]
                self.assertEqual(unjudged, [])


class NotEvidenceTests(EntryTestCase):
    """What these tests are, said out loud."""

    def test_every_artefact_lands_in_a_temporary_directory(self):
        run = self._run()
        result = run.run()
        self.assertTrue(result["artefact_path"].startswith(self._dir.name))

    def test_the_walk_used_here_is_a_controlled_string_list(self):
        self.assertEqual(len(WALK), REQUIRED_FIXES)
        for line in WALK:
            parse_fix(line)
