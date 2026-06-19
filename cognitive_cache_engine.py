"""
cognitive_cache_engine.py — Semantic RF Memory & Actor Continuity Engine

Weaponizing LLM KV Cache techniques for real-world entity tracking.
Implementation of "Cognitive Cache Engineering" (docs/KV_Cache.md).

Key features:
1. Multi-Tier Semantic Memory (HOT/WARM/COLD).
2. Semantic Eviction based on attention-aware scoring.
3. Low-Rank Actor Compression for trajectories.
4. Persistent World-Model Consolidation.
"""

from __future__ import annotations

import logging
import time
import math
import threading
from collections import deque, Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from mac_cluster_engine import MacCluster, exp_decay, haversine

logger = logging.getLogger(__name__)

# ── Multi-Tier Thresholds ───────────────────────────────────────────────────
HOT_TTL_S = 300.0        # 5 minutes for active hot cache
WARM_TTL_S = 3600.0      # 1 hour for warm recent history
COLD_RETENTION_S = 86400.0 # 24 hours for cold archival (before DB consolidation)

# ── Semantic Eviction Weights ───────────────────────────────────────────────
RETENTION_WEIGHTS = {
    "confidence": 0.35,
    "novelty": 0.15,
    "recurrence": 0.20,
    "threat": 0.20,
    "motion_consistency": 0.10,
}

@dataclass
class CompressedTrajectory:
    """Low-rank representation of an actor's motion history."""
    basis_vector: str           # e.g., "vehicular-westbound", "stationary-periodic"
    start_ts: float
    end_ts: float
    center_lat: float
    center_lon: float
    velocity_mps: float
    heading_deg: float
    drift_tensor: List[float]   # Residual errors or spline coefficients
    confidence: float

class SemanticEvictor:
    """Attention-aware eviction logic for RF observations."""

    @staticmethod
    def compute_retention_score(cluster: MacCluster) -> float:
        """
        retention_score = confidence * novelty * recurrence * threat_weight * motion_consistency
        Analogous to attention-aware KV eviction.
        """
        # Confidence from the cluster engine
        conf = cluster.confidence()

        # Novelty: inverse of duration (new active things are interesting)
        # But recurrence also matters.
        times = [obs.get("timestamp", 0) for obs in cluster.observations]
        duration = max(times) - min(times) if len(times) > 1 else 0
        novelty = 1.0 / (1.0 + math.log1p(duration / 60.0))

        # Recurrence: how many observations do we have?
        recurrence = min(1.0, len(cluster.observations) / 50.0)

        # Threat Weight: derived from behavior or specific signatures
        threat_weight = 0.5
        if cluster.randomized_count > 0:
            threat_weight += 0.2
        if "mobile" in str(cluster.centroid().get("device_class", "")).lower():
            threat_weight += 0.1

        # Motion Consistency
        motion_consistency = cluster.stability_score()

        score = (
            RETENTION_WEIGHTS["confidence"] * conf +
            RETENTION_WEIGHTS["novelty"] * novelty +
            RETENTION_WEIGHTS["recurrence"] * recurrence +
            RETENTION_WEIGHTS["threat"] * threat_weight +
            RETENTION_WEIGHTS["motion_consistency"] * motion_consistency
        )

        return round(max(0.0, min(1.0, score)), 4)

