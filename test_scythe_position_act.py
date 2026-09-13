"""Slice 10h: the entrypoint and the wiring from fix to observation record.

**No live execution here.** Every run below uses the injected terminal and
clock from slice 10g's tests, controlled coordinate strings, and pinned
constants redirected into a `TemporaryDirectory`. Nothing touches the real
`/home/spectrcyde/scythe-live-observation` tree, no real terminal is opened,
and none of it can drain `PENDING_AMENDMENTS.md` entry 7.
"""

import ast
import io
import json
import os
import unittest
from contextlib import redirect_stdout

import scythe_position_act as act_module
import scythe_position_entry as entry_module
from scythe_derived_evidence import (
    INSTRUMENT_CONFIGURED_IDLE, RF_MEASUREMENT_NOT_PERFORMED, read_artefact,
)
from scythe_position_entry import REQUIRED_FIXES
from scythe_position_act import (
    ACT_COMPLETE, ACT_REFUSED_BEFORE_PUBLICATION,
    ARTEFACT_PUBLISHED_OBSERVATION_REFUSED, LIVE_RUN, PREFLIGHT_ONLY,
    PREFLIGHT_PASSED, PREFLIGHT_REFUSED, ActRefused, build_parser,
    expected_artefact_path, expected_record_path, live_run, main, preflight,
    run_identity,
)
from scythe_shadow_observation import (
    CARRIED_FROM_ARTEFACT, EVIDENCE_DERIVED_ARTEFACT, LIVE_OBSERVATION,
    NO_LINEAGE_NAMESPACE,
)
from test_scythe_position_entry import WALK, Clock, EntryTestCase, FakeTerminal


class ActTestCase(EntryTestCase):
    """The pinned constants, redirected into this test's temporary tree.

    Redirected rather than parameterised: in production they are the
    authorization's terms and must not be a caller's option, so the seam is a
    module constant a test replaces and `argv` cannot.
    """

    def setUp(self):
        super().setUp()
        self._pinned = {name: getattr(act_module, name) for name in
                        ("DERIVED_DIRECTORY", "RECORDS_DIRECTORY",
                         "LINEAGE_ROOT")}
        act_module.DERIVED_DIRECTORY = self.derived
        act_module.RECORDS_DIRECTORY = self.records
        act_module.LINEAGE_ROOT = self.lineage
        self.addCleanup(self._unpin)
        # Nothing is set aside. An earlier version of this fixture removed
        # `threading` from FORBIDDEN_MODULES because the act reached it through
        # `rf_iq_retention`; §13n Amendment O extracted the identity instead,
        # so the real constant is what every test below sees.

    def _unpin(self):
        for name, value in self._pinned.items():
            setattr(act_module, name, value)

    def _live(self, lines=None, **kw):
        terminal = FakeTerminal(WALK if lines is None else lines, self.clock,
                                **kw)
        self.terminal = terminal
        return live_run(run_identity("t"), terminal=terminal, clock=self.clock)


