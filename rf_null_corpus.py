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
  decides persistence and was **accepted 2026-09-14** -- for the three captured
  strata only, and it authorises no code by itself. Nothing here writes, and
  nothing here is the writer it contemplates.

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

from rf_promotion_geometry import (
    PROMOTION_GEOMETRY_REFUSED,
    PROMOTION_SAMPLE_RATE_HZ,
    PROMOTION_WINDOW_SAMPLES,
    promotion_geometry_deviations,
)
from rf_validation_manifest import STRATA, STRATUM_KEYS

SCHEMA = "scythe.rf-null-corpus.v1"
# v2: the generator declaration grew `purpose`, so every identity it computes
# covers a field v1's did not. Not a rescaling and not comparable -- a v1 record
# is refused by `regenerate` rather than reinterpreted, which is what a revision
# is for.
GENERATOR_REVISION = "synthetic-null.v2"

# -- what a configuration is for -------------------------------------------
#
# §5.20 correction A. The geometry lives in `rf_promotion_geometry`, which
# imports nothing but `typing`; this module reads it from there rather than
# duplicating the numbers or importing the ring, whose graph carries
# `threading`, NumPy and a 4 MB allocation for anyone who wanted a constant.
#
# 262 144 stays reachable, and only through `DEVELOPMENT_ONLY`: half the
# arithmetic proves the same properties in a test suite. It is not a smaller
# promotion geometry, it is not a geometry at all, and a promotion configuration
# that asks for it is refused rather than rounded.
PROMOTION_CORPUS = "PROMOTION_CORPUS"
DEVELOPMENT_ONLY = "DEVELOPMENT_ONLY"
GENERATOR_PURPOSES: Tuple[str, ...] = (PROMOTION_CORPUS, DEVELOPMENT_ONLY)

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
    "PROVENANCE_DIGEST_MISMATCH",
)

# -- plan states ------------------------------------------------------------
#
# `SYNTHESIS_PLANNED`, not `STRATUM_SYNTHESISED`. `plan_state` has generated
# nothing: it reports what this module *could* build if asked, and a capability
# is not evidence that 5 561 windows exist.
SYNTHESIS_PLANNED = "SYNTHESIS_PLANNED"
STRATUM_AWAITING_CAPTURE = "STRATUM_AWAITING_CAPTURE"
PLAN_STATES: Tuple[str, ...] = (SYNTHESIS_PLANNED, STRATUM_AWAITING_CAPTURE)

CORPUS_INCOMPLETE_AWAITING_CAPTURE = "CORPUS_INCOMPLETE_AWAITING_CAPTURE"
CORPUS_INCOMPLETE_STRATA_MISSING = "CORPUS_INCOMPLETE_STRATA_MISSING"
PROVENANCE_DIGEST_MISMATCH = "PROVENANCE_DIGEST_MISMATCH"

# -- two questions that used to be one --------------------------------------
#
# §5.20 correction C. `plan_state` answered `may_freeze: False` with a note
# saying no `PromotionCorpusLock` may exist while a stratum awaits capture.
# That was wrong in the direction that matters: the lock is a **configuration
# precommitment** and has to exist *before* the first promotion window, or
# thresholds get tuned against the windows that validate them. It holds no
# window counts -- read its fields.
#
# What the old answer was protecting is the other question: nothing may claim a
# corpus is *complete* while a stratum is missing or awaiting capture. That is
# `CorpusCompletionReceipt`, and it is unreachable here.
#
# There is deliberately **no `may_freeze` alias**. A key that kept the old name
# and the old meaning would be the contradiction preserved under a synonym, and
# a reader who never revisited it would never learn it had been answered wrong.
CONFIGURATION_PRECOMMITMENT_AVAILABLE = "CONFIGURATION_PRECOMMITMENT_AVAILABLE"
PRECOMMITMENT_STATES: Tuple[str, ...] = (CONFIGURATION_PRECOMMITMENT_AVAILABLE,)

