"""Two-operator walkthrough of the SDR approval flow in a real browser.  See README.md.

    python e2e/sdr_approvals/run.py [--out DIR] [--port 5099] [--server-python PATH] [--keep-going]

Starts server.py, drives two operators (ALPHA, BRAVO) plus a second ALPHA session in headless Chromium, checks
what the browser shows and what the server recorded (bridge calls, control audit), and stops the server.

Exit status:  0 every case passed;  1 a case failed (functional regression, or an intentional policy change);
2 infrastructure failure (the harness could not run: dependencies, server start, page load).
"""
import argparse
import contextlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

OPERATORS = {"tok-alpha": {"operator_id": "op-1", "callsign": "ALPHA"},
             "tok-alpha2": {"operator_id": "op-1", "callsign": "ALPHA"},
             "tok-bravo": {"operator_id": "op-2", "callsign": "BRAVO"}}


class InfrastructureError(Exception):
    pass


class Walkthrough:
    def __init__(self, base, out):
        self.base, self.out = base, Path(out)
        self.results = []
        self.out.mkdir(parents=True, exist_ok=True)

    # --- server state -------------------------------------------------------------------------------------------------
    def get(self, path):
        return json.load(urllib.request.urlopen(self.base + path, timeout=10))

    def set_lifecycle(self, state):
        urllib.request.urlopen(f"{self.base}/__lifecycle/{state}", timeout=10).read()

    def calls(self): return self.get("/__bridge_calls")
    def proposals(self, tool): return [p for p in self.get("/__audit") if p.get("tool") == tool]
    def last(self, tool): return self.proposals(tool)[-1]
    @staticmethod
    def events(p): return [f"{action}:{result}" for action, result in p["events"]]

    # --- browser ------------------------------------------------------------------------------------------------------
    def open_page(self, browser, token):
        from playwright.sync_api import Error as PlaywrightError
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        # The login page is not exercised: the operator's session token is seeded and the session check is answered here.
        context.add_init_script(f"sessionStorage.setItem('scythe:orchestrator:brokerSession', JSON.stringify({{sessionToken: '{token}'}}));")
        page = context.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)[:160]))

        def route(r):
            if r.request.url.endswith("/api/operator/session"):
                return r.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"status": "ok", "operator": OPERATORS[token]}))
            r.continue_()
        page.route("**/*", route)
        dismiss_cesium = "() => document.querySelectorAll('.cesium-widget-errorPanel-buttonPanel button').forEach(b => b.click())"
        try:
            page.goto(self.base + "/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_function("window.SDRPPBridge && window.SDRPPOutcome && window.ScytheTransport", timeout=90000)
            page.evaluate("""t => { ScytheTransport.setSessionToken(t); window.__notes = []; const o = window.showNotification;
                window.showNotification = (a, m, ty) => { window.__notes.push(a + ': ' + m); if (o) return o(a, m, ty); }; }""", token)
            page.wait_for_timeout(5000)      # the page finishes its own start-up (it re-renders the sidebar)
            page.evaluate("() => showPanel('signals')")
            page.evaluate(dismiss_cesium)    # headless Chromium makes Cesium raise a render-error dialog
            page.wait_for_selector("#scanSpectrumBtn", state="visible", timeout=30000)
        except PlaywrightError as exc:
            raise InfrastructureError(f"the page did not become ready for {token}: {str(exc).splitlines()[0]}")
        self.refresh(page)
        return page

    def refresh(self, page):
        page.evaluate("() => window.SDRPPBridge.refreshApprovals()")
        page.wait_for_timeout(500)

    def notes(self, page): return page.evaluate("() => window.__notes")
    def panel(self, page): return page.inner_text("#sdrpp-approvals")
    def badge(self, page): return page.inner_text("#sdrpp-approvals-btn").strip()
    def shot(self, page, name): page.screenshot(path=str(self.out / f"{name}.png"))
    def show_spectrogram(self, page): page.evaluate("() => { document.getElementById('spectrogramContainer').style.display = 'block'; }")

    def decide(self, page, text, decision):
        """Approve or deny with a real pointer click (a scripted DOM click would hide an unreachable button)."""
        if page.eval_on_selector("#sdrpp-approvals", "e => e.style.display") == "none":
            page.click("#sdrpp-approvals-btn")
        self.refresh(page)
        page.locator(".sdrpp-approval-row", has_text=text).first.locator("button", has_text=decision).click(timeout=8000)
        page.wait_for_timeout(1200)

    # --- reporting ----------------------------------------------------------------------------------------------------
    @contextlib.contextmanager
    def case(self, number, name):
        c = {"ok": False, "detail": ""}
        try:
            yield c
        except InfrastructureError:
            raise
        except Exception as exc:     # a case that cannot complete is a failed case (e.g. a button no click can reach)
            c["ok"], c["detail"] = False, f"did not complete: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        self.results.append((number, name, c["ok"]))
        print(f"[{'PASS' if c['ok'] else 'FAIL'}] {number:>2}. {name}\n         {c['detail']}", flush=True)

    # --- the walkthrough ----------------------------------------------------------------------------------------------
    def run(self, browser):
        a = self.open_page(browser, "tok-alpha")
        b = self.open_page(browser, "tok-bravo")
        a2 = None

        with self.case(1, "ALPHA: Scan Spectrum is held for approval; stream not opened, bridge not started; audit shows only the pending proposal") as c:
            a.click("#scanSpectrumBtn")
            a.wait_for_timeout(1500)
            state = a.evaluate("() => ({streaming: window.SDRPPBridge.state.streaming, status: document.getElementById('sdrpp-live-status').textContent})")
            p = self.last("rf_capture_start")
            self.shot(a, "01-scan-pending")
            c["ok"] = (not state["streaming"] and "AWAITING" in state["status"] and "start" not in self.calls()
                       and self.events(p) == ["proposal.created:pending_approval"] and p["proposed_by"] == "op-1")
            c["detail"] = f"{state} calls={self.calls()} audit={self.events(p)}"

        with self.case(2, "BRAVO: the Approvals panel is fully visible and its Approve/Deny buttons are reachable by a real click") as c:
            self.show_spectrogram(b)     # BRAVO only needs the panel; a Scan click would make BRAVO a proposer
            b.click("#sdrpp-approvals-btn")
            self.refresh(b)
            b.wait_for_timeout(300)
            self.shot(b, "02-approver-panel")
            box = b.eval_on_selector("#sdrpp-approvals", "e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, iw: innerWidth, ih: innerHeight}; }")
            sidebar_x = b.eval_on_selector("#command-sidebar-right", "e => e.getBoundingClientRect().x")
            topmost = b.evaluate("() => { const e = document.getElementById('sdrpp-approvals'); const r = e.getBoundingClientRect(); const t = document.elementFromPoint(r.x + r.width / 2, r.y + 10); return !!t && e.contains(t); }")
            hits = b.evaluate("() => [...document.querySelectorAll('#sdrpp-approvals button')].map(x => { const r = x.getBoundingClientRect(); return x.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)); })")
            c["ok"] = bool(hits) and all(hits) and topmost and box["x"] >= 0 and box["x"] + box["w"] <= sidebar_x and box["y"] >= 0
            c["detail"] = f"panel x={box['x']:.0f}..{box['x'] + box['w']:.0f} sidebar starts x={sidebar_x:.0f} topmost={topmost} buttons_hit={hits}"
            b.click("#sdrpp-approvals-btn")

        with self.case(3, "BRAVO sees ALPHA's command with Approve/Deny; ALPHA sees it as their own, with no buttons") as c:
            b.click("#sdrpp-approvals-btn")
            a.click("#sdrpp-approvals-btn")
            self.refresh(a)
            theirs, mine = self.panel(b), self.panel(a)
            c["ok"] = ("ALPHA" in theirs and "start the capture" in theirs and b.locator("#sdrpp-approvals button", has_text="Approve").count() == 1
                       and "yours" in mine and a.locator("#sdrpp-approvals button").count() == 0)
            c["detail"] = f"bravo={theirs!r} alpha={mine!r}"
            a.click("#sdrpp-approvals-btn")

        with self.case(4, "BRAVO approves: the bridge starts; audit is created, approved, executed with ALPHA proposing and BRAVO approving and executing") as c:
            self.decide(b, "capture", "Approve")
            p = self.last("rf_capture_start")
            self.shot(b, "03-approved")
            c["ok"] = (self.calls().count("start") == 1 and p["proposed_by"] == "op-1" and p["approved_by"] == "op-2" and p["executed_by"] == "op-2"
                       and p["executed"] and self.events(p)[0] == "proposal.created:pending_approval" and self.events(p)[1].startswith("proposal.approved"))
            c["detail"] = f"audit={self.events(p)} calls={self.calls()} notes={self.notes(b)}"

        with self.case(5, "Tune is held for approval and ALPHA is told so; BRAVO denies: nothing is tuned, audit is created then denied, ALPHA's badge clears") as c:
            a.fill("#sdrpp-frequency-input", "145.350M")
            a.click("#sdrpp-tune-btn")
            a.wait_for_timeout(1200)
            self.shot(a, "04-tune-pending")
            tuned_before = [x for x in self.calls() if x.startswith("tune")]
            self.decide(b, "tune", "Deny")
            p = self.last("rf_tune")
            self.shot(b, "05-denied")
            self.refresh(a)
            c["ok"] = (not tuned_before and any("Waiting for a second operator" in n for n in self.notes(a)) and p["denied_by"] == "op-2"
                       and not p["executed"] and self.badge(a) == "Approvals" and not [x for x in self.calls() if x.startswith("tune")])
            c["detail"] = f"audit={self.events(p)} notes={self.notes(a)}"

        with self.case(6, "BRAVO approves a Tune: the bridge tunes to 433.92 MHz; audit shows approved and executed") as c:
            a.fill("#sdrpp-frequency-input", "433.92M")
            a.click("#sdrpp-tune-btn")
            a.wait_for_timeout(1000)
            self.decide(b, "433.920 MHz", "Approve")
            p = self.last("rf_tune")
            c["ok"] = "tune 433920000.0" in self.calls() and p["executed"] and p["approved_by"] == "op-2"
            c["detail"] = f"audit={self.events(p)} calls={self.calls()}"

        with self.case(7, "ALPHA's second session cannot approve ALPHA's own command: shown as own with no buttons, and approving it is a 403") as c:
            a.fill("#sdrpp-frequency-input", "100M")
            a.click("#sdrpp-tune-btn")
            a.wait_for_timeout(1000)
            a2 = self.open_page(browser, "tok-alpha2")
            self.show_spectrogram(a2)
            a2.click("#sdrpp-approvals-btn")
            a2.wait_for_timeout(300)
            pid = a2.evaluate("async () => (await (await ScytheTransport.fetch('/api/mcp/proposals/pending')).json()).proposals[0].proposal_id")
            code = a2.evaluate("async id => (await ScytheTransport.fetch('/api/mcp/proposals/' + id + '/decision', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({decision: 'approve'})})).status", pid)
            self.shot(a2, "06-same-operator-second-session")
            c["ok"] = a2.locator("#sdrpp-approvals button").count() == 0 and code == 403 and "tune 100000000.0" not in self.calls()
            c["detail"] = f"status={code}"
            self.decide(b, "100.000 MHz", "Deny")

        with self.case(8, "Disconnect runs at once without a second operator (halting actions are exempt) and leaves nothing pending") as c:
            stops = self.calls().count("stop")
            a.click("#sdrpp-disconnect-btn")
            a.wait_for_timeout(1000)
            p = self.last("rf_capture_stop")
            self.refresh(a)
            self.shot(a, "07-disconnect")
            status = a.inner_text("#sdrpp-live-status")
            c["ok"] = self.calls().count("stop") == stops + 1 and p["executed"] and p["approved_by"] is None and "STOPPED" in status and self.badge(a) == "Approvals"
            c["detail"] = f"audit={self.events(p)} status={status!r} badge={self.badge(a)!r}"

        with self.case(9, "With the lifecycle unavailable a Tune is a 503 and the UI says so (never a silent success)") as c:
            self.set_lifecycle("off")
            try:
                a.evaluate("() => { window.__notes = []; }")
                a.fill("#sdrpp-frequency-input", "145.350M")
                a.click("#sdrpp-tune-btn")
                a.wait_for_timeout(1000)
                status = a.evaluate("async () => (await ScytheTransport.fetch('/api/sdr/tune', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({frequency_hz: 1e6})})).status")
                self.shot(a, "08-lifecycle-unavailable")
                c["ok"] = status == 503 and any("not initialized" in n or "unavailable" in n for n in self.notes(a))
                c["detail"] = f"status={status} notes={self.notes(a)}"
            finally:
                self.set_lifecycle("on")

        with self.case(10, "A Tune with no frequency is a 400 and the UI shows a plain message (no Python exception text)") as c:
            a.evaluate("() => { window.__notes = []; }")
            code = a.evaluate("async () => (await ScytheTransport.fetch('/api/sdr/tune', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mode: 'NFM'})})).status")
            ui = a.evaluate("async () => { try { await window.SDRPPBridge.tune(NaN); return ''; } catch (e) { return e.message; } }")
            self.shot(a, "09-validation-error")
            c["ok"] = code == 400 and "frequency_hz" in ui and "float()" not in ui and "NoneType" not in ui
            c["detail"] = f"http={code} ui={ui!r}"

        with self.case(11, "Malformed stream settings are a 400 and never become a proposal (validation precedes approval)") as c:
            before = len(self.get("/__audit"))
            bad = [{}, {"nonsense": 1}, {"fft_size": 0}]
            got = a.evaluate("""async bodies => { const out = []; for (const b of bodies) {
                const r = await ScytheTransport.fetch('/api/sdr/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
                out.push([r.status, (await r.json()).outcome || null]); } return out; }""", bad)
            c["ok"] = all(code == 400 and outcome == "invalid" for code, outcome in got) and len(self.get("/__audit")) == before
            c["detail"] = f"responses={got} new_proposals={len(self.get('/__audit')) - before}"

        print("page errors:", json.dumps({k: p.errors for k, p in (("alpha", a), ("bravo", b), ("alpha2", a2)) if p})[:600])


def start_server(python, port, log_path):
    # To a file, not a pipe: nothing reads a pipe while the walkthrough runs, and a full pipe would stall the server.
    log = open(log_path, "w")
    proc = subprocess.Popen([python, str(HERE / "server.py"), "--port", str(port)], cwd=REPO, stdout=log, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 120
    while time.time() < deadline:
        if proc.poll() is not None:
            raise InfrastructureError(f"the harness server exited during start-up (log: {log_path}):\n" + Path(log_path).read_text()[-1500:])
        try:
            urllib.request.urlopen(base + "/__bridge_calls", timeout=2)
            return proc, base
        except Exception:
            time.sleep(1)
    proc.kill()
    raise InfrastructureError("the harness server did not answer within 120 s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(HERE / "out"), help="screenshot directory (default: e2e/sdr_approvals/out)")
    ap.add_argument("--port", type=int, default=5099)
    venv = REPO / ".venv" / "bin" / "python"
    ap.add_argument("--server-python", default=str(venv if venv.exists() else sys.executable),
                    help="interpreter for the server: the project's, with jsonschema (default: .venv if present)")
    args = ap.parse_args()

    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError:
        print("INFRASTRUCTURE: Playwright is not installed for this interpreter (see README.md, Dependencies)")
        return 2
    proc = None
    try:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        proc, base = start_server(args.server_python, args.port, Path(args.out) / "server.log")
        walk = Walkthrough(base, args.out)
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except PlaywrightError as exc:
                raise InfrastructureError(f"Chromium could not be launched (run `playwright install chromium`): {str(exc).splitlines()[0]}")
            try:
                walk.run(browser)
            finally:
                browser.close()
    except InfrastructureError as exc:
        print(f"INFRASTRUCTURE: {exc}")
        return 2
    finally:
        if proc is not None:
            proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)

    passed = sum(1 for _, _, ok in walk.results if ok)
    print(f"{passed}/{len(walk.results)} passed; screenshots in {args.out}")
    return 0 if passed == len(walk.results) else 1


if __name__ == "__main__":
    sys.exit(main())
