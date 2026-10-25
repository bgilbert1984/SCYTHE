#!/usr/bin/env python3
"""Draw how the workspace runtime (scythe-web/) is wired: which plugin needs which service, who provides it, and what each plugin and service reaches in the page.

Reads the files of one git ref, like measure_workspace.py:

    python3 tools/workspace_graph.py                  # JSON for HEAD
    python3 tools/workspace_graph.py --ref a75e3d94   # JSON for a commit
    python3 tools/workspace_graph.py --mermaid        # a Mermaid diagram of the plugins and services

The edges come from text patterns, so they are a map to check against the code, not a proof:
  - plugin --requires--> service: the tool's `requires: [...]` list;
  - plugin --uses--> service: a `services.get("x")` call, and `declared` says whether `requires` lists it (a service used but not declared is a finding);
  - service <--provided by-- workspaceBoot.js: a `workspace.provide("x", ...)` call;
  - service --reads--> window global: a `win.X` in the text of that `provide` call (approximate: the text up to the next `provide`/`register`);
  - module --imports--> module: `import ... from "./x.js"`.
See docs/architecture/workspace-graph.md.
"""
import argparse
import json
import re
import subprocess
import sys

from measure_workspace import BOOT, RUNTIME_DIR, classify, git, read

IMPORT = re.compile(r'^import\s+(?:[^"\']*?\s+from\s+)?["\']\./([\w.\-]+\.js)["\']', re.M)
CONSTANT = re.compile(r'^export const (\w+)\s*=\s*"([^"]+)"', re.M)
TOOL = re.compile(r'^\s+id:\s*(\w+|"[^"]+")\s*,\s*\n\s+title:', re.M)
REQUIRES = re.compile(r"requires:\s*\[([^\]]*)\]")
SERVICE_GET = re.compile(r'services\.get\(\s*"([\w\-]+)"\s*\)')
BOUNDARY = re.compile(r"workspace\.(?:provide|register)\(")
PROVIDE = re.compile(r'^\s*workspace\.provide\(\s*"([\w\-]+)"', re.M)
WINDOW_REF = re.compile(r"\bwin\.(\w+)\b(?!\s*=[^=])")   # a read, not an assignment
WINDOW_ASSIGN = re.compile(r"\bwin\.(\w+)\s*=[^=]")


BROWSER = {"console", "fetch", "document", "performance", "requestAnimationFrame", "cancelAnimationFrame", "location", "CustomEvent", "Event", "EventSource",
           "localStorage", "sessionStorage", "setInterval", "clearInterval", "setTimeout", "clearTimeout", "navigator", "URL", "dispatchEvent", "addEventListener"}
LIBRARY = {"Cesium", "io"}   # third-party scripts the page loads


def strings(text):
    return re.findall(r'"([^"]+)"', text)


def plugin_tools(name, text):
    """The tools a plugin file defines: [{id, requires}], with an id written as a constant resolved from the same file."""
    constants = dict(CONSTANT.findall(text))
    tools = []
    for match in TOOL.finditer(text):
        raw = match.group(1)
        tool_id = raw.strip('"') if raw.startswith('"') else constants.get(raw)
        required = REQUIRES.search(text, match.end())
        tools.append({"id": tool_id or raw, "requires": strings(required.group(1)) if required else []})
    return tools


def provides(boot):
    """Each service the boot module provides, with the window globals named in the text of its call, up to the next provide or register."""
    marks = [m.start() for m in BOUNDARY.finditer(boot)] + [len(boot)]
    out = {}
    for start, end in zip(marks, marks[1:]):
        segment = boot[start:end]
        match = PROVIDE.match(segment) or re.match(r'workspace\.provide\(\s*"([\w\-]+)"', segment)
        if match:
            out[match.group(1)] = sorted(set(WINDOW_REF.findall(segment)))
    return out


