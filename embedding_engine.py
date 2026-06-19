"""
embedding_engine.py — Semantic memory layer for RF Scythe / GraphOps

Auto-detects the best local embedding model via Ollama:
  embeddinggemma → 768 dims (preferred, if pulled)
  llama3.2:3b    → 3072 dims (fallback, always available)

Stores normalized vectors in FAISS for cosine similarity, metadata in DuckDB.

Public API:
  EmbeddingEngine.add_entity(entity_id, description)
  EmbeddingEngine.embed_text(text) → np.ndarray
  EmbeddingEngine.search_similar(query_text, k=5) → list[dict]
  EmbeddingEngine.detect_anomaly(embedding, threshold=0.85) → dict | None
  EmbeddingEngine.resolve_entity(embedding, threshold=0.92) → str | None
  EmbeddingEngine.save_index() / load_index()
  EmbeddingEngine.build_entity_description(event) → str
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional

import duckdb
import faiss
import numpy as np

logger = logging.getLogger(__name__)

# ─── embedding model priority ─────────────────────────────────────────────────
# Tier 1 — Reflex Cognition (384)
# Tier 2 — Analytical Cognition (768-1024)
# Tier 3 — Strategic Cognition (2048+)

_EMBED_MODEL_PRIORITY = [
    # Tier 1 (Fast/Edge/Reflex)
    ("granite-embedding:278m", 384),   # IBM Granite small
    ("nomic-embed-text-v1.5", 768),

    # Tier 2 (Analytical)
    ("embeddinggemma", 768),
    ("gemma3:270m",    1152),

    # Tier 3 (Strategic/Large)
    ("gemma3:1b",      2048),
    ("llama3.2:3b",    3072),
]

class CognitiveTier:
    REFLEX = "reflex"           # 384 (L1 Cache)
    ANALYTICAL = "analytical"   # 768-1024 (L2 Cache)
    STRATEGIC = "strategic"     # 2048+ (Deep Memory)

def _detect_embed_model(ollama_url: str, tier: str = CognitiveTier.ANALYTICAL) -> tuple[str, int]:
    """Return (model_name, embedding_dim) for the best available model in the requested tier."""
    # Mapping tier to target dimensions
    target_dim = 768
    if tier == CognitiveTier.REFLEX: target_dim = 384
    elif tier == CognitiveTier.STRATEGIC: target_dim = 2048

    try:
        with urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=3) as r:
            data = json.loads(r.read())
        available = {m["name"] for m in data.get("models", [])}

        # Filter priority list by tier-appropriate models
        for model, dim in _EMBED_MODEL_PRIORITY:
            if tier == CognitiveTier.REFLEX and dim > 768: continue
            if tier == CognitiveTier.STRATEGIC and dim < 1024: continue

            if model in available or True: # Force probe if registry check is unreliable
                emb = _raw_embed(ollama_url, model, "test")
                if emb is not None and len(emb) > 0:
                    logger.info("[EmbeddingEngine] using %s (dim=%d) for %s tier",
                                model, len(emb), tier)
                    return model, len(emb)
    except Exception as exc:
        logger.debug("[EmbeddingEngine] model detection failed: %s", exc)

    # Tier-specific fallbacks
    if tier == CognitiveTier.REFLEX: return "granite-embedding:278m", 384
    return "llama3.2:3b", 3072


def _raw_embed(ollama_url: str, model: str, text: str) -> Optional[List[float]]:
    """Call Ollama /api/embeddings and return the raw float list."""
    try:
        payload = json.dumps({"model": model, "prompt": text}).encode()
        req = urllib.request.Request(
            f"{ollama_url}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return data.get("embedding") or None
    except Exception as exc:
        logger.debug("[EmbeddingEngine] raw embed error: %s", exc)
        return None


# ─── EmbeddingEngine ─────────────────────────────────────────────────────────

class EmbeddingEngine:
    """Semantic memory: embed → FAISS cosine search → DuckDB persistence."""

    INDEX_PATH = "/home/spectrcyde/NerfEngine/embedding_index.faiss"
    DB_PATH    = "/home/spectrcyde/NerfEngine/embedding_store.duckdb"

    def __init__(self, ollama_url: str = "http://localhost:11434",
                 db_path: Optional[str] = None,
                 index_path: Optional[str] = None,
                 instance_db: Optional[Any] = None,
                 tier: str = CognitiveTier.ANALYTICAL):
        self._ollama = ollama_url
        self._instance_db = instance_db
        self._tier = tier

        # Allow instance-scoped paths so multiple server instances don't
        # contend for the same DuckDB file lock.
        self.DB_PATH    = db_path    or self.__class__.DB_PATH
        self.INDEX_PATH = index_path or self.__class__.INDEX_PATH
        self._model, self._dim = _detect_embed_model(ollama_url, tier=tier)

        # FAISS index — L2 on normalized vectors ≡ cosine similarity
        self._index = faiss.IndexFlatL2(self._dim)

        # int → metadata dict (volatile; re-populated on load_index)
        self._meta: Dict[int, Dict[str, Any]] = {}

        # DuckDB for cold persistence — fall back to a PID-scoped path if the
        # primary file is locked by another instance.
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.DB_PATH)), exist_ok=True)
            self._db = duckdb.connect(self.DB_PATH)
        except Exception as _lock_err:
            fallback = self.DB_PATH + f".{os.getpid()}.tmp"
            logger.warning(
                "[EmbeddingEngine] %s locked (%s) — using PID-scoped fallback: %s",
                self.DB_PATH, _lock_err, fallback,
            )
            self.DB_PATH = fallback
            self._db = duckdb.connect(fallback)
        self._init_db()

        # Attempt to restore a previously saved index
        self.load_index(silent=True)

        logger.info(
            "[EmbeddingEngine] ready — model=%s dim=%d vectors=%d",
            self._model, self._dim, self._index.ntotal,
        )

    # ── DB schema ─────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                vec_idx    INTEGER PRIMARY KEY,
                entity_id  TEXT,
                description TEXT,
                model      TEXT,
                dim        INTEGER,
                vector_json TEXT,
                created_at DOUBLE
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS entity_index (
                entity_id  TEXT,
                vec_idx    INTEGER,
                created_at DOUBLE,
                PRIMARY KEY (entity_id, vec_idx)
            )
        """)

    # ── description builder ───────────────────────────────────────────────────

    @staticmethod
    def build_entity_description(event: Dict[str, Any]) -> str:
        """Convert a raw event/entity dict into a rich text description for embedding."""
        parts: List[str] = ["Entity observed with the following signals:"]

        if rtt := event.get("rtt_ms"):
            parts.append(f"- RTT avg: {rtt} ms")
        if dist := event.get("distance_km"):
            parts.append(f"- Distance estimate: {dist:.0f} km")

        if ports := event.get("ports"):
            parts.append(f"- Open ports: {', '.join(str(p) for p in ports)}")
        if proto := event.get("protocols"):
            parts.append(f"- Protocols: {', '.join(proto) if isinstance(proto, list) else proto}")

        if beh := event.get("behavior"):
            parts.append(f"- Behavior: {beh}")
        if bsg := event.get("behavior_group"):
            parts.append(f"- Behavior group: {bsg}")

        if freq := event.get("freq"):
            power = event.get("power", "unknown")
            parts.append(f"- RF: {freq} MHz @ {power} dBm")
        if ssid := event.get("ssid"):
            rssi  = event.get("rssi", "?")
            parts.append(f"- WiFi AP: SSID={ssid}, RSSI={rssi} dBm")

        if geo := event.get("geo") or event.get("geo_hint"):
            parts.append(f"- Geo hint: {geo}")
        if lat := event.get("lat"):
            lon = event.get("lon", "?")
            parts.append(f"- Coordinates: {lat}, {lon}")

        if entity_type := event.get("entity_type") or event.get("icon"):
            parts.append(f"- Entity type: {entity_type}")
        if eid := event.get("entity_id"):
            parts.append(f"- Entity ID: {eid}")

        if extra := event.get("labels"):
            for k, v in (extra.items() if isinstance(extra, dict) else []):
                parts.append(f"- {k}: {v}")

        # Fallback: raw summary field
        if summary := event.get("summary") or event.get("description"):
            parts.append(f"- Summary: {summary}")

        return "\n".join(parts) if len(parts) > 1 else f"Entity: {json.dumps(event)[:500]}"

    # ── embedding ─────────────────────────────────────────────────────────────

    def embed_text(self, text: str) -> Optional[np.ndarray]:
        """Embed text → normalized unit vector (float32). Returns None on failure."""
        raw = _raw_embed(self._ollama, self._model, text)
        if raw is None or len(raw) == 0:
            logger.warning("[EmbeddingEngine] embed_text returned empty vector")
            return None
        vec = np.array(raw, dtype=np.float32)
        # L2-normalize for cosine similarity via IndexFlatL2
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    # ── add entity ────────────────────────────────────────────────────────────

    def add_entity(self, entity_id: str, description: str, mirror_to_pg: bool = True) -> Optional[int]:
        """Embed description, add to FAISS (HOT), and optionally InstanceDB (COLD)."""
        vec = self.embed_text(description)
        if vec is None:
            return None

        # 1. Add to HOT memory (FAISS)
        vec_idx = self._index.ntotal
        self._index.add(np.array([vec]))

        meta = {
            "entity_id":   entity_id,
            "description": description,
            "model":       self._model,
            "dim":         self._dim,
            "created_at":  time.time(),
        }
        self._meta[vec_idx] = meta

        # 2. Add to COLD memory (InstanceDB / pgvector)
        if mirror_to_pg and self._instance_db:
            try:
                self._instance_db.upsert_mac_cluster(
                    cluster_id=entity_id,
                    embedding=vec.tolist(),
                    metadata=meta
                )
            except Exception as e:
                logger.warning("[EmbeddingEngine] Mirror to pgvector failed: %s", e)

        # 3. DuckDB persistence (Backup)
        self._db.execute(
            "INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?,?,?,?)",
            (
                vec_idx, entity_id, description, self._model, self._dim,
                json.dumps(vec.tolist()), meta["created_at"],
            ),
        )
        self._db.execute(
            "INSERT OR REPLACE INTO entity_index VALUES (?,?,?)",
            (entity_id, vec_idx, meta["created_at"]),
        )

        logger.debug("[EmbeddingEngine] added entity=%s idx=%d total=%d",
                     entity_id, vec_idx, self._index.ntotal)
        return vec_idx

    # ── similarity search ─────────────────────────────────────────────────────

    def search_similar(self, query_text: str, k: int = 5, search_cold: bool = True) -> List[Dict[str, Any]]:
        """Search similar entities in HOT (FAISS) and optionally COLD (pgvector)."""
        vec = self.embed_text(query_text)
        if vec is None:
            return []

        # 1. Search HOT memory (FAISS)
        hot_results = []
        if self._index.ntotal > 0:
            distances, indices = self._index.search(np.array([vec]), min(k, self._index.ntotal))
            for dist, idx in zip(distances[0], indices[0]):
                if idx < 0: continue
                # L2 distance on normalized vectors → cosine similarity = 1 - dist/2
                cosine_sim = float(1.0 - dist / 2.0)
                entry = dict(self._meta.get(int(idx), {}))
                entry["similarity"] = round(cosine_sim, 4)
                entry["vec_idx"] = int(idx)
                entry["tier"] = "HOT"
                hot_results.append(entry)

        # 2. Search COLD memory (InstanceDB / pgvector)
        cold_results = []
        if search_cold and self._instance_db:
            try:
                pg_matches = self._instance_db.search_similar_clusters(
                    query_embedding=vec.tolist(),
                    threshold=0.7,
                    limit=k
                )
                for m in pg_matches:
                    cold_results.append({
                        "entity_id": m["cluster_id"],
                        "description": m.get("metadata", {}).get("description", ""),
                        "similarity": round(m["similarity"], 4),
                        "tier": "COLD",
                        "metadata": m.get("metadata", {})
                    })
            except Exception as e:
                logger.warning("[EmbeddingEngine] COLD search failed: %s", e)

        # Merge and deduplicate
        all_results = hot_results + cold_results
        all_results.sort(key=lambda x: x["similarity"], reverse=True)

        # Deduplicate by entity_id
        seen = set()
        unique = []
        for r in all_results:
            eid = r.get("entity_id")
            if eid not in seen:
                unique.append(r)
                seen.add(eid)

        return unique[:k]

    # ── anomaly detection ─────────────────────────────────────────────────────

    def detect_anomaly(
        self, embedding: np.ndarray, threshold: float = 0.85
    ) -> Optional[Dict[str, Any]]:
        """Return anomaly dict if embedding is unusually similar to existing cluster.

        High mean similarity → pattern match (known behavior).
        Low mean similarity  → novel / anomalous entity.
        """
        if self._index.ntotal < 5:
            return None
        k = min(10, self._index.ntotal)
        distances, _ = self._index.search(np.array([embedding]), k)
        sims = [float(1.0 - d / 2.0) for d in distances[0] if d >= 0]
        if not sims:
            return None
        mean_sim = float(np.mean(sims))
        if mean_sim > threshold:
            return {
                "type":        "pattern_match",
                "confidence":  round(mean_sim, 4),
                "neighbors":   len(sims),
                "mean_sim":    round(mean_sim, 4),
                "max_sim":     round(max(sims), 4),
            }
        if mean_sim < (1.0 - threshold):  # novel
            return {
                "type":       "novel_pattern",
                "confidence": round(1.0 - mean_sim, 4),
                "neighbors":  len(sims),
                "mean_sim":   round(mean_sim, 4),
            }
        return None

    # ── identity stitching ────────────────────────────────────────────────────

    def resolve_entity(
        self, embedding: np.ndarray, threshold: float = 0.92
    ) -> Optional[str]:
        """If an existing entity has cosine similarity ≥ threshold, return its entity_id."""
        if self._index.ntotal == 0:
            return None
        k = min(5, self._index.ntotal)
        distances, indices = self._index.search(np.array([embedding]), k)
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0:
                continue
            sim = float(1.0 - dist / 2.0)
            if sim >= threshold:
                return self._meta.get(int(idx), {}).get("entity_id")
        return None

    # ── cluster summary ───────────────────────────────────────────────────────

    def get_semantic_clusters(self, n_clusters: int = 5) -> List[Dict[str, Any]]:
        """Simple k-means cluster summary over all stored vectors.

        Returns a list of cluster dicts with centroid members and representative entities.
        Falls back to listing recent entities if sklearn unavailable.
        """
        total = self._index.ntotal
        if total < n_clusters:
            # Not enough data — return flat list
            return [
                {
                    "cluster_id":    i,
                    "entity_id":     self._meta.get(i, {}).get("entity_id", f"vec_{i}"),
                    "description":   self._meta.get(i, {}).get("description", "")[:200],
                }
                for i in range(total)
            ]

        try:
            from sklearn.cluster import KMeans

            # Reconstruct all vectors from FAISS (IndexFlatL2 supports reconstruct)
            all_vecs = np.zeros((total, self._dim), dtype=np.float32)
            for i in range(total):
                all_vecs[i] = self._index.reconstruct(i)

            km = KMeans(n_clusters=min(n_clusters, total), n_init=10, random_state=42)
            labels = km.fit_predict(all_vecs)

            clusters: Dict[int, List[int]] = {}
            for idx, label in enumerate(labels):
                clusters.setdefault(int(label), []).append(idx)

            result = []
            for cluster_id, members in sorted(clusters.items()):
                rep_idx  = members[0]
                entities = [self._meta.get(m, {}).get("entity_id", f"vec_{m}") for m in members[:5]]
                result.append({
                    "cluster_id":   cluster_id,
                    "size":         len(members),
                    "entities":     entities,
                    "representative": self._meta.get(rep_idx, {}).get("entity_id"),
                    "description":  self._meta.get(rep_idx, {}).get("description", "")[:200],
                })
            return result

        except ImportError:
            logger.debug("[EmbeddingEngine] sklearn not available — using flat list")
            return [
                {
                    "cluster_id":  i,
                    "entity_id":   self._meta.get(i, {}).get("entity_id", f"vec_{i}"),
                    "description": self._meta.get(i, {}).get("description", "")[:200],
                }
                for i in range(min(total, 20))
            ]

    # ── persistence ───────────────────────────────────────────────────────────

    def save_index(self) -> None:
        """Write FAISS index to disk."""
        try:
            faiss.write_index(self._index, self.INDEX_PATH)
            logger.info("[EmbeddingEngine] index saved (%d vectors)", self._index.ntotal)
        except Exception as exc:
            logger.warning("[EmbeddingEngine] save_index failed: %s", exc)

    def load_index(self, silent: bool = False) -> bool:
        """Restore FAISS index from disk + rebuild metadata from DuckDB.

        If the saved index has a different dimension than the active model
        (e.g. switching from llama3.2:3b/3072 to embeddinggemma/768),
        the stale index is discarded and a fresh one is started.
        """
        import os
        if not os.path.exists(self.INDEX_PATH):
            return False
        try:
            loaded = faiss.read_index(self.INDEX_PATH)

            # Dimension mismatch → model changed; start fresh
            if loaded.d != self._dim:
                logger.warning(
                    "[EmbeddingEngine] saved index dim=%d != current model dim=%d "
                    "(model changed to %s) — discarding stale index",
                    loaded.d, self._dim, self._model,
                )
                os.remove(self.INDEX_PATH)
                self._db.execute("DELETE FROM embeddings")
                self._db.execute("DELETE FROM entity_index")
                self._meta = {}
                self._index = faiss.IndexFlatL2(self._dim)
                return False

            self._index = loaded

            # Rebuild metadata dict from DuckDB (only rows matching current model dim)
            rows = self._db.execute(
                "SELECT vec_idx, entity_id, description, model, dim, created_at "
                "FROM embeddings WHERE dim = ?", [self._dim]
            ).fetchall()
            self._meta = {}
            for vec_idx, entity_id, description, model, dim, created_at in rows:
                self._meta[int(vec_idx)] = {
                    "entity_id":   entity_id,
                    "description": description,
                    "model":       model,
                    "dim":         dim,
                    "created_at":  created_at,
                }

            if not silent:
                logger.info("[EmbeddingEngine] loaded %d vectors from disk (model=%s dim=%d)",
                            self._index.ntotal, self._model, self._dim)
            return True
        except Exception as exc:
            if not silent:
                logger.warning("[EmbeddingEngine] load_index failed: %s", exc)
            return False

    # ── stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "model":        self._model,
            "dim":          self._dim,
            "total_vectors": self._index.ntotal,
            "db_path":      self.DB_PATH,
            "index_path":   self.INDEX_PATH,
        }


