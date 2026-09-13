"""A bounded foreground runner for operator-declared position fixes.

§17 slice 10g, implementing the entry half of PROMOTION_EXECUTION_CONTRACT.md
§13l Amendment M. **Code only. This module has never been run against a
person**, and running it is a separately authorized act (M.8 step 4).

What it does: prompt for twelve latitude/longitude pairs a person reads off a
display and types, validate each one, stamp it, and publish exactly one v2
artefact. What it must not do is anything that would let the record claim more
than that.

Three refusals carry the design:

  **No terminal, no run.** Piped or redirected stdin is refused before the
  first prompt. A file of coordinates entered at machine speed is not a walk,
  and the time authority here is the moment *this process accepted a typed
  line* -- which is meaningless if nobody typed.

  **The deadline outlives nothing.** The wait is `select` against a monotonic
  deadline, never a blocking read. `input()` cannot be interrupted by a clock,
  so a run using one would end when the operator felt like it rather than at
  240 seconds. No thread, subprocess, signal timer, socket or scheduler is
  used to work around that -- the wait itself is bounded.

  **Nothing is published until everything is valid.** Fixes live in bounded
  memory; the producer stays disabled until the complete set has passed every
  check; and a timeout, a malformed line or a changed path publishes neither
  artefact nor observation record.

**Coordinates never leave this process except inside the artefact.** Terminal
echo is disabled while a fix is typed, because a session may be recorded.
Progress is `FIX_ACCEPTED 3/12` and nothing else. No refusal detail, log line,
filename or exception message here carries a coordinate -- a bounded code is
the whole answer, and the value that caused it is not part of it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import os
import re
import select
import stat
import termios
import time
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Tuple

from rf_walk_transitions import walk_signature
from scythe_derived_evidence import (
    ArtefactRefused, MAX_NUMERIC_DECIMALS, refuse_instrument_declaration,
    refuse_scalar,
)
from scythe_derived_evidence_producer import DerivedEvidenceProducer, ProducerRefused
from scythe_shadow_observation import lineage_snapshot, refuse_record_path

SCHEMA = "scythe.position-entry.v1"

# -- bounds, from §13l M.5 -------------------------------------------------
#
# Not runtime knobs, for §13f G.4's reason: a limit a deployment can raise will
# be raised at the moment it first binds. The spacing is a target for the
# operator and is deliberately **not** enforced -- refusing a fix for arriving
# early would teach an operator to wait by the clock rather than walk.
REQUIRED_FIXES = 12
TARGET_SPACING_S = 15.0
MAX_RUN_MONOTONIC_S = 240.0

MAX_LINE_BYTES = 128

# -- refusals ---------------------------------------------------------------
ENTRY_NOT_INTERACTIVE = "ENTRY_NOT_INTERACTIVE"
ENTRY_DEADLINE_REACHED = "ENTRY_DEADLINE_REACHED"
FIX_REFUSED = "FIX_REFUSED"
ENTRY_SET_INCOMPLETE = "ENTRY_SET_INCOMPLETE"
RUN_PRECONDITION_REFUSED = "RUN_PRECONDITION_REFUSED"
ENTRY_REFUSALS: Tuple[str, ...] = (
    ENTRY_NOT_INTERACTIVE, ENTRY_DEADLINE_REACHED, FIX_REFUSED,
    ENTRY_SET_INCOMPLETE, RUN_PRECONDITION_REFUSED,
)

# The only thing this runner prints beside a refusal code.
FIX_ACCEPTED = "FIX_ACCEPTED"

# -- the accepted number form ----------------------------------------------
#
# Not `float()`. `float` accepts "nan", "inf", "1e400", "1_0" and whatever the
# platform's strtod happens to like, and none of those is a coordinate a person
# read off a screen. Decimal digits with an optional sign and an optional
# fractional part, and nothing else: no exponent, no separator, no comma
# decimal mark, no leading or trailing dot, no whitespace inside.
#
# The fractional bound is MAX_NUMERIC_DECIMALS, imported rather than chosen --
# §13i J.8 already declares what excessive precision is, and a second opinion
# here would be a second bound to keep in step.
DECIMAL = re.compile(r"^[+-]?[0-9]{1,3}(?:\.[0-9]{1,%d})?$" % MAX_NUMERIC_DECIMALS)

MIN_LATITUDE, MAX_LATITUDE = -90.0, 90.0
MIN_LONGITUDE, MAX_LONGITUDE = -180.0, 180.0

REQUIRED_DIRECTORY_MODE = 0o700


class EntryRefused(RuntimeError):
    """A run that did not happen. Carries a code, **never the input**.

    There is no `value` attribute and no formatting of one into `detail`. A
    refusal that quoted the line that caused it would put a coordinate into
    every traceback, log and bug report that ever repeated it -- which is the
    leak the echo suppression exists to prevent, arriving by a different door.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Fix:
    """One accepted fix, stamped once, at acceptance.

    `observed_monotonic_ns` is this process's ingestion time -- the moment the
    line was accepted -- and not the receiver's fix time (§13l M.3). The two
    differ by however long the transcription took, and the artefact says which
    one it carries.
    """

    latitude: float
    longitude: float
    observed_monotonic_ns: int


