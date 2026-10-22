"""Node picking on the real globe page, in headless Chromium.  See README.md.

    python e2e/globe_picking/run.py [--out DIR]

Serves the repository root, answers the page's API calls with canned data (a grid of seeded nodes), loads
cesium-hypergraph-globe.html with software WebGL, and checks that a click on a node selects exactly that node. Where a
node is on screen is asked of Cesium's own projection, not of the picker under test.

Exit status:  0 every case passed;  1 a case failed;  2 infrastructure failure (Playwright or Chromium missing, the page
or its CDN scripts did not load).
"""
import argparse
import http.server
import json
import os
import sys
import threading
import time
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml"}
# A grid far enough apart that every node is a distinct disk at the zoom levels asserted on.
NODES = [{"node_id": f"n{row * 4 + col}", "lat": 34.0 + row * 4.0, "lon": -108.0 + col * 5.0, "confidence": 0.95,
          "method": "observed" if (row + col) % 2 == 0 else "neighbor_inferred"} for row in range(3) for col in range(4)]
VIEW_LON, VIEW_LAT = -100.5, 38.0
# metres above the grid -> how many of the 12 nodes must be reachable (on the canvas, not under a panel) at that height.
# Measured on a 1400x850 viewport: 4 fit at 1,200 km and all 12 at 6,000 km. Fewer reachable means the page layout changed.
ASSERTED_HEIGHTS = {1.2e6: 4, 6e6: 12}
CASE_HEIGHT = 6e6                    # the zoom the empty-space, drag and hidden-node cases use (all 12 nodes reachable)
REPORTED_HEIGHT = 2.5e7               # orbital: disks overlap, so only reported


