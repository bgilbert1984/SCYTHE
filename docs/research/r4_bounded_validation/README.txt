NON-EVIDENTIARY bounded validation of the r4 implementation (not the r4 experiment).
One pinned fixture (HA_s+f0.05a0.075), run with --boundary-only: identity/namespace, Layer A, Layer B' (12 synthetic cases), Layer B (8 cases).
run_header.json and MANIFEST.sha256 are omitted because they record local absolute paths; the gate verified all 5 manifest entries before they were left out.
Every row is function-level (a direct detect_candidates call); none is production-path coverage. The production-path test is test_rf_metamorphic_r4_production_path.py.
Regenerate:  python3 tools/rf_metamorphic_harness.py --holdout DIR --hashes FILE --results-dir NEW --limit 1 --boundary-only