class TrajectoryCompressor:
    """LoRA for physical actor trajectories.
    Compresses 4000 observations into a few motion primitives.
    """

    @staticmethod
    def classify_motion_basis(observations: List[Dict[str, Any]]) -> str:
        if len(observations) < 3:
            return "unknown"

        dist_m = haversine(observations[0], observations[-1])
        duration_s = max(obs.get("timestamp", 0) for obs in observations) - min(obs.get("timestamp", 0) for obs in observations)
        velocity = dist_m / max(1.0, duration_s)

        # Check for circularity or linear motion
        # (Simplified heuristic)
        if velocity < 0.2:
            return "stationary"

        lats = [o.get("lat", 0) for o in observations]
        lons = [o.get("lon", 0) for o in observations]

        # Linear correlation as a proxy for straight-line motion
        try:
            corr = np.corrcoef(lats, lons)[0, 1]
            if abs(corr) > 0.95:
                return "linear-transit"
        except:
            pass

        if velocity > 12.0:
            return "vehicular-high-speed"
        elif velocity > 2.0:
            return "vehicular-low-speed"
        else:
            return "pedestrian"

    @staticmethod
    def compress(observations: List[Dict[str, Any]]) -> Optional[CompressedTrajectory]:
        if len(observations) < 5:
            return None

        lats = [o.get("lat", 0) for o in observations]
        lons = [o.get("lon", 0) for o in observations]
        ts = [o.get("timestamp", 0) for o in observations]

        center_lat = np.mean(lats)
        center_lon = np.mean(lons)

        dist_m = haversine(observations[0], observations[-1])
        duration_s = max(ts) - min(ts)
        velocity = dist_m / max(1.0, duration_s)

        # Calculate heading
        d_lat = observations[-1].get("lat", 0) - observations[0].get("lat", 0)
        d_lon = observations[-1].get("lon", 0) - observations[0].get("lon", 0)
        heading = math.degrees(math.atan2(d_lon, d_lat)) % 360

        basis = TrajectoryCompressor.classify_motion_basis(observations)

        # Drift tensor: standard deviation of positions from center
        # This is a "low-rank" representation of spatial variance
        drift = [float(np.std(lats)), float(np.std(lons))]

        return CompressedTrajectory(
            basis_vector=basis,
            start_ts=min(ts),
            end_ts=max(ts),
            center_lat=float(center_lat),
            center_lon=float(center_lon),
            velocity_mps=float(velocity),
            heading_deg=float(heading),
            drift_tensor=drift,
            confidence=0.85
        )

