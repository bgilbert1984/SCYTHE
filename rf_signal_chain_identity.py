"""The signal-chain identity, and nothing else.

§17 slice 10h, implementing PROMOTION_EXECUTION_CONTRACT.md §13n Amendment O.

This module owns manifest construction, canonical serialization and hashing for
the RF signal chain. It exists because a caller that wanted only the identity
had to import `rf_iq_retention` to get it, and importing that module **executes
its graph**: the IQ ring owner, `threading`, NumPy, the channelizer and the
antenna catalogue all arrive with the hash. A pure function reached through an
impure module is not a pure function -- it is the whole module, with a
convenient name on one of its exports.

So the arithmetic moved here and nothing else changed. **Every digest is
preserved byte-for-byte**, and `SIGNAL_CHAIN_REVISION` does not advance: a move
is not a revision, because nothing about the instrument changed.

**It resolves nothing.** Every value the old builder reached for when an
argument was absent is a required parameter here: the antenna, the feedline,
the mast extension -- which came from `SDRPP_ANTENNA_ID`, `SDRPP_FEEDLINE_ID`
and `SDRPP_ANTENNA_EXTENSION_MM` -- and the feedline length, which came from
`graphops_rf_antenna.FEEDLINES`. `rf_iq_retention` still reads all four, which
is where they belong, and calls this. A length the caller supplies is a length
the caller can be held to.

Its imports are `hashlib`, `json` and `typing`. That is the whole point of it,
and §13n's tests hold it there.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional, Tuple

SIGNAL_CHAIN_SCHEMA = "scythe.rf-signal-chain.v2"
SIGNAL_CHAIN_REVISION = "v3"
# v1 was a positional hash over sensor, antenna, sample type and rate, with no
# feedline. v2 added the feedline. v3 adds the telescopic mast extension, which
# was the last instrument-defining field the chain identity could not see: the
# same mast at 730 mm and at 165 mm is a quarter wave at 102.7 MHz and at
# 454.2 MHz, and until then both produced the same chain hash. No revision is
# rescalable into another and nothing attempts to reinterpret one.
PRIOR_SIGNAL_CHAIN_REVISION_COMPARABLE = False
# Vendor figure for the SMArt v5, carried as a declaration rather than a
# measurement: nothing here has disciplined this oscillator against a reference.
CLOCK_QUALITY = "MODEL_DECLARED_0_5_PPM_TCXO"

UNDECLARED = "UNDECLARED"

# §5.26 point 5, 3c-wire: the clock vocabulary, declared once, in the leaf that
# already owns `UNDECLARED`. `rf_iq_ring` names the clock that stamped a
# window's capture times; `rf_corpus_namespace` names the clock the ownership
# scope acquired when it opened. Both import this one declaration. Two copies
# of one vocabulary is the single-source defect §5.27 spent a slice removing,
# and importing the ring's copy into the namespace would couple the namespace
# to the IQ ring, which it does not otherwise depend on.
#
# `POSIX_REALTIME` is what `time.time` reads. It is named rather than praised:
# it is settable, it is not monotonic, and nothing here has disciplined it
# against a reference. The authority is derived by each owner from the clock
# actually installed -- exactly `time.time` is `POSIX_REALTIME`, and any
# injected or wrapped callable is `UNDECLARED`, including a wrapper that calls
# `time.time` itself. Authority is never inferred from equivalent behaviour.
# The derivation stays with the owners because it needs `time`, and this
# module's imports are held to `hashlib`, `json` and `typing`.
CLOCK_AUTHORITY_POSIX_REALTIME = "POSIX_REALTIME"
CLOCK_AUTHORITIES: Tuple[str, ...] = (CLOCK_AUTHORITY_POSIX_REALTIME, UNDECLARED)


def signal_chain_manifest(*, sensor_id: str, sample_type: str,
                          sample_rate_hz: float, antenna: str, feedline: str,
                          extension_mm: Any, gain_db: Optional[float],
                          feedline_length_m: Optional[float]) -> Dict[str, Any]:
    """Everything the physical and decode path is made of, declared or not.

    A manifest rather than an argument list: the chain grew a feedline the
    moment someone asked what the antenna was plugged into, and it will grow a
    direct-sampling state when that is wired. An expanding positional hash
    input makes every such addition a silent rewrite of what a hash meant,
    whereas a manifest is retained beside its hash and can simply be read.

    Every absence is named. ``UNDECLARED`` is a metadata omission and says so;
    it is never ``UNKNOWN``, which would suggest the system looked and was
    puzzled.

    **Every parameter is required**, and none has a default that would send this
    module looking somewhere for a value. `feedline_length_m` in particular is
    passed rather than looked up: the catalogue that holds it is one of the
    imports §13n O.2 excludes.
    """
    declared_feedline = feedline not in (UNDECLARED, "undeclared")
    # `isinstance(True, (int, float))` is True, so a boolean extension has
    # always been a declared 1.0 or 0.0. That is almost certainly not what an
    # operator meant, and refusing it may well be right -- but §13n O.3 says
    # every existing digest is preserved byte-for-byte, and excluding `bool`
    # here changes one. A refusal is a behaviour change and needs its own
    # amendment; this move does not get to smuggle one in.
    declared_extension = isinstance(extension_mm, (int, float))
    return {
        "schema": SIGNAL_CHAIN_SCHEMA,
        "sensor_id": sensor_id,
        "sample_type": sample_type,
        "sample_rate_hz": float(sample_rate_hz),
        "antenna": {
            "id": antenna if antenna != UNDECLARED else None,
            "authority": ("OPERATOR_DECLARED" if antenna != UNDECLARED
                          else UNDECLARED),
            # A telescopic mast is not one instrument. Its extension sets which
            # frequencies it receives efficiently, so it belongs in the identity
            # of the chain rather than only in the declaration receipt.
            "extension_mm": float(extension_mm) if declared_extension else None,
            "extension_authority": ("OPERATOR_DECLARED" if declared_extension
                                    else str(extension_mm)),
        },
        "feedline": {
            "id": feedline if declared_feedline else None,
            "length_m": feedline_length_m if declared_feedline else None,
            "authority": "OPERATOR_DECLARED" if declared_feedline else UNDECLARED,
        },
        # Declared only once something has actually set it. An automatic-gain
        # receiver has a gain, but not one this process knows, and reporting a
        # number for it would be inventing the instrument's own state.
        "gain": ({"value_db": float(gain_db), "authority": "OPERATOR_DECLARED"}
                 if gain_db is not None else
                 {"value_db": None, "authority": UNDECLARED}),
        "direct_sampling": UNDECLARED,
        # Not a control and not a measurement: this receiver has no bias tee, so
        # there is nothing to switch and nothing to sense.
        "bias_tee": "NOT_FITTED",
        "clock_quality": CLOCK_QUALITY,
    }


def canonical_signal_chain_bytes(manifest: Dict[str, Any]) -> bytes:
    """The bytes that are hashed. Sorted keys and no incidental whitespace, so a
    reordered or reformatted manifest is the same chain and hashes the same."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def signal_chain_hash(*, sensor_id: str, sample_type: str, sample_rate_hz: float,
                      antenna: str, feedline: str, extension_mm: Any,
                      gain_db: Optional[float],
                      feedline_length_m: Optional[float]) -> str:
    """Identity of the physical and decode path the samples came through.

    Deliberately excludes the centre frequency. Retuning does not change the
    chain -- it is its own invalidation reason -- and folding it in here would
    make every retune look like a different antenna.

    It deliberately excludes the declaration note as well: prose about an
    instrument is not part of one, and a reworded comment must never invalidate
    a product.
    """
    manifest = signal_chain_manifest(
        sensor_id=sensor_id, sample_type=sample_type,
        sample_rate_hz=sample_rate_hz, antenna=antenna, feedline=feedline,
        extension_mm=extension_mm, gain_db=gain_db,
        feedline_length_m=feedline_length_m)
    digest = hashlib.blake2s(canonical_signal_chain_bytes(manifest),
                             digest_size=16).hexdigest()
    return f"blake2s:{digest}"
