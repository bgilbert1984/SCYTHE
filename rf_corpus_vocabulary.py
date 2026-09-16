"""Where a window came from. Two words, owned by neither side that uses them.

A capture plan names the source of every stratum's trials; `rf_null_corpus`
labels every window it builds. Those are the same distinction, so there is one
declaration -- but ownership follows the concept rather than the import graph,
and neither module is where this belongs.

This exists because the mechanical name check in
`test_scythe_verdict_vocabularies` refused two copies, and the first fix made
`rf_null_corpus` import the whole promotion-envelope module to obtain two
strings. One declaration was right; that route to it was not.
"""

from __future__ import annotations

from typing import Tuple

SYNTHETIC = "SYNTHETIC"
CAPTURED = "CAPTURED"
WINDOW_SOURCES: Tuple[str, ...] = (SYNTHETIC, CAPTURED)
