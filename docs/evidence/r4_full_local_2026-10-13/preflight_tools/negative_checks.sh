#!/usr/bin/env bash
# Negative controls for r4_preflight.py: each must FAIL (or refuse) for the named reason and must never create the job directory.
ARC=$HOME/r41_package_build/final/scythe_r4_1_pkg_8d34de9ac0b1.tar.gz; PY=$HOME/SCYTHE/.venv/bin/python; REPO=$HOME/SCYTHE-r4decl
JOB=$HOME/r4_full_run_job_20261013; B=$HOME/r4_full_run_preflight
run() { # name, expect-substring, then preflight args
  name=$1; expect=$2; shift 2; out=$(python3 $B/r4_preflight.py --mode CHECK_ONLY "$@" 2>&1); rc=$?
  hit=$(echo "$out" | grep -cE "$expect"); [ -e $JOB ] && created=JOB_DIR_CREATED || created=job_dir_absent
  echo "$name | rc=$rc | expected-failure-line-present=$hit | $created | $( [ $rc -ne 0 ] && [ $hit -ge 1 ] && echo REFUSED || echo NOT-REFUSED)"
}
SYSPY=$(command -v python3.12 || command -v python3)
run "wrong interpreter (no NumPy 2.5.1 venv)" "^FAIL    2b" --archive $ARC --python $SYSPY --job-dir $JOB --attempt-dir $B/neg_1 --repo $REPO
run "job directory already exists" "^FAIL    3 " --archive $ARC --python $PY --job-dir /tmp --attempt-dir $B/neg_2 --repo $REPO
cp $ARC /tmp/tampered_pkg.tar.gz; cp $ARC.sha256 /tmp/tampered_pkg.tar.gz.sha256; printf '\x01' | dd of=/tmp/tampered_pkg.tar.gz bs=1 seek=5000 conv=notrunc 2>/dev/null
run "tampered archive" "^FAIL    1 " --archive /tmp/tampered_pkg.tar.gz --python $PY --job-dir $JOB --attempt-dir $B/neg_3 --repo $REPO
run "relative PYTHON path" "absolute" --archive $ARC --python .venv/bin/python --job-dir $JOB --attempt-dir $B/neg_4 --repo $REPO
python3 $B/r4_preflight.py --mode LAUNCH --archive $ARC --python $PY --job-dir $JOB --attempt-dir $B/neg_5 --repo $REPO >/dev/null 2>&1; echo "mode LAUNCH | rc=$? | $( [ -e $JOB ] && echo JOB_DIR_CREATED || echo job_dir_absent) | $( [ -e $B/neg_5 ] && echo RECORD_CREATED || echo no_record_written)"
bash -c 'exec -a run_job.sh sleep 40' & sp=$!; sleep 1
run "competing r4 job process" "^FAIL    6" --archive $ARC --python $PY --job-dir $JOB --attempt-dir $B/neg_6 --repo $REPO
kill $sp 2>/dev/null; rm -f /tmp/tampered_pkg.tar.gz /tmp/tampered_pkg.tar.gz.sha256
