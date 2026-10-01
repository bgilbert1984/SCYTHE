"""The §5.21 catalogue runner: per-window verdicts from the in-span check.

`CapturePlanDeclaration` checks the visits a plan *draws* at construction.
The runner checks the windows a run *captured* after the fact: for each
window it reports which catalogued products the retune model puts in span
at the LO the window was actually captured at, and the verdict §5.21's
table gives that stratum there.

- THERMAL_NO_INPUT: CLEAN where the catalogue puts nothing in span,
  VIOLATION (PLAN_THERMAL_NOT_SPUR_FREE) where it puts anything.
- RECEIVER_SPURS: CLEAN where an eligible trial unit names the window's
  tuning, VIOLATION (PLAN_SPUR_NOT_IN_SPAN) where none does.
- any other stratum: NOT_APPLICABLE. The in-span check governs only the
  two catalogue strata; a gain step is not a verdict about spurs.

The runner reports; it does not refuse. A caller that wants the gate
re-raised calls `assert_clean`, which raises the first violation with the
same code the plan would have refused it with.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from rf_promotion_envelope import (
    CapturePlanDeclaration,
    PLAN_SPUR_NOT_IN_SPAN,
    PLAN_THERMAL_NOT_SPUR_FREE,
    catalogued_spurs_in_span,
    usable_half_span_hz,
)

# The two strata §5.21's table governs, as the plan names them.
THERMAL_NO_INPUT = "THERMAL_NO_INPUT"
RECEIVER_SPURS = "RECEIVER_SPURS"

VERDICT_CLEAN = "CLEAN"
VERDICT_VIOLATION = "VIOLATION"
VERDICT_NOT_APPLICABLE = "NOT_APPLICABLE"
_VERDICTS = (VERDICT_CLEAN, VERDICT_VIOLATION, VERDICT_NOT_APPLICABLE)

RUNNER_PLAN_NOT_DECLARED = "RUNNER_PLAN_NOT_DECLARED"
RUNNER_WINDOW_NOT_DECLARED = "RUNNER_WINDOW_NOT_DECLARED"
RUNNER_WINDOW_DECLARED_TWICE = "RUNNER_WINDOW_DECLARED_TWICE"
RUNNER_STRATUM_NOT_PLANNED = "RUNNER_STRATUM_NOT_PLANNED"
RUNNER_TUNING_NOT_DECLARED = "RUNNER_TUNING_NOT_DECLARED"
RUNNER_LO_NOT_AT_TUNING = "RUNNER_LO_NOT_AT_TUNING"
RUNNER_SPUR_CATALOGUE_ABSENT = "RUNNER_SPUR_CATALOGUE_ABSENT"
RUNNER_QUANTITY_NOT_FINITE = "RUNNER_QUANTITY_NOT_FINITE"
RUNNER_REPORT_NOT_DECLARED = "RUNNER_REPORT_NOT_DECLARED"


class RunnerRefused(RuntimeError):
    """The runner's own regime. A violated window is a verdict, not this;
    this is for inputs the runner cannot walk at all."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CapturedWindow:
    """One window as it was captured: the stratum it was captured under,
    the LO the tuner was actually set to, and the declared tuning that LO
    belongs to. The LO is the measured fact; the tuning says which declared
    centre it is an offset from."""

    window_id: str
    stratum: str
    lo_hz: float
    tuning_id: str

    def __post_init__(self) -> None:
        for name in ("window_id", "stratum", "tuning_id"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise RunnerRefused(
                    RUNNER_WINDOW_NOT_DECLARED,
                    f"a captured window declares {name}; got {value!r}")
        if type(self.lo_hz) not in (int, float):
            raise RunnerRefused(
                RUNNER_QUANTITY_NOT_FINITE,
                f"a captured window carries the LO it was captured at; "
                f"got {self.lo_hz!r}")
        if not math.isfinite(self.lo_hz):
            raise RunnerRefused(
                RUNNER_QUANTITY_NOT_FINITE,
                f"a captured window carries a finite LO; got {self.lo_hz!r}")
        object.__setattr__(self, "lo_hz", float(self.lo_hz))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "stratum": self.stratum,
            "lo_hz": float(self.lo_hz),
            "tuning_id": self.tuning_id,
        }


