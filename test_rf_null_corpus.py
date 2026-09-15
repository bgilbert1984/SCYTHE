"""§5.19: the synthetic harness, and the boundary it must not blur.

Every test here injects a small plan. The declared plan is 66 732 windows and a
suite that generated it to assert a shape would be measuring patience.
"""

import ast
import unittest

import numpy as np

import rf_null_corpus as corpus_module
from rf_null_corpus import (
    CAPTURED, CORPUS_INCOMPLETE_AWAITING_CAPTURE, GENERATOR_CONFIG_REFUSED,
    STRATUM_AWAITING_CAPTURE, STRATUM_NOT_DECLARED, STRATUM_NOT_SYNTHESISABLE,
    SYNTHESIS_PLANNED, SYNTHESISABLE, SYNTHETIC, TUNER_REQUIRED,
    CORPUS_INCOMPLETE_STRATA_MISSING, PROVENANCE_DIGEST_MISMATCH,
    CorpusRefused, GeneratorConfig, SyntheticWindow, declared_plan,
    generate_stratum, generate_synthetic_corpus, generate_window, plan_state,
    regenerate, status,
)
from rf_validation_manifest import STRATA, STRATUM_KEYS

SMALL = 4096


def config(seed=3):
    return GeneratorConfig(seed=seed, window_samples=SMALL)


def small_plan(count=2):
    return {key: count for key in STRATUM_KEYS}


class ClassificationTests(unittest.TestCase):
    """Which strata a generator may honestly claim."""

    def test_the_tuner_strata_are_exactly_the_three_5_19_named(self):
        self.assertEqual(sorted(TUNER_REQUIRED),
                         ["GAIN_STEPS", "RECEIVER_SPURS", "RETUNE_TRANSIENTS"])

    def test_every_stratum_is_classified_exactly_once(self):
        """A second declaration about the strata is a thing that can drift.

        Partition, checked three ways: the union is every stratum, the
        intersection is empty, and the counts add up. Any one alone would miss
        a stratum appearing twice or not at all.
        """
        self.assertEqual(set(SYNTHESISABLE) | set(TUNER_REQUIRED),
                         set(STRATUM_KEYS))
        self.assertEqual(set(SYNTHESISABLE) & set(TUNER_REQUIRED), set())
        self.assertEqual(len(SYNTHESISABLE) + len(TUNER_REQUIRED),
                         len(STRATUM_KEYS))

    def test_every_synthesisable_stratum_has_a_synthesiser(self):
        self.assertEqual(sorted(corpus_module._SYNTHESISERS),
                         sorted(SYNTHESISABLE))

    def test_no_tuner_stratum_has_a_synthesiser(self):
        for key in TUNER_REQUIRED:
            self.assertNotIn(key, corpus_module._SYNTHESISERS)


class RefusalTests(unittest.TestCase):
    """Absent, and named. Never filled."""

    def test_a_tuner_stratum_refuses_by_name(self):
        for key in TUNER_REQUIRED:
            with self.subTest(stratum=key):
                with self.assertRaises(CorpusRefused) as caught:
                    generate_window(key, 0, config())
                self.assertEqual(caught.exception.code,
                                 STRATUM_NOT_SYNTHESISABLE)
                self.assertIn(key, str(caught.exception))

    def test_an_undeclared_stratum_refuses(self):
        for key in ("NOT_A_STRATUM", "", "thermal_no_input"):
            with self.subTest(stratum=key):
                with self.assertRaises(CorpusRefused) as caught:
                    generate_window(key, 0, config())
                self.assertEqual(caught.exception.code, STRATUM_NOT_DECLARED)

    def test_an_unusable_configuration_refuses(self):
        for kw in ({"seed": -1}, {"seed": True}, {"seed": 2 ** 32},
                   {"window_samples": 512}, {"sample_rate_hz": 0.0},
                   {"noise_power": 0.0}):
            with self.subTest(**kw):
                fields = dict(seed=3, window_samples=SMALL)
                fields.update(kw)
                with self.assertRaises(CorpusRefused) as caught:
                    GeneratorConfig(**fields)
                self.assertEqual(caught.exception.code,
                                 GENERATOR_CONFIG_REFUSED)

    def test_a_negative_index_refuses(self):
        with self.assertRaises(CorpusRefused):
            generate_window("THERMAL_NO_INPUT", -1, config())


