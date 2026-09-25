#!/usr/bin/env python3
"""The seven-item review of a completed sweep, computed rather than narrated.

    python3 review_sweep.py <sweep.jsonl> [controls-module]

Reports: the table, zeros, suppression, collection degradation, duplicate
failing sets, unique-witness coverage, and completeness against the DECLARED
controls of the named module --- `controls527` by default, `controls_ring` for
the ring-lifetime slice.

The expected identifiers are derived from that module rather than written here.
They were hardcoded to 5.27's forty-three, which would have reported a
ten-control sweep as forty-three missing and ten unexpected, and never
certified. Same defect the mutation audit was parameterised to avoid: a checker
closing over one slice's declarations silently audits the wrong slice.
"""
import importlib
import itertools
import json
import os
import sys

BROAD = 40          # a control breaking more than this is a detonation, not a
                    # witness: it subsumes narrow controls and makes them look
                    # unwitnessed. Measured separately for that reason.


def main(path, module="controls527"):
    sys.path.insert(0, os.environ.get("SCYTHE_HARNESS", os.path.dirname(
        os.path.abspath(__file__))))
    declared = importlib.import_module(module)
    rows = [json.loads(l) for l in open(path) if l.strip()]
    base = [r for r in rows if r["control"] == "BASELINE"]
    ctl = [r for r in rows if r["control"] != "BASELINE"]
    sets = {r["control"].split()[0]: set(r["failing"]) for r in ctl}
    ids = sorted(sets, key=lambda i: (i[0], int(i[1:])))

    print("=" * 74)
    if not base:
        print("NO BASELINE ROW -- the run is not interpretable")
    else:
        b = base[0]
        ok = b["classification"] == "ZERO_DISCRIMINATION" and not b["failing"]
        print("BASELINE  %s  ran=%d  failing=%d  %s"
              % (b["classification"], b["tests_ran"], len(b["failing"]),
                 "OK" if ok else "<<< NOT A SANE BASELINE"))
    print("checkpoint: %s" % (rows[0].get("checkpoint", "?")))
    print("controls with rows: %d" % len(ctl))
    print()

    print("%-5s %-22s %5s %5s %7s" % ("ID", "CLASSIFICATION", "n", "ran", "UNIQ"))
    print("-" * 74)
    ran_values = {}
    for r in ctl:
        i = r["control"].split()[0]
        others = set().union(*[sets[j] for j in ids if j != i]) if len(ids) > 1 else set()
        uniq = len(sets[i] - others)
        ran_values[i] = r["tests_ran"]
        flag = ""
        if r["classification"] == "TIMED_OUT":
            # an empty failing set that is NOT a zero: the control was never
            # measured, and the bound it was held to is in the row
            flag = "  <<< TIMED_OUT at %ss (bound %ss)" % (
                r.get("elapsed_s"), r.get("timeout_s", "unrecorded"))
        elif r["classification"] != "TEST_FAILURE":
            flag = "  <<< not TEST_FAILURE"
        elif not sets[i]:
            flag = "  <<< ZERO"
        print("%-5s %-22s %5d %5s %7d%s"
              % (i, r["classification"], len(sets[i]), r["tests_ran"], uniq, flag))

    print()
    zeros = [i for i in ids if not sets[i]]
    print("ZEROS                : %s" % (zeros or "none"))

    # Empty sets are excluded. Every zero equals every other zero, so eight of
    # them reported twenty-eight duplicate pairs on top of their own ZEROS line
    # --- the same failure counted twice, drowning the two real duplicates.
    # Zeros have their own verdict; this line is about controls that
    # discriminated the same thing.
    dup = [(a, b) for a, b in itertools.combinations(ids, 2)
           if sets[a] and sets[a] == sets[b]]
    print("DUPLICATE SETS       : %s" % (dup or "none"))

    # States the ABSENCE rather than leaving it to be inferred from the
    # classification tally. A row with no `tests_ran` produced no measurement at
    # all --- it timed out, crashed before collection, or the mutation never
    # applied --- and an empty failing set from a control that never ran is not
    # the same fact as one from a control that ran and discriminated nothing.
    not_measured = [i for i in ids if ran_values.get(i) is None]
    print("NOT MEASURED         : %s" % (not_measured or "none"))

    modal = max(set(ran_values.values()), key=list(ran_values.values()).count)
    degraded = {i: n for i, n in ran_values.items() if n != modal}
    print("COLLECTION (modal %d): %s" % (modal, degraded or "uniform"))

    # `BROAD` stays an ABSOLUTE tripwire and is never recalibrated to whatever
    # a run happened to produce; that would make the gate a formality. What
    # excuses a chokepoint control from the unique-witness competition is an
    # explicit declaration, fixed in the controls module BEFORE launch, naming
    # the ID, the causal reason, and the direct witness that must keep firing.
    # "Broad" must not come to mean "any failure is good enough".
    declared_broad = dict(getattr(declared, "BROAD_DECLARED", {}))
    measured = [i for i in ids if len(sets[i]) > BROAD]
    undeclared = [i for i in measured if i not in declared_broad]
    print("MEASURED OVER %-6d : %s"
          % (BROAD, [f"{i}(n={len(sets[i])})" for i in measured] or "none"))
    print("UNDECLARED DETONATION: %s" % (undeclared or "none"))

    broad_problems = []
    for cid in sorted(declared_broad):
        entry = declared_broad[cid]
        reason, witness = entry["reason"], entry["witness"]
        if cid not in sets:
            broad_problems.append(
                f"{cid} is declared broad but has no row in this sweep")
        elif not sets[cid]:
            broad_problems.append(
                f"{cid} is declared broad but discriminated nothing; a zero is "
                f"not a detonation")
        elif witness not in sets[cid]:
            broad_problems.append(
                f"{cid}'s named direct witness {witness!r} is NOT in its "
                f"failing set --- the declaration excuses breadth, not the "
                f"loss of the property it is supposed to be testing")
        else:
            print("  DECLARED BROAD %-4s n=%-3d %s"
                  % (cid, len(sets[cid]), reason))
            print("      direct witness present: %s" % witness)
    for _p in broad_problems:
        print("  BROAD PROBLEM  %s" % _p)

    # Excluded from the unique-witness competition ONLY. Zeros, classification,
    # suppression, completeness and the witness check above all still apply.
    narrow = [i for i in ids if i not in declared_broad]
    nou = []
    for i in narrow:
        others = set().union(*[sets[j] for j in narrow if j != i]) if len(narrow) > 1 else set()
        if not (sets[i] - others):
            nou.append(i)

    # Named allowances only, and each one verified against this run's own
    # failing sets. A declared pair whose containment no longer holds is stale
    # rather than permissive: it allows nothing and is reported, because an
    # allowance that has stopped being true is how a strict gate quietly
    # becomes a formality. Nothing here exempts a subset relation in general.
    allowed, stale = {}, []
    for (weak, strong), reason in getattr(declared, "SUBSUMED", {}).items():
        if weak not in sets or strong not in sets:
            stale.append(f"{weak} subset {strong}: one of the pair has no row")
        elif not sets[weak]:
            stale.append(f"{weak} subset {strong}: {weak} discriminated "
                         f"nothing, so there is no subsumption to allow")
        elif sets[weak] == sets[strong]:
            # Equality is two spellings of one discrimination, not a weaker
            # hazard inside a stronger one. DUPLICATE SETS already refuses it
            # globally; the allowance must not be the one place it is excused.
            stale.append(f"{weak} subset {strong}: the failing sets are "
                         f"EQUAL, which is duplicate discrimination rather "
                         f"than subsumption")
        elif not (sets[weak] < sets[strong]):
            stale.append(f"{weak} subset {strong}: strict containment does "
                         f"not hold in this run")
        else:
            allowed[weak] = (strong, reason)
    unallowed = [i for i in nou if i not in allowed]
    print("NO UNIQUE WITNESS    : among %d narrow controls: %s"
          % (len(narrow), unallowed or "none"))
    for i in sorted(set(nou) & set(allowed)):
        strong, reason = allowed[i]
        print("  ALLOWED  %s subset %s -- %s" % (i, strong, reason))
    for i in sorted(set(allowed) - set(nou)):
        print("  UNUSED   %s subset %s -- it earned a unique witness; the "
              "allowance is no longer needed" % (i, allowed[i][0]))
    for s in stale:
        print("  STALE    %s" % s)

    supp = {r["control"].split()[0]: r["skips_added"] for r in ctl
            if r.get("skips_added")}
    have = any("skips_added" in r for r in ctl)
    print("SUPPRESSION          : %s"
          % (supp if supp else ("none" if have
                                else "NOT RECORDED -- runner predates the fix")))

    kinds = {}
    for r in ctl:
        kinds[r["classification"]] = kinds.get(r["classification"], 0) + 1
    print("CLASSIFICATIONS      : %s" % kinds)

    expected = [c.split()[0] for c, _patches in declared.CONTROLS]
    missing = [i for i in expected if i not in sets]
    extra = [i for i in sets if i not in expected]
    print("COMPLETENESS         : %d/%d (%s)  missing=%s  unexpected=%s"
          % (len(sets), len(expected), module, missing or "none",
             extra or "none"))

    print()
    certified = (base and not base[0]["failing"] and not zeros and not dup
                 and not unallowed and not stale and not missing and not extra
                 and not broad_problems and not undeclared
                 and not not_measured
                 and all(r["classification"] == "TEST_FAILURE" for r in ctl))
    print("VERDICT: %s" % ("every control discriminated"
                           if certified else "NOT certified -- see the flags above"))
    print("=" * 74)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sweep.jsonl",
         sys.argv[2] if len(sys.argv) > 2 else "controls527")
