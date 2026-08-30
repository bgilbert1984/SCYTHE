import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, resolve, sep } from "node:path";
import test from "node:test";
import { chromium } from "playwright";

const repositoryRoot = resolve(import.meta.dirname, "..");
const mediaTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
]);

async function startServer() {
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, "http://127.0.0.1");
      const path = resolve(repositoryRoot, `.${decodeURIComponent(url.pathname)}`);
      if (!(path === repositoryRoot || path.startsWith(`${repositoryRoot}${sep}`)) ||
          !(await stat(path)).isFile()) {
        response.writeHead(404).end("not found");
        return;
      }
      response.setHeader("Content-Type", mediaTypes.get(extname(path)) ?? "application/octet-stream");
      response.setHeader("Cache-Control", "no-store");
      response.end(await readFile(path));
    } catch {
      response.writeHead(404).end("not found");
    }
  });
  await new Promise((ready) => server.listen(0, "127.0.0.1", ready));
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}

test("real browser loads a contract-backed optical scenario deterministically", async (context) => {
  const { server, origin } = await startServer();
  context.after(() => new Promise((done) => server.close(done)));
  const browser = await chromium.launch({ headless: true });
  context.after(() => browser.close());
  const page = await browser.newPage();
  await page.goto(`${origin}/scythe-web/browser-harness.html`);

  const result = await page.evaluate(async () => {
    const { installScytheWeb } = await import("./browser-entry.js");
    const { validateContractBoundary } = await import("./contractLoader.js");
    const entities = new Map();
    const listeners = [];
    class Cartesian2 { constructor(x, y) { this.x = x; this.y = y; } }
    const color = (css) => ({ css, withAlpha(alpha) { return { css, alpha }; } });
    const Cesium = {
      Ellipsoid: { WGS84: { cartesianToCartographic: () =>
        ({ longitude: 0, latitude: 0, height: 25 }) } },
      Math: { toDegrees: (value) => value * 180 / Math.PI },
      Cartesian3: { fromDegrees: (longitude, latitude, height = 0) =>
        ({ longitude, latitude, height }) },
      Cartesian2,
      Color: { fromCssColorString: color, BLACK: color("#000"), WHITE: color("#fff") },
      ArcType: { GEODESIC: "GEODESIC" },
      PolylineDashMaterialProperty: class { constructor(options) { Object.assign(this, options); } },
      JulianDate: { toDate: (value) => new Date(value) },
    };
    const viewer = {
      scene: {
        camera: { positionWC: { x: 1, y: 1, z: 1 } },
        postRender: { addEventListener: (listener) => {
          listeners.push(listener); return () => listeners.splice(listeners.indexOf(listener), 1);
        } },
      },
      camera: { setView: (view) => { viewer.startView = view; } },
      clock: { currentTime: "2026-07-30T00:00:00Z" },
      entities: {
        add: (entity) => { entities.set(entity.id, entity); return entity; },
        removeById: (id) => entities.delete(id),
      },
    };
    const contractUrl = `${location.origin}/datasets/meep-slab-650nm-convergence-v1/manifest.json`;
    const client = await installScytheWeb({
      contractUrl,
      Cesium,
      resolveViewer: () => viewer,
      timeSource: () => new Date("2026-07-30T00:00:00Z"),
      scenario: {
        id: "browser-optical-test",
        datasets: [{ id: "optics", kind: "OPTICAL", contractUrl }],
        transmitters: [{ id: "laser", longitudeDegrees: 0, latitudeDegrees: 0,
          heightMeters: 0, frequencyHz: 4.61e14, rangeMeters: 100 }],
        opticalDepthPlaneIndex: 2,
      },
      createTileIndex: () => ({ locate: () =>
        ({ tileId: "plane-2", u: 0.5, v: 0.5, depthPlaneIndex: 2 }) }),
      createTileLoader: () => ({ getTilePayload: async () => ({
        shape: [2, 2],
        realValues: new Float32Array([1, 3, 5, 7]),
        imaginaryValues: new Float32Array([2, 2, 2, 2]),
      }) }),
    });
    await client.layer.tick();
    const query = {
      longitudeDegrees: 0, latitudeDegrees: 0, heightMeters: 25,
      wavelengthNanometers: 650, depthPlaneIndex: 2,
    };
    const first = await client.opticsSampler.sample(query);
    const second = await client.opticsSampler.sample(query);
    const invalid = await fetch(contractUrl).then((response) => response.json());
    invalid.visualizationIsAuthoritative = true;
    let authorityRejected = false;
    try { validateContractBoundary(invalid); } catch { authorityRejected = true; }
    return {
      first: { phase: first.phaseRadians, intensity: first.relativeIntensity,
        evidence: first.evidenceClass, authoritative: first.visualizationIsAuthoritative },
      second: { phase: second.phaseRadians, intensity: second.relativeIntensity },
      entityIds: [...entities.keys()].sort(),
      opticalHud: document.querySelector('[data-role="optical"]')?.textContent,
      evidenceBadge: document.querySelector('[data-role="evidence"]')?.className,
      opticalEntity: entities.get("scythe-web:optical-cue")?.properties,
      authorityRejected,
      descriptorType: client.descriptor.descriptorType,
    };
  });

  assert.deepEqual(result.first, {
    phase: Math.atan2(2, 4), intensity: 20,
    evidence: "SOLVER_OUTPUT", authoritative: false,
  });
  assert.deepEqual(result.second, { phase: result.first.phase, intensity: result.first.intensity });
  assert.equal(result.authorityRejected, true);
  assert.equal(result.descriptorType, "SCYTHE_DATASET_DESCRIPTOR_V1");
  assert.match(result.opticalHud, /PLANE 2.*SOLVER_OUTPUT/);
  assert.match(result.evidenceBadge, /scythe-evidence-solver-output/);
  assert.deepEqual(result.opticalEntity, {
    datasetId: "meep-slab-650nm-convergence-v1",
    evidenceClass: "SOLVER_OUTPUT",
    visualizationIsAuthoritative: false,
  });
  assert.deepEqual(result.entityIds, [
    "scythe-web:optical-cue",
    "scythe-web:range:laser",
    "scythe-web:tx:laser",
  ]);
});