# -- the terminal seam ------------------------------------------------------

@dataclass
class Terminal:
    """Everything this runner does to a tty, in one replaceable object.

    A seam rather than direct calls, so the tests can drive a controlled
    terminal without one, and so every echo change has exactly one place to go
    wrong.
    """

    fd: int = 0

    def interactive(self) -> bool:
        return os.isatty(self.fd)

    def silence(self) -> Any:
        """Turn off echo, returning what it takes to put it back."""
        saved = termios.tcgetattr(self.fd)
        quiet = termios.tcgetattr(self.fd)
        quiet[3] = quiet[3] & ~termios.ECHO
        termios.tcsetattr(self.fd, termios.TCSADRAIN, quiet)
        return saved

    def restore(self, saved: Any) -> None:
        if saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, saved)

    def ready(self, timeout_s: float) -> bool:
        """Foreground wait, bounded. Never a blocking read."""
        readable, _w, _x = select.select([self.fd], [], [], max(0.0, timeout_s))
        return bool(readable)

    def read(self, count: int) -> bytes:
        return os.read(self.fd, count)

    def say(self, message: str) -> None:
        """Bounded progress only. Nothing here formats a coordinate."""
        os.write(2, (message + "\n").encode("ascii", "replace"))


# -- validation -------------------------------------------------------------

def parse_fix(line: str) -> Tuple[float, float]:
    """`latitude longitude`, or refused. **Nothing is echoed back.**

    Exactly two fields: a third is not extra information, it is a line this
    runner does not understand, and guessing which two of three were meant is
    the interpolation M.7 forbids wearing a smaller hat.
    """
    fields = line.split()
    if len(fields) != 2:
        raise EntryRefused(FIX_REFUSED,
                           f"a fix is two fields; {len(fields)} were entered")
    values: List[float] = []
    for name, text in zip(("latitude", "longitude"), fields):
        if not DECIMAL.match(text):
            raise EntryRefused(
                FIX_REFUSED,
                f"{name} is not a plain decimal number with at most "
                f"{MAX_NUMERIC_DECIMALS} fractional digits")
        values.append(float(text))

    latitude, longitude = values
    if not MIN_LATITUDE <= latitude <= MAX_LATITUDE:
        raise EntryRefused(FIX_REFUSED, "latitude is outside [-90, 90]")
    if not MIN_LONGITUDE <= longitude <= MAX_LONGITUDE:
        raise EntryRefused(FIX_REFUSED, "longitude is outside [-180, 180]")
    for name, value in (("latitude", latitude), ("longitude", longitude)):
        try:
            refuse_scalar(name, value)
        except ArtefactRefused as refused:
            # The reader's own bound, applied here as defence in depth -- and
            # re-raised without its detail, because that detail names the value.
            raise EntryRefused(FIX_REFUSED,
                               f"{name}: {refused.code}") from None
    return latitude, longitude


# -- the runner -------------------------------------------------------------

