Experiment E Stage 0B - dipole 584 mm session (ambient, XTR on scythe-1)
Recorded 2026-11-07. This directory holds hashes, logs and analysis OUTPUT only.
The raw captures (6 x u8 I/Q, 1,720,320,000 B) are NOT in the repo.

Receiver: Nooelec SMArt XTR v5, SN 71874552, E4000 (from rtl_tcp stderr, temp_rtl_tcp.stderr.log).
Settings (capture_log.json, not committed): 2.048 MS/s, u8 interleaved I,Q, direct sampling Q-branch
(rtl_tcp cmd 0x09 = 2), rtl_tcp default gain (no gain command), 2 s discarded after each retune.
Capture tool: scripts/experiment_e/stage0b_recapture.py (sha256 38608f35d144d1416458268761e2fe04101d15736ddb254142473d89b097bb1a,
identical in the repo and on scythe-1). Started 2026-11-07T18:57:21Z, finished 19:04:33Z (10:57-11:04 PST).

Antenna (OWNER-STATED, NOT VERIFIED BY SOFTWARE, 2026-11-07): telescopic dipole fitted on the XTR,
extended to 584 mm, on the magnetic base; standard 6' feedline from the XTR to the base; no enclosure
(standard aluminum case). The owner's site address is in capture_log.json and is deliberately not committed.
The 2026-11-02 re-capture logged its antenna as "NOT VERIFIED" with no statement at all.

How it was run: the production rtl_tcp (scythe-rtl-tcp.service) could not be used. The SCYTHE orchestrator's
SDR++ IQ client (SDRPP_AUTO_START=true, SDRPP_IQ_HOST=100.107.199.57:1234) held the single rtl_tcp client slot
and was not draining it. The service and watchdog were stopped (reported to me in a pasted message from the owner; I did not stop them), a temporary rtl_tcp for the
same device was bound to 127.0.0.1:1234 for the capture, then stopped, and the service and watchdog were restarted.
The orchestrator was not touched.

Captures (stream hash == disk hash for all six):
  q1       12.500 MHz   60 s  66dda79f8f9c99bd76c97c5305d643ad47d76b18e9989071d625083bc503988c
  am        1.000 MHz   60 s  06d1b8ae7bb22b3e9f0eb4f3f781173fa5edac0fe03692f436a4d02958b1bb62
  hf7       7.074 MHz   60 s  0e47e5b93993f1170b8d266ed5c0d802ab2e28e6bfcde2ecd4733328d640bc6f
  hf5       5.000 MHz   60 s  a0406fe772669bdd90e23118ce2a0aa00b65eabf504bfc9bee3d8fcb03ab6fbc
  q2       12.500 MHz   60 s  300e1d75aae40dc2303662c22d8c965c7f438b7cf52c006c477bb2e15afce1b4
  am_120s   1.000 MHz  120 s  3ee83d8f9b32e231dde55e1eebb35c1a4cdd70f6ccc7b37cdd356f488ef9c935

Files here:
  MANIFEST.sha256, MANIFEST_ADDENDUM.{sha256,txt}  - the directory manifest as written, and the addendum for the
      one file (temp_rtl_tcp.stderr.log) that changed 13 s after hashing (rtl_tcp shutdown text). The manifest
      lists capture_log.json, which is not in this repo; its hash is there so a holder of the file can check it.
  SHA256SUMS-archive-contents - sha256 of the 11 files inside the archive (paths as in the tar).
  archive/ - tar.sha256, zst.sha256, tree.txt of stage0b_dipole584_20261107.tar.zst
      (zstd -6 of a deterministic tar: 562,703,052 B for 1,720,324,830 B, 3.06x; zstd -t OK; decompressed
      tar hash == tar hash; extraction compared all 11 files and the tree).
  analysis/ - reanalyze.txt (stage0b_reanalyze.py on am, am_120s), surrogate_am_120s.txt
      (confound_suite.py --surrogate, seed 11, one draw), reanalyze_1102.txt (same script on the 11-02 files),
      compare_1102_vs_1107.txt (scripts/experiment_e/compare_sessions.py, sha256
      fb5425f2e56554b7e7f4f140b5ae9d68b572b12c769769aef899c81de34bd0d0, byte-identical to what produced it).

Locations (2026-11-07): raw set and archive on scythe-1 ~/exp_e_evidence/stage0b_dipole584_20261107[_archive];
archive only on the operator workstation ~/exp_e_evidence_offhost/stage0b_dipole584_20261107_archive (hash-verified).
The raw .u8 files exist only on scythe-1. No clean-machine restore test has been done.
