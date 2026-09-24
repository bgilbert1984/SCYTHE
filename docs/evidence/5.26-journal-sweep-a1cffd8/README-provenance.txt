§5.26 membership journal sweep, run 2, checkpoint a1cffd8b2bf9bcc9d9b25f79ed0c23941d0ef35e
host dedirock, 2026-09-24, launched 09:46:11, 27 controls J1..J27
bundle: scythe-bundle-a1cffd8.tar.gz sha256 190c749c470b00e6017608c167bbd96858e568c9d1ba9133dc58e1ee1e65c56f (verified; SHA256SUMS clean)
as received: harness-as-received-0940/   folded here: harness/   fold diff: harness-dedirock-0940.patch
fold: control_timeout (floor 1800, 10x baseline), per-row timeout_s, baseline bound 4x, gate 2245 + expected=2245,
      reviewer TIMED_OUT flag, selftest S8-timeout / S10-gate, BUILD build3br->build3bc (local worktree)
selftest: HARNESS ACCEPTANCE PASS 09:45:42
BASELINE  ZERO_DISCRIMINATION  2245 tests  exit 0  68.0s ; TIMEOUT bound 1800s
result: 27/27 TEST_FAILURE, zeros none, duplicates none, not measured none, collection uniform 2245, suppression none
VERDICT: every control discriminated
jsonl sha256: dfc85921ac1cc5f8