@dataclass
class PositionEntryRun:
    """One bounded foreground run. Off until `collect` is called, and never
    reachable by a default, a schedule or a background task.

    Every declaration below is a required field. None has a default, because a
    default is the claim made silently, and the claims here -- what the
    instrument did, which authority the positions carry -- are exactly the ones
    §13l exists to keep a program from making on an operator's behalf.
    """

    derived_directory: str
    records_directory: str
    lineage_root: str
    run_id: str
    device_id: str
    receiver_state_chain_hash: str
    signal_chain_hash: str
    configuration_epoch: int
    monotonic_source_id: str
    pose_uncertainty_m: float
    claim_sources: Mapping[str, str]
    configuration_identity: str
    measurement_status: str
    instrument_state: str
    instrument_settings: Mapping[str, Any] = field(default_factory=dict)
    terminal: Terminal = field(default_factory=Terminal)
    clock: Callable[[], int] = time.monotonic_ns

    def __post_init__(self) -> None:
        self._fixes: List[Fix] = []
        self._canonical = self._resolved()

    # -- paths ------------------------------------------------------------

    def _resolved(self) -> Dict[str, str]:
        return {
            "derived": os.path.realpath(self.derived_directory),
            "records": os.path.realpath(self.records_directory),
            "lineage": os.path.realpath(
                os.path.dirname(os.path.abspath(self.lineage_root))),
        }

    def _require_paths(self) -> None:
        """Resolved, disjoint, and 0700 -- checked at the start and again
        immediately before publication.

        Twice, because a path is a name and can be repointed between the two
        moments (§13i J.7). A run that checked once would attest a directory it
        had stopped writing to.
        """
        if self._resolved() != self._canonical:
            raise EntryRefused(RUN_PRECONDITION_REFUSED,
                               "a pinned path no longer resolves where it did")
        for name in ("derived", "records"):
            directory = self._canonical[name]
            try:
                info = os.stat(directory)
            except OSError:
                raise EntryRefused(
                    RUN_PRECONDITION_REFUSED,
                    f"the {name} directory is not there") from None
            if not stat.S_ISDIR(info.st_mode):
                raise EntryRefused(RUN_PRECONDITION_REFUSED,
                                   f"the {name} path is not a directory")
            if stat.S_IMODE(info.st_mode) != REQUIRED_DIRECTORY_MODE:
                raise EntryRefused(
                    RUN_PRECONDITION_REFUSED,
                    f"the {name} directory is not mode "
                    f"{oct(REQUIRED_DIRECTORY_MODE)}")
            try:
                refuse_record_path(os.path.join(directory, "probe"),
                                   self.lineage_root)
            except Exception:
                raise EntryRefused(
                    RUN_PRECONDITION_REFUSED,
                    f"the {name} directory resolves inside the lineage "
                    f"namespace") from None
        if self._canonical["derived"] == self._canonical["records"]:
            raise EntryRefused(RUN_PRECONDITION_REFUSED,
                               "the two output directories are one directory")

    def _require_declarations(self) -> None:
        try:
            refuse_instrument_declaration(dict(
                self.instrument_settings,
                measurement_status=self.measurement_status,
                instrument_state=self.instrument_state))
        except ArtefactRefused as refused:
            raise EntryRefused(RUN_PRECONDITION_REFUSED,
                               refused.code) from None

    # -- entry ------------------------------------------------------------

    def collect(self) -> Tuple[Fix, ...]:
        """Twelve fixes or a refusal. Publishes nothing.

        Echo is disabled for the whole of entry and restored in `finally`,
        including on the deadline and on a refused line: a run that left a
        terminal silent would be worse than one that echoed, because the next
        thing typed into it would be invisible to the person typing it.
        """
        self._require_paths()
        self._require_declarations()
        if not self.terminal.interactive():
            raise EntryRefused(
                ENTRY_NOT_INTERACTIVE,
                "stdin is not a terminal; a file of coordinates entered at "
                "machine speed is not a walk, and the accepted time authority "
                "is the moment a typed line was accepted")

        started = self.clock()
        deadline = started + int(MAX_RUN_MONOTONIC_S * 1_000_000_000)
        saved = None
        try:
            saved = self.terminal.silence()
            while len(self._fixes) < REQUIRED_FIXES:
                line = self._read_line(deadline)
                latitude, longitude = parse_fix(line)
                # Stamped here and once: after validation, never before, so a
                # refused line leaves no timestamp behind and an accepted one
                # is stamped at the moment it became a fix.
                self._fixes.append(Fix(latitude=latitude, longitude=longitude,
                                       observed_monotonic_ns=self.clock()))
                self.terminal.say(
                    f"{FIX_ACCEPTED} {len(self._fixes)}/{REQUIRED_FIXES}")
        finally:
            self.terminal.restore(saved)
        return tuple(self._fixes)

    def _read_line(self, deadline: int) -> str:
        """One line, or the deadline. Bounded in bytes as well as in time."""
        buffer = b""
        while True:
            remaining = (deadline - self.clock()) / 1_000_000_000
            if remaining <= 0:
                raise EntryRefused(
                    ENTRY_DEADLINE_REACHED,
                    f"{MAX_RUN_MONOTONIC_S} monotonic seconds reached with "
                    f"{len(self._fixes)} of {REQUIRED_FIXES} fixes accepted")
            if not self.terminal.ready(remaining):
                continue
            chunk = self.terminal.read(MAX_LINE_BYTES)
            if not chunk:
                raise EntryRefused(ENTRY_NOT_INTERACTIVE,
                                   "the terminal closed during entry")
            buffer += chunk
            if len(buffer) > MAX_LINE_BYTES:
                raise EntryRefused(FIX_REFUSED,
                                   f"a fix is at most {MAX_LINE_BYTES} bytes")
            if b"\n" in buffer:
                line, _sep, _rest = buffer.partition(b"\n")
                return line.decode("ascii", "replace")

    # -- publication ------------------------------------------------------

    @contextmanager
    def _producing(self, producer: DerivedEvidenceProducer) -> Iterator[None]:
        """The only window in which the producer is enabled.

        Disabled on the way out whatever happened, so a refusal mid-publication
        cannot leave a writer armed for whatever runs next in this process.
        """
        producer.enabled = True
        try:
            yield
        finally:
            producer.enabled = False

    def publish(self, fixes: Tuple[Fix, ...]) -> Dict[str, Any]:
        """Exactly one artefact, from a complete and revalidated set.

        Everything is checked again here rather than trusted from `collect`:
        the set's size, every fix's exact type, the paths, the lineage, the
        declarations. The gap between entry and publication is where a path can
        be repointed and a lineage can appear, and this is the last moment
        either can be noticed.
        """
        if len(fixes) != REQUIRED_FIXES:
            raise EntryRefused(
                ENTRY_SET_INCOMPLETE,
                f"{len(fixes)} of {REQUIRED_FIXES} fixes; a short set is not "
                f"completed by repeating or interpolating one")
        for fix in fixes:
            # Nominal, never `isinstance`: a structurally compatible substitute
            # is exactly the impostor that would carry an unstamped value in.
            if type(fix) is not Fix or type(fix.latitude) is not float \
                    or type(fix.longitude) is not float \
                    or type(fix.observed_monotonic_ns) is not int \
                    or type(fix.observed_monotonic_ns) is bool:
                raise EntryRefused(RUN_PRECONDITION_REFUSED,
                                   "a fix is not the declared type")
        stamps = [fix.observed_monotonic_ns for fix in fixes]
        if stamps != sorted(stamps) or len(set(stamps)) != len(stamps):
            raise EntryRefused(
                RUN_PRECONDITION_REFUSED,
                "the fixes are not in strictly increasing ingestion order")

        self._require_paths()
        self._require_declarations()
        before = lineage_snapshot(self.lineage_root)

        producer = DerivedEvidenceProducer(
            directory=self._canonical["derived"],
            run_id=self.run_id,
            device_id=self.device_id,
            signal_chain_hash=self.signal_chain_hash,
            configuration_epoch=self.configuration_epoch,
            monotonic_source_id=self.monotonic_source_id,
            claim_sources=dict(self.claim_sources),
            configuration_identity=self.configuration_identity,
            measurement_status=self.measurement_status,
            instrument_state=self.instrument_state,
            instrument_settings=dict(self.instrument_settings),
            enabled=False)
        try:
            with self._producing(producer):
                for before_fix, after_fix in zip(fixes, fixes[1:]):
                    producer.record_walk_step(self._signature(before_fix),
                                              self._signature(after_fix))
                published = producer.publish()
        except ProducerRefused as refused:
            raise EntryRefused(RUN_PRECONDITION_REFUSED, refused.code) from None
        self._require_producer_disabled(producer)

        after = lineage_snapshot(self.lineage_root)
        return {
            "schema": SCHEMA,
            "artefact_path": published["path"],
            "artifact_id": published["artifact_id"],
            "content_digest": published["content_digest"],
            "records": published["records"],
            "fixes": len(fixes),
            "lineage_presence_before": before.presence,
            "lineage_presence_after": after.presence,
            "measurement_status": self.measurement_status,
            "instrument_state": self.instrument_state,
        }

    def _require_producer_disabled(self, producer: DerivedEvidenceProducer) -> None:
        """The second layer under `_producing`.

        The scope disables on the way out; this notices if it did not. Its own
        method so a control can remove either layer alone and see the other
        still catch the fault.
        """
        if producer.enabled:
            raise EntryRefused(RUN_PRECONDITION_REFUSED,
                               "the producer is still enabled after publication")

    def _signature(self, fix: Fix) -> Mapping[str, Any]:
        return walk_signature(
            device_id=self.device_id,
            receiver_state_chain_hash=self.receiver_state_chain_hash,
            monotonic_source_id=self.monotonic_source_id,
            signal_chain_hash=self.signal_chain_hash,
            configuration_epoch=self.configuration_epoch,
            latitude=fix.latitude, longitude=fix.longitude,
            observed_monotonic_ns=fix.observed_monotonic_ns,
            pose_uncertainty_m=self.pose_uncertainty_m)

    # -- the whole act ----------------------------------------------------

    def run(self) -> Dict[str, Any]:
        """Collect, then publish. A refusal anywhere publishes nothing."""
        return self.publish(self.collect())

    def status(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "required_fixes": REQUIRED_FIXES,
            "target_spacing_s": TARGET_SPACING_S,
            "max_run_monotonic_s": MAX_RUN_MONOTONIC_S,
            "bounds_are_configurable": False,
            "refusals": list(ENTRY_REFUSALS),
            "accepted": len(self._fixes),
            "acquires": False,
            "opens_a_socket": False,
            "schedules": False,
            "measurement_status": self.measurement_status,
            "instrument_state": self.instrument_state,
            "note": ("COORDINATES REACH THE ARTEFACT AND NOTHING ELSE. NO "
                     "REFUSAL, MESSAGE OR FILENAME HERE CARRIES ONE"),
        }
