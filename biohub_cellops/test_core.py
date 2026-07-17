import unittest
import csv
import tempfile
from pathlib import Path
import numpy as np
from biohub_cellops.core import (
    CellDetection,
    CellTrackLink,
    CellTrajectoryPoint,
    CandidateDropout,
    NearestNeighborTracker,
    OptimizationTracker,
    SpeculativeTrackerEnsemble,
    LineageAnomalyDetector,
    SimpleKalmanFilter3D,
    CellMotionTracker,
    CellLineageIntelligenceSystem,
    KAGGLE_SUBMISSION_COLUMNS,
    KaggleSubmissionCompiler,
    KaggleSubmissionValidationError,
)

class TestBiohubCellOps(unittest.TestCase):
    def setUp(self):
        self.voxel_scale = (1.0, 1.0, 2.0)
        self.config = {
            "voxel_scale": self.voxel_scale,
            "max_speed_um_per_frame": 8.0,
            "max_volume_ratio": 2.5,
            "density_threshold": 4,
            "uncertainty_threshold": 0.65
        }

    def test_cell_detection_serialization(self):
        """Verifies that CellDetection converts to dictionaries with standard python types."""
        cell = CellDetection(
            id="cell_99",
            embryo_id="embryo_test",
            t=10,
            z=1.5,
            y=2.5,
            x=3.5,
            confidence=0.95,
            volume_voxels=150.0,
            mean_intensity=180.0,
            source_run="test"
        )
        d = cell.to_dict()
        self.assertEqual(d["id"], "cell_99")
        self.assertEqual(d["t"], 10)
        self.assertIsInstance(d["z"], float)
        self.assertIsInstance(d["t"], int)

    def test_kaggle_submission_compiler_uses_node_edge_schema(self):
        cells = [
            CellDetection("c1", "dataset_b", 0, 1.2, 2.5, 3.6, 0.9),
            CellDetection("c2", "dataset_b", 1, 2.0, 3.0, 4.0, 0.9),
            # Internal IDs may repeat in a different dataset.
            CellDetection("c1", "dataset_a", 0, 5.0, 6.0, 7.0, 0.9),
        ]
        links = [CellTrackLink("e1", "dataset_b", "c1", "c2", 0.95)]

        rows = KaggleSubmissionCompiler.compile(cells, links)

        self.assertEqual([row["id"] for row in rows], list(range(4)))
        self.assertTrue(all(list(row) == KAGGLE_SUBMISSION_COLUMNS for row in rows))
        self.assertEqual([row["row_type"] for row in rows], ["node", "node", "node", "edge"])
        self.assertEqual(len({row["node_id"] for row in rows[:3]}), 3)
        self.assertEqual(rows[-1]["dataset"], "dataset_b")
        self.assertEqual(rows[-1]["node_id"], -1)
        self.assertEqual((rows[-1]["t"], rows[-1]["z"], rows[-1]["y"], rows[-1]["x"]), (-1, -1, -1, -1))

    def test_kaggle_submission_csv_round_trip_and_sample_contract(self):
        cells = [CellDetection("c1", "dataset_a", 0, 1.0, 2.0, 3.0, 0.9)]
        rows = KaggleSubmissionCompiler.compile(cells, [])

        with tempfile.TemporaryDirectory() as tmp_dir:
            sample_path = Path(tmp_dir) / "sample_submission.csv"
            output_path = Path(tmp_dir) / "submission.csv"
            with sample_path.open("w", newline="", encoding="utf-8") as sample_file:
                csv.writer(sample_file).writerow(KAGGLE_SUBMISSION_COLUMNS)

            result = KaggleSubmissionCompiler.write_csv(rows, output_path, sample_path)
            with result.open(newline="", encoding="utf-8") as output_file:
                reader = csv.DictReader(output_file)
                written_rows = list(reader)

            self.assertEqual(reader.fieldnames, KAGGLE_SUBMISSION_COLUMNS)
            self.assertEqual(len(written_rows), 1)
            self.assertEqual(written_rows[0]["row_type"], "node")

    def test_kaggle_submission_rejects_dangling_edges(self):
        cells = [CellDetection("c1", "dataset_a", 0, 1.0, 2.0, 3.0, 0.9)]
        links = [CellTrackLink("e1", "dataset_a", "c1", "missing", 0.5)]

        with self.assertRaises(KaggleSubmissionValidationError):
            KaggleSubmissionCompiler.compile(cells, links)

    def test_candidate_dropout(self):
        """Verifies that Gumbel candidate dropout filters low-confidence candidates correctly."""
        dropout = CandidateDropout(threshold=0.5)
        # Mocking 3 candidates: high, low, high confidence
        features = np.array([
            [100.0, 120.0, 0.9, 0.1, 1.0, 1.0, 0.0, 1.0],  # fg_prob = 0.9
            [10.0, 12.0, 0.1, 0.9, 1.0, 1.0, 0.0, 1.0],    # fg_prob = 0.1
            [90.0, 100.0, 0.8, 0.2, 1.0, 1.0, 0.0, 1.0]    # fg_prob = 0.8
        ], dtype=np.float32)
        
        pruned, keep_probs = dropout.prune_candidates(features, training=False)
        self.assertEqual(keep_probs[0], 1.0)
        self.assertEqual(keep_probs[1], 0.0)
        self.assertEqual(keep_probs[2], 1.0)
        self.assertEqual(pruned[1, 2], 0.0)  # low-confidence feature gets zeroed

    def test_nearest_neighbor_tracker(self):
        """Verifies greedy closest-distance matches inside maximum physical bounds."""
        tracker = NearestNeighborTracker(max_dist_um=10.0, voxel_scale=self.voxel_scale)
        
        parents = [
            CellDetection("p0", "e1", 1, 10.0, 10.0, 10.0, 0.9),
            CellDetection("p1", "e1", 1, 20.0, 20.0, 20.0, 0.9)
        ]
        children = [
            CellDetection("c0", "e1", 2, 10.1, 10.2, 10.1, 0.9),  # very close to p0
            CellDetection("c1", "e1", 2, 25.0, 20.0, 20.0, 0.9)   # z is 25.0, dist_z = 5.0 * 2.0 = 10.0 um
        ]
        
        links = tracker.link_cells(parents, children)
        self.assertEqual(len(links), 2)
        # Verify first link
        self.assertEqual(links[0].source_cell_id, "p0")
        self.assertEqual(links[0].target_cell_id, "c0")
        # Verify second link is matched because physical dist = 10 um <= max_dist_um
        self.assertEqual(links[1].source_cell_id, "p1")
        self.assertEqual(links[1].target_cell_id, "c1")

    def test_optimization_tracker(self):
        """Verifies global bipartite matching produces standard Hungarian assignments."""
        tracker = OptimizationTracker(max_dist_um=15.0, voxel_scale=self.voxel_scale)
        
        parents = [
            CellDetection("p0", "e1", 1, 10.0, 10.0, 10.0, 0.9),
            CellDetection("p1", "e1", 1, 11.0, 10.0, 10.0, 0.9)
        ]
        children = [
            CellDetection("c0", "e1", 2, 10.1, 10.0, 10.0, 0.9),
            CellDetection("c1", "e1", 2, 11.2, 10.0, 10.0, 0.9)
        ]
        
        links = tracker.link_cells(parents, children)
        # Should link p0 -> c0 and p1 -> c1 globally
        self.assertEqual(len(links), 2)
        linked_p0 = next((l for l in links if l.source_cell_id == "p0"), None)
        linked_p1 = next((l for l in links if l.source_cell_id == "p1"), None)
        self.assertIsNotNone(linked_p0)
        self.assertEqual(linked_p0.target_cell_id, "c0")
        self.assertIsNotNone(linked_p1)
        self.assertEqual(linked_p1.target_cell_id, "c1")

    def test_speculative_ensemble_escalation(self):
        """Verifies routing of sparse vs dense frame regions to trackers."""
        fast = NearestNeighborTracker(max_dist_um=10.0, voxel_scale=self.voxel_scale)
        slow = OptimizationTracker(max_dist_um=10.0, voxel_scale=self.voxel_scale)
        ensemble = SpeculativeTrackerEnsemble(fast, slow, density_threshold=2)
        
        # Scenario A: Sparse (1 parent, 1 child) -> Fast tracker
        parents_sparse = [CellDetection("p0", "e1", 1, 10.0, 10.0, 10.0, 0.9)]
        children_sparse = [CellDetection("c0", "e1", 2, 10.1, 10.1, 10.1, 0.9)]
        links_sparse = ensemble.link_frame_region(parents_sparse, children_sparse)
        self.assertEqual(len(links_sparse), 1)
        self.assertEqual(ensemble.stats["fast_calls"], 1)
        self.assertEqual(ensemble.stats["slow_calls"], 0)
        
        # Scenario B: Dense (3 parents, 3 children) -> Escalate to Slow tracker immediately
        parents_dense = [
            CellDetection("p0", "e1", 1, 10.0, 10.0, 10.0, 0.9),
            CellDetection("p1", "e1", 1, 12.0, 12.0, 12.0, 0.9),
            CellDetection("p2", "e1", 1, 14.0, 14.0, 14.0, 0.9)
        ]
        children_dense = [
            CellDetection("c0", "e1", 2, 10.1, 10.1, 10.1, 0.9),
            CellDetection("c1", "e1", 2, 12.1, 12.1, 12.1, 0.9),
            CellDetection("c2", "e1", 2, 14.1, 14.1, 14.1, 0.9)
        ]
        links_dense = ensemble.link_frame_region(parents_dense, children_dense)
        self.assertEqual(len(links_dense), 3)
        self.assertEqual(ensemble.stats["slow_calls"], 1)

    def test_lineage_anomaly_detector_edge(self):
        """Verifies detection of impossible coordinates jumps (teleportation) and volume mismatch."""
        detector = LineageAnomalyDetector(max_speed_um_per_frame=5.0, max_volume_ratio=2.0, use_nn=False)
        
        # 1. Normal link
        parent = CellDetection("p0", "e1", 1, 10.0, 10.0, 10.0, 0.9, volume_voxels=100.0, mean_intensity=150.0)
        child_normal = CellDetection("c0", "e1", 2, 10.2, 10.2, 10.1, 0.9, volume_voxels=102.0, mean_intensity=148.0)
        report_normal = detector.score_edge(parent, child_normal, voxel_scale=self.voxel_scale)
        self.assertFalse(report_normal["anomaly_detected"])
        
        # 2. Teleportation anomaly (large spatial coordinate gap)
        child_teleport = CellDetection("c1", "e1", 2, 10.0, 10.0, 20.0, 0.9, volume_voxels=100.0, mean_intensity=150.0)
        # z diff = 10.0 * 2.0 = 20.0 um. speed = 20 um/frame > max speed (5.0)
        report_teleport = detector.score_edge(parent, child_teleport, voxel_scale=self.voxel_scale)
        self.assertTrue(report_teleport["anomaly_detected"])
        self.assertIn("Z_AXIS_TELEPORT", report_teleport["possible_errors"])
        self.assertIn("TELEPORT", report_teleport["flags"])

        # 3. Volume-shift anomaly
        child_giant = CellDetection("c2", "e1", 2, 10.1, 10.1, 10.1, 0.9, volume_voxels=350.0, mean_intensity=150.0)
        # volume ratio = 3.5 > 2.0
        report_vol = detector.score_edge(parent, child_giant, voxel_scale=self.voxel_scale)
        self.assertTrue(report_vol["anomaly_detected"])
        self.assertIn("VOLUME_SHIFT", report_vol["flags"])

    def test_lineage_anomaly_detector_division(self):
        """Verifies biological mass-conservation and daughter cell symmetry validation during mitosis."""
        detector = LineageAnomalyDetector(max_speed_um_per_frame=8.0, min_division_symmetry=0.5, use_nn=False)
        
        parent = CellDetection("p0", "e1", 1, 10.0, 10.0, 10.0, 0.9, volume_voxels=100.0)
        
        # 1. Healthy division (symmetry and conservation are preserved)
        d1_healthy = CellDetection("d1", "e1", 2, 10.5, 10.5, 10.0, 0.9, volume_voxels=51.0)
        d2_healthy = CellDetection("d2", "e1", 2, 9.5, 9.5, 10.0, 0.9, volume_voxels=49.0)
        report_healthy = detector.score_division(parent, d1_healthy, d2_healthy, voxel_scale=self.voxel_scale)
        self.assertFalse(report_healthy["anomaly_detected"])
        
        # 2. Asymmetric division
        d1_asym = CellDetection("d1", "e1", 2, 10.2, 10.2, 10.0, 0.9, volume_voxels=80.0)
        d2_asym = CellDetection("d2", "e1", 2, 9.8, 9.8, 10.0, 0.9, volume_voxels=20.0)
        # symmetry = 20/80 = 0.25 < min symmetry (0.5)
        report_asym = detector.score_division(parent, d1_asym, d2_asym, voxel_scale=self.voxel_scale)
        self.assertTrue(report_asym["anomaly_detected"])
        self.assertIn("ASYMMETRIC_DIVISION", report_asym["flags"])

    def test_cell_motion_tracker_kinetics_and_predictions(self):
        """Verifies velocity calculation, Kalman filtering, and spatial tissue flow prediction."""
        tracker = CellMotionTracker(voxel_scale=self.voxel_scale)
        
        # Add points representing constant positive motion drift of +1 physical unit per frame
        # pos increases by x=+1, y=+1, z=+0.5. Since scale is (1,1,2), physical pos increases by x=+1, y=+1, z=+1
        p1 = CellTrajectoryPoint(t=1, position=np.array([10.0, 10.0, 10.0], dtype=np.float32), volume_voxels=100.0, mean_intensity=120.0, confidence=0.9)
        p2 = CellTrajectoryPoint(t=2, position=np.array([11.0, 11.0, 10.5], dtype=np.float32), volume_voxels=100.0, mean_intensity=120.0, confidence=0.9)
        p3 = CellTrajectoryPoint(t=3, position=np.array([12.0, 12.0, 11.0], dtype=np.float32), volume_voxels=100.0, mean_intensity=120.0, confidence=0.9)
        
        tracker.add_point(track_id=1, point=p1)
        tracker.add_point(track_id=1, point=p2)
        tracker.add_point(track_id=1, point=p3)
        
        # Check derived physical velocity at t=3
        self.assertIsNotNone(p3.velocity)
        # dx = 1.0 * 1.0 = 1.0 um/f, dy = 1.0 * 1.0 = 1.0 um/f, dz = 0.5 * 2.0 = 1.0 um/f
        np.testing.assert_array_almost_equal(p3.velocity, [1.0, 1.0, 1.0], decimal=4)
        
        # Check trajectory analysis
        analysis = tracker.get_trajectory_analysis(track_id=1)
        self.assertEqual(analysis["length"], 3)
        # distance from p1 to p3 is sqrt(2^2 + 2^2 + 2^2) = sqrt(12) = 3.46 um
        self.assertAlmostEqual(analysis["total_distance_um"], 3.464, places=2)
        
        # Test position prediction using Constant Velocity
        pred_cv = tracker.predict_next(track_id=1, method="constant_velocity")
        # predicted_pos = latest.pos (12.0, 12.0, 11.0) + velocity(1.0, 1.0, 1.0)/scale(1.0, 1.0, 2.0)
        # = (12+1, 12+1, 11+0.5) = (13.0, 13.0, 11.5)
        np.testing.assert_array_almost_equal(pred_cv, [13.0, 13.0, 11.5], decimal=4)
        
        # Test spatial tissue flow calculations
        # Since cell 1 recorded its velocity as [1,1,1] around position [12,12,11] (which is [12,12,22] in microns),
        # requesting flow nearby should return [1,1,1]
        flow_vel = tracker.get_local_tissue_flow(position=np.array([12.0, 12.0, 11.0], dtype=np.float32), radius_um=10.0)
        np.testing.assert_array_almost_equal(flow_vel, [1.0, 1.0, 1.0], decimal=4)

if __name__ == "__main__":
    unittest.main()
