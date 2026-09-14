"""Synthetic null windows for the Phase 3 corpus, and nothing else.

Implements `docs/RF_Signal_Family_Classifier_Scope.md` §5.19, accepted
2026-09-14 alongside §5.18.

> **A generator and a receiver are two sources of windows, and a corpus that
> cannot tell them apart is a corpus that cannot be audited.**

What this module does: build labelled synthetic windows for the nine strata a
generator can honestly produce, deterministically from a seed.

What it does not do, and has no code for:

  **It never acquires.** No device, no socket, no `rtl_tcp`, no bridge. The
  strata that need a real receiver -- `GAIN_STEPS`, `RETUNE_TRANSIENTS`,
  `RECEIVER_SPURS` -- are refused here by name, not omitted quietly.

  **It never writes.** Not even synthetic windows. A synthetic window is
  reproducible from its seed and configuration, so persisting one buys nothing
  a `SyntheticWindow` does not already carry -- and §5.19 authorises generation
  and labelling, which is not the same as authorising a corpus on disk. §5.20
  is where persistence gets decided, and it is unwritten.

  **It has no generic writer.** There is no `write(payload)` and no `emit(kind,
  data)`. Real-window ingestion, when §5.20 permits it, gets its own entry
  points; one function taking both sources is the hole that blurs the authority
  boundary, which is the shape §13k L.1 refused in the derived-evidence
  producer for exactly this reason.

  **It never substitutes.** A tuner stratum with no tuner is absent, and
  `plan_state` reports it absent. Filling it with synthesis would be the
  substitution every refusal in this repository exists to prevent.

Window counts come from the declared stratum plan -- `rf_validation_manifest`'s
`STRATA` and the minima §5.18 set -- never from a constant transcribed here. A
test may inject a smaller plan, which is what keeps a suite from generating
66 732 windows to assert a shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple

import numpy as np

from rf_validation_manifest import STRATA, STRATUM_KEYS

SCHEMA = "scythe.rf-null-corpus.v1"
GENERATOR_REVISION = "synthetic-null.v1"

# -- where a window came from ---------------------------------------------
#
# Two members, and they are not interchangeable. A window says which it is, and
# nothing in this module can produce the second one.
SYNTHETIC = "SYNTHETIC"
CAPTURED = "CAPTURED"
WINDOW_SOURCES: Tuple[str, ...] = (SYNTHETIC, CAPTURED)

# -- which strata a generator can honestly build ---------------------------
#
# Declared here rather than on `Stratum`, because this is a fact about what the
# generator can do and not about what the gate requires. A better synthesiser
# would move a key across this line without the validation manifest changing at
# all -- and the strata set is inside `PromotionCorpusLock`, which is not a
# thing to disturb for a capability note.
#
# The three on the right are §5.19's, and they are refused by name:
#
#   GAIN_STEPS        a real tuner gain change, per §5.12's wiring
#   RETUNE_TRANSIENTS a real retune, with whatever the hardware does across it
#   RECEIVER_SPURS    internal spurious products, which are a property of the
#                     actual receiver and cannot be invented
TUNER_REQUIRED: Tuple[str, ...] = (
    "GAIN_STEPS", "RETUNE_TRANSIENTS", "RECEIVER_SPURS",
)
SYNTHESISABLE: Tuple[str, ...] = tuple(
    key for key in STRATUM_KEYS if key not in TUNER_REQUIRED)

# -- refusals ---------------------------------------------------------------
STRATUM_NOT_SYNTHESISABLE = "STRATUM_NOT_SYNTHESISABLE"
STRATUM_NOT_DECLARED = "STRATUM_NOT_DECLARED"
GENERATOR_CONFIG_REFUSED = "GENERATOR_CONFIG_REFUSED"
CORPUS_REFUSALS: Tuple[str, ...] = (
    STRATUM_NOT_SYNTHESISABLE, STRATUM_NOT_DECLARED, GENERATOR_CONFIG_REFUSED,
)

# -- plan states ------------------------------------------------------------
STRATUM_SYNTHESISED = "STRATUM_SYNTHESISED"
STRATUM_AWAITING_CAPTURE = "STRATUM_AWAITING_CAPTURE"
PLAN_STATES: Tuple[str, ...] = (STRATUM_SYNTHESISED, STRATUM_AWAITING_CAPTURE)

CORPUS_INCOMPLETE_AWAITING_CAPTURE = "CORPUS_INCOMPLETE_AWAITING_CAPTURE"


class CorpusRefused(RuntimeError):
    """A window that was not built. A code, never a repair."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class GeneratorConfig:
    """Everything that decides what the samples are.

    Frozen, and hashed into every window: a synthetic window whose configuration
    cannot be recovered is indistinguishable from a captured one that lost its
    provenance, which is the confusion §5.19 exists to prevent.
    """

    seed: int
    sample_rate_hz: float = 2_048_000.0
    window_samples: int = 262_144          # 128 ms at 2.048 MHz
    noise_power: float = 1.0

    def __post_init__(self) -> None:
        if type(self.seed) is not int or isinstance(self.seed, bool) \
                or not 0 <= self.seed < 2 ** 32:
            raise CorpusRefused(GENERATOR_CONFIG_REFUSED,
                                "seed is an integer in [0, 2**32)")
        if not (0.0 < float(self.sample_rate_hz) <= 20e6):
            raise CorpusRefused(GENERATOR_CONFIG_REFUSED,
                                "sample rate outside a plausible range")
        if type(self.window_samples) is not int or self.window_samples < 1024:
            raise CorpusRefused(GENERATOR_CONFIG_REFUSED,
                                "a window under 1024 samples resolves nothing")
        if not (0.0 < float(self.noise_power) <= 1e6):
            raise CorpusRefused(GENERATOR_CONFIG_REFUSED, "noise power unusable")

    def identity(self) -> str:
        """A digest over the configuration, carried by every window it makes."""
        material = json.dumps(
            {"schema": SCHEMA, "generator_revision": GENERATOR_REVISION,
             "seed": self.seed, "sample_rate_hz": float(self.sample_rate_hz),
             "window_samples": self.window_samples,
             "noise_power": float(self.noise_power)},
            sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "blake2s:" + hashlib.blake2s(material, digest_size=16).hexdigest()


@dataclass(frozen=True)
class SyntheticWindow:
    """One labelled window, and the means to rebuild it.

    `source` is `SYNTHETIC` and there is no path here that sets it otherwise.
    `seed` and `config_identity` are what make the label checkable: given both,
    anyone can regenerate these samples and compare.
    """

    stratum: str
    index: int
    samples: np.ndarray
    sample_rate_hz: float
    source: str
    generator_revision: str
    config_identity: str
    seed: int

    def provenance(self) -> Dict[str, Any]:
        """What this window says about itself, without the samples."""
        return {
            "schema": SCHEMA,
            "stratum": self.stratum,
            "index": self.index,
            "source": self.source,
            "generator_revision": self.generator_revision,
            "config_identity": self.config_identity,
            "seed": self.seed,
            "sample_rate_hz": self.sample_rate_hz,
            "samples": int(self.samples.size),
            "note": ("SYNTHETIC. REGENERABLE FROM seed AND config_identity. "
                     "NOT A CAPTURE AND NEVER A SUBSTITUTE FOR ONE"),
        }


# -- the plan ---------------------------------------------------------------

def declared_plan() -> Dict[str, int]:
    """The stratum plan, read from the manifest (§5.19).

    Never a constant transcribed here. If §5.18 moves again, this moves with it
    and nothing in the harness needs editing.
    """
    return {stratum.key: stratum.minimum_windows for stratum in STRATA}


def plan_state(plan: Optional[Mapping[str, int]] = None) -> Dict[str, Any]:
    """What the plan asks for, and what this module can honestly supply.

    A tuner stratum is reported `STRATUM_AWAITING_CAPTURE` with its count
    intact. It is not zero, not omitted, and not quietly reassigned to a
    synthesisable neighbour -- the corpus is incomplete and says which part.
    """
    plan = declared_plan() if plan is None else dict(plan)
    unknown = sorted(set(plan) - set(STRATUM_KEYS))
    if unknown:
        raise CorpusRefused(STRATUM_NOT_DECLARED,
                            f"{unknown} are not declared strata")
    strata = []
    for key in STRATUM_KEYS:
        if key not in plan:
            continue
        synthesisable = key in SYNTHESISABLE
        strata.append({
            "stratum": key,
            "windows_required": plan[key],
            "state": STRATUM_SYNTHESISED if synthesisable
            else STRATUM_AWAITING_CAPTURE,
            "source": SYNTHETIC if synthesisable else CAPTURED,
        })
    awaiting = [s["stratum"] for s in strata
                if s["state"] == STRATUM_AWAITING_CAPTURE]
    return {
        "schema": SCHEMA,
        "generator_revision": GENERATOR_REVISION,
        "strata": strata,
        "synthesisable": [s["stratum"] for s in strata
                          if s["state"] == STRATUM_SYNTHESISED],
        "awaiting_capture": awaiting,
        "windows_synthesisable": sum(s["windows_required"] for s in strata
                                     if s["state"] == STRATUM_SYNTHESISED),
        "windows_awaiting_capture": sum(s["windows_required"] for s in strata
                                        if s["state"] == STRATUM_AWAITING_CAPTURE),
        "complete": not awaiting,
        "completion_state": (None if not awaiting
                             else CORPUS_INCOMPLETE_AWAITING_CAPTURE),
        "may_freeze": False,
        "freeze_note": ("NO PromotionCorpusLock WHILE ANY STRATUM AWAITS "
                        "CAPTURE. A FREEZE OVER A PARTIAL CORPUS WOULD RECORD "
                        "A STRATA SET NOBODY BUILT"),
        "persists": False,
        "acquires": False,
        "persistence_note": ("SYNTHETIC WINDOWS ARE REGENERATED, NEVER STORED. "
                             "CAPTURED-WINDOW PERSISTENCE AWAITS §5.20"),
    }


# -- generation -------------------------------------------------------------

def _rng(config: GeneratorConfig, stratum: str, index: int) -> np.random.Generator:
    """One stream per (config, stratum, index).

    Derived rather than sequential, so window 4 000 of a stratum is the same
    whether or not the first 3 999 were drawn. A generator whose output depends
    on how much of it you consumed is not reproducible from a seed.
    """
    material = f"{config.identity()}|{stratum}|{index}".encode("utf-8")
    derived = int.from_bytes(hashlib.blake2s(material, digest_size=8).digest(),
                             "big")
    return np.random.default_rng(derived)


def _noise(rng: np.random.Generator, count: int, power: float) -> np.ndarray:
    """Circularly symmetric complex Gaussian, the honest thermal null."""
    scale = np.sqrt(power / 2.0)
    return (rng.normal(0.0, scale, count)
            + 1j * rng.normal(0.0, scale, count)).astype(np.complex64)


def generate_window(stratum: str, index: int,
                    config: GeneratorConfig) -> SyntheticWindow:
    """One labelled window, or a refusal naming why this stratum is not ours."""
    if stratum not in STRATUM_KEYS:
        raise CorpusRefused(STRATUM_NOT_DECLARED,
                            f"{str(stratum)[:32]!r} is not a declared stratum")
    if stratum in TUNER_REQUIRED:
        raise CorpusRefused(
            STRATUM_NOT_SYNTHESISABLE,
            f"{stratum} needs a real receiver; synthesising it would be a "
            f"substitution, and §5.20 has not authorised the capture path")
    if type(index) is not int or isinstance(index, bool) or index < 0:
        raise CorpusRefused(GENERATOR_CONFIG_REFUSED, "index is a natural number")

    rng = _rng(config, stratum, index)
    samples = _SYNTHESISERS[stratum](rng, config)
    return SyntheticWindow(
        stratum=stratum, index=index, samples=samples,
        sample_rate_hz=float(config.sample_rate_hz), source=SYNTHETIC,
        generator_revision=GENERATOR_REVISION,
        config_identity=config.identity(), seed=config.seed)


def generate_stratum(stratum: str, config: GeneratorConfig, *,
                     plan: Optional[Mapping[str, int]] = None
                     ) -> Iterator[SyntheticWindow]:
    """Every window the plan asks of one stratum, lazily.

    The count comes from the plan, which comes from the manifest. Tests inject
    a smaller plan; nothing here knows a number of its own.
    """
    plan = declared_plan() if plan is None else dict(plan)
    if stratum not in plan:
        raise CorpusRefused(STRATUM_NOT_DECLARED,
                            f"{str(stratum)[:32]!r} is not in the plan")
    for index in range(plan[stratum]):
        yield generate_window(stratum, index, config)


def generate_synthetic_corpus(config: GeneratorConfig, *,
                              plan: Optional[Mapping[str, int]] = None
                              ) -> Iterator[SyntheticWindow]:
    """Every synthesisable stratum in the plan. **Never the tuner ones.**

    They are skipped rather than attempted, and `plan_state` is where their
    absence is reported. A corpus that silently contained nine strata where the
    gate expects twelve would be a corpus whose shortfall lived only in a
    reader's memory.
    """
    plan = declared_plan() if plan is None else dict(plan)
    for stratum in SYNTHESISABLE:
        if stratum in plan:
            yield from generate_stratum(stratum, config, plan=plan)


# -- the nine ---------------------------------------------------------------
#
# Each returns one window of complex samples at `config.sample_rate_hz`. They
# are nulls for a *symbol-clock* detector: none carries a symbol clock, and each
# is a different way for a detector to think it found one.

def _thermal(rng, config):
    return _noise(rng, config.window_samples, config.noise_power)


def _time(config) -> np.ndarray:
    return np.arange(config.window_samples) / float(config.sample_rate_hz)


def _analogue_fm(rng, config):
    """Steady FM voice: a wandering instantaneous frequency, constant envelope.

    A single tone would be too kind -- its envelope is exactly flat and any
    detector rejects it. The modulating signal is band-limited noise, which is
    what speech looks like to an envelope test.
    """
    t = _time(config)
    modulator = _lowpass_noise(rng, config.window_samples, 0.002)
    phase = 2 * np.pi * (3_000.0 * np.cumsum(modulator) / config.sample_rate_hz
                         + 1_000.0 * t)
    signal = np.exp(1j * phase).astype(np.complex64)
    return signal + _noise(rng, config.window_samples, config.noise_power * 0.01)


def _am(rng, config):
    """Amplitude modulation: envelope variation with no symbol structure.

    The trap this stratum is for -- an envelope that varies is not an envelope
    that ticks, and a peak-to-sidelobe test must not confuse the two.
    """
    t = _time(config)
    modulator = 1.0 + 0.6 * _lowpass_noise(rng, config.window_samples, 0.001)
    carrier = np.exp(2j * np.pi * 1_000.0 * t)
    signal = (modulator * carrier).astype(np.complex64)
    return signal + _noise(rng, config.window_samples, config.noise_power * 0.01)


def _constant_envelope_digital(rng, config):
    """C4FM-like: a real symbol clock, invisible to an envelope test.

    In the *null* corpus for this detector because the detector cannot see it --
    §5.12's point that `CONSTANT_ENVELOPE` maps to `NOT_ATTEMPTED` rather than
    to a measured negative. A DIGITAL call on this window would be right by
    accident, which is why it must not happen.
    """
    symbol_rate = 4_800.0
    sps = max(2, int(config.sample_rate_hz / symbol_rate))
    count = config.window_samples // sps + 2
    symbols = rng.choice([-3.0, -1.0, 1.0, 3.0], size=count)
    deviation = 1_800.0
    frequency = np.repeat(symbols, sps)[:config.window_samples] * deviation
    phase = 2 * np.pi * np.cumsum(frequency) / float(config.sample_rate_hz)
    return np.exp(1j * phase).astype(np.complex64)


def _adjacent_channel(rng, config):
    """A strong neighbour offset in frequency, plus the thermal floor."""
    t = _time(config)
    neighbour = 8.0 * np.exp(2j * np.pi * (config.sample_rate_hz * 0.2) * t)
    modulator = 1.0 + 0.3 * _lowpass_noise(rng, config.window_samples, 0.001)
    return ((neighbour * modulator).astype(np.complex64)
            + _noise(rng, config.window_samples, config.noise_power))


def _dc_contamination(rng, config):
    """A zero-IF DC artefact: a constant offset on the thermal floor.

    The classic false peak -- DC in the squared envelope is mean power, which
    §13's detector removes before transforming. This stratum is what proves it.
    """
    offset = complex(rng.normal(0.0, 2.0), rng.normal(0.0, 2.0))
    return (_noise(rng, config.window_samples, config.noise_power)
            + np.complex64(offset))


def _dropped_frames(rng, config):
    """Lost samples: the stream spliced across a gap.

    A splice is a discontinuity, and a discontinuity is broadband -- which is a
    plausible way to manufacture a spurious line.
    """
    samples = _noise(rng, config.window_samples, config.noise_power)
    gap_at = int(rng.integers(config.window_samples // 4,
                              3 * config.window_samples // 4))
    gap_len = int(rng.integers(64, 1024))
    kept = np.concatenate([samples[:gap_at], samples[gap_at + gap_len:]])
    padding = _noise(rng, config.window_samples - kept.size, config.noise_power)
    return np.concatenate([kept, padding]).astype(np.complex64)


def _overloaded(rng, config):
    """Converter saturation: hard clipping, which is a nonlinearity.

    Nonlinearity makes harmonics, and harmonics are lines. A detector that
    calls this DIGITAL has found the receiver, not the signal.
    """
    t = _time(config)
    strong = 6.0 * np.exp(2j * np.pi * 1_000.0 * t)
    raw = strong + _noise(rng, config.window_samples, config.noise_power)
    limit = 1.0
    clipped = np.clip(raw.real, -limit, limit) + 1j * np.clip(raw.imag, -limit,
                                                              limit)
    return clipped.astype(np.complex64)


def _two_signals(rng, config):
    """Two emitters in the span, neither of them digital."""
    t = _time(config)
    first = 3.0 * np.exp(2j * np.pi * 900.0 * t)
    second = 2.5 * np.exp(2j * np.pi * -1_500.0 * t)
    modulator = 1.0 + 0.4 * _lowpass_noise(rng, config.window_samples, 0.0008)
    return ((first * modulator + second).astype(np.complex64)
            + _noise(rng, config.window_samples, config.noise_power * 0.1))


def _lowpass_noise(rng: np.random.Generator, count: int,
                   bandwidth: float) -> np.ndarray:
    """Band-limited real noise in [-1, 1], for modulating something.

    A single-pole filter in the frequency domain: cheap, and the shape does not
    matter provided it is smooth and carries no periodicity of its own -- which
    is the property that would otherwise plant the very line being searched for.
    """
    spectrum = rng.normal(0.0, 1.0, count) + 1j * rng.normal(0.0, 1.0, count)
    cutoff = max(1, int(count * bandwidth))
    mask = np.zeros(count)
    mask[:cutoff] = 1.0
    mask[-cutoff:] = 1.0
    shaped = np.fft.ifft(np.fft.fft(spectrum) * mask).real
    peak = float(np.abs(shaped).max())
    return shaped / peak if peak > 0 else shaped


_SYNTHESISERS: Dict[str, Any] = {
    "THERMAL_NO_INPUT": _thermal,
    "STATIONARY_ANALOGUE_FM": _analogue_fm,
    "AM": _am,
    "CONSTANT_ENVELOPE_DIGITAL": _constant_envelope_digital,
    "ADJACENT_CHANNEL_INTERFERENCE": _adjacent_channel,
    "DC_CONTAMINATION": _dc_contamination,
    "DROPPED_FRAMES_TIMING_GAPS": _dropped_frames,
    "OVERLOADED_CLIPPED": _overloaded,
    "TWO_SIGNAL_COLLISIONS": _two_signals,
}


def status() -> Dict[str, Any]:
    """Declared capability, and declared absence of everything else."""
    return {
        "schema": SCHEMA,
        "generator_revision": GENERATOR_REVISION,
        "synthesisable_strata": list(SYNTHESISABLE),
        "tuner_required_strata": list(TUNER_REQUIRED),
        "window_sources": list(WINDOW_SOURCES),
        "produces": SYNTHETIC,
        "acquires": False,
        "persists": False,
        "opens_a_socket": False,
        "has_generic_writer": False,
        "substitutes_for_capture": False,
        "refusals": list(CORPUS_REFUSALS),
        "note": ("GENERATION AND LABELLING ONLY (§5.19). CAPTURED-WINDOW "
                 "INGESTION AND PERSISTENCE AWAIT §5.20"),
    }
