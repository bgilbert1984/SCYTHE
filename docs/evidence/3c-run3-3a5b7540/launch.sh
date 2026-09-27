#!/bin/bash
export SCYTHE_REPO=/home/bgilbert1984/SCYTHE
export SCYTHE_SCRATCH=/home/bgilbert1984/SCYTHE-sweep/3c-k-run3-3a5b7540/scratch
export SCYTHE_BRANCH=feat/5.20-3c-wire
cd /home/bgilbert1984/SCYTHE-sweep/3c-k-run3-3a5b7540/harness
exec python3 run_sweep_3c.py
