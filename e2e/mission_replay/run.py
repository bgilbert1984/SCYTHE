"""Mission replay invariants on the real globe page, in headless Chromium.  See README.md.

    python e2e/mission_replay/run.py [--out DIR]

Loads cesium-hypergraph-globe.html?mission=m1 with canned mission events, draws edges through the page's own handlers,
scrubs the mission timeline, and asserts which edges are hidden and how each edge event was matched. It checks logic,
not appearance.

Exit status:  0 every case passed;  1 a case failed;  2 infrastructure failure (Playwright or Chromium missing, the page
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

T0 = 1_700_000_000_000
SECOND = 1000


def event(seq, offset, kind, entity, **payload):
    return {"seq": seq, "timestamp": T0 + offset * SECOND, "event_type": kind, "entity_id": entity, "session_id": "s", "payload": payload}


EVENTS = [
    event(1, 0, "NODE_CREATE", "n0"), event(2, 1, "NODE_CREATE", "n1"),
    event(3, 5, "EDGE_CREATE", "edge-by-id", src="n0", dst="n1"),            # id matches the drawn edge
    event(4, 6, "EDGE_CREATE", "server-id-B", src="n2", dst="n3"),           # id differs; ends match exactly one drawn edge
    event(5, 7, "EDGE_CREATE", "server-id-D", src="n6", dst="n7"),           # ends match two drawn edges: ambiguous
    event(6, 8, "EDGE_CREATE", "ghost"),                                      # names no ends
    event(7, 9, "probe", "n0"),
    event(8, 3, "EDGE_CREATE", "edge-del", src="n8", dst="n9"),              # a drawn edge the server later deletes
    event(9, 8, "EDGE_DELETE", "edge-del", id="edge-del"),
]
# Drawn edges: id -> (src, dst). "edge-C" has no event at all.
DRAWN = {"edge-by-id": ("n0", "n1"), "drawn-B": ("n2", "n3"), "edge-C": ("n4", "n5"), "dup-1": ("n6", "n7"), "dup-2": ("n6", "n7"),
         "edge-del": ("n8", "n9")}
SCRUBS = [   # seconds after T0 -> edges that must be hidden (among the drawn ones)
    (2, {"edge-by-id", "drawn-B", "edge-del"}),   # before any of them was created
    (5.5, {"drawn-B"}),                           # edge-by-id and edge-del exist, drawn-B does not yet
    (6.5, set()),                                 # all exist
    (8.5, {"edge-del"}),                          # edge-del was deleted at +8s; unmatched events hide nothing
                                                  # (+9s is the newest event, where the slider means Live, so it is not probed)
]
EXPECTED_IDENTITY = {"byId": 3, "byEndpoints": 1, "unmatched": 2}


class Handler(globe_page.Handler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/missions/m1":
            return self.reply_json({"mission": {"status": "open", "name": "M1"}})
        if path == "/api/missions/m1/events":
            return self.reply_json({"events": EVENTS, "total": len(EVENTS)})
        return super().do_GET()


class Check:
    def __init__(self, page):
        self.page, self.results = page, []

    def record(self, name, ok, detail=""):
        self.results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
        if not ok:
            print("      state:", self.page.evaluate("""() => JSON.stringify({
              identity: scytheMissionViewport.state.edgeIdentity, scrubMs: scytheMissionViewport.state.scrubMs,
              events: scytheMissionViewport.state.events.length, deleted: [...globe._deletedEdges], hidden: [...globe._hiddenEdges],
              drawn: [...globe._graph.edges.entries()].filter(([id]) => !id.startsWith('prox:')).map(([id, e]) => [id, e.src, e.dst]),
              queue: globe._updateQueue.length})"""))
            print("      matches:", self.page.evaluate("""async () => {
              const m = await import('/scythe-web/missionViewport.js');
              const edges = globe._graph.edges;
              return JSON.stringify(m.resolveEdgeEvents(scytheMissionViewport.state.events, edges).matches
                .map(x => [x.event.entity_id, x.mode, x.edgeId, m.edgeEndpoints(x.event)]));
            }"""))

    def hidden(self):
        drawn = set(DRAWN)
        return set(self.page.evaluate("[...globe._hiddenEdges]")) & drawn

    def scrub_to(self, seconds):
        self.page.evaluate("(ms) => { const s = document.querySelector('.mvp__slider'); s.value = ms; s.dispatchEvent(new Event('input')); }",
                           T0 + seconds * SECOND)
        self.page.wait_for_timeout(400)

    def visible_ids(self):
        return {e["id"] for e in self.page.evaluate("globe._getVisibleEdges().map(e => ({id: e.id}))")}

    def graph_event(self, event_type, entity_id, **data):
        """Deliver an event through the page's own graph_event socket handler, as the server would."""
        self.page.evaluate("""([type, id, data]) => globe._socket.listeners('graph_event')[0](
            {event_type: type, entity_id: id, entity_kind: 'edge', data}) """, [event_type, entity_id, data])
        # The globe applies queued updates on a timer, which software rendering can delay: wait for the queue to drain.
        self.page.wait_for_function("globe._updateQueue.length === 0", timeout=15000)
        self.page.wait_for_timeout(150)

    def live_delete_cases(self):
        self.record("before a delete, the edge is drawn live", "edge-del" in self.visible_ids())
        self.graph_event("EDGE_DELETE", "edge-del", id="edge-del")
        self.record("a live EDGE_DELETE hides the edge", "edge-del" not in self.visible_ids())
        self.record("  and keeps it, so a scrub can still show it", self.page.evaluate("globe._graph.edges.has('edge-del')"))
        before = self.visible_ids()
        self.graph_event("EDGE_DELETE", "never-drawn", id="never-drawn")
        self.record("deleting an edge that was never drawn changes nothing", self.visible_ids() == before)

    def live_recreate_cases(self):
        self.graph_event("EDGE_CREATE", "edge-del", src="n8", dst="n9", confidence=0.9)
        self.record("an edge the server creates again is drawn again", "edge-del" in self.visible_ids())
        self.graph_event("EDGE_DELETE", "edge-del", id="edge-del")
        self.record("and a second delete hides it again", "edge-del" not in self.visible_ids())

    def run(self):
        page = self.page
        page.evaluate("""(drawn) => { for (const [id, [src, dst]] of Object.entries(drawn))
            globe._queueUpdate({type: 'edge_update', id, src, dst, confidence: 0.9, entropy: 0.5}); }""", {k: list(v) for k, v in DRAWN.items()})
        page.wait_for_function("(ids) => ids.every(id => globe._graph.edges.has(id))", arg=list(DRAWN), timeout=15000)
        page.wait_for_function("scytheMissionViewport.state.events.length >= %d" % len(EVENTS), timeout=30000)
        page.evaluate("scytheMissionViewport.refresh()")     # the panel recomputes on its poll; do not race it
        page.wait_for_timeout(500)
        identity = page.evaluate("scytheMissionViewport.state.edgeIdentity")
        self.record("edge events are matched by id, by endpoints, or not at all", identity == EXPECTED_IDENTITY, f"{identity}")
        self.record("live hides no edge by the timeline", self.hidden() == set(), f"{sorted(self.hidden())}")
        self.live_delete_cases()
        for seconds, expected in SCRUBS:
            self.scrub_to(seconds)
            got = self.hidden()
            self.record(f"scrubbed to +{seconds}s hides exactly {sorted(expected) or 'nothing'}", got == expected, f"hidden: {sorted(got)}")
            visible = {e["id"] for e in page.evaluate("globe._getVisibleEdges().map(e => ({id: e.id}))")}
            shown_ok = set(DRAWN) - expected <= visible and not (expected & visible)
            self.record(f"  the drawn arcs agree at +{seconds}s", shown_ok)
        self.record("an edge that no event names is never hidden", "edge-C" not in self.hidden())
        self.record("two edges sharing ends are never matched by their ends", not ({"dup-1", "dup-2"} & self.hidden()))
        page.evaluate("document.querySelector('.mvp__live').click()")
        page.wait_for_timeout(400)
        self.record("Live restores every edge the timeline hid", self.hidden() == set())
        self.record("back on Live, the deleted edge stays hidden", "edge-del" not in self.visible_ids())
        self.live_recreate_cases()
        page.evaluate("scytheMissionViewport.refresh()")     # recompute now rather than race the poll
        page.wait_for_timeout(300)
        tooltip = page.evaluate("document.querySelector('.mvp__clock').title")
        self.record("the tooltip reports the match counts", "3 by id, 1 by their ends, 2 not matched" in tooltip, tooltip)
        self.record("the page raised no script errors", not page.errors, "; ".join(page.errors[:3]))
        return all(self.results)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.parse_args()
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
            page = browser.new_page(viewport={"width": 1400, "height": 850})
            page.errors = []
            page.on("pageerror", lambda e: page.errors.append(str(e)[:160]))
            page.add_init_script(f"window.SCYTHE_API_BASE = '{base}'")
            page.goto(base + "/?mission=m1", wait_until="domcontentloaded")
            try:
                page.wait_for_function("typeof globe !== 'undefined' && globe._viewer && globe._nodeCount >= 12 && typeof scytheMissionViewport !== 'undefined'", timeout=60000)
            except PlaywrightError as error:
                print(f"INFRASTRUCTURE: the globe did not come up (CDN scripts unreachable?): {str(error)[:160]}")
                return 2
            # The page draws synthetic proximity arcs (prox:<a>-><b>) between nearby nodes from about 8 s after load. One of them
            # would share its ends with a test edge, which the matcher rightly treats as ambiguous, so turn the generator off
            # and remove what it already drew: this harness tests only the edges it draws itself.
            page.evaluate("window.buildProximityEdges = () => 0")
            # Upserts the generator already queued are applied on the globe's timer: let them land first, or they would
            # put the edges back after the removal below.
            for _ in range(3):
                page.wait_for_function("globe._updateQueue.length === 0", timeout=30000)
                page.wait_for_timeout(300)
                page.evaluate("[...globe._graph.edges.keys()].filter(id => id.startsWith('prox:')).forEach(id => globe._queueUpdate({type: 'edge_remove', edge_id: id}))")
            page.wait_for_function("globe._updateQueue.length === 0", timeout=30000)
            if page.evaluate("[...globe._graph.edges.keys()].some(id => id.startsWith('prox:'))"):
                print("INFRASTRUCTURE: proximity edges kept reappearing; the harness cannot isolate its own edges")
                return 2
            ok = Check(page).run()
            browser.close()
    except PlaywrightError as error:
        print(f"INFRASTRUCTURE: {str(error)[:200]}")
        return 2
    finally:
        server.shutdown()
    print("\nall passed" if ok else "\nFAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