class ReproducibilityTests(unittest.TestCase):
    """A synthetic window whose configuration cannot be recovered is
    indistinguishable from a captured one that lost its provenance."""

    def test_the_same_seed_and_index_give_the_same_samples(self):
        one = generate_window("THERMAL_NO_INPUT", 5, config(seed=9))
        other = generate_window("THERMAL_NO_INPUT", 5, config(seed=9))
        np.testing.assert_array_equal(one.samples, other.samples)
        self.assertEqual(one.provenance()["config_identity"],
                         other.provenance()["config_identity"])

    def test_a_different_seed_gives_different_samples(self):
        one = generate_window("THERMAL_NO_INPUT", 5, config(seed=9))
        other = generate_window("THERMAL_NO_INPUT", 5, config(seed=10))
        self.assertFalse(np.array_equal(one.samples, other.samples))
        self.assertNotEqual(one.provenance()["config_identity"],
                            other.provenance()["config_identity"])

    def test_a_window_does_not_depend_on_how_many_preceded_it(self):
        """Derived streams, not a sequential one.

        A generator whose output depends on how much of it you consumed is not
        reproducible from a seed: window 40 would differ depending on whether
        anyone asked for the first 39.
        """
        direct = generate_window("AM", 40, config(seed=4))
        consumed = list(generate_stratum("AM", config(seed=4),
                                         plan={"AM": 41}))[40]
        np.testing.assert_array_equal(direct.samples, consumed.samples)

    def test_the_stream_itself_depends_on_the_stratum(self):
        """Tested at `_rng`, not through the synthesisers.

        Two strata produce different samples even from one stream, because they
        are different functions — so comparing their outputs cannot show the
        stream was derived per stratum. Drawing from the streams directly can.
        """
        settings = config(seed=6)
        first = corpus_module._rng(settings, "THERMAL_NO_INPUT", 0).normal(size=8)
        second = corpus_module._rng(settings, "AM", 0).normal(size=8)
        same = corpus_module._rng(settings, "THERMAL_NO_INPUT", 0).normal(size=8)
        self.assertFalse(np.array_equal(first, second))
        np.testing.assert_array_equal(first, same)

    def test_the_stream_depends_on_the_index(self):
        settings = config(seed=6)
        self.assertFalse(np.array_equal(
            corpus_module._rng(settings, "AM", 0).normal(size=8),
            corpus_module._rng(settings, "AM", 1).normal(size=8)))

    def test_every_window_carries_its_seed_and_configuration(self):
        window = generate_window("AM", 2, config(seed=12))
        provenance = window.provenance()
        self.assertEqual(provenance["seed"], 12)
        self.assertEqual(provenance["config_identity"],
                         config(seed=12).identity())
        self.assertEqual(provenance["generator_revision"],
                         corpus_module.GENERATOR_REVISION)
        self.assertEqual(provenance["stratum"], "AM")
        self.assertEqual(provenance["index"], 2)

    def test_the_configuration_identity_moves_with_every_field(self):
        base = GeneratorConfig(seed=1, window_samples=SMALL)
        for kw in ({"seed": 2}, {"window_samples": SMALL * 2},
                   {"sample_rate_hz": 1_024_000.0}, {"noise_power": 2.0}):
            with self.subTest(**kw):
                fields = dict(seed=1, window_samples=SMALL)
                fields.update(kw)
                self.assertNotEqual(GeneratorConfig(**fields).identity(),
                                    base.identity())