class ExtractionTests(unittest.TestCase):
    """§13n Amendment O: the identity, out of the instrument.

    This class deliberately does **not** inherit `ActTestCase`, so it sees the
    real `FORBIDDEN_MODULES` and the real pinned paths. The conflict these
    replace -- `threading` reaching the act through `rf_iq_retention` -- was
    recorded here as `KnownFindingTests` until Amendment O resolved it.
    """

    VERIFIED_SIGNAL = "blake2s:6809e50b8cb9a4a50b1ce4afcdbfa232"
    VERIFIED_RECEIVER = "blake2s:e0fcdd0101c4f04257a1c4df23012975"

    def test_both_verified_digests_are_unchanged(self):
        """37al. Byte-for-byte, against the values reproduced independently
        before the extraction."""
        self.assertEqual(act_module.declared_signal_chain_hash(),
                         self.VERIFIED_SIGNAL)
        self.assertEqual(act_module.declared_receiver_state_chain_hash(),
                         self.VERIFIED_RECEIVER)

    def test_the_retention_api_produces_the_same_digests(self):
        """37am. The public API is unchanged and so is every value it gives."""
        import rf_iq_retention
        self.assertEqual(
            rf_iq_retention.signal_chain_hash(
                sensor_id="RTL2838-0BDA-2838", sample_type="uint8",
                sample_rate_hz=2_400_000.0, antenna="UNDECLARED",
                feedline="UNDECLARED", extension_mm="UNDECLARED", gain_db=40.2),
            self.VERIFIED_SIGNAL)
        self.assertEqual(rf_iq_retention.SIGNAL_CHAIN_REVISION, "v3")
        self.assertEqual(rf_iq_retention.SIGNAL_CHAIN_SCHEMA,
                         "scythe.rf-signal-chain.v2")

    def test_the_pure_module_imports_nothing_heavy(self):
        """37an. Over its own source and its transitive first-party closure."""
        import rf_signal_chain_identity
        root = os.path.dirname(os.path.abspath(rf_signal_chain_identity.__file__))
        closure = act_module._first_party_closure(
            root, "rf_signal_chain_identity")
        self.assertEqual(closure, ["rf_signal_chain_identity"])
        imported = set()
        with open(rf_signal_chain_identity.__file__) as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("threading", "rf_iq_ring", "graphops_rf_antenna",
                          "numpy", "np", "rf_channelizer", "rf_bridge",
                          "socket", "subprocess"):
            self.assertNotIn(forbidden, imported, forbidden)
        self.assertEqual(imported, {"__future__", "hashlib", "json", "typing"})

    def test_the_pure_module_resolves_nothing(self):
        """37ao. AST for the lookups, then a behavioural check."""
        import rf_signal_chain_identity
        with open(rf_signal_chain_identity.__file__) as handle:
            tree = ast.parse(handle.read())
        attributes = {node.attr for node in ast.walk(tree)
                      if isinstance(node, ast.Attribute)}
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        for forbidden in ("environ", "getenv", "read", "listdir"):
            self.assertNotIn(forbidden, attributes, forbidden)
        for forbidden in ("open", "input", "__import__"):
            self.assertNotIn(forbidden, called, forbidden)

        declaration = dict(act_module.SIGNAL_CHAIN_DECLARATION)
        before = rf_signal_chain_identity.signal_chain_hash(**declaration)
        for name in ("SDRPP_ANTENNA_ID", "SDRPP_FEEDLINE_ID",
                     "SDRPP_ANTENNA_EXTENSION_MM"):
            os.environ[name] = "ARBITRARY"
            self.addCleanup(os.environ.pop, name, None)
        self.assertEqual(rf_signal_chain_identity.signal_chain_hash(**declaration),
                         before)

    def test_there_is_exactly_one_hashing_implementation(self):
        """37ap. Mechanically: `rf_iq_retention` keeps no copy of the
        construction, the canonical bytes or the digest."""
        import rf_iq_retention
        with open(rf_iq_retention.__file__) as handle:
            tree = ast.parse(handle.read())
        for name in ("signal_chain_manifest", "canonical_signal_chain_bytes",
                     "signal_chain_hash"):
            function = next(node for node in ast.walk(tree)
                            if isinstance(node, ast.FunctionDef)
                            and node.name == name)
            delegates = any(isinstance(inner, ast.Attribute)
                            and isinstance(inner.value, ast.Name)
                            and inner.value.id == "_identity"
                            for inner in ast.walk(function))
            self.assertTrue(delegates, f"{name} does not delegate")
            hashes = any(isinstance(inner, ast.Attribute)
                         and inner.attr == "blake2s"
                         for inner in ast.walk(function))
            self.assertFalse(hashes, f"{name} hashes on its own")
        self.assertIs(rf_iq_retention.canonical_signal_chain_bytes.__module__
                      and True, True)

    def test_the_environment_still_moves_the_retention_side_digest(self):
        """37aq. The resolvers did not vanish -- they moved nowhere."""
        import rf_iq_retention
        before = rf_iq_retention.signal_chain_hash(
            sensor_id="S", sample_type="uint8", sample_rate_hz=2_048_000.0,
            gain_db=None)
        os.environ["SDRPP_ANTENNA_ID"] = "WHIP-TEST-ONLY"
        self.addCleanup(os.environ.pop, "SDRPP_ANTENNA_ID", None)
        after = rf_iq_retention.signal_chain_hash(
            sensor_id="S", sample_type="uint8", sample_rate_hz=2_048_000.0,
            gain_db=None)
        self.assertNotEqual(before, after)
        # And the act, whose declaration is complete, does not move.
        self.assertEqual(act_module.declared_signal_chain_hash(),
                         self.VERIFIED_SIGNAL)

    def test_an_incomplete_declaration_raises_rather_than_resolves(self):
        """The structural half of 37aj.

        Before the extraction, omitting the antenna meant `signal_chain_manifest`
        quietly read `SDRPP_ANTENNA_ID`. The pure module has no defaults, so an
        omission is a `TypeError` at the call — the failure is loud and at the
        caller rather than silent and in the digest.
        """
        import rf_signal_chain_identity
        for field in ("antenna", "feedline", "extension_mm",
                      "feedline_length_m", "gain_db"):
            with self.subTest(field=field):
                short = {k: v for k, v
                         in act_module.SIGNAL_CHAIN_DECLARATION.items()
                         if k != field}
                with self.assertRaises(TypeError):
                    rf_signal_chain_identity.signal_chain_hash(**short)

    def test_the_act_imports_the_pure_module_not_the_instrument(self):
        """37ar."""
        imports = act_module._imports_of(act_module.__file__)
        self.assertIn("rf_signal_chain_identity", imports)
        self.assertNotIn("rf_iq_retention", imports)

    def test_the_real_preflight_passes_with_nothing_reachable(self):
        """37as. On the pinned paths, read-only, with the real constants."""
        report = preflight(run_identity("t"))
        self.assertEqual(report["forbidden_modules_reachable"], [])
        self.assertEqual(report["dynamic_import_constructs_detected"], [])
        self.assertNotIn("rf_iq_retention", report["first_party_modules"])
        self.assertIn("rf_signal_chain_identity", report["first_party_modules"])
        self.assertEqual(report["outcome"], PREFLIGHT_PASSED)
        self.assertEqual(report["findings"], [])


