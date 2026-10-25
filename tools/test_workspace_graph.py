import os
import subprocess
import tempfile
import unittest

import measure_workspace as mw
import workspace_graph as wg

PLUGIN_A = '''import {helper} from "./helper.js";
export const A_ID = "alpha";
export function createA() {
  const tool = {
    id: A_ID,
    title: "Alpha",
    requires: ["httpFetch", "alphaView"],
    activate(ctx) {
      const http = ctx.services.get("httpFetch");
      const note = ctx.services.get("notify");
    },
  };
}
'''
PLUGIN_B = '''export function createB() {
  const tool = {
    id: "beta",
    title: "Beta",
    requires: ["httpFetch"],
    activate(ctx) { ctx.services.get("httpFetch"); },
  };
}
'''
PLUGIN_TWO = '''export function createTwo() {
  const one = {
    id: "gamma",
    title: "Gamma",
    requires: ["httpFetch"],
    activate(ctx) { ctx.services.get("other"); },
  };
  const two = {
    id: "delta",
    title: "Delta",
    requires: ["httpFetch"],
    activate(ctx) {},
  };
}
'''
BOOT = '''import {createA} from "./alphaPlugin.js";
import {boot} from "./bootOnly.js";
workspace.provide("httpFetch", (url) => win.fetch(url));
workspace.provide("alphaView", {show: () => win.AlphaPanel.show(win.console)});
workspace.register(createA());
workspace.provide("unused", {x: win.viewer});
workspace.provide("notify", () => {});
win.AlphaPanel = alpha.controls;
'''


class Parsing(unittest.TestCase):
    def test_a_tool_id_written_as_a_constant_is_resolved_and_its_requires_read(self):
        self.assertEqual(wg.plugin_tools("alphaPlugin.js", PLUGIN_A), [{"id": "alpha", "requires": ["httpFetch", "alphaView"]}])
        self.assertEqual(wg.plugin_tools("betaPlugin.js", PLUGIN_B), [{"id": "beta", "requires": ["httpFetch"]}])

    def test_provides_attributes_each_window_global_to_its_own_service_only(self):
        self.assertEqual(wg.provides(BOOT), {"httpFetch": ["fetch"], "alphaView": ["AlphaPanel", "console"], "unused": ["viewer"], "notify": []})   # win.AlphaPanel = ... after notify is an assignment, not a read

    def test_window_globals_are_split_into_page_library_and_browser(self):
        got = wg.window_globals({"s": ["console", "Cesium", "OperatorSession", "Old"], "t": ["OperatorSession", "fetch"]}, assigned={"OperatorSession"})
        self.assertEqual(got, {"page": {"Old": ["s"]}, "runtime": {"OperatorSession": ["s", "t"]}, "library": {"Cesium": ["s"]}, "browser": {"console": ["s"], "fetch": ["t"]}})

    def test_mermaid_draws_requires_and_page_globals_but_not_browser_ones(self):
        g = {"plugins": [{"id": "alpha", "requires": ["svc"]}], "services": {"svc": {"reads": ["Page", "Tool", "console"]}}, "windowGlobals": {"page": {"Page": ["svc"]}, "runtime": {"Tool": ["svc"]}}}
        drawn = wg.mermaid(g)
        self.assertIn("p_alpha --> s_svc", drawn)
        self.assertIn('s_svc -.-> w_Page["win.Page"]', drawn)
        self.assertIn('s_svc -.-> w_Tool["win.Tool"]', drawn)
        self.assertNotIn("console", drawn)


class AtARef(unittest.TestCase):
    def test_finds_the_findings_at_a_ref_even_when_run_from_a_subdirectory(self):
        with tempfile.TemporaryDirectory() as repo:
            run = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)
            run("init", "-q"); run("config", "user.email", "t@example.invalid"); run("config", "user.name", "t")
            def write(name, text):
                os.makedirs(os.path.dirname(os.path.join(repo, name)), exist_ok=True)
                open(os.path.join(repo, name), "w").write(text)
            write("scythe-web/alphaPlugin.js", PLUGIN_A)
            write("scythe-web/betaPlugin.js", PLUGIN_B)
            write("scythe-web/helper.js", "export const helper = 1;\n")
            write("scythe-web/orphan.js", "// orphan.js is not used\nexport const orphan = 1;\n")
            write("scythe-web/gammaPlugin.js", PLUGIN_TWO)
            write("scythe-web/bootOnly.js", "export const boot = 1;\n")
            write("scythe-web/demoOnly.test.js", 'import "./demoOnly.js"\n')
            write("scythe-web/demoOnly.js", "export const demo = 1;\n")
            write("scythe-web/demo.html", '<script type="module">import "./demoOnly.js"</script>\n')
            write("scythe-web/workspaceBoot.js", BOOT)
            run("add", "-A"); run("commit", "-q", "-m", "x")
            old = os.getcwd()
            os.chdir(os.path.join(repo, "scythe-web"))
            try:
                g = wg.graph("HEAD")
            finally:
                os.chdir(old)
        self.assertEqual([p["id"] for p in g["plugins"]], ["alpha", "beta", "delta", "gamma"])
        alpha = g["plugins"][0]
        self.assertEqual((alpha["usedNotDeclared"], alpha["declaredNotUsed"], alpha["imports"]), (["notify"], ["alphaView"], ["helper.js"]))
        self.assertEqual(g["services"]["httpFetch"]["requiredBy"], ["alpha", "beta", "delta", "gamma"])
        self.assertEqual(g["services"]["notify"]["requiredBy"], [], "alpha uses it without requiring it")
        gamma = next(p for p in g["plugins"] if p["id"] == "gamma")
        self.assertEqual((gamma["usedNotDeclared"], gamma["declaredNotUsed"]), ([], []), "a file with two tools is not judged: its uses cannot be told apart")
        f = g["findings"]
        self.assertEqual(f["requiredButNotProvided"], [])
        self.assertEqual(f["providedButNotRequired"], ["notify", "unused"])
        self.assertEqual(f["pluginsWithUndeclaredServices"], {"alpha": ["notify"]})
        self.assertEqual(f["pluginsWithUnusedRequires"], {"alpha": ["alphaView"]})
        self.assertEqual(f["modulesNotImportedByTheRuntime"], {"demoOnly.js": ["scythe-web/demo.html"], "orphan.js": []})
        self.assertEqual(f["modulesNothingReferences"], ["orphan.js"])
        self.assertEqual(g["windowGlobals"]["page"], {"viewer": ["unused"]})
        self.assertEqual(g["windowGlobals"]["runtime"], {"AlphaPanel": ["alphaView"]}, "set by the boot module, so the runtime reached through the window")


if __name__ == "__main__":
    unittest.main()