class LabellingTests(unittest.TestCase):
    """Every record identifies itself as synthetic."""

    def test_every_window_is_labelled_synthetic(self):
        for stratum in SYNTHESISABLE:
            with self.subTest(stratum=stratum):
                window = generate_window(stratum, 0, config())
                self.assertEqual(window.source, SYNTHETIC)
                self.assertEqual(window.provenance()["source"], SYNTHETIC)

    def test_nothing_in_the_module_produces_a_captured_window(self):
        """AST: no call anywhere passes a `source`.

        Stronger than the earlier version, which checked that every `source=`
        keyword was `SYNTHETIC`. Since the field became `init=False` there are
        no such keywords at all, and a test asserting a property of an empty
        set passes for the wrong reason.
        """
        with open(corpus_module.__file__) as handle:
            tree = ast.parse(handle.read())
        supplied = [node for node in ast.walk(tree)
                    if isinstance(node, ast.keyword) and node.arg == "source"]
        self.assertEqual(supplied, [])
        self.assertEqual(CAPTURED, "CAPTURED")
        self.assertIn(CAPTURED, corpus_module.WINDOW_SOURCES)

    def test_the_provenance_says_it_is_not_a_substitute(self):
        note = generate_window("AM", 0, config()).provenance()["note"]
        self.assertIn("SYNTHETIC", note)
        self.assertIn("NEVER A SUBSTITUTE", note)


class PlanTests(unittest.TestCase):
    """Counts come from the declared plan, never from a constant here."""

    def test_the_declared_plan_is_the_manifest(self):
        self.assertEqual(declared_plan(),
                         {s.key: s.minimum_windows for s in STRATA})

    def test_the_module_declares_no_window_count_of_its_own(self):
        """AST: no integer literal in this module equals a stratum minimum or
        the corpus target. A transcribed count is the second answer §5.19
        refuses."""
        with open(corpus_module.__file__) as handle:
            tree = ast.parse(handle.read())
        forbidden = {s.minimum_windows for s in STRATA}
        forbidden.add(sum(s.minimum_windows for s in STRATA))
        literals = {node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, int)}
        self.assertEqual(literals & forbidden, set())

    def test_an_injected_plan_is_honoured(self):
        windows = list(generate_stratum("THERMAL_NO_INPUT", config(),
                                        plan={"THERMAL_NO_INPUT": 3}))
        self.assertEqual(len(windows), 3)
        self.assertEqual([w.index for w in windows], [0, 1, 2])

    def test_a_stratum_outside_the_plan_refuses(self):
        with self.assertRaises(CorpusRefused) as caught:
            list(generate_stratum("AM", config(), plan={"THERMAL_NO_INPUT": 1}))
        self.assertEqual(caught.exception.code, STRATUM_NOT_DECLARED)

    def test_an_undeclared_key_in_a_plan_refuses(self):
        with self.assertRaises(CorpusRefused) as caught:
            plan_state({"NOT_A_STRATUM": 1})
        self.assertEqual(caught.exception.code, STRATUM_NOT_DECLARED)


