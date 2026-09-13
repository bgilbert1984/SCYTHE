"""The entrypoint for the bounded act, and the wiring from fix to record.

§17 slice 10h, completing §13l M.8 step 5: produce, read, observe. **Code
only.** Nothing here has been executed live, and doing so is a separately
authorized act.

Two operations, and the default is the one that does nothing:

  `--preflight`   resolve and check everything, print bounded facts, exit.
                  **No terminal is touched, no fix is accepted, no file is
                  written.** This is what runs when no operation is named.

  `--live-run`    the act. Requires a second, deliberately awkward switch and
                  a run identity, because a live run should not be one
                  mistyped character away from a preflight.

The sequence, once entered, is fixed and has no branches a caller can steer:

    collect 12 fixes  ->  publish one v2 artefact   (slice 10g)
                      ->  read it back              (slice 10b)
                      ->  observe the verdicts      (slice 10a/10e)
                      ->  publish one record        into the pinned directory

The read is a real read, from disk, through the merged reader -- not the
in-memory fixes handed sideways to the observer. An artefact nobody can read is
not evidence, and the only way to know it can be read is to read it.

**The interactive terminal is the sole entry for a coordinate** (§13m N.2).
There is no `--latitude`, no fixes file, and no environment variable. Stdin is
not excluded and could not be: the interactive terminal **is** stdin, and what
the runner refuses is stdin that is not a terminal. `argv` carries a run label and two
switches, and nothing else: **the paths and the identities are pinned in this
module**, not passed in. A test asserts mechanically that no option converts a
value.

**A published artefact is never deleted or retried.** If the artefact publishes
and the observation refuses, the act reports
`ARTEFACT_PUBLISHED_OBSERVATION_REFUSED` and stops with the artefact intact. It
is real evidence of a real walk; removing it to tidy up a failed second step
would destroy the only record of the thing that actually happened.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import stat
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from rf_signal_chain_identity import signal_chain_hash
from rf_receiver_state import (
    DEFAULT_MOUNT_UNCERTAINTY_M, receiver_state_chain_hash,
    receiver_state_chain_manifest,
)
from scythe_derived_evidence import (
    ARTEFACT_SCHEMA, CONFIGURED_NOT_EXERCISED, INSTRUMENT_CONFIGURED_IDLE,
    RF_MEASUREMENT_NOT_PERFORMED, ArtefactRefused, read_artefact,
    refuse_instrument_declaration, walk_verdicts_of,
)
from scythe_position_entry import (
    MAX_RUN_MONOTONIC_S, REQUIRED_DIRECTORY_MODE, REQUIRED_FIXES,
    EntryRefused, PositionEntryRun, Terminal,
)
from scythe_shadow_observation import (
    EVIDENCE_DERIVED_ARTEFACT, InstrumentDeclaration, ObservationRefused,
    ShadowObservation, VerdictSource, lineage_snapshot,
)

SCHEMA = "scythe.position-act.v1"

# -- the two operations -----------------------------------------------------
PREFLIGHT_ONLY = "PREFLIGHT_ONLY"
LIVE_RUN = "LIVE_RUN"
ACT_MODES: Tuple[str, ...] = (PREFLIGHT_ONLY, LIVE_RUN)

# -- outcomes ---------------------------------------------------------------
PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
PREFLIGHT_REFUSED = "PREFLIGHT_REFUSED"
ACT_COMPLETE = "ACT_COMPLETE"
ARTEFACT_PUBLISHED_OBSERVATION_REFUSED = "ARTEFACT_PUBLISHED_OBSERVATION_REFUSED"
ACT_REFUSED_BEFORE_PUBLICATION = "ACT_REFUSED_BEFORE_PUBLICATION"
ACT_OUTCOMES: Tuple[str, ...] = (
    PREFLIGHT_PASSED, PREFLIGHT_REFUSED, ACT_COMPLETE,
    ARTEFACT_PUBLISHED_OBSERVATION_REFUSED, ACT_REFUSED_BEFORE_PUBLICATION,
)

# The switch a live run needs beyond `--live-run`. Long, unmemorable and
# impossible to produce by a slip of the hand: a live act should not be a
# mistyped character away from a preflight.
LIVE_RUN_CONFIRMATION = "I-AM-WALKING-NOW"

# -- the pinned act, from the authorization -------------------------------
#
# Declared here rather than accepted from argv, because these are the
# authorization's terms and not a caller's options. A flag that could change
# them would make the pinned decision advisory.
DERIVED_DIRECTORY = "/home/spectrcyde/scythe-live-observation/derived"
RECORDS_DIRECTORY = "/home/spectrcyde/scythe-live-observation/records"
LINEAGE_ROOT = "/home/spectrcyde/scythe-ledger/promotion"

MEASUREMENT_STATUS = RF_MEASUREMENT_NOT_PERFORMED
INSTRUMENT_STATE = INSTRUMENT_CONFIGURED_IDLE

# -- the device, bounded honestly (§13m N.5c) -----------------------------
#
# A **VID/PID class identity, operator-declared**, with a non-unique collision
# domain: every 0bda:2838 on earth carries this string. No serial is attested
# because obtaining one means opening the device, which §13l M.1 forbids. The
# identity a declaration can support is the one recorded.
DEVICE_VID_PID = "0bda:2838"
DEVICE_ID = "RTL2838-0BDA-2838"
DEVICE_IDENTITY_SCOPE = "OPERATOR_DECLARED_VID_PID_CLASS"
DEVICE_IDENTITY_UNIQUE = False
DEVICE_IDENTITY_NOTE = (
    "A USB VID/PID CLASS, OPERATOR-DECLARED. EVERY 0bda:2838 CARRIES IT. NO "
    "SERIAL IS ATTESTED BECAUSE OBTAINING ONE MEANS OPENING THE DEVICE")

# The scope is carried in these declared fields rather than spelled into
# `DEVICE_ID`. Widening the string to say CLASS would change both chain digests
# for no gain -- the digest tracks the declaration, so a cosmetic rename
# invalidates every value a reviewer has already reproduced. What the identity
# covers is a fact about it, and a fact belongs in a field.

CONFIGURATION_EPOCH = 1
MONOTONIC_SOURCE_ID = "host-monotonic-ns"
POSE_UNCERTAINTY_M = 10.0

# -- the declared signal chain (§13m N.5, N.5b) ---------------------------
#
# Every input is passed explicitly. `rf_signal_chain_identity` resolves nothing
# and has no defaults, so an omission is a TypeError rather than a silent
# lookup -- but the declaration is complete regardless, because
# `rf_iq_retention`'s resolvers (SDRPP_ANTENNA_ID, SDRPP_FEEDLINE_ID,
# SDRPP_ANTENNA_EXTENSION_MM and the feedline catalogue) are what this act is
# kept away from, and an identity that changed with the launching shell would
# identify the environment as much as the instrument.
SIGNAL_CHAIN_DECLARATION: Mapping[str, Any] = {
    "sensor_id": DEVICE_ID,
    "sample_type": "uint8",
    "sample_rate_hz": 2_400_000.0,
    "antenna": "UNDECLARED",
    "feedline": "UNDECLARED",
    "extension_mm": "UNDECLARED",
    "gain_db": 40.2,
    # Supplied rather than looked up. `rf_iq_retention` reads this from the
    # `graphops_rf_antenna` catalogue; the identity module takes it, and with no
    # feedline declared there is no length to take (§13n O.2).
    "feedline_length_m": None,
}

# -- the declared receiver state (§13m N.5) -------------------------------
#
# NOT_ATTEMPTED rather than SHARED_MONOTONIC_SOURCE. Every timestamp in the
# artefact does come from one clock -- but the position display's clock was
# never aligned to this host's, and claiming a shared source would imply the
# join §13l M.3 forbids.
RECEIVER_STATE_DECLARATION: Mapping[str, Any] = {
    "device_id": DEVICE_ID,
    "position_authority": "OPERATOR_DECLARED",
    "course_source": "UNDECLARED",
    "heading_source": "UNDECLARED",
    "alignment_method": "NOT_ATTEMPTED",
    "mount_orientation": "UNDECLARED",
    "mount_uncertainty_m": DEFAULT_MOUNT_UNCERTAINTY_M,
}

CLAIM_SOURCES: Mapping[str, str] = {
    "device_id": "OPERATOR_DECLARED",
    "signal_chain_hash": "OPERATOR_DECLARED",
    "configuration_epoch": "OPERATOR_DECLARED",
    "monotonic_source_id": "scythe_position_entry",
}

INSTRUMENT_SETTINGS: Mapping[str, Any] = {
    "sample_rate_hz": 2_400_000,
    "sample_rate_hz_exercise": CONFIGURED_NOT_EXERCISED,
    "gain_db": 40.2,
    "gain_db_exercise": CONFIGURED_NOT_EXERCISED,
}

# Capabilities this act must not have. Checked over the static first-party
# import closure, not by promise and not by `sys.modules`.
FORBIDDEN_MODULES: Tuple[str, ...] = (
    "socket", "threading", "subprocess", "signal", "multiprocessing",
    "asyncio", "sched", "http", "urllib", "concurrent", "ctypes", "usb",
)

# §13m N.6a. A **closed, named** set, because "any dynamic import" is wider
# than an AST can establish. What is published is detection of these four and
# nothing broader: an attribute reached through `getattr`, or a C extension
# calling back into the import machinery, is not detected and the claim does
# not cover it.
DYNAMIC_IMPORT_CONSTRUCTS: Tuple[str, ...] = (
    "__import__", "importlib", "runpy", "eval", "exec",
)
CAPABILITY_CLAIM = (
    "STATIC FIRST-PARTY IMPORT REACHABILITY, PLUS DETECTION OF THE NAMED "
    "DYNAMIC-IMPORT CONSTRUCT SET. NOT A PROOF OF RUNTIME CAPABILITY")


# -- the computed identities (§13m N.5) -----------------------------------
#
# Functions, not constants. **The act accepts no identity from a caller and
# computes all three itself** -- which is the enforceable boundary, because an
# evaluated digest and an identical literal are the same string and nothing at
# run time can ask which one it was. Where a value came from is a property of
# the code, and the guarantee is 37ah's control substituting a literal.

ACT_CONFIGURATION_SCHEMA = "scythe.position-act-configuration.v1"

# §13m N.5a, closed. A field not here is not hashed; a field here that is
# missing is a refusal rather than an omission.
ACT_CONFIGURATION_FIELDS: Tuple[str, ...] = (
    "schema", "device_id", "signal_chain_hash", "receiver_state_chain_hash",
    "configuration_epoch", "monotonic_source_id", "pose_uncertainty_m",
    "measurement_status", "instrument_state", "instrument_settings",
    "required_fixes", "max_run_monotonic_s", "artefact_schema",
)


def declared_signal_chain_hash() -> str:
    """The chain identity, through the repository's own helper."""
    return signal_chain_hash(**SIGNAL_CHAIN_DECLARATION)


