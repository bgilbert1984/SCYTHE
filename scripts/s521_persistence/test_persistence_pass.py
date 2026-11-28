"""Persistence pass: the detector, the shared eligibility mask, RF-fixed classification across runs and sites, bounded frequency
association, the instrument vocabulary, and the evidence checks (visit-level completeness, manifest binding, pinned revision,
provenance, cache isolation). Nothing here needs the network or huggingface_hub: downloads are replaced by a stub module."""
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import persistence_pass as p  # noqa: E402

F = 445_400_000.0
REV = "a" * 40
CSHA = "c" * 64


@contextlib.contextmanager
def stub_hub(fake):
    """Replace huggingface_hub by a module whose hf_hub_download is `fake` (so the tests run without the package or a network)."""
    with mock.patch.dict(sys.modules, {"huggingface_hub": types.SimpleNamespace(hf_hub_download=fake)}):
        yield


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
    """A loose visit record whose detector found a line at absolute frequency line_at (or none), for the classification tests."""
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


# ---- fixtures with a real manifest, for the evidence checks ---------------------------------------------------------------

def rows_for(set_id="setx", site="siteA", n=3, base=445_000_000.0):
    return [{"set_id": set_id, "site_key": site, "position": i, "file": f"data/runs/{set_id}/spool/visit_{i:04d}.iq",
             "sha256": hashlib.sha256(bytes([i % 256])).hexdigest(), "centre_hz": base + i * 150_000.0, "retune_delta_hz": -100_000.0, "clipping_pct": 0.0}
            for i in range(n)]


def valid_doc(rows, line_at=None):
    visits = [{"position": r["position"], "file": r["file"], "sha256": r["sha256"], "centre_hz": r["centre_hz"], "retune_delta_hz": r["retune_delta_hz"],
               "clipping_pct": r["clipping_pct"], "lines": [] if line_at is None else [[line_at - r["centre_hz"], 18.0]], "error": None, "sha256_verified": True}
              for r in rows]
    prov = p.provenance_for(REV, CSHA, p.manifest_sha256(rows), len(rows), len(rows), True)
    d = {"set_id": rows[0]["set_id"], "site_key": rows[0]["site_key"], "provenance": prov, "visits": visits}
    prov["detection_document_sha256"] = p.document_sha256(d)
    return d


def reseal(d):
    d["provenance"]["detection_document_sha256"] = p.document_sha256(d)
    return d


def write(out_dir, d, name=None):
    with open(os.path.join(out_dir, name or d["set_id"] + ".json"), "w") as fh:
        json.dump(d, fh)


def df_from(rows_by_set):
    recs = [{"kind": "observation_run_visit", "set_id": r["set_id"], "site_key": r["site_key"], "visit_position": r["position"], "file": r["file"],
             "sha256": r["sha256"], "centre_hz": r["centre_hz"], "retune_delta_hz": r["retune_delta_hz"], "clipping_pct": r["clipping_pct"]}
            for rows in rows_by_set.values() for r in rows]
    return pd.DataFrame(recs)


class DetectLines(unittest.TestCase):
    def test_a_tone_is_found_at_its_offset(self):
        lines = p.detect_lines(window([(300_000.0, 12.0)]))
        self.assertTrue(any(abs(off - 300_000.0) < 1000.0 and snr > 15.0 for off, snr in lines), lines)

    def test_noise_alone_gives_no_lines(self):
        self.assertEqual(p.detect_lines(window([])), [])

    def test_the_dc_guard_and_the_canary_lines_are_not_reported(self):
        self.assertEqual(p.detect_lines(window([(2_000.0, 15.0), (p.CANARY_HZ, 15.0), (-p.CANARY_HZ, 15.0)])), [])

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
        blind = [visit(F - 10_000.0, None, i) for i in range(3)]
        ag = p.aggregate_run(doc("r", "s", blind + diverse(F)))
        k = int((F - ag["f_lo"]) / 1000 + 0.5)
        self.assertEqual((int(ag["cover"][k]), int(ag["det"][k])), (3, 3))
        lines = p.lines_of(ag, require_rf_fixed=True)
        self.assertTrue(lines and lines[0]["persistence"] == 1.0, lines)

    def test_a_frequency_no_visit_can_observe_is_unobservable_not_quiet(self):
        aggs = {"r": p.aggregate_run(doc("r", "s", [visit(F - 10_000.0, None, i) for i in range(4)]))}
        self.assertEqual(p.presence(aggs, F)["r"]["state"], "UNOBSERVABLE")

    def test_a_detection_inside_the_guard_is_not_counted(self):
        ag = p.aggregate_run(doc("r", "s", [visit(F - 10_000.0, F, i) for i in range(3)]))
        self.assertEqual(int(ag["det"].sum()), 0)


