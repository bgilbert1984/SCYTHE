#!/usr/bin/env python3
# filepath: /home/gorelock/gemma/NerfEngine/doma_rf_motion_model.py
"""
DOMA (Dynamic Object Motion Analysis) RF Motion Model

This module implements a neural network-based motion prediction model for RF signals
using the DOMA (Dynamic Object Motion Analysis) approach. It can predict the future
positions of RF signals based on their past trajectory and characteristics.

The model can be integrated with the RF Directional Tracking system to provide
more accurate predictions of RF signal movement patterns and trajectory forecasting.
"""

import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
from scipy.spatial import distance
import logging
import os
import time
from functools import lru_cache
from typing import List, Dict, Any, Tuple, Optional

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DOMA_COORD_SCALE_M = 100.0
DEFAULT_FORECAST_STEPS = 4
DEFAULT_STEP_SECONDS = 5.0

class DOMAMotionModel(nn.Module):
    """
    Dynamic Object Motion Analysis (DOMA) model for RF signal motion prediction.

    This neural network model predicts the future position and motion transform
    of RF signals based on their current position and temporal features.
    """
    def __init__(self, input_dim=4, hidden_dim=64, dropout_rate=0.2):
        """
        Initialize the DOMA Motion Model.

        Args:
            input_dim: Dimension of input features (position + time)
            hidden_dim: Dimension of hidden layers
            dropout_rate: Dropout rate for regularization
        """
        super(DOMAMotionModel, self).__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, hidden_dim)
        self.fc3 = nn.Linear(hidden_dim, 6)  # Outputs affine transformation (rotation + translation vector)
        self.dropout = nn.Dropout(dropout_rate)
        self.activation = nn.SiLU()  # SiLU (Swish) activation function
        self.batch_norm1 = nn.BatchNorm1d(hidden_dim)
        self.batch_norm2 = nn.BatchNorm1d(hidden_dim)

    def forward(self, x):
        """
        Forward pass through the network.

        Args:
            x: Input tensor containing position and time information
               Expected shape: [batch_size, input_dim] where input_dim is typically 4
               (3D position + time)

        Returns:
            Tensor containing predicted affine transformation parameters
        """
        # Check if we're in training or evaluation mode for batch normalization
        if x.dim() == 1:
            x = x.unsqueeze(0)  # Add batch dimension for single sample

        x = self.activation(self.batch_norm1(self.fc1(x)))
        x = self.dropout(x)
        x = self.activation(self.batch_norm2(self.fc2(x)))
        x = self.dropout(x)
        return self.fc3(x)

    def predict_next_position(self, position, time_step):
        """
        Predict the next position of an RF signal.

        Args:
            position: Current 3D position as [x, y, z]
            time_step: Current time step value

        Returns:
            Predicted next position as [x, y, z]
        """
        # Prepare input for the model
        input_data = torch.tensor(np.hstack((position, [time_step])), dtype=torch.float32)

        # Make prediction
        with torch.no_grad():
            self.eval()  # Set model to evaluation mode
            prediction = self(input_data)

        # Extract affine transformation parameters
        # In this simplified version, we're using the first 3 values as the next position
        # and ignoring the affine transformation parameters
        next_position = prediction.reshape(-1)[:3].detach().cpu().numpy()

        return next_position

    def apply_motion_transform(self, positions, time_steps):
        """
        Apply the predicted motion transform to a set of positions.

        Args:
            positions: Array of 3D positions, shape [n, 3]
            time_steps: Array of time steps, shape [n]

        Returns:
            Array of transformed positions
        """
        # Convert to numpy if tensors
        if isinstance(positions, torch.Tensor):
            positions = positions.detach().numpy()
        if isinstance(time_steps, torch.Tensor):
            time_steps = time_steps.detach().numpy()

        # Prepare input data
        n = len(positions)
        data = np.hstack((positions, time_steps.reshape(-1, 1)))

        # Create tensor input
        tensor_data = torch.tensor(data, dtype=torch.float32)

        # Make predictions
        with torch.no_grad():
            self.eval()  # Set model to evaluation mode
            predictions = self(tensor_data)

        # Convert predictions to numpy
        predictions = predictions.detach().numpy()

        # Extract next positions (first 3 values of each prediction)
        next_positions = predictions[:, :3]

        return next_positions

    def save(self, path="doma_rf_motion_model.pth"):
        """Save the model to a file"""
        torch.save({
            'model_state_dict': self.state_dict(),
            'input_dim': self.fc1.in_features,
            'hidden_dim': self.fc1.out_features
        }, path)
        logger.info(f"Model saved to {path}")

    @classmethod
    def load(cls, path="doma_rf_motion_model.pth", device='cpu'):
        """Load the model from a file"""
        if not os.path.exists(path):
            logger.error(f"Model file {path} not found")
            return None

        checkpoint = torch.load(path, map_location=device)
        model = cls(
            input_dim=checkpoint.get('input_dim', 4),
            hidden_dim=checkpoint.get('hidden_dim', 64)
        )
        model.load_state_dict(checkpoint['model_state_dict'])
        model.to(device)
        model.eval()
        logger.info(f"Model loaded from {path}")
        return model


