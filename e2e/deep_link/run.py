"""Deep-link contract on the real pages, in headless Chromium.  See README.md.

    python e2e/deep_link/run.py

Opens the globe, the regional page and Mission Ops with ?mission= and ?select= and checks what each does with them: the
globe selects the object, every page's links to the others carry the context, an invalid value is reported and ignored,
and following a link lands on the next page with the context intact. It checks logic, not appearance.

Exit status:  0 every case passed;  1 a case failed;  2 infrastructure failure (Playwright or Chromium missing, a page
or its CDN scripts did not load).
"""
import argparse
import http.server
import sys
import threading
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "globe_picking"))
import run as globe_page   # the canned-API server and page launch settings shared with the picking harness

GLOBE, REGIONAL, OPS = "cesium-hypergraph-globe.html", "scythe-web/regional-rf-demo.html", "command-ops-visualization.html"
GLOBE_READY = "typeof globe !== 'undefined' && globe._viewer && globe._nodeCount >= 12 && typeof scytheMissionViewport !== 'undefined'"


class Handler(globe_page.Handler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/missions/m1":
            return self.reply_json({"mission": {"status": "open", "name": "M1"}})
        return super().do_GET()


class Check:
    def __init__(self, browser, base):
        self.browser, self.base, self.results = browser, base, []

    def record(self, name, ok, detail=""):
        self.results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    def open(self, path, query, ready, session=False):
        page = self.browser.new_page(viewport={"width": 1400, "height": 850})
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)[:160]))
        page.add_init_script(f"window.SCYTHE_API_BASE = '{self.base}'")
        if session:
            # Mission Ops redirects to login.html without a session. Give it one the canned /api/operator/session accepts, so
            # the page's own auth guard runs and passes (a page with no bootstrap metadata is a "broker" and keeps its session here).
            page.add_init_script("sessionStorage.setItem('scythe:orchestrator:brokerSession', JSON.stringify({sessionToken: 'harness-session'}))")
        page.goto(f"{self.base}/{path}{query}", wait_until="domcontentloaded")
        page.wait_for_function(ready, timeout=60000)
        return page

    @staticmethod
    def links(page, selector):
        return page.evaluate("(s) => [...document.querySelectorAll(s)].map(a => [a.textContent.trim(), a.getAttribute('href')])", selector)

    def globe_cases(self):
        page = self.open(GLOBE, "?mission=m1&select=n3", GLOBE_READY)
        page.wait_for_function("window.scytheDeepLinkSelect !== undefined", timeout=45000)
        self.record("globe: ?select selects the object once it is on the globe",
                    page.evaluate("globe._selectedEntityId") == "n3" and page.evaluate("window.scytheDeepLinkSelect") == "selected")
        self.record("globe: the mission comes from the link", page.evaluate("scytheMissionViewport.missionId") == "m1")
        want = [["Regional", "scythe-web/regional-rf-demo.html?mission=m1&select=n3"], ["Mission Ops", "command-ops-visualization.html?mission=m1&select=n3"]]
        # The link carries the link's own selection until the user selects something else.
        self.record("globe: its links to the other views carry the mission and selection", self.links(page, ".deeplink-nav a") == want, str(self.links(page, ".deeplink-nav a")))
        page.evaluate("globe.selectNode('n5')")
        page.wait_for_timeout(300)
        self.record("globe: the links follow a new selection",
                    self.links(page, ".deeplink-nav a")[0][1] == "scythe-web/regional-rf-demo.html?mission=m1&select=n5", str(self.links(page, ".deeplink-nav a")))
        page.evaluate("globe.selectNode(null)")
        page.wait_for_timeout(300)
        self.record("globe: clearing the selection drops it from the links",
                    self.links(page, ".deeplink-nav a")[1][1] == "command-ops-visualization.html?mission=m1", str(self.links(page, ".deeplink-nav a")))
        self.record("globe: no script errors", not page.errors, "; ".join(page.errors[:3]))
        page.close()

    def invalid_cases(self):
        page = self.open(GLOBE, "?mission=bad%20id&select=n3", GLOBE_READY)
        note = page.evaluate("document.querySelector('.deeplink-nav__note')?.textContent")
        self.record("globe: an invalid mission is said to be ignored", note == "ignored in link: mission", str(note))
        self.record("globe: and is not used", page.evaluate("scytheMissionViewport.missionId") is None)
        self.record("globe: and is not forwarded", "mission=" not in " ".join(h for _, h in self.links(page, ".deeplink-nav a")), str(self.links(page, ".deeplink-nav a")))
        page.close()

    def regional_cases(self):
        page = self.open(REGIONAL, "?mission=m1&select=tower-7", "document.querySelector('.deeplink-nav a') !== null")
        want = [["Globe", "../cesium-hypergraph-globe.html?mission=m1&select=tower-7"], ["Mission Ops", "../command-ops-visualization.html?mission=m1&select=tower-7"]]
        self.record("regional: its links carry the mission and selection, relative to its own directory", self.links(page, ".deeplink-nav a") == want, str(self.links(page, ".deeplink-nav a")))
        self.record("regional: the mission reaches its governance panel", "M1" in page.evaluate("document.getElementById('mission-governance').textContent") or
                    "m1" in page.evaluate("document.getElementById('mission-governance').textContent"))
        page.close()

    def ops_cases(self):
        page = self.open(OPS, "?mission=m1&select=n3", "document.getElementById('deeplink-globe')?.getAttribute('href')?.includes('mission=')", session=True)
        got = self.links(page, "#deeplink-globe, #deeplink-regional")
        want = [["GLOBE INTEL", "cesium-hypergraph-globe.html?mission=m1&select=n3"], ["REGIONAL RF", "scythe-web/regional-rf-demo.html?mission=m1&select=n3"]]
        self.record("ops: its links to the other views carry the context it was opened with", got == want, str(got))
        page.close()

    def follow_cases(self):
        page = self.open(GLOBE, "?mission=m1&select=n3", GLOBE_READY)
        page.wait_for_function("window.scytheDeepLinkSelect !== undefined", timeout=45000)
        page.click(".deeplink-nav a[data-target=regional]")
        page.wait_for_function("document.querySelector('.deeplink-nav a[data-target=globe]') !== null", timeout=60000)
        landed = page.evaluate("location.pathname + location.search")
        self.record("following the globe's Regional link lands there with the context", landed == "/scythe-web/regional-rf-demo.html?mission=m1&select=n3", landed)
        back = page.evaluate("document.querySelector('.deeplink-nav a[data-target=globe]').getAttribute('href')")
        self.record("  and its link back to the globe carries it on", back == "../cesium-hypergraph-globe.html?mission=m1&select=n3", back)
        page.close()

    def run(self):
        for cases in (self.globe_cases, self.invalid_cases, self.regional_cases, self.ops_cases, self.follow_cases):
            cases()
        return all(self.results)


def main():
    argparse.ArgumentParser(description=__doc__.split("\n")[0]).parse_args()
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError as error:
        print(f"INFRASTRUCTURE: Playwright is not installed ({error})")
        return 2
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
            ok = Check(browser, base).run()
            browser.close()
    except PlaywrightError as error:
        print(f"INFRASTRUCTURE: {str(error)[:300]}")
        return 2
    finally:
        server.shutdown()
    print("\nall passed" if ok else "\nFAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