class CrossRunClassification(unittest.TestCase):
    """F-02: one rule (RF-fixed, persistence >= 0.6, >= 3 covering visits) decides detection in every run."""

    def classify(self, docs):
        return p.classify_docs({d["set_id"]: d for d in docs})[0]

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

    def test_a_site_that_cannot_observe_the_frequency_is_unobservable(self):
        blind = [visit(F - 10_000.0, None, i) for i in range(3)]
        out = self.classify([doc("A1", "siteA", diverse(F)), doc("A2", "siteA", diverse(F, base=160_000.0)), doc("B", "siteB", blind)])
        self.assertEqual(out[0]["per_run"]["B"]["state"], "UNOBSERVABLE")
        self.assertEqual(out[0]["class"], "MULTI_RUN_SINGLE_SITE_OTHERS_UNCLEAR")

    def test_clipped_visits_are_excluded(self):
        v = [visit(F - 150_000.0 * (i + 1), F, i, clip=0.5) for i in range(3)]
        self.assertEqual(p.lines_of(p.aggregate_run(doc("r", "s", v))), [])


class BoundedAssociation(unittest.TestCase):
    """P1-02: frequencies are associated within a bounded cluster diameter, never by chaining neighbours; no qualified line is lost."""

    def classify(self, offsets_by_run):
        docs = {r: doc(r, r, diverse(F + off)) for r, off in offsets_by_run.items()}
        return p.classify_docs(docs)[0]

    def test_three_lines_3_khz_apart_at_three_sites_are_all_kept(self):
        out = self.classify({"A": 0.0, "B": 3000.0, "C": 6000.0})
        members = [m["run"] for x in out for m in x["members"]]
        self.assertEqual(sorted(members), ["A", "B", "C"])
        detecting = {s for x in out for s in x["sites_detecting"]}
        self.assertEqual(detecting, {"A", "B", "C"})                               # no site lost

    def test_a_cluster_never_spans_more_than_the_maximum_diameter(self):
        out = self.classify({r: 3000.0 * i for i, r in enumerate("ABCD")})         # a chain 0, 3, 6, 9 kHz
        for x in out:
            self.assertLessEqual(x["freq_max_hz"] - x["freq_min_hz"], p.GROUP_MAX_DIAMETER_HZ)
        self.assertEqual(sorted(m["run"] for x in out for m in x["members"]), ["A", "B", "C", "D"])

    def test_member_frequencies_and_runs_are_preserved(self):
        out = self.classify({"A": 0.0, "B": 2000.0})
        self.assertEqual(len(out), 1)
        self.assertEqual({(m["run"], m["freq_hz"]) for m in out[0]["members"]}, {("A", F), ("B", F + 2000.0)})
        self.assertEqual(out[0]["sites_detecting"], ["A", "B"])

    def test_adjacent_clusters_are_marked_unresolved(self):
        out = self.classify({"A": 0.0, "B": 3000.0, "C": 6000.0})
        self.assertTrue(all(x["unresolved_association"] for x in out))

    def test_an_isolated_line_is_not_marked_unresolved(self):
        out = self.classify({"A": 0.0, "B": 1000.0})
        self.assertFalse(out[0]["unresolved_association"])

    def test_two_lines_from_one_run_in_a_cluster_are_marked_unresolved(self):
        visits = diverse(F)
        for v in visits:                                                             # each visit also sees a second line 1.5 kHz up
            v["lines"].append([F + 1500.0 - v["centre_hz"], 18.0])
        out = p.classify_docs({"A": doc("A", "siteA", visits)})[0]
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["members"]), 2)
        self.assertTrue(out[0]["unresolved_association"])


