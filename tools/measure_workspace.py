#!/usr/bin/env python3
"""Measure how far command-ops-visualization.html has moved into the workspace runtime (scythe-web/).

Reads the files of one git ref (default HEAD), not the working tree, so the same command gives the same numbers for the same commit:

    python3 tools/measure_workspace.py                  # JSON for HEAD
    python3 tools/measure_workspace.py --ref a75e3d94   # JSON for any commit
    python3 tools/measure_workspace.py --markdown       # the same as a table

The counts are regular expressions over source text, so they are for comparing one commit with another, not exact facts about the code (an `innerHTML` in a comment
counts). See docs/architecture/workspace-baseline.md for what each number means and what it does not.
"""
import argparse
import json
import re
import subprocess
import sys

PAGE = "command-ops-visualization.html"
RUNTIME_DIR = "scythe-web"
BOOT = f"{RUNTIME_DIR}/workspaceBoot.js"
INFRASTRUCTURE = {"lifecycle.js", "bootForTest.js"}   # the runtime's own files: neither a plugin nor a logic module

# What the page still does itself. Resource ownership first (those are the numbers to watch), then the DOM ones (trend only).
PAGE_COUNTS = {
    "resources": {
        "fetch": r"\bfetch\(",
        "eventSource": r"new EventSource",
        "setInterval": r"\bsetInterval\(",
        "addEventListener": r"addEventListener\(",
        "cesiumReferences": r"\bCesium\.",
        "cesiumConstructions": r"new Cesium\.",
        "viewerReferences": r"\bviewer\.",
    },
    "dom": {
        "innerHTMLWrites": r"\.innerHTML\s*\+?=(?!=)",
        "textContentWrites": r"\.textContent\s*=(?!=)",
        "styleWrites": r"\.style\.\w+\s*=(?!=)",
        "getElementById": r"getElementById",
        "querySelector": r"querySelector",
        "inlineHandlers": r'\son(?:click|change|input|submit)="',
    },
    "globals": {
        "windowAssignments": r"\bwindow\.\w+\s*=[^=]",
    },
}
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)
EXTERNAL_SCRIPT = re.compile(r"<script[^>]*\bsrc=")
OBJECT_LITERAL = re.compile(r"^(\s{0,10})(?:const|let|var)\s+([A-Z]\w+)\s*=\s*\{", re.M)
CLASS = re.compile(r"^\s{0,10}class\s+(\w+)", re.M)


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def read(ref, path, missing_ok=False):
    try:
        return git("show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        if missing_ok:
            return ""   # a commit from before the runtime existed has no workspaceBoot.js
        raise


def count(pattern, text):
    return len(re.findall(pattern, text))


def lines(text):
    return text.count("\n")


def classify(names):
    """Splits the runtime directory's files into tests, plugins, stores, boot modules and logic modules."""
    src = [n for n in names if n.endswith(".js") and not n.endswith(".test.js")]
    base = lambda n: n.rsplit("/", 1)[-1]
    plugins = [n for n in src if base(n).endswith("Plugin.js")]
    boots = [n for n in src if n == BOOT]
    stores = [n for n in src if base(n).endswith("Store.js")]
    taken = set(plugins) | set(boots) | set(stores)
    logic = [n for n in src if n not in taken and base(n) not in INFRASTRUCTURE]
    return {"tests": [n for n in names if n.endswith(".test.js")], "plugins": plugins, "stores": stores, "boot": boots, "logic": logic}


def page_objects(text):
    """The capitalised object literals the page still declares, with their length in lines, largest first."""
    rows, text_lines = [], text.split("\n")
    for match in OBJECT_LITERAL.finditer(text):
        indent, start = match.group(1), text.count("\n", 0, match.start())
        for i in range(start + 1, len(text_lines)):
            if text_lines[i].startswith(indent + "};") or text_lines[i] == indent + "}":
                rows.append({"name": match.group(2), "lines": i - start + 1})
                break
    return sorted((r for r in rows if r["lines"] >= 20), key=lambda r: -r["lines"])   # shorter ones are constants, not page objects


def measure(ref="HEAD"):
    page = read(ref, PAGE)
    names = [n for n in git("ls-tree", "-r", "--full-tree", "--name-only", ref, RUNTIME_DIR).split("\n") if n]
    files = classify(names)
    boot = read(ref, BOOT, missing_ok=True)
    contents = {n: read(ref, n) for n in files["plugins"] + files["stores"] + files["logic"] + files["boot"] + files["tests"]}
    size = lambda group: sum(lines(contents[n]) for n in files[group])
    scripts = INLINE_SCRIPT.findall(page)
    return {
        "commit": git("rev-parse", "--short=8", ref).strip(),
        "page": {
            "lines": lines(page),
            "inlineScripts": len(scripts),
            "inlineScriptLines": sum(lines(s) for s in scripts),
            "externalScripts": len(EXTERNAL_SCRIPT.findall(page)),
            "objects": page_objects(page),
            "classes": sorted(set(CLASS.findall(page))),
            **{group: {name: count(pattern, page) for name, pattern in group_counts.items()} for group, group_counts in PAGE_COUNTS.items()},
        },
        "runtime": {
            "plugins": len(files["plugins"]),
            "pluginLines": size("plugins"),
            "lifecycleTools": count(r"workspace\.register\(", boot),
            "services": count(r"workspace\.provide\(", boot),
            "stores": len(files["stores"]),
            "logicModules": len(files["logic"]),
            "logicLines": size("logic"),
            "bootLines": lines(boot),
            "windowShims": count(r"\bwin\.\w+\s*=[^=]", boot),
            "windowReadsInPlugins": sum(count(r"\b(?:win|window)\.\w+", contents[n]) for n in files["plugins"]),
            "testFiles": len(files["tests"]),
            "testLines": size("tests"),
            "testCases": sum(len(re.findall(r"^\s*test\(", contents[n], re.M)) for n in files["tests"]),
        },
    }


def markdown(m):
    rows = [("Commit", m["commit"]), ("Page lines", m["page"]["lines"]), ("Inline scripts", f'{m["page"]["inlineScripts"]} ({m["page"]["inlineScriptLines"]} lines)')]
    rows += [(k, v) for k, v in m["runtime"].items()]
    for group in PAGE_COUNTS:
        rows += [(f"page.{group}.{k}", v) for k, v in m["page"][group].items()]
    rows += [(f'page object {o["name"]}', f'{o["lines"]} lines') for o in m["page"]["objects"]]
    rows += [("page classes", ", ".join(m["page"]["classes"]))]
    return "| Measure | Value |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ref", default="HEAD", help="the git ref to measure (default HEAD)")
    parser.add_argument("--markdown", action="store_true", help="print a table instead of JSON")
    args = parser.parse_args(argv)
    result = measure(args.ref)
    print(markdown(result) if args.markdown else json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
