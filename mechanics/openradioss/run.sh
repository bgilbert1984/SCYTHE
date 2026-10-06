#!/bin/bash
# Documented OpenCourant invocation for generating mechanical truth data.
#
# THIS SCRIPT IS DOCUMENTATION. SCYTHE never executes it; the solver runs
# out-of-tree by hand, and SCYTHE consumes only the exported artifacts
# (CSV via th_to_csv, VTK via anim_to_vtk). Keeping the invocation here
# (rather than tribal knowledge) is what makes a run reproducible.
#
# Pinned solver: /home/bgilbert1984/OpenCourant (commit 33e68517...,
# ARCHIVE_MANIFEST.md). Use the release zip's prebuilt binaries or build
# from the pinned source; record solver_build + solver_commit in provenance.
#
# Typical pipeline:
#
#   1. Starter: reads the deck (.rad), builds the model, writes the
#      restart/engine input.
#        ./starter_linux64_sp -i <deck>.rad -o <run>/
#
#   2. Engine: explicit time integration. MPI variant for large models.
#        ./engine_linux64_ompi_sp -i <run>_0001.rad
#      Produces: .th (time history), animation files.
#
#   3. Time history -> CSV (this is what importers/ consumes):
#        ./th_to_csv_linux64 <run>.th -o <run>_history.csv
#
#   4. Animation -> VTK (optional, for inspection / the globe):
#        ./anim_to_vtk_linux64 <run>.anim -o <run>.vtk
#
#   5. Hash everything (see ../provenance.py), then run:
#        python3 ../importers/openradioss_history.py \
#            <run>_history.csv <phase-center-node-id> provenance.json
#
# Deck layout convention: decks/<name>/<name>.rad + decks/<name>/NOTES.md
# describing what the deck models, its material cards, and its boundary
# conditions. The NOTES.md is hashed as the material/bc digest source.
set -euo pipefail
echo "This script documents the solver invocation; it is not executed by SCYTHE." >&2
exit 1