@dataclass(frozen=True)
class WindowVerdict:
    """What the in-span check says about one captured window. `in_span`
    is the measurement -- the catalogued product ids the retune model puts
    inside the analysis span at the window's LO. `verdict` is the decision
    read off it, and a VIOLATION carries the refusal code the plan would
    have refused the visit with."""

    window_id: str
    stratum: str
    lo_hz: float
    tuning_id: str
    in_span: Tuple[str, ...]
    verdict: str
    refusal_code: Optional[str]
    detail: str

    def __post_init__(self) -> None:
        if self.verdict not in _VERDICTS:
            raise RunnerRefused(
                RUNNER_WINDOW_NOT_DECLARED,
                f"a verdict is one of {list(_VERDICTS)}; "
                f"got {self.verdict!r}")
        if (self.verdict == VERDICT_VIOLATION) == (self.refusal_code is None):
            raise RunnerRefused(
                RUNNER_WINDOW_NOT_DECLARED,
                f"a VIOLATION carries its refusal code and nothing else "
                f"does; got {self.verdict} with {self.refusal_code!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "stratum": self.stratum,
            "lo_hz": float(self.lo_hz),
            "tuning_id": self.tuning_id,
            "in_span": list(self.in_span),
            "verdict": self.verdict,
            "refusal_code": self.refusal_code,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CatalogueRunReport:
    """Every window the runner walked, and the digest of the window list it
    walked, so a reader can tell whether the report in hand is the report of
    the run being discussed."""

    verdicts: Tuple[WindowVerdict, ...]
    windows_digest: str

    def counts(self) -> Dict[str, int]:
        counts = {verdict: 0 for verdict in _VERDICTS}
        for verdict in self.verdicts:
            counts[verdict.verdict] += 1
        return counts

    def violations(self) -> Tuple[WindowVerdict, ...]:
        return tuple(v for v in self.verdicts
                     if v.verdict == VERDICT_VIOLATION)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "windows_digest": self.windows_digest,
            "counts": self.counts(),
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


def _canonical_windows(windows: Tuple[CapturedWindow, ...]) -> bytes:
    return json.dumps([w.to_dict() for w in windows],
                      sort_keys=True).encode("utf-8")


def _thermal_verdict(window: CapturedWindow, allocation: Any,
                     catalogue: Any, tunings: Any) -> WindowVerdict:
    # Mirrors the plan's THERMAL_NO_INPUT row, per captured window rather
    # than per drawn visit: a captured thermal window is captured where NO
    # catalogued spur falls in the span, at the LO the visit actually set.
    if allocation is None:
        raise RunnerRefused(
            RUNNER_SPUR_CATALOGUE_ABSENT,
            f"window {window.window_id!r} is THERMAL_NO_INPUT and no "
            f"catalogue says what else could be in it. A terminated window "
            f"cannot be shown to hold thermal noise only until the catalogue "
            f"says what the receiver's own products are")
    in_span = catalogued_spurs_in_span(catalogue, tunings, window.lo_hz)
    if in_span:
        return WindowVerdict(
            window_id=window.window_id, stratum=window.stratum,
            lo_hz=window.lo_hz, tuning_id=window.tuning_id,
            in_span=in_span, verdict=VERDICT_VIOLATION,
            refusal_code=PLAN_THERMAL_NOT_SPUR_FREE,
            detail=(f"{window.window_id} is a THERMAL_NO_INPUT window "
                    f"captured at {window.lo_hz:.0f} Hz, where the catalogue "
                    f"puts {list(in_span[:3])} in span. A window with an "
                    f"internal product in it is a RECEIVER_SPURS window "
                    f"mislabelled"))
    return WindowVerdict(
        window_id=window.window_id, stratum=window.stratum,
        lo_hz=window.lo_hz, tuning_id=window.tuning_id,
        in_span=in_span, verdict=VERDICT_CLEAN, refusal_code=None,
        detail=(f"{window.window_id} is a THERMAL_NO_INPUT window captured "
                f"at {window.lo_hz:.0f} Hz, where the catalogue puts nothing "
                f"in span"))


def _spur_verdict(window: CapturedWindow, allocation: Any,
                  catalogue: Any, tunings: Any) -> WindowVerdict:
    # Mirrors the plan's RECEIVER_SPURS row: every window is captured where
    # at least one catalogued spur falls in the span, and the eligible units
    # say at which tunings each product is observable.
    if allocation is None:
        raise RunnerRefused(
            RUNNER_SPUR_CATALOGUE_ABSENT,
            f"window {window.window_id!r} is RECEIVER_SPURS and no catalogue "
            f"was declared. §5.22 requires feasibility established before "
            f"the corpus opens")
    in_span = catalogued_spurs_in_span(catalogue, tunings, window.lo_hz)
    if window.tuning_id not in allocation.eligible_tuning_ids():
        return WindowVerdict(
            window_id=window.window_id, stratum=window.stratum,
            lo_hz=window.lo_hz, tuning_id=window.tuning_id,
            in_span=in_span, verdict=VERDICT_VIOLATION,
            refusal_code=PLAN_SPUR_NOT_IN_SPAN,
            detail=(f"{window.window_id} is a RECEIVER_SPURS window at "
                    f"{window.tuning_id}, where no eligible trial unit puts "
                    f"a catalogued product in span. A window with no internal "
                    f"product in it is not a RECEIVER_SPURS window"))
    return WindowVerdict(
        window_id=window.window_id, stratum=window.stratum,
        lo_hz=window.lo_hz, tuning_id=window.tuning_id,
        in_span=in_span, verdict=VERDICT_CLEAN, refusal_code=None,
        detail=(f"{window.window_id} is a RECEIVER_SPURS window at "
                f"{window.tuning_id}, where the eligible units put "
                f"{list(in_span[:3]) or 'a catalogued product'} in span"))


def run_catalogue_windows(*, plan: Any, windows: Any) -> CatalogueRunReport:
    """Walk captured windows through the in-span check and report the
    per-window verdicts. The plan is the declaration the windows are
    audited against: every window's stratum must be one the plan captures,
    and every window's tuning one the plan declares."""
    if type(plan) is not CapturePlanDeclaration:
        raise RunnerRefused(
            RUNNER_PLAN_NOT_DECLARED,
            f"the runner audits captured windows against a "
            f"CapturePlanDeclaration; got {type(plan).__name__}")
    windows = tuple(windows)
    for window in windows:
        if type(window) is not CapturedWindow:
            raise RunnerRefused(
                RUNNER_WINDOW_NOT_DECLARED,
                f"the runner walks CapturedWindow descriptors; got "
                f"{type(window).__name__}")
    seen = set()
    for window in windows:
        if window.window_id in seen:
            raise RunnerRefused(
                RUNNER_WINDOW_DECLARED_TWICE,
                f"window {window.window_id!r} is walked twice. A run "
                f"reports each captured window once")
        seen.add(window.window_id)
    by_tuning = {t.tuning_id: t for t in plan.tunings}
    captured = set(plan.captured_strata())
    allocation = plan.spur_allocation
    catalogue = allocation.catalogue if allocation is not None else ()
    half_span = usable_half_span_hz()
    verdicts = []
    for window in windows:
        if window.stratum not in captured:
            raise RunnerRefused(
                RUNNER_STRATUM_NOT_PLANNED,
                f"window {window.window_id!r} claims stratum "
                f"{window.stratum!r}, which this plan captures nothing "
                f"under. The runner audits a plan's captured windows, not "
                f"windows captured outside it")
        tuning = by_tuning.get(window.tuning_id)
        if tuning is None:
            raise RunnerRefused(
                RUNNER_TUNING_NOT_DECLARED,
                f"window {window.window_id!r} claims {window.tuning_id!r}, "
                f"which this plan does not declare, so nothing can say "
                f"where the catalogue falls at it")
        if abs(window.lo_hz - tuning.center_frequency_hz) > half_span:
            raise RunnerRefused(
                RUNNER_LO_NOT_AT_TUNING,
                f"window {window.window_id!r} was captured at "
                f"{window.lo_hz:.0f} Hz and claims {window.tuning_id} at "
                f"{tuning.center_frequency_hz:.0f} Hz, further than the "
                f"analysis half-span. The LO is not at the tuning it claims")
        if window.stratum == THERMAL_NO_INPUT:
            verdicts.append(
                _thermal_verdict(window, allocation, catalogue, plan.tunings))
        elif window.stratum == RECEIVER_SPURS:
            verdicts.append(
                _spur_verdict(window, allocation, catalogue, plan.tunings))
        else:
            in_span = catalogued_spurs_in_span(
                catalogue, plan.tunings, window.lo_hz)
            verdicts.append(WindowVerdict(
                window_id=window.window_id, stratum=window.stratum,
                lo_hz=window.lo_hz, tuning_id=window.tuning_id,
                in_span=in_span, verdict=VERDICT_NOT_APPLICABLE,
                refusal_code=None,
                detail=(f"{window.window_id} is a {window.stratum} window. "
                        f"The in-span check governs THERMAL_NO_INPUT and "
                        f"RECEIVER_SPURS; this stratum gets no verdict from "
                        f"it")))
    return CatalogueRunReport(
        verdicts=tuple(verdicts),
        windows_digest=hashlib.sha256(
            _canonical_windows(windows)).hexdigest())


def assert_clean(report: Any) -> None:
    """The gate behaviour: raise the first violation with the code the plan
    would have refused it with, or return None if every window is clean or
    not applicable."""
    if type(report) is not CatalogueRunReport:
        raise RunnerRefused(
            RUNNER_REPORT_NOT_DECLARED,
            f"assert_clean reads a CatalogueRunReport; got "
            f"{type(report).__name__}")
    for verdict in report.violations():
        raise RunnerRefused(verdict.refusal_code, verdict.detail)
    return None
