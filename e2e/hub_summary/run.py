"""The hub summary on the real home page, in headless Chromium.  See README.md.

    python e2e/hub_summary/run.py

Serves the repository root like the orchestrator does (including the /scythe/i/<instance>/ prefix) and answers the orchestrator's
instance list and an instance's mission list with canned data. Opens rf_scythe_home.html and checks what the instrument panel
shows, that missions load only when asked, that an unreachable orchestrator is said, and that a mission's link opens the globe
with that mission. It checks logic, not appearance.

Exit status:  0 every case passed;  1 a case failed;  2 infrastructure failure (Playwright or Chromium missing, the page or its
CDN scripts did not load).
"""
import argparse
import http.server
import re
import sys
import threading
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "globe_picking"))
import run as globe_page   # the canned-API server and page launch settings shared with the other harnesses

NOW_ISO = "2026-10-23T12:00:25Z"
RUNNING = {"id": "scythe-ab12", "name": "ops-1", "port": 51001, "status": "running", "socket_io_ready": True, "last_health": NOW_ISO,
           "info": {"node_count": 9, "edge_count": 8, "session_count": 1, "behavior_groups": 0},
           "authority": {"ok": True, "authoritative_state": {"sessions": 2, "nodes": 2183, "edges": 8417, "bsgs": 4},
                         "health": {"graph_engine": True, "bsg_engine": True, "authority": "postgres", "llm": False},
                         "write_access": {"enabled": False, "reason": "No authenticated operator provenance"}}}
SPARSE = {"id": "scythe-new", "name": "just-started", "port": 51002, "status": "running"}
DEAD = {"id": "scythe-dead", "name": "old", "port": 51003, "status": "dead", "last_health": NOW_ISO}
MISSIONS = [{"mission_id": f"m-{c}", "name": f"Mission {c.upper()}", "status": s, "updated_at": t}
            for c, s, t in (("a", "open", 100), ("b", "closed", 300), ("c", "open", 200), ("d", "weird", 50), ("e", "open", 400),
                            ("f", "open", 10), ("g", "closed", 20))]
INSTANCE_PATH = re.compile(r"^/scythe/i/([A-Za-z0-9._-]+)(/.*)?$")


class Handler(globe_page.Handler):
    requests = []          # every path asked for, to prove what was and was not fetched
    fail_instances = False

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        Handler.requests.append(path)
        if path == "/api/scythe/instances":
            if Handler.fail_instances:
                return self.reply_json({"status": "error"}, 500)
            return self.reply_json({"instances": [RUNNING, SPARSE, DEAD], "count": 3, "orchestrator_uptime": 3700})
        if path == "/api/operator/session":
            return self.reply_json({"status": "ok", "operator": {"callsign": "HARNESS-1", "role": "operator"}})
        match = INSTANCE_PATH.match(path)
        if match:   # the orchestrator's reverse proxy: /scythe/i/<id>/<path> is that instance's <path>
            inner = match.group(2) or "/"
            if inner == "/api/missions":
                return self.reply_json({"status": "ok", "missions": MISSIONS, "count": len(MISSIONS)})
            self.path = inner + (("?" + self.path.split("?", 1)[1]) if "?" in self.path else "")
            return super().do_GET()
        if path == "/":
            self.path = "/rf_scythe_home.html"
        return super().do_GET()


