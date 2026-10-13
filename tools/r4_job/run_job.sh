#!/usr/bin/env bash
# r4 one-shot detached job. NOT AUTHORIZED TO RUN until a separate authorization gate.
# Order of custody (corrects r3's STATUS_LOG_APPEND_AFTER_HASH): terminal status record -> exit codes -> flush -> seal -> NOTHING written after.
# The terminal record is DONE only if the harness AND its gate both succeeded; any failure is FAILED, with both return codes preserved.
# The fixture hash list is copied into the job (and so sealed); the fixture bytes are re-verified against it at seal time.
# Stages: package verify -> bounded controls (counted) -> fixtures -> smoke -> smoke gate -> full run -> full gate -> terminal record -> seal.
set -u
PKG="$(cd "$(dirname "$0")/../.." && pwd)"; JOB="$1"; PY="${PYTHON:-$HOME/SCYTHE/.venv/bin/python}"
[ -e "$JOB" ] && { echo "job dir exists: $JOB" >&2; exit 2; }
mkdir -p "$JOB/logs" || exit 2
st() { echo "$(date -Is) $*" >> "$JOB/STATUS.txt"; sync "$JOB/STATUS.txt" 2>/dev/null; }
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
cd "$PKG" || exit 2
st "START job=$JOB pkg=$PKG pid=$$"
{ echo "uname: $(uname -a)"; echo "python: $($PY -c 'import sys,numpy;print(sys.version.replace(chr(10)," "),"numpy",numpy.__version__)')"
  echo "threads: OPENBLAS=$OPENBLAS_NUM_THREADS OMP=$OMP_NUM_THREADS MKL=$MKL_NUM_THREADS NUMEXPR=$NUMEXPR_NUM_THREADS"; echo "nproc: $(nproc)"; df -h "$JOB" | tail -1; } > "$JOB/env.txt" 2>&1
fail() { st "FAILED $1"; echo "$1 exit=$2" >> "$JOB/exit_codes.txt"; sync "$JOB/exit_codes.txt" 2>/dev/null; $PY tools/r4_job/seal_job.py "$JOB" --require-fixtures || $PY tools/r4_job/seal_job.py "$JOB"; exit "$2"; }
sha256sum -c SHA256SUMS > "$JOB/logs/package_verify.txt" 2>&1 || fail "package hash verification" 10
st "OK package verified"
$PY -m unittest test_rf_metamorphic_controls test_rf_metamorphic_r4_controls test_rf_metamorphic_r4_production_path test_rf_metamorphic_r4_job > "$JOB/logs/controls.txt" 2>&1 || fail "bounded controls" 11
st "OK bounded controls ($(grep -E '^Ran ' "$JOB/logs/controls.txt"))"
$PY fixtures/generate_holdout.py --spec fixtures/synth_521_holdout_frozen.json --outdir "$JOB/holdout" > "$JOB/logs/fixtures_generate.txt" 2>&1 || fail "fixture generation" 12
( cd "$JOB/holdout" && sha256sum -c "$PKG/fixtures/fixture_hashes.sha256" ) > "$JOB/logs/fixtures_verify.txt" 2>&1 || fail "fixture hash verification" 13
st "OK fixtures regenerated, $(grep -c ': OK$' "$JOB/logs/fixtures_verify.txt")/$(grep -c . "$PKG/fixtures/fixture_hashes.sha256") hashes verified"
H="$PKG/fixtures/fixture_hashes.sha256"; cp "$H" "$JOB/fixture_hashes.sha256"
$PY tools/rf_metamorphic_harness.py --holdout "$JOB/holdout" --hashes "$H" --results-dir "$JOB/results_smoke" --limit 2 --mutant-fixtures 2 > "$JOB/logs/smoke.log" 2>&1 || fail "smoke run" 14
$PY tools/r4_job/gate_results.py "$JOB/results_smoke" 2 2 > "$JOB/logs/smoke_gate.txt" 2>&1 || fail "smoke gate" 15
st "OK smoke gate: $(head -1 "$JOB/logs/smoke_gate.txt")"
st "BEGIN full run (36 fixtures, mutants on first 6)"
$PY tools/rf_metamorphic_harness.py --holdout "$JOB/holdout" --hashes "$H" --results-dir "$JOB/results_full" --mutant-fixtures 6 > "$JOB/logs/full.log" 2>&1
rc=$?; echo "full exit=$rc" >> "$JOB/exit_codes.txt"
$PY tools/r4_job/gate_results.py "$JOB/results_full" 36 6 > "$JOB/logs/full_gate.txt" 2>&1
grc=$?; echo "full gate exit=$grc" >> "$JOB/exit_codes.txt"
# 1. terminal status record (flushed), 2. seal the manifest LAST, 3. write nothing to the job directory afterwards
if [ "$rc" -eq 0 ] && [ "$grc" -eq 0 ]; then
  st "DONE harness_rc=$rc gate_rc=$grc (outputs retained on this host until independently verified)"
else
  st "FAILED harness_rc=$rc gate_rc=$grc (the run did not complete successfully; outputs retained as evidence of the failure)"
fi
$PY tools/r4_job/seal_job.py "$JOB" --require-fixtures || exit 20
exit $(( rc != 0 || grc != 0 ))
