"""Worktree-isolated negative-control sweep.

Restoration is not a trusted operation here, because it was tried and it
failed: a previous harness left a mutation on disk after a CLEAN exit, and
three controls ran against files that were not the baseline. Nothing in this
harness restores anything. Every control gets a fresh detached worktree at a
fixed checkpoint, is mutated there, is measured there, and the worktree is
destroyed. No control inherits a file from another.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# Machine-specific locations.
#   SCYTHE_REPO     the git repository holding the slice branch
#   SCYTHE_SCRATCH  a writable directory for worktrees and results
#   SCYTHE_PYTHON   the interpreter that has numpy and requests
#
# The defaults below are the box this file was written on. They are RESOLVED
# AND CHECKED rather than trusted: on any other machine they do not exist, and
# a harness that silently targets nothing is worse than one that refuses.

_AUTHORING_REPO = "/home/spectrcyde/SCYTHE"
_AUTHORING_SCRATCH = ("/tmp/claude-1000/-home-spectrcyde/"
                      "8cbdba26-1530-4b3a-b2b7-1259a6aff70b/scratchpad")


def _resolved(variable, default, what):
    value = Path(os.environ.get(variable, default)).expanduser()
    if value.exists():
        # Resolved, so that containment comparisons below are made between
        # paths of the same kind. Comparing a resolved path against an
        # unresolved one silently fails whenever a component is a symlink,
        # which is how the harness-inside-the-repo check first missed.
        value = value.resolve()
    if not value.exists():
        raise SystemExit(
            f"{variable} resolves to {value}, which does not exist.\n"
            f"Export {variable} to {what}. The default is the machine this "
            f"harness was written on and is not portable.")
    return value


REPO = _resolved("SCYTHE_REPO", _AUTHORING_REPO,
                 "the git repository holding the slice branch")
if not (REPO / ".git").exists():
    raise SystemExit(f"SCYTHE_REPO is {REPO}, which is not a git repository.")

SCRATCH = _resolved("SCYTHE_SCRATCH", _AUTHORING_SCRATCH,
                    "a writable directory for worktrees and results")

# The instrument must not live inside the thing it mutates. Stated as a check,
# because the README said so first and the README was wrong on the machine it
# mattered on: an untracked harness inside SCYTHE_REPO leaves the repository
# permanently dirty while the harness shells git against it.
_HARNESS = Path(__file__).resolve().parent
for _name, _path in (("the harness", _HARNESS), ("SCYTHE_SCRATCH", SCRATCH)):
    if _path == REPO or REPO in _path.parents:
        raise SystemExit(
            f"{_name} is at {_path}, inside SCYTHE_REPO ({REPO}).\n"
            "Move it outside the repository. Nothing the sweep writes may "
            "appear in the repository's status.")

WORKTREES = SCRATCH / "wt"
PYTHON = os.environ.get("SCYTHE_PYTHON",
                        str(REPO / ".venv" / "bin" / "python3"))
if not os.access(PYTHON, os.X_OK):
    raise SystemExit(
        f"SCYTHE_PYTHON resolves to {PYTHON}, which is not executable.\n"
        "Export SCYTHE_PYTHON to an interpreter with numpy and requests.")
SUITE_FULL = ["test_rf_*.py", "test_graphops_rf_*.py", "test_scythe_*.py"]
TIMEOUT = 1800          # the FLOOR of a control's bound, in seconds
TIMEOUT_FACTOR = 10     # unmutated baselines a control may take before TIMED_OUT


def control_timeout(baseline_elapsed, floor=TIMEOUT, factor=TIMEOUT_FACTOR):
    """The bound on one control, relative to the baseline THIS host produced.

    A fixed 1800s was calibrated on one host. On a host three times slower, or
    the same host under load, a healthy control passes it and reads as
    TIMED_OUT --- an empty failing set, which a reviewer counting zeros takes
    for an unwitnessed mutation. The bound guards against a non-terminating
    mutation; it is not a performance test, so it is generous: the larger of
    the floor and `factor` baselines. The runner measures the baseline's
    elapsed time immediately before the controls, on the same host and under
    the same load, and records the bound it derived in every row.
    """
    if not baseline_elapsed or baseline_elapsed <= 0:
        return floor
    return max(floor, int(math.ceil(factor * baseline_elapsed)))

# -- classifications --------------------------------------------------------
MUTATION_DID_NOT_APPLY = "MUTATION_DID_NOT_APPLY"
MUTATION_AMBIGUOUS = "MUTATION_AMBIGUOUS"
CRASHED_BEFORE_COLLECTION = "CRASHED_BEFORE_COLLECTION"
TIMED_OUT = "TIMED_OUT"
HARNESS_ERROR = "HARNESS_ERROR"
ZERO_DISCRIMINATION = "ZERO_DISCRIMINATION"
TEST_FAILURE = "TEST_FAILURE"


def git(*args, cwd=REPO):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=True).stdout.strip()


def resolve_branch(name):
    """A branch name, then the remote-tracking ref, and say which answered.

    `git rev-parse` does not DWIM a bare branch name in a clone that holds
    only the remote ref, so a dedicated sweep clone --- which has no local
    `feat/ring-lifetime-id` --- fails outright with "Needed a single
    revision". Failing is better than measuring the wrong tree, but it made
    the operator responsible for knowing to write `origin/` themselves.

    Resolution is printed rather than silent: the whole run hangs off this
    one commit, and "which tip did it measure" must not need reconstructing.
    """
    for candidate in (name, f"origin/{name}"):
        try:
            commit = git("rev-parse", "--verify", f"{candidate}^{{commit}}")
        except subprocess.CalledProcessError:
            continue
        print(f"branch {name!r} resolved through {candidate!r} to {commit}",
              flush=True)
        return commit
    raise SystemExit(
        f"refusing to launch: {name!r} resolves neither as a branch nor as "
        f"origin/{name} in {REPO}")


def checkpoint_blobs(commit, paths):
    """The baseline each worktree must present before anything is mutated."""
    out = {}
    for path in paths:
        blob = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=str(REPO),
                              capture_output=True, check=True).stdout
        out[path] = hashlib.sha256(blob).hexdigest()
    return out


def expand(suite, tree):
    """Expand shell globs against the worktree. `subprocess` does not.

    The first isolated sweep passed `test_rf_*.py` straight to `unittest`,
    which took it as a module name -- so every control, and the BASELINE,
    reported three failing "tests" named after the patterns themselves. The
    baseline said so on its first line and the run was launched anyway, which
    is why `require_sane_baseline` exists.
    """
    # `discover` takes its own pattern via `-p`, whose job may be to match
    # nothing at all; expanding that would turn a deliberate empty run into a
    # harness crash. Only bare file patterns are expanded.
    if suite and suite[0] == "discover":
        return list(suite)
    out = []
    for item in suite:
        if item.startswith("-"):
            out.append(item)
        elif any(ch in item for ch in "*?["):
            hits = sorted(q.name for q in Path(tree).glob(item))
            if not hits:
                raise RuntimeError(f"{item!r} matched no file in {tree}")
            out.extend(hits)
        else:
            out.append(item)
    return out


def require_sane_baseline(cls, count, minimum, ids=None, proc=None,
                          report=None, expected=None):
    """The baseline is the harness's own control. Read it before proceeding.

    The refusal used to say only the classification and the count, and then
    exit --- so a baseline that failed **once** and passed on every rerun left
    nothing behind saying which tests failed. An intermittent baseline is the
    one failure that invalidates a whole sweep, and it is exactly the one this
    gate was throwing away the evidence for. The failing ids go in the message,
    and the child's whole output is written to `report` when one is given,
    because the next run may not reproduce it.

    `minimum` is a floor and a floor is not enough: a bundle whose controls
    were written for an older tree launches happily against a newer one that
    collects more tests, and nothing reports that the controls and the tree
    are from different generations. `expected` is the count the checkpoint
    is known to collect; when given, the baseline must match it exactly, so
    a bundle is tied to the checkpoint it was audited against.
    """
    stale = expected is not None and count != expected
    if (cls == ZERO_DISCRIMINATION and count is not None and count >= minimum
            and not stale):
        return
    where = ""
    if report is not None and proc is not None:
        try:
            Path(report).write_text((proc.stderr or "") + (proc.stdout or ""),
                                    encoding="utf-8")
            where = f"\n  full output: {report}"
        except OSError as exc:                            # pragma: no cover
            where = f"\n  (could not write {report}: {exc})"
    listed = ""
    if ids:
        shown = ", ".join(sorted(ids)[:20])
        more = "" if len(ids) <= 20 else f" (+{len(ids) - 20} more)"
        listed = f"\n  failing: {shown}{more}"
    if stale and cls == ZERO_DISCRIMINATION and count >= minimum:
        raise SystemExit(
            f"BASELINE collects {count} tests; this bundle was audited against "
            f"a checkpoint collecting {expected}. The controls and the tree are "
            f"from different generations{where}")
    raise SystemExit(
        f"BASELINE is {cls} with {count} tests (expected a clean run of at "
        f"least {minimum}); the instrument is not measuring the tree"
        f"{listed}{where}")


def classify(proc, elapsed):
    """The five outcomes, plus the two mutation-application ones.

    `Ran 0 tests` is HARNESS_ERROR, not a green run: a suite that collected
    nothing is evidence of nothing, and the old harness would have called it
    zero discrimination.
    """
    if proc is None:
        return TIMED_OUT, None, []
    text = (proc.stderr or "") + (proc.stdout or "")
    ran = re.search(r"^Ran (\d+) tests?", text, re.M)
    ids = sorted({m for m in re.findall(r"^(?:FAIL|ERROR): (\S+)", text, re.M)})
    # Skips are recorded too. A mutation that SUPPRESSES a witness -- by
    # routing a guarded test into skipTest -- manufactures a distinct failing
    # set without discriminating anything, and a harness that counts only
    # failures cannot tell that apart from independent evidence.
    skipped = sorted({m for m in re.findall(
        r"^(\S+) \([^)]*\) \.\.\. skipped", text, re.M)})
    classify.last_skipped = skipped
    if ran is None:
        return (CRASHED_BEFORE_COLLECTION if proc.returncode != 0
                else HARNESS_ERROR), None, ids
    count = int(ran.group(1))
    if count == 0:
        return HARNESS_ERROR, 0, ids
    if ids:
        return TEST_FAILURE, count, ids
    if proc.returncode != 0:
        return HARNESS_ERROR, count, ids
    return ZERO_DISCRIMINATION, count, ids


def unittest_argv(python, suite, tree):
    """The child's argv, with `-v` placed where `unittest` will accept it.

    `unittest.main` enters discover mode only when `argv[1] == "discover"`, so
    `-m unittest -v discover -p PATTERN` never reaches the discover parser:
    the ordinary one sees `-p` and exits 2 with "unrecognized arguments",
    which the harness reports as CRASHED_BEFORE_COLLECTION. `expand` already
    refuses to glob a discover pattern --- and that care was unreachable,
    because every caller put `-v` first. `discover -v -p PATTERN` runs and
    reports `Ran 0 tests`.

    `-v` itself is load-bearing rather than cosmetic: `classify` reads skips
    from verbose per-test lines, and a run without it records an empty skip
    set that makes every later control look like it suppressed a witness.
    """
    expanded = expand(suite, tree)
    if expanded and expanded[0] == "discover":
        return [python, "-m", "unittest", "discover", "-v", *expanded[1:]]
    return [python, "-m", "unittest", "-v", *expanded]


def run_control(cid, patches, commit, baseline, suite=None, timeout=TIMEOUT):
    """One control, in its own disposable worktree. Nothing is restored."""
    suite = SUITE_FULL if suite is None else suite
    tree = WORKTREES / cid.split()[0].replace("/", "_")
    if tree.exists():
        shutil.rmtree(tree, ignore_errors=True)
    WORKTREES.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(tree), commit],
                   cwd=str(REPO), capture_output=True, check=True)
    record = {"checkpoint": commit, "control": cid, "worktree": str(tree),
              "timeout_s": timeout}
    started = time.time()
    try:
        # 2. the baseline is verified INSIDE the worktree, before any mutation
        for rel in {p for p, _o, _n in patches}:
            got = hashlib.sha256((tree / rel).read_bytes()).hexdigest()
            if got != baseline[rel]:                      # pragma: no cover
                record.update(classification=HARNESS_ERROR,
                              detail=f"{rel} is not the checkpoint baseline")
                return record
        # 3/4. exactly one mutation, matching exactly one anchor
        for rel, old, new in patches:
            text = (tree / rel).read_text(encoding="utf-8")
            hits = text.count(old)
            if hits == 0:
                record.update(classification=MUTATION_DID_NOT_APPLY, anchor=rel)
                return record
            if hits > 1:
                record.update(classification=MUTATION_AMBIGUOUS, anchor=rel,
                              detail=f"{hits} occurrences")
                return record
            (tree / rel).write_text(text.replace(old, new, 1), encoding="utf-8")
        diff = subprocess.run(["git", "diff"], cwd=str(tree),
                              capture_output=True, text=True).stdout
        record["mutation_diff_sha256"] = hashlib.sha256(
            diff.encode()).hexdigest()
        record["mutation_lines"] = sum(
            1 for line in diff.splitlines()
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
        if not diff.strip():                              # pragma: no cover
            record.update(classification=HARNESS_ERROR,
                          detail="the mutation changed nothing")
            return record
        # 5/6. run, with a hard timeout
        popen = subprocess.Popen(
            unittest_argv(PYTHON, suite, tree),
            cwd=str(tree), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True)
        try:
            out, err = popen.communicate(timeout=timeout)
            proc = subprocess.CompletedProcess(popen.args, popen.returncode,
                                               out, err)
        except subprocess.TimeoutExpired:
            # Kill the process GROUP, then drain with a bound. `subprocess.run`
            # kills only the direct child and then calls `communicate()` with
            # no timeout; a non-terminating mutation can leave a grandchild
            # holding the pipes, and the sweep blocks there forever. That is
            # how E4 ended two runs without writing a row.
            try:
                os.killpg(os.getpgid(popen.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):   # pragma: no cover
                popen.kill()
            try:
                popen.communicate(timeout=120)
            except subprocess.TimeoutExpired:               # pragma: no cover
                pass
            proc = None
        cls, count, ids = classify(proc, time.time() - started)
        record.update(classification=cls, tests_ran=count,
                      exit_code=None if proc is None else proc.returncode,
                      failing=[i.split("(")[0].strip() for i in ids])
        return record
    finally:
        record["elapsed_s"] = round(time.time() - started, 1)
        subprocess.run(["git", "worktree", "remove", "--force", str(tree)],
                       cwd=str(REPO), capture_output=True)
        shutil.rmtree(tree, ignore_errors=True)


def working_tree_hashes(paths):
    return {p: hashlib.sha256((REPO / p).read_bytes()).hexdigest() for p in paths}
