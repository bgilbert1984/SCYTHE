#!/usr/bin/env bash
# r4 one-shot detached job. NOT AUTHORIZED TO RUN until a separate authorization gate.
# Order of custody (corrects r3's STATUS_LOG_APPEND_AFTER_HASH): terminal status record -> exit codes -> flush -> seal -> NOTHING written after.
# The terminal record is DONE only if the harness AND its gate both succeeded; any failure is FAILED, with both return codes preserved.
# The fixture hash list is copied into the job (and so sealed); the fixture bytes are re-verified against it at seal time.
# Stages: package verify -> bounded controls (counted) -> fixtures -> generated-artifact pin -> smoke -> smoke gate -> full run -> full gate -> terminal record -> seal.
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
# finalize COMPUTATION: seal (last), read the manifest back, verify independently, and report three SEPARATE dispositions OUTSIDE the job directory ($JOB.disposition.txt).
# DONE is never JOB_ACCEPTED: COMPUTATION_COMPLETED (harness+gate), EVIDENCE_SEALED (manifest published and read back), JOB_ACCEPTED (verify_job.py PASS and terminal DONE).
finalize() {
  local req="--require-fixtures" sealout verout src=0 vrc=0
  sealout=$($PY tools/r4_job/seal_job.py "$JOB" --require-fixtures 2>&1) || { req=""; sealout=$($PY tools/r4_job/seal_job.py "$JOB" 2>&1); src=$?; }
  if [ "$src" -eq 0 ]; then verout=$($PY tools/r4_job/verify_job.py "$JOB" $req 2>&1); vrc=$?; else verout="verification not run: the job was not sealed"; vrc=1; fi
  { echo "COMPUTATION_COMPLETED: $1"
    if [ "$src" -eq 0 ] && echo "$sealout" | grep -q '^EVIDENCE_SEALED: YES'; then echo "EVIDENCE_SEALED: YES"; else echo "EVIDENCE_SEALED: NO"; src=1; fi
    if [ "$vrc" -eq 0 ] && echo "$verout" | grep -q '^JOB_ACCEPTED: YES'; then echo "JOB_ACCEPTED: YES"; else echo "JOB_ACCEPTED: NO"; vrc=1; fi
    echo "--- sealer"; echo "$sealout"; echo "--- independent verifier"; echo "$verout"; } > "$JOB.disposition.txt"
  cat "$JOB.disposition.txt"
  [ "$1" = YES ] && [ "$src" -eq 0 ] && [ "$vrc" -eq 0 ]
}
fail() { st "FAILED $1"; echo "$1 exit=$2" >> "$JOB/exit_codes.txt"; sync "$JOB/exit_codes.txt" 2>/dev/null; finalize NO; exit "$2"; }
sha256sum -c SHA256SUMS > "$JOB/logs/package_verify.txt" 2>&1 || fail "package hash verification" 10
st "OK package verified"
$PY -m unittest test_rf_metamorphic_controls test_rf_metamorphic_r4_controls test_rf_metamorphic_r4_production_path test_rf_metamorphic_r4_job > "$JOB/logs/controls.txt" 2>&1 || fail "bounded controls" 11
st "OK bounded controls ($(grep -E '^Ran ' "$JOB/logs/controls.txt"))"
$PY fixtures/generate_holdout.py --spec fixtures/synth_521_holdout_frozen.json --outdir "$JOB/holdout" > "$JOB/logs/fixtures_generate.txt" 2>&1 || fail "fixture generation" 12
( cd "$JOB/holdout" && sha256sum -c "$PKG/fixtures/fixture_hashes.sha256" ) > "$JOB/logs/fixtures_verify.txt" 2>&1 || fail "fixture hash verification" 13
st "OK fixtures regenerated, $(grep -c ': OK$' "$JOB/logs/fixtures_verify.txt")/$(grep -c . "$PKG/fixtures/fixture_hashes.sha256") hashes verified"
G="$PKG/tools/r4_job/generated_artifacts.sha256"
( cd "$JOB/holdout" && sha256sum -c "$G" ) > "$JOB/logs/generated_verify.txt" 2>&1 || fail "generated artifact (index.json) hash verification" 16
st "OK generated artifacts: $(grep -c ': OK$' "$JOB/logs/generated_verify.txt")/$(grep -c . "$G") pinned digests verified"
H="$PKG/fixtures/fixture_hashes.sha256"; cp "$H" "$JOB/fixture_hashes.sha256"; cp "$G" "$JOB/generated_artifacts.sha256"
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
  st "DONE COMPUTATION_COMPLETED harness_rc=$rc gate_rc=$grc (this is not JOB_ACCEPTED; outputs retained until sealed and independently verified)"
  comp=YES
else
  st "FAILED harness_rc=$rc gate_rc=$grc (the run did not complete successfully; outputs retained as evidence of the failure)"
  comp=NO
fi
finalize "$comp"; fin=$?
[ "$comp" = YES ] && [ "$fin" -ne 0 ] && exit 20
exit $(( rc != 0 || grc != 0 ))
