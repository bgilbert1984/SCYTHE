#!/usr/bin/env python3
"""Run an explicit list of unittest modules and prove the list ran.

CI must not be green because nothing ran. Each argument is a test module name
(`test_writebus`, not a glob). The runner loads exactly those modules and
refuses to pass unless:

  * every module imports (an import error is a failure, not a missing test);
  * every module collects at least one test;
  * every collected test was executed (collected == run);
  * no test was skipped, unless --allow-skips says how many may be. A skip is
    a host-dependent gap, and a gap must be named, not absorbed.

It prints a per-module collected count and a total, so a log shows what ran.
Run it from the repository root (the test modules import their siblings).
"""

import argparse
import os
import sys
import unittest


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("modules", nargs="+", help="test module names")
    parser.add_argument("--allow-skips", type=int, default=0,
                        help="skipped tests tolerated (default 0)")
    args = parser.parse_args(argv)

    # `python tools/x.py` puts tools/ on sys.path, not the working directory.
    sys.path.insert(0, os.getcwd())

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    collected = {}
    unimportable = []
    for name in args.modules:
        if name.endswith(".py") or "/" in name:
            print(f"error: give a module name, not a path: {name!r}",
                  file=sys.stderr)
            return 2
        try:
            module_suite = loader.loadTestsFromName(name)
        except Exception as exc:  # the loader only catches ImportError
            unimportable.append((name, f"{type(exc).__name__}: {exc}"))
            continue
        # An import error comes back as one pseudo-test that fails when run.
        # Counting it as a collected test would hide the real problem.
        failed = [t for t in _flatten(module_suite)
                  if type(t).__name__ == "_FailedTest"]
        if failed:
            reason = "import failed"
            try:
                getattr(failed[0], failed[0]._testMethodName)()
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
            unimportable.append((name, reason.strip().splitlines()[-1]))
            continue
        count = module_suite.countTestCases()
        collected[name] = count
        suite.addTest(module_suite)

    if unimportable:
        print("error: cannot import: "
              + ", ".join(name for name, _ in unimportable), file=sys.stderr)
        for name, reason in unimportable:
            print(f"  {name}: {reason}", file=sys.stderr)
        return 1

    width = max(len(name) for name in collected)
    for name, count in collected.items():
        print(f"collected {name:<{width}} {count:>4}")
    total = sum(collected.values())
    print(f"collected {'TOTAL':<{width}} {total:>4}")

    empty = [name for name, count in collected.items() if count == 0]
    if empty:
        print(f"error: modules that collected no tests: {', '.join(empty)}",
              file=sys.stderr)
        return 1

    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(f"executed  {'TOTAL':<{width}} {result.testsRun:>4}"
          f"  (skipped {len(result.skipped)})")

    if result.testsRun != total:
        print(f"error: collected {total} tests but executed "
              f"{result.testsRun}", file=sys.stderr)
        return 1
    if len(result.skipped) > args.allow_skips:
        print(f"error: {len(result.skipped)} skipped test(s), "
              f"{args.allow_skips} allowed:", file=sys.stderr)
        for test, reason in result.skipped:
            print(f"  {test.id()}: {reason}", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