COMPLETION_ELIGIBLE = "COMPLETION_ELIGIBLE"
COMPLETION_BLOCKED_AWAITING_CAPTURE = "COMPLETION_BLOCKED_AWAITING_CAPTURE"
COMPLETION_BLOCKED_STRATA_MISSING = "COMPLETION_BLOCKED_STRATA_MISSING"
COMPLETION_ELIGIBILITY_STATES: Tuple[str, ...] = (
    COMPLETION_ELIGIBLE, COMPLETION_BLOCKED_AWAITING_CAPTURE,
    COMPLETION_BLOCKED_STRATA_MISSING,
)


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
    # No default. A configuration that did not say what it was for would
    # default to one of the two answers, and whichever was chosen, half the
    # callers would be silently wrong -- the promotion ones dangerously so.
    purpose: str
    sample_rate_hz: float = PROMOTION_SAMPLE_RATE_HZ
    window_samples: int = PROMOTION_WINDOW_SAMPLES
    noise_power: float = 1.0

    def __post_init__(self) -> None:
        if type(self.seed) is not int or isinstance(self.seed, bool) \
                or not 0 <= self.seed < 2 ** 32:
            raise CorpusRefused(GENERATOR_CONFIG_REFUSED,
                                "seed is an integer in [0, 2**32)")
        if self.purpose not in GENERATOR_PURPOSES:
            raise CorpusRefused(
                GENERATOR_CONFIG_REFUSED,
                f"purpose is one of {list(GENERATOR_PURPOSES)}; "
                f"got {str(self.purpose)[:32]!r}")
        if not (0.0 < float(self.sample_rate_hz) <= 20e6):
            raise CorpusRefused(GENERATOR_CONFIG_REFUSED,
                                "sample rate outside a plausible range")
        if type(self.window_samples) is not int or self.window_samples < 1024:
            raise CorpusRefused(GENERATOR_CONFIG_REFUSED,
                                "a window under 1024 samples resolves nothing")
        if not (0.0 < float(self.noise_power) <= 1e6):
            raise CorpusRefused(GENERATOR_CONFIG_REFUSED, "noise power unusable")
        # Last, so a promotion configuration that is wrong in two ways hears
        # about the generic fault first and the geometry second.
        if self.purpose == PROMOTION_CORPUS:
            deviations = promotion_geometry_deviations(
                sample_rate_hz=self.sample_rate_hz,
                window_samples=self.window_samples)
            if deviations:
                raise CorpusRefused(
                    PROMOTION_GEOMETRY_REFUSED,
                    f"a promotion corpus is {PROMOTION_WINDOW_SAMPLES} samples "
                    f"at {PROMOTION_SAMPLE_RATE_HZ} Hz; {list(deviations)} "
                    "deviate. Use DEVELOPMENT_ONLY for anything else")

    def identity(self) -> str:
        """A digest over **this configuration**, and nothing wider.

        Deliberately not stretched to cover the stratum or the index: it is the
        identity of a `GeneratorConfig`, one configuration serves every stratum,
        and a name that quietly covered more than it says would be the next
        thing to be wrong about. `window_identity` is what covers a window.
        """
        material = json.dumps(
            {"schema": SCHEMA, "generator_revision": GENERATOR_REVISION,
             "seed": self.seed, "purpose": self.purpose,
             "sample_rate_hz": float(self.sample_rate_hz),
             "window_samples": self.window_samples,
             "noise_power": float(self.noise_power)},
            sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "blake2s:" + hashlib.blake2s(material, digest_size=16).hexdigest()


def window_identity(*, schema: str, generator_revision: str, source: str,
                    stratum: str, index: int, seed: int, purpose: str,
                    sample_rate_hz: float, window_samples: int,
                    noise_power: float) -> str:
    """The digest over a window's complete canonical declaration.

    Keyword-only and exhaustive, so adding a declared field without extending
    this is a `TypeError` at the call rather than a quietly narrower digest --
    which is the failure mode `config_identity` had when it was asked to stand
    in for this.
    """
    material = json.dumps(
        {"schema": schema, "generator_revision": generator_revision,
         "source": source, "stratum": stratum, "index": index, "seed": seed,
         "purpose": purpose, "sample_rate_hz": float(sample_rate_hz),
         "window_samples": window_samples, "noise_power": float(noise_power)},
        sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "blake2s:" + hashlib.blake2s(material, digest_size=16).hexdigest()


@dataclass(frozen=True)
class SyntheticWindow:
    """One labelled window, carrying everything needed to rebuild it.

    The generator declaration is on the window itself -- seed, rate, length,
    noise power, stratum, index -- because that is what `regenerate()` needs. A
    digest alone would not do: it commits to a configuration and does not reveal
    one, so a record carrying only `config_identity` is checkable by someone who
    already has the configuration and regenerable by nobody.
    """

    stratum: str
    index: int
    samples: np.ndarray
    seed: int
    purpose: str
    sample_rate_hz: float
    window_samples: int
    noise_power: float
    # Not parameters. A caller has no authority to say where a window came
    # from or which generator made it, so neither is suppliable: `init=False`
    # means `SyntheticWindow(source=CAPTURED, ...)` is a TypeError rather than
    # a lie this module would then carry.
    source: str = field(init=False, default=SYNTHETIC)
    generator_revision: str = field(init=False, default=GENERATOR_REVISION)
    schema: str = field(init=False, default=SCHEMA)

    def identity(self) -> str:
        """A digest over the **complete canonical declaration** of this window.

        Everything `regenerate` needs and everything it must not be free to
        change: the sealed constants, the stratum, the index, and the four
        configuration fields. `config_identity` covers only the configuration,
        so a record could previously be edited in the fields it did not reach
        -- source, schema, revision, stratum, index -- and still verify.
        """
        return window_identity(
            schema=SCHEMA, generator_revision=GENERATOR_REVISION,
            source=SYNTHETIC, stratum=self.stratum, index=self.index,
            seed=self.seed, purpose=self.purpose,
            sample_rate_hz=self.sample_rate_hz,
            window_samples=self.window_samples, noise_power=self.noise_power)

    def config(self) -> "GeneratorConfig":
        """The configuration that made this window, rebuilt from its own fields."""
        return GeneratorConfig(seed=self.seed,
                               purpose=self.purpose,
                               sample_rate_hz=self.sample_rate_hz,
                               window_samples=self.window_samples,
                               noise_power=self.noise_power)

    def provenance(self) -> Dict[str, Any]:
        """The **complete generator declaration**, without the samples.

        Every field `GeneratorConfig` takes, plus the stratum and index. Not a
        digest alone: a digest **commits** to a configuration and does not
        reveal one, so a record carrying only `config_identity` could be
        checked against a configuration someone already had and regenerated
        from nothing. `regenerate()` takes this dictionary and returns the same
        samples, which is what makes the word reproducible mean something.
        """
        return {
            "schema": self.schema,
            "generator_revision": self.generator_revision,
            "source": self.source,
            "stratum": self.stratum,
            "index": self.index,
            "seed": self.seed,
            "purpose": self.purpose,
            "sample_rate_hz": self.sample_rate_hz,
            "window_samples": self.window_samples,
            "noise_power": self.noise_power,
            "config_identity": self.config().identity(),
            "window_identity": self.identity(),
            "samples": int(self.samples.size),
            "note": ("SYNTHETIC. REGENERABLE FROM THE FIELDS ABOVE VIA "
                     "regenerate(). NOT A CAPTURE AND NEVER A SUBSTITUTE"),
        }


# Every field a complete declaration carries. `regenerate` requires all of
# them, and `window_identity` covers all but `samples` -- which is checked
# against `window_samples` instead, being a second name for one length.
DECLARATION_FIELDS: Tuple[str, ...] = (
    "schema", "generator_revision", "source", "stratum", "index", "seed",
    "purpose", "sample_rate_hz", "window_samples", "noise_power",
    "config_identity", "window_identity", "samples",
)


def regenerate(provenance: Mapping[str, Any]) -> SyntheticWindow:
    """Rebuild a window from its declaration, verifying everything first.

    **This is a second constructor**, and the dataclass being sealed does not
    seal it. Without the checks below it would take a record whose `source` had
    been changed to `CAPTURED` and hand back a valid `SyntheticWindow` -- the
    laundering the sealing was meant to prevent, through a different door.

    Every field in `DECLARATION_FIELDS` is required and every one is checked.
    `PROVENANCE_DIGEST_MISMATCH` means the record and its digests disagree;
    this module will not guess which side was edited.
    """
    missing = [name for name in DECLARATION_FIELDS if name not in provenance]
    if missing:
        raise CorpusRefused(
            STRATUM_NOT_DECLARED,
            f"the provenance is not a complete generator declaration; "
            f"{sorted(missing)} are absent")

    # The sealed constants are not a caller's to supply here either. The
    # dataclass refuses them at construction; this is the other constructor,
    # and without these three it would launder an altered record into a valid
    # synthetic object -- source: CAPTURED going in, SYNTHETIC coming out.
    for name, expected in (("schema", SCHEMA),
                           ("generator_revision", GENERATOR_REVISION),
                           ("source", SYNTHETIC)):
        if provenance[name] != expected:
            raise CorpusRefused(
                PROVENANCE_DIGEST_MISMATCH,
                f"{name} is {str(provenance[name])[:32]!r} and this module "
                f"only ever produces {expected!r}")

    # Verified **before** generation, over everything: the stratum and the index
    # are in here because `config_identity` does not reach them, and editing
    # either produces different samples under a digest that still matched.
    declared = window_identity(
        schema=provenance["schema"],
        generator_revision=provenance["generator_revision"],
        source=provenance["source"], stratum=provenance["stratum"],
        index=provenance["index"], seed=provenance["seed"],
        purpose=provenance["purpose"],
        sample_rate_hz=provenance["sample_rate_hz"],
        window_samples=provenance["window_samples"],
        noise_power=provenance["noise_power"])
    if declared != provenance["window_identity"]:
        raise CorpusRefused(
            PROVENANCE_DIGEST_MISMATCH,
            "the complete declaration does not hash to its window_identity")

    config = GeneratorConfig(seed=provenance["seed"],
                             purpose=provenance["purpose"],
                             sample_rate_hz=provenance["sample_rate_hz"],
                             window_samples=provenance["window_samples"],
                             noise_power=provenance["noise_power"])
    if config.identity() != provenance["config_identity"]:
        raise CorpusRefused(
            PROVENANCE_DIGEST_MISMATCH,
            "the configuration does not hash to its config_identity")

    # Two names for one length drift. `samples` is kept because a reader wants
    # the count without rebuilding the window, and checked because an unchecked
    # duplicate is worse than no duplicate.
    if provenance["samples"] != provenance["window_samples"]:
        raise CorpusRefused(
            PROVENANCE_DIGEST_MISMATCH,
            f"samples {provenance['samples']} and window_samples "
            f"{provenance['window_samples']} disagree about one length")

    return generate_window(provenance["stratum"], provenance["index"], config)


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
            "state": SYNTHESIS_PLANNED if synthesisable
            else STRATUM_AWAITING_CAPTURE,
            "source": SYNTHETIC if synthesisable else CAPTURED,
        })
    awaiting = [s["stratum"] for s in strata
                if s["state"] == STRATUM_AWAITING_CAPTURE]
    # Completion needs both: every declared stratum present, and none of them
    # waiting on a receiver. A plan of only the nine this module can build is
    # not a complete corpus -- it is a complete *subset*, and calling it
    # complete is the claim §5.19 forbids. In this module the declared plan can
    # therefore never be complete, which is the honest state of affairs.
    missing = [key for key in STRATUM_KEYS if key not in plan]
    complete = not awaiting and not missing
    if missing:
        completion_state = CORPUS_INCOMPLETE_STRATA_MISSING
        completion_eligibility = COMPLETION_BLOCKED_STRATA_MISSING
    elif awaiting:
        completion_state = CORPUS_INCOMPLETE_AWAITING_CAPTURE
        completion_eligibility = COMPLETION_BLOCKED_AWAITING_CAPTURE
    else:                                        # pragma: no cover - unreachable
        completion_state = None                  # here while TUNER_REQUIRED is
        completion_eligibility = COMPLETION_ELIGIBLE   # non-empty
    return {
        "schema": SCHEMA,
        "generator_revision": GENERATOR_REVISION,
        "strata": strata,
        "synthesis_planned": [s["stratum"] for s in strata
                              if s["state"] == SYNTHESIS_PLANNED],
        "awaiting_capture": awaiting,
        "missing_from_plan": missing,
        "windows_synthesis_planned": sum(s["windows_required"] for s in strata
                                         if s["state"] == SYNTHESIS_PLANNED),
        "windows_awaiting_capture": sum(s["windows_required"] for s in strata
                                        if s["state"] == STRATUM_AWAITING_CAPTURE),
        "complete": complete,
        "completion_state": completion_state,
        "completion_note": ("COMPLETION REQUIRES EVERY DECLARED STRATUM "
                            "PRESENT AND NONE AWAITING CAPTURE. NOTHING HERE "
                            "HAS GENERATED A WINDOW: A PLAN IS NOT A CORPUS"),
        "configuration_precommitment": CONFIGURATION_PRECOMMITMENT_AVAILABLE,
        "precommitment_note": ("A PromotionCorpusLock FREEZES CONFIGURATION "
                               "BEFORE THE FIRST PROMOTION WINDOW. IT HOLDS NO "
                               "WINDOW COUNTS AND IS NOT A COMPLETION CLAIM"),
        "completion_eligibility": completion_eligibility,
        "completion_eligibility_note": ("NO CorpusCompletionReceipt WHILE ANY "
                                        "DECLARED STRATUM IS MISSING OR "
                                        "AWAITING CAPTURE"),
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
        stratum=stratum, index=index, samples=samples, seed=config.seed,
        purpose=config.purpose,
        sample_rate_hz=float(config.sample_rate_hz),
        window_samples=config.window_samples,
        noise_power=float(config.noise_power))


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
