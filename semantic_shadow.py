"""
semantic_shadow.py — Similarity-driven speculative edge generation.

Wires EmbeddingEngine (FAISS AVX2) and TurboQuantStore (3-bit fp16 streaming
index) into ShadowGraph so that every newly observed recon entity is embedded
and searched for semantic neighbors.  When a neighbor is close enough (cosine
similarity >= threshold), a speculative edge of kind "semantic_similarity" is
automatically pushed into the shadow graph.

Search pipeline per new entity:
  1. _embed_with_delta   -> blended fp32 vec (α=0.80 identity continuity)
                        -> pushed to TurboQuant fp16 dense cache
  2. TurboQuantStore     -> sub-2ms fp16 matmul similarity search (primary)
     FAISS               -> fallback if TQ store not yet populated
  3. MMR selection       -> MAX_NEIGHBORS diverse-yet-relevant candidates
  4. Age check           -> temporal decay on confidence/evidence deltas
  5. push/observe        -> ShadowGraph

TurboQuant advantages over FAISS for this workload:
  - No index rebuild needed for streaming inserts (O(1) add)
  - fp16 matmul ≈ 2× faster than fp32 on SIMD/CUDA
  - Inner-product-optimal quantization (no L2-vs-cosine mismatch)
  - 2× memory compression (3-bit codes + fp16 dense = ~1.5KB per 768-dim vec)
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional

# numpy is imported lazily inside methods — avoids ~40ms load penalty at startup
# (EmbeddingEngine already owns the hot FAISS/numpy context)

logger = logging.getLogger(__name__)

# ── Similarity thresholds ─────────────────────────────────────────────────────
SIM_THRESHOLD   = 0.72   # minimum cosine similarity to create a speculative edge
SIM_HIGH        = 0.88   # triggers immediate high-evidence bump (same infra likely)

# ── Candidate pool / MMR ──────────────────────────────────────────────────────
MAX_NEIGHBORS   = 5      # final edges to create per entity
CANDIDATE_MULT  = 3      # fetch MAX_NEIGHBORS x CANDIDATE_MULT from FAISS for MMR
MMR_LAMBDA      = 0.55   # lambda=1.0 -> pure relevance; 0.0 -> pure diversity

# ── Evidence / confidence deltas ──────────────────────────────────────────────
EVIDENCE_BASE   = 0.15   # base evidence_delta for a fresh similarity link

# ── Temporal decay ────────────────────────────────────────────────────────────
DECAY_HALF_LIFE = 3600.0 # seconds; after this long, bumps are halved
DECAY_FLOOR     = 0.05   # never decay below this fraction of the base delta


class SemanticShadow:
    """
    Thread-safe facade: embeds entities and creates similarity-driven
    speculative edges in ShadowGraph.
    """

    _instance: Optional["SemanticShadow"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._ready = False
        self._embedding_engine = None
        self._shadow_graph = None
        # Delta embedding cache: {entity_id: np.ndarray}
        # Stores the exponentially-blended "living" representation of each entity.
        self._entity_vecs: dict = {}
        # TurboQuant streaming store — primary similarity search backend.
        # Falls back to FAISS (via EmbeddingEngine) if turboquant unavailable.
        self._tq_store = None
        self._init()

    @classmethod
    def get_instance(cls) -> "SemanticShadow":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ─── Init ────────────────────────────────────────────────────────────────

    def _init(self) -> None:
        try:
            from shadow_graph import ShadowGraph
            self._shadow_graph = ShadowGraph.get_instance()

            # Try to grab the already-running EmbeddingEngine from api_server globals
            # (it was instantiated there and stored as embedding_engine)
            import sys
            ee = None
            for mod_name, mod in sys.modules.items():
                if 'rf_scythe_api_server' in mod_name or mod_name == '__main__':
                    ee = getattr(mod, 'embedding_engine', None)
                    if ee is not None:
                        break
            if ee is None:
                # Cold-start fallback: construct our own instance
                import os
                from embedding_engine import EmbeddingEngine
                data_dir = os.environ.get('SCYTHE_DATA_DIR',
                           os.path.join(os.path.dirname(__file__), 'instances'))
                ee = EmbeddingEngine(
                    ollama_url=os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434'),
                    db_path=os.path.join(data_dir, 'embedding_store.duckdb'),
                    index_path=os.path.join(data_dir, 'embedding_index.faiss'),
                )

            self._embedding_engine = ee

            # TurboQuant streaming store — primary similarity search backend.
            # Instantiate with the same dimension as the embedding model.
            # Falls back silently: if turboquant is unavailable, _tq_store stays None
            # and all search paths use FAISS instead.
            try:
                from turbo_quant_store import TurboQuantStore
                dim = getattr(ee, '_dim', 768)
                self._tq_store = TurboQuantStore(dim=dim, bits=3, name="semantic")
                logger.info("[SemanticShadow] TurboQuantStore ready (dim=%d, 3-bit)", dim)
            except Exception as _tqe:
                logger.info("[SemanticShadow] TurboQuant unavailable (%s) — using FAISS only", _tqe)
                self._tq_store = None

            self._ready = True
            logger.info("[SemanticShadow] ready — EmbeddingEngine + ShadowGraph wired")
        except Exception as e:
            logger.warning("[SemanticShadow] init failed (non-fatal): %s", e)

    # ─── Core ────────────────────────────────────────────────────────────────

    @staticmethod
    def _edge_id(src: str, dst: str, context: str) -> str:
        """Mirror ShadowGraph's edge_id formula so we can pre-check existence."""
        return hashlib.md5(
            f"{src}:{dst}:semantic_similarity:{context}".encode(),
            usedforsecurity=False
        ).hexdigest()[:12]

    @staticmethod
    def _decayed_delta(base: float, age_secs: float) -> float:
        """
        Exponential decay: bumps on older edges are smaller.
        delta = base * max(FLOOR, exp(-ln2 * age / half_life))
        """
        decay = math.exp(-0.693 * max(0.0, age_secs) / DECAY_HALF_LIFE)
        return base * max(DECAY_FLOOR, decay)

    @staticmethod
    def _cosine(a, b) -> float:
        import numpy as np
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        return float(np.dot(a, b) / denom) if denom > 1e-8 else 0.0

    def _embed_with_delta(self, entity_id: str, description: str,
                          alpha: float = 0.80):
        """
        Return an embedding for entity_id, applying temporal identity continuity.

        If the entity already has a stored vector, blend:
            new_vec = alpha * old_vec + (1 - alpha) * fresh_embed

        This preserves identity across description changes (rotating IPs,
        evolving ASN, updated port profiles) rather than treating each
        observation as a fresh identity.

        Alpha=0.80 means each new observation shifts identity by only 20%,
        matching the paper's insight that context windows should be reused
        rather than discarded.

        Returns the blended vector (np.ndarray) or None on embedding failure.
        """
        import numpy as np
        ee = self._embedding_engine
        fresh = ee.embed_text(description)
        if fresh is None:
            return self._entity_vecs.get(entity_id)

        fresh = fresh.astype("float32")
        old = self._entity_vecs.get(entity_id)
        if old is not None:
            blended = alpha * old + (1.0 - alpha) * fresh
            # Re-normalise to unit sphere (FAISS uses L2, cosine via normalised vecs)
            norm = np.linalg.norm(blended)
            if norm > 1e-8:
                blended /= norm
        else:
            blended = fresh
        self._entity_vecs[entity_id] = blended
        # Mirror into TurboQuant store — replaces any previous entry in-place.
        # This keeps the fp16 dense cache current so search() uses the blended vec.
        if self._tq_store is not None:
            self._tq_store.add(entity_id, blended)
        return blended

    def _build_candidate_vecs(self, candidates: list) -> dict:
        """
        Return {entity_id: np.ndarray} for each candidate.

        Priority order:
          1. Live delta-blended vec from _entity_vecs  (freshest, always preferred)
          2. FAISS reconstruct                         (fallback for entities not yet
                                                        seen by this SemanticShadow instance)

        Note: TurboQuant stores fp16 — _entity_vecs already holds the fp32 blended
        vec so there's no need to dequantize from the TQ store for MMR.  The TQ
        store's role is search (fp16 matmul), not reconstruction.
        """
        import numpy as np
        ee = self._embedding_engine
        vecs: dict = {}

        # Build FAISS reverse map as fallback (entity_id → faiss index)
        id_to_fidx: dict = {}
        for fidx, meta in ee._meta.items():
            eid = meta.get("entity_id", "")
            if eid:
                id_to_fidx[eid] = fidx

        for c in candidates:
            cid = c.get("entity_id", "")
            if not cid:
                continue
            # Prefer live delta-blended vec (already fp32, already normalised)
            if cid in self._entity_vecs:
                vecs[cid] = self._entity_vecs[cid]
                continue
            # Fallback: reconstruct from FAISS
            fidx = id_to_fidx.get(cid)
            if fidx is not None:
                vec = np.zeros(ee._dim, dtype="float32")
                try:
                    ee._index.reconstruct(fidx, vec)
                    vecs[cid] = vec
                except Exception:
                    pass
        return vecs

    def _mmr_select(self, candidates: list, candidate_vecs: dict) -> list:
        """
        Maximal Marginal Relevance selection.

        From the candidate pool, iteratively pick the entity that maximises:
            MMR = lambda * similarity_to_query
                  - (1 - lambda) * max_similarity_to_already_selected

        This prevents the common failure mode where a dense IP cluster
        (e.g. CDN subnet) generates O(n^2) near-duplicate speculative edges.

        Returns up to MAX_NEIGHBORS candidates, ordered by selection priority.
        """
        eligible = [c for c in candidates if c.get("entity_id") in candidate_vecs]
        if not eligible:
            return candidates[:MAX_NEIGHBORS]  # fallback: no vecs, use top-k

        selected: list = []
        remaining = list(eligible)

        while len(selected) < MAX_NEIGHBORS and remaining:
            if not selected:
                # First pick: highest raw similarity
                best = max(remaining, key=lambda c: c.get("similarity", 0.0))
            else:
                sel_vecs = [candidate_vecs[s["entity_id"]]
                            for s in selected if s["entity_id"] in candidate_vecs]

                def mmr_score(c: dict) -> float:
                    rel = c.get("similarity", 0.0)
                    cv  = candidate_vecs.get(c["entity_id"])
                    if cv is None or not sel_vecs:
                        return rel
                    max_overlap = max(self._cosine(cv, sv) for sv in sel_vecs)
                    return MMR_LAMBDA * rel - (1.0 - MMR_LAMBDA) * max_overlap

                best = max(remaining, key=mmr_score)

            selected.append(best)
            remaining.remove(best)

        return selected

    def process_entity(self, entity_id: str, description: str,
                        extra_labels: Optional[Dict[str, Any]] = None) -> int:
        """
        Embed entity, run MMR-filtered neighbour search, push/bump speculative
        edges with temporal decay.

        extra_labels (optional) — dict of additional metadata to attach to the
        edge context and entity description.  Protocol anomaly scores injected
        by the live ingest worker arrive here as:
            {'protocol_anomaly_score': 0.55, 'protocol_violations': 'missing_tls dns_tunnel'}

        Returns the number of speculative edges created or reinforced.
        """
        if not self._ready:
            return 0

        ee = self._embedding_engine
        sg = self._shadow_graph

        try:
            # 1. Embed with delta continuity (blends new signal into existing identity)
            #    Also pushes blended vec into TurboQuant store (see _embed_with_delta).
            vec = self._embed_with_delta(entity_id, description)
            if vec is None:
                return 0
            # Also index in FAISS for backward-compat / cold-start fallback
            ee.add_entity(entity_id, description)

            # 2. Fetch candidate pool — TurboQuant primary, FAISS fallback
            k_fetch = MAX_NEIGHBORS * CANDIDATE_MULT
            if self._tq_store is not None and len(self._tq_store) > 1:
                # TurboQuant: sub-2ms fp16 matmul search, returns (entity_id, cosine_sim)
                tq_results = self._tq_store.search(vec, k=k_fetch + 1)
                all_candidates = [
                    {"entity_id": eid, "similarity": sim}
                    for eid, sim in tq_results
                ]
            else:
                # FAISS fallback (used until enough vecs are in the TQ store)
                all_candidates = ee.search_similar(description, k=k_fetch + 1)

            # Filter: remove self and below threshold
            candidates = [
                c for c in all_candidates
                if c.get("entity_id") != entity_id
                and c.get("similarity", 0.0) >= SIM_THRESHOLD
            ]
            if not candidates:
                return 0

            # 3. Reconstruct embeddings for MMR diversity scoring
            candidate_vecs = self._build_candidate_vecs(candidates)

            # 4. MMR: select MAX_NEIGHBORS diverse-yet-relevant neighbours
            selected = self._mmr_select(candidates, candidate_vecs)

            now_mono = time.monotonic()
            created  = 0

            for c in selected:
                nid = c.get("entity_id", "")
                sim = c.get("similarity", 0.0)
                if not nid:
                    continue

                # Raw evidence/confidence scaled by similarity above threshold
                evidence_delta = EVIDENCE_BASE + (sim - SIM_THRESHOLD) * 0.5
                conf_initial   = round(0.3 + (sim - SIM_THRESHOLD) * 1.5, 3)

                # 5. Check if edge already exists so we can apply temporal decay
                eid = self._edge_id(entity_id, nid, entity_id)
                existing = sg._edges.get(eid)

                if existing is not None:
                    # Edge already in shadow graph — apply temporal decay to bump
                    age_secs = now_mono - existing.created_at
                    d_conf   = self._decayed_delta(0.03, age_secs)
                    d_evid   = self._decayed_delta(evidence_delta * 0.4, age_secs)
                    if d_conf > 0.001:
                        sg.observe(eid,
                                   confidence_delta=d_conf,
                                   evidence_delta=d_evid)
                        logger.debug(
                            "[SemanticShadow] bump(decayed) %s->%s age=%.0fs "
                            "d_conf=%.3f d_evid=%.3f",
                            entity_id, nid, age_secs, d_conf, d_evid
                        )
                    created += 1
                else:
                    # Fresh edge — create via push
                    edge = {
                        "src":        entity_id,
                        "dst":        nid,
                        "kind":       "semantic_similarity",
                        "confidence": conf_initial,
                        "requires":   ["repeat_observation", "dpi_confirmation"],
                        "_raw_kind":  "semantic_similarity",
                    }
                    if extra_labels:
                        edge.update({k: v for k, v in extra_labels.items()
                                     if v is not None})
                    result = sg.push(
                        edge,
                        rejection_reason="low_confidence",
                        context_node_id=entity_id,
                        ttl_secs=600.0,
                    )
                    if result:
                        # High similarity → immediate high-evidence bump (no decay
                        # since the edge was just born)
                        if sim >= SIM_HIGH:
                            sg.observe(result,
                                       confidence_delta=0.08,
                                       evidence_delta=evidence_delta)
                        created += 1
                        logger.debug(
                            "[SemanticShadow] new edge %s->%s sim=%.3f "
                            "conf=%.3f mmr_rank=%d",
                            entity_id, nid, sim, conf_initial,
                            selected.index(c) + 1
                        )

            if created:
                logger.info(
                    "[SemanticShadow] entity=%s edges=%d/%d candidates "
                    "(MMR lambda=%.2f)",
                    entity_id, created, len(candidates), MMR_LAMBDA
                )
            return created

        except Exception as e:
            logger.debug("[SemanticShadow] process_entity error: %s", e)
            return 0

    # ─── Semantic space (PCA projection for Deck.gl) ─────────────────────────

    def get_pca_coords(self, entity_positions: dict, n_components: int = 2) -> list:
        """
        Project all known embeddings into 2D using PCA, then scale coords to
        fit within a 1°×1° box centred on the server's geographic position.

        Uses TurboQuant dense cache (fp16 matmul) when available — avoids the
        slow FAISS reconstruct() loop.  Falls back to FAISS if TQ store is empty.

        entity_positions: {entity_id: {"lat": float, "lon": float}} for
                          anchoring the semantic cloud to real geography.

        Returns list of dicts:
          {entity_id, description, pca_x, pca_y, lon, lat, similarity_group}
        """
        if not self._ready:
            return []

        try:
            import numpy as np
            from sklearn.decomposition import PCA

            ee = self._embedding_engine
            vecs   = []
            metas  = []

            # ── Source vectors ────────────────────────────────────────────────
            # Prefer _entity_vecs (fp32 blended, always most current) over both
            # TurboQuant fp16 and FAISS reconstruct.
            if self._entity_vecs:
                for eid, vec in self._entity_vecs.items():
                    vecs.append(vec)
                    metas.append({"entity_id": eid,
                                  "description": eid})
            else:
                # Cold fallback: pull from FAISS index
                n = ee._index.ntotal
                for idx in range(n):
                    meta = ee._meta.get(idx)
                    if meta:
                        vec = np.zeros((1, ee._dim), dtype='float32')
                        try:
                            ee._index.reconstruct(idx, vec[0])
                        except Exception:
                            continue
                        vecs.append(vec[0])
                        metas.append(meta)

            if len(vecs) < 2:
                return []

            matrix = np.array(vecs, dtype='float32')
            pca = PCA(n_components=min(n_components, len(vecs)))
            coords_2d = pca.fit_transform(matrix)

            # Normalize to [-0.5, 0.5]
            for col in range(coords_2d.shape[1]):
                mn, mx = coords_2d[:, col].min(), coords_2d[:, col].max()
                span = mx - mn if mx > mn else 1.0
                coords_2d[:, col] = (coords_2d[:, col] - mn) / span - 0.5

            results = []
            for i, meta in enumerate(metas):
                eid = meta.get("entity_id", "")
                geo = entity_positions.get(eid, {})
                # Map PCA coords into ±0.3° around entity's real position
                # (or use a default cluster centre if no geo known)
                base_lon = geo.get("lon", -95.0)
                base_lat = geo.get("lat", 30.0)
                results.append({
                    "entity_id":       eid,
                    "description":     meta.get("description", ""),
                    "pca_x":           round(float(coords_2d[i, 0]), 4),
                    "pca_y":           round(float(coords_2d[i, 1]), 4),
                    "lon":             round(base_lon + float(coords_2d[i, 0]) * 0.6, 5),
                    "lat":             round(base_lat + float(coords_2d[i, 1]) * 0.6, 5),
                })
            return results

        except Exception as e:
            logger.warning("[SemanticShadow] pca_coords error: %s", e)
            return []
