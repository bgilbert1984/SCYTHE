import assert from "node:assert/strict";
import test from "node:test";

import {clampPanelSize} from "./resizablePanel.js";

test("panel dimensions remain usable and inside the viewport", () => {
  assert.deepEqual(clampPanelSize({width: 180, height: 120}, {viewportWidth: 1440, viewportHeight: 900}),
    {width: 320, height: 220});
  assert.deepEqual(clampPanelSize({width: 2000, height: 1200}, {viewportWidth: 1440, viewportHeight: 900}),
    {width: 1416, height: 876});
});

test("small viewports reduce the effective minimum instead of overflowing", () => {
  assert.deepEqual(clampPanelSize({width: 460, height: 300}, {viewportWidth: 300, viewportHeight: 210}),
    {width: 276, height: 186});
});