class IdentityTests(unittest.TestCase):
    """Deterministic from the label, so a retry rediscovers rather than
    duplicates."""

    def test_the_same_label_is_the_same_run(self):
        self.assertEqual(run_identity("mukilteo-01"), run_identity("mukilteo-01"))
        self.assertEqual(expected_artefact_path(run_identity("a")),
                         expected_artefact_path(run_identity("a")))

    def test_different_labels_are_different_runs(self):
        self.assertNotEqual(expected_artefact_path(run_identity("a")),
                            expected_artefact_path(run_identity("b")))

    def test_the_identity_is_not_a_timestamp_or_a_nonce(self):
        """AST: nothing in the identity path reads a clock or randomness."""
        with open(act_module.__file__) as handle:
            tree = ast.parse(handle.read())
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "run_identity")
        attributes = {node.attr for node in ast.walk(function)
                      if isinstance(node, ast.Attribute)}
        for forbidden in ("time", "time_ns", "monotonic", "urandom", "random",
                          "uuid4", "now"):
            self.assertNotIn(forbidden, attributes, forbidden)

    def test_an_unusable_label_is_refused(self):
        for label in ("", "x" * 65, "has space", "../escape", "semi;colon",
                      "51.5"):
            with self.subTest(label=label):
                with self.assertRaises(ActRefused):
                    run_identity(label)

    def test_the_expected_paths_land_in_their_own_directories(self):
        run_id = run_identity("t")
        self.assertTrue(expected_artefact_path(run_id).startswith(
            act_module.DERIVED_DIRECTORY + os.sep))
        self.assertTrue(expected_record_path(run_id).startswith(
            act_module.RECORDS_DIRECTORY + os.sep))


