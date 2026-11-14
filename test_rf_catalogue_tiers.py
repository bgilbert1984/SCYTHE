import json
import random

import pytest

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


def test_fixed_offset_is_intrinsic_and_cloud_is_not(tmp_path):
    ss = [_session(tmp_path, f"s{i}", i) for i in range(3)]
    out = T.classify(ss)
    assert [round(r["offset_hz"] / 1000) for r in out["intrinsic"]] == [-256, 256]
    assert out["everything_else"] == T.SESSION


def test_dense_cloud_cannot_qualify_by_density(tmp_path):
    ss = [_session(tmp_path, f"s{i}", i, fixed=(), cloud=450) for i in range(3)]
    assert T.classify(ss)["intrinsic"] == []


def test_offset_missing_in_one_session_is_not_intrinsic(tmp_path):
    ss = [_session(tmp_path, "a", 1), _session(tmp_path, "b", 2),
          _session(tmp_path, "c", 3, fixed=(256000.0,))]
    assert [round(r["offset_hz"] / 1000) for r in T.classify(ss)["intrinsic"]] == [256]


def test_too_few_sessions(tmp_path):
    with pytest.raises(T.TierRefused) as e:
        T.classify([_session(tmp_path, "a", 1), _session(tmp_path, "b", 2)])
    assert e.value.code == "TOO_FEW_SESSIONS"


def test_mismatched_schedule_or_tuner_refused(tmp_path):
    ss = [_session(tmp_path, "a", 1), _session(tmp_path, "b", 2), _session(tmp_path, "c", 3, sched="x")]
    with pytest.raises(T.TierRefused) as e:
        T.classify(ss)
    assert e.value.code == "SCHEDULE_MISMATCH"
    ss = [_session(tmp_path, "a", 1), _session(tmp_path, "b", 2), _session(tmp_path, "c", 3, tuner=5)]
    with pytest.raises(T.TierRefused) as e:
        T.classify(ss)
    assert e.value.code == "TUNER_MISMATCH"


def test_implausible_density_and_incomplete_refused(tmp_path):
    with pytest.raises(T.TierRefused) as e:
        _session(tmp_path, "d", 1, n=2, cloud=T.MAX_ENTRIES_PER_TUNING + 10)
    assert e.value.code == "SESSION_IMPLAUSIBLE_DENSITY"
    with pytest.raises(T.TierRefused) as e:
        _session(tmp_path, "e", 1, refused=1)
    assert e.value.code == "SESSION_NOT_COMPLETE"


def test_duplicate_label_refused(tmp_path):
    s = _session(tmp_path, "a", 1)
    with pytest.raises(T.TierRefused):
        T.classify([s, s, s])
