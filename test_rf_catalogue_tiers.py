import json
import random

import tempfile
import unittest
from pathlib import Path

import rf_catalogue_tiers as T


def _doc(run_id, per_tuning_offsets, sched="s", tuner=1, refused=0, reconciled=True):
    entries = []
    for t, offs in per_tuning_offsets.items():
        for o in offs:
            entries.append({"slope": {"matched_slope": 0, "signed_baseband_hz": [o, o]},
                            "persistence": {"tuning_id": t}})
    return {"run_id": run_id, "n_tunings": len(per_tuning_offsets), "n_visits_refused": refused,
            "reconciliation": {"reconciled": reconciled}, "entries": entries,
            "schedule_digest": sched, "tuner_type": tuner, "chain_digest": "c" + run_id}


def _write(tmp_path, name, doc):
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(doc))
    return p


def _session(tmp_path, name, seed, fixed=(256000.0, -256000.0), n=20, cloud=30, **kw):
    rng = random.Random(seed)
    per = {f"t{i}": list(fixed) + [rng.uniform(-50000, 50000) for _ in range(cloud)] for i in range(n)}
    return T.load_session(name, _write(tmp_path, name, _doc(name, per, **kw)))


class TierTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.tmp = Path(self._d.name)

    def test_fixed_offset_is_intrinsic_and_cloud_is_not(self):
        ss = [_session(self.tmp, f"s{i}", i) for i in range(3)]
        out = T.classify(ss)
        self.assertEqual([round(r["offset_hz"] / 1000) for r in out["intrinsic"]], [-256, 256])
        self.assertEqual(out["everything_else"], T.SESSION)


    def test_dense_cloud_cannot_qualify_by_density(self):
        ss = [_session(self.tmp, f"s{i}", i, fixed=(), cloud=450) for i in range(3)]
        self.assertEqual(T.classify(ss)["intrinsic"], [])


    def test_offset_missing_in_one_session_is_not_intrinsic(self):
        ss = [_session(self.tmp, "a", 1), _session(self.tmp, "b", 2),
              _session(self.tmp, "c", 3, fixed=(256000.0,))]
        self.assertEqual([round(r["offset_hz"] / 1000) for r in T.classify(ss)["intrinsic"]], [256])


    def test_too_few_sessions(self):
        with self.assertRaises(T.TierRefused) as e:
            T.classify([_session(self.tmp, "a", 1), _session(self.tmp, "b", 2)])
        self.assertEqual(e.exception.code, "TOO_FEW_SESSIONS")


    def test_mismatched_schedule_or_tuner_refused(self):
        ss = [_session(self.tmp, "a", 1), _session(self.tmp, "b", 2), _session(self.tmp, "c", 3, sched="x")]
        with self.assertRaises(T.TierRefused) as e:
            T.classify(ss)
        self.assertEqual(e.exception.code, "SCHEDULE_MISMATCH")
        ss = [_session(self.tmp, "a", 1), _session(self.tmp, "b", 2), _session(self.tmp, "c", 3, tuner=5)]
        with self.assertRaises(T.TierRefused) as e:
            T.classify(ss)
        self.assertEqual(e.exception.code, "TUNER_MISMATCH")


    def test_implausible_density_and_incomplete_refused(self):
        with self.assertRaises(T.TierRefused) as e:
            _session(self.tmp, "d", 1, n=2, cloud=T.MAX_ENTRIES_PER_TUNING + 10)
        self.assertEqual(e.exception.code, "SESSION_IMPLAUSIBLE_DENSITY")
        with self.assertRaises(T.TierRefused) as e:
            _session(self.tmp, "e", 1, refused=1)
        self.assertEqual(e.exception.code, "SESSION_NOT_COMPLETE")


    def test_duplicate_label_refused(self):
        s = _session(self.tmp, "a", 1)
        with self.assertRaises(T.TierRefused):
            T.classify([s, s, s])


if __name__ == '__main__':
    unittest.main()
