"""Regression test: /NODE field widths must be exact.

The 20-character coordinate field discovery (2026-10-05): using 10-char
fields for /NODE coordinates silently misparses, producing zero-volume
errors on valid meshes. This test inspects a generated deck and refuses
wrong field widths — converting tribal knowledge into an executable invariant.
"""
import re
import sys


def check_node_fields(deck_path):
    with open(deck_path) as f:
        lines = f.readlines()
    in_node = False
    checked = 0
    for line in lines:
        if line.startswith("/NODE"):
            in_node = True
            continue
        if in_node:
            if line.startswith("/"):
                break
            if not line.strip() or line.startswith("#"):
                continue
            # Node ID: 10 chars, then three 20-char coordinate fields = 70 chars
            if len(line.rstrip("\n")) != 70:
                print(f"FAIL: {deck_path}: node line wrong length "
                      f"({len(line.rstrip())} != 70): {line.rstrip()[:40]}...")
                return False
            nid = line[0:10]
            x = line[10:30]
            y = line[30:50]
            z = line[50:70]
            # Each field must parse as a number
            try:
                int(nid)
                float(x)
                float(y)
                float(z)
            except ValueError:
                print(f"FAIL: {deck_path}: unparsable node fields: {line.rstrip()}")
                return False
            checked += 1
    if checked == 0:
        print(f"FAIL: {deck_path}: no node lines found")
        return False
    print(f"OK: {deck_path}: {checked} node lines, 10+20+20+20 field widths correct")
    return True


if __name__ == "__main__":
    ok = all(check_node_fields(p) for p in sys.argv[1:])
    sys.exit(0 if ok else 1)
