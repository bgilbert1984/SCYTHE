"""The login page follows `next` only to a path on its own origin.  See README.md.

    python e2e/login_next/run.py

Serves the repository root with a canned /api/operator/session, gives the browser a valid session, and opens
login.html?next=<value>. With a valid session the page redirects at once, so where the browser ends up is where `next` sent it.
A `next` on another host or a javascript: URL must land on the home page instead, and must not reach the other host or run script.

Exit status:  0 every case passed;  1 a case failed;  2 infrastructure failure (Playwright or Chromium missing, the page did not load).
"""
import argparse
import http.server
import sys
import threading
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "globe_picking"))
import run as globe_page   # the canned-API server shared with the other harnesses

CASES = [  # (name, next, expected landing path+query, must stay on this origin)
    ("a path on this origin is followed", "/cesium-hypergraph-globe.html?mission=m-1", "/cesium-hypergraph-globe.html?mission=m-1"),
    ("a full URL on this origin is followed as its path", "{origin}/rf_scythe_home.html#x", "/rf_scythe_home.html"),
    ("another host is not followed", "https://evil.example/steal", "/rf_scythe_home.html"),
    ("a protocol-relative URL is not followed", "//evil.example/steal", "/rf_scythe_home.html"),
    ("a backslash host is not followed", "/\\evil.example/steal", "/rf_scythe_home.html"),
    ("a javascript: URL is not followed", "javascript:window.__pwned=1", "/rf_scythe_home.html"),
]


class Handler(globe_page.Handler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/operator/session":
            return self.reply_json({"status": "ok", "operator": {"callsign": "HARNESS-1", "role": "operator"}})
        return super().do_GET()


class Check:
    def __init__(self, browser, base):
        self.browser, self.base, self.results = browser, base, []

    def record(self, name, ok, detail=""):
        self.results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    def case(self, name, value, want):
        context = self.browser.new_context(viewport={"width": 1200, "height": 800})
        reached = []
        context.route("**://evil.example/**", lambda route: (reached.append(route.request.url), route.fulfill(status=200, body="evil")))
        page = context.new_page()
        page.add_init_script(f"window.SCYTHE_API_BASE = '{self.base}'")
        page.add_init_script("sessionStorage.setItem('scythe:orchestrator:brokerSession', JSON.stringify({sessionToken: 'harness-session'}))")
        value = value.replace("{origin}", self.base)
        page.goto(f"{self.base}/login.html?next={urllib.parse.quote(value, safe='')}", wait_until="domcontentloaded")
        try:
            page.wait_for_function("location.pathname !== '/login.html'", timeout=30000)
        except Exception:
            self.record(name, False, "the login page never redirected")
            context.close()
            return
        page.wait_for_load_state("domcontentloaded")
        landed = page.evaluate("location.origin === arguments[0] ? location.pathname + location.search : location.href".replace("arguments[0]", repr(self.base)))
        pwned = page.evaluate("!!window.__pwned")
        self.record(name, landed == want and not reached and not pwned, f"landed {landed}; reached {reached}; script ran {pwned}")
        context.close()

    def run(self):
        for name, value, want in CASES:
            self.case(name, value, want)
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