def declared_receiver_state_chain_hash() -> str:
    """The receiver-state identity, through the repository's own helper."""
    return receiver_state_chain_hash(
        receiver_state_chain_manifest(**RECEIVER_STATE_DECLARATION))


def act_configuration_manifest() -> Dict[str, Any]:
    """The **complete semantic configuration declaration** (§13m N.5a).

    What the instrument and the run were declared to be. **Deployment paths are
    not in it** and are resolved and reported separately by the preflight: where
    an artefact is written is not part of what the instrument was configured as,
    and folding a directory in would make a relocated output look like a
    different instrument.
    """
    return {
        "schema": ACT_CONFIGURATION_SCHEMA,
        "device_id": DEVICE_ID,
        "signal_chain_hash": declared_signal_chain_hash(),
        "receiver_state_chain_hash": declared_receiver_state_chain_hash(),
        "configuration_epoch": CONFIGURATION_EPOCH,
        "monotonic_source_id": MONOTONIC_SOURCE_ID,
        "pose_uncertainty_m": POSE_UNCERTAINTY_M,
        "measurement_status": MEASUREMENT_STATUS,
        "instrument_state": INSTRUMENT_STATE,
        "instrument_settings": dict(INSTRUMENT_SETTINGS),
        "required_fixes": REQUIRED_FIXES,
        "max_run_monotonic_s": MAX_RUN_MONOTONIC_S,
        "artefact_schema": ARTEFACT_SCHEMA,
    }


