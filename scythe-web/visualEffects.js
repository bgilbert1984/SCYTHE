import { renderRealityPrism } from "./realityPrism.js";

function property(entity, name, time) {
  return entity.properties?.[name]?.getValue?.(time) ?? null;
}

export function registerVisualEffects(runtime, { viewer, Cesium, prismRoot, dslRoot = null, correlationRoot = null }) {
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
  const noOp = { apply: () => null, revert: () => undefined };
  runtime.register("view.show-provenance-path", noOp);
  runtime.register("view.highlight-targets", noOp);
  return runtime;
}
