/**
 * The Auto-Recon entity store: the one map of entities (entity_id -> entity) and the one door writers go through.
 *
 * The page's `AutoReconVisualization.entities` is this same map, so every reader of it (the panels, the sensors tool, the metrics probe) sees
 * what a writer puts in. The map is never replaced, only cleared, so a reference taken at any time stays current: the old code
 * replaced it with a fresh `{}` on every reload from the server, which would have cut off anything holding the old one.
 *
 * The page's writers (the satellite buttons, the nmap geolocation, the AIS correlation, the upload and re-ingest code) call
 * `ReconStore.upsert(entity)` instead of reaching into `AutoReconVisualization.entities`, and no longer create a bare
 * `window.AutoReconVisualization = {}` to write into when it is missing.
 */

export function createReconStore(entities = {}) {
  return {
    entities,
    /** Adds the entity, or replaces the one with its id. Returns it. */
    upsert(entity) {
      entities[entity.entity_id] = entity;
      return entity;
    },
    /** Empties the map in place. */
    clear() {
      for (const id of Object.keys(entities)) delete entities[id];
    },
  };
}

/**
 * `window.ReconStore` for the page. Adopts the map that `AutoReconVisualization.entities` already holds (so what an earlier writer put there,
 * even into a bare stub of the object, is kept) and makes it the store's. If the page has no `AutoReconVisualization` the store starts empty and
 * nothing is created for it.
 */
export function adoptReconStore(win) {
  const page = win.AutoReconVisualization;
  const store = createReconStore((page && page.entities) || {});
  if (page) page.entities = store.entities;
  return store;
}