class QualifiedNeighbour(unittest.TestCase):
    """P2-03: nearby unqualified energy never overrides a qualified detection."""

    def agg(self):
        nb = int((p.F_HI - p.F_LO) / 1000)
        k = int((F - p.F_LO) / 1000)
        ag = {"cover": np.zeros(nb, dtype=np.int32), "det": np.zeros(nb, dtype=np.int32), "snr_sum": np.zeros(nb),
              "offs": [[] for _ in range(nb)], "visits_used": 5, "f_lo": p.F_LO}
        ag["cover"][k] = ag["det"][k] = 3
        ag["offs"][k] = [150e3, 250e3, 350e3]
        ag["snr_sum"][k] = 54.0
        ag["cover"][k + 1] = ag["det"][k + 1] = 5                                   # more detections, but one offset only
        ag["offs"][k + 1] = [150e3] * 5
        ag["snr_sum"][k + 1] = 90.0
        return ag, k

    def test_a_qualified_bin_is_chosen_over_a_stronger_unqualified_neighbour(self):
        ag, k = self.agg()
        pr = p.presence({"r": ag}, p.F_LO + k * 1000.0)["r"]
        self.assertEqual(pr["state"], "DETECTING")
        self.assertTrue(pr["rf_fixed"])
        self.assertEqual(pr["detecting_visits"], 3)

    def test_without_a_qualified_bin_the_strongest_is_reported(self):
        ag, k = self.agg()
        ag["offs"][k] = [150e3] * 3
        pr = p.presence({"r": ag}, p.F_LO + k * 1000.0)["r"]
        self.assertEqual(pr["state"], "INTERMEDIATE")
        self.assertEqual(pr["detecting_visits"], 5)


class InstrumentVocabulary(unittest.TestCase):
    """F-03: frequency alignment with the reference clock is a candidate, never a confirmation."""

    def classes(self, docs):
        return [x["class"] for x in p.classify_docs({d["set_id"]: d for d in docs})[0]]

    def test_alignment_alone_is_a_candidate(self):
        self.assertEqual(self.classes([doc("A", "siteA", diverse(14 * p.REF_HZ))]), ["REFERENCE_HARMONIC_CANDIDATE"])

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


class WindowsAndReads(unittest.TestCase):
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
                r = p.one_visit(("setx", "data/runs/setx/spool/visit_0000.iq", F, 0.0, 0, root, None, REV))
        self.assertIsNotNone(r["error"])
        self.assertEqual(r["lines"], [])
        self.assertFalse(r["sha256_verified"])

    def test_remote_reads_use_the_pinned_revision_and_an_isolated_cache(self):
        seen = {}

        def fake(repo, rel, repo_type, revision, cache_dir):
            seen.update(revision=revision, cache_dir=cache_dir, existed=os.path.isdir(cache_dir))
            path = os.path.join(cache_dir, "w.iq")
            with open(path, "wb") as fh:
                fh.write(b"abc")
            return path

        with stub_hub(fake):
            raw = p.read_window("run_remote", "data/runs/run_remote/spool/visit_0000.iq", "/nonexistent", REV)
        self.assertEqual(raw, b"abc")
        self.assertEqual(seen["revision"], REV)
        self.assertTrue(seen["existed"])
        self.assertNotIn(".cache", seen["cache_dir"])
        self.assertFalse(os.path.exists(seen["cache_dir"]))

    def test_remote_only_ignores_a_local_copy_and_reads_the_pinned_dataset(self):
        calls = []

        def fake(repo, rel, repo_type, revision, cache_dir):
            calls.append(revision)
            path = os.path.join(cache_dir, "w.iq")
            with open(path, "wb") as fh:
                fh.write(b"remote")
            return path

        with tempfile.TemporaryDirectory() as root, mock.patch.dict(p.LOCAL_DIRS, {"setx": "x"}), stub_hub(fake):
            os.makedirs(os.path.join(root, "x", "spool"))
            with open(os.path.join(root, "x", "spool", "visit_0000.iq"), "wb") as fh:
                fh.write(b"local")
            rel = "data/runs/setx/spool/visit_0000.iq"
            self.assertEqual(p.read_window("setx", rel, root, REV), b"local")
            self.assertEqual(p.read_window("setx", rel, None, REV), b"remote")
        self.assertEqual(calls, [REV])

    def test_the_software_commit_can_be_supplied_when_running_outside_a_checkout(self):
        with mock.patch.dict(os.environ, {"S521_SOFTWARE_COMMIT": "abc123"}):
            self.assertEqual(p.git_state()["git_head"], "abc123")