class PlanStateTests(unittest.TestCase):
    """The shortfall is reported, not carried in a reader's memory."""

    def test_the_tuner_strata_are_reported_awaiting_capture(self):
        state = plan_state(small_plan())
        self.assertEqual(sorted(state["awaiting_capture"]),
                         sorted(TUNER_REQUIRED))
        for entry in state["strata"]:
            expected = (STRATUM_AWAITING_CAPTURE
                        if entry["stratum"] in TUNER_REQUIRED
                        else SYNTHESIS_PLANNED)
            self.assertEqual(entry["state"], expected, entry["stratum"])

    def test_a_tuner_stratum_keeps_its_full_count(self):
        """Not zeroed, not omitted, not reassigned to a neighbour.

        The presence assertion is the load-bearing one: an earlier version
        looped over the entries and checked only those in TUNER_REQUIRED, so
        dropping them entirely made it pass vacuously — which is exactly the
        omission it exists to catch.
        """
        state = plan_state()
        present = {entry["stratum"] for entry in state["strata"]}
        for key in TUNER_REQUIRED:
            self.assertIn(key, present, key)
        for entry in state["strata"]:
            if entry["stratum"] in TUNER_REQUIRED:
                self.assertEqual(entry["windows_required"],
                                 declared_plan()[entry["stratum"]])
                self.assertEqual(entry["source"], CAPTURED)

    def test_the_corpus_is_incomplete_and_says_so(self):
        state = plan_state(small_plan())
        self.assertFalse(state["complete"])
        self.assertEqual(state["completion_state"],
                         CORPUS_INCOMPLETE_AWAITING_CAPTURE)

    def test_no_freeze_while_a_stratum_awaits_capture(self):
        state = plan_state(small_plan())
        self.assertFalse(state["may_freeze"])
        self.assertIn("PromotionCorpusLock", state["freeze_note"])

    def test_the_counts_partition_the_plan(self):
        plan = small_plan(count=5)
        state = plan_state(plan)
        self.assertEqual(
            state["windows_synthesis_planned"] + state["windows_awaiting_capture"],
            sum(plan.values()))

    def test_a_plan_of_only_synthesisable_strata_is_still_incomplete(self):
        """The gate wants twelve. Nine is nine, however complete they are."""
        state = plan_state({key: 2 for key in SYNTHESISABLE})
        self.assertFalse(state["complete"])
        self.assertEqual(state["completion_state"],
                         CORPUS_INCOMPLETE_STRATA_MISSING)
        self.assertEqual(sorted(state["missing_from_plan"]),
                         sorted(TUNER_REQUIRED))


class CorpusGenerationTests(unittest.TestCase):
    """The tuner strata are skipped, never attempted, never substituted."""

    def test_the_corpus_contains_only_synthesisable_strata(self):
        windows = list(generate_synthetic_corpus(config(), plan=small_plan(1)))
        self.assertEqual(sorted({w.stratum for w in windows}),
                         sorted(SYNTHESISABLE))
        self.assertEqual(len(windows), len(SYNTHESISABLE))

    def test_no_tuner_stratum_appears_under_another_name(self):
        windows = list(generate_synthetic_corpus(config(), plan=small_plan(2)))
        for window in windows:
            self.assertNotIn(window.stratum, TUNER_REQUIRED)

    def test_the_corpus_is_lazy(self):
        """A generator, so a plan of 66 732 costs nothing until drawn."""
        self.assertFalse(isinstance(
            generate_synthetic_corpus(config(), plan=small_plan()), list))
        first = next(iter(generate_synthetic_corpus(config(), plan=small_plan())))
        self.assertIsInstance(first, SyntheticWindow)


class BoundaryTests(unittest.TestCase):
    """§5.19's four refusals, over the source."""

    def _tree(self):
        with open(corpus_module.__file__) as handle:
            return ast.parse(handle.read())

    def test_the_module_acquires_nothing(self):
        tree = self._tree()
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("socket", "threading", "subprocess", "rf_bridge",
                          "rf_iq_ring", "rf_iq_retention", "usb", "asyncio",
                          "requests", "urllib", "http"):
            self.assertNotIn(forbidden, imported, forbidden)

    def test_the_module_writes_nothing(self):
        """Not even synthetic windows. A synthetic window is regenerated."""
        tree = self._tree()
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        attributes = {node.attr for node in ast.walk(tree)
                      if isinstance(node, ast.Attribute)}
        self.assertNotIn("open", called)
        for forbidden in ("write", "writelines", "mkdir", "makedirs", "dump",
                          "savez", "tofile", "rename", "unlink"):
            self.assertNotIn(forbidden, attributes, forbidden)

    def test_there_is_no_generic_writer(self):
        """One entry point taking both sources is the hole §13k L.1 refused."""
        tree = self._tree()
        public = [node.name for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef)
                  and not node.name.startswith("_")]
        for name in ("write", "emit", "record", "ingest", "add_window",
                     "write_window", "store"):
            self.assertNotIn(name, public, name)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                arguments = {a.arg for a in node.args.args + node.args.kwonlyargs}
                self.assertNotIn("payload", arguments, node.name)
                self.assertNotIn("source", arguments, node.name)

    def test_the_status_declares_what_it_does_not_do(self):
        declared = status()
        self.assertFalse(declared["acquires"])
        self.assertFalse(declared["persists"])
        self.assertFalse(declared["opens_a_socket"])
        self.assertFalse(declared["has_generic_writer"])
        self.assertFalse(declared["substitutes_for_capture"])
        self.assertEqual(declared["produces"], SYNTHETIC)
        self.assertEqual(sorted(declared["tuner_required_strata"]),
                         sorted(TUNER_REQUIRED))

    def test_the_plan_state_declares_the_same(self):
        state = plan_state(small_plan())
        self.assertFalse(state["persists"])
        self.assertFalse(state["acquires"])
        self.assertIn("5.20", state["persistence_note"])