def act_configuration_identity(manifest: Optional[Mapping[str, Any]] = None) -> str:
    """§13m N.5a. The same canonical convention as the two chain helpers.

    Shared convention, independent verification: the encoding is settled here
    and says nothing about this schema, this field set, or these values, each
    of which is recomputed and checked on its own.
    """
    material = dict(act_configuration_manifest() if manifest is None else manifest)
    missing = [name for name in ACT_CONFIGURATION_FIELDS if name not in material]
    extra = [name for name in material if name not in ACT_CONFIGURATION_FIELDS]
    if missing or extra:
        raise ActRefused(
            ACT_REFUSED_BEFORE_PUBLICATION,
            f"the configuration manifest is not the declared field set; "
            f"missing {sorted(missing)}, unexpected {sorted(extra)}")
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    return "blake2s:" + hashlib.blake2s(canonical, digest_size=16).hexdigest()


class ActRefused(RuntimeError):
    """The act did not happen, or did not finish. A code, never a coordinate."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# -- identities -------------------------------------------------------------

def run_identity(label: str) -> str:
    """Deterministic from the operator's label. The same label is the same run.

    Not a timestamp and not a random nonce: the producer is request-addressed,
    so a repeated identity rediscovers the artefact it already published rather
    than writing a second one (§13k L.3). That is the behaviour a retry should
    have, and it only works if the identity is a function of what the operator
    said rather than of when they said it.
    """
    if not label or len(label) > 64 or not all(
            character.isalnum() or character in "-_" for character in label):
        raise ActRefused(
            ACT_REFUSED_BEFORE_PUBLICATION,
            "a run label is 1..64 characters of letters, digits, - and _")
    return f"position-attestation-{label}"


def expected_artefact_path(run_id: str) -> str:
    """What the producer will address, computed without constructing one."""
    import hashlib

    stamp = hashlib.blake2s(run_id.encode("utf-8"), digest_size=8).hexdigest()
    return os.path.join(DERIVED_DIRECTORY, f"derived.{stamp}.jsonl")


def expected_record_path(run_id: str) -> str:
    """One record per run, named by the run. `publish_record` refuses a second
    at one path rather than extending the first, so a repeated live run stops
    here rather than accumulating observations of one walk."""
    return os.path.join(RECORDS_DIRECTORY, f"observation.{run_id}.json")


# -- preflight --------------------------------------------------------------

def preflight(run_id: str) -> Dict[str, Any]:
    """Everything the act depends on, checked and reported. Writes nothing.

    No terminal is opened, no echo is changed, no fix is accepted and no file
    is created. Every value below is a path, a mode, a closed vocabulary
    member, a boolean or a count -- there is nothing here that could carry a
    coordinate, because nothing here has ever seen one.
    """
    findings: List[str] = []
    resolved = {
        "derived": os.path.realpath(DERIVED_DIRECTORY),
        "records": os.path.realpath(RECORDS_DIRECTORY),
        "lineage_namespace": os.path.realpath(
            os.path.dirname(os.path.abspath(LINEAGE_ROOT))),
    }

    modes: Dict[str, Optional[str]] = {}
    for name in ("derived", "records"):
        try:
            info = os.stat(resolved[name])
        except OSError:
            modes[name] = None
            findings.append(f"{name} directory absent")
            continue
        modes[name] = oct(stat.S_IMODE(info.st_mode))
        if not stat.S_ISDIR(info.st_mode):
            findings.append(f"{name} is not a directory")
        elif stat.S_IMODE(info.st_mode) != REQUIRED_DIRECTORY_MODE:
            findings.append(f"{name} is not mode {oct(REQUIRED_DIRECTORY_MODE)}")

    namespace = resolved["lineage_namespace"]
    disjoint = {}
    for name in ("derived", "records"):
        inside = (resolved[name] == namespace
                  or resolved[name].startswith(namespace + os.sep))
        disjoint[name] = not inside
        if inside:
            findings.append(f"{name} resolves inside the lineage namespace")
    if resolved["derived"] == resolved["records"]:
        findings.append("the two output directories are one directory")

    try:
        snapshot = lineage_snapshot(LINEAGE_ROOT)
        presence, generations = snapshot.presence, len(snapshot.digest)
    except ObservationRefused as refused:
        presence, generations = refused.code, 0
        findings.append("the lineage namespace could not be inspected")
    if os.path.exists(namespace):
        findings.append("the pinned lineage namespace exists and must not")

    try:
        refuse_instrument_declaration(dict(
            INSTRUMENT_SETTINGS, measurement_status=MEASUREMENT_STATUS,
            instrument_state=INSTRUMENT_STATE))
        declarations_valid = True
    except ArtefactRefused as refused:
        declarations_valid = False
        findings.append(f"declarations refused: {refused.code}")

    artefact_path = expected_artefact_path(run_id)
    record_path = expected_record_path(run_id)
    for name, path in (("artefact", artefact_path), ("record", record_path)):
        if os.path.exists(path):
            findings.append(f"the expected {name} already exists")

    capabilities = _forbidden_capabilities()
    findings.extend(f"{module} is reachable" for module in capabilities)
    dynamic = _dynamic_imports()
    findings.extend(f"{entry} is a dynamic-import construct" for entry in dynamic)

    return {
        "schema": SCHEMA,
        "outcome": PREFLIGHT_PASSED if not findings else PREFLIGHT_REFUSED,
        "mode": PREFLIGHT_ONLY,
        "run_id": run_id,
        "resolved_paths": resolved,
        "directory_modes": modes,
        "required_directory_mode": oct(REQUIRED_DIRECTORY_MODE),
        "namespace_disjoint": disjoint,
        "lineage_presence": presence,
        "lineage_generations": generations,
        "lineage_namespace_exists": os.path.exists(namespace),
        "producer_enabled": False,
        "producer_note": ("THE PRODUCER IS CONSTRUCTED DISABLED AND ENABLED "
                          "ONLY INSIDE PUBLICATION. PREFLIGHT CONSTRUCTS NONE"),
        "measurement_status": MEASUREMENT_STATUS,
        "instrument_state": INSTRUMENT_STATE,
        "instrument_settings_labelled": sorted(INSTRUMENT_SETTINGS),
        "declarations_valid": declarations_valid,
        "expected_artefact_path": artefact_path,
        "expected_record_path": record_path,
        "required_fixes": REQUIRED_FIXES,
        "max_run_monotonic_s": MAX_RUN_MONOTONIC_S,
        "expected_walk_steps": REQUIRED_FIXES - 1,
        "artefact_schema": ARTEFACT_SCHEMA,
        "forbidden_modules_reachable": capabilities,
        "dynamic_import_constructs_detected": dynamic,
        "dynamic_import_constructs_checked": list(DYNAMIC_IMPORT_CONSTRUCTS),
        "capability_claim": CAPABILITY_CLAIM,
        "first_party_modules": _first_party_closure(),
        "device_identity_scope": DEVICE_IDENTITY_SCOPE,
        "device_identity_unique": DEVICE_IDENTITY_UNIQUE,
        "device_identity_note": DEVICE_IDENTITY_NOTE,
        "signal_chain_hash": declared_signal_chain_hash(),
        "receiver_state_chain_hash": declared_receiver_state_chain_hash(),
        "configuration_identity": act_configuration_identity(),
        "configuration_fields": list(ACT_CONFIGURATION_FIELDS),
        "opens_a_device": False,
        "opens_a_socket": False,
        "starts_a_process": False,
        "schedules": False,
        "findings": findings,
        "note": ("PREFLIGHT WRITES NOTHING AND OPENS NO TERMINAL. EVERY VALUE "
                 "HERE IS A PATH, A MODE, A DECLARED NAME OR A COUNT"),
    }


def _first_party_closure(root: Optional[str] = None,
                         entry: Optional[str] = None) -> List[str]:
    """Every module in this repository reachable from this one by import.

    Transitive rather than direct: a capability arriving through
    `scythe_derived_evidence` would be just as much a capability as one
    imported here, and a scan of this file alone would not see it.

    `root` and `entry` are a **test seam**, and they exist for a specific
    reason: the alternative was a test that wrote a probe into a real
    repository source file and restored it afterwards. That test passes and is
    still a hazard — a crash between the write and the restore leaves the
    repository modified, and two such runs at once race each other. A seam
    costs two parameters and removes the whole class.
    """
    root = os.path.dirname(os.path.abspath(__file__)) if root is None else root
    seen: List[str] = []
    frontier = [entry if entry is not None
                else os.path.splitext(os.path.basename(__file__))[0]]
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        path = os.path.join(root, name + ".py")
        if not os.path.exists(path):
            continue
        seen.append(name)
        frontier.extend(_imports_of(path))
    return sorted(seen)


def _imports_of(path: str) -> List[str]:
    with open(path) as handle:
        tree = ast.parse(handle.read())
    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.append(node.module.split(".")[0])
    return found


def _forbidden_capabilities() -> List[str]:
    """Which forbidden modules this act's own code can reach.

    A static closure over the repository's modules, **not** `sys.modules`. The
    running process is the wrong thing to measure: a test harness imports
    `signal` for its own interrupt handling, and reporting that as a capability
    of this act would be reporting the room rather than the program. What
    matters is whether the code that runs here can reach one, which is a
    question about the import graph and answerable without running anything.
    """
    root = os.path.dirname(os.path.abspath(__file__))
    reachable: List[str] = []
    for module in _first_party_closure():
        for imported in _imports_of(os.path.join(root, module + ".py")):
            if imported in FORBIDDEN_MODULES and imported not in reachable:
                reachable.append(f"{imported} (via {module})")
    return sorted(reachable)


def _dynamic_imports(root: Optional[str] = None,
                     entry: Optional[str] = None) -> List[str]:
    """§13m N.6a. Detection of the named set, and of nothing wider.

    The constructs that would silently void the static closure's claim must not
    be the ones the check cannot see -- so the four are named, looked for, and
    the report says which four were looked for.
    """
    root = os.path.dirname(os.path.abspath(__file__)) if root is None else root
    found: List[str] = []
    for module in _first_party_closure(root, entry):
        with open(os.path.join(root, module + ".py")) as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                name = node.func.id if node.func.id in DYNAMIC_IMPORT_CONSTRUCTS \
                    else None
            elif isinstance(node, ast.Attribute):
                name = _root_name(node)
            elif isinstance(node, ast.Name):
                name = node.id if node.id in DYNAMIC_IMPORT_CONSTRUCTS else None
            elif isinstance(node, ast.Import):
                name = next((alias.name.split(".")[0] for alias in node.names
                             if alias.name.split(".")[0]
                             in DYNAMIC_IMPORT_CONSTRUCTS), None)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_name = node.module.split(".")[0]
                name = root_name if root_name in DYNAMIC_IMPORT_CONSTRUCTS else None
            if name is not None:
                entry = f"{name} (via {module})"
                if entry not in found:
                    found.append(entry)
    return sorted(found)


def _root_name(node: Any) -> Optional[str]:
    """`importlib.import_module` -> `importlib`; anything else -> None."""
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Name) and node.id in DYNAMIC_IMPORT_CONSTRUCTS:
        return node.id
    return None


# -- the act ----------------------------------------------------------------

def live_run(run_id: str, terminal: Optional[Terminal] = None,
             clock: Optional[Callable[[], int]] = None) -> Dict[str, Any]:
    """Collect, publish, read back, observe, publish the record.

    The order is fixed and the caller steers none of it. Every step's refusal
    stops the act where it stands; none is retried, and nothing already written
    is removed.
    """
    checks = preflight(run_id)
    if checks["outcome"] != PREFLIGHT_PASSED:
        raise ActRefused(ACT_REFUSED_BEFORE_PUBLICATION,
                         f"preflight findings: {len(checks['findings'])}")

    run = PositionEntryRun(
        derived_directory=DERIVED_DIRECTORY,
        records_directory=RECORDS_DIRECTORY,
        lineage_root=LINEAGE_ROOT,
        run_id=run_id,
        device_id=DEVICE_ID,
        receiver_state_chain_hash=declared_receiver_state_chain_hash(),
        signal_chain_hash=declared_signal_chain_hash(),
        configuration_epoch=CONFIGURATION_EPOCH,
        monotonic_source_id=MONOTONIC_SOURCE_ID,
        pose_uncertainty_m=POSE_UNCERTAINTY_M,
        claim_sources=dict(CLAIM_SOURCES),
        configuration_identity=act_configuration_identity(),
        measurement_status=MEASUREMENT_STATUS,
        instrument_state=INSTRUMENT_STATE,
        instrument_settings=dict(INSTRUMENT_SETTINGS),
        **({"terminal": terminal} if terminal is not None else {}),
        **({"clock": clock} if clock is not None else {}))

    try:
        published = run.run()
    except EntryRefused as refused:
        raise ActRefused(ACT_REFUSED_BEFORE_PUBLICATION, refused.code) from None

    # From here an artefact exists on disk, and the act's obligations change:
    # nothing below may remove it, rewrite it, or run again to replace it.
    return _observe(run_id, published)


def _observe(run_id: str, published: Mapping[str, Any]) -> Dict[str, Any]:
    """Read the published artefact back and observe it.

    Read from disk through the merged reader rather than reusing the fixes the
    runner still holds. An artefact nobody can read is not evidence, and
    handing the observer the in-memory values would prove only that this
    process can talk to itself.
    """
    artefact_path = published["path"] if "path" in published \
        else published["artefact_path"]
    record_path = expected_record_path(run_id)
    try:
        artefact = read_artefact(artefact_path)
        source = VerdictSource(
            declaration=InstrumentDeclaration.from_artefact(artefact),
            verdicts=walk_verdicts_of(artefact))
        observation = ShadowObservation(
            lineage_root=LINEAGE_ROOT,
            record_path=record_path,
            verdict_limit=REQUIRED_FIXES,
            duration_s=MAX_RUN_MONOTONIC_S,
            evidence_source=EVIDENCE_DERIVED_ARTEFACT,
            require_live=True)
        record = observation.run(source)
    except (ArtefactRefused, ObservationRefused) as refused:
        # The artefact stands. It is evidence of a walk that happened, and
        # deleting it to tidy up a failed second step would destroy the only
        # record of the thing that did work.
        raise ActRefused(
            ARTEFACT_PUBLISHED_OBSERVATION_REFUSED,
            f"{refused.code}; the artefact at "
            f"{os.path.basename(artefact_path)} stands and is neither "
            f"removed nor republished") from None

    return {
        "schema": SCHEMA,
        "outcome": ACT_COMPLETE,
        "mode": LIVE_RUN,
        "run_id": run_id,
        "artefact_path": artefact_path,
        "artifact_id": published["artifact_id"],
        "content_digest": published["content_digest"],
        "walk_steps": published["records"],
        "record_path": record_path,
        "run_class": record["run_class"],
        "evidence_source": record["evidence_source"],
        "measurement_status": record["instrument_declaration"]["measurement_status"],
        "instrument_state": record["instrument_declaration"]["instrument_state"],
        "declaration_authority": record["instrument_declaration"]["authority"],
        "verdicts_observed": record["verdicts_observed"],
        "would_promote": record["would_promote"],
        "ending": record["ending"],
        "lineage_presence_before": record["lineage_presence_before"],
        "lineage_presence_after": record["lineage_presence_after"],
        "quiescence": record["quiescence"],
    }


# -- the entrypoint ---------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """A run label and two switches. **No coordinate has an option here.**

    Not paths and not identities: those are pinned in this module, because they
    are the authorization's terms rather than a caller's options.

    There is deliberately no `--latitude`, no `--fixes-file` and no stdin
    parsing: a coordinate reaches this program through the runner's terminal or
    it does not reach it at all.
    """
    parser = argparse.ArgumentParser(
        prog="scythe_position_act",
        description="Bounded position-attestation act. Preflight by default.")
    parser.add_argument("--run-label", required=True,
                        help="letters, digits, - and _; the run identity is "
                             "derived from it and is the same for the same "
                             "label")
    parser.add_argument("--live-run", action="store_true",
                        help="perform the act. Requires --i-am-walking-now.")
    parser.add_argument("--i-am-walking-now", action="store_true",
                        help=f"the second switch a live run needs. Without it "
                             f"--live-run refuses. ({LIVE_RUN_CONFIRMATION})")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Preflight unless a live run is asked for twice, in two different ways."""
    arguments = build_parser().parse_args(argv)
    try:
        run_id = run_identity(arguments.run_label)
    except ActRefused as refused:
        print(json.dumps({"schema": SCHEMA, "outcome": PREFLIGHT_REFUSED,
                          "code": refused.code, "detail": refused.detail},
                         indent=2, sort_keys=True))
        return 2

    if not arguments.live_run:
        report = preflight(run_id)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["outcome"] == PREFLIGHT_PASSED else 1

    if not arguments.i_am_walking_now:
        print(json.dumps(
            {"schema": SCHEMA, "outcome": PREFLIGHT_REFUSED,
             "code": ACT_REFUSED_BEFORE_PUBLICATION,
             "detail": "a live run needs --live-run AND --i-am-walking-now; "
                       "one switch is a typo and two are a decision"},
            indent=2, sort_keys=True))
        return 2

    try:
        report = live_run(run_id)
    except ActRefused as refused:
        print(json.dumps({"schema": SCHEMA, "outcome": refused.code,
                          "detail": refused.detail, "run_id": run_id},
                         indent=2, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":                        # pragma: no cover
    raise SystemExit(main())
