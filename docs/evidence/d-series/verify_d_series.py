"""Prove each §5.20 3d compulsion control is killed by its witness test.

The full harness sweep runs the whole suite against every mutant; this proves
the same property one control at a time, cheaply, so the D-series can be trusted
before a sweep is spent. For each control it confirms the witness passes on the
clean tree, applies the control's mutation in place, confirms the witness now
fails (the kill), and restores the file. A control whose witness still passes
under the mutation is a side route left open, and the script reports it non-zero.

Run from anywhere:  python3 docs/evidence/d-series/verify_d_series.py
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
    "controls_3d", os.path.join(_HERE, "controls_3d.py"))
controls_3d = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(controls_3d)

PYTHON = sys.executable


def _run_witness(module, method):
    """Return True if the witness passes, False if it fails."""
    proc = subprocess.run(
        [PYTHON, "-m", "unittest", "-k", method, module[:-3]],
        cwd=_REPO, capture_output=True, text=True)
    return proc.returncode == 0


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
    return originals


def _restore(originals):
    for full, text in originals.items():
        open(full, "w").write(text)


def main():
    problems = []
    print(f"repository: {_REPO}\n")
    inventory = controls_3d.check_inventory()
    if inventory:
        problems.extend(inventory)
    for cid_prose, mutations in controls_3d.CONTROLS:
        cid = cid_prose.split()[0]
        module, method = controls_3d.WITNESS[cid]
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
        print("D-SERIES INCOMPLETE:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"D-SERIES COMPLETE: every one of {len(controls_3d.CONTROLS)} side "
          "routes is killed by its witness. No side route survives.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