class NullQualityTests(unittest.TestCase):
    """A null that no detector could ever call DIGITAL bounds nothing.

    These do not validate anything -- the corpus is 5 561 windows per stratum
    and this is four. They check the generators produce the *kind* of signal
    each stratum names, so a later zero-failure run means the detector rejected
    something rather than that the harness produced nothing to reject.
    """

    def _envelope_cv(self, stratum, index=0):
        window = generate_window(stratum, index,
                                 GeneratorConfig(seed=5, window_samples=65536))
        envelope = np.abs(window.samples) ** 2
        return float(envelope.std() / envelope.mean())

    def test_the_constant_envelope_stratum_is_actually_constant(self):
        """It must reach the detector's CONSTANT_ENVELOPE path, which is
        NOT_ATTEMPTED -- a test that could not run has no negative to report."""
        import rf_symbol_clock as detector
        self.assertLess(self._envelope_cv("CONSTANT_ENVELOPE_DIGITAL"),
                        detector.CONSTANT_ENVELOPE_CV)

    def test_the_varying_strata_actually_vary(self):
        """An envelope that does not vary would be rejected for the wrong
        reason, and the stratum would bound nothing."""
        import rf_symbol_clock as detector
        for stratum in SYNTHESISABLE:
            if stratum == "CONSTANT_ENVELOPE_DIGITAL":
                continue
            with self.subTest(stratum=stratum):
                self.assertGreater(self._envelope_cv(stratum),
                                   detector.CONSTANT_ENVELOPE_CV)

    def test_no_synthesiser_plants_the_line_it_is_meant_to_be_a_null_for(self):
        """The modulating noise is band-limited and aperiodic on purpose: a
        periodic modulator would put a symbol-rate line into a null."""
        import rf_symbol_clock as detector
        threshold = detector.threshold_declaration()["threshold"]
        for stratum in SYNTHESISABLE:
            if stratum == "CONSTANT_ENVELOPE_DIGITAL":
                continue
            with self.subTest(stratum=stratum):
                worst = 0.0
                for index in range(4):
                    window = generate_window(
                        stratum, index,
                        GeneratorConfig(seed=13, window_samples=65536))
                    statistic, *_ = detector.squared_envelope_statistic(
                        window.samples, window.sample_rate_hz)
                    if statistic is not None:
                        worst = max(worst, statistic)
                self.assertLess(worst, threshold, f"{stratum}: {worst}")


class NotACorpusTests(unittest.TestCase):
    """What this module is, said out loud."""

    def test_nothing_here_claims_a_corpus_exists(self):
        self.assertFalse(plan_state()["complete"])
        self.assertFalse(plan_state()["may_freeze"])

    def test_the_declared_plan_is_far_larger_than_anything_tested_here(self):
        """Every test injects a small plan; the real one is 66 732."""
        self.assertGreater(sum(declared_plan().values()), 60_000)