class CognitiveCacheEngine:
    """Orchestrator for multi-tier RF semantic memory."""

    def __init__(self, cluster_engine: Any, instance_db: Optional[Any] = None,
                 embedding_engine: Optional[Any] = None):
        self.cluster_engine = cluster_engine
        self.instance_db = instance_db
        self.embedding_engine = embedding_engine

        # Tiers
        self.hot_clusters: Dict[str, MacCluster] = {}
        self.warm_clusters: Dict[str, Dict[str, Any]] = {} # Summarized form
        self.cold_archive: deque = deque(maxlen=1000)      # Volatile overflow

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._maintenance_loop, daemon=True)
        self._thread.start()
        logger.info("[CognitiveCache] Started multi-tier maintenance loop")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _maintenance_loop(self):
        """Background consolidation and eviction."""
        while self._running:
            try:
                self._consolidate_tiers()
            except Exception as e:
                logger.error(f"[CognitiveCache] Maintenance error: {e}")
            time.sleep(30) # Run every 30 seconds

    def _consolidate_tiers(self):
        now = time.time()

        with self._lock:
            # 1. Promote/Demote between HOT and WARM
            all_clusters = list(self.cluster_engine.clusters.values())

            for cluster in all_clusters:
                age = now - cluster.updated_at
                retention = SemanticEvictor.compute_retention_score(cluster)

                # Semantic Eviction instead of just Time Eviction
                # Higher retention score keeps it in HOT longer.
                adjusted_ttl = HOT_TTL_S * (1.0 + retention)

                if age > adjusted_ttl:
                    # Move to WARM
                    logger.debug(f"[CognitiveCache] Demoting {cluster.cluster_id} to WARM (retention={retention})")
                    warm_data = {
                        "cluster_obj": cluster, # Keep the actual object
                        "summary": cluster.to_dict(),
                        "centroid": cluster.centroid(),
                        "compressed_trajectory": TrajectoryCompressor.compress(list(cluster.observations)),
                        "demoted_at": now
                    }
                    self.warm_clusters[cluster.cluster_id] = warm_data

                    # ── Persistent World-Model Mirror (COLD) ──
                    if self.instance_db:
                        try:
                            # Generate behavior embedding if engine available
                            embedding = None
                            if self.embedding_engine:
                                behavior_desc = cluster.behavior_summary()
                                embedding = self.embedding_engine.embed_text(behavior_desc).tolist()

                            self.instance_db.upsert_mac_cluster(
                                cluster_id=cluster.cluster_id,
                                behavior=cluster.behavior_summary(),
                                confidence=cluster.confidence(),
                                motion_basis=warm_data["compressed_trajectory"].basis_vector if warm_data["compressed_trajectory"] else "unknown",
                                centroid=(float(warm_data["centroid"].get("lat", 0)), float(warm_data["centroid"].get("lon", 0))),
                                drift_tensor=warm_data["compressed_trajectory"].drift_tensor if warm_data["compressed_trajectory"] else [],
                                embedding=embedding,
                                metadata=warm_data["summary"]
                            )
                        except Exception as e:
                            logger.warning(f"[CognitiveCache] COLD persistence failed for {cluster.cluster_id}: {e}")

                    # Remove from main engine to save "KV cache" (working set)
                    if cluster.cluster_id in self.cluster_engine.clusters:
                        del self.cluster_engine.clusters[cluster.cluster_id]

            # 2. Evict from WARM to COLD
            stale_warm = []
            for cid, data in self.warm_clusters.items():
                if now - data["demoted_at"] > WARM_TTL_S:
                    stale_warm.append(cid)

            for cid in stale_warm:
                data = self.warm_clusters.pop(cid)
                if data["compressed_trajectory"]:
                    self.cold_archive.append(data["compressed_trajectory"])
                logger.debug(f"[CognitiveCache] Archiving {cid} to COLD (memory overflow)")

    def get_cache_stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "hot_count": len(self.cluster_engine.clusters),
                "warm_count": len(self.warm_clusters),
                "cold_count": len(self.cold_archive)
            }

    def semantic_recall(self, query_obs: Dict[str, Any]) -> List[MacCluster]:
        """Attempt to recall continuity from WARM or COLD tiers if HOT miss.
        Returns matching clusters to be promoted back to HOT.
        """
        recalled = []
        now = time.time()

        # 1. Search WARM
        with self._lock:
            matches = []
            for cid, data in self.warm_clusters.items():
                # Spatial check first (fast)
                dist = haversine(query_obs, data["centroid"])
                if dist > 500: # 500m radius for warm search
                    continue

                cluster = data["cluster_obj"]
                score = cluster.similarity(query_obs)

                if score >= 0.70: # Threshold for warm matching
                    matches.append(cid)
                    recalled.append(cluster)

            for cid in matches:
                # Promotion: will be added back to HOT by the engine
                del self.warm_clusters[cid]
                logger.info(f"[CognitiveCache] Promoting {cid} from WARM to HOT (semantic hit)")

        # 2. Search COLD (Postgres/pgvector)
        if not recalled and self.instance_db and self.embedding_engine:
            try:
                # Use current observation as a query
                query_desc = f"New observation near {query_obs.get('lat')}, {query_obs.get('lon')}"
                # In a real system, we'd use a more sophisticated description or signature
                query_vec = self.embedding_engine.embed_text(query_desc).tolist()

                pg_matches = self.instance_db.search_similar_clusters(query_vec, threshold=0.85)
                if pg_matches:
                    logger.info(f"[CognitiveCache] COLD search found {len(pg_matches)} latent identities in pgvector")
                    # Promotion from COLD would involve re-inflating the MacCluster from DB metadata.
            except Exception as e:
                logger.warning(f"[CognitiveCache] COLD recall failed: {e}")

        return recalled
