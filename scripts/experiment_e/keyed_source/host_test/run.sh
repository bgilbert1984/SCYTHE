#!/bin/sh
# Build and run the host timing/safety tests for keyed_source.ino. No AVR toolchain needed.
set -e
cd "$(dirname "$0")"
g++ -std=c++17 -Wall -Wno-unused -Istubs -I. -o "${TMPDIR:-/tmp}/ks_sim_$$" sim.cpp
"${TMPDIR:-/tmp}/ks_sim_$$"; rc=$?; rm -f "${TMPDIR:-/tmp}/ks_sim_$$"; exit $rc
