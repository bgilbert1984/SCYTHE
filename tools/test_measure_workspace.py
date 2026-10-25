import os
import subprocess
import tempfile
import unittest

import measure_workspace as mw


class Counting(unittest.TestCase):
    def test_classify_separates_tests_plugins_stores_boot_and_logic(self):
        got = mw.classify(["scythe-web/aPlugin.js", "scythe-web/aPlugin.test.js", "scythe-web/reconStore.js", "scythe-web/workspaceBoot.js",
                           "scythe-web/lifecycle.js", "scythe-web/bootForTest.js", "scythe-web/pure.js", "scythe-web/README.md"])
        self.assertEqual(got["plugins"], ["scythe-web/aPlugin.js"])
        self.assertEqual(got["tests"], ["scythe-web/aPlugin.test.js"])
        self.assertEqual(got["stores"], ["scythe-web/reconStore.js"])
        self.assertEqual(got["boot"], ["scythe-web/workspaceBoot.js"])
        self.assertEqual(got["logic"], ["scythe-web/pure.js"])

    def test_page_objects_measures_length_and_drops_short_constants(self):
        body = "\n".join(f"  x{i}: {i}," for i in range(30))
        text = f"      const BigThing = {{\n{body}\n      }};\n      const SMALL = {{\n a: 1\n      }};\n"
        self.assertEqual(mw.page_objects(text), [{"name": "BigThing", "lines": 32}])
        longer = "\n".join(f"  y{i}: {i}," for i in range(50))
        both = f"      const Medium = {{\n{body}\n      }};\n      const Large = {{\n{longer}\n      }};\n"
        self.assertEqual([o["name"] for o in mw.page_objects(both)], ["Large", "Medium"], "largest first")

    def test_patterns_count_what_they_name(self):
        self.assertEqual(mw.count(mw.PAGE_COUNTS["dom"]["innerHTMLWrites"], "a.innerHTML = 1; b.innerHTML += 2; c.innerHTML == 3"), 2)
        self.assertEqual(mw.count(mw.PAGE_COUNTS["dom"]["textContentWrites"], "a.textContent = 1; b.textContent === 2; c.textContent == 3"), 1)
        self.assertEqual(mw.count(mw.PAGE_COUNTS["dom"]["styleWrites"], "a.style.x = 1; b.style.y === 2"), 1)
        self.assertEqual(mw.count(mw.PAGE_COUNTS["resources"]["fetch"], "fetch(a); prefetch(b); myfetch(c)"), 1)
        self.assertEqual(mw.count(mw.PAGE_COUNTS["globals"]["windowAssignments"], "window.a = 1; window.b == 2; window.c = 3"), 2)


class AtARef(unittest.TestCase):
    def test_measures_the_ref_and_not_the_working_tree(self):
        with tempfile.TemporaryDirectory() as repo:
            run = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)
            run("init", "-q"); run("config", "user.email", "t@example.invalid"); run("config", "user.name", "t")
            write = lambda name, text: (os.makedirs(os.path.dirname(os.path.join(repo, name)) or repo, exist_ok=True), open(os.path.join(repo, name), "w").write(text))
            write(mw.PAGE, "<script>fetch(1)\n</script>\n<script src=x></script>\n")
            run("add", "-A"); run("commit", "-q", "-m", "before the runtime")
            old = os.getcwd()
            os.chdir(repo)
            try:
                before = mw.measure("HEAD")
                write(f"{mw.RUNTIME_DIR}/aPlugin.js", "x\n")
                write(f"{mw.RUNTIME_DIR}/aPlugin.test.js", "test('a', () => {})\ntest('b', () => {})\n")
                write(mw.BOOT, "workspace.register(a)\nworkspace.provide('s', {})\nworkspace.provide('t', {})\nwin.X = 1\nif (win.Y == 2) {}\n")
                run("add", "-A"); run("commit", "-q", "-m", "with the runtime")
                write(mw.PAGE, "changed in the working tree only\n")
                after = mw.measure("HEAD")
                os.chdir(os.path.join(repo, mw.RUNTIME_DIR))   # a path in ls-tree is relative to where the command runs, so run it from a subdirectory too
                from_subdir = mw.measure("HEAD")
            finally:
                os.chdir(old)
        self.assertEqual((before["page"]["lines"], before["page"]["inlineScripts"], before["page"]["externalScripts"], before["page"]["resources"]["fetch"]), (3, 1, 1, 1))
        self.assertEqual((before["runtime"]["plugins"], before["runtime"]["services"]), (0, 0))
        self.assertEqual(from_subdir, after)
        self.assertEqual((after["page"]["lines"], after["runtime"]["plugins"], after["runtime"]["lifecycleTools"], after["runtime"]["services"],
                          after["runtime"]["windowShims"], after["runtime"]["testCases"]), (3, 1, 1, 2, 1, 2))


if __name__ == "__main__":
    unittest.main()