class InfrastructureError(Exception):
    pass


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply_json(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self.reply_json({"status": "ok"})

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/cache/nodes":
            return self.reply_json({"status": "ok", "nodes": NODES, "count": len(NODES)})
        if path.startswith("/api/"):
            return self.reply_json({"status": "ok", "nodes": [], "edges": [], "events": [], "entities": [], "proposals": [], "total": 0})
        target = REPO / (path.lstrip("/") or "cesium-hypergraph-globe.html")
        if target.is_file() and REPO in target.resolve().parents:
            self.send_response(200)
            self.send_header("content-type", TYPES.get(target.suffix, "application/octet-stream"))
            self.end_headers()
            self.wfile.write(target.read_bytes())
        else:
            self.send_response(404)
            self.end_headers()


class Check:
    def __init__(self, page, out):
        self.page, self.out, self.results = page, Path(out), []
        self.out.mkdir(parents=True, exist_ok=True)

    def record(self, name, ok, detail=""):
        self.results.append((name, ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    def view(self, height, lon=VIEW_LON, lat=VIEW_LAT):
        self.page.evaluate(f"globe._viewer.camera.setView({{destination: Cesium.Cartesian3.fromDegrees({lon}, {lat}, {height})}})")
        self.settle()

    # What the camera is doing, and whether the camera the picker reads is the one Cesium has.
    CAMERA_STATE = """() => { const vm = Array.from(globe._viewer.camera.viewMatrix), tm = globe._camera.matrixWorldInverse.elements;
      return {frame: globe._viewer.scene.frameState.frameNumber, vm, synced: Math.max(...vm.map((v, i) => Math.abs(v - tm[i]))) < 1e-6}; }"""

    def settle(self, timeout_s=30):
        """Wait until the camera has stopped and the picker has caught up with it.

        The picker projects through the camera matrices copied from Cesium once per rendered frame, while where a node is on
        screen is asked of Cesium's current camera. Right after the camera is moved those differ until a frame renders; under
        load the page can go well over a fixed 700 ms without one, and a click aimed with Cesium's answer then lands where the
        picker's older pose puts a neighbouring node. So: two or more frames rendered, the matrix unchanged across them, and
        the two cameras equal.
        """
        deadline = time.monotonic() + timeout_s
        anchor = None
        while time.monotonic() < deadline:
            now = self.page.evaluate(self.CAMERA_STATE)
            if anchor is None or now["vm"] != anchor["vm"]:
                anchor = now        # the first sample, or the camera moved: count frames from here
            elif now["frame"] >= anchor["frame"] + 2 and now["synced"]:
                return              # (counted from the anchor, not between polls: under load a poll may see one frame or none)
            self.page.wait_for_timeout(100)
        raise InfrastructureError(f"the camera did not settle within {timeout_s} s (frames are not rendering, or it keeps moving)")

    def screen(self, node_id):
        """Where Cesium itself puts the node, in page pixels, or None when it is off the canvas."""
        return self.page.evaluate("""(id) => {
          const node = globe._graph.nodes.get(id);
          const c = Cesium.SceneTransforms.wgs84ToWindowCoordinates(globe._viewer.scene, Cesium.Cartesian3.fromDegrees(node.lon, node.lat));
          const r = globe._viewer.scene.canvas.getBoundingClientRect();
          return c ? [c.x + r.left, c.y + r.top] : null; }""", node_id)

    def selected(self):
        return self.page.evaluate("globe._selectedEntityId")

    def clickable(self, point):
        """A point on the canvas itself, not under the page's own panels."""
        return bool(point) and 0 <= point[0] < 1400 and 0 <= point[1] < 850 and \
            self.page.evaluate("([x, y]) => document.elementFromPoint(x, y).tagName", point) == "CANVAS"

    def click_node(self, height, node_id):
        self.view(height)
        self.page.evaluate("globe.selectNode(null)")
        point = self.screen(node_id)
        if not self.clickable(point):
            return "unreachable"
        self.page.mouse.click(*point)
        self.page.wait_for_timeout(100)
        return self.selected()

    def run(self):
        ids = [n["node_id"] for n in NODES]
        for height, minimum in ASSERTED_HEIGHTS.items():
            outcomes = {i: self.click_node(height, i) for i in ids}
            reachable = [i for i, got in outcomes.items() if got != "unreachable"]
            wrong = {i: got for i, got in outcomes.items() if got not in (i, "unreachable")}
            self.record(f"every reachable node is selected exactly at {height / 1000:.0f} km",
                        len(reachable) >= minimum and not wrong, f"{len(reachable) - len(wrong)}/{len(reachable)} exact, {len(ids) - len(reachable)} unreachable (need {minimum} reachable), wrong: {wrong}")
            self.page.screenshot(path=str(self.out / f"picking-{int(height / 1000)}km.png"))
        outcomes = {i: self.click_node(REPORTED_HEIGHT, i) for i in ids}
        exact = sum(1 for i, got in outcomes.items() if got == i)
        print(f"INFO  orbital {REPORTED_HEIGHT / 1000:.0f} km (not asserted): {exact}/{len(ids)} exact, "
              f"{sum(1 for got in outcomes.values() if got == 'unreachable')} unreachable")

        self.view(CASE_HEIGHT)
        self.page.evaluate("globe.selectNode(null)")
        self.page.mouse.click(1250, 300)
        self.page.wait_for_timeout(100)
        self.record("a click on empty space selects nothing", self.selected() is None)

        self.view(CASE_HEIGHT)
        point = self.screen("n0")
        self.page.mouse.move(*point)
        self.page.mouse.down()
        self.page.mouse.move(point[0] + 60, point[1] + 5, steps=6)
        self.page.mouse.up()
        self.page.wait_for_timeout(150)
        self.record("a drag that ends on a node does not select it", self.selected() is None)

        self.view(CASE_HEIGHT)
        point = self.screen("n5")
        self.page.evaluate("globe.setNodeVisible('n5', false)")
        self.page.evaluate("globe.selectNode(null)")
        self.page.mouse.click(*point)
        self.page.wait_for_timeout(100)
        picked = self.selected()
        self.record("a hidden node cannot be selected", picked is None, "" if picked is None else f"selected {picked!r} instead")
        self.page.evaluate("globe.setNodeVisible('n5', true)")
        self.page.mouse.click(*point)
        self.page.wait_for_timeout(100)
        self.record("the same node is selectable again once shown", self.selected() == "n5")

        self.view(6e6, lon=80, lat=-38)
        self.page.evaluate("globe.selectNode(null)")
        self.page.mouse.click(700, 425)
        self.page.wait_for_timeout(100)
        self.record("with the Earth between the camera and the nodes, nothing is selected", self.selected() is None)
        self.record("the page raised no script errors", not self.page.errors, "; ".join(self.page.errors[:3]))
        return all(ok for _, ok in self.results)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
    args = parser.parse_args()
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
            page.goto(base + "/", wait_until="domcontentloaded")
            try:
                page.wait_for_function("typeof globe !== 'undefined' && globe._viewer && globe._nodeCount >= %d" % len(NODES), timeout=60000)
            except PlaywrightError as error:
                print(f"INFRASTRUCTURE: the globe did not come up (CDN scripts unreachable?): {str(error)[:160]}")
                return 2
            page.evaluate("document.querySelectorAll('button').forEach(b => { if (b.textContent.trim() === 'Not now') b.click(); })")
            # The page's own selection handler flies the camera to the node; hold it still so the next click is where it was computed.
            page.evaluate("globe.flyToNode = () => {}")
            ok = Check(page, args.out).run()
            browser.close()
    except InfrastructureError as error:
        print(f"INFRASTRUCTURE: {error}")
        return 2
    except PlaywrightError as error:
        print(f"INFRASTRUCTURE: {str(error)[:200]}")
        return 2
    finally:
        server.shutdown()
    print("\nall passed" if ok else "\nFAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
