#!/bin/bash
export SCYTHE_REPO=/home/bgilbert1984/SCYTHE
export SCYTHE_SCRATCH=/home/bgilbert1984/SCYTHE-sweep/3c-k-run4-16f5e51a/scratch
export SCYTHE_PYTHON=/home/bgilbert1984/SCYTHE/.venv/bin/python3
export SCYTHE_BRANCH=main
cd /home/bgilbert1984/SCYTHE-sweep/3c-k-run4-16f5e51a/harness
exec /home/bgilbert1984/SCYTHE/.venv/bin/python3 run_sweep_3c.py
