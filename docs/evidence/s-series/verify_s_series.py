"""Prove each §5.21 catalogue-analysis control is killed by its witness test.

The full harness sweep runs the whole suite against every mutant; this proves
the same property one control at a time, cheaply, so the S-series can be trusted
before a sweep is spent. For each control it confirms the witness passes on the
clean tree, applies the control's mutation in place, confirms the witness now
fails (the kill), and restores the file. A control whose witness still passes
under the mutation is a clause with no control, and the script reports it non-zero.

Run from anywhere:  python3 docs/evidence/d-series/verify_s_series.py
"""

import importlib.util
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = _HERE
while not os.path.exists(os.path.join(_REPO, "rf_capture_admission.py")):
    parent = os.path.dirname(_REPO)
    if parent == _REPO:
        raise SystemExit("could not locate the repository root")
    _REPO = parent

_spec = importlib.util.spec_from_file_location(
    "controls_521", os.path.join(_HERE, "controls_521.py"))
controls_521 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(controls_521)

PYTHON = sys.executable


def _run_witness(module, method):
    """Return True if the witness passes, False if it fails."""
    proc = subprocess.run(
        [PYTHON, "-m", "unittest", "-k", method, module[:-3]],
        cwd=_REPO, capture_output=True, text=True)
    return proc.returncode == 0


def _forget_bytecode(full):
    """A mutation that keeps the file's size and lands within the same second
    as the clean run is invisible to the bytecode cache, which validates by
    size and whole-second mtime: S4 (`< 3` to `< 2`) SURVIVED its witness on
    the first run of this script for exactly that reason, and was killed when
    applied by hand a few seconds later. The cache is discarded on every apply
    and every restore, so what runs is what is on disk."""
    directory, name = os.path.split(full)
    cache = os.path.join(directory, "__pycache__")
    if os.path.isdir(cache):
        stem = os.path.splitext(name)[0] + "."
        for entry in os.listdir(cache):
            if entry.startswith(stem):
                os.remove(os.path.join(cache, entry))


def _apply(mutations):
    originals = {}
    for path, before, after in mutations:
        full = os.path.join(_REPO, path)
        text = open(full).read()
        if text.count(before) != 1:
            raise AssertionError(
                f"{path}: mutation anchor appears {text.count(before)} times, "
                "not once")
        originals.setdefault(full, text)
        open(full, "w").write(text.replace(before, after, 1))
        _forget_bytecode(full)
    return originals


def _restore(originals):
    for full, text in originals.items():
        open(full, "w").write(text)
        _forget_bytecode(full)


def main():
    problems = []
    print(f"repository: {_REPO}\n")
    inventory = controls_521.check_inventory()
    if inventory:
        problems.extend(inventory)
    for cid_prose, mutations in controls_521.CONTROLS:
        cid = cid_prose.split()[0]
        module, method = controls_521.WITNESS[cid]
        if not _run_witness(module, method):
            problems.append(f"{cid}: witness {method} does not pass on the "
                            "clean tree")
            print(f"{cid:4} SKIP  witness not green before mutation")
            continue
        originals = _apply(mutations)
        try:
            killed = not _run_witness(module, method)
        finally:
            _restore(originals)
        status = "KILLED" if killed else "SURVIVED"
        print(f"{cid:4} {status:9} {cid_prose[len(cid) + 1:]}")
        if not killed:
            problems.append(f"{cid}: SURVIVED -- {method} did not catch it")

    print()
    if problems:
        print("S-SERIES INCOMPLETE:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"S-SERIES COMPLETE: every one of {len(controls_521.CONTROLS)} "
          "controls is killed by its witness. No clause is without one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
