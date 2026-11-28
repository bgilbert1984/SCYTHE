"""Persistence pass: the detector, the shared eligibility mask, RF-fixed classification across runs and sites, the instrument
vocabulary, integrity checks (size, sha256, revision, provenance, completeness) and cache isolation."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import persistence_pass as p  # noqa: E402

F = 445_400_000.0


def window(tones, seconds=0.5, noise=2.0, seed=1):
    rng = np.random.default_rng(seed)
    n = int(p.FS * seconds)
    t = np.arange(n) / p.FS
    z = noise * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    for off, amp in tones:
        z = z + amp * np.exp(2j * np.pi * off * t)
    iq = np.empty(2 * n, dtype=np.uint8)
    iq[0::2] = np.clip(np.round(127.5 + z.real), 0, 255)
    iq[1::2] = np.clip(np.round(127.5 + z.imag), 0, 255)
    return iq.tobytes()


def visit(centre, line_at=None, pos=0, clip=0.0, error=None):
    """A visit whose detector found a line at absolute frequency line_at (or none)."""
    lines = [] if line_at is None else [[line_at - centre, 18.0]]
    return {"error": error, "centre_hz": centre, "retune_delta_hz": 0.0, "position": pos, "clipping_pct": clip, "lines": lines, "sha256_verified": True}


def doc(set_id, site, visits):
    return {"set_id": set_id, "site_key": site, "visits": visits}


def diverse(freq, n=3, base=150_000.0):
    """n visits that each see freq at a different offset from their centre (RF-fixed)."""
    return [visit(freq - base * (i + 1), freq, i) for i in range(n)]


def same_offset(freq, n=3, off=150_000.0):
    """n visits at one centre: a line at a fixed offset, no offset diversity."""
    return [visit(freq - off, freq, i) for i in range(n)]


class DetectLines(unittest.TestCase):
    def test_a_tone_is_found_at_its_offset(self):
        lines = p.detect_lines(window([(300_000.0, 12.0)]))
        self.assertTrue(any(abs(off - 300_000.0) < 1000.0 and snr > 15.0 for off, snr in lines), lines)

    def test_noise_alone_gives_no_lines(self):
        self.assertEqual(p.detect_lines(window([])), [])

    def test_the_dc_guard_and_the_canary_lines_are_not_reported(self):
        lines = p.detect_lines(window([(2_000.0, 15.0), (p.CANARY_HZ, 15.0), (-p.CANARY_HZ, 15.0)]))
        self.assertEqual(lines, [])

    def test_a_wide_signal_is_not_a_narrow_line(self):
        rng = np.random.default_rng(3)
        n = int(p.FS * 0.5)
        t = np.arange(n) / p.FS
        wide = np.exp(2j * np.pi * (200_000.0 * t + 30_000.0 * np.cumsum(rng.standard_normal(n)) / p.FS * 40))
        z = 2.0 * (rng.standard_normal(n) + 1j * rng.standard_normal(n)) + 10.0 * wide
        iq = np.empty(2 * n, dtype=np.uint8)
        iq[0::2] = np.clip(np.round(127.5 + z.real), 0, 255)
        iq[1::2] = np.clip(np.round(127.5 + z.imag), 0, 255)
        self.assertEqual([x for x in p.detect_lines(iq.tobytes()) if abs(x[0] - 200_000.0) < 100_000.0], [])


class Eligibility(unittest.TestCase):
    """F-01: the mask that stops the detector reporting is the mask that stops a visit counting as coverage."""

    def test_the_mask_excludes_dc_canary_and_edges(self):
        e = p.eligible_offsets([0.0, 10_000.0, 100_000.0, p.CANARY_HZ, -p.CANARY_HZ, 930_000.0, -930_000.0])
        self.assertEqual(e.tolist(), [False, False, True, False, False, False, False])

    def test_visits_that_cannot_observe_a_frequency_do_not_dilute_its_persistence(self):
        blind = [visit(F - 10_000.0, None, i) for i in range(3)]                     # F is inside their DC guard
        ag = p.aggregate_run(doc("r", "s", blind + diverse(F)))
        k = int((F - ag["f_lo"]) / 1000 + 0.5)
        self.assertEqual((int(ag["cover"][k]), int(ag["det"][k])), (3, 3))
        lines = p.lines_of(ag, require_rf_fixed=True)
        self.assertTrue(lines and lines[0]["persistence"] == 1.0, lines)

    def test_a_frequency_no_visit_can_observe_is_unobservable_not_quiet(self):
        blind = [visit(F - 10_000.0, None, i) for i in range(4)]
        aggs = {"r": p.aggregate_run(doc("r", "s", blind))}
        self.assertEqual(p.presence(aggs, F)["r"]["state"], "UNOBSERVABLE")

    def test_a_detection_inside_the_guard_is_not_counted(self):
        v = [visit(F - 10_000.0, F, i) for i in range(3)]                             # a line the detector could not have reported
        ag = p.aggregate_run(doc("r", "s", v))
        self.assertEqual(int(ag["det"].sum()), 0)


class CrossRunClassification(unittest.TestCase):
    """F-02: one rule (RF-fixed, persistence >= 0.6, >= 3 covering visits) decides detection in every run."""

    def classify(self, docs):
        out, _ = p.classify_docs({d["set_id"]: d for d in docs})
        return out

    def test_a_site_without_offset_diversity_is_not_a_detecting_site(self):
        out = self.classify([doc("A", "siteA", diverse(F)), doc("B", "siteB", same_offset(F))])
        self.assertEqual([x["class"] for x in out], ["SINGLE_RUN"])
        self.assertEqual(out[0]["sites_detecting"], ["siteA"])
        self.assertEqual(out[0]["per_run"]["B"]["state"], "INTERMEDIATE")
        self.assertFalse(out[0]["per_run"]["B"]["rf_fixed"])

    def test_two_sites_with_offset_diversity_are_multi_site(self):
        out = self.classify([doc("A", "siteA", diverse(F)), doc("B", "siteB", diverse(F, base=170_000.0))])
        self.assertIn(out[0]["class"], ("MULTI_SITE_PARTIAL", "PRESENT_AT_ALL_OBSERVED_SITES"))
        self.assertEqual(out[0]["sites_detecting"], ["siteA", "siteB"])

    def test_fifty_percent_persistence_is_below_the_documented_threshold(self):
        half = diverse(F, 2) + [visit(F - 400_000.0, None, 9), visit(F - 500_000.0, None, 10)]
        out = self.classify([doc("A", "siteA", diverse(F)), doc("B", "siteB", half)])
        self.assertEqual(out[0]["per_run"]["B"]["persistence"], 0.5)
        self.assertNotEqual(out[0]["per_run"]["B"]["state"], "DETECTING")
        self.assertEqual(out[0]["class"], "SINGLE_RUN")

    def test_a_line_quiet_at_every_other_observed_site_is_site_local(self):
        quiet = [visit(F - 150_000.0 * (i + 1), None, i) for i in range(3)]
        out = self.classify([doc("A1", "siteA", diverse(F)), doc("A2", "siteA", diverse(F, base=160_000.0)), doc("B", "siteB", quiet)])
        self.assertEqual(out[0]["class"], "SITE_LOCAL_PERSISTENT")

    def test_a_site_that_cannot_observe_the_frequency_does_not_make_it_site_local_or_not(self):
        blind = [visit(F - 10_000.0, None, i) for i in range(3)]
        out = self.classify([doc("A1", "siteA", diverse(F)), doc("A2", "siteA", diverse(F, base=160_000.0)), doc("B", "siteB", blind)])
        self.assertEqual(out[0]["per_run"]["B"]["state"], "UNOBSERVABLE")
        self.assertEqual(out[0]["class"], "MULTI_RUN_SINGLE_SITE_OTHERS_UNCLEAR")

    def test_clipped_visits_are_excluded(self):
        v = [visit(F - 150_000.0 * (i + 1), F, i, clip=0.5) for i in range(3)]
        self.assertEqual(p.lines_of(p.aggregate_run(doc("r", "s", v))), [])


class InstrumentVocabulary(unittest.TestCase):
    """F-03: frequency alignment with the reference clock is a candidate, never a confirmation."""

    def classes(self, docs):
        return [x["class"] for x in p.classify_docs({d["set_id"]: d for d in docs})[0]]

    def test_alignment_alone_is_a_candidate(self):
        f = 14 * p.REF_HZ
        self.assertEqual(self.classes([doc("A", "siteA", diverse(f))]), ["REFERENCE_HARMONIC_CANDIDATE"])

    def test_present_wherever_observed_at_three_venues_is_consistent_with_internal_mixing(self):
        f = 14 * p.REF_HZ
        c = self.classes([doc("A", "siteA", diverse(f)), doc("B", "siteB", diverse(f, base=160_000.0)), doc("C", "siteC", diverse(f, base=170_000.0))])
        self.assertEqual(c, ["CONSISTENT_WITH_INTERNAL_MIXING"])

    def test_two_venues_is_still_only_a_candidate(self):
        f = 14 * p.REF_HZ
        self.assertEqual(self.classes([doc("A", "siteA", diverse(f)), doc("B", "siteB", diverse(f, base=160_000.0))]), ["REFERENCE_HARMONIC_CANDIDATE"])

    def test_origin_is_never_reported_as_confirmed(self):
        f = 14 * p.REF_HZ
        docs = [doc(s, s, diverse(f, base=150_000.0 + 10_000.0 * i)) for i, s in enumerate("ABCDE")]
        out = p.classify_docs({d["set_id"]: d for d in docs})[0]
        self.assertFalse({x["class"] for x in out} & {"INSTRUMENT_ORIGIN_CONFIRMED", "EXTERNAL_RF_CONFIRMED"})


class Integrity(unittest.TestCase):
    """F-04, F-05."""

    def test_a_truncated_window_is_rejected(self):
        with self.assertRaises(p.IntegrityError):
            p.validate_window(bytes(8192), None)

    def test_a_window_with_the_wrong_hash_is_rejected(self):
        raw = bytes(p.EXPECTED_BYTES)
        p.validate_window(raw, hashlib.sha256(raw).hexdigest())
        with self.assertRaises(p.IntegrityError):
            p.validate_window(raw, "0" * 64)

    def test_a_bad_visit_is_recorded_as_an_error_not_processed(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "x", "spool"))
            with open(os.path.join(root, "x", "spool", "visit_0000.iq"), "wb") as fh:
                fh.write(bytes(8192))
            with mock.patch.dict(p.LOCAL_DIRS, {"setx": "x"}):
                r = p.one_visit(("setx", "data/runs/setx/spool/visit_0000.iq", F, 0.0, 0, root, None, "rev"))
        self.assertIsNotNone(r["error"])
        self.assertEqual(r["lines"], [])
        self.assertFalse(r["sha256_verified"])

    def test_a_good_visit_is_verified(self):
        raw = window([(300_000.0, 12.0)], seconds=2.048)[: p.EXPECTED_BYTES]
        raw = raw + bytes(p.EXPECTED_BYTES - len(raw))
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "x", "spool"))
            with open(os.path.join(root, "x", "spool", "visit_0000.iq"), "wb") as fh:
                fh.write(raw)
            with mock.patch.dict(p.LOCAL_DIRS, {"setx": "x"}):
                r = p.one_visit(("setx", "data/runs/setx/spool/visit_0000.iq", F, 0.0, 0, root, hashlib.sha256(raw).hexdigest(), "rev"))
        self.assertIsNone(r["error"])
        self.assertTrue(r["sha256_verified"])

    def test_remote_reads_use_the_pinned_revision_and_an_isolated_cache(self):
        seen = {}

        def fake(repo, rel, repo_type, revision, cache_dir):
            seen.update(revision=revision, cache_dir=cache_dir, existed=os.path.isdir(cache_dir))
            path = os.path.join(cache_dir, "w.iq")
            with open(path, "wb") as fh:
                fh.write(b"abc")
            return path

        with mock.patch("huggingface_hub.hf_hub_download", fake):
            raw = p.read_window("run_remote", "data/runs/run_remote/spool/visit_0000.iq", "/nonexistent", "deadbeef")
        self.assertEqual(raw, b"abc")
        self.assertEqual(seen["revision"], "deadbeef")
        self.assertTrue(seen["existed"])
        self.assertNotIn(".cache", seen["cache_dir"])
        self.assertFalse(os.path.exists(seen["cache_dir"]))                          # removed afterwards

    def test_remote_only_ignores_a_local_copy_and_reads_the_pinned_dataset(self):
        calls = []

        def fake(repo, rel, repo_type, revision, cache_dir):
            calls.append(revision)
            path = os.path.join(cache_dir, "w.iq")
            with open(path, "wb") as fh:
                fh.write(b"remote")
            return path

        with tempfile.TemporaryDirectory() as root, mock.patch.dict(p.LOCAL_DIRS, {"setx": "x"}), mock.patch("huggingface_hub.hf_hub_download", fake):
            os.makedirs(os.path.join(root, "x", "spool"))
            with open(os.path.join(root, "x", "spool", "visit_0000.iq"), "wb") as fh:
                fh.write(b"local")
            rel = "data/runs/setx/spool/visit_0000.iq"
            self.assertEqual(p.read_window("setx", rel, root, "rev9"), b"local")
            self.assertEqual(p.read_window("setx", rel, None, "rev9"), b"remote")
        self.assertEqual(calls, ["rev9"])

    def test_the_software_commit_can_be_supplied_when_running_outside_a_checkout(self):
        with mock.patch.dict(os.environ, {"S521_SOFTWARE_COMMIT": "abc123"}):
            self.assertEqual(p.git_state()["git_head"], "abc123")

    def test_the_manifest_hash_is_order_independent_and_changes_with_content(self):
        a = [("f1", "aa"), ("f2", "bb")]
        self.assertEqual(p.manifest_sha256(a), p.manifest_sha256(list(reversed(a))))
        self.assertNotEqual(p.manifest_sha256(a), p.manifest_sha256([("f1", "aa"), ("f2", "bc")]))


class Provenance(unittest.TestCase):
    def good(self, **over):
        pv = p.provenance_for("rev1", "m" * 64, 2, 2, True)
        pv.update(over)
        return pv

    def write(self, d, set_id, pv, visits=None):
        with open(os.path.join(d, set_id + ".json"), "w") as fh:
            json.dump({"set_id": set_id, "site_key": "s", "provenance": pv, "visits": visits or [visit(F, None, 0), visit(F, None, 1)]}, fh)

    def test_matching_provenance_is_accepted_for_skipping(self):
        self.assertTrue(p.provenance_matches(self.good(), "rev1", "m" * 64))

    def test_a_different_revision_manifest_config_or_code_is_not_skipped(self):
        self.assertFalse(p.provenance_matches(self.good(), "rev2", "m" * 64))
        self.assertFalse(p.provenance_matches(self.good(), "rev1", "n" * 64))
        self.assertFalse(p.provenance_matches(self.good(analysis_config_sha256="0" * 64), "rev1", "m" * 64))
        sw = dict(self.good()["software"], code_sha256="0" * 64)
        self.assertFalse(p.provenance_matches(self.good(software=sw), "rev1", "m" * 64))
        self.assertFalse(p.provenance_matches(None, "rev1", "m" * 64))

    def test_an_incomplete_or_unverified_run_is_not_skipped_and_is_refused(self):
        self.assertFalse(p.provenance_matches(self.good(processed_visits=1), "rev1", "m" * 64))
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "r1", self.good(processed_visits=1))
            with self.assertRaises(p.IntegrityError):
                p.load_docs(d)

    def test_a_file_without_provenance_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "r1", None)
            with self.assertRaises(p.IntegrityError):
                p.load_docs(d)

    def test_a_missing_run_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "r1", self.good())
            with self.assertRaises(p.IntegrityError):
                p.load_docs(d, {"r1", "r2"})

    def test_runs_from_different_revisions_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "r1", self.good())
            self.write(d, "r2", self.good(source_dataset_revision="rev2"))
            with self.assertRaises(p.IntegrityError):
                p.load_docs(d)

    def test_results_carry_the_provenance_block(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, "r1", self.good(), diverse(F) + [visit(F, None, 9)])
            out = os.path.join(d, "results.json")
            ns = type("A", (), {"out": d, "results": out, "sets": None})()
            p.cmd_classify(ns)
            with open(out) as fh:
                r = json.load(fh)
        for key in ("analysis_schema_version", "source_dataset_revision", "capture_manifest_sha256", "analysis_config_sha256", "software",
                    "expected_visits", "processed_visits", "integrity_verified"):
            self.assertIn(key, r["provenance"])
        self.assertTrue(r["provenance"]["integrity_verified"])
        self.assertEqual(r["provenance"]["software"]["code_sha256"], p.code_sha256())


if __name__ == "__main__":
    unittest.main()