class PreflightTests(ActTestCase):
    """The default operation, which does nothing."""

    def test_a_clean_tree_passes(self):
        report = preflight(run_identity("t"))
        self.assertEqual(report["outcome"], PREFLIGHT_PASSED)
        self.assertEqual(report["mode"], PREFLIGHT_ONLY)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["lineage_presence"], NO_LINEAGE_NAMESPACE)
        self.assertFalse(report["producer_enabled"])
        self.assertEqual(report["expected_walk_steps"], 11)

    def test_preflight_writes_nothing_and_opens_no_terminal(self):
        before = (sorted(os.listdir(self.derived)),
                  sorted(os.listdir(self.records)))
        preflight(run_identity("t"))
        self.assertEqual(before, (sorted(os.listdir(self.derived)),
                                  sorted(os.listdir(self.records))))
        self.assertFalse(os.path.exists(os.path.dirname(self.lineage)))

    def test_preflight_constructs_no_terminal(self):
        """AST: the preflight function names no terminal and no producer."""
        with open(act_module.__file__) as handle:
            tree = ast.parse(handle.read())
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "preflight")
        called = {node.func.id for node in ast.walk(function)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        for forbidden in ("Terminal", "PositionEntryRun",
                          "DerivedEvidenceProducer", "ShadowObservation"):
            self.assertNotIn(forbidden, called, forbidden)

    def test_a_loosened_directory_mode_is_a_finding(self):
        os.chmod(self.derived, 0o755)
        report = preflight(run_identity("t"))
        self.assertEqual(report["outcome"], PREFLIGHT_REFUSED)
        self.assertTrue(any("mode" in finding for finding in report["findings"]))

    def test_a_missing_directory_is_a_finding(self):
        os.rmdir(self.records)
        report = preflight(run_identity("t"))
        self.assertEqual(report["outcome"], PREFLIGHT_REFUSED)
        self.assertIsNone(report["directory_modes"]["records"])

    def test_an_output_inside_the_lineage_namespace_is_a_finding(self):
        inside = os.path.join(self._dir.name, "ledger", "derived")
        os.makedirs(inside, 0o700)
        act_module.DERIVED_DIRECTORY = inside
        report = preflight(run_identity("t"))
        self.assertEqual(report["outcome"], PREFLIGHT_REFUSED)
        self.assertFalse(report["namespace_disjoint"]["derived"])

    def test_an_existing_lineage_namespace_is_a_finding(self):
        os.makedirs(os.path.dirname(self.lineage), 0o700)
        report = preflight(run_identity("t"))
        self.assertEqual(report["outcome"], PREFLIGHT_REFUSED)
        self.assertTrue(report["lineage_namespace_exists"])

    def test_an_existing_artefact_is_a_finding(self):
        run_id = run_identity("t")
        with open(expected_artefact_path(run_id), "wb") as handle:
            handle.write(b"")
        report = preflight(run_id)
        self.assertEqual(report["outcome"], PREFLIGHT_REFUSED)
        self.assertTrue(any("already exists" in f for f in report["findings"]))

    def test_the_report_is_bounded_and_carries_no_coordinate(self):
        """Every value is a path, a mode, a declared name, a bool or a count.
        Nothing in the preflight has ever seen a coordinate."""
        report = preflight(run_identity("t"))
        blob = json.dumps(report)
        for line in WALK:
            for value in line.split():
                self.assertNotIn(value, blob)
        self.assertNotIn("latitude", blob)
        self.assertNotIn("longitude", blob)

    def test_the_report_is_json_serializable(self):
        json.dumps(preflight(run_identity("t")))


class LiveRunTests(ActTestCase):
    """The wiring, driven by an injected terminal over controlled strings."""

    def test_the_act_produces_one_artefact_and_one_record(self):
        report = self._live()
        self.assertEqual(report["outcome"], ACT_COMPLETE)
        self.assertEqual(report["mode"], LIVE_RUN)
        self.assertEqual(len(os.listdir(self.derived)), 1)
        self.assertEqual(len(os.listdir(self.records)), 1)

    def test_the_record_says_it_is_a_live_observation_of_an_idle_instrument(self):
        report = self._live()
        self.assertEqual(report["run_class"], LIVE_OBSERVATION)
        self.assertEqual(report["evidence_source"], EVIDENCE_DERIVED_ARTEFACT)
        self.assertEqual(report["measurement_status"],
                         RF_MEASUREMENT_NOT_PERFORMED)
        self.assertEqual(report["instrument_state"], INSTRUMENT_CONFIGURED_IDLE)
        self.assertEqual(report["declaration_authority"], CARRIED_FROM_ARTEFACT)

    def test_the_record_lands_in_the_records_directory_only(self):
        report = self._live()
        self.assertEqual(os.path.dirname(report["record_path"]), self.records)
        self.assertEqual(os.listdir(self.records),
                         [os.path.basename(expected_record_path(run_identity("t")))])
        self.assertTrue(os.path.basename(report["artefact_path"])
                        in os.listdir(self.derived))

    def test_the_paths_are_the_ones_preflight_predicted(self):
        run_id = run_identity("t")
        predicted = (expected_artefact_path(run_id), expected_record_path(run_id))
        report = self._live()
        self.assertEqual((report["artefact_path"], report["record_path"]),
                         predicted)

    def test_the_artefact_is_read_back_from_disk(self):
        """Not the in-memory fixes handed sideways: a test counts the reads."""
        opened = []
        real = act_module.read_artefact

        def counting(path):
            opened.append(path)
            return real(path)

        act_module.read_artefact = counting
        self.addCleanup(setattr, act_module, "read_artefact", real)
        report = self._live()
        self.assertEqual(opened, [report["artefact_path"]])

    def test_the_observed_verdicts_come_from_the_published_records(self):
        report = self._live()
        artefact = read_artefact(report["artefact_path"])
        self.assertEqual(report["walk_steps"], len(artefact.records))
        self.assertEqual(report["verdicts_observed"], len(artefact.records))

    def test_the_lineage_is_reported_absent_and_stays_absent(self):
        report = self._live()
        self.assertEqual(report["lineage_presence_before"], NO_LINEAGE_NAMESPACE)
        self.assertEqual(report["lineage_presence_after"], NO_LINEAGE_NAMESPACE)
        self.assertFalse(os.path.exists(os.path.dirname(self.lineage)))

    def test_a_failed_preflight_stops_before_any_terminal_is_touched(self):
        os.chmod(self.derived, 0o755)
        terminal = FakeTerminal(WALK, self.clock)
        with self.assertRaises(ActRefused) as caught:
            live_run(run_identity("t"), terminal=terminal, clock=self.clock)
        self.assertEqual(caught.exception.code, ACT_REFUSED_BEFORE_PUBLICATION)
        self.assertEqual(terminal.silenced, 0)
        self.assertEqual(os.listdir(self.records), [])

    def test_a_short_walk_publishes_neither_artefact_nor_record(self):
        with self.assertRaises(ActRefused) as caught:
            self._live(WALK[:4])
        self.assertEqual(caught.exception.code, ACT_REFUSED_BEFORE_PUBLICATION)
        self.assertEqual(os.listdir(self.derived), [])
        self.assertEqual(os.listdir(self.records), [])


class PublishedArtefactTests(ActTestCase):
    """An artefact that exists is evidence, whatever happens next."""

    def _break_observation(self):
        real = act_module.ShadowObservation

        class Refusing(real):
            def run(self, source):
                raise act_module.ObservationRefused("OBSERVATION_PATH_REFUSED",
                                                    "deliberate")

        act_module.ShadowObservation = Refusing
        self.addCleanup(setattr, act_module, "ShadowObservation", real)

    def test_a_refused_observation_leaves_the_artefact_standing(self):
        self._break_observation()
        with self.assertRaises(ActRefused) as caught:
            self._live()
        self.assertEqual(caught.exception.code,
                         ARTEFACT_PUBLISHED_OBSERVATION_REFUSED)
        self.assertEqual(len(os.listdir(self.derived)), 1)
        self.assertEqual(os.listdir(self.records), [])

    def test_the_artefact_is_byte_identical_after_the_refusal(self):
        """37af, behaviourally. The bytes are captured the instant publication
        returns, and compared after the refusal has propagated."""
        captured = {}
        real = act_module._observe

        def capture_then_observe(run_id, published):
            path = published["path"] if "path" in published \
                else published["artefact_path"]
            with open(path, "rb") as handle:
                captured["bytes"] = handle.read()
            captured["path"] = path
            return real(run_id, published)

        act_module._observe = capture_then_observe
        self.addCleanup(setattr, act_module, "_observe", real)
        self._break_observation()
        with self.assertRaises(ActRefused):
            self._live()

        self.assertTrue(captured["bytes"])
        with open(captured["path"], "rb") as handle:
            self.assertEqual(handle.read(), captured["bytes"])
        self.assertEqual(os.listdir(self.derived),
                         [os.path.basename(captured["path"])])

    def test_exactly_one_publication_counted_at_the_writer(self):
        """At the **producer**, which is the thing that writes.

        An earlier version of this test counted calls to
        `PositionEntryRun.publish`, one layer above. A negative control that
        published twice from inside that method was invisible to it: the
        counter wrapped the outermost call and saw one. Counting at the layer
        that actually creates the file is the difference between measuring the
        property and measuring the wrapper.
        """
        import scythe_derived_evidence_producer as producer_module

        published = []
        real = producer_module.DerivedEvidenceProducer.publish

        def counting(producer):
            result = real(producer)
            published.append((result["path"], result["rediscovered"]))
            return result

        producer_module.DerivedEvidenceProducer.publish = counting
        self.addCleanup(setattr, producer_module.DerivedEvidenceProducer,
                        "publish", real)
        self._break_observation()
        with self.assertRaises(ActRefused):
            self._live()
        self.assertEqual(len(published), 1, published)
        self.assertFalse(published[0][1], "the artefact was published twice")
        self.assertEqual(len(os.listdir(self.derived)), 1)

    def test_the_artefact_still_reads_after_the_refusal(self):
        self._break_observation()
        run_id = run_identity("t")
        with self.assertRaises(ActRefused):
            self._live()
        artefact = read_artefact(expected_artefact_path(run_id))
        self.assertEqual(len(artefact.records), REQUIRED_FIXES - 1)
        self.assertEqual(artefact.measurement_status,
                         RF_MEASUREMENT_NOT_PERFORMED)

    def test_the_act_removes_nothing(self):
        """A supplement to the behavioural tests above, not the proof.

        It establishes that six names are absent, which is a much weaker claim
        than "never rewritten or republished" -- that one is made by the byte
        comparison and the publication count.
        """
        with open(act_module.__file__) as handle:
            tree = ast.parse(handle.read())
        attributes = {node.attr for node in ast.walk(tree)
                      if isinstance(node, ast.Attribute)}
        for forbidden in ("unlink", "remove", "rmtree", "rmdir", "replace",
                          "truncate"):
            self.assertNotIn(forbidden, attributes, forbidden)

    def test_the_refusal_names_no_directory_and_no_coordinate(self):
        self._break_observation()
        with self.assertRaises(ActRefused) as caught:
            self._live()
        message = str(caught.exception)
        self.assertNotIn(self.derived, message)
        for line in WALK:
            for value in line.split():
                self.assertNotIn(value, message)


class EntrypointTests(ActTestCase):
    """Preflight by default, and a live run asked for twice."""

    def _main(self, argv):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = main(argv)
        return code, json.loads(stream.getvalue())

    def test_no_operation_is_a_preflight(self):
        code, report = self._main(["--run-label", "t"])
        self.assertEqual(code, 0)
        self.assertEqual(report["mode"], PREFLIGHT_ONLY)
        self.assertEqual(report["outcome"], PREFLIGHT_PASSED)
        self.assertEqual(os.listdir(self.derived), [])

    def test_a_failing_preflight_exits_nonzero(self):
        os.chmod(self.records, 0o755)
        code, report = self._main(["--run-label", "t"])
        self.assertEqual(code, 1)
        self.assertEqual(report["outcome"], PREFLIGHT_REFUSED)

    def test_one_switch_is_not_enough_for_a_live_run(self):
        code, report = self._main(["--run-label", "t", "--live-run"])
        self.assertEqual(code, 2)
        self.assertEqual(report["outcome"], PREFLIGHT_REFUSED)
        self.assertEqual(os.listdir(self.derived), [])

    def test_the_confirmation_alone_is_still_a_preflight(self):
        code, report = self._main(["--run-label", "t", "--i-am-walking-now"])
        self.assertEqual(code, 0)
        self.assertEqual(report["mode"], PREFLIGHT_ONLY)
        self.assertEqual(os.listdir(self.derived), [])

    def test_an_unusable_label_exits_without_a_preflight(self):
        code, report = self._main(["--run-label", "has space"])
        self.assertEqual(code, 2)
        self.assertEqual(report["outcome"], PREFLIGHT_REFUSED)

    def test_no_coordinate_can_enter_through_argv(self):
        """Every option is a label or a switch. Nothing in argv is ever
        converted to a number, so there is no door for a fix."""
        parser = build_parser()
        for action in parser._actions:
            with self.subTest(option=action.dest):
                self.assertIn(action.type, (None,))
                self.assertNotIn("lat", action.dest)
                self.assertNotIn("lon", action.dest)
                self.assertNotIn("fix", action.dest)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--run-label", "t", "--latitude", "51.5"])

    def test_the_module_reads_no_coordinate_from_anywhere_else(self):
        """AST: no environment read, no stdin parse, no float() over input."""
        with open(act_module.__file__) as handle:
            tree = ast.parse(handle.read())
        attributes = {node.attr for node in ast.walk(tree)
                      if isinstance(node, ast.Attribute)}
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        for forbidden in ("environ", "getenv", "stdin", "readlines"):
            self.assertNotIn(forbidden, attributes, forbidden)
        for forbidden in ("float", "input"):
            self.assertNotIn(forbidden, called, forbidden)
        # `open` exists, but only where the import closure reads this
        # repository's own source. Anywhere else it would be a door a fix
        # could come through, so the test names the one function rather than
        # banning the builtin and then quietly allowing it back.
        readers = {node.name for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and any(isinstance(inner, ast.Call)
                           and isinstance(inner.func, ast.Name)
                           and inner.func.id == "open"
                           for inner in ast.walk(node))}
        self.assertEqual(readers, {"_imports_of", "_dynamic_imports"})


class ReachabilityTests(ActTestCase):
    """§13l M.7, over the module and over the live process."""

    def test_the_module_imports_no_concurrency_or_network(self):
        with open(act_module.__file__) as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in act_module.FORBIDDEN_MODULES:
            self.assertNotIn(forbidden, imported, forbidden)

    def test_the_module_names_no_device_or_capture_path(self):
        with open(act_module.__file__) as handle:
            tree = ast.parse(handle.read())
        strings = {node.value for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names |= {node.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute)}
        for token in ("rtl_tcp", "/dev/bus/usb", "ARMED", "315535", "rtlsdr"):
            self.assertEqual([text for text in strings if token in text], [],
                             token)
            self.assertNotIn(token, names, token)

    def test_the_preflight_reports_the_reachable_capabilities(self):
        """A static first-party import closure, **not** `sys.modules`.

        The earlier wording here said the opposite, and survived the first
        version of this test being replaced. It described `sys.modules` -- the
        approach §13m N.6 rejects for reporting whatever the harness imported.
        """
        report = preflight(run_identity("t"))
        self.assertIn("forbidden_modules_reachable", report)
        self.assertIn("scythe_position_entry", report["first_party_modules"])
        self.assertIn("scythe_shadow_observation", report["first_party_modules"])
        self.assertFalse(report["opens_a_device"])
        self.assertFalse(report["opens_a_socket"])
        self.assertFalse(report["starts_a_process"])
        self.assertFalse(report["schedules"])

    def test_a_reachable_forbidden_module_is_a_finding(self):
        """`json` is imported by this act's own module, so declaring it
        forbidden must be noticed."""
        real = act_module.FORBIDDEN_MODULES
        act_module.FORBIDDEN_MODULES = real + ("json",)
        self.addCleanup(setattr, act_module, "FORBIDDEN_MODULES", real)
        report = preflight(run_identity("t"))
        self.assertTrue(any(entry.startswith("json ")
                            for entry in report["forbidden_modules_reachable"]),
                        report["forbidden_modules_reachable"])
        self.assertEqual(report["outcome"], PREFLIGHT_REFUSED)

    def test_a_capability_reached_through_a_dependency_is_a_finding(self):
        """`termios` arrives through `scythe_position_entry`, not through this
        module -- the case a scan of this file alone would miss."""
        real = act_module.FORBIDDEN_MODULES
        act_module.FORBIDDEN_MODULES = real + ("termios",)
        self.addCleanup(setattr, act_module, "FORBIDDEN_MODULES", real)
        report = preflight(run_identity("t"))
        self.assertIn("termios (via scythe_position_entry)",
                      report["forbidden_modules_reachable"])

    def test_the_harness_is_not_mistaken_for_the_act(self):
        """`unittest` imports `signal`, so a `sys.modules` check would report
        this test run as a capability of the act. The closure is static."""
        import sys
        self.assertIn("signal", sys.modules)
        self.assertIn("signal", act_module.FORBIDDEN_MODULES)
        report = preflight(run_identity("t"))
        self.assertEqual(report["forbidden_modules_reachable"], [])
        self.assertEqual(report["outcome"], PREFLIGHT_PASSED)

    def test_the_new_names_collide_with_nothing_unjudged(self):
        from test_scythe_verdict_vocabularies import (
            cross_set_collisions, discovered_tokens, judged,
        )
        tokens = discovered_tokens()
        for candidate in act_module.ACT_MODES + act_module.ACT_OUTCOMES:
            with self.subTest(candidate=candidate):
                self.assertIn(candidate, tokens)
                unjudged = [hit for hit in cross_set_collisions(candidate, tokens)
                            if not judged(candidate, hit)]
                self.assertEqual(unjudged, [])


class NotLiveTests(ActTestCase):
    """What these tests are, said out loud."""

    def test_every_path_used_here_is_temporary(self):
        report = self._live()
        for key in ("artefact_path", "record_path"):
            self.assertTrue(report[key].startswith(self._dir.name), key)

    def test_the_pinned_constants_are_restored_after_each_test(self):
        self._unpin()
        self.assertEqual(act_module.DERIVED_DIRECTORY,
                         "/home/spectrcyde/scythe-live-observation/derived")
        self.assertEqual(act_module.LINEAGE_ROOT,
                         "/home/spectrcyde/scythe-ledger/promotion")

    def test_the_terminal_here_is_an_object(self):
        report = self._live()
        self.assertEqual(report["outcome"], ACT_COMPLETE)
        self.assertIsInstance(self.terminal, FakeTerminal)
        self.assertEqual(self.terminal.silenced, 1)


class ComputedIdentityTests(ActTestCase):
    """§13m N.5: the act accepts no identity from a caller.

    Not "a literal is refused" -- after evaluation a computed digest and an
    identical literal are the same string and nothing at run time can tell them
    apart. What is enforceable is that the act computes all three itself, and
    the guarantee is the control that substitutes a literal and makes these
    fail.
    """

    def test_the_chain_identities_match_the_canonical_helpers(self):
        from rf_receiver_state import (receiver_state_chain_hash,
                                       receiver_state_chain_manifest)
        from rf_signal_chain_identity import signal_chain_hash
        self.assertEqual(
            act_module.declared_signal_chain_hash(),
            signal_chain_hash(**act_module.SIGNAL_CHAIN_DECLARATION))
        self.assertEqual(
            act_module.declared_receiver_state_chain_hash(),
            receiver_state_chain_hash(receiver_state_chain_manifest(
                **act_module.RECEIVER_STATE_DECLARATION)))

    def test_the_configuration_identity_is_recomputed_independently(self):
        """Recomputed here from N.5a's rules rather than from the act's own
        function: a shared serialization convention settles the encoding and
        says nothing about this schema, field set or values."""
        import hashlib
        import json
        manifest = act_module.act_configuration_manifest()
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8")
        expected = "blake2s:" + hashlib.blake2s(canonical,
                                                digest_size=16).hexdigest()
        self.assertEqual(act_module.act_configuration_identity(), expected)

    def test_every_identity_the_act_carries_is_the_computed_one(self):
        report = self._live()
        artefact = read_artefact(report["artefact_path"])
        self.assertEqual(artefact.provenance["signal_chain_hash"],
                         act_module.declared_signal_chain_hash())
        for record in artefact.records:
            for side in ("before", "after"):
                self.assertEqual(record[side]["receiver_state_chain_hash"],
                                 act_module.declared_receiver_state_chain_hash())

    def test_changing_a_declared_value_changes_its_digest(self):
        for declaration, field, value in (
                ("SIGNAL_CHAIN_DECLARATION", "gain_db", 20.0),
                ("SIGNAL_CHAIN_DECLARATION", "sample_rate_hz", 1_024_000.0),
                ("SIGNAL_CHAIN_DECLARATION", "antenna", "WHIP-B"),
                ("RECEIVER_STATE_DECLARATION", "alignment_method",
                 "SHARED_MONOTONIC_SOURCE"),
                ("RECEIVER_STATE_DECLARATION", "heading_source",
                 "DEVICE_MAGNETOMETER")):
            with self.subTest(field=field):
                real = getattr(act_module, declaration)
                before = (act_module.declared_signal_chain_hash(),
                          act_module.declared_receiver_state_chain_hash(),
                          act_module.act_configuration_identity())
                setattr(act_module, declaration, dict(real, **{field: value}))
                try:
                    after = (act_module.declared_signal_chain_hash(),
                             act_module.declared_receiver_state_chain_hash(),
                             act_module.act_configuration_identity())
                finally:
                    setattr(act_module, declaration, real)
                self.assertNotEqual(before, after)
                # The configuration identity moves with either chain.
                self.assertNotEqual(before[2], after[2])

    def test_the_environment_cannot_change_an_identity(self):
        """37aj. `signal_chain_manifest` falls back to these three when a value
        is not declared, so a complete declaration is what keeps the identity
        about the instrument rather than about the shell."""
        before = act_module.declared_signal_chain_hash()
        for name in ("SDRPP_ANTENNA_ID", "SDRPP_FEEDLINE_ID",
                     "SDRPP_ANTENNA_EXTENSION_MM"):
            os.environ[name] = "SOMETHING-ELSE-ENTIRELY"
            self.addCleanup(os.environ.pop, name, None)
        self.assertEqual(act_module.declared_signal_chain_hash(), before)
        self.assertEqual(act_module.act_configuration_identity(),
                         act_module.act_configuration_identity())

    def test_the_configuration_manifest_is_the_closed_field_set(self):
        manifest = act_module.act_configuration_manifest()
        self.assertEqual(sorted(manifest),
                         sorted(act_module.ACT_CONFIGURATION_FIELDS))

    def test_a_missing_configuration_field_refuses(self):
        manifest = act_module.act_configuration_manifest()
        for field in act_module.ACT_CONFIGURATION_FIELDS:
            with self.subTest(field=field):
                short = {k: v for k, v in manifest.items() if k != field}
                with self.assertRaises(ActRefused):
                    act_module.act_configuration_identity(short)

    def test_an_undeclared_configuration_field_refuses(self):
        manifest = dict(act_module.act_configuration_manifest(),
                        derived_directory=act_module.DERIVED_DIRECTORY)
        with self.assertRaises(ActRefused):
            act_module.act_configuration_identity(manifest)

    def test_relocating_the_output_leaves_the_identity_unchanged(self):
        """37ak. Paths are deliberately outside the semantic configuration:
        moving an output must not invent a different instrument."""
        before = act_module.act_configuration_identity()
        act_module.DERIVED_DIRECTORY = os.path.join(self._dir.name, "moved")
        act_module.RECORDS_DIRECTORY = os.path.join(self._dir.name, "moved-r")
        self.assertEqual(act_module.act_configuration_identity(), before)

    def test_the_device_identity_is_bounded_to_a_class(self):
        """N.5c. No serial, no uniqueness, and the report says so."""
        self.assertFalse(act_module.DEVICE_IDENTITY_UNIQUE)
        self.assertEqual(act_module.DEVICE_IDENTITY_SCOPE,
                         "OPERATOR_DECLARED_VID_PID_CLASS")
        self.assertIn(act_module.DEVICE_VID_PID.replace(":", "-").upper(),
                      act_module.DEVICE_ID.upper())
        report = preflight(run_identity("t"))
        self.assertFalse(report["device_identity_unique"])
        self.assertIn("SERIAL", report["device_identity_note"])


class DynamicImportTests(ActTestCase):
    """§13m N.6a: a named, closed set, reported as such."""

    def test_the_checked_set_is_the_contracted_one(self):
        self.assertEqual(act_module.DYNAMIC_IMPORT_CONSTRUCTS,
                         ("__import__", "importlib", "runpy", "eval", "exec"))
        report = preflight(run_identity("t"))
        self.assertEqual(report["dynamic_import_constructs_checked"],
                         list(act_module.DYNAMIC_IMPORT_CONSTRUCTS))

    def test_the_claim_is_bounded_where_it_is_published(self):
        report = preflight(run_identity("t"))
        claim = report["capability_claim"]
        self.assertIn("STATIC FIRST-PARTY IMPORT REACHABILITY", claim)
        self.assertIn("NOT A PROOF OF RUNTIME CAPABILITY", claim)

    def test_each_construct_is_detected_where_it_appears(self):
        """Over a synthetic module tree, one construct at a time.

        Deliberately **not** by writing a probe into a repository source file
        and restoring it: that passes, and it leaves the repository modified if
        the process dies between the two, and it races another run doing the
        same. The detector takes a root so this costs nothing.
        """
        tree = os.path.join(self._dir.name, "closure")
        os.makedirs(tree, exist_ok=True)
        for construct, snippet in (
                ("__import__", "__import__('os')"),
                ("importlib", "importlib.import_module('os')"),
                ("runpy", "runpy.run_module('os')"),
                ("eval", "eval('1')"),
                ("exec", "exec('pass')")):
            with self.subTest(construct=construct):
                with open(os.path.join(tree, "probe.py"), "w") as handle:
                    handle.write(f"import other\n\n\ndef go():\n    return {snippet}\n")
                with open(os.path.join(tree, "other.py"), "w") as handle:
                    handle.write("x = 1\n")
                detected = act_module._dynamic_imports(root=tree, entry="probe")
                self.assertIn(f"{construct} (via probe)", detected)

    def test_a_construct_reached_through_a_dependency_is_detected(self):
        """Transitive, like the module closure it supplements."""
        tree = os.path.join(self._dir.name, "closure2")
        os.makedirs(tree, exist_ok=True)
        with open(os.path.join(tree, "probe.py"), "w") as handle:
            handle.write("import deep\n")
        with open(os.path.join(tree, "deep.py"), "w") as handle:
            handle.write("import importlib\n\n\ndef go():\n"
                         "    return importlib.import_module('os')\n")
        detected = act_module._dynamic_imports(root=tree, entry="probe")
        self.assertIn("importlib (via deep)", detected)

    def test_a_clean_tree_detects_nothing(self):
        tree = os.path.join(self._dir.name, "closure3")
        os.makedirs(tree, exist_ok=True)
        with open(os.path.join(tree, "probe.py"), "w") as handle:
            handle.write("import os\n\n\ndef go():\n    return os.getcwd()\n")
        self.assertEqual(act_module._dynamic_imports(root=tree, entry="probe"), [])

    def test_the_detector_touches_no_repository_source(self):
        """The regression guard for the test this replaced."""
        with open(act_module.__file__) as handle:
            tree = ast.parse(handle.read())
        writers = {node.name for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and any(isinstance(inner, ast.Call)
                           and isinstance(inner.func, ast.Name)
                           and inner.func.id == "open"
                           and len(inner.args) > 1
                           for inner in ast.walk(node))}
        self.assertEqual(writers, set())