class Check:
    def __init__(self, browser, base):
        self.browser, self.base, self.results = browser, base, []

    def record(self, name, ok, detail=""):
        self.results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    def home(self):
        page = self.browser.new_page(viewport={"width": 1400, "height": 1000})
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)[:160]))
        page.add_init_script(f"window.SCYTHE_API_BASE = '{self.base}'")
        # The home page redirects to login.html without a session: give it one the canned /api/operator/session accepts, so its
        # own auth gate runs and passes (the page declares itself the broker, which keeps its session in sessionStorage).
        page.add_init_script("sessionStorage.setItem('scythe:orchestrator:brokerSession', JSON.stringify({sessionToken: 'harness-session'}))")
        page.goto(self.base + "/rf_scythe_home.html", wait_until="domcontentloaded")
        return page

    def cards(self, page):
        return page.evaluate("""() => [...document.querySelectorAll('#hub-summary-list .hub__card')].map(c => ({
          id: c.dataset.instance, text: c.querySelector('.hub__counts').textContent,
          chips: Object.fromEntries([...c.querySelectorAll('.hub__chip')].map(x => [x.dataset.key, [x.dataset.state, x.querySelector('.hub__detail').textContent]])),
          links: [...c.querySelectorAll(':scope > .hub__links a')].map(a => a.getAttribute('href')),
          toggle: !!c.querySelector('.hub__toggle') }))""")

    def panel_cases(self):
        page = self.home()
        page.wait_for_selector("#hub-summary-list .hub__card", timeout=60000)
        cards = {c["id"]: c for c in self.cards(page)}
        self.record("one card per instance", list(cards) == ["scythe-ab12", "scythe-new", "scythe-dead"], str(list(cards)))
        run = cards["scythe-ab12"]
        self.record("counts come from the authority state", run["text"] == "2183 nodes · 8417 edges · 2 sessions · 4 BSGs", run["text"])
        self.record("the instance's own health is shown, not assumed",
                    [run["chips"][k][0] for k in ("instance", "graph", "authority", "llm", "writes")] == ["ok", "ok", "ok", "down", "locked"], str(run["chips"]))
        self.record("a locked write gate says why", run["chips"]["writes"][1] == "No authenticated operator provenance", str(run["chips"]["writes"]))
        sparse = cards["scythe-new"]
        self.record("an instance that reports nothing says so", "not reported" in sparse["text"] and sparse["chips"]["graph"][0] == "unknown" and sparse["chips"]["writes"][0] == "unknown", str(sparse))
        self.record("a dead instance is shown dead and offers no missions", cards["scythe-dead"]["chips"]["instance"][0] == "down" and not cards["scythe-dead"]["toggle"])
        self.record("its links go to that instance's views",
                    run["links"] == ["scythe/i/scythe-ab12/cesium-hypergraph-globe.html", "scythe/i/scythe-ab12/scythe-web/regional-rf-demo.html", "scythe/i/scythe-ab12/command-ops-visualization.html"], str(run["links"]))
        self.record("no mission list is fetched until a card is expanded", not any(p.endswith("/api/missions") for p in Handler.requests))
        page.close()

    def missions_cases(self):
        page = self.home()
        page.wait_for_selector("#hub-summary-list .hub__card", timeout=60000)
        page.click("[data-instance=scythe-ab12] .hub__toggle")
        page.wait_for_selector("[data-instance=scythe-ab12] .hub__mission", timeout=30000)
        text = page.evaluate("document.querySelector('[data-instance=scythe-ab12] .hub__missions').textContent")
        self.record("missions are counted by status", "7 missions: 4 open, 2 closed, 1 other" in text, text[:120])
        self.record("only the newest few are listed, and it says so", "Showing the 5 most recently updated." in text)
        ids = page.evaluate("[...document.querySelectorAll('[data-instance=scythe-ab12] .hub__mission')].map(m => m.dataset.mission)")
        self.record("the newest come first", ids == ["m-e", "m-b", "m-c", "m-a", "m-d"], str(ids))
        href = page.evaluate("document.querySelector('[data-mission=m-e] a').getAttribute('href')")
        self.record("a mission's link carries the mission", href == "scythe/i/scythe-ab12/cesium-hypergraph-globe.html?mission=m-e", href)
        self.record("the other instances' cards fetched no missions", sum(p.endswith("/api/missions") for p in Handler.requests) >= 1 and
                    not any("scythe-new" in p or "scythe-dead" in p for p in Handler.requests if p.endswith("/api/missions")))
        page.click("[data-mission=m-e] a")
        page.wait_for_function("typeof scytheMissionViewport !== 'undefined'", timeout=60000)
        landed = page.evaluate("location.pathname + location.search")
        self.record("following it opens that instance's globe on the mission",
                    landed == "/scythe/i/scythe-ab12/cesium-hypergraph-globe.html?mission=m-e" and page.evaluate("scytheMissionViewport.missionId") == "m-e", landed)
        page.close()

    def unreachable_cases(self):
        Handler.fail_instances = True
        try:
            page = self.home()
            page.wait_for_function("document.getElementById('hub-summary-list')?.textContent.includes('Orchestrator unreachable')", timeout=30000)
            self.record("an orchestrator that does not answer is said, not shown as empty",
                        "Orchestrator unreachable (500)" in page.evaluate("document.getElementById('hub-summary-list').textContent"))
            page.wait_for_function("document.getElementById('shell-orchestrator').textContent.startsWith('unreachable')", timeout=15000)
            self.record("  and so does the state bar", page.evaluate("document.getElementById('shell-orchestrator').textContent") == "unreachable (500)")
            page.close()
        finally:
            Handler.fail_instances = False

    NOT_ON_A_HUB = ["Melted Down", "NeRF Engine", "Threat Detection", "GEMMA LLM", "JWST", "LHC", "Remora", "X-37B", "Hypersonic", "hypersonic",
                    "mastodon", "Spectrcyde", "WordPress", "All rights reserved", "Advanced Packet Capture"]
    GONE = ["#scene-container", "#loading-screen", ".hero", ".modules-section", ".module-card", ".status-panel", "footer", "#system-time",
            "#btn-wordpress-site", "#scythe-wp-nav-link"]

    def shell_cases(self):
        page = self.home()
        page.wait_for_selector("#hub-summary-list .hub__card", timeout=60000)
        page.wait_for_function("document.getElementById('shell-operator').textContent !== 'checking…'", timeout=30000)
        present = page.evaluate("(sels) => sels.filter(s => document.querySelector(s))", self.GONE)
        self.record("the marketing and decorative elements are gone", present == [], str(present))
        text = page.evaluate("document.body.innerText + ' ' + document.documentElement.outerHTML")
        found = [t for t in self.NOT_ON_A_HUB if t in text]
        self.record("no invented status, demo promotion, social or sales text remains", found == [], str(found))
        hrefs = page.evaluate("[...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href')).filter(h => !h.startsWith('scythe/i/'))")
        self.record("the only links off the page are the dashboard anchor, the documentation and the account page",
                    sorted(set(hrefs)) == ["#dashboard", "docs/documentation.html", "user_account.html"], str(sorted(set(hrefs))))
        self.record("the state bar shows the operator from the session", page.evaluate("document.getElementById('shell-operator').textContent") == "HARNESS-1 · operator",
                    page.evaluate("document.getElementById('shell-operator').textContent"))
        page.wait_for_function("document.getElementById('shell-instances').textContent.includes('running')", timeout=30000)
        self.record("  and what the orchestrator reported",
                    page.evaluate("[document.getElementById('shell-instances').textContent, document.getElementById('shell-orchestrator').textContent]") == ["2 running of 3", "reachable"])
        self.record("  and a UTC clock", bool(page.evaluate("/^\\d\\d:\\d\\d:\\d\\dZ$/.test(document.getElementById('shell-clock').textContent)")))
        fleet = page.evaluate("document.getElementById('hub-fleet').textContent")
        want = "2 running of 3 · 2183 nodes (2 not reporting) · 8417 edges (2 not reporting) · 2 sessions (2 not reporting) · 1 dead · writes locked on 1 · orchestrator up 1h"
        self.record("the fleet line totals only what instances reported, and says how many did not", fleet == want, fleet)
        page.click("#btn-new-instance")
        page.wait_for_selector("#modal-new-instance.active", timeout=15000)
        self.record("New instance still opens its dialog", True)
        page.keyboard.press("Escape")
        page.click(".shell__nav >> text=Connect to an instance")
        page.wait_for_selector("#modal-join-instance.active .instance-list li", timeout=15000)
        self.record("Connect lists the instances to join", page.evaluate("document.querySelectorAll('#modal-join-instance .instance-list li').length") == 3)
        page.keyboard.press("Escape")
        page.click(".shell__nav >> text=Account")
        page.wait_for_selector("#account-modal.active", timeout=15000)
        self.record("Account still opens its dialog", True)
        self.record("the page raised no script errors", not page.errors, "; ".join(page.errors[:3]))
        page.close()

    def run(self):
        for cases in (self.shell_cases, self.panel_cases, self.missions_cases, self.unreachable_cases):
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
