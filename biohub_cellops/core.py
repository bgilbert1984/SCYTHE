import logging
import numpy as np
import threading
import time
import json
import os
from queue import Queue
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple, Union

logger = logging.getLogger("BiohubCellOps")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# PyTorch support fallback
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    PYTORCH_AVAILABLE = True
except ImportError:
    logger.warning("PyTorch not available in BiohubCellOps. Some ML modules will use fallbacks.")
    PYTORCH_AVAILABLE = False

# FastAPI support fallback
try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    logger.warning("FastAPI not available in BiohubCellOps. LineageRiskAPI will be disabled.")
    FASTAPI_AVAILABLE = False

# Custom JSON encoder for numpy types
class NumpyJSONEncoder(json.JSONEncoder):
    """JSON encoder that properly handles numpy types"""
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

@dataclass
class CellDetection:
    """Cell Detection data structure from segmentation runs (e.g. Ultrack, Cellpose)."""
    id: str
    embryo_id: str
    t: int
    z: float
    y: float
    x: float
    confidence: float
    volume_voxels: float = 0.0
    mean_intensity: float = 0.0
    source_run: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization, handling numpy types cleanly."""
        return {
            "id": self.id,
            "embryo_id": self.embryo_id,
            "t": int(self.t),
            "z": float(self.z),
            "y": float(self.y),
            "x": float(self.x),
            "confidence": float(self.confidence),
            "volume_voxels": float(self.volume_voxels),
            "mean_intensity": float(self.mean_intensity),
            "source_run": self.source_run,
            "metadata": self.metadata
        }

@dataclass
class CellTrackLink:
    """Represents a tracking edge (parent-child relationship) between two cell frames."""
    id: str
    embryo_id: str
    source_cell_id: str
    target_cell_id: str
    confidence: float
    link_type: str = "continuation"  # "continuation", "mitosis_parent", "mitosis_child", etc.
    source_run: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "embryo_id": self.embryo_id,
            "source_cell_id": self.source_cell_id,
            "target_cell_id": self.target_cell_id,
            "confidence": float(self.confidence),
            "link_type": self.link_type,
            "source_run": self.source_run,
            "metadata": self.metadata
        }

@dataclass
class CellTrajectoryPoint:
    """Represents a trajectory point stored in the motion model tracker."""
    t: int
    position: np.ndarray  # [x, y, z] coordinates in pixels/voxels
    volume_voxels: float
    mean_intensity: float
    confidence: float
    velocity: Optional[np.ndarray] = None  # [vx, vy, vz] in microns/frame
    acceleration: Optional[np.ndarray] = None  # [ax, ay, az] in microns/frame^2
    cell_id: Optional[str] = None
    track_id: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "t": int(self.t),
            "position": self.position.tolist() if isinstance(self.position, np.ndarray) else list(self.position),
            "volume_voxels": float(self.volume_voxels),
            "mean_intensity": float(self.mean_intensity),
            "confidence": float(self.confidence),
            "velocity": self.velocity.tolist() if isinstance(self.velocity, np.ndarray) else (list(self.velocity) if self.velocity is not None else None),
            "acceleration": self.acceleration.tolist() if isinstance(self.acceleration, np.ndarray) else (list(self.acceleration) if self.acceleration is not None else None),
            "cell_id": self.cell_id,
            "track_id": self.track_id,
            "metadata": self.metadata
        }

class CandidateDropout:
    """
    CandidateDropout prunes low-information cell candidates before expensive tracking.
    Uses a differentiable Gumbel-Sigmoid model if PyTorch is available, or threshold heuristics.
    """
    def __init__(self, feature_dim: int = 8, threshold: float = 0.15, temperature: float = 1.0):
        self.feature_dim = feature_dim
        self.threshold = threshold
        self.temperature = temperature
        
        if PYTORCH_AVAILABLE:
            # Simple neural scoring model to evaluate candidate quality
            class ScoreNet(nn.Module):
                def __init__(self, dim):
                    super().__init__()
                    self.net = nn.Sequential(
                        nn.Linear(dim, 16),
                        nn.ReLU(),
                        nn.Linear(16, 1)
                    )
                def forward(self, x):
                    return self.net(x)
            self.score_net = ScoreNet(feature_dim)
        else:
            self.score_net = None

    def prune_candidates(self, features: np.ndarray, training: bool = True) -> Tuple[np.ndarray, np.ndarray]:
        """
        Prunes candidates given their feature matrices.
        Features represent:
        [intensity, volume, fg_prob, bg_prob, local_density, model_agreement, dist_to_track, temp_consistency]
        """
        if PYTORCH_AVAILABLE and self.score_net is not None:
            # Conversion to PyTorch tensor for Gumbel-Sigmoid dropout
            with torch.set_grad_enabled(training):
                feat_tensor = torch.tensor(features, dtype=torch.float32)
                logits = self.score_net(feat_tensor).squeeze(-1)
                
                if training:
                    g_noise1 = torch.rand_like(logits).clamp(1e-10, 1.0 - 1e-10)
                    g_noise2 = torch.rand_like(logits).clamp(1e-10, 1.0 - 1e-10)
                    gumbel = -torch.log(-torch.log(g_noise1)) + torch.log(-torch.log(g_noise2))
                    keep_probs = torch.sigmoid((logits + gumbel) / self.temperature)
                else:
                    keep_probs = torch.sigmoid(logits)
                
                mask = (keep_probs > self.threshold).float()
                pruned_feats = feat_tensor * mask.unsqueeze(-1)
                return pruned_feats.numpy(), keep_probs.numpy()
        else:
            # Fallback numpy logic: prune based on foreground probability (index 2)
            fg_prob = features[..., 2] if features.shape[-1] > 2 else features[..., -1]
            keep_mask = fg_prob > self.threshold
            keep_probs = keep_mask.astype(np.float32)
            pruned_feats = features * keep_probs[..., np.newaxis]
            return pruned_feats, keep_probs

class NearestNeighborTracker:
    """Greedy Nearest Neighbor Cell Tracker operating in physical units."""
    def __init__(self, max_dist_um: float = 8.0, voxel_scale: Tuple[float, float, float] = (1.0, 1.0, 1.0)):
        self.max_dist_um = max_dist_um
        self.voxel_scale = np.array(voxel_scale, dtype=np.float32)

    def link_cells(self, cells_t: List[CellDetection], cells_tplus1: List[CellDetection]) -> List[CellTrackLink]:
        links = []
        if not cells_t or not cells_tplus1:
            return links

        targets_available = list(cells_tplus1)
        for parent in cells_t:
            if not targets_available:
                break
                
            p_pos = np.array([parent.x, parent.y, parent.z], dtype=np.float32) * self.voxel_scale
            best_target = None
            best_dist = float('inf')
            
            for child in targets_available:
                c_pos = np.array([child.x, child.y, child.z], dtype=np.float32) * self.voxel_scale
                dist = np.linalg.norm(c_pos - p_pos)
                if dist < best_dist and dist <= self.max_dist_um:
                    best_dist = dist
                    best_target = child
            
            if best_target:
                targets_available.remove(best_target)
                confidence = float(np.clip(1.0 - (best_dist / self.max_dist_um), 0.1, 0.99))
                links.append(CellTrackLink(
                    id=f"lnk_{parent.id}_{best_target.id}",
                    embryo_id=parent.embryo_id,
                    source_cell_id=parent.id,
                    target_cell_id=best_target.id,
                    confidence=confidence,
                    link_type="continuation",
                    source_run="nearest_neighbor"
                ))
        return links

# Helper for global assignment
try:
    from scipy.optimize import linear_sum_assignment
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

class OptimizationTracker:
    """Global Optimization Cell Tracker using the Hungarian algorithm."""
    def __init__(self, max_dist_um: float = 12.0, voxel_scale: Tuple[float, float, float] = (1.0, 1.0, 1.0)):
        self.max_dist_um = max_dist_um
        self.voxel_scale = np.array(voxel_scale, dtype=np.float32)

    def link_cells(self, cells_t: List[CellDetection], cells_tplus1: List[CellDetection]) -> List[CellTrackLink]:
        links = []
        if not cells_t or not cells_tplus1:
            return links

        n_parents = len(cells_t)
        n_children = len(cells_tplus1)
        
        # Distance matrix (physical cost matrix)
        cost_matrix = np.zeros((n_parents, n_children), dtype=np.float32)
        for i, parent in enumerate(cells_t):
            p_pos = np.array([parent.x, parent.y, parent.z], dtype=np.float32) * self.voxel_scale
            for j, child in enumerate(cells_tplus1):
                c_pos = np.array([child.x, child.y, child.z], dtype=np.float32) * self.voxel_scale
                cost_matrix[i, j] = np.linalg.norm(c_pos - p_pos)

        if SCIPY_AVAILABLE:
            row_ind, col_ind = linear_sum_assignment(cost_matrix)
            for r, c in zip(row_ind, col_ind):
                dist = cost_matrix[r, c]
                if dist <= self.max_dist_um:
                    parent = cells_t[r]
                    child = cells_tplus1[c]
                    confidence = float(np.clip(1.0 - (dist / self.max_dist_um), 0.15, 0.99))
                    links.append(CellTrackLink(
                        id=f"lnk_{parent.id}_{child.id}",
                        embryo_id=parent.embryo_id,
                        source_cell_id=parent.id,
                        target_cell_id=child.id,
                        confidence=confidence,
                        link_type="continuation",
                        source_run="optimization"
                    ))
        else:
            # Fallback to greedy if scipy is not available
            logger.warning("Scipy linear_sum_assignment not available. Falling back to NearestNeighbor matching.")
            greedy_tracker = NearestNeighborTracker(max_dist_um=self.max_dist_um, voxel_scale=tuple(self.voxel_scale))
            return greedy_tracker.link_cells(cells_t, cells_tplus1)
            
        return links

class SpeculativeTrackerEnsemble:
    """
    Arbitrates cell tracking using a SpeculativeEnsemble model.
    Sends simple, sparse tracking regions to a fast NearestNeighbor tracker, while
    dense mitosis pits/clusters escalate to the slower, optimized Hungarian tracker.
    """
    def __init__(self, fast_tracker: NearestNeighborTracker, slow_tracker: OptimizationTracker, 
                 density_threshold: int = 5, uncertainty_threshold: float = 0.6):
        self.fast_tracker = fast_tracker
        self.slow_tracker = slow_tracker
        self.density_threshold = density_threshold
        self.uncertainty_threshold = uncertainty_threshold
        self.stats = {
            "fast_calls": 0,
            "slow_calls": 0,
            "escalations": 0
        }

    def link_frame_region(self, candidates_t: List[CellDetection], candidates_tplus1: List[CellDetection], 
                          region_metadata: Optional[Dict[str, Any]] = None) -> List[CellTrackLink]:
        """Links frame segments, arbitrating between fast and slow tracker lanes."""
        local_density = len(candidates_t)
        has_mitosis_hints = region_metadata.get("has_mitosis_hints", False) if region_metadata else False
        
        # High density/complexity escalates immediately toslow optimized tracker
        if local_density > self.density_threshold or has_mitosis_hints:
            self.stats["slow_calls"] += 1
            self.stats["escalations"] += 1
            logger.debug(f"Arbitrator: Complex region (density={local_density}, mitosis={has_mitosis_hints}). Routing to slow tracker.")
            return self.slow_tracker.link_cells(candidates_t, candidates_tplus1)

        # Sparse easy region goes to fast nearest-neighbor tracker
        self.stats["fast_calls"] += 1
        fast_links = self.fast_tracker.link_cells(candidates_t, candidates_tplus1)
        
        # Identify links with low confidence/high-uncertainty to re-evaluate
        uncertain_source_ids = []
        uncertain_target_ids = []
        for link in fast_links:
            if link.confidence < self.uncertainty_threshold:
                uncertain_source_ids.append(link.source_cell_id)
                uncertain_target_ids.append(link.target_cell_id)

        if uncertain_source_ids:
            self.stats["escalations"] += 1
            logger.debug(f"Arbitrator: Found {len(uncertain_source_ids)} uncertain link(s). Escalating to slow tracker.")
            
            # Run slow tracker on the isolated uncertain nodes only
            sub_candidates_t = [c for c in candidates_t if c.id in uncertain_source_ids]
            sub_candidates_tplus1 = [c for c in candidates_tplus1 if c.id in uncertain_target_ids]
            
            if sub_candidates_t and sub_candidates_tplus1:
                slow_links = self.slow_tracker.link_cells(sub_candidates_t, sub_candidates_tplus1)
                
                # Merge: discard low-confidence fast links and inject optimized ones
                retained_fast_links = [l for l in fast_links if l.source_cell_id not in uncertain_source_ids]
                return retained_fast_links + slow_links

        return fast_links

class LineageAnomalyDetector:
    """
    Analyzes cell lineage graph edges and divisions to detect tracking and division anomalies.
    Replaces GhostAnomalyDetector.
    """
    def __init__(self, max_speed_um_per_frame: float = 8.0, max_volume_ratio: float = 2.5, 
                 max_intensity_ratio: float = 3.0, min_division_symmetry: float = 0.4, use_nn: bool = True):
        self.max_speed_um_per_frame = max_speed_um_per_frame
        self.max_volume_ratio = max_volume_ratio
        self.max_intensity_ratio = max_intensity_ratio
        self.min_division_symmetry = min_division_symmetry
        self.use_nn = use_nn and PYTORCH_AVAILABLE
        
        if self.use_nn:
            # Simple neural anomaly scorer taking 10 features
            class NeuralAnomalier(nn.Module):
                def __init__(self):
                    super().__init__()
                    self.mlp = nn.Sequential(
                        nn.Linear(10, 32),
                        nn.ReLU(),
                        nn.Linear(32, 16),
                        nn.ReLU(),
                        nn.Linear(16, 1),
                        nn.Sigmoid()
                    )
                def forward(self, x):
                    return self.mlp(x)
            self.neural_model = NeuralAnomalier()
        else:
            self.neural_model = None

    def score_edge(self, parent: CellDetection, child: CellDetection, 
                   voxel_scale: Tuple[float, float, float] = (1.0, 1.0, 1.0)) -> Dict[str, Any]:
        """Evaluates a single parent-child link edge for spatial and physical anomalies."""
        scale = np.array(voxel_scale, dtype=np.float32)
        p_pos = np.array([parent.x, parent.y, parent.z], dtype=np.float32) * scale
        c_pos = np.array([child.x, child.y, child.z], dtype=np.float32) * scale

        dt = max(1, child.t - parent.t)
        dist = float(np.linalg.norm(c_pos - p_pos))
        speed = dist / dt

        p_vol = max(parent.volume_voxels, 1.0)
        c_vol = max(child.volume_voxels, 1.0)
        volume_ratio = max(p_vol, c_vol) / min(p_vol, c_vol)

        p_int = max(parent.mean_intensity, 1.0)
        c_int = max(child.mean_intensity, 1.0)
        intensity_ratio = max(p_int, c_int) / min(p_int, c_int)

        confidence_drop = max(0.0, parent.confidence - child.confidence)

        # Rule-based risk accrual
        risk = 0.0
        flags = []

        if child.t != parent.t + 1:
            risk += 12.0
            flags.append("NON_ADJACENT_TIME")

        if speed > self.max_speed_um_per_frame:
            excess = min(speed / self.max_speed_um_per_frame, 5.0)
            risk += 8.0 * excess
            flags.append("TELEPORT")

        if volume_ratio > self.max_volume_ratio:
            excess = min(volume_ratio / self.max_volume_ratio, 5.0)
            risk += 5.0 * excess
            flags.append("VOLUME_SHIFT")

        if intensity_ratio > self.max_intensity_ratio:
            excess = min(intensity_ratio / self.max_intensity_ratio, 5.0)
            risk += 4.0 * excess
            flags.append("INTENSITY_SHIFT")

        risk += confidence_drop * 5.0

        # Feed feature vector into Neural Network
        neural_risk_score = 0.0
        if self.use_nn and self.neural_model is not None:
            features = [
                dist,                          # scaled_distance_um
                speed,                         # speed_um_per_frame
                volume_ratio,                  # volume_ratio
                confidence_drop,               # confidence_drop
                5.0,                           # local_density (default)
                0.0,                           # ensemble_disagreement
                intensity_ratio,               # intensity_ratio
                1.0,                           # daughter_symmetry_score (placeholder)
                1.0,                           # track_age
                float(dt)                      # gap_length
            ]
            try:
                with torch.no_grad():
                    feats_t = torch.tensor(features, dtype=torch.float32).unsqueeze(0)
                    neural_risk_score = float(self.neural_model(feats_t).item())
                # Blend
                risk = risk * 0.4 + neural_risk_score * 12.0
            except Exception as e:
                logger.debug(f"Neural anomaly prediction failed: {e}")

        anomaly_detected = (risk > 15.0) or (neural_risk_score > 0.75) or (len(flags) > 0)
        possible_errors = []
        if "TELEPORT" in flags:
            possible_errors.append("Z_AXIS_TELEPORT")
        if "VOLUME_SHIFT" in flags:
            possible_errors.append("MERGED_SEGMENTATION" if child.volume_voxels > parent.volume_voxels else "FRAGMENTED_SEGMENTATION")
        if "NON_ADJACENT_TIME" in flags:
            possible_errors.append("ORPHANED_TRACKLET")

        return {
            "anomaly_detected": anomaly_detected,
            "confidence": float(1.0 / (1.0 + np.exp(-risk / 10.0))),
            "detection_method": "hybrid_neural_rules" if self.use_nn else "rule_engine",
            "possible_errors": possible_errors,
            "flags": flags,
            "risk_score": float(risk),
            "distance_um": dist,
            "speed_um_per_frame": speed,
            "volume_ratio": volume_ratio,
            "intensity_ratio": intensity_ratio,
        }

    def score_division(self, parent: CellDetection, daughter1: CellDetection, daughter2: CellDetection, 
                       voxel_scale: Tuple[float, float, float] = (1.0, 1.0, 1.0)) -> Dict[str, Any]:
        """Evaluates physical mass-conservation and coordinate metrics for 3D mitotic division."""
        scale = np.array(voxel_scale, dtype=np.float32)
        p_pos = np.array([parent.x, parent.y, parent.z], dtype=np.float32) * scale
        d1_pos = np.array([daughter1.x, daughter1.y, daughter1.z], dtype=np.float32) * scale
        d2_pos = np.array([daughter2.x, daughter2.y, daughter2.z], dtype=np.float32) * scale

        dist_d1 = float(np.linalg.norm(d1_pos - p_pos))
        dist_d2 = float(np.linalg.norm(d2_pos - p_pos))

        parent_vol = max(parent.volume_voxels, 1.0)
        daughter_vol_sum = daughter1.volume_voxels + daughter2.volume_voxels
        vol_conservation = daughter_vol_sum / parent_vol

        min_vol = max(min(daughter1.volume_voxels, daughter2.volume_voxels), 1.0)
        max_vol = max(max(daughter1.volume_voxels, daughter2.volume_voxels), 1.0)
        symmetry = min_vol / max_vol

        risk = 0.0
        flags = []

        if max(dist_d1, dist_d2) > self.max_speed_um_per_frame * 1.5:
            risk += 9.0
            flags.append("LONG_DISTANCE_DIVISION")

        if vol_conservation < 0.6 or vol_conservation > 1.4:
            risk += 7.0
            flags.append("VOLUME_UNCONSERVED")

        if symmetry < self.min_division_symmetry:
            risk += 8.0
            flags.append("ASYMMETRIC_DIVISION")

        anomaly_detected = (risk > 10.0) or (len(flags) > 0)
        possible_errors = []
        if "ASYMMETRIC_DIVISION" in flags:
            possible_errors.append("BAD_MITOSIS")
        if "VOLUME_UNCONSERVED" in flags:
            possible_errors.append("MERGED_SEGMENTATION")

        return {
            "anomaly_detected": anomaly_detected,
            "confidence": float(1.0 / (1.0 + np.exp(-risk / 5.0))),
            "detection_method": "mitosis_conservation_rules",
            "possible_errors": possible_errors,
            "flags": flags,
            "risk_score": float(risk),
            "symmetry_score": float(symmetry),
            "vol_conservation_ratio": float(vol_conservation),
            "daughter1_distance_um": dist_d1,
            "daughter2_distance_um": dist_d2
        }

class SimpleKalmanFilter3D:
    """A lightweight 3D Kalman filter tracking 3D position and velocity."""
    def __init__(self, dt: float = 1.0, process_noise: float = 0.1, measurement_noise: float = 1.0):
        self.dt = dt
        self.x = np.zeros(6, dtype=np.float32)  # [x, y, z, vx, vy, vz]
        
        # Transition matrix
        self.A = np.array([
            [1, 0, 0, dt,  0,  0],
            [0, 1, 0,  0, dt,  0],
            [0, 0, 1,  0,  0, dt],
            [0, 0, 0,  1,  0,  0],
            [0, 0, 0,  0,  1,  0],
            [0, 0, 0,  0,  0,  1]
        ], dtype=np.float32)
        
        # Measurement matrix (position only)
        self.H = np.array([
            [1, 0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 0],
            [0, 0, 1, 0, 0, 0]
        ], dtype=np.float32)
        
        self.P = np.eye(6, dtype=np.float32) * 10.0
        self.Q = np.eye(6, dtype=np.float32) * process_noise
        self.R = np.eye(3, dtype=np.float32) * measurement_noise

    def initialize(self, pos: np.ndarray):
        self.x[:3] = pos
        self.x[3:] = 0.0
        self.P = np.eye(6, dtype=np.float32) * 10.0

    def predict(self) -> np.ndarray:
        self.x = self.A.dot(self.x)
        self.P = self.A.dot(self.P).dot(self.A.T) + self.Q
        return self.x[:3]

    def update(self, pos: np.ndarray):
        y = pos - self.H.dot(self.x)
        S = self.H.dot(self.P).dot(self.H.T) + self.R
        K = self.P.dot(self.H.T).dot(np.linalg.inv(S))
        self.x = self.x + K.dot(y)
        self.P = (np.eye(6, dtype=np.float32) - K.dot(self.H)).dot(self.P)

class CellMotionTracker:
    """
    Cell trajectory tracker replacing DOMASignalTracker.
    Tracks kinetic states, calculates velocities and accelerations, and performs
    coordinate prediction blending constant velocity, Kalman filters, and tissue-flow fields.
    """
    def __init__(self, voxel_scale: Tuple[float, float, float] = (1.0, 1.0, 1.0)):
        self.voxel_scale = np.array(voxel_scale, dtype=np.float32)
        self.tracklets: Dict[int, List[CellTrajectoryPoint]] = {}
        self.kalman_filters: Dict[int, SimpleKalmanFilter3D] = {}
        self.flow_history: List[Tuple[np.ndarray, np.ndarray]] = []  # List of (pos_um, vel_um)

    def add_point(self, track_id: int, point: CellTrajectoryPoint):
        """Adds a trajectory point and updates Kalman and kinetic stats."""
        if track_id not in self.tracklets:
            self.tracklets[track_id] = []
            kf = SimpleKalmanFilter3D()
            kf.initialize(point.position)
            self.kalman_filters[track_id] = kf

        trajectory = self.tracklets[track_id]
        kf = self.kalman_filters[track_id]
        
        kf.predict()
        kf.update(point.position)
        
        point.track_id = track_id
        
        if trajectory:
            prev = trajectory[-1]
            dt = max(1, point.t - prev.t)
            fd_velocity = (point.position - prev.position) * self.voxel_scale / dt
            point.velocity = fd_velocity
            
            # Save flow history
            self.flow_history.append((point.position * self.voxel_scale, fd_velocity))
            
            if prev.velocity is not None:
                point.acceleration = (point.velocity - prev.velocity) / dt
        else:
            point.velocity = np.zeros(3, dtype=np.float32)
            point.acceleration = np.zeros(3, dtype=np.float32)
            
        trajectory.append(point)
        
        # Cap flow history size
        if len(self.flow_history) > 3000:
            self.flow_history = self.flow_history[-3000:]

    def get_local_tissue_flow(self, position: np.ndarray, radius_um: float = 25.0) -> np.ndarray:
        """Finds spatial tissue flow vector by averaging velocities of close cell trajectories."""
        if not self.flow_history:
            return np.zeros(3, dtype=np.float32)

        target_pos_um = position * self.voxel_scale
        velocities = []
        
        for pos_um, vel_um in self.flow_history:
            dist = np.linalg.norm(pos_um - target_pos_um)
            if dist < radius_um:
                weight = 1.0 / (dist + 1.0)
                velocities.append((vel_um, weight))
                
        if not velocities:
            return np.zeros(3, dtype=np.float32)
            
        sum_vel = np.zeros(3, dtype=np.float32)
        sum_weight = 0.0
        for vel, weight in velocities:
            sum_vel += vel * weight
            sum_weight += weight
            
        return sum_vel / sum_weight

    def predict_next(self, track_id: int, method: str = "kalman") -> Optional[np.ndarray]:
        """Predicts the next coordinate location [x, y, z] for a track."""
        trajectory = self.tracklets.get(track_id, [])
        if not trajectory:
            return None
            
        latest = trajectory[-1]
        kf = self.kalman_filters.get(track_id)
        
        if method == "constant_velocity" or latest.velocity is None:
            if latest.velocity is None or np.all(latest.velocity == 0):
                return latest.position
            return latest.position + latest.velocity / self.voxel_scale
            
        elif method == "kalman" and kf is not None:
            return kf.x[:3]
            
        elif method == "tissue_flow":
            flow_vel_um = self.get_local_tissue_flow(latest.position)
            return latest.position + flow_vel_um / self.voxel_scale
            
        else:  # ensemble
            pos_cv = latest.position + (latest.velocity if latest.velocity is not None else np.zeros(3)) / self.voxel_scale
            pos_kf = kf.x[:3] if kf is not None else pos_cv
            pos_flow = latest.position + self.get_local_tissue_flow(latest.position) / self.voxel_scale
            return 0.4 * pos_kf + 0.3 * pos_cv + 0.3 * pos_flow

    def get_trajectory_analysis(self, track_id: int) -> Optional[Dict[str, Any]]:
        trajectory = self.tracklets.get(track_id, [])
        if len(trajectory) < 2:
            return None
            
        positions = np.array([p.position for p in trajectory])
        vols = np.array([p.volume_voxels for p in trajectory])
        intensities = np.array([p.mean_intensity for p in trajectory])
        
        scaled_pos = positions * self.voxel_scale
        distances = np.linalg.norm(np.diff(scaled_pos, axis=0), axis=1)
        total_dist = float(np.sum(distances))
        
        dt = trajectory[-1].t - trajectory[0].t
        speed = total_dist / dt if dt > 0 else 0.0
        
        return {
            "track_id": track_id,
            "length": len(trajectory),
            "total_distance_um": total_dist,
            "average_speed_um_per_frame": speed,
            "volume_std": float(np.std(vols)),
            "intensity_std": float(np.std(intensities)),
            "start_position": positions[0].tolist(),
            "end_position": positions[-1].tolist()
        }

    def cleanup_old_tracks(self, max_idle_frames: int = 15, current_frame: int = 0):
        inactive_ids = []
        for tid, traj in self.tracklets.items():
            if traj and (current_frame - traj[-1].t) > max_idle_frames:
                inactive_ids.append(tid)
                
        for tid in inactive_ids:
            if tid in self.kalman_filters:
                del self.kalman_filters[tid]

if PYTORCH_AVAILABLE:
    class CellSequenceEncoder(nn.Module):
        """
        Transformer-based encoder for spatial-temporal sequences of cell candidates.
        Corrects constructor signature mismatch in legacy SpectrumEncoder.
        """
        def __init__(self, input_dim: int = 8, hidden_dim: int = 128, num_heads: int = 4, 
                     num_layers: int = 2, dropout: float = 0.1):
            super().__init__()
            self.input_dim = input_dim
            self.hidden_dim = hidden_dim
            
            self.input_proj = nn.Linear(input_dim, hidden_dim)
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=hidden_dim,
                nhead=num_heads,
                dim_feedforward=hidden_dim * 4,
                dropout=dropout,
                batch_first=True
            )
            self.transformer = nn.TransformerEncoder(encoder_layer, num_layers)
            self.output_proj = nn.Linear(hidden_dim, input_dim)

        def forward(self, seq_tensor: torch.Tensor) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
            x = self.input_proj(seq_tensor)
            x = self.transformer(x)
            encoded = self.output_proj(x)
            return encoded, None
else:
    class CellSequenceEncoder:
        def __init__(self, *args, **kwargs):
            logger.warning("CellSequenceEncoder requires PyTorch - disabled.")
        def forward(self, x):
            return x, None

class SegmentationRunIntegrator:
    """Manages integration of diverse segmentation outputs (Ultrack, Cellpose, StarDist)."""
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.runs = {}
        self.active_runs = []

    def register_run(self, name: str, run_class: Any, params: Dict[str, Any]):
        try:
            self.runs[name] = run_class(**params)
            logger.info(f"Registered segmentation pipeline: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to register run {name}: {e}")
            return False

    def activate_run(self, name: str):
        if name in self.runs and name not in self.active_runs:
            self.active_runs.append(name)
            logger.info(f"Activated run lane: {name}")
            return True
        return False

    def deactivate_run(self, name: str):
        if name in self.active_runs:
            self.active_runs.remove(name)
            logger.info(f"Deactivated run lane: {name}")
            return True
        return False

    def get_run_data(self, name: str) -> Optional[Dict[str, Any]]:
        if name in self.runs:
            try:
                return self.runs[name].get_data()
            except Exception as e:
                logger.error(f"Error pulling from {name}: {e}")
        return None

class MockSegmentationRun:
    """Simulates active embryo cell segmentations, including mitosis and drifting coordinate positions."""
    def __init__(self, run_name: str = "Mock Run"):
        self.run_name = run_name
        self.t = 0
        self.cells = []
        self._initialize_cells()

    def _initialize_cells(self):
        for i in range(5):
            self.cells.append({
                "id": f"cell_{self.run_name}_{i}",
                "z": 10.0 + i * 2.5,
                "y": 50.0 + np.random.uniform(-8, 8),
                "x": 50.0 + np.random.uniform(-8, 8),
                "vol": 120.0 + np.random.uniform(-10, 10),
                "intensity": 180.0 + np.random.uniform(-15, 15),
                "conf": 0.95
            })

    def get_data(self) -> Dict[str, Any]:
        self.t += 1
        candidates = []
        new_cells = []
        
        for cell in self.cells:
            # 8% chance of mitotic division
            if np.random.rand() < 0.08 and len(self.cells) < 12:
                id1 = f"{cell['id']}_d1"
                id2 = f"{cell['id']}_d2"
                half_vol = cell["vol"] * 0.5
                
                new_cells.append({
                    "id": id1,
                    "z": cell["z"] + np.random.uniform(-0.8, 0.8),
                    "y": cell["y"] + np.random.uniform(-1.5, 1.5),
                    "x": cell["x"] + np.random.uniform(-1.5, 1.5),
                    "vol": half_vol + np.random.uniform(-4, 4),
                    "intensity": cell["intensity"] + np.random.uniform(-8, 8),
                    "conf": 0.9
                })
                new_cells.append({
                    "id": id2,
                    "z": cell["z"] + np.random.uniform(-0.8, 0.8),
                    "y": cell["y"] + np.random.uniform(-1.5, 1.5),
                    "x": cell["x"] + np.random.uniform(-1.5, 1.5),
                    "vol": half_vol + np.random.uniform(-4, 4),
                    "intensity": cell["intensity"] + np.random.uniform(-8, 8),
                    "conf": 0.9
                })
            else:
                # Basic motion drift
                cell["z"] += np.random.uniform(-0.3, 0.3)
                cell["y"] += np.random.uniform(-1.0, 1.0)
                cell["x"] += np.random.uniform(-1.0, 1.0)
                cell["vol"] += np.random.uniform(-1.5, 1.5)
                cell["intensity"] += np.random.uniform(-4, 4)
                new_cells.append(cell)
                
        self.cells = new_cells
        
        for c in self.cells:
            candidates.append(CellDetection(
                id=f"{c['id']}_t{self.t}",
                embryo_id="embryo_demo",
                t=self.t,
                z=c["z"],
                y=c["y"],
                x=c["x"],
                confidence=c["conf"],
                volume_voxels=c["vol"],
                mean_intensity=c["intensity"],
                source_run=self.run_name
            ))
            
        return {
            "embryo_id": "embryo_demo",
            "t": self.t,
            "candidates": candidates
        }

class CellLineageIntelligenceSystem:
    """
    Main runtime orchestrator for CellOps intelligence.
    Extracts cell candidates, applies Gumbel candidate dropout, coordinates spatial
    tracking via SpeculativeTrackerEnsemble, analyzes motion, updates Kalman states,
    and runs LineageAnomalyDetector scoring.
    """
    def __init__(self, config: Dict[str, Any], comm_network: Any):
        self.config = config
        self.comm_network = comm_network
        
        self.voxel_scale = tuple(config.get("voxel_scale", (1.0, 1.0, 1.0)))
        
        self.motion_tracker = CellMotionTracker(voxel_scale=self.voxel_scale)
        self.anomaly_detector = LineageAnomalyDetector(
            max_speed_um_per_frame=config.get("max_speed_um_per_frame", 8.0),
            max_volume_ratio=config.get("max_volume_ratio", 2.5)
        )
        
        fast_tracker = NearestNeighborTracker(max_dist_um=config.get("max_speed_um_per_frame", 8.0), voxel_scale=self.voxel_scale)
        slow_tracker = OptimizationTracker(max_dist_um=config.get("max_speed_um_per_frame", 8.0) * 1.5, voxel_scale=self.voxel_scale)
        
        self.speculative_ensemble = SpeculativeTrackerEnsemble(
            fast_tracker=fast_tracker,
            slow_tracker=slow_tracker,
            density_threshold=config.get("density_threshold", 5),
            uncertainty_threshold=config.get("uncertainty_threshold", 0.6)
        )
        
        self.cell_queue = Queue()
        self.processed_cells: List[CellDetection] = []
        self.active_tracks: Dict[int, List[CellDetection]] = {}
        self.cell_id_to_track_id: Dict[str, int] = {}
        self.track_counter = 0
        self.lineage_warnings: List[Dict[str, Any]] = []
        
        self.segmentation_integrator = SegmentationRunIntegrator(config)
        self.running = False
        
        self._register_default_lanes()

    def _register_default_lanes(self):
        self.segmentation_integrator.register_run("ultrack_lane", MockSegmentationRun, {"run_name": "Ultrack Raw"})
        self.segmentation_integrator.register_run("cellpose_lane", MockSegmentationRun, {"run_name": "Cellpose Base"})
        self.segmentation_integrator.activate_run("ultrack_lane")

    def start(self):
        logger.info("Starting CellLineageIntelligenceSystem loops.")
        self.running = True
        
        self.processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
        self.processing_thread.start()
        
        self.ingest_thread = threading.Thread(target=self._ingest_loop, daemon=True)
        self.ingest_thread.start()

    def shutdown(self):
        logger.info("Shutting down CellLineageIntelligenceSystem loops.")
        self.running = False

    def put_frame_candidates(self, embryo_id: str, t: int, candidates: List[CellDetection]):
        self.cell_queue.put({
            "embryo_id": embryo_id,
            "t": t,
            "candidates": candidates
        })

    def _processing_loop(self):
        while self.running:
            try:
                if not self.cell_queue.empty():
                    frame_data = self.cell_queue.get(timeout=0.1)
                    self.process_frame(frame_data["embryo_id"], frame_data["t"], frame_data["candidates"])
                    self.cell_queue.task_done()
                else:
                    time.sleep(0.05)
            except Exception as e:
                logger.error(f"Error in CellOps processing thread: {e}", exc_info=True)
                time.sleep(0.5)

    def _ingest_loop(self):
        while self.running:
            try:
                for run_name in self.segmentation_integrator.active_runs:
                    data = self.segmentation_integrator.get_run_data(run_name)
                    if data:
                        self.put_frame_candidates(
                            embryo_id=data.get("embryo_id", "embryo_demo"),
                            t=data.get("t", 0),
                            candidates=data.get("candidates", [])
                        )
                time.sleep(1.0)
            except Exception as e:
                logger.error(f"Error in CellOps ingest loop: {e}")
                time.sleep(2.0)

    def process_frame(self, embryo_id: str, t: int, candidates: List[CellDetection]):
        """Runs the main pipeline: dropout filter, tracking match, kinetics analysis, anomaly checks."""
        logger.info(f"Processing frame {t} for embryo {embryo_id} with {len(candidates)} candidates.")
        
        # 1. Candidate selection / Pruning
        pruned_candidates = []
        for cand in candidates:
            # Build feature array for CandidateDropout checks
            feat = np.array([
                cand.mean_intensity,
                cand.volume_voxels,
                cand.confidence,
                1.0 - cand.confidence,
                float(len(candidates)),
                1.0,
                0.0,
                1.0
            ], dtype=np.float32)
            
            # Simple threshold check
            if cand.confidence > 0.15:
                pruned_candidates.append(cand)
                self.comm_network.publish("cell_detected", cand.to_dict())
                self.processed_cells.append(cand)

        if not pruned_candidates:
            return

        # 2. Compute Trajectory Links
        prev_candidates = []
        for track_id, track_list in self.active_tracks.items():
            if track_list and track_list[-1].t == t - 1 and track_list[-1].embryo_id == embryo_id:
                prev_candidates.append(track_list[-1])

        if prev_candidates:
            links = self.speculative_ensemble.link_frame_region(
                candidates_t=prev_candidates,
                candidates_tplus1=pruned_candidates,
                region_metadata={"has_mitosis_hints": len(pruned_candidates) > len(prev_candidates)}
            )
            
            linked_target_ids = set()
            for link in links:
                linked_target_ids.add(link.target_cell_id)
                
                parent_cell = next((c for c in prev_candidates if c.id == link.source_cell_id), None)
                child_cell = next((c for c in pruned_candidates if c.id == link.target_cell_id), None)
                
                if parent_cell and child_cell:
                    track_id = self.cell_id_to_track_id[parent_cell.id]
                    self.cell_id_to_track_id[child_cell.id] = track_id
                    self.active_tracks[track_id].append(child_cell)
                    
                    # Update Motion Model state
                    pt = CellTrajectoryPoint(
                        t=child_cell.t,
                        position=np.array([child_cell.x, child_cell.y, child_cell.z], dtype=np.float32),
                        volume_voxels=child_cell.volume_voxels,
                        mean_intensity=child_cell.mean_intensity,
                        confidence=child_cell.confidence,
                        cell_id=child_cell.id
                    )
                    self.motion_tracker.add_point(track_id, pt)
                    
                    # Anomaly analysis
                    edge_anom = self.anomaly_detector.score_edge(parent_cell, child_cell, voxel_scale=self.voxel_scale)
                    if edge_anom["anomaly_detected"]:
                        warn = {
                            "warning_type": "EDGE_ANOMALY",
                            "embryo_id": embryo_id,
                            "t": t,
                            "track_id": track_id,
                            "source_cell_id": parent_cell.id,
                            "target_cell_id": child_cell.id,
                            "anomaly_details": edge_anom
                        }
                        self.lineage_warnings.append(warn)
                        self.comm_network.publish("lineage_warning", warn)
                        
                    self.comm_network.publish("track_link_created", link.to_dict())

            # Initialize unlinked targets as new tracks
            for child_cell in pruned_candidates:
                if child_cell.id not in linked_target_ids:
                    self._initialize_new_track(child_cell)
        else:
            for child_cell in pruned_candidates:
                self._initialize_new_track(child_cell)

        # 3. Mitotic division checking
        if prev_candidates and 'links' in locals():
            parent_to_kids = {}
            for link in links:
                parent_to_kids.setdefault(link.source_cell_id, []).append(link.target_cell_id)

            for p_id, kid_ids in parent_to_kids.items():
                if len(kid_ids) == 2:
                    p_cell = next((c for c in prev_candidates if c.id == p_id), None)
                    d1_cell = next((c for c in pruned_candidates if c.id == kid_ids[0]), None)
                    d2_cell = next((c for c in pruned_candidates if c.id == kid_ids[1]), None)
                    
                    if p_cell and d1_cell and d2_cell:
                        div_anom = self.anomaly_detector.score_division(p_cell, d1_cell, d2_cell, voxel_scale=self.voxel_scale)
                        if div_anom["anomaly_detected"]:
                            warn = {
                                "warning_type": "MITOSIS_ANOMALY",
                                "embryo_id": embryo_id,
                                "t": t,
                                "parent_cell_id": p_id,
                                "daughter1_cell_id": kid_ids[0],
                                "daughter2_cell_id": kid_ids[1],
                                "anomaly_details": div_anom
                            }
                            self.lineage_warnings.append(warn)
                            self.comm_network.publish("lineage_warning", warn)

        self.motion_tracker.cleanup_old_tracks(max_idle_frames=10, current_frame=t)

    def _initialize_new_track(self, cell: CellDetection):
        self.track_counter += 1
        track_id = self.track_counter
        self.cell_id_to_track_id[cell.id] = track_id
        self.active_tracks[track_id] = [cell]
        
        pt = CellTrajectoryPoint(
            t=cell.t,
            position=np.array([cell.x, cell.y, cell.z], dtype=np.float32),
            volume_voxels=cell.volume_voxels,
            mean_intensity=cell.mean_intensity,
            confidence=cell.confidence,
            cell_id=cell.id
        )
        self.motion_tracker.add_point(track_id, pt)

    def analyze_lineages(self) -> Dict[str, Any]:
        """Runs global analysis of lineage tracks and warnings for UI telemetry."""
        total_tracks = len(self.active_tracks)
        warns = len(self.lineage_warnings)
        
        active_cnt = 0
        for tid, traj in self.active_tracks.items():
            if traj and abs(traj[-1].t - max((c.t for c in self.processed_cells), default=0)) < 5:
                active_cnt += 1
                
        analysis = {
            "total_tracks": total_tracks,
            "active_tracks": active_cnt,
            "total_detections": len(self.processed_cells),
            "total_warnings": warns,
            "warnings": self.lineage_warnings[-15:],
            "timestamp": time.time()
        }
        self.comm_network.publish("lineage_analysis", analysis)
        return analysis

class LineageRiskAPI:
    """FastAPI interface supporting local human operator validation and recomputation."""
    def __init__(self, system_instance: CellLineageIntelligenceSystem):
        if not FASTAPI_AVAILABLE:
            raise ImportError("FastAPI is not available.")
            
        self.system = system_instance
        self.app = FastAPI(title="Biohub CellOps Intelligence API", version="2026.1.0")
        self._setup_routes()

    def _setup_routes(self):
        @self.app.post("/api/biohub/analyze_cell")
        async def analyze_cell(cell_data: Dict[str, Any]):
            try:
                cell = CellDetection(
                    id=cell_data["id"],
                    embryo_id=cell_data["embryo_id"],
                    t=int(cell_data["t"]),
                    z=float(cell_data["z"]),
                    y=float(cell_data["y"]),
                    x=float(cell_data["x"]),
                    confidence=float(cell_data["confidence"]),
                    volume_voxels=float(cell_data.get("volume_voxels", 0.0)),
                    mean_intensity=float(cell_data.get("mean_intensity", 0.0)),
                    source_run=cell_data.get("source_run", "api")
                )
                return JSONResponse(content={
                    "cell_id": cell.id,
                    "retained": cell.confidence > 0.15,
                    "intensity_status": "NORMAL" if cell.mean_intensity > 50 else "WEAK",
                    "volume_status": "NORMAL" if cell.volume_voxels > 10 else "TINY"
                })
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))

        @self.app.post("/api/biohub/analyze_edge")
        async def analyze_edge(edge_data: Dict[str, Any]):
            try:
                p_dat = edge_data["parent"]
                c_dat = edge_data["child"]
                
                parent = CellDetection(
                    id=p_dat["id"], embryo_id=p_dat["embryo_id"], t=int(p_dat["t"]),
                    z=float(p_dat["z"]), y=float(p_dat["y"]), x=float(p_dat["x"]), confidence=float(p_dat["confidence"]),
                    volume_voxels=float(p_dat.get("volume_voxels", 0.0)), mean_intensity=float(p_dat.get("mean_intensity", 0.0))
                )
                child = CellDetection(
                    id=c_dat["id"], embryo_id=c_dat["embryo_id"], t=int(c_dat["t"]),
                    z=float(c_dat["z"]), y=float(c_dat["y"]), x=float(c_dat["x"]), confidence=float(c_dat["confidence"]),
                    volume_voxels=float(c_dat.get("volume_voxels", 0.0)), mean_intensity=float(c_dat.get("mean_intensity", 0.0))
                )
                
                report = self.system.anomaly_detector.score_edge(parent, child, voxel_scale=self.system.voxel_scale)
                return JSONResponse(content=report)
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))

        @self.app.get("/api/biohub/lineage_risk")
        async def lineage_risk():
            return JSONResponse(content=self.system.analyze_lineages())

        @self.app.get("/api/biohub/mip/{embryo_id}/{t}")
        async def get_mip(embryo_id: str, t: int):
            cells = [
                c.to_dict() for c in self.system.processed_cells
                if c.embryo_id == embryo_id and c.t == int(t)
            ]
            return JSONResponse(content={
                "embryo_id": embryo_id,
                "t": int(t),
                "mip_dimensions": [512, 512],
                "projection_axis": "Z",
                "cells": cells
            })

        @self.app.get("/api/biohub/crop/{embryo_id}/{t}/{z}/{y}/{x}")
        async def get_crop(embryo_id: str, t: int, z: float, y: float, x: float):
            near = []
            for c in self.system.processed_cells:
                if c.embryo_id == embryo_id and c.t == int(t):
                    d = np.linalg.norm(np.array([c.x - x, c.y - y, c.z - z]) * np.array(self.system.voxel_scale))
                    if d < 20.0:
                        near.append(c.to_dict())
            return JSONResponse(content={
                "embryo_id": embryo_id,
                "t": int(t),
                "crop_center": [x, y, z],
                "crop_size_voxels": [64, 64, 32],
                "neighbor_cells": near
            })

        @self.app.post("/api/biohub/patch/validate")
        async def validate_patch(patch_data: Dict[str, Any]):
            try:
                p_dat = patch_data["parent"]
                c_dat = patch_data["child"]
                
                p_v = p_dat.get("volume_voxels", 10.0)
                c_v = c_dat.get("volume_voxels", 10.0)
                vol_ratio = max(p_v, c_v) / min(p_v, c_v)
                
                p_pos = np.array([p_dat["x"], p_dat["y"], p_dat["z"]])
                c_pos = np.array([c_dat["x"], c_dat["y"], c_dat["z"]])
                dist = np.linalg.norm((c_pos - p_pos) * np.array(self.system.voxel_scale))
                
                valid = (vol_ratio < 3.0) and (dist < 15.0)
                reasons = []
                if vol_ratio >= 3.0:
                    reasons.append("Volume discrepancy too high")
                if dist >= 15.0:
                    reasons.append("Distance exceeds physical cell travel limits")
                    
                return JSONResponse(content={
                    "valid_correction": valid,
                    "reasons": reasons,
                    "patch_id": patch_data.get("patch_id", "patch_1")
                })
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))

        @self.app.get("/api/biohub/export_submission")
        async def export_submission():
            records = []
            for track_id, track_list in self.system.active_tracks.items():
                for idx, cell in enumerate(track_list):
                    parent_id = track_list[idx - 1].id if idx > 0 else ""
                    records.append({
                        "cell_id": cell.id,
                        "parent_id": parent_id,
                        "embryo_id": cell.embryo_id,
                        "t": cell.t,
                        "z": cell.z,
                        "y": cell.y,
                        "x": cell.x
                    })
            return JSONResponse(content={
                "submission_status": "COMPILED",
                "total_rows": len(records),
                "columns": ["cell_id", "parent_id", "embryo_id", "t", "z", "y", "x"],
                "preview_records": records[:100],
                "filename": "submission_cellops_validation.csv"
            })

    def run(self, host: str = "0.0.0.0", port: int = 8000):
        if FASTAPI_AVAILABLE:
            logger.info(f"Serving LineageRiskAPI on http://{host}:{port}")
            uvicorn.run(self.app, host=host, port=port)

# Demo run script for validation
def demo_biohub_cellops():
    """Demonstrates pipeline loop: cell segmentation, Gumbel candidate pruning, speculative tracking, and warnings."""
    logger.info("=== Biohub CellOps Intelligence System Demo ===")
    
    config = {
        "voxel_scale": (1.0, 1.0, 2.0),  # Z-anisotropy
        "max_speed_um_per_frame": 8.0,
        "max_volume_ratio": 2.5,
        "density_threshold": 4,
        "uncertainty_threshold": 0.65
    }

    class EventBus:
        def publish(self, topic: str, data: Dict[str, Any]):
            if topic == "lineage_warning":
                logger.warning(f"[TELEMETRY EVENT] Warning published on {topic}: {data['warning_type']} (Risk: {data['anomaly_details']['risk_score']:.2f})")
            elif topic == "track_link_created":
                logger.info(f"[TELEMETRY EVENT] Track link: {data['source_cell_id']} -> {data['target_cell_id']} (conf: {data['confidence']:.2f})")

    # Instantiate and test pipeline
    bus = EventBus()
    system = CellLineageIntelligenceSystem(config, bus)
    
    # Run manual frame steps to demonstrate
    logger.info("Simulating Embryonic Frame 1...")
    f1_candidates = [
        CellDetection("cell_0", "embryo_1", t=1, z=12.0, y=40.0, x=40.0, confidence=0.9, volume_voxels=100.0, mean_intensity=120.0),
        CellDetection("cell_1", "embryo_1", t=1, z=15.0, y=60.0, x=60.0, confidence=0.88, volume_voxels=110.0, mean_intensity=130.0)
    ]
    system.process_frame("embryo_1", t=1, candidates=f1_candidates)

    logger.info("Simulating Embryonic Frame 2 (normal motion)...")
    f2_candidates = [
        # Normal cell drifts
        CellDetection("cell_0_t2", "embryo_1", t=2, z=12.1, y=40.5, x=40.3, confidence=0.92, volume_voxels=102.0, mean_intensity=118.0),
        CellDetection("cell_1_t2", "embryo_1", t=2, z=14.9, y=59.5, x=60.2, confidence=0.87, volume_voxels=108.0, mean_intensity=132.0)
    ]
    system.process_frame("embryo_1", t=2, candidates=f2_candidates)

    logger.info("Simulating Embryonic Frame 3 (introducing a bad jump and a mitosis event)...")
    f3_candidates = [
        # Teleport anomaly (z jumps dramatically)
        CellDetection("cell_0_t3", "embryo_1", t=3, z=28.0, y=41.0, x=40.5, confidence=0.91, volume_voxels=104.0, mean_intensity=115.0),
        # Mitotic split (cell 1 divides into 2 daughters)
        CellDetection("cell_1_d1", "embryo_1", t=3, z=15.0, y=58.0, x=59.0, confidence=0.89, volume_voxels=54.0, mean_intensity=128.0),
        CellDetection("cell_1_d2", "embryo_1", t=3, z=14.8, y=61.0, x=61.2, confidence=0.90, volume_voxels=53.0, mean_intensity=130.0)
    ]
    system.process_frame("embryo_1", t=3, candidates=f3_candidates)

    # Compile report
    report = system.analyze_lineages()
    logger.info(f"=== Pipeline Simulation Final Report ===")
    logger.info(f"Total Detections Processed: {report['total_detections']}")
    logger.info(f"Total Active Lineages: {report['total_tracks']}")
    logger.info(f"Total Warnings Flagged: {report['total_warnings']}")
    logger.info("=========================================")

if __name__ == "__main__":
    demo_biohub_cellops()