class ManifestBinding(unittest.TestCase):
    """P2-04: the manifest binds every input that affects analysis; the revision and any local parquet are verified."""

    def test_the_manifest_hash_is_sensitive_to_every_analytical_input(self):
        rows = rows_for()
        base = p.manifest_sha256(rows)
        for field, value in (("centre_hz", 1.0), ("retune_delta_hz", 5.0), ("position", 99), ("clipping_pct", 0.5), ("site_key", "other"),
                             ("sha256", "0" * 64), ("file", "data/runs/setx/spool/other.iq")):
            changed = [dict(r) for r in rows]
            changed[0][field] = value
            self.assertNotEqual(p.manifest_sha256(changed), base, field)

    def test_a_branch_name_or_short_sha_is_not_a_revision(self):
        for bad in ("main", "6426d013", "A" * 40, "", None, "a" * 39):
            with self.assertRaises(p.IntegrityError, msg=str(bad)):
                p.check_revision(bad)
        p.check_revision(REV)

    def test_a_local_parquet_must_match_the_pinned_revision(self):
        with tempfile.TemporaryDirectory() as d:
            remote = os.path.join(d, "remote.parquet")
            local = os.path.join(d, "local.parquet")
            df_from({"setx": rows_for()}).to_parquet(remote)
            df_from({"setx": rows_for(base=446_000_000.0)}).to_parquet(local)

            def fake(repo, rel, repo_type, revision, cache_dir):
                path = os.path.join(cache_dir, "captures.parquet")
                with open(remote, "rb") as src, open(path, "wb") as dst:
                    dst.write(src.read())
                return path

            with stub_hub(fake):
                with self.assertRaises(p.IntegrityError):
                    p.load_captures(local, REV)
                df, sha = p.load_captures(remote, REV)
                self.assertEqual(sha, p.file_sha256(remote))
                self.assertEqual(len(df), 3)
                with self.assertRaises(p.IntegrityError):
                    p.load_captures(None, "main")


class VisitLevelCompleteness(unittest.TestCase):
    """P1-01: completeness is established from the visit records against the manifest, never from the provenance flags."""

    def check(self, d, rows=None, revision=REV, csha=CSHA):
        rows = rows or rows_for(d["set_id"], d["site_key"])
        p.validate_detection_doc(d, rows, revision, csha)

    def test_a_consistent_document_is_accepted(self):
        self.check(valid_doc(rows_for()))

    def test_one_unverified_record_under_a_declared_full_run_is_refused(self):
        rows = rows_for(n=192)
        d = valid_doc(rows)
        d["visits"] = [dict(d["visits"][0], sha256_verified=False)]
        reseal(d)                                                                    # provenance still declares 192 verified
        with self.assertRaises(p.IntegrityError):
            self.check(d, rows)

    def test_missing_records_are_refused_even_with_a_matching_flag(self):
        rows = rows_for(n=5)
        d = valid_doc(rows)
        d["visits"] = d["visits"][:3]
        with self.assertRaises(p.IntegrityError):
            self.check(reseal(d), rows)

    def test_duplicate_positions_are_refused(self):
        rows = rows_for()
        d = valid_doc(rows)
        d["visits"][1] = dict(d["visits"][0])
        with self.assertRaises(p.IntegrityError):
            self.check(reseal(d), rows)

    def test_positions_that_differ_from_the_manifest_are_refused(self):
        rows = rows_for()
        d = valid_doc(rows)
        d["visits"][2]["position"] = 7
        with self.assertRaises(p.IntegrityError):
            self.check(reseal(d), rows)

    def test_an_error_record_is_refused(self):
        rows = rows_for()
        d = valid_doc(rows)
        d["visits"][0]["error"] = "x"
        with self.assertRaises(p.IntegrityError):
            self.check(reseal(d), rows)

    def test_a_record_whose_inputs_differ_from_the_manifest_is_refused(self):
        for field, value in (("centre_hz", 1.0), ("retune_delta_hz", 5.0), ("clipping_pct", 0.5), ("sha256", "0" * 64), ("file", "x")):
            rows = rows_for()
            d = valid_doc(rows)
            d["visits"][1][field] = value
            with self.assertRaises(p.IntegrityError, msg=field):
                self.check(reseal(d), rows)

    def test_a_different_site_assignment_is_refused(self):
        rows = rows_for()
        d = valid_doc(rows)
        d["site_key"] = "elsewhere"
        with self.assertRaises(p.IntegrityError):
            self.check(reseal(d), rows)

    def test_an_edited_document_does_not_match_its_recorded_hash(self):
        rows = rows_for()
        d = valid_doc(rows)
        d["visits"][0]["lines"] = [[250_000.0, 20.0]]                                # edited after hashing
        with self.assertRaises(p.IntegrityError):
            self.check(d, rows)

    def test_a_different_revision_parquet_config_or_code_is_refused(self):
        rows = rows_for()
        with self.assertRaises(p.IntegrityError):
            self.check(valid_doc(rows), rows, revision="b" * 40)
        with self.assertRaises(p.IntegrityError):
            self.check(valid_doc(rows), rows, csha="d" * 64)
        for key in ("analysis_config_sha256", "analysis_code_sha256", "capture_manifest_sha256"):
            d = valid_doc(rows)
            d["provenance"][key] = "0" * 64
            with self.assertRaises(p.IntegrityError, msg=key):
                self.check(reseal(d), rows)

    def test_a_document_without_current_schema_provenance_is_refused(self):
        rows = rows_for()
        d = valid_doc(rows)
        d["provenance"]["analysis_schema_version"] = 1
        with self.assertRaises(p.IntegrityError):
            self.check(d, rows)
        d = valid_doc(rows)
        d["provenance"] = None
        with self.assertRaises(p.IntegrityError):
            self.check(d, rows)


