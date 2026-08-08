import { renderRealityPrism } from "./realityPrism.js";

function property(entity, name, time) {
  return entity.properties?.[name]?.getValue?.(time) ?? null;
}

export function registerVisualEffects(runtime, { viewer, Cesium, prismRoot, dslRoot = null,
                                                  correlationRoot = null, worldStack = null }) {
  function panel(text) {
    if (!correlationRoot) return null;
    const previous = {hidden: correlationRoot.hidden, text: correlationRoot.textContent};
    correlationRoot.hidden = false; correlationRoot.textContent = text;
    return previous;
  }
  function restorePanel(previous) {
    if (correlationRoot && previous) {
      correlationRoot.hidden = previous.hidden; correlationRoot.textContent = previous.text;
    }
  }
  runtime.register("view.show-reality-prism", {
    apply(effect) {
      const previous = { hidden: prismRoot.hidden, html: prismRoot.innerHTML };
      renderRealityPrism(prismRoot, effect.parameters);
      return previous;
    },
    revert(effect, receipt) {
      prismRoot.innerHTML = receipt.html;
      prismRoot.hidden = receipt.hidden;
    },
  });
  runtime.register("view.set-coverage-threshold", {
    apply(effect) {
      const previous = [];
      const time = viewer.clock.currentTime;
      for (const entity of viewer.entities.values ?? []) {
        if (!entity.id?.startsWith("scythe-web:coverage:")) continue;
        const value = Number(property(entity, "value", time));
        if (!Number.isFinite(value)) continue;
        const covered = effect.parameters.comparison === "GTE"
          ? value >= effect.parameters.value : value <= effect.parameters.value;
        previous.push([entity, entity.rectangle.material, property(entity, "coverage", time)]);
        entity.rectangle.material = Cesium.Color.fromCssColorString(
          covered ? "#00d4ff" : "#ff445e").withAlpha(covered ? 0.28 : 0.16);
      }
      return previous;
    },
    revert(effect, receipt) {
      for (const [entity, material] of receipt) entity.rectangle.material = material;
    },
  });
  runtime.register("view.show-dsl-preview", {
    apply(effect) {
      if (!dslRoot) return null;
      const previous = {hidden: dslRoot.hidden, text: dslRoot.textContent};
      dslRoot.hidden = false;
      dslRoot.textContent = `GRAPHOPS DSL // ${effect.parameters.executed ? "EXECUTED" : "PREVIEW"}\n${effect.parameters.dsl.join("\n")}`;
      return previous;
    },
    revert(effect, receipt) { if (dslRoot && receipt) { dslRoot.hidden = receipt.hidden; dslRoot.textContent = receipt.text; } },
  });
  runtime.register("view.show-no-data", {
    apply(effect) {
      if (!correlationRoot) return null;
      const previous = {hidden: correlationRoot.hidden, text: correlationRoot.textContent};
      correlationRoot.hidden = false;
      correlationRoot.textContent = `TEMPORAL_EVIDENCE: ${effect.parameters.temporalAuthority}\n${effect.parameters.reason}\nNEXT // ${effect.parameters.requiredObservation}`;
      return previous;
    },
    revert(effect, receipt) { if (correlationRoot && receipt) { correlationRoot.hidden = receipt.hidden; correlationRoot.textContent = receipt.text; } },
  });
  runtime.register("view.show-correlation-fibers", {
    apply(effect) {
      const from = effect.parameters.from; const to = effect.parameters.to;
      const id = `scythe-web:correlation:${encodeURIComponent(effect.effectId)}`;
      viewer.entities.add({id, polyline: {
        positions: [Cesium.Cartesian3.fromDegrees(from[1], from[0], Math.max(from[2] ?? 0, 1000)),
          Cesium.Cartesian3.fromDegrees(to[1], to[0], Math.max(to[2] ?? 0, 1000))],
        width: 3, material: new Cesium.PolylineDashMaterialProperty({
          color: Cesium.Color.fromCssColorString("#f7d154").withAlpha(0.9), dashLength: 18, dashPattern: 0xf0f0,
        })}, properties: {findingClass: effect.parameters.findingClass,
          caveat: effect.parameters.caveat, evidenceClass: "INFERRED"}});
      let previous = null;
      if (correlationRoot) {
        previous = {hidden: correlationRoot.hidden, text: correlationRoot.textContent};
        correlationRoot.hidden = false;
        correlationRoot.textContent = `${effect.parameters.label}\nMATCHES // ${effect.parameters.matches.length}\n${effect.parameters.caveat}`;
      }
      return {id, previous};
    },
    revert(effect, receipt) {
      viewer.entities.removeById(receipt.id);
      if (correlationRoot && receipt.previous) {
        correlationRoot.hidden = receipt.previous.hidden; correlationRoot.textContent = receipt.previous.text;
      }
    },
  });
  runtime.register("view.pin-time", {
    apply(effect) {
      return panel(`TIME PIN // ${effect.parameters.label}\n${new Date(effect.parameters.timestamp * 1000).toISOString()}\nCLOCK // ${effect.parameters.clockId}\nUNCERTAINTY // ±${effect.parameters.uncertaintyMilliseconds} ms`);
    },
    revert(effect, receipt) { restorePanel(receipt); },
  });
  runtime.register("view.show-graph-delta", {
    apply(effect) {
      const changed = [];
      const delta = effect.parameters.delta;
      for (const node of delta.addedNodes ?? []) {
        const entity = viewer.entities.getById?.(`scythe-web:graph-node:${encodeURIComponent(node.id)}`);
        if (!entity?.point) continue;
        changed.push({entity, kind: "node", color: entity.point.color, pixelSize: entity.point.pixelSize});
        entity.point.color = Cesium.Color.fromCssColorString("#7dff7d"); entity.point.pixelSize = 14;
      }
      for (const edge of delta.addedEdges ?? []) {
        const entity = viewer.entities.getById?.(`scythe-web:graph-edge:${encodeURIComponent(edge.id)}`);
        if (!entity?.polyline) continue;
        changed.push({entity, kind: "edge", material: entity.polyline.material, width: entity.polyline.width});
        entity.polyline.material = Cesium.Color.fromCssColorString("#7dff7d"); entity.polyline.width = 4;
      }
      const coverage = delta.windowCoverage;
      const previous = panel(`GRAPH_DELTA // ${effect.parameters.executed ? "EXECUTED" : "PREVIEW"}\nFROM // ${delta.fromGraphRevision ?? "PENDING"}\nTO // ${delta.toGraphRevision ?? "PENDING"}\nADDED // ${delta.addedNodes?.length ?? 0} NODES / ${delta.addedEdges?.length ?? 0} EDGES\nREMOVED // ${delta.removedNodes?.length ?? 0} NODES / ${delta.removedEdges?.length ?? 0} EDGES\nCHANGED // ${delta.changedNodes?.length ?? 0} NODES / ${delta.changedEdges?.length ?? 0} EDGES\nWINDOW // ${coverage ? (coverage.clamped ? "CLAMPED" : "EXACT") : "PREVIEW"}\nBOUNDARY // ${effect.parameters.caveat}`);
      return {changed, previous};
    },
    revert(effect, receipt) {
      for (const item of receipt.changed) {
        if (item.kind === "node") { item.entity.point.color = item.color; item.entity.point.pixelSize = item.pixelSize; }
        else { item.entity.polyline.material = item.material; item.entity.polyline.width = item.width; }
      }
      restorePanel(receipt.previous);
    },
  });
  runtime.register("view.show-graph-provenance", {
    apply(effect) {
      const changed = [];
      for (const node of effect.parameters.path.nodes ?? []) {
        const entity = viewer.entities.getById?.(`scythe-web:graph-node:${encodeURIComponent(node.id)}`);
        if (!entity?.point) continue;
        changed.push({entity, kind: "node", color: entity.point.color, pixelSize: entity.point.pixelSize});
        entity.point.color = Cesium.Color.fromCssColorString("#00d4ff"); entity.point.pixelSize = 12;
      }
      for (const edge of effect.parameters.path.edges ?? []) {
        const entity = viewer.entities.getById?.(`scythe-web:graph-edge:${encodeURIComponent(edge.id)}`);
        if (!entity?.polyline) continue;
        changed.push({entity, kind: "edge", material: entity.polyline.material, width: entity.polyline.width});
        entity.polyline.material = new Cesium.PolylineDashMaterialProperty({
          color: Cesium.Color.fromCssColorString("#00d4ff"), dashLength: 12,
        }); entity.polyline.width = 3;
      }
      const path = effect.parameters.path;
      const previous = panel(`PROVENANCE IMPACT // ${effect.parameters.executed ? "EXECUTED" : "PREVIEW"}\nENTITIES // ${(path.nodes?.length ?? 0) + (path.edges?.length ?? 0)}\nDECLARED SOURCES // ${path.sources?.length ?? 0}\nBOUNDARY // ${effect.parameters.caveat}`);
      return {changed, previous};
    },
    revert(effect, receipt) {
      for (const item of receipt.changed) {
        if (item.kind === "node") { item.entity.point.color = item.color; item.entity.point.pixelSize = item.pixelSize; }
        else { item.entity.polyline.material = item.material; item.entity.polyline.width = item.width; }
      }
      restorePanel(receipt.previous);
    },
  });
  runtime.register("view.show-contradictions", {
    apply(effect) {
      const changed = [];
      for (const finding of effect.parameters.findings) {
        const entity = viewer.entities.getById?.(`scythe-web:graph-edge:${encodeURIComponent(finding.id)}`);
        if (!entity?.polyline) continue;
        changed.push({entity, material: entity.polyline.material, width: entity.polyline.width});
        entity.polyline.material = new Cesium.PolylineDashMaterialProperty({
          color: Cesium.Color.fromCssColorString("#ff445e"), dashLength: 18, dashPattern: 0xaaaa,
        }); entity.polyline.width = 5;
      }
      const previous = panel(`CONTRADICTIONS // ${effect.parameters.executed ? "EXPOSED" : "PREVIEW"}\nROOT // ${effect.parameters.root}\nRELATIONS // ${effect.parameters.findings.length}\nPOLICY // ${effect.parameters.caveat}`);
      return {changed, previous};
    },
    revert(effect, receipt) {
      for (const item of receipt.changed) { item.entity.polyline.material = item.material; item.entity.polyline.width = item.width; }
      restorePanel(receipt.previous);
    },
  });
  runtime.register("view.show-causal-worlds", {
    apply(effect) { return worldStack?.apply(effect.parameters) ?? null; },
    revert(effect, receipt) { worldStack?.revert(receipt); },
  });
  const noOp = { apply: () => null, revert: () => undefined };
  runtime.register("view.show-provenance-path", noOp);
  runtime.register("view.highlight-targets", noOp);
  return runtime;
}
