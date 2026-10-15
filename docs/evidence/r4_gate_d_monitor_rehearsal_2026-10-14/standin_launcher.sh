#!/usr/bin/env bash
# Local stand-in for tools/r4_job/run_job.sh's PID-capture idiom and multi-stage child-process shape,
# used only to rehearse the Gate D monitor/launcher handshake (docs/research/metamorphic_r4_gate_d_protocol.md
# section 5/7, Revision 2). This is NOT the r4.1 harness and runs no production code: it exists so the
# rehearsal can exercise the exact "cmd & echo $! > launch.pid" idiom and a process tree that rotates
# through several short-lived Python children, without running the real multi-hour experiment.
#
#   standin_launcher.sh JOB_DIR [STAGE_SECONDS] [STAGE_COUNT]
#
# Writes JOB_DIR/STATUS.txt (one line per stage, timestamped) and exits 0 when all stages complete.
set -u
JOB="$1"
STAGE_SECONDS="${2:-1}"
STAGE_COUNT="${3:-4}"
PY="${PYTHON:-python3}"
mkdir -p "$JOB"
st() { echo "$(date -Is) $*" >> "$JOB/STATUS.txt"; }
st "START pid=$$ py=$PY stages=$STAGE_COUNT stage_seconds=$STAGE_SECONDS"
for i in $(seq 1 "$STAGE_COUNT"); do
  "$PY" -c "import time; time.sleep($STAGE_SECONDS)"
  st "STAGE $i/$STAGE_COUNT complete"
done
st "DONE"
