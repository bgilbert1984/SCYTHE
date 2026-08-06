import { validateEffectPlan } from "./directiveProtocol.js";

export class EffectRuntime {
  constructor() {
    this.handlers = new Map();
    this.applied = new Map();
  }

  register(type, handler) {
    if (typeof handler?.apply !== "function" || typeof handler?.revert !== "function") {
      throw new TypeError("Effect handlers require apply and revert functions");
    }
    this.handlers.set(type, handler);
    return this;
  }

  async applyPlan(planInput, context = {}) {
    const plan = validateEffectPlan(planInput);
    const applied = [];
    try {
      for (const effect of plan.effects) {
        if (this.applied.has(effect.effectId)) continue;
        const handler = this.handlers.get(effect.type);
        if (!handler) throw new Error(`No runtime handler for ${effect.type}`);
        const receipt = await handler.apply(effect, context);
        this.applied.set(effect.effectId, { effect, receipt, handler, planId: plan.planId });
        applied.push(effect.effectId);
      }
      return Object.freeze({ planId: plan.planId, applied: Object.freeze(applied) });
    } catch (error) {
      for (const effectId of applied.reverse()) await this.revert(effectId, context);
      throw error;
    }
  }

  async revert(effectId, context = {}) {
    const entry = this.applied.get(effectId);
    if (!entry) return false;
    await entry.handler.revert(entry.effect, entry.receipt, context);
    this.applied.delete(effectId);
    return true;
  }

  async revertPlan(planId, context = {}) {
    const ids = [...this.applied.entries()].filter(([, value]) => value.planId === planId).map(([id]) => id).reverse();
    for (const id of ids) await this.revert(id, context);
    return ids.length;
  }
}
