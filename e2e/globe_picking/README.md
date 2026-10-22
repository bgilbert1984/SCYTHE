# Globe node picking on the real page

Loads `cesium-hypergraph-globe.html` in headless Chromium with software WebGL and checks that a click on a node selects
that node. The page's API is answered with canned data (a 3x4 grid of seeded nodes), so no backend is needed; the page
does fetch Cesium, three.js and Socket.IO from their CDNs, so it needs network access.

```
python e2e/globe_picking/run.py            # screenshots go to e2e/globe_picking/out/ (git-ignored)
```

Dependencies: Playwright and its Chromium, as for `e2e/sdr_approvals` (`pip install -r e2e/sdr_approvals/requirements.txt`,
then `playwright install chromium`).

## What it checks

- Every node that is on screen and not under a panel is selected exactly, at 1,200 km and 6,000 km above the grid. Where a
  node is on screen comes from Cesium's own projection (`SceneTransforms.wgs84ToWindowCoordinates`), not from the picker.
- A click on empty space, a drag that ends on a node, a hidden node, and a node on the far side of the Earth select nothing;
  the hidden node is selectable again once shown.
- The page raises no script errors.
- Orbital zoom (25,000 km) is reported, not asserted: disks overlap there.

Exit status: 0 passed, 1 a case failed, 2 infrastructure failure (Playwright or Chromium missing, the page or its CDN
scripts did not load).

## Limits

- Software WebGL is slow: allow a few minutes.
- The page's selection handler flies the camera to the selected node; the harness disables `flyToNode` so that each click
  lands where it was computed.
- It does not check how nodes look (rings, glow), only what a click selects.

## Why the harness waits for the camera to settle

The picker projects through camera matrices copied from Cesium once per rendered frame, while the harness asks Cesium's *current* camera
where a node is on screen. Right after the camera is moved the two differ until a frame renders. Under CPU load the page can go well
over the old fixed 700 ms wait without rendering one, and a click aimed with Cesium's answer then lands where the picker's older pose
puts a neighbouring node: "a hidden node cannot be selected" failed intermittently this way (about 1 run in 10 under load, none in 80
on a quiet machine), on the base commit too. `Check.settle()` waits until at least two frames have rendered, the camera matrix is
unchanged across them, and the picker's camera equals Cesium's; if that does not happen in 30 s the harness exits 2 (infrastructure)
instead of reporting a false pick.