class RunInventory(unittest.TestCase):
    def setUp(self):
        self.rows = {"r1": rows_for("r1", "siteA"), "r2": rows_for("r2", "siteB")}

    def test_the_expected_inventory_is_required_not_whatever_files_exist(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, valid_doc(self.rows["r1"]))
            with self.assertRaises(p.IntegrityError):
                p.load_docs(d, self.rows, REV, CSHA)                                 # r2 missing
            write(d, valid_doc(self.rows["r2"]))
            self.assertEqual(set(p.load_docs(d, self.rows, REV, CSHA)), {"r1", "r2"})

    def test_a_duplicate_run_identifier_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, valid_doc(self.rows["r1"]))
            write(d, valid_doc(self.rows["r1"]), name="r1_copy.json")
            with self.assertRaises(p.IntegrityError):
                p.load_docs(d, self.rows, REV, CSHA, {"r1"})

    def test_a_file_name_that_does_not_match_its_run_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, valid_doc(self.rows["r1"]), name="r9.json")
            with self.assertRaises(p.IntegrityError):
                p.load_docs(d, self.rows, REV, CSHA, {"r1"})

    def test_a_run_not_in_the_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, valid_doc(rows_for("ghost", "siteZ")))
            with self.assertRaises(p.IntegrityError):
                p.load_docs(d, self.rows, REV, CSHA, {"r1"} | {"ghost"})

    def test_documents_must_all_come_from_one_revision(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, valid_doc(self.rows["r1"]))
            other = valid_doc(self.rows["r2"])
            other["provenance"]["source_dataset_revision"] = "b" * 40
            write(d, reseal(other))
            with self.assertRaises(p.IntegrityError):
                p.revision_of_documents(d)

    def test_a_subset_is_loaded_when_named_and_the_rest_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, valid_doc(self.rows["r1"]))
            write(d, valid_doc(self.rows["r2"]))
            self.assertEqual(set(p.load_docs(d, self.rows, REV, CSHA, {"r2"})), {"r2"})