class PlanIsNotACorpusTests(unittest.TestCase):
    """§5.19 correction: a capability is not evidence that windows exist."""

    def test_the_planned_state_does_not_claim_synthesis_happened(self):
        """`SYNTHESIS_PLANNED`, not `STRATUM_SYNTHESISED`.

        `plan_state` generates nothing. An earlier name said the windows had
        been made, which would have been a claim about 50 049 windows that do
        not exist.
        """
        self.assertEqual(SYNTHESIS_PLANNED, "SYNTHESIS_PLANNED")
        self.assertNotIn("SYNTHESISED", SYNTHESIS_PLANNED)
        blob = str(plan_state(small_plan()))
        self.assertNotIn("STRATUM_SYNTHESISED", blob)

    def test_plan_state_generates_nothing(self):
        """AST: it calls no synthesiser and no generator."""
        with open(corpus_module.__file__) as handle:
            tree = ast.parse(handle.read())
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "plan_state")
        called = {node.func.id for node in ast.walk(function)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        for name in ("generate_window", "generate_stratum",
                     "generate_synthetic_corpus"):
            self.assertNotIn(name, called)

    def test_the_declared_plan_can_never_be_complete_here(self):
        """While any stratum needs a receiver, this module cannot finish one."""
        state = plan_state()
        self.assertFalse(state["complete"])
        self.assertEqual(state["completion_state"],
                         CORPUS_INCOMPLETE_AWAITING_CAPTURE)
        self.assertEqual(state["missing_from_plan"], [])

    def test_a_plan_missing_a_synthesisable_stratum_is_incomplete(self):
        """Not only the tuner ones: any absent key blocks completion."""
        plan = {key: 2 for key in STRATUM_KEYS if key != "AM"}
        state = plan_state(plan)
        self.assertFalse(state["complete"])
        self.assertEqual(state["completion_state"],
                         CORPUS_INCOMPLETE_STRATA_MISSING)
        self.assertEqual(state["missing_from_plan"], ["AM"])

    def test_completion_needs_both_conditions(self):
        """Every declared stratum present, and none awaiting capture."""
        everything = plan_state(small_plan())
        self.assertEqual(everything["missing_from_plan"], [])
        self.assertTrue(everything["awaiting_capture"])
        self.assertFalse(everything["complete"])

        subset = plan_state({key: 2 for key in SYNTHESISABLE})
        self.assertTrue(subset["missing_from_plan"])
        self.assertFalse(subset["awaiting_capture"])
        self.assertFalse(subset["complete"])

    def test_the_note_says_a_plan_is_not_a_corpus(self):
        note = plan_state(small_plan())["completion_note"]
        self.assertIn("A PLAN IS NOT A CORPUS", note)


class RegenerabilityTests(unittest.TestCase):
    """A digest commits to a configuration; it does not reveal one."""

    def test_the_provenance_carries_the_whole_generator_declaration(self):
        window = generate_window("AM", 7, config(seed=21))
        provenance = window.provenance()
        for field_name in ("schema", "generator_revision", "source", "stratum",
                           "index", "seed", "sample_rate_hz", "window_samples",
                           "noise_power", "config_identity"):
            self.assertIn(field_name, provenance, field_name)
        self.assertEqual(provenance["window_samples"], SMALL)
        self.assertEqual(provenance["noise_power"], 1.0)

    def test_a_window_is_rebuilt_from_its_provenance_alone(self):
        """The property, not the claim: hand back the record, get the samples."""
        for stratum in SYNTHESISABLE:
            with self.subTest(stratum=stratum):
                original = generate_window(stratum, 3, config(seed=17))
                rebuilt = regenerate(original.provenance())
                np.testing.assert_array_equal(original.samples, rebuilt.samples)
                self.assertEqual(rebuilt.provenance(), original.provenance())

    def test_an_edited_declaration_refuses_against_its_digest(self):
        provenance = dict(generate_window("AM", 1, config(seed=8)).provenance())
        provenance["noise_power"] = 2.0
        with self.assertRaises(CorpusRefused) as caught:
            regenerate(provenance)
        self.assertEqual(caught.exception.code, PROVENANCE_DIGEST_MISMATCH)

    def test_an_edited_digest_refuses_against_its_declaration(self):
        provenance = dict(generate_window("AM", 1, config(seed=8)).provenance())
        provenance["config_identity"] = "blake2s:" + "0" * 32
        with self.assertRaises(CorpusRefused) as caught:
            regenerate(provenance)
        self.assertEqual(caught.exception.code, PROVENANCE_DIGEST_MISMATCH)

    def test_an_incomplete_declaration_refuses(self):
        full = generate_window("AM", 1, config(seed=8)).provenance()
        for field_name in ("stratum", "index", "seed", "sample_rate_hz",
                           "window_samples", "noise_power", "config_identity"):
            with self.subTest(missing=field_name):
                short = {k: v for k, v in full.items() if k != field_name}
                with self.assertRaises(CorpusRefused):
                    regenerate(short)

    def test_the_digest_alone_would_not_be_enough(self):
        """Stated as a property of the record: the declaration is recoverable.

        A record carrying only `config_identity` could be *checked* by someone
        who already had the configuration and *regenerated* by nobody.
        """
        provenance = generate_window("AM", 1, config(seed=8)).provenance()
        rebuilt = GeneratorConfig(seed=provenance["seed"],
                                  sample_rate_hz=provenance["sample_rate_hz"],
                                  window_samples=provenance["window_samples"],
                                  noise_power=provenance["noise_power"])
        self.assertEqual(rebuilt.identity(), provenance["config_identity"])


class SourceAuthorityTests(unittest.TestCase):
    """A caller has no authority to say where a window came from."""

    def test_a_caller_cannot_supply_the_source(self):
        with self.assertRaises(TypeError):
            SyntheticWindow(stratum="AM", index=0, samples=np.zeros(4),
                            seed=1, sample_rate_hz=1_000.0, window_samples=1024,
                            noise_power=1.0, source=CAPTURED)

    def test_a_caller_cannot_supply_the_generator_revision(self):
        with self.assertRaises(TypeError):
            SyntheticWindow(stratum="AM", index=0, samples=np.zeros(4),
                            seed=1, sample_rate_hz=1_000.0, window_samples=1024,
                            noise_power=1.0, generator_revision="forged.v9")

    def test_a_caller_cannot_supply_the_schema(self):
        with self.assertRaises(TypeError):
            SyntheticWindow(stratum="AM", index=0, samples=np.zeros(4),
                            seed=1, sample_rate_hz=1_000.0, window_samples=1024,
                            noise_power=1.0, schema="something.else.v1")

    def test_the_sealed_fields_are_declared_non_init(self):
        import dataclasses
        fields = {f.name: f for f in dataclasses.fields(SyntheticWindow)}
        for name, expected in (("source", SYNTHETIC),
                               ("generator_revision",
                                corpus_module.GENERATOR_REVISION),
                               ("schema", corpus_module.SCHEMA)):
            with self.subTest(field=name):
                self.assertFalse(fields[name].init, name)
                self.assertEqual(fields[name].default, expected)

    def test_a_constructed_window_is_synthetic_whatever_was_intended(self):
        window = SyntheticWindow(stratum="AM", index=0, samples=np.zeros(4),
                                 seed=1, sample_rate_hz=1_000.0,
                                 window_samples=1024, noise_power=1.0)
        self.assertEqual(window.source, SYNTHETIC)
        self.assertEqual(window.generator_revision,
                         corpus_module.GENERATOR_REVISION)

    def test_a_frozen_window_cannot_be_relabelled_after_construction(self):
        window = generate_window("AM", 0, config())
        with self.assertRaises(Exception):
            window.source = CAPTURED
        self.assertEqual(window.source, SYNTHETIC)