test("real browser verifies and samples the regional Uint16 ITM fixture", async (context) => {
  const { server, origin } = await startServer();
  context.after(() => new Promise((done) => server.close(done)));
  const browser = await chromium.launch({ headless: true });
  context.after(() => browser.close());
  const page = await browser.newPage();
  await page.goto(`${origin}/scythe-web/browser-harness.html`);

  const result = await page.evaluate(async () => {
    const { loadContract } = await import("./contractLoader.js");
    const { createRegionalTileIndex, createRegionalTileLoader } =
      await import("./regionalRfDataset.js");
    const { ScytheRfSampler } = await import("./rfSampler.js");
    const contractUrl = `${location.origin}/datasets/ntia-itm-sf-bay-area-v1/manifest.json`;
    const descriptor = await loadContract(contractUrl);
    const binding = { id: "regional-rf", kind: "RF", contractUrl };
    const sampler = new ScytheRfSampler({
      descriptor,
      tileIndex: await createRegionalTileIndex(descriptor, binding),
      tileLoader: await createRegionalTileLoader(descriptor, binding),
    });
    return sampler.sample({
      longitudeDegrees: -122.50,
      latitudeDegrees: 37.84,
      heightMeters: 1.5,
      utc: "2026-08-02T00:00:00Z",
      frequencyHz: 900_000_000,
      coverageThreshold: { value: 145, units: "dB", comparison: "LTE" },
    });
  });

  assert.equal(result.available, true);
  assert.equal(result.datasetId, "ntia-itm-sf-bay-area-v1");
  assert.equal(result.units, "dB");
  assert.equal(result.evidenceClass, "SOLVER_OUTPUT");
  assert.equal(result.visualizationIsAuthoritative, false);
  assert.equal(Number.isFinite(result.value), true);
  assert.equal(typeof result.coverage, "boolean");
});

