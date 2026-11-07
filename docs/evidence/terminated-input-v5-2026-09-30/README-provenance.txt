Terminated-input captures, NESDR SMArt v5 (serial 14530058) - hash record
Recorded 2026-11-07. This directory holds hashes and a transcription of the
sidecar metadata ONLY. The six .iq files (491,520,000 bytes each) and the six
.json sidecars are NOT in the repo. The sidecars contain a street address
(`site` field) and were deliberately left out.

Where the files were when hashed (this host, scratch):
  /var/tmp/scythe-sweep/thermal_windows/
/var/tmp can be cleaned. Until the files are copied somewhere durable, this
record lets you check a copy; it cannot recover one.

Check: place the 12 files in one directory, then `sha256sum -c SHA256SUMS`.
SHA256SUMS covers both .iq and .json (first-observation SHA-256; the sidecars
themselves carry only md5, listed below and recomputed to match on 2026-11-07).

Common to all six (from the sidecars): host scythe-1; receiver NESDR SMArt v5,
serial 14530058, tuner_type 5; SMA-male 50 ohm terminator (DC-18GHz, 1W);
2,048,000 S/s; uint8 interleaved IQ; nominal 120 s; 491,520,000 bytes;
provenance LOCAL; no enclosure. Gain: undeclared ("rtl_tcp default (no -g flag
in service launch)").

  epoch  centre     elapsed_s  md5 (sidecar)                     stimulus (sidecar)
  1      100.0 MHz  120.13     b00ffc792228b23955b692ad6fb825a5  none stated
  2      100.0 MHz  120.12     5d775501281f28dea84a62b8f4105603  usbipd detach/reattach, no physical touch
  3      100.0 MHz  120.14     af2cc23835c1ff151adde948db6ff02c  laptop sleep + venue move + physical USB reattach
  4      100.0 MHz  118.26     d995d4cf0a7b83757f6b85349882570e  retune baseline, terminator refitted, new site
  5      101.0 MHz  118.08     9c8b06e68b292b00e3621967a37e7753  retune
  6      100.5 MHz  118.40     ee529430c464a83bf43af147e25e192a  retune

Notes:
  - Epochs 4-6 report ~118 s elapsed for the same byte count as epochs 1-3
    (~120 s). Owner's explanation, 2026-11-07: "we optimized". Recorded as
    stated; not verified here.
  - The sidecars' `site` string differs between epochs 1, 2-3 and 4-6
    (consistent with the venue move in epoch 3 and "new site" in epoch 4);
    not copied here.
  - The .json mtimes for epochs 4-6 precede their .iq mtimes by about an
    hour; not investigated.
