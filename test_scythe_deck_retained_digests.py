#!/usr/bin/env python3
"""Cantilever decks keep the identities the solver evidence was produced from.

test_scythe_solver_verification_deck.py proves the generator reproduces the
COMMITTED decks. That cannot notice a change made to the generator and the
decks together: the generator and the files still agree, and the decks the
recorded solver results came from are gone. (Demonstrated by changing density
7850 -> 7800 and refining the medium mesh 20 -> 24, regenerating, and running
that test: both passed.) This module pins the decks to digests that were
recorded or established independently of the generator.

Authority for each digest, in the order it was applied:

  HISTORICAL_MANIFEST   rerun_sha256sums_2026-10-08.txt, the manifest retained
                        with the solver rerun
  HISTORICAL_AMENDMENT  estimator_amendment_2026-10-08.json, which names the
                        deck file it was written against
  HISTORICAL_BY_IDENTITY  no entry of its own, but byte-identical to files the
                        manifest attests (the engine input is the same for every
                        mesh), and corroborated by the solver run directories
  NEW_BASELINE          no digest was recorded when the artifact was produced.
                        Established 2026-10-11 from the committed blob, and
                        corroborated by a byte-identical copy in a solver run
                        directory. A regression anchor, not a historical
                        attestation.

A literal in a test is an anchor for review, not protection against someone
changing the test and the deck together; it makes that change visible in one
more place. Where a digest here disagrees with historical evidence the right
move is to stop and find out why, not to update the literal.

Nothing here modifies a deck, the generator, the analytical reference or any
protocol. The generator runs only in a temporary directory, as an unmodified
copy of itself: it writes beside its own file, so no source patching is needed
and a rewording of its output line cannot break the test.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DECK_DIR = os.path.join(REPO_ROOT, "mechanics", "openradioss", "decks",
                        "cantilever_eb")
GENERATOR = os.path.join(DECK_DIR, "generate_deck.py")
MANIFEST = os.path.join(DECK_DIR, "rerun_sha256sums_2026-10-08.txt")
AMENDMENT = os.path.join(DECK_DIR, "estimator_amendment_2026-10-08.json")
PROTOCOL_V2 = os.path.join(DECK_DIR, "dynamic_protocol_2026-10-08-v2.json")

HISTORICAL_MANIFEST = "HISTORICAL_MANIFEST"
HISTORICAL_AMENDMENT = "HISTORICAL_AMENDMENT"
HISTORICAL_BY_IDENTITY = "HISTORICAL_BY_IDENTITY"
NEW_BASELINE = "NEW_BASELINE"

_MANIFEST_SOURCE = "rerun_sha256sums_2026-10-08.txt"

# The six static decks the generator emits.
STATIC_DECKS = {
    "cantilever_coarse_0000.rad": dict(
        sha256="22036f430ccc786fcddf92539618b1fb8affdc73dced51169788aca502bb6604",
        authority=HISTORICAL_AMENDMENT,
        source="estimator_amendment_2026-10-08.json artifacts.deck_0000, naming "
               "/var/tmp/scythe-sweep/cantilever-rerun-20261007/deck/"
               "cantilever_coarse_0000.rad"),
    "cantilever_coarse_0001.rad": dict(
        sha256="94894b74ecfb825cdb41c32f2f2cc803d440b121914d1cf3312c270134c49402",
        authority=HISTORICAL_BY_IDENTITY,
        source="equal to the manifest-attested medium and fine engine inputs; "
               "7 identical copies across the solver run directories"),
    "cantilever_medium_0000.rad": dict(
        sha256="5807e3a9830743d85ecb0b8d9781a1a578214ac5ea13064bd7f048dbeab04bd4",
        authority=HISTORICAL_MANIFEST,
        source=_MANIFEST_SOURCE + " run_medium/cantilever_medium_0000.rad"),
    "cantilever_medium_0001.rad": dict(
        sha256="94894b74ecfb825cdb41c32f2f2cc803d440b121914d1cf3312c270134c49402",
        authority=HISTORICAL_MANIFEST,
        source=_MANIFEST_SOURCE + " run_medium/cantilever_medium_0001.rad"),
    "cantilever_fine_0000.rad": dict(
        sha256="a8439555771dc27c7d3d8166df15796c23a20442a629fe5fef4d09cbf9c6f02f",
        authority=HISTORICAL_MANIFEST,
        source=_MANIFEST_SOURCE + " run_fine/cantilever_fine_0000.rad"),
    "cantilever_fine_0001.rad": dict(
        sha256="94894b74ecfb825cdb41c32f2f2cc803d440b121914d1cf3312c270134c49402",
        authority=HISTORICAL_MANIFEST,
        source=_MANIFEST_SOURCE + " run_fine/cantilever_fine_0001.rad"),
}

# analytical_reference.json is frozen (2026-10-05) but no digest was ever
# recorded for it.
ANALYTICAL_REFERENCE = dict(
    name="analytical_reference.json",
    sha256="4cde0c1f2a591117b94b0b7795ec88fa60e9328611b0a1c130a6f20828bd2005",
    authority=NEW_BASELINE,
    established="2026-10-11",
    git_blob="c0d78ec62e5babdf4f300489ec51148f1beea331",
    source_commit="398eeb234433b3618a1256e8dd321f778aff2ae3",
    corroborated_by="/var/tmp/scythe-sweep/cantilever-mf-20261008/deck/"
                    "analytical_reference.json (byte-identical)")

# The dynamic decks are a hand-edit of the fine decks (commit 55c7dde8), not
# generator output, and no digest was recorded for them.
DYNAMIC_DECKS = {
    "cantilever_dynamic_0000.rad": dict(
        sha256="e4f8535c71a7cb8dfa31f10770de93551d44acab315697257e8486ddf93c0c39",
        authority=NEW_BASELINE, established="2026-10-11",
        git_blob="2cc3ada09cb5a81b9ea4c8fe1f369cf43a3b80e9",
        source_commit="55c7dde8e0a5ce2edcc42c8ecde6470898b87095",
        corroborated_by="/var/tmp/scythe-sweep/cantilever-dynamic-20261009/"
                        "{deck,run}/cantilever_dynamic_0000.rad (byte-identical)"),
    "cantilever_dynamic_0001.rad": dict(
        sha256="2bcee7d089f858200dba7dc5563c8203d7f84bb9dc4f510df0167fd9ef691cc4",
        authority=NEW_BASELINE, established="2026-10-11",
        git_blob="7fe03ac36a37d911c829865db49c68becc337564",
        source_commit="55c7dde8e0a5ce2edcc42c8ecde6470898b87095",
        corroborated_by="/var/tmp/scythe-sweep/cantilever-dynamic-20261009/"
                        "{deck,run}/cantilever_dynamic_0001.rad (byte-identical)"),
}

# What distinguishes the dynamic runs from the static fine run, in solver terms.
FINE_CURVE = [(0.0, 0.0), (0.1, -1.0), (10.0, -1.0)]
DYNAMIC_CURVE = [(0.0, 0.0), (0.1, -1.0), (0.5, -1.0), (0.501, 0.0), (2.7, 0.0)]
FINE_END_TIME_S = 0.5
RAMP_END_S = 0.1
RELEASE_START_S = 0.5       # load starts to come off
RELEASE_COMPLETE_S = 0.501  # load is zero from here (protocol: "deck-specified")
END_TIME_S = 2.7            # engine stops here


def sha256_of(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def read_lines(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read().splitlines()


def funct_blocks(lines):
    """{function id: (title, [(x, y), ...], first line index, last line index)}
    for every /FUNCT card. Points are parsed to numbers, not matched as text."""
    blocks = {}
    for index, line in enumerate(lines):
        if not line.startswith("/FUNCT/"):
            continue
        function_id = int(line.split("/")[2])
        if function_id in blocks:
            raise AssertionError(f"/FUNCT/{function_id} appears twice")
        points = []
        cursor = index + 2
        while cursor < len(lines) and not lines[cursor].startswith(("#", "/")):
            x, y = lines[cursor].split()
            points.append((float(x), float(y)))
            cursor += 1
        blocks[function_id] = (lines[index + 1], points, index, cursor - 1)
    return blocks


def engine_end_time(lines):
    """The engine stop time: the value on the line after the one /RUN card."""
    runs = [i for i, line in enumerate(lines) if line.startswith("/RUN/")]
    if len(runs) != 1:
        raise AssertionError(f"expected one /RUN card, found {len(runs)}")
    return float(lines[runs[0] + 1].split()[0]), runs[0] + 1


def without_lines(lines, first, last):
    return lines[:first] + lines[last + 1:]


def snapshot(directory):
    """(digest, mtime_ns) of every regular file directly in the directory. The
    mtime is there because a generator that wrote the committed bytes back over
    themselves would change nothing else."""
    out = {}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            out[name] = (sha256_of(path), os.stat(path).st_mtime_ns)
    return out


class GeneratorRun(unittest.TestCase):
    """Runs the generator once, as an unmodified copy of itself in a temporary
    directory. It writes to the directory it lives in, so its output lands
    there."""

    GENERATOR_COPY = "generate_deck.py"

    @classmethod
    def setUpClass(cls):
        cls.before = snapshot(DECK_DIR)
        cls.out = tempfile.mkdtemp(prefix="scythe-deck-digests-")
        cls.addClassCleanup(shutil.rmtree, cls.out, ignore_errors=True)
        cls.copy = os.path.join(cls.out, cls.GENERATOR_COPY)
        shutil.copyfile(GENERATOR, cls.copy)
        proc = subprocess.run([sys.executable, cls.copy], cwd=cls.out,
                              capture_output=True, text=True, timeout=120,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        if proc.returncode != 0:
            raise RuntimeError(f"generator failed:\n{proc.stdout}\n{proc.stderr}")
        cls.after = snapshot(DECK_DIR)


class RedirectionHardeningTests(GeneratorRun):
    """If the generator ever stopped writing beside itself, it would overwrite
    the committed decks and a comparison would then be a file against itself.
    These fail loudly in that case."""

    def test_the_generator_ran_unmodified(self):
        self.assertEqual(sha256_of(self.copy), sha256_of(GENERATOR))

    def test_output_landed_in_the_temporary_directory(self):
        expected = (set(STATIC_DECKS) | {ANALYTICAL_REFERENCE["name"]}
                    | {self.GENERATOR_COPY})
        self.assertEqual(set(os.listdir(self.out)), expected)

    def test_generating_left_the_committed_directory_untouched(self):
        """Same bytes and same modification times: a rewrite of identical
        bytes would show in the latter."""
        self.assertEqual(self.after, self.before)

    def test_generator_did_not_emit_the_dynamic_decks(self):
        """They are a hand-edit of the fine decks; if a generator starts
        producing them this module needs a new derivation test."""
        self.assertFalse(set(DYNAMIC_DECKS) & set(os.listdir(self.out)))


class StaticDeckDigestTests(GeneratorRun):

    def test_committed_decks_match_the_frozen_digests(self):
        for name, record in STATIC_DECKS.items():
            with self.subTest(name, authority=record["authority"]):
                self.assertEqual(sha256_of(os.path.join(DECK_DIR, name)),
                                 record["sha256"])

    def test_generated_decks_match_the_frozen_digests(self):
        """The coordinated-change case: a generator edited together with the
        decks still has to reproduce the digests the solver evidence used."""
        for name, record in STATIC_DECKS.items():
            with self.subTest(name, authority=record["authority"]):
                self.assertEqual(sha256_of(os.path.join(self.out, name)),
                                 record["sha256"])

    def test_every_static_deck_has_a_named_authority(self):
        for name, record in STATIC_DECKS.items():
            with self.subTest(name):
                self.assertRegex(record["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(record["source"])
                self.assertIn(record["authority"],
                              {HISTORICAL_MANIFEST, HISTORICAL_AMENDMENT,
                               HISTORICAL_BY_IDENTITY})

    def test_literals_agree_with_the_in_repo_manifest(self):
        manifest = {}
        with open(MANIFEST, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    digest, path = line.split(None, 1)
                    manifest[os.path.basename(path.strip())] = digest
        attested = {name for name, record in STATIC_DECKS.items()
                    if record["authority"] == HISTORICAL_MANIFEST}
        self.assertEqual(attested,
                         {"cantilever_medium_0000.rad", "cantilever_medium_0001.rad",
                          "cantilever_fine_0000.rad", "cantilever_fine_0001.rad"})
        for name in attested:
            with self.subTest(name):
                self.assertEqual(manifest.get(name), STATIC_DECKS[name]["sha256"])

    def test_literal_agrees_with_the_estimator_amendment(self):
        with open(AMENDMENT, encoding="utf-8") as handle:
            amendment = json.load(handle)
        self.assertEqual(amendment["artifacts"]["deck_0000"]["sha256"],
                         STATIC_DECKS["cantilever_coarse_0000.rad"]["sha256"])

    def test_the_engine_input_is_one_file_for_every_mesh(self):
        """The basis of HISTORICAL_BY_IDENTITY for the coarse engine deck."""
        digests = {STATIC_DECKS[f"cantilever_{mesh}_0001.rad"]["sha256"]
                   for mesh in ("coarse", "medium", "fine")}
        self.assertEqual(len(digests), 1)


class AnalyticalReferenceTests(GeneratorRun):

    def test_generated_reference_equals_the_committed_reference(self):
        name = ANALYTICAL_REFERENCE["name"]
        with open(os.path.join(DECK_DIR, name), "rb") as committed, \
                open(os.path.join(self.out, name), "rb") as generated:
            self.assertEqual(committed.read(), generated.read())

    def test_committed_reference_matches_the_new_baseline(self):
        """Labelled NEW_BASELINE: no digest was recorded when the reference was
        frozen on 2026-10-05, so this one is established, not attested."""
        self.assertEqual(ANALYTICAL_REFERENCE["authority"], NEW_BASELINE)
        self.assertEqual(
            sha256_of(os.path.join(DECK_DIR, ANALYTICAL_REFERENCE["name"])),
            ANALYTICAL_REFERENCE["sha256"])

    def test_generated_reference_matches_the_new_baseline(self):
        self.assertEqual(
            sha256_of(os.path.join(self.out, ANALYTICAL_REFERENCE["name"])),
            ANALYTICAL_REFERENCE["sha256"])

    def test_the_baseline_record_is_complete(self):
        for key in ("sha256", "established", "git_blob", "source_commit",
                    "corroborated_by"):
            self.assertTrue(ANALYTICAL_REFERENCE[key], key)
        self.assertRegex(ANALYTICAL_REFERENCE["sha256"], r"^[0-9a-f]{64}$")


class DynamicDeckTests(unittest.TestCase):
    """The dynamic decks are the fine decks with a documented edit: the load
    comes off between 0.5 s and 0.501 s, and the engine runs to 2.7 s."""

    @classmethod
    def setUpClass(cls):
        cls.fine_deck = read_lines(os.path.join(DECK_DIR, "cantilever_fine_0000.rad"))
        cls.dyn_deck = read_lines(os.path.join(DECK_DIR, "cantilever_dynamic_0000.rad"))
        cls.fine_engine = read_lines(os.path.join(DECK_DIR, "cantilever_fine_0001.rad"))
        cls.dyn_engine = read_lines(os.path.join(DECK_DIR, "cantilever_dynamic_0001.rad"))

    def test_dynamic_digests_match_the_new_baselines(self):
        for name, record in DYNAMIC_DECKS.items():
            with self.subTest(name):
                self.assertEqual(record["authority"], NEW_BASELINE)
                for key in ("established", "git_blob", "source_commit",
                            "corroborated_by"):
                    self.assertTrue(record[key], key)
                self.assertEqual(sha256_of(os.path.join(DECK_DIR, name)),
                                 record["sha256"])

    def test_the_fine_load_curve_is_the_plain_ramp_and_hold(self):
        self.assertEqual(funct_blocks(self.fine_deck)[1][1], FINE_CURVE)

    def test_the_dynamic_load_curve_is_the_documented_release(self):
        self.assertEqual(funct_blocks(self.dyn_deck)[1][1], DYNAMIC_CURVE)

    def test_release_start_release_complete_and_end_are_three_different_times(self):
        points = funct_blocks(self.dyn_deck)[1][1]
        times = [t for t, _ in points]
        values = [v for _, v in points]
        self.assertEqual(times, sorted(times))
        last_loaded = max(t for t, v in points if v == -1.0)
        first_unloaded = min(t for t, v in points if v == 0.0 and t > last_loaded)
        first_full = min(t for t, v in points if v == -1.0)
        self.assertEqual(first_full, RAMP_END_S)
        self.assertEqual(last_loaded, RELEASE_START_S)
        self.assertEqual(first_unloaded, RELEASE_COMPLETE_S)
        self.assertEqual(times[-1], END_TIME_S)
        self.assertLess(RAMP_END_S, RELEASE_START_S)
        self.assertLess(RELEASE_START_S, RELEASE_COMPLETE_S)
        self.assertLess(RELEASE_COMPLETE_S, END_TIME_S)
        self.assertEqual(values[-1], 0.0)

    def test_the_engine_runs_to_the_end_of_the_load_curve(self):
        end_time, _ = engine_end_time(self.dyn_engine)
        self.assertEqual(end_time, END_TIME_S)
        self.assertEqual(funct_blocks(self.dyn_deck)[1][1][-1][0], end_time)
        self.assertEqual(engine_end_time(self.fine_engine)[0], FINE_END_TIME_S)

    def test_starter_deck_differs_from_fine_only_in_the_load_function(self):
        _, _, fine_first, fine_last = funct_blocks(self.fine_deck)[1]
        _, _, dyn_first, dyn_last = funct_blocks(self.dyn_deck)[1]
        self.assertEqual(without_lines(self.fine_deck, fine_first, fine_last),
                         without_lines(self.dyn_deck, dyn_first, dyn_last))

    def test_only_one_load_function_exists_in_each_starter_deck(self):
        self.assertEqual(set(funct_blocks(self.fine_deck)), {1})
        self.assertEqual(set(funct_blocks(self.dyn_deck)), {1})

    def test_engine_input_differs_from_fine_only_in_the_end_time(self):
        _, fine_index = engine_end_time(self.fine_engine)
        _, dyn_index = engine_end_time(self.dyn_engine)
        self.assertEqual(fine_index, dyn_index)
        self.assertEqual(without_lines(self.fine_engine, fine_index, fine_index),
                         without_lines(self.dyn_engine, dyn_index, dyn_index))

    def test_the_protocol_states_the_same_two_times(self):
        """Prose, not structure, so this checks the two numbers appear where the
        protocol puts them. The protocol is read, never amended, here."""
        with open(PROTOCOL_V2, encoding="utf-8") as handle:
            protocol = json.load(handle)
        strings = []

        def collect(node):
            if isinstance(node, dict):
                for value in node.values():
                    collect(value)
            elif isinstance(node, list):
                for value in node:
                    collect(value)
            elif isinstance(node, str):
                strings.append(node)
        collect(protocol)
        self.assertTrue(any("deck-specified 0.501 s" in s for s in strings))
        self.assertTrue(any(re.search(r"t_end extended to 2\.7 s", s) for s in strings))


if __name__ == "__main__":
    unittest.main()
