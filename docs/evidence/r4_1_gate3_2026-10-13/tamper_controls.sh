#!/usr/bin/env bash
# Tamper controls on DISPOSABLE copies of the sealed reference job; the reference is never modified.
set -u
P=$PWD/pkg_ok/scythe_r4_1_pkg; PY=$HOME/SCYTHE/.venv/bin/python; REF=$PWD/job_ok
before=$(sha256sum $REF/JOB_SHA256SUMS | cut -d' ' -f1)
run() { # name  mutation-command (operates on $C)
  C=$PWD/tamper_copy; rm -rf "$C"; cp -a "$REF" "$C"; eval "$2"
  $PY -B $P/tools/r4_job/verify_job.py "$C" --require-fixtures > t_out.txt 2>&1; v=$?
  python3 -I indep_inventory_check.py "$C" --expected-fixture-list $P/fixtures/fixture_hashes.sha256 --generated-pin $P/tools/r4_job/generated_artifacts.sha256 --require-fixtures > t_out2.txt 2>&1; i=$?
  echo "$1 | verify_job rc=$v $(grep '^JOB_ACCEPTED' t_out.txt | cut -c1-30) | independent rc=$i | $( [ $v -ne 0 ] && [ $i -ne 0 ] && echo REFUSED || echo "NOT-REFUSED (rc-based)")"
  rm -rf "$C"
}
run "0 unmodified copy (control; must PASS)" ':'
run "1 missing index.json" 'rm $C/holdout/index.json'
run "2 modified index.json" 'sed -i "0,/\"seed\": 1000/s//\"seed\": 1001/" $C/holdout/index.json'
run "3 unexpected file in holdout/" 'echo x > $C/holdout/stray.txt'
run "4 modified .npy fixture" 'python3 -c "
import sys;p=sys.argv[1];f=open(p,\"r+b\");f.seek(123457);b=f.read(1);f.seek(123457);f.write(bytes([b[0]^1]))" "$C/holdout/$(ls $C/holdout | grep npy | sed -n 17p)"'
run "5 STATUS.txt modified after sealing" 'echo "late line" >> $C/STATUS.txt'
after=$(sha256sum $REF/JOB_SHA256SUMS | cut -d' ' -f1)
[ "$before" = "$after" ] && echo "reference manifest unchanged: $after"