test("RF Field Inspector hovers, pins, and preserves a correlation-ready cell", async (context) => {
  const {server,origin}=await startServer(); context.after(()=>new Promise((done)=>server.close(done)));
  const browser=await chromium.launch({headless:true}); context.after(()=>browser.close());
  const page=await browser.newPage(); await page.goto(`${origin}/scythe-web/browser-harness.html`);
  const result=await page.evaluate(async()=>{
    const {MonocleOverlayLayer}=await import("./monocleOverlayLayer.js");
    const actions={}; const entities=new Map(); let picked=null; const events=[];
    class Color {withAlpha(){return this;} static fromCssColorString(){return new Color();}}
    class Handler {setInputAction(callback,type){actions[type]=callback;} destroy(){}}
    const Cesium={Ellipsoid:{WGS84:{cartesianToCartographic:()=>({longitude:0,latitude:0,height:10})}},
      Math:{toDegrees:(value)=>value*180/Math.PI},Cartesian3:{fromDegrees:(longitude,latitude,height=0)=>({longitude,latitude,height})},
      Cartesian2:class{constructor(x,y){this.x=x;this.y=y;}},Rectangle:{fromDegrees:(...values)=>values},
      Color:Object.assign(Color,{BLACK:new Color(),WHITE:new Color()}),ArcType:{GEODESIC:"GEODESIC"},
      PolylineDashMaterialProperty:class{},ScreenSpaceEventHandler:Handler,
      ScreenSpaceEventType:{LEFT_CLICK:"click",MOUSE_MOVE:"move"}};
    const sample={available:true,status:"OK",datasetId:"itm",tileId:"z0",displayAssetHash:"hash-1",
      evidenceClass:"SOLVER_OUTPUT",visualizationIsAuthoritative:false,quantity:"path_loss",value:141.25,units:"dB",
      coverage:true,uncertainty:{kind:"MODEL_BOUND",value:2.5,units:"dB"},
      query:{frequencyHz:900e6,coverageThreshold:{value:145,units:"dB",comparison:"LTE"}},
      provenance:{solverName:"ITM",solverVersion:"1.2",sourceRevision:"source-1",runId:"run-1"}};
    const rfSampler={descriptor:{evidenceClass:"SOLVER_OUTPUT",physics:{rf:{frequencyHz:900e6}}},
      sample:async()=>sample,sampleGrid:async()=>[{x:0,y:0,boundsDegrees:[-123,47,-122,48],sample}]};
    const viewer={scene:{canvas:{},camera:{positionWC:{}},pick:()=>({id:picked}),
      postRender:{addEventListener:()=>()=>{}}},clock:{currentTime:1},entities:{add:(entity)=>{entities.set(entity.id,entity);return entity;},
        removeById:(id)=>entities.delete(id)}};
    const container=document.getElementById("globe-root");
    for(const type of ["scythe-web:coverage-cell-selected","scythe-web:rf-correlate-requested"])
      container.addEventListener(type,(event)=>events.push({type,detail:event.detail}));
    const layer=new MonocleOverlayLayer({viewer,Cesium,rfSampler,container,documentRoot:document,
      fixedStepSeconds:1,timeSource:()=>new Date("2026-08-29T12:00:00Z"),scenario:{coverageThreshold:{value:145,units:"dB",comparison:"LTE"},
        coverageGrid:{westDegrees:-123,southDegrees:47,eastDegrees:-122,northDegrees:48,longitudeCells:1,latitudeCells:1,heightMeters:0}}}).start();
    await layer.tick(); picked=entities.get("scythe-web:coverage:0:0"); layer.setSamplingMode("HOVER");
    actions.move({endPosition:{x:4,y:5}}); actions.click({position:{x:4,y:5}}); layer.setCorrelationAvailable(true);
    document.querySelector('[data-role="correlate"]').click();
    const output={title:document.querySelector(".scythe-web-monocle__title").textContent,
      mode:document.querySelector('[data-role="mode"]').textContent,
      detail:document.querySelector('[data-role="detail"]').textContent,
      provenance:document.querySelector('[data-role="provenance"]').textContent,
      optical:Boolean(document.querySelector('[data-role="optical"]')),events}; layer.destroy(); return output;
  });
  assert.match(result.title,/RF FIELD INSPECTOR \/\/ 900 MHz/); assert.match(result.mode,/PINNED/);
  assert.match(result.detail,/CONTRACT VALUE.*DISPLAY VALUE.*Δ 0\.000/);
  assert.match(result.provenance,/ITM 1\.2.*source-1/); assert.equal(result.optical,false);
  assert.deepEqual(result.events.map((event)=>event.type),
    ["scythe-web:coverage-cell-selected","scythe-web:rf-correlate-requested"]);
  assert.equal(result.events[0].detail.uncertainty.value,2.5);
});

test("real browser renders bounded contextual MCP evidence and routes it to GraphOps", async (context) => {
  const {server, origin} = await startServer();
  context.after(() => new Promise((done) => server.close(done)));
  const browser = await chromium.launch({headless: true});
  context.after(() => browser.close());
  const page = await browser.newPage();
  await page.goto(`${origin}/scythe-web/browser-harness.html`);
  const result = await page.evaluate(async () => {
    const {ContextualWorkbench} = await import("./contextualWorkbench.js");
    const host = document.getElementById("globe-root"); const roots = {};
    for (const panel of ["autopilot", "semantic", "spectrum", "events"]) {
      roots[panel] = document.createElement("section"); host.append(roots[panel]);
    }
    let request = null; let investigation = null;
    host.addEventListener("scythe-web:workbench-investigate", (event) => { investigation = event.detail; });
    const workbench = new ContextualWorkbench({roots, refreshMilliseconds: 60000, fetchImpl: async (_url, options) => {
      request = JSON.parse(options.body);
      return {ok: true, json: async () => ({status: "ok", panel: request.panel,
        selection: request.selection, generatedAt: 1, bounded: true, readOnly: true,
        records: [{tool: "query_hot_entities", status: "ok", authority: "OBSERVATIONAL_MCP",
          result: {entities: [{id: "host:203.0.113.8", degree: 9}]}}],
        proposals: [{tool: "ingest_live_event", boundary: "ORCHESTRATOR APPROVAL REQUIRED"}],
        boundary: "LIVE OBSERVATIONAL MCP RESULTS; REVISION RETAINED"})};
    }});
    workbench.setSelection({kind: "graph-node", entityId: "host:203.0.113.8", graphRevision: "graph-a"});
    await workbench.refresh("events");
    roots.events.querySelectorAll("button")[1].click();
    const output = {request, text: roots.events.textContent, investigation};
    workbench.destroy(); return output;
  });
  assert.deepEqual(result.request, {panel: "events", selection: {kind: "graph-node",
    entityId: "host:203.0.113.8", graphRevision: "graph-a"}});
  assert.match(result.text, /query_hot_entities/);
  assert.match(result.text, /GUARDED CAPABILITIES/);
  assert.equal(result.investigation.selection.graphRevision, "graph-a");
  assert.match(result.investigation.output, /OBSERVATIONAL_MCP/);
});
