"""What each RF configuration change is permitted and required to do.

Domain contracts for ``scythe_invariant_ledger``. The ledger knows nothing
about RF; this module says which coordinates an RF operation may move, which it
must move, and which claims it may not assert. It computes nothing and mutates
nothing.

The signatures are built from the **real hash functions** -- ``signal_chain_hash``
from ``rf_iq_retention`` and ``instrument_hash`` / ``declaration_hash`` from
``graphops_rf_antenna`` -- rather than from restated values. A contract that
said "the signal chain must change" while comparing numbers this module made up
would be checking itself. Building the signature through the same code the
bridge uses means the check is against the implementation.

Why "must change" is the interesting half
-----------------------------------------
Ordinary integrity checking asks whether something moved that should not have.
The failure this repository actually hit was the other one: a telescopic mast
retracted from 730 mm to 165 mm keeps its ``antenna_id`` while its frequency
response moves from the FM broadcast span to the 433 MHz ISM band. The
declaration hash knew; the comparability boundary stayed silent. **A change that
moves a hash and raises no boundary is a signal-chain change that produces no
complaint** -- and only a required-transition rule catches it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from graphops_rf_antenna import (
    DECLARATION_HASH_FIELDS, declaration_hash, instrument_hash,
)
from rf_iq_retention import signal_chain_hash
from scythe_invariant_ledger import (
    Coordinate, TransitionContract, signature,
)


SCHEMA = "scythe.rf-product-transitions.v1"

# Claims no configuration change establishes. A declaration is bookkeeping; a
# resonance and a calibrated response are measurements, and a receive-only path
# has no reflectometer with which to make either.
NEVER_ESTABLISHED_BY_A_DECLARATION: Tuple[str, ...] = (
    "RESONANCE_MEASURED",
    "RESPONSE_AT_TUNE_CALIBRATED",
    "ANTENNA_AUTO_DETECTED",
)

# Coordinates carried on every RF product signature.
SIGNATURE_FIELDS: Tuple[str, ...] = (
    "sensor_id", "sample_type", "sample_rate_hz",
    "antenna_id", "feedline_id", "extension_mm", "gain_db",
    "center_frequency_hz", "configuration_epoch",
    "signal_chain_hash", "instrument_hash", "declaration_hash",
    "quarter_wave_reference_hz",
)


def _quarter_wave_hz(extension_mm: Optional[float]) -> Any:
    """Ideal free-space quarter wave, or an explicit absence.

    A derived geometric reference and nothing more: it does not establish that
    the antenna is resonant there, which is why it travels beside the geometry
    rather than as a property of it.
    """
    if extension_mm in (None, ""):
        return Coordinate("ABSENT")
    return round(299_792_458.0 / (4.0 * (float(extension_mm) / 1000.0)))


def product_signature(*, sensor_id: str, sample_type: str, sample_rate_hz: float,
                      antenna_id: str, feedline_id: str,
                      extension_mm: Optional[float], gain_db: Optional[float],
                      center_frequency_hz: float, configuration_epoch: int,
                      authority: str = "OPERATOR_DECLARED",
                      extension_authority: str = "OPERATOR_ESTIMATED",
                      note: str = "",
                      claims: Tuple[str, ...] = ()) -> Dict[str, Coordinate]:
    """One RF product's signature, hashed by the code the bridge itself uses."""
    record = {"antenna_id": antenna_id, "feedline_id": feedline_id,
              "extension_mm": extension_mm, "authority": authority,
              "extension_authority": extension_authority, "note": note}
    missing = set(DECLARATION_HASH_FIELDS) - set(record)
    if missing:                                            # pragma: no cover
        raise ValueError(f"declaration record is missing {sorted(missing)}")
    return signature(
        sensor_id=sensor_id, sample_type=sample_type, sample_rate_hz=sample_rate_hz,
        antenna_id=antenna_id, feedline_id=feedline_id,
        extension_mm=extension_mm if extension_mm is not None else Coordinate("ABSENT"),
        gain_db=gain_db if gain_db is not None else Coordinate("ABSENT"),
        center_frequency_hz=center_frequency_hz,
        configuration_epoch=configuration_epoch,
        signal_chain_hash=signal_chain_hash(
            sensor_id=sensor_id, sample_type=sample_type,
            sample_rate_hz=sample_rate_hz, antenna=antenna_id,
            feedline=feedline_id, extension_mm=extension_mm, gain_db=gain_db),
        instrument_hash=instrument_hash(record),
        declaration_hash=declaration_hash(record),
        quarter_wave_reference_hz=_quarter_wave_hz(extension_mm),
        claims=tuple(claims),
    )


# -- the transitions ------------------------------------------------------

# Extending or retracting a telescopic mast. The instrument changes, and so
# does everything derived from it: this is the transition whose silence was the
# defect. The epoch must advance because products either side of it were taken
# through a different frequency response.
DECLARE_ANTENNA_EXTENSION = TransitionContract(
    name="DECLARE_ANTENNA_EXTENSION",
    must_preserve=("sensor_id", "sample_type", "sample_rate_hz",
                   "antenna_id", "feedline_id", "center_frequency_hz", "gain_db"),
    must_change=("extension_mm", "instrument_hash", "signal_chain_hash",
                 "configuration_epoch"),
    may_change=("declaration_hash", "quarter_wave_reference_hz"),
    prohibited_claims=NEVER_ESTABLISHED_BY_A_DECLARATION,
)

