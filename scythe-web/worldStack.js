function line(document, className, text) {
  const element = document.createElement("div"); element.className = className;
  element.textContent = text; return element;
}

export class WorldStack {
  constructor({root = null, store = null} = {}) {
    this.root = root; this.store = store; this.current = store?.snapshot().worldStack ?? null;
    this.render();
  }

  apply(parameters) {
    const previous = this.current;
    this.current = JSON.parse(JSON.stringify(parameters));
    this.store?.replaceWorldStack(this.current); this.render();
    return previous;
  }

  revert(previous) {
    this.current = previous == null ? null : JSON.parse(JSON.stringify(previous));
    this.store?.replaceWorldStack(this.current); this.render();
  }

  render() {
    if (!this.root) return;
    this.root.replaceChildren(); this.root.hidden = !this.current;
    if (!this.current) return;
    const document = this.root.ownerDocument;
    this.root.append(line(document, "world-stack__title",
      `CAUSAL WORLD STACK // ${this.current.executed ? "EXECUTED" : "PREVIEW"}`));
    const observed = this.current.observedWorld;
    this.root.append(line(document, "world-stack__observed",
      `W0 OBSERVED // ${observed.graphRevision} // ${observed.timeWindow.clockId}`));
    for (const world of this.current.worlds) {
      const details = document.createElement("details"); details.className = "world-stack__world";
      const summary = document.createElement("summary");
      summary.textContent = `${world.worldId} // ${world.label} // ${world.support}`;
      details.append(summary);
      details.append(line(document, "world-stack__datum", `EVIDENCE CLASS // ${world.evidenceClass}`));
      details.append(line(document, "world-stack__datum", `ASSUMPTIONS // ${world.assumptions.join(" | ")}`));
      details.append(line(document, "world-stack__datum", `PREDICTS // ${world.predictedObservation}`));
      details.append(line(document, "world-stack__falsifier", `FALSIFIER // ${world.falsifier}`));
      details.append(line(document, "world-stack__next", `NEXT OBSERVATION // ${world.nextObservation}`));
      this.root.append(details);
    }
    this.root.append(line(document, "world-stack__boundary", `BOUNDARY // ${this.current.boundary}`));
  }
}