def _meters_per_degree_lon(lat_deg: float) -> float:
    return 111_320.0 * max(np.cos(np.radians(lat_deg)), 1e-6)


def _relative_xyz_m(
    lat: float,
    lon: float,
    alt_m: float,
    *,
    ref_lat: float,
    ref_lon: float,
    ref_alt_m: float,
) -> np.ndarray:
    return np.array(
        [
            (lon - ref_lon) * _meters_per_degree_lon(ref_lat),
            (lat - ref_lat) * 111_320.0,
            alt_m - ref_alt_m,
        ],
        dtype=np.float32,
    )


def _xyz_to_location(
    xyz_m: np.ndarray,
    *,
    ref_lat: float,
    ref_lon: float,
    ref_alt_m: float,
) -> Dict[str, float]:
    return {
        "lat": float(ref_lat + (float(xyz_m[1]) / 111_320.0)),
        "lon": float(ref_lon + (float(xyz_m[0]) / _meters_per_degree_lon(ref_lat))),
        "alt_m": float(ref_alt_m + float(xyz_m[2])),
    }


def _normalize_motion_history(history: List[Dict[str, Any]]) -> List[Dict[str, float]]:
    normalized: List[Dict[str, float]] = []
    for point in history or []:
        if not isinstance(point, dict):
            continue
        try:
            lat = float(point["lat"])
            lon = float(point["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        timestamp = float(point.get("timestamp", time.time()) or time.time())
        normalized.append(
            {
                "lat": lat,
                "lon": lon,
                "alt_m": float(point.get("alt_m", 0.0) or 0.0),
                "timestamp": timestamp,
                "confidence": float(point.get("confidence", 0.6) or 0.6),
            }
        )
    normalized.sort(key=lambda item: item["timestamp"])
    if not normalized:
        return []
    deduped: List[Dict[str, float]] = []
    for point in normalized:
        previous = deduped[-1] if deduped else None
        if previous and all(
            abs(point[key] - previous[key]) < 1e-6
            for key in ("lat", "lon", "alt_m", "timestamp")
        ):
            continue
        deduped.append(point)
    return deduped[-6:]


def _estimate_velocity_xyz_mps(
    history: List[Dict[str, float]],
    *,
    ref_lat: float,
    ref_lon: float,
    ref_alt_m: float,
) -> np.ndarray:
    if len(history) < 2:
        return np.zeros(3, dtype=np.float32)
    velocities: List[np.ndarray] = []
    for previous, current in zip(history[:-1], history[1:]):
        dt = max(0.5, float(current["timestamp"] - previous["timestamp"]))
        prev_xyz = _relative_xyz_m(
            previous["lat"],
            previous["lon"],
            previous.get("alt_m", 0.0),
            ref_lat=ref_lat,
            ref_lon=ref_lon,
            ref_alt_m=ref_alt_m,
        )
        curr_xyz = _relative_xyz_m(
            current["lat"],
            current["lon"],
            current.get("alt_m", 0.0),
            ref_lat=ref_lat,
            ref_lon=ref_lon,
            ref_alt_m=ref_alt_m,
        )
        velocities.append((curr_xyz - prev_xyz) / dt)
    if not velocities:
        return np.zeros(3, dtype=np.float32)
    return np.mean(np.stack(velocities[-3:], axis=0), axis=0).astype(np.float32)


@lru_cache(maxsize=4)
def load_default_doma_model(path: Optional[str] = None, device: str = "cpu") -> Optional["DOMAMotionModel"]:
    model_path = path or os.environ.get("DOMA_MODEL_PATH", "doma_rf_motion_model.pth")
    try:
        return DOMAMotionModel.load(model_path, device=device)
    except Exception as exc:
        logger.warning("Unable to load DOMA motion model from %s: %s", model_path, exc)
        return None


def predict_next_states(
    history: List[Dict[str, Any]],
    *,
    model: Optional["DOMAMotionModel"] = None,
    steps: int = DEFAULT_FORECAST_STEPS,
    step_seconds: float = DEFAULT_STEP_SECONDS,
    model_weight: float = 0.35,
) -> List[Dict[str, Any]]:
    """
    Predict future geo states from timestamped lat/lon/alt observations.

    The DOMA model is used opportunistically and blended with a kinematic estimate
    so forecasts remain stable even when the neural model is missing or uncertain.
    """
    normalized = _normalize_motion_history(history)
    if not normalized:
        return []

    latest = normalized[-1]
    ref_lat = latest["lat"]
    ref_lon = latest["lon"]
    ref_alt_m = latest.get("alt_m", 0.0)
    current_xyz = _relative_xyz_m(
        latest["lat"],
        latest["lon"],
        latest.get("alt_m", 0.0),
        ref_lat=ref_lat,
        ref_lon=ref_lon,
        ref_alt_m=ref_alt_m,
    )
    velocity_xyz = _estimate_velocity_xyz_mps(
        normalized,
        ref_lat=ref_lat,
        ref_lon=ref_lon,
        ref_alt_m=ref_alt_m,
    )

    if model is None:
        model = load_default_doma_model()

    base_confidence = max(0.25, min(0.98, normalized[-1].get("confidence", 0.6)))
    base_uncertainty_m = 18.0 if len(normalized) >= 3 else 36.0
    predicted_states: List[Dict[str, Any]] = []
    model_mode = "kinematic"

    for step in range(1, max(1, steps) + 1):
        linear_xyz = current_xyz + velocity_xyz * float(step_seconds)
        next_xyz = linear_xyz
        step_mode = "kinematic"

        if model is not None:
            try:
                time_feature = ((latest["timestamp"] + step * step_seconds) % 1000.0) / 1000.0
                neural_input = current_xyz / DOMA_COORD_SCALE_M
                neural_xyz = np.asarray(
                    model.predict_next_position(neural_input, time_feature),
                    dtype=np.float32,
                ).reshape(-1)[:3] * DOMA_COORD_SCALE_M
                if np.all(np.isfinite(neural_xyz)):
                    max_step_m = max(
                        40.0,
                        float(np.linalg.norm(velocity_xyz)) * float(step_seconds) * 4.0 + 80.0,
                    )
                    if float(np.linalg.norm(neural_xyz - current_xyz)) <= max_step_m:
                        next_xyz = (linear_xyz * (1.0 - model_weight)) + (neural_xyz * model_weight)
                        step_mode = "doma_blend"
            except Exception as exc:
                logger.debug("DOMA forecast step failed: %s", exc)

        velocity_xyz = ((next_xyz - current_xyz) / max(float(step_seconds), 0.5)).astype(np.float32)
        current_xyz = next_xyz.astype(np.float32)
        speed_mps = float(np.linalg.norm(velocity_xyz))
        location = _xyz_to_location(
            current_xyz,
            ref_lat=ref_lat,
            ref_lon=ref_lon,
            ref_alt_m=ref_alt_m,
        )
        predicted_states.append(
            {
                "step": step,
                "time_offset_s": round(float(step * step_seconds), 3),
                "timestamp": round(float(latest["timestamp"] + (step * step_seconds)), 3),
                "location": {
                    "lat": round(location["lat"], 7),
                    "lon": round(location["lon"], 7),
                    "alt_m": round(location["alt_m"], 2),
                },
                "confidence": round(max(0.12, min(0.99, base_confidence * (0.92 ** step))), 4),
                "radius_m": round(min(600.0, base_uncertainty_m + step * (10.0 + speed_mps * 0.75)), 2),
                "speed_mps": round(speed_mps, 3),
                "model": step_mode,
            }
        )
        model_mode = step_mode

    if predicted_states:
        predicted_states[-1]["model"] = model_mode
    return predicted_states


class DOMATrainer:
    """
    Trainer for the DOMA Motion Model.

    This class handles the training and evaluation of the DOMA model using
    RF signal trajectory data.
    """
    def __init__(
        self,
        model: DOMAMotionModel,
        learning_rate: float = 0.001,
        weight_decay: float = 1e-5,
        save_path: str = "doma_rf_motion_model.pth"
    ):
        """
        Initialize the DOMA trainer.

        Args:
            model: The DOMA model to train
            learning_rate: Learning rate for optimization
            weight_decay: Weight decay for regularization
            save_path: Path to save the trained model
        """
        self.model = model
        self.save_path = save_path
        self.criterion = nn.MSELoss()
        self.optimizer = optim.Adam(
            model.parameters(),
            lr=learning_rate,
            weight_decay=weight_decay
        )
        self.scheduler = optim.lr_scheduler.ReduceLROnPlateau(
            self.optimizer,
            patience=20,
            factor=0.5,
            verbose=True
        )

        # Training metrics
        self.best_loss = float('inf')
        self.training_history = {
            "losses": [],
            "val_losses": []
        }

    def train_epoch(self, data, targets):
        """
        Train the model for one epoch.

        Args:
            data: Input data tensor, shape [n, input_dim]
            targets: Target data tensor, shape [n, 6]

        Returns:
            Average loss for the epoch
        """
        self.model.train()  # Set model to training mode
        self.optimizer.zero_grad()

        # Forward pass
        predictions = self.model(data)
        loss = self.criterion(predictions, targets)

        # Backward pass
        loss.backward()
        self.optimizer.step()

        return loss.item()

    def validate(self, data, targets):
        """
        Validate the model.

        Args:
            data: Input data tensor, shape [n, input_dim]
            targets: Target data tensor, shape [n, 6]

        Returns:
            Validation loss
        """
        self.model.eval()  # Set model to evaluation mode
        with torch.no_grad():
            predictions = self.model(data)
            loss = self.criterion(predictions, targets)
        return loss.item()

    def train(
        self,
        train_data,
        train_targets,
        val_data=None,
        val_targets=None,
        num_epochs=500,
        batch_size=32,
        log_interval=50
    ):
        """
        Train the model.

        Args:
            train_data: Training data tensor, shape [n, input_dim]
            train_targets: Training targets tensor, shape [n, 6]
            val_data: Validation data tensor (optional)
            val_targets: Validation targets tensor (optional)
            num_epochs: Number of training epochs
            batch_size: Batch size for training
            log_interval: Interval for logging training progress

        Returns:
            Training history
        """
        logger.info(f"Starting training for {num_epochs} epochs")

        n_samples = len(train_data)
        indices = np.arange(n_samples)

        for epoch in range(num_epochs):
            # Shuffle data for each epoch
            np.random.shuffle(indices)
            shuffled_data = train_data[indices]
            shuffled_targets = train_targets[indices]

            # Train in batches
            epoch_loss = 0
            num_batches = int(np.ceil(n_samples / batch_size))

            for batch in range(num_batches):
                start_idx = batch * batch_size
                end_idx = min((batch + 1) * batch_size, n_samples)

                batch_data = shuffled_data[start_idx:end_idx]
                batch_targets = shuffled_targets[start_idx:end_idx]

                batch_loss = self.train_epoch(batch_data, batch_targets)
                epoch_loss += batch_loss

            # Calculate average epoch loss
            avg_epoch_loss = epoch_loss / num_batches
            self.training_history["losses"].append(avg_epoch_loss)

            # Validate if validation data is provided
            if val_data is not None and val_targets is not None:
                val_loss = self.validate(val_data, val_targets)
                self.training_history["val_losses"].append(val_loss)

                # Update learning rate based on validation loss
                self.scheduler.step(val_loss)

                # Save best model
                if val_loss < self.best_loss:
                    self.best_loss = val_loss
                    self.model.save(self.save_path)
                    if epoch % log_interval == 0:
                        logger.info(f"Epoch {epoch}: New best model saved with val_loss {val_loss:.6f}")
            else:
                # Save based on training loss if no validation data
                if avg_epoch_loss < self.best_loss:
                    self.best_loss = avg_epoch_loss
                    self.model.save(self.save_path)

            # Log progress
            if epoch % log_interval == 0:
                log_msg = f"Epoch {epoch}/{num_epochs}: Loss = {avg_epoch_loss:.6f}"
                if val_data is not None:
                    log_msg += f", Val Loss = {val_loss:.6f}"
                logger.info(log_msg)

        logger.info("DOMA-based RF motion model training complete.")
        logger.info(f"Best loss achieved: {self.best_loss:.6f}")
        logger.info(f"Model saved to {self.save_path}")

        return self.training_history


def generate_synthetic_data(
    num_points=1000,
    time_range=(0, 10),
    position_range=(-10, 10),
    velocity_scale=0.1,
    noise_scale=0.02,
    random_seed=None
):
    """
    Generate synthetic RF point data for training.

    Args:
        num_points: Number of data points to generate
        time_range: Range of time steps as (min, max)
        position_range: Range of positions as (min, max)
        velocity_scale: Scale factor for velocities
        noise_scale: Scale factor for random noise
        random_seed: Random seed for reproducibility

    Returns:
        data: Input data array with positions and time steps
        targets: Target data array with next positions and affine parameters
    """
    if random_seed is not None:
        np.random.seed(random_seed)

    # Generate time steps
    time_steps = np.linspace(time_range[0], time_range[1], num_points)

    # Generate 3D positions
    positions = np.random.uniform(
        position_range[0],
        position_range[1],
        (num_points, 3)
    )

    # Add some structure to the positions (e.g., circular motion)
    positions[:, 0] += 5 * np.sin(0.5 * time_steps)
    positions[:, 1] += 3 * np.cos(0.3 * time_steps)
    positions[:, 2] += 2 * np.sin(0.2 * time_steps) * np.cos(0.1 * time_steps)

    # Add some random noise
    positions += np.random.normal(0, noise_scale, positions.shape)

    # Generate velocities (derivatives of position)
    velocities = np.zeros_like(positions)
    velocities[1:] = positions[1:] - positions[:-1]
    velocities[0] = velocities[1]

    # Scale velocities
    velocities *= velocity_scale

    # Prepare input data: positions + time
    data = np.hstack((positions, time_steps.reshape(-1, 1)))

    # Prepare target data: next positions + affine parameters (simplified)
    next_positions = positions + velocities
    targets = np.hstack((next_positions, np.zeros((num_points, 3))))  # Simplified affine params

    return data, targets


def train_doma_model(
    num_points=1000,
    input_dim=4,
    hidden_dim=64,
    num_epochs=500,
    batch_size=32,
    save_path="doma_rf_motion_model.pth",
    validation_split=0.2,
    random_seed=42
):
    """
    Train a DOMA motion model using synthetic or real data.

    Args:
        num_points: Number of data points to generate
        input_dim: Input dimension (positions + time)
        hidden_dim: Hidden layer dimension
        num_epochs: Number of training epochs
        batch_size: Batch size for training
        save_path: Path to save the trained model
        validation_split: Fraction of data to use for validation
        random_seed: Random seed for reproducibility

    Returns:
        Trained DOMA model
    """
    logger.info("Generating synthetic training data...")
    data, targets = generate_synthetic_data(
        num_points=num_points,
        random_seed=random_seed
    )

    # Split data into training and validation sets
    if validation_split > 0:
        split_idx = int(num_points * (1 - validation_split))
        train_data = data[:split_idx]
        train_targets = targets[:split_idx]
        val_data = data[split_idx:]
        val_targets = targets[split_idx:]
    else:
        train_data = data
        train_targets = targets
        val_data = None
        val_targets = None

    # Convert to PyTorch tensors
    tensor_train_data = torch.tensor(train_data, dtype=torch.float32)
    tensor_train_targets = torch.tensor(train_targets, dtype=torch.float32)

    if val_data is not None:
        tensor_val_data = torch.tensor(val_data, dtype=torch.float32)
        tensor_val_targets = torch.tensor(val_targets, dtype=torch.float32)
    else:
        tensor_val_data = None
        tensor_val_targets = None

    # Initialize model
    model = DOMAMotionModel(input_dim=input_dim, hidden_dim=hidden_dim)

    # Initialize trainer
    trainer = DOMATrainer(
        model=model,
        learning_rate=0.001,
        save_path=save_path
    )

    # Train model
    logger.info(f"Training DOMA model with {num_points} data points...")
    trainer.train(
        train_data=tensor_train_data,
        train_targets=tensor_train_targets,
        val_data=tensor_val_data,
        val_targets=tensor_val_targets,
        num_epochs=num_epochs,
        batch_size=batch_size
    )

    return model


def main():
    """Main function to train and save a DOMA motion model"""
    # Train a DOMA model with default parameters
    train_doma_model(
        num_points=2000,
        hidden_dim=128,
        num_epochs=500,
        batch_size=64,
        save_path="doma_rf_motion_model.pth"
    )

    # Load and test the trained model
    model = DOMAMotionModel.load("doma_rf_motion_model.pth")

    if model is not None:
        # Test a simple prediction
        test_position = np.array([1.0, 2.0, 3.0])
        test_time = 0.5
        prediction = model.predict_next_position(test_position, test_time)

        logger.info(f"Test prediction:")
        logger.info(f"Input position: {test_position}")
        logger.info(f"Predicted next position: {prediction}")


if __name__ == "__main__":
    main()
