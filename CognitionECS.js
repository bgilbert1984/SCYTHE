/**
 * CognitionECS.js — Semantic entity-component substrate (minimal ECS)
 *
 * Hosts become entities; modalities attach as components for selective replay/inference.
 */

const COMPONENT_TYPES = [
  'IdentityComponent',
  'TemporalComponent',
  'TrustComponent',
  'ProtocolFingerprintComponent',
  'FieldComponent',
  'RFSignatureComponent',
  'ReplayStateComponent',
  'SemanticNarrativeComponent',
];

class CognitionEntity {
  constructor(entityId) {
    this.entityId = entityId;
    this.components = new Map();
    this.created_simTime = 0;
  }

  attach(type, instance) {
    this.components.set(type, instance);
    return this;
  }

  get(type) {
    return this.components.get(type);
  }

  has(type) {
    return this.components.has(type);
  }

  toJSON() {
    const out = { entityId: this.entityId, components: {} };
    for (const [type, comp] of this.components) {
      out.components[type] = comp?.toJSON?.() ?? comp;
    }
    return out;
  }
}

class CognitionECS {
  constructor() {
    this.entities = new Map();
  }

  getOrCreate(entityId, simTime = 0) {
    if (!this.entities.has(entityId)) {
      const e = new CognitionEntity(entityId);
      e.created_simTime = simTime;
      this.entities.set(entityId, e);
    }
    return this.entities.get(entityId);
  }

  attachComponent(entityId, type, instance, simTime = 0) {
    const ent = this.getOrCreate(entityId, simTime);
    ent.attach(type, instance);
    return ent;
  }

  queryByComponent(type) {
    const out = [];
    for (const ent of this.entities.values()) {
      if (ent.has(type)) out.push(ent);
    }
    return out;
  }

  export() {
    return {
      entity_count: this.entities.size,
      entities: Array.from(this.entities.values()).map((e) => e.toJSON()),
    };
  }
}

if (typeof window !== 'undefined') {
  window.CognitionECS = CognitionECS;
  window.CognitionEntity = CognitionEntity;
  window.COMPONENT_TYPES = COMPONENT_TYPES;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { CognitionECS, CognitionEntity, COMPONENT_TYPES };
}
