#!/bin/bash
export SCYTHE_REPO=/home/bgilbert1984/SCYTHE
export SCYTHE_SCRATCH=/home/bgilbert1984/SCYTHE-sweep/521-s-run3-edf2c41a/scratch
export SCYTHE_PYTHON=/home/bgilbert1984/SCYTHE/.venv/bin/python3
export SCYTHE_BRANCH=feat/5.21-controls-s22
cd /home/bgilbert1984/SCYTHE-sweep/521-s-run3-edf2c41a/harness
exec /home/bgilbert1984/SCYTHE/.venv/bin/python3 run_sweep_521.py