# Swapping the mast itself.
DECLARE_ANTENNA = TransitionContract(
    name="DECLARE_ANTENNA",
    must_preserve=("sensor_id", "sample_type", "sample_rate_hz",
                   "feedline_id", "center_frequency_hz", "gain_db"),
    must_change=("antenna_id", "instrument_hash", "signal_chain_hash",
                 "configuration_epoch"),
    may_change=("extension_mm", "declaration_hash", "quarter_wave_reference_hz"),
    prohibited_claims=NEVER_ESTABLISHED_BY_A_DECLARATION,
)

# Changing the cable. A mast on 2 m of RG58 is not the same signal chain as the
# same mast on the SMA port, which is why feedline is in the comparability set.
DECLARE_FEEDLINE = TransitionContract(
    name="DECLARE_FEEDLINE",
    must_preserve=("sensor_id", "sample_type", "sample_rate_hz",
                   "antenna_id", "extension_mm", "center_frequency_hz", "gain_db",
                   # The quarter wave derives from the geometry alone, and the
                   # geometry is preserved here. Leaving it ungoverned would let
                   # it move without complaint.
                   "quarter_wave_reference_hz"),
    must_change=("feedline_id", "instrument_hash", "signal_chain_hash",
                 "configuration_epoch"),
    may_change=("declaration_hash",),
    prohibited_claims=NEVER_ESTABLISHED_BY_A_DECLARATION,
)

# Moving the tuner. The instrument is untouched: a retune changes where the
# receiver is listening, not what it is listening through. The instrument hash
# must therefore NOT move, which is the rule that stops a retune from being
# recorded as an apparatus change.
RETUNE = TransitionContract(
    name="RETUNE",
    must_preserve=("sensor_id", "sample_type", "sample_rate_hz",
                   "antenna_id", "feedline_id", "extension_mm", "gain_db",
                   "instrument_hash", "signal_chain_hash", "declaration_hash",
                   "quarter_wave_reference_hz"),
    must_change=("center_frequency_hz", "configuration_epoch"),
    prohibited_claims=NEVER_ESTABLISHED_BY_A_DECLARATION,
)

# Gain is in the hashed signal chain but not in the instrument: it changes what
# arrives at the ADC without changing what the RF passed through.
CHANGE_GAIN = TransitionContract(
    name="CHANGE_GAIN",
    must_preserve=("sensor_id", "sample_type", "sample_rate_hz",
                   "antenna_id", "feedline_id", "extension_mm",
                   "center_frequency_hz", "instrument_hash", "declaration_hash",
                   "quarter_wave_reference_hz"),
    must_change=("gain_db", "signal_chain_hash", "configuration_epoch"),
    prohibited_claims=NEVER_ESTABLISHED_BY_A_DECLARATION,
)

# The rate labels every frequency in the trace, so a change to it re-labels
# every product taken under the old one.
CHANGE_SAMPLE_RATE = TransitionContract(
    name="CHANGE_SAMPLE_RATE",
    must_preserve=("sensor_id", "sample_type", "antenna_id", "feedline_id",
                   "extension_mm", "gain_db", "center_frequency_hz",
                   "instrument_hash", "declaration_hash",
                   "quarter_wave_reference_hz"),
    must_change=("sample_rate_hz", "signal_chain_hash", "configuration_epoch"),
    prohibited_claims=NEVER_ESTABLISHED_BY_A_DECLARATION,
)

CONTRACTS: Dict[str, TransitionContract] = {
    contract.name: contract for contract in (
        DECLARE_ANTENNA_EXTENSION, DECLARE_ANTENNA, DECLARE_FEEDLINE,
        RETUNE, CHANGE_GAIN, CHANGE_SAMPLE_RATE)
}


def transitions_status() -> Dict[str, Any]:
    """The contracts, published so a reader can enumerate them."""
    return {
        "schema": SCHEMA,
        "ledger": "scythe.invariant-ledger.v1",
        "transitions": sorted(CONTRACTS),
        "signature_fields": list(SIGNATURE_FIELDS),
        "hash_authority": {
            "signal_chain_hash": "rf_iq_retention.signal_chain_hash",
            "instrument_hash": "graphops_rf_antenna.instrument_hash",
            "declaration_hash": "graphops_rf_antenna.declaration_hash",
        },
        "hashes_are_computed_not_restated": True,
        "never_established_by_a_declaration": list(NEVER_ESTABLISHED_BY_A_DECLARATION),
        "quarter_wave_note": (
            "A DERIVED GEOMETRIC REFERENCE. IT DOES NOT ESTABLISH THAT THE "
            "ANTENNA IS RESONANT THERE, AND A RECEIVE-ONLY PATH CANNOT"),
        "mutates": False,
        "side_effects": "NONE",
    }
