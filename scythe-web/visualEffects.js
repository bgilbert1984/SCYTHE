import { renderRealityPrism } from "./realityPrism.js";

function property(entity, name, time) {
  return entity.properties?.[name]?.getValue?.(time) ?? null;
}

export function registerVisualEffects(runtime, { viewer, Cesium, prismRoot }) {
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
  const noOp = { apply: () => null, revert: () => undefined };
  runtime.register("view.show-provenance-path", noOp);
  runtime.register("view.highlight-targets", noOp);
  return runtime;
}