class DetectAndClassifyEndToEnd(unittest.TestCase):
    """The commands, with the manifest and downloads replaced by fixtures: documents are written with provenance, accepted only if they
    validate, skipped when they still do and recomputed when they do not."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "local")
        os.makedirs(os.path.join(self.root, "x", "spool"))
        self.rows = []
        for i in range(3):
            raw = window([(300_000.0, 12.0)], seconds=2.048)[: p.EXPECTED_BYTES]
            raw = raw + bytes(p.EXPECTED_BYTES - len(raw))
            with open(os.path.join(self.root, "x", "spool", f"visit_{i:04d}.iq"), "wb") as fh:
                fh.write(raw)
            self.rows.append({"set_id": "setx", "site_key": "siteA", "position": i, "file": f"data/runs/setx/spool/visit_{i:04d}.iq",
                              "sha256": hashlib.sha256(raw).hexdigest(), "centre_hz": 445_000_000.0 + i * 150_000.0, "retune_delta_hz": -100_000.0, "clipping_pct": 0.0})
        self.out = os.path.join(self.tmp.name, "out")
        self.patches = [mock.patch.dict(p.LOCAL_DIRS, {"setx": "x"}), mock.patch.object(p, "load_captures", lambda path, rev: (df_from({"setx": self.rows}), CSHA))]
        for m in self.patches:
            m.start()

    def tearDown(self):
        for m in reversed(self.patches):
            m.stop()
        self.tmp.cleanup()

    def args(self, **kw):
        base = dict(out=self.out, revision=REV, captures=None, sets=None, local_root=self.root, workers=1, remote_only=False)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def run_detect(self, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = p.cmd_detect(self.args(**kw))
        return rc, buf.getvalue()

    def test_a_run_is_processed_then_skipped_then_recomputed_when_tampered(self):
        rc, text = self.run_detect()
        self.assertEqual(rc, 0)
        self.assertIn("3/3 visits verified", text)
        with open(os.path.join(self.out, "setx.json")) as fh:
            d = json.load(fh)
        p.validate_detection_doc(d, self.rows, REV, CSHA)
        rc, text = self.run_detect()
        self.assertIn("skip", text)
        d["visits"][0]["lines"] = [[1.0, 1.0]]
        write(self.out, d)
        rc, text = self.run_detect()
        self.assertIn("recompute", text)
        self.assertIn("3/3 visits verified", text)

    def test_a_corrupt_window_leaves_an_incomplete_document_that_is_refused(self):
        with open(os.path.join(self.root, "x", "spool", "visit_0001.iq"), "r+b") as fh:
            fh.write(b"\x01\x02\x03")                                                # same size, wrong content: sha256 differs
        rc, text = self.run_detect()
        self.assertEqual(rc, 2)
        self.assertIn("INCOMPLETE", text)
        with open(os.path.join(self.out, "setx.json")) as fh:
            d = json.load(fh)
        self.assertFalse(d["provenance"]["integrity_verified"])
        with self.assertRaises(p.IntegrityError):
            p.validate_detection_doc(d, self.rows, REV, CSHA)

    def test_classify_writes_results_with_the_full_provenance_block(self):
        self.run_detect()
        results = os.path.join(self.tmp.name, "results.json")
        with contextlib.redirect_stdout(io.StringIO()):
            p.cmd_classify(types.SimpleNamespace(out=self.out, results=results, captures=None, sets=None))
        with open(results) as fh:
            r = json.load(fh)
        pv = r["provenance"]
        for key in ("analysis_schema_version", "source_dataset_revision", "captures_parquet_sha256", "capture_manifest_sha256", "detection_document_sha256",
                    "analysis_config_sha256", "analysis_code_sha256", "software", "expected_visits", "processed_visits", "integrity_verified"):
            self.assertIn(key, pv)
        self.assertEqual((pv["expected_visits"], pv["processed_visits"], pv["integrity_verified"]), (3, 3, True))
        self.assertEqual(pv["analysis_code_sha256"], p.code_sha256())
        self.assertEqual(pv["source_dataset_revision"], REV)

    def test_classify_refuses_an_unverifiable_document(self):
        self.run_detect()
        with open(os.path.join(self.out, "setx.json")) as fh:
            d = json.load(fh)
        d["visits"] = d["visits"][:1]
        d["provenance"]["expected_visits"] = d["provenance"]["processed_visits"] = 1               # the forged-flags case
        write(self.out, reseal(d))
        with self.assertRaises(p.IntegrityError):
            p.cmd_classify(types.SimpleNamespace(out=self.out, results=os.path.join(self.tmp.name, "r.json"), captures=None, sets=None))


if __name__ == "__main__":
    unittest.main()