def graph(ref="HEAD"):
    names = [n for n in git("ls-tree", "-r", "--full-tree", "--name-only", ref, RUNTIME_DIR).split("\n") if n]
    files = classify(names)
    boot = read(ref, BOOT, missing_ok=True)
    texts = {n: read(ref, n) for n in files["plugins"] + files["stores"] + files["logic"]}
    base = lambda n: n.rsplit("/", 1)[-1]
    provided = provides(boot)
    plugins = []
    for name in files["plugins"]:
        text = texts[name]
        used = sorted(set(SERVICE_GET.findall(text)))
        for tool in plugin_tools(name, text):
            plugins.append({
                "id": tool["id"], "file": base(name), "requires": tool["requires"], "uses": used,
                "usedNotDeclared": sorted(set(used) - set(tool["requires"])) if len(plugin_tools(name, text)) == 1 else [],
                "declaredNotUsed": sorted(set(tool["requires"]) - set(used)) if len(plugin_tools(name, text)) == 1 else [],
                "imports": sorted(set(IMPORT.findall(text))),
            })
    modules = {base(n): sorted(set(IMPORT.findall(texts[n]))) for n in files["stores"] + files["logic"]}
    required = {s for p in plugins for s in p["requires"]}
    imported = {i for p in plugins for i in p["imports"]} | {i for m in modules.values() for i in m} | set(IMPORT.findall(boot))
    outside = outside_references(ref, sorted(set(modules) - imported))
    return {
        "commit": git("rev-parse", "--short=8", ref).strip(),
        "plugins": sorted(plugins, key=lambda p: p["id"]),
        "services": {name: {"reads": reads, "requiredBy": sorted(p["id"] for p in plugins if name in p["requires"])} for name, reads in sorted(provided.items())},
        "windowGlobals": window_globals(provided, set(WINDOW_ASSIGN.findall(boot))),
        "bootImports": sorted(set(IMPORT.findall(boot))),
        "modules": modules,
        "findings": {
            "requiredButNotProvided": sorted(required - set(provided)),
            "providedButNotRequired": sorted(set(provided) - required),
            "pluginsWithUndeclaredServices": {p["id"]: p["usedNotDeclared"] for p in plugins if p["usedNotDeclared"]},
            "pluginsWithUnusedRequires": {p["id"]: p["declaredNotUsed"] for p in plugins if p["declaredNotUsed"]},
            "modulesNotImportedByTheRuntime": outside,
            "modulesNothingReferences": sorted(name for name, by in outside.items() if not by),
        },
    }


def window_globals(provided, assigned=()):
    """The window globals the services read, split into the page's own objects (the legacy surface), the ones the boot module itself sets (a tool's controls or a store: the
    runtime reached through the window), third-party libraries, and the browser's own, each with its readers."""
    readers = {}
    for service, reads in provided.items():
        for name in reads:
            readers.setdefault(name, []).append(service)
    kind = lambda name: "browser" if name in BROWSER else "library" if name in LIBRARY else "runtime" if name in assigned else "page"
    return {k: {n: sorted(r) for n, r in sorted(readers.items()) if kind(n) == k} for k in ("page", "runtime", "library", "browser")}


def outside_references(ref, names):
    """For each module the runtime's own files do not import: the other files at the ref (not tests, not docs) that mention its file name, i.e. the page or a standalone page."""
    out = {}
    for name in names:
        found = subprocess.run(["git", "grep", "-l", "--full-name", "-F", name, ref, "--", ":/"], capture_output=True, text=True).stdout.split("\n")   # ":/" is the whole repo, wherever this runs
        paths = {line.split(":", 1)[1] for line in found if ":" in line}
        out[name] = sorted(p for p in paths - {f"{RUNTIME_DIR}/{name}"} if not p.endswith(".test.js") and not p.startswith(("docs/", "tools/")))
    return out


def mermaid(g):
    """plugins -> services they require -> the window globals those services read that are the page's own or set by the boot module (the browser's and libraries' are left out)."""
    node = lambda prefix, name: prefix + re.sub(r"\W", "_", name)
    lines = ["flowchart LR"]
    for p in g["plugins"]:
        lines.append(f'  {node("p_", p["id"])}["{p["id"]}"]')
    for name, info in g["services"].items():
        lines.append(f'  {node("s_", name)}(["{name}"])')
    for p in g["plugins"]:
        lines += [f'  {node("p_", p["id"])} --> {node("s_", s)}' for s in p["requires"] if s in g["services"]]
    for name, info in g["services"].items():
        lines += [f'  {node("s_", name)} -.-> {node("w_", w)}["win.{w}"]' for w in info["reads"] if w in g["windowGlobals"]["page"] or w in g["windowGlobals"]["runtime"]]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--mermaid", action="store_true")
    args = parser.parse_args(argv)
    g = graph(args.ref)
    print(mermaid(g) if args.mermaid else json.dumps(g, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