# ─── MCP tool registration ────────────────────────────────────────────────────

def register_embedding_tools(engine, mcp_handler, embedding_engine: EmbeddingEngine) -> None:
    """Register 5 semantic memory MCP tools into an MCPHandler instance."""
    from mcp_server import ToolDef

    # ── embed_entity ──────────────────────────────────────────────────────────
    def _embed_entity(params: dict) -> dict:
        entity_id   = params.get("entity_id", "")
        description = params.get("description", "")
        event_json  = params.get("event", {})

        if not entity_id:
            return {"error": "entity_id is required"}

        if not description and not event_json:
            # Try to pull from hypergraph
            if engine:
                try:
                    node = engine.get_node(entity_id)
                    if node:
                        description = EmbeddingEngine.build_entity_description(
                            {**node.get("labels", {}), "entity_id": entity_id}
                        )
                except Exception:
                    pass

        if not description and event_json:
            description = EmbeddingEngine.build_entity_description(
                {**event_json, "entity_id": entity_id}
            )

        if not description:
            return {"error": "description or event required (or entity_id must exist in hypergraph)"}

        vec_idx = embedding_engine.add_entity(entity_id, description)
        if vec_idx is None:
            return {"error": "embedding failed — Ollama may be unavailable"}

        embedding_engine.save_index()
        return {
            "entity_id":     entity_id,
            "vec_idx":       vec_idx,
            "total_vectors": embedding_engine.stats()["total_vectors"],
            "model":         embedding_engine._model,
        }

    # ── search_similar_entities ───────────────────────────────────────────────
    def _search_similar(params: dict) -> dict:
        query = params.get("query", "")
        k     = int(params.get("k", 5))
        if not query:
            return {"error": "query is required"}
        results = embedding_engine.search_similar(query, k=k)
        return {
            "query":   query,
            "results": results,
            "count":   len(results),
        }

    # ── detect_anomaly_pattern ────────────────────────────────────────────────
    def _detect_anomaly(params: dict) -> dict:
        description = params.get("description", "")
        threshold   = float(params.get("threshold", 0.85))
        if not description:
            return {"error": "description is required"}
        vec = embedding_engine.embed_text(description)
        if vec is None:
            return {"error": "embedding failed"}
        anomaly = embedding_engine.detect_anomaly(vec, threshold=threshold)
        resolved = embedding_engine.resolve_entity(vec, threshold=0.92)
        return {
            "anomaly":      anomaly,
            "resolved_to":  resolved,
            "description":  description[:200],
            "vector_store": embedding_engine.stats()["total_vectors"],
        }

    # ── stitch_identities ─────────────────────────────────────────────────────
    def _stitch_identities(params: dict) -> dict:
        description = params.get("description", "")
        threshold   = float(params.get("threshold", 0.88))
        if not description:
            return {"error": "description is required"}
        results = embedding_engine.search_similar(description, k=10)
        stitched = [r for r in results if r["similarity"] >= threshold]
        return {
            "query_description": description[:200],
            "threshold":   threshold,
            "stitched":    stitched,
            "count":       len(stitched),
        }

    # ── get_semantic_clusters ─────────────────────────────────────────────────
    def _get_clusters(params: dict) -> dict:
        n = int(params.get("n_clusters", 5))
        clusters = embedding_engine.get_semantic_clusters(n_clusters=n)
        return {
            "clusters":      clusters,
            "count":         len(clusters),
            "total_vectors": embedding_engine.stats()["total_vectors"],
        }

    # ── register all ─────────────────────────────────────────────────────────
    mcp_handler._tools["embed_entity"] = ToolDef(
        name="embed_entity",
        description=(
            "Embed a recon entity or hypergraph node into semantic memory. "
            "Provide entity_id + description (or event dict). "
            "Enables similarity search, anomaly detection, and identity stitching."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "entity_id":   {"type": "string",  "description": "Unique entity/node ID"},
                "description": {"type": "string",  "description": "Rich text description to embed"},
                "event":       {"type": "object",  "description": "Raw event dict (used if description omitted)"},
            },
            "required": ["entity_id"],
        },
        fn=_embed_entity,
    )

    mcp_handler._tools["search_similar_entities"] = ToolDef(
        name="search_similar_entities",
        description=(
            "Natural-language query → top-k semantically similar entities from the "
            "embedding memory. Returns entity IDs, descriptions, and cosine similarity scores."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string",  "description": "Natural-language search query"},
                "k":     {"type": "integer", "description": "Max results to return (default 5)", "default": 5},
            },
            "required": ["query"],
        },
        fn=_search_similar,
    )

    mcp_handler._tools["detect_anomaly_pattern"] = ToolDef(
        name="detect_anomaly_pattern",
        description=(
            "Check if a behavioral description matches a known cluster (pattern_match) "
            "or is novel (novel_pattern). Also attempts identity resolution. "
            "Threshold controls match sensitivity (default 0.85)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Rich text description of the behavior"},
                "threshold":   {"type": "number", "description": "Cosine similarity threshold (default 0.85)", "default": 0.85},
            },
            "required": ["description"],
        },
        fn=_detect_anomaly,
    )

    mcp_handler._tools["stitch_identities"] = ToolDef(
        name="stitch_identities",
        description=(
            "Find all stored entities whose semantic embedding is similar to a given "
            "description (threshold ≥ 0.88 by default). Used to detect the same actor "
            "appearing across sessions, IPs, or RF signatures."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Description of the entity to match"},
                "threshold":   {"type": "number", "description": "Cosine similarity threshold (default 0.88)", "default": 0.88},
            },
            "required": ["description"],
        },
        fn=_stitch_identities,
    )

    mcp_handler._tools["get_semantic_clusters"] = ToolDef(
        name="get_semantic_clusters",
        description=(
            "Cluster all stored entity embeddings into semantic groups using k-means. "
            "Returns cluster summaries with representative entity descriptions. "
            "Useful for identifying behavioral families and swarm patterns."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "n_clusters": {"type": "integer", "description": "Number of clusters (default 5)", "default": 5},
            },
        },
        fn=_get_clusters,
    )

    logger.info("[embedding_engine] registered 5 MCP tools: "
                "embed_entity, search_similar_entities, detect_anomaly_pattern, "
                "stitch_identities, get_semantic_clusters")
