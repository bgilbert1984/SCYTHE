# Workspace runtime baseline

How far `command-ops-visualization.html` has moved into the workspace runtime (`scythe-web/`), measured by a script so the next measurement is a command, not a
hand edit.

- **Baseline commit:** `a75e3d94` (main after #339, OperatorSession step 5), measured 2026-10-25.
- **Tool:** `tools/measure_workspace.py` (JSON by default, `--markdown` for a table, `--ref <commit>` for any commit; it reads the files of the ref, not the working
  tree). Its tests are `tools/test_measure_workspace.py` (`cd tools && python3 -m unittest test_measure_workspace`).
- **Snapshot:** `docs/architecture/workspace-baseline.json` is the tool's output for `a75e3d94`. Regenerate with
  `python3 tools/measure_workspace.py --ref a75e3d94 > docs/architecture/workspace-baseline.json` and the file does not change.

To compare a later commit with this one: `python3 tools/measure_workspace.py --ref HEAD`, and read the two JSON files side by side.

## Purpose

The page started as the application, its state, its rendering and its business logic at once. The work since #276 moves page objects into tools with a
lifecycle (`scythe-web/lifecycle.js`), services provided by `workspaceBoot.js`, pure logic modules and stores. Line count alone says how much text left the page.
These measures say what the page still owns and what the runtime owns.

## Progress

All numbers below come from the script run at each commit.

| | #276 | #300 | #331 | #335 | #339 |
|---|---|---|---|---|---|
| Page lines | 44,022 | 43,892 | 36,987 | 36,431 | **36,112** |
| Plugins (lifecycle tools) | 0 | 2 | 15 | 17 | **18** |
| Services (`provide()`) | 0 | 8 | 44 | 51 | **52** |
| Logic modules | 63 | 69 | 73 | 73 | **73** |
| Test cases | 234 | 347 | 1,050 | 1,138 | **1,206** |
| Page `fetch(` | 191 | 187 | 154 | 145 | **140** |
| Page `new EventSource` | 10 | 10 | 7 | 7 | **7** |
| Page `setInterval(` | 24 | 24 | 19 | 19 | **18** |
| Page `Cesium.` | 963 | 959 | 741 | 741 | **738** |
| Page `viewer.` | 438 | 435 | 360 | 360 | **357** |
| Page `window.X =` | 241 | 241 | 224 | 222 | **221** |

## The runtime at `a75e3d94`

- 18 plugins (3,973 lines) registered in `workspaceBoot.js` (1,086 lines), with 52 services and 22 `win.X =` shims for the old page.
- 73 logic modules (11,969 lines) and 2 stores (`reconStore`, `investigationStore`).
- 1,206 test cases in 88 test files (16,720 lines), about the size of the source they cover.
- The plugins read `win.*` or `window.*` 24 times in all.

## What the page still owns

- 29 inline scripts, about 30,200 lines, and 70 external scripts.
- Four page objects of 20 lines or more: `AISVisualization` (1,368 lines), `RFPropagationManager` (609), `PcapGlobeOverlay` (379), `AISStreamManager` (84).
- Three page classes: `CesiumEntityManager`, `ScytheVirtualList`, `WebGLHypergraphRenderer`.
- Most of the inline script is not a named object but functions and handlers that the object list does not show.

## What to watch

The numbers that say who owns a resource: `fetch(`, `new EventSource`, `setInterval(`, `addEventListener(`, `Cesium.`, `new Cesium.`, `viewer.`. When these move behind
services the architecture has changed.

The DOM numbers (`innerHTML`, `textContent` and `.style.` writes, `getElementById`, `querySelector`, inline `on*=` handlers) are trend indicators only. Direct DOM
access is often right, and driving these to zero is not a goal: the goal is less unnecessary coupling.

## Checklist

A reading of the evidence above, not a score.

| Goal | Status | Basis |
|---|---|---|
| Lifecycle runtime | done | `lifecycle.js`; every plugin uses it |
| Plugin registration | done | 18 `workspace.register` calls in `workspaceBoot.js` |
| Workspace services | done | 52 `provide()` services |
| Logic extraction | in progress | 73 logic modules; the page still holds four objects, three classes and most of its inline script |
| Store consolidation | in progress | 2 stores; whether the page and plugins still hold duplicate state is not measured |
| Transport abstraction | in progress | the page still has 140 `fetch(` and 7 `new EventSource` |
| Page object elimination | in progress | four objects of 20+ lines left |
| Renderer abstraction | in progress | 738 `Cesium.` and 357 `viewer.` references in the page |
| Command shell only | not started | 29 inline scripts remain |

## Caveats

- The counts are regular expressions over source text. They compare one commit with another; they are not exact facts. An `innerHTML` in a comment counts, and the inline
  script count depends on the pattern (an earlier hand count that used another pattern found 25).
- The `==` and `===` comparisons are not counted as DOM writes (found while testing the script: a first version counted them).
- The `window.X =` count mixes compatibility aliases with exports the runtime needs. They are not separated yet.
- The "logic modules" are every file in `scythe-web/` that is not a test, plugin, store, `workspaceBoot.js` or one of the runtime's own files, so they include views and
  browser entry points as well as pure functions.
- Not measured yet: duplicate state stores, and the relationships between plugins, services and logic modules (a dependency graph, which this tool is meant to feed later).
