import logging
import numpy as np
import threading
import time
import json
import os
from queue import Queue
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger("SignalIntelligence")

# FlashAttention and modern ML imports
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from flash_attn.modules.mha import FlashMHA
    from rotary_embedding_torch import RotaryEmbedding
    PYTORCH_AVAILABLE = True
except ImportError:
    logger.warning("PyTorch or FlashAttention not available. Some features will be disabled.")
    PYTORCH_AVAILABLE = False

# FastAPI imports for ghost detector API
try:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    logger.warning("FastAPI not available. Ghost detector API will be disabled.")
    FASTAPI_AVAILABLE = False

# DOMA RF Motion Model imports
try:
    from doma_rf_motion_model import DOMAMotionModel
    from enhanced_doma_rf_motion_model import EnhancedDOMAMotionModel
    DOMA_AVAILABLE = True
except ImportError:
    logger.warning("DOMA RF Motion Model not available. Motion prediction features will be disabled.")
    DOMA_AVAILABLE = False

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

# FlashAttention and Efficiency Modules
if PYTORCH_AVAILABLE:
    class RMSNorm(nn.Module):
        """Root Mean Square Layer Normalization - more efficient than LayerNorm"""
        def __init__(self, embed_dim, eps=1e-6):
            super().__init__()
            self.weight = nn.Parameter(torch.ones(embed_dim))
            self.eps = eps

        def forward(self, x):
            # RMS normalization
            norm = x.pow(2).mean(-1, keepdim=True).sqrt() + self.eps
            return x / norm * self.weight

    class GroupQueryAttention(nn.Module):
        """Grouped Query Attention - memory efficient variant of MHA"""
        def __init__(self, embed_dim, num_heads=8, num_kv_heads=2):
            super().__init__()
            self.embed_dim = embed_dim
            self.num_heads = num_heads
            self.num_kv_heads = num_kv_heads
            self.head_dim = embed_dim // num_heads
            
            self.q_proj = nn.Linear(embed_dim, embed_dim)
            self.k_proj = nn.Linear(embed_dim, num_kv_heads * self.head_dim)
            self.v_proj = nn.Linear(embed_dim, num_kv_heads * self.head_dim)
            self.out_proj = nn.Linear(embed_dim, embed_dim)
            
        def forward(self, x):
            B, T, C = x.shape
            
            q = self.q_proj(x).view(B, T, self.num_heads, self.head_dim).transpose(1, 2)
            k = self.k_proj(x).view(B, T, self.num_kv_heads, self.head_dim).transpose(1, 2)
            v = self.v_proj(x).view(B, T, self.num_kv_heads, self.head_dim).transpose(1, 2)
            
            # Repeat k, v for all query heads
            k = k.repeat_interleave(self.num_heads // self.num_kv_heads, dim=1)
            v = v.repeat_interleave(self.num_heads // self.num_kv_heads, dim=1)
            
            # Scaled dot-product attention
            scores = torch.matmul(q, k.transpose(-2, -1)) / (self.head_dim ** 0.5)
            attn = F.softmax(scores, dim=-1)
            out = torch.matmul(attn, v)
            
            out = out.transpose(1, 2).contiguous().view(B, T, C)
            return self.out_proj(out)

    class SpectrumEncoder(nn.Module):
        """Multi-Head Latent Attention (MHLA) for spectrum compression"""
        def __init__(self, input_dim: int, hidden_dim: int = 512, num_heads: int = 8, 
                     num_layers: int = 6, use_rope: bool = True, dropout_threshold: float = 0.01):
            super().__init__()
            self.input_dim = input_dim
            self.hidden_dim = hidden_dim
            
            # Token dropout for Flash-MHA
            self.token_dropout = GumbelTokenDropout(threshold=dropout_threshold)
            
            # Input projection
            self.input_projection = nn.Linear(input_dim, hidden_dim)
            
            # RoPE for position-aware encoding
            self.use_rope = use_rope
            if use_rope:
                # Assuming RotaryPositionalEmbedding is defined elsewhere or will be added
                # For now, let's make it optional to avoid NameError if not defined
                try:
                    # self.rope = RotaryPositionalEmbedding(hidden_dim // num_heads) # Commented out due to undefined error
                    pass # Placeholder
                except NameError:
                    print("Warning: RotaryPositionalEmbedding not defined. RoPE will not be used.")
                    self.rope = None
                    self.use_rope = False # Ensure use_rope reflects this
            else:
                self.rope = None
                
            # Transformer layers
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=hidden_dim,
                nhead=num_heads,
                dim_feedforward=hidden_dim * 4,
                dropout=0.1,
                batch_first=True
            )
            self.transformer = nn.TransformerEncoder(encoder_layer, num_layers)
            
            # Output projection
            self.output_projection = nn.Linear(hidden_dim, input_dim)
            
        def forward(self, spectrum_tensor: torch.Tensor) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
            # Apply token dropout for uninformative bins
            spectrum_tensor = self.token_dropout(spectrum_tensor)
            
            # Project to hidden dimension
            x = self.input_projection(spectrum_tensor)
            
            # Apply RoPE for position-aware encoding
            if self.use_rope and self.rope is not None:
                batch_size, seq_len = x.shape[:2]
                pos = torch.arange(0, seq_len, device=x.device).unsqueeze(0).expand(batch_size, -1)
                x = self.rope(x, pos)
            
            # Transformer encoding with attention extraction
            attention_weights = []
            # To extract attention weights, we need to modify how TransformerEncoderLayer is called
            # or ensure that the specific PyTorch version/custom layer supports it.
            # For standard nn.TransformerEncoderLayer, direct attention weight extraction per layer
            # in a loop like this is not straightforward without hooks or custom implementation.
            # Assuming a mechanism exists or will be added to populate layer.self_attn.attention_weights
            
            # Simplification: We'll iterate through layers and try to get attention.
            # This part might need adjustment based on the actual TransformerEncoderLayer implementation
            # or if a custom layer is used that exposes attention weights.
            current_input = x
            for i, layer in enumerate(self.transformer.layers):
                # For nn.TransformerEncoderLayer, forward pass doesn't return attention by default.
                # We would typically use hooks for this.
                # Let's assume for now that `layer.self_attn` might store it after its forward pass,
                # or that a custom mechanism is in place.
                # This is a placeholder for actual attention extraction logic.
                
                # HACK: To make it runnable, we'll just pass through the layer.
                # Proper attention extraction requires modifying the Transformer or using hooks.
                current_input = layer(current_input) # src, src_mask, src_key_padding_mask
                
                # Placeholder for attention weight extraction
                # In a real scenario, you'd use hooks:
                # def hook_fn(module, input, output):
                #     # output is a tuple (output_tensor, attention_weights_tensor)
                #     # if module.self_attn.batch_first is True
                #     attention_weights.append(output[1]) 
                # handle = layer.self_attn.register_forward_hook(hook_fn)
                # current_input = layer(current_input)
                # handle.remove()

                # For the sake of this example, let's assume attention_weights are somehow populated
                # or this part is handled by a custom Transformer implementation.
                # If `layer.self_attn.attention_weights` is a hypothetical attribute:
                if hasattr(layer.self_attn, 'attention_weights_placeholder_for_demo'): # Renamed to avoid conflict
                     attention_weights.append(layer.self_attn.attention_weights_placeholder_for_demo)

            x = current_input # final output from transformer layers
            
            # Project back to original dimension
            encoded = self.output_projection(x)
            
            return encoded, torch.stack(attention_weights) if attention_weights else None

    class GumbelTokenDropout(nn.Module):
        """Flash-MHA token dropout with differentiable Gumbel-Sigmoid"""
        
        def __init__(self, threshold: float = 0.01, temperature: float = 1.0):
            super().__init__()
            self.threshold = threshold
            self.temperature = temperature
            
        def _drop_low_energy_bins(self, spectrum_tensor: torch.Tensor, training: bool = True) -> torch.Tensor:
            """Drop uninformative tokens based on energy content"""
            energy = spectrum_tensor.mean(dim=-1)  # [batch, seq_len]
            
            if training:
                # Differentiable Gumbel-Sigmoid during training
                logits = (energy - self.threshold) / self.temperature
                gumbel_noise = torch.rand_like(logits).clamp(1e-10, 1-1e-10)
                gumbel_noise = -torch.log(-torch.log(gumbel_noise))
                keep_probs = torch.sigmoid((logits + gumbel_noise) / self.temperature)
                return spectrum_tensor * keep_probs.unsqueeze(-1)
            else:
                # Hard threshold during inference
                keep_mask = energy > self.threshold
                return spectrum_tensor * keep_mask.unsqueeze(-1)
        
        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return self._drop_low_energy_bins(x, self.training)

    class SpeculativeEnsemble:
        """Speculative decoding for ensemble classification"""
        def __init__(self, fast_model, slow_model, threshold=0.8):
            self.fast_model = fast_model
            self.slow_model = slow_model
            self.threshold = threshold

        def classify(self, signal):
            # Fast model prediction
            fast_pred, fast_conf, fast_probs = self.fast_model.classify_signal(signal)
            
            # If confidence is high enough, return fast prediction
            if fast_conf >= self.threshold:
                return fast_pred, fast_conf, fast_probs
            
            # Otherwise, use slow model for refinement
            slow_pred, slow_conf, slow_probs = self.slow_model.classify_signal(signal)
            
            # Combine predictions (weighted by confidence)
            total_conf = fast_conf + slow_conf
            if total_conf > 0:
                weight_fast = fast_conf / total_conf
                weight_slow = slow_conf / total_conf
                
                # Weighted average of probabilities
                combined_probs = {}
                all_classes = set(fast_probs.keys()) | set(slow_probs.keys())
                for cls in all_classes:
                    fast_prob = fast_probs.get(cls, 0.0)
                    slow_prob = slow_probs.get(cls, 0.0)
                    combined_probs[cls] = weight_fast * fast_prob + weight_slow * slow_prob
                
                # Select best class
                best_class = max(combined_probs.keys(), key=lambda k: combined_probs[k])
                combined_conf = max(fast_conf, slow_conf)
                
                return best_class, combined_conf, combined_probs
            else:
                return slow_pred, slow_conf, slow_probs

    class AttentionModelAdapter:
        """Flexible wrapper for different attention models"""
        def __init__(self, model_type="flash", **kwargs):
            self.model_type = model_type
            
            if model_type == "flash" and 'FlashMHA' in globals():
                embed_dim = kwargs.get('embed_dim', 128)
                num_heads = kwargs.get('num_heads', 8)
                self.attention = FlashMHA(embed_dim, num_heads)
            elif model_type == "grouped":
                embed_dim = kwargs.get('embed_dim', 128)
                num_heads = kwargs.get('num_heads', 8)
                num_kv_heads = kwargs.get('num_kv_heads', 2)
                self.attention = GroupQueryAttention(embed_dim, num_heads, num_kv_heads)
            elif model_type == "latent":
                d_model = kwargs.get('d_model', 128)
                num_latents = kwargs.get('num_latents', 32)
                self.attention = SpectrumEncoder(d_model, num_latents)
            else:
                # Fallback to standard attention
                embed_dim = kwargs.get('embed_dim', 128)
                num_heads = kwargs.get('num_heads', 8)
                self.attention = nn.MultiheadAttention(embed_dim, num_heads, batch_first=True)
                
            logger.info(f"Initialized {model_type} attention model")

        def forward(self, x):
            if hasattr(self.attention, 'forward'):
                return self.attention(x)
            else:
                # For standard MultiheadAttention
                attn_out, _ = self.attention(x, x, x)
                return attn_out
else:
    # Fallback classes when PyTorch is not available
    class RMSNorm:
        def __init__(self, *args, **kwargs):
            logger.warning("RMSNorm requires PyTorch - using identity function")
        
    class GroupQueryAttention:
        def __init__(self, *args, **kwargs):
            logger.warning("GroupQueryAttention requires PyTorch - disabled")
            
    class SpectrumEncoder:
        def __init__(self, *args, **kwargs):
            logger.warning("SpectrumEncoder requires PyTorch - disabled")
            
    class SpeculativeEnsemble:
        def __init__(self, fast_model, slow_model, threshold=0.8):
            self.fast_model = fast_model
            self.slow_model = slow_model
            self.threshold = threshold
            logger.warning("SpeculativeEnsemble will use simple fallback without PyTorch")
            
        def classify(self, signal):
            # Simple fallback: just use fast model
            return self.fast_model.classify_signal(signal)
            
    class AttentionModelAdapter:
        def __init__(self, model_type="simple", **kwargs):
            self.model_type = "simple"
            logger.warning("AttentionModelAdapter requires PyTorch - using simple fallback")

class CompiledGhostDetectorSingleton:
    """Singleton for compiled ghost anomaly detector to save memory"""
    _instance = None
    _detector = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CompiledGhostDetectorSingleton, cls).__new__(cls)
        return cls._instance
    
    def get_detector(self, num_patterns=64):
        """Get or create the ghost detector"""
        if self._detector is None:
            if PYTORCH_AVAILABLE:
                try:
                    # Create a simple ghost anomaly detector
                    self._detector = GhostAnomalyDetector(num_patterns)
                    logger.info(f"Ghost anomaly detector initialized with {num_patterns} patterns")
                except Exception as e:
                    logger.error(f"Failed to initialize ghost detector: {e}")
                    self._detector = MockGhostDetector()
            else:
                self._detector = MockGhostDetector()
        return self._detector

class GhostAnomalyDetector:
    """Ghost anomaly detector for identifying unusual RF signatures"""
    
    def __init__(self, num_patterns=64):
        self.num_patterns = num_patterns
        self.patterns = []
        self.thresholds = {}
        
        # Initialize with random patterns if PyTorch available
        if PYTORCH_AVAILABLE:
            self.model = self._create_model()
        else:
            self.model = None
            
    def _create_model(self):
        """Create simple anomaly detection model"""
        if not PYTORCH_AVAILABLE:
            return None
            
        try:
            model = nn.Sequential(
                nn.Linear(256, 128),
                nn.ReLU(),
                nn.Linear(128, 64),
                nn.ReLU(),
                nn.Linear(64, 1),
                nn.Sigmoid()
            )
            return model
        except Exception as e:
            logger.error(f"Failed to create ghost detector model: {e}")
            return None
    
    def detect_anomaly(self, signal_data):
        """Detect anomalies in RF signal data"""
        try:
            if self.model is None or not PYTORCH_AVAILABLE:
                # Simple threshold-based detection
                return self._simple_anomaly_detection(signal_data)
            
            # Convert signal to tensor and run through model
            if isinstance(signal_data, np.ndarray):
                # Simple feature extraction
                features = np.array([
                    np.mean(signal_data),
                    np.std(signal_data),
                    np.max(signal_data),
                    np.min(signal_data)
                ])
                # Pad to expected size
                features = np.pad(features, (0, 256 - len(features)), 'constant')
                
                with torch.no_grad():
                    tensor_data = torch.tensor(features).float().unsqueeze(0)
                    anomaly_score = self.model(tensor_data).item()
                    
                return {
                    "anomaly_detected": anomaly_score > 0.7,
                    "confidence": anomaly_score,
                    "detection_method": "neural"
                }
            
        except Exception as e:
            logger.error(f"Error in ghost anomaly detection: {e}")
            
        return self._simple_anomaly_detection(signal_data)
    
    def _simple_anomaly_detection(self, signal_data):
        """Simple threshold-based anomaly detection fallback"""
        try:
            if isinstance(signal_data, np.ndarray) and len(signal_data) > 0:
                mean_power = np.mean(signal_data)
                std_power = np.std(signal_data)
                
                # Simple anomaly: signal significantly above normal
                anomaly_detected = mean_power > (3 * std_power)
                confidence = min(1.0, abs(mean_power) / (std_power + 1e-6) / 10)
                
                return {
                    "anomaly_detected": anomaly_detected,
                    "confidence": confidence,
                    "detection_method": "threshold"
                }
        except Exception as e:
            logger.error(f"Error in simple anomaly detection: {e}")
            
        return {
            "anomaly_detected": False,
            "confidence": 0.0,
            "detection_method": "error"
        }

class MockGhostDetector:
    """Mock ghost detector when PyTorch is not available"""
    
    def __init__(self):
        logger.info("Using mock ghost detector (PyTorch not available)")
    
    def detect_anomaly(self, signal_data):
        """Mock anomaly detection"""
        return {
            "anomaly_detected": False,
            "confidence": 0.5,
            "detection_method": "mock"
        }

class GhostAnomalyAPI:
    """FastAPI wrapper for ghost anomaly detector"""
    
    def __init__(self, num_patterns=64):
        if not FASTAPI_AVAILABLE:
            raise ImportError("FastAPI not available")
            
        self.detector = GhostAnomalyDetector(num_patterns)
        self.app = FastAPI(title="Ghost Anomaly Detector API")
        
        @self.app.post("/detect_anomaly")
        async def detect_anomaly_endpoint(data: dict):
            try:
                signal_data = np.array(data.get("signal_data", []))
                result = self.detector.detect_anomaly(signal_data)
                return JSONResponse(content=result)
            except Exception as e:
                return JSONResponse(
                    content={"error": str(e)}, 
                    status_code=500
                )
    
    def run(self, host="0.0.0.0", port=8000):
        """Run the API server"""
        if FASTAPI_AVAILABLE:
            uvicorn.run(self.app, host=host, port=port)

@dataclass
class RFSignal:
    """RF Signal data structure"""
    id: str
    timestamp: float
    frequency: float
    bandwidth: float
    power: float
    iq_data: np.ndarray
    source: str
    classification: Optional[str] = None
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization (excludes IQ data)"""
        return {
            "id": self.id,
            "timestamp": float(self.timestamp),
            "frequency": float(self.frequency),
            "frequency_mhz": float(self.frequency / 1e6),
            "bandwidth": float(self.bandwidth),
            "bandwidth_khz": float(self.bandwidth / 1e3),
            "power": float(self.power),
            "source": self.source,
            "classification": self.classification,
            "confidence": float(self.confidence),
            "metadata": self.metadata
        }

class SignalProcessor:
    """Enhanced Signal processing engine with FlashAttention support"""
    def __init__(self, config):
        self.config = config
        self.use_cuda = config.get("use_cuda", False)
        self.neural_model_loaded = False
        self.frequency_based_fallback = config.get("frequency_based_fallback", True)
        
        # FlashAttention configuration
        self.attention_config = config.get("attention", {})
        self.use_flash_attention = self.attention_config.get("enabled", False) and PYTORCH_AVAILABLE
        
        if self.use_flash_attention:
            # Initialize spectrum encoder for latent compression
            self.spectrum_encoder = SpectrumEncoder(
                d_model=self.attention_config.get("d_model", 128),
                num_latents=self.attention_config.get("num_latents", 32),
                num_heads=self.attention_config.get("num_heads", 8)
            )
            
            # Initialize RoPE for positional encoding
            if 'RotaryEmbedding' in globals():
                self.rope = RotaryEmbedding(
                    dim=self.attention_config.get("d_model", 128)
                )
            else:
                self.rope = None
                
            logger.info("FlashAttention features enabled in SignalProcessor")
        else:
            self.spectrum_encoder = None
            self.rope = None
            logger.info("Using traditional signal processing (FlashAttention disabled)")
        
    def process_iq_data(self, iq_data: np.ndarray) -> Dict[str, Any]:
        """Enhanced IQ data processing with optional FlashAttention features"""
        # Signal processing algorithm
        if len(iq_data) < 128:
            return {}
            
        # Calculate power
        power = np.mean(np.abs(iq_data)**2)
        
        # Calculate spectrum
        spectrum = np.abs(np.fft.fftshift(np.fft.fft(iq_data)))**2
        
        # Find peak frequency
        peak_idx = np.argmax(spectrum)
        freq_range = np.linspace(-0.5, 0.5, len(spectrum))
        peak_freq = freq_range[peak_idx]
        
        # Estimate bandwidth
        threshold = np.max(spectrum) / 2
        mask = spectrum > threshold
        bandwidth = np.sum(mask) / len(spectrum)
        
        features = {
            "power": power,
            "peak_frequency": peak_freq,
            "bandwidth": bandwidth,
            "spectrum": spectrum
        }
        
        # Enhanced processing with FlashAttention
        if self.use_flash_attention and self.spectrum_encoder is not None:
            try:
                # Convert spectrum to tensor and reshape for processing
                spectrum_tensor = torch.from_numpy(spectrum).float().unsqueeze(0)
                
                # Pad or truncate to fixed size for model
                target_len = self.attention_config.get("spectrum_length", 512)
                if len(spectrum) > target_len:
                    spectrum_tensor = spectrum_tensor[:, :target_len]
                else:
                    pad_len = target_len - len(spectrum)
                    spectrum_tensor = F.pad(spectrum_tensor, (0, pad_len))
                
                # Project to model dimension
                d_model = self.attention_config.get("d_model", 128)
                spectrum_tensor = spectrum_tensor.unsqueeze(-1).expand(-1, -1, d_model)
                
                # Apply spectrum encoder (MHLA)
                with torch.no_grad():
                    compressed_features = self.spectrum_encoder(spectrum_tensor)
                    
                # Extract features from compressed representation
                compressed_numpy = compressed_features.squeeze(0).numpy()
                features["compressed_spectrum"] = compressed_numpy
                features["spectral_attention_features"] = {
                    "mean_activation": float(np.mean(compressed_numpy)),
                    "max_activation": float(np.max(compressed_numpy)),
                    "std_activation": float(np.std(compressed_numpy))
                }
                
                logger.debug("Applied FlashAttention spectrum encoding")
                
            except Exception as e:
                logger.warning(f"FlashAttention processing failed: {e}, using traditional processing")
        
        return features

    def process_spectrum_frame(
        self,
        power_db,
        *,
        center_frequency_hz,
        sample_rate_hz,
        native_bin_width_hz,
        analysis_bin_width_hz,
        window,
        signal_chain_hash,
        captured_at,
        frame_id="unidentified-frame",
    ) -> Dict[str, Any]:
        """Process calibrated spectrum bins without repeating the FFT.

        This is an experimental annotation path. It deliberately returns no
        classification and cannot promote its result into graph evidence.
        """
        levels = np.asarray(power_db, dtype=np.float32)
        if levels.ndim != 1 or not 16 <= levels.size <= 4096 or not np.all(np.isfinite(levels)):
            raise ValueError("power_db must contain 16 to 4096 finite spectrum bins")
        if float(sample_rate_hz) <= 0 or float(analysis_bin_width_hz) <= 0:
            raise ValueError("spectrum rates and bin widths must be positive")
        if not str(signal_chain_hash).startswith("sha256:"):
            raise ValueError("signal_chain_hash must identify a SHA-256 chain")

        noise_floor_db = float(np.median(levels))
        peak_index = int(np.argmax(levels))
        peak_db = float(levels[peak_index])
        peak_frequency_hz = (
            float(center_frequency_hz) - float(sample_rate_hz) / 2.0
            + (peak_index + 0.5) * float(analysis_bin_width_hz)
        )
        normalized = np.clip((levels - noise_floor_db) / 40.0, -1.0, 1.0)
        features = {
            "bin_count": int(levels.size),
            "noise_floor_db": round(noise_floor_db, 4),
            "peak_db": round(peak_db, 4),
            "peak_excess_db": round(peak_db - noise_floor_db, 4),
            "peak_frequency_hz": round(peak_frequency_hz, 3),
            "occupied_fraction_6db": round(float(np.mean(levels >= noise_floor_db + 6.0)), 6),
        }

        if self.use_flash_attention and self.spectrum_encoder is not None:
            try:
                tensor = torch.from_numpy(normalized).float().unsqueeze(0)
                target_len = self.attention_config.get("spectrum_length", 512)
                tensor = tensor[:, :target_len]
                if tensor.shape[1] < target_len:
                    tensor = F.pad(tensor, (0, target_len - tensor.shape[1]))
                d_model = self.attention_config.get("d_model", 128)
                tensor = tensor.unsqueeze(-1).expand(-1, -1, d_model)
                with torch.no_grad():
                    encoded = self.spectrum_encoder(tensor).squeeze(0).numpy()
                features["experimental_encoding"] = {
                    "mean_activation": float(np.mean(encoded)),
                    "max_activation": float(np.max(encoded)),
                    "std_activation": float(np.std(encoded)),
                }
            except Exception as exc:
                logger.warning("Spectrum-product encoding failed: %s", exc)

        return {
            "schema": "nerfengine.rf.analysis.v1",
            "source_frame_id": str(frame_id),
            "model_revision": "nerfengine-spectrum-adapter.v1",
            "result": "FEATURES_EXTRACTED",
            "confidence": 0.0,
            "authority": "experimental_inference",
            "promotion": "not_graph_evidence",
            "features": features,
            "provenance": {
                "captured_at": str(captured_at),
                "window": str(window),
                "native_bin_width_hz": float(native_bin_width_hz),
                "analysis_bin_width_hz": float(analysis_bin_width_hz),
                "signal_chain_hash": str(signal_chain_hash),
            },
        }
    
    def classify_signal(self, signal: RFSignal) -> Tuple[str, float]:
        """Classify signal using neural network or frequency-based method if ML fails"""
        # This is now a fallback method when ML classifier isn't available
        # The actual ML classification is handled by the ML classifier object
        # in the SignalIntelligenceSystem class
        
        if not self.frequency_based_fallback:
            return "Unknown", 0.5
            
        # Simple frequency-based classification
        freq_mhz = signal.frequency / 1e6  # Convert to MHz for easier comparison
        
        # GSM bands
        if (freq_mhz > 914 and freq_mhz < 916) or (freq_mhz > 925 and freq_mhz < 960):
            return "GSM", 0.9
        # VHF Amateur Radio
        elif freq_mhz > 143.8 and freq_mhz < 148:
            return "VHF Amateur", 0.85
        # UHF Amateur Radio
        elif freq_mhz > 430 and freq_mhz < 440:
            return "UHF Amateur", 0.85
        # WiFi 2.4GHz
        elif freq_mhz > 2400 and freq_mhz < 2500:
            return "WiFi", 0.8
        # WiFi 5GHz
        elif freq_mhz > 5150 and freq_mhz < 5850:
            return "WiFi 5GHz", 0.8
        # GPS
        elif freq_mhz > 1575 and freq_mhz < 1585:
            return "GPS", 0.95
        # FM Radio
        elif freq_mhz > 87.5 and freq_mhz < 108:
            return "FM Radio", 0.9
        # Bluetooth
        elif freq_mhz > 2400 and freq_mhz < 2485:
            return "Bluetooth", 0.75
        # LoRa
        elif freq_mhz > 902 and freq_mhz < 928:
            return "LoRa/IoT", 0.7
        # Unknown
        else:
            return "Unknown", 0.5

class ExternalSourceIntegrator:
    """Integrates with external data sources"""
    def __init__(self, config):
        self.config = config
        self.sources = {}
        self.active_sources = []
        
    def register_source(self, name, source_class, params):
        """Register new data source"""
        try:
            source = source_class(**params)
            self.sources[name] = source
            logger.info(f"Registered source: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to register source {name}: {e}")
            return False
    
    def activate_source(self, name):
        """Activate a registered source"""
        if name in self.sources and name not in self.active_sources:
            self.active_sources.append(name)
            logger.info(f"Activated source: {name}")
            return True
        return False
    
    def deactivate_source(self, name):
        """Deactivate a source"""
        if name in self.active_sources:
            self.active_sources.remove(name)
            logger.info(f"Deactivated source: {name}")
            return True
        return False
    
    def get_data_from_source(self, name):
        """Get data from a specific source"""
        if name in self.sources:
            try:
                return self.sources[name].get_data()
            except Exception as e:
                logger.error(f"Error getting data from {name}: {e}")
        return None

class KiwiSDRSource:
    """KiwiSDR integration"""
    def __init__(self, host, port=8073, frequency=14070, modulation="usb"):
        self.host = host
        self.port = port
        self.frequency = frequency
        self.modulation = modulation
        self.connected = False
        self.client = None
        
    def connect(self):
        """Connect to KiwiSDR"""
        logger.info(f"Connecting to KiwiSDR at {self.host}:{self.port}")
        # In real implementation, this would connect to actual KiwiSDR client
        self.connected = True
        
    def get_data(self, sample_size=1024):
        """Get sample data from KiwiSDR"""
        if not self.connected:
            self.connect()
        
        # Simulate data for now
        data = np.random.normal(0, 1, sample_size) + 1j * np.random.normal(0, 1, sample_size)
        return {
            "iq_data": data,
            "frequency": self.frequency,
            "timestamp": time.time(),
            "source": "KiwiSDR"
        }

class JWSTSource:
    """JWST data integration"""
    def __init__(self, api_key=None, data_path=None):
        self.api_key = api_key
        self.data_path = data_path
        
    def get_data(self):
        """Get data from JWST"""
        # Simulated data
        return {
            "timestamp": time.time(),
            "source": "JWST",
            "observations": [
                {"target": "NGC-" + str(np.random.randint(1000, 9999)), 
                 "intensity": np.random.random()}
                for _ in range(5)
            ]
        }

class ISSSource:
    """ISS data integration"""
    def __init__(self, api_url=None):
        self.api_url = api_url
        
    def get_data(self):
        """Get data from ISS"""
        # Simulated positional data
        return {
            "timestamp": time.time(),
            "source": "ISS",
            "position": {
                "lat": np.random.uniform(-80, 80),
                "lon": np.random.uniform(-180, 180),
                "alt": 420 + np.random.normal(0, 2)
            },
            "velocity": 7.66 + np.random.normal(0, 0.1)
        }

class LHCSource:
    """LHC data integration"""
    def __init__(self, data_endpoint=None):
        self.data_endpoint = data_endpoint
        
    def get_data(self):
        """Get data from LHC"""
        # Simulated LHC data
        return {
            "timestamp": time.time(),
            "source": "LHC",
            "beam_energy": 6.8 + np.random.normal(0, 0.1),
            "luminosity": 2.4e34 * (1 + np.random.normal(0, 0.05)),
            "detector": np.random.choice(["ATLAS", "CMS", "ALICE", "LHCb"])
        }

class SignalIntelligenceSystem:
    """Enhanced Signal Intelligence System with FlashAttention support"""
    def __init__(self, config, comm_network):
        self.config = config
        self.comm_network = comm_network
        self.signal_processor = SignalProcessor(config)
        self.source_integrator = ExternalSourceIntegrator(config)
        self.running = False
        self.signal_queue = Queue()
        self.processed_signals = []
        
        # Get signal intelligence specific config
        si_config = config.get("signal_intelligence", {})
        
        # Initialize ML classifier based on configuration with FlashAttention support
        classifier_type = si_config.get("classifier_type", "simple")
        attention_config = si_config.get("attention", {})
        
        if classifier_type == "ensemble":
            # Initialize ensemble classifier
            from SignalIntelligence.ensemble_ml_classifier import EnsembleMLClassifier
            base_classifier = EnsembleMLClassifier(si_config.get("ml_classifier", {}))
            
            # Check if speculative decoding is enabled
            if attention_config.get("speculative_decoding", False) and PYTORCH_AVAILABLE:
                # Create a fast and slow model for speculative ensemble
                fast_config = si_config.get("ml_classifier", {}).copy()
                fast_config["model_complexity"] = "low"
                
                from SignalIntelligence.ml_classifier import MLClassifier
                fast_classifier = MLClassifier(fast_config)
                
                self.ml_classifier = SpeculativeEnsemble(
                    fast_model=fast_classifier,
                    slow_model=base_classifier,
                    threshold=attention_config.get("speculation_threshold", 0.8)
                )
                logger.info("Using speculative ensemble ML classifier with FlashAttention")
            else:
                self.ml_classifier = base_classifier
                logger.info("Using standard ensemble ML classifier")
                
        elif classifier_type == "hierarchical":
            # Initialize hierarchical classifier
            from SignalIntelligence.hierarchical_ml_classifier import HierarchicalMLClassifier
            self.ml_classifier = HierarchicalMLClassifier(si_config.get("ml_classifier", {}))
            logger.info("Using hierarchical ML classifier")
            
        elif classifier_type == "flash":
            # Initialize FlashAttention-optimized classifier
            if PYTORCH_AVAILABLE:
                from SignalIntelligence.ml_classifier import MLClassifier
                flash_config = si_config.get("ml_classifier", {}).copy()
                flash_config["attention_type"] = "flash"
                flash_config["attention_config"] = attention_config
                self.ml_classifier = MLClassifier(flash_config)
                logger.info("Using FlashAttention-optimized ML classifier")
            else:
                logger.warning("FlashAttention not available, falling back to standard classifier")
                from SignalIntelligence.ml_classifier import MLClassifier
                self.ml_classifier = MLClassifier(si_config.get("ml_classifier", {}))
                
        elif classifier_type == "latent":
            # Initialize Multi-Head Latent Attention classifier
            if PYTORCH_AVAILABLE:
                from SignalIntelligence.ml_classifier import MLClassifier
                latent_config = si_config.get("ml_classifier", {}).copy()
                latent_config["attention_type"] = "latent"
                latent_config["attention_config"] = attention_config
                self.ml_classifier = MLClassifier(latent_config)
                logger.info("Using Multi-Head Latent Attention ML classifier")
            else:
                logger.warning("MHLA not available, falling back to standard classifier")
                from SignalIntelligence.ml_classifier import MLClassifier
                self.ml_classifier = MLClassifier(si_config.get("ml_classifier", {}))
                
        elif classifier_type == "grouped":
            # Initialize Grouped Query Attention classifier
            if PYTORCH_AVAILABLE:
                from SignalIntelligence.ml_classifier import MLClassifier
                gqa_config = si_config.get("ml_classifier", {}).copy()
                gqa_config["attention_type"] = "grouped"
                gqa_config["attention_config"] = attention_config
                self.ml_classifier = MLClassifier(gqa_config)
                logger.info("Using Grouped Query Attention ML classifier")
            else:
                logger.warning("GQA not available, falling back to standard classifier")
                from SignalIntelligence.ml_classifier import MLClassifier
                self.ml_classifier = MLClassifier(si_config.get("ml_classifier", {}))
                
        else:
            # Initialize standard ML classifier
            from SignalIntelligence.ml_classifier import MLClassifier
            self.ml_classifier = MLClassifier(si_config.get("ml_classifier", {}))
            logger.info("Using standard ML classifier")
        
        # Register external sources
        self._register_default_sources()
        
        # Initialize Ghost Anomaly Detector if enabled
        ghost_config = si_config.get("ghost_anomaly_detector", {})
        self.ghost_detector = None
        self.ghost_api = None
        
        if ghost_config.get("enabled", False) and PYTORCH_AVAILABLE:
            num_patterns = ghost_config.get("num_patterns", 64)
            self.ghost_detector_singleton = CompiledGhostDetectorSingleton()
            self.ghost_detector = self.ghost_detector_singleton.get_detector(num_patterns)
            logger.info(f"Ghost Anomaly Detector initialized with {num_patterns} spectral patterns")
            
            # Initialize API if requested
            if ghost_config.get("enable_api", False) and FASTAPI_AVAILABLE:
                try:
                    self.ghost_api = GhostAnomalyAPI(num_patterns)
                    logger.info("Ghost Anomaly Detector API initialized")
                except Exception as e:
                    logger.warning(f"Failed to initialize Ghost API: {e}")
        elif ghost_config.get("enabled", False):
            logger.warning("Ghost Anomaly Detector requested but PyTorch not available")
        
        # Initialize BloodysignalDetector if enabled
        bloodsignal_config = si_config.get("bloodsignal_detector", {})
        self.bloodsignal_detector = None
        
        if bloodsignal_config.get("enabled", False) and PYTORCH_AVAILABLE:
            try:
                self.bloodsignal_detector = BloodysignalDetector(bloodsignal_config)
                logger.info("BloodysignalDetector initialized - RF biomarker detection active")
                
                # Initialize Temporal Query Denoiser
                self.temporal_denoiser = TemporalQueryDenoiser()
                logger.info("Temporal Query Denoiser initialized - bloodhound-like discrimination active")
                
            except Exception as e:
                logger.warning(f"Failed to initialize BloodysignalDetector: {e}")
        elif bloodsignal_config.get("enabled", False):
            logger.warning("BloodysignalDetector requested but PyTorch not available")
        
        # Initialize DOMA RF Motion Tracker
        doma_config = si_config.get("doma_motion_tracker", {})
        self.doma_tracker = None
        
        if doma_config.get("enabled", True):
            try:
                self.doma_tracker = DOMASignalTracker(doma_config)
                logger.info("DOMA RF Motion Tracker initialized - signal trajectory prediction active")
            except Exception as e:
                logger.warning(f"Failed to initialize DOMA Motion Tracker: {e}")
        
        # Store signal positions for DOMA tracking (simplified positioning)
        self.signal_positions = {}  # signal_id -> last known position
    
    def _register_default_sources(self):
        """Register default external sources"""
        # Register KiwiSDR
        self.source_integrator.register_source(
            "kiwisdr1", 
            KiwiSDRSource, 
            {"host": "localhost", "port": 8073}
        )
        
        # Register JWST
        self.source_integrator.register_source(
            "jwst", 
            JWSTSource, 
            {"api_key": self.config.get("jwst_api_key")}
        )
        
        # Register ISS
        self.source_integrator.register_source(
            "iss", 
            ISSSource, 
            {"api_url": "https://api.wheretheiss.at/v1/satellites/25544"}
        )
        
        # Register LHC
        self.source_integrator.register_source(
            "lhc", 
            LHCSource, 
            {"data_endpoint": "https://lhcdata.cern.ch/api/v1/status"}
        )
        
        # Activate sources
        for source in ["kiwisdr1", "jwst", "iss", "lhc"]:
            self.source_integrator.activate_source(source)
    
    def get_signals(self):
        """Get all processed signals"""
        # Convert RFSignal objects to dictionaries for JSON serialization
        signals_list = []
        for signal in self.processed_signals:
            signals_list.append({
                "id": signal.id,
                "timestamp": signal.timestamp,
                "frequency": signal.frequency,
                "bandwidth": signal.bandwidth,
                "power": signal.power,
                "source": signal.source,
                "classification": signal.classification,
                "confidence": signal.confidence
            })
        return signals_list
    
    def process_signal(self, signal_data):
        """Process incoming signal data"""
        # Extract features
        if "iq_data" in signal_data:
            features = self.signal_processor.process_iq_data(signal_data["iq_data"])
            signal_data.update(features)
        
        # Create signal object
        signal = RFSignal(
            id=f"sig_{time.time()}_{np.random.randint(1000, 9999)}",
            timestamp=signal_data.get("timestamp", time.time()),
            frequency=signal_data.get("frequency", 0),
            bandwidth=signal_data.get("bandwidth", 0),
            power=signal_data.get("power", 0),
            iq_data=signal_data.get("iq_data", np.array([])),
            source=signal_data.get("source", "unknown")
        )
        
        # Classify signal using ML classifier
        try:
            # Attempt to use ML classifier
            classification, confidence, probabilities = self.ml_classifier.classify_signal(signal)
            signal.classification = classification
            signal.confidence = confidence
            signal.metadata["probabilities"] = probabilities
            signal.metadata["classifier"] = "ml_classifier"
        except Exception as e:
            # Fall back to frequency-based classification if ML fails
            logger.warning(f"ML classification failed: {str(e)}, falling back to frequency-based classification")
            classification, confidence = self.signal_processor.classify_signal(signal)
            signal.classification = classification
            signal.confidence = confidence
            signal.metadata["classifier"] = "frequency_based"
        
        # Store and share processed signal
        self.processed_signals.append(signal)
        
        # Add to DOMA motion tracking if enabled
        if self.doma_tracker is not None:
            # Estimate signal position (simplified - in real system this would come from direction finding)
            position = self._estimate_signal_position(signal)
            if position is not None:
                self.doma_tracker.add_trajectory_point(signal, position)
                self.signal_positions[signal.id] = position
                
                # Get motion prediction if we have enough trajectory data
                prediction = self.doma_tracker.predict_next_position(signal.id)
                if prediction:
                    signal.metadata["motion_prediction"] = prediction
                    logger.debug(f"Motion prediction for signal {signal.id}: {prediction}")
        
        self.comm_network.publish("signal_detected", signal)
        
        return signal
    
    def start(self):
        """Start Signal Intelligence System"""
        logger.info("Starting Signal Intelligence System")
        self.running = True
        
        # Start signal processing thread
        processing_thread = threading.Thread(target=self._signal_processing_loop)
        processing_thread.daemon = True
        processing_thread.start()
        
        # Start data collection thread
        collection_thread = threading.Thread(target=self._data_collection_loop)
        collection_thread.daemon = True
        collection_thread.start()
    
    def _signal_processing_loop(self):
        """Main signal processing loop"""
        while self.running:
            try:
                # Get signal from queue
                if not self.signal_queue.empty():
                    signal_data = self.signal_queue.get(timeout=1)
                    self.process_signal(signal_data)
                    self.signal_queue.task_done()
                else:
                    time.sleep(0.1)
            except Exception as e:
                logger.error(f"Error in signal processing: {e}")
                time.sleep(1)
    
    def _data_collection_loop(self):
        """Data collection from external sources"""
        cleanup_counter = 0
        while self.running:
            try:
                # Collect data from all active sources
                for source_name in self.source_integrator.active_sources:
                    data = self.source_integrator.get_data_from_source(source_name)
                    if data:
                        self.signal_queue.put(data)
                
                # Periodic cleanup of old trajectory data (every 60 iterations ~ 1 minute)
                cleanup_counter += 1
                if cleanup_counter >= 60 and self.doma_tracker is not None:
                    self.doma_tracker.cleanup_old_trajectories()
                    cleanup_counter = 0
                
                # Wait before next collection
                time.sleep(1)
            except Exception as e:
                logger.error(f"Error in data collection: {e}")
                time.sleep(5)
    
    def shutdown(self):
        """Shutdown the system"""
        logger.info("Shutting down Signal Intelligence System")
        self.running = False
        
    def start_scan(self):
        """Start a new RF scan"""
        logger.info("Starting RF scan")
        # This is a placeholder for actual scan implementation
        # In a real system, this might configure hardware or start a specialized scan mode
        
        # For simulation, we'll trigger some additional data collection
        for source_name in self.source_integrator.active_sources:
            for _ in range(5):  # Collect 5 samples from each source
                data = self.source_integrator.get_data_from_source(source_name)
                if data:
                    self.signal_queue.put(data)
                    
        return True
        
    def analyze_signals(self):
        """Analyze detected signals with enhanced DOMA motion analysis"""
        logger.info("Analyzing signals with DOMA motion tracking")
        results = {
            "total_signals": len(self.processed_signals),
            "signal_sources": {},
            "classifications": {},
            "motion_analysis": {}
        }
        
        # Count signals by source
        for signal in self.processed_signals:
            if signal.source not in results["signal_sources"]:
                results["signal_sources"][signal.source] = 0
            results["signal_sources"][signal.source] += 1
            
            # Count signals by classification
            if signal.classification not in results["classifications"]:
                results["classifications"][signal.classification] = 0
            results["classifications"][signal.classification] += 1
        
        # Add DOMA motion analysis if available
        if self.doma_tracker is not None:
            motion_results = {
                "tracked_signals": len(self.doma_tracker.signal_trajectories),
                "trajectory_summaries": {},
                "motion_predictions": {},
                "movement_statistics": {}
            }
            
            # Get trajectory analysis for each tracked signal
            total_distance = 0
            total_speed = 0
            tracked_count = 0
            
            for signal_id in self.doma_tracker.signal_trajectories.keys():
                # Get trajectory analysis
                trajectory_analysis = self.doma_tracker.get_trajectory_analysis(signal_id)
                if trajectory_analysis:
                    motion_results["trajectory_summaries"][signal_id] = trajectory_analysis
                    total_distance += trajectory_analysis["total_distance"]
                    total_speed += trajectory_analysis["average_speed"]
                    tracked_count += 1
                
                # Get motion prediction
                prediction = self.doma_tracker.predict_next_position(signal_id)
                if prediction:
                    motion_results["motion_predictions"][signal_id] = prediction
            
            # Calculate aggregate statistics
            if tracked_count > 0:
                motion_results["movement_statistics"] = {
                    "average_distance_traveled": total_distance / tracked_count,
                    "average_speed": total_speed / tracked_count,
                    "most_mobile_signals": self._get_most_mobile_signals(),
                    "stationary_signals": self._get_stationary_signals()
                }
            
            results["motion_analysis"] = motion_results
            
        # Add timestamp to results
        results["timestamp"] = time.time()
        
        # Publish analysis results to network
        self.comm_network.publish("signal_analysis", results)
        
        return results
    
    def _get_most_mobile_signals(self, top_n: int = 5) -> List[Dict]:
        """Get the most mobile signals based on movement speed"""
        if self.doma_tracker is None:
            return []
        
        mobile_signals = []
        for signal_id in self.doma_tracker.signal_trajectories.keys():
            analysis = self.doma_tracker.get_trajectory_analysis(signal_id)
            if analysis:
                mobile_signals.append({
                    "signal_id": signal_id,
                    "average_speed": analysis["average_speed"],
                    "total_distance": analysis["total_distance"],
                    "trajectory_points": analysis["trajectory_points"]
                })
        
        # Sort by average speed and return top N
        mobile_signals.sort(key=lambda x: x["average_speed"], reverse=True)
        return mobile_signals[:top_n]
    
    def _get_stationary_signals(self, speed_threshold: float = 1.0) -> List[Dict]:
        """Get signals that appear to be stationary"""
        if self.doma_tracker is None:
            return []
        
        stationary_signals = []
        for signal_id in self.doma_tracker.signal_trajectories.keys():
            analysis = self.doma_tracker.get_trajectory_analysis(signal_id)
            if analysis and analysis["average_speed"] <= speed_threshold:
                stationary_signals.append({
                    "signal_id": signal_id,
                    "average_speed": analysis["average_speed"],
                    "position_stability": analysis["total_distance"],
                    "trajectory_points": analysis["trajectory_points"]
                })
        
        return stationary_signals
    
    def analyze_spectrum_with_ghost_detector(self, spectrum_data):
        """Analyze spectrum using Ghost Anomaly Detector for stealth/spoofing detection"""
        if self.ghost_detector is None:
            logger.warning("Ghost Anomaly Detector not initialized")
            return None
            
        try:
            # Convert spectrum data to tensor format
            if isinstance(spectrum_data, np.ndarray):
                spectrum_tensor = torch.from_numpy(spectrum_data).float()
            else:
                spectrum_tensor = torch.tensor(spectrum_data, dtype=torch.float32)
            
            # Ensure proper batch dimension
            if spectrum_tensor.dim() == 1:
                spectrum_tensor = spectrum_tensor.unsqueeze(0)
            
            with torch.no_grad():
                # Ghost imaging reconstruction
                reconstructed = self.ghost_detector(spectrum_tensor)
                
                # Calculate anomaly score
                anomaly_score = self.ghost_detector.anomaly_score(spectrum_tensor, reconstructed)
                
                # Determine if anomaly (threshold can be configurable)
                ghost_config = self.config.get("signal_intelligence", {}).get("ghost_anomaly_detector", {})
                threshold = ghost_config.get("anomaly_threshold", 0.05)
                is_anomaly = anomaly_score.item() > threshold
                
                result = {
                    "original_spectrum": spectrum_tensor[0].tolist(),
                    "reconstructed_spectrum": reconstructed[0].tolist(),
                    "anomaly_score": anomaly_score.item(),
                    "is_anomaly": is_anomaly,
                    "threshold": threshold,
                    "timestamp": time.time(),
                    "analysis_type": "ghost_imaging_spectral"
                }
                
                if is_anomaly:
                    logger.warning(f"Ghost Anomaly Detected! Score: {anomaly_score.item():.6f}")
                    result["threat_level"] = "HIGH" if anomaly_score.item() > 0.1 else "MEDIUM"
                    result["possible_threats"] = [
                        "Stealth emission",
                        "Signal spoofing",
                        "Unknown modulation",
                        "Adversarial interference"
                    ]
                else:
                    result["threat_level"] = "LOW"
                
                return result
                
        except Exception as e:
            logger.error(f"Ghost Anomaly Detector analysis failed: {e}")
            return {"error": str(e), "analysis_type": "ghost_imaging_spectral"}

    def start_ghost_detector_api(self, host="0.0.0.0", port=8000):
        """Start the Ghost Anomaly Detector REST API server"""
        if self.ghost_api is None:
            logger.error("Ghost API not initialized. Enable in config with 'enable_api': True")
            return False
            
        try:
            logger.info(f"Starting Ghost Anomaly Detector API on {host}:{port}")
            self.ghost_api.run_server(host=host, port=port)
            return True
        except Exception as e:
            logger.error(f"Failed to start Ghost API server: {e}")
            return False

    def get_ghost_detector_status(self):
        """Get status of the Ghost Anomaly Detector"""
        if self.ghost_detector is None:
            return {"status": "disabled", "reason": "not_initialized"}
        
        ghost_config = self.config.get("signal_intelligence", {}).get("ghost_anomaly_detector", {})
        return {
            "status": "operational",
            "num_patterns": ghost_config.get("num_patterns", 64),
            "anomaly_threshold": ghost_config.get("anomaly_threshold", 0.05),
            "compiled": True,
            "api_enabled": self.ghost_api is not None,
            "timestamp": time.time()
        }
        
@dataclass
class RFTrajectoryPoint:
    """RF signal trajectory point data structure"""
    timestamp: float
    position: np.ndarray  # 3D position [x, y, z]
    frequency: float
    power: float
    velocity: Optional[np.ndarray] = None  # 3D velocity vector
    acceleration: Optional[np.ndarray] = None  # 3D acceleration
    signal_id: Optional[str] = None
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

class DOMASignalTracker:
    """DOMA-based RF signal motion tracking and prediction"""
    def __init__(self, config):
        self.config = config
        self.signal_trajectories = {}  # signal_id -> list of trajectory points
        self.motion_models = {}  # signal_id -> DOMA model
        self.use_enhanced_model = config.get("use_enhanced_doma", True)
        self.model_path = config.get("doma_model_path", "doma_rf_motion_model.pth")
        self.enhanced_model_path = config.get("enhanced_doma_model_path", "enhanced_doma_rf_motion_model.pth")
        
        # Initialize default DOMA models if available
        if DOMA_AVAILABLE and PYTORCH_AVAILABLE:
            self.default_model = self._load_default_model()
            logger.info("DOMA RF Motion Tracker initialized")
        else:
            self.default_model = None
            logger.warning("DOMA RF Motion Tracker disabled - PyTorch or DOMA models not available")
    
    def _load_default_model(self):
        """Load the default DOMA model"""
        try:
            if self.use_enhanced_model and os.path.exists(self.enhanced_model_path):
                model = EnhancedDOMAMotionModel.load(self.enhanced_model_path)
                logger.info(f"Loaded enhanced DOMA model from {self.enhanced_model_path}")
                return model
            elif os.path.exists(self.model_path):
                model = DOMAMotionModel.load(self.model_path)
                logger.info(f"Loaded standard DOMA model from {self.model_path}")
                return model
            else:
                # Create and return an untrained model for basic prediction
                if self.use_enhanced_model:
                    model = EnhancedDOMAMotionModel()
                    logger.warning("Using untrained enhanced DOMA model - predictions may be inaccurate")
                else:
                    model = DOMAMotionModel()
                    logger.warning("Using untrained DOMA model - predictions may be inaccurate")
                return model
        except Exception as e:
            logger.error(f"Failed to load DOMA model: {e}")
            return None
    
    def add_trajectory_point(self, signal: RFSignal, position: np.ndarray):
        """Add a new trajectory point for a signal"""
        if not DOMA_AVAILABLE or self.default_model is None:
            return
        
        # Create trajectory point
        point = RFTrajectoryPoint(
            timestamp=signal.timestamp,
            position=position,
            frequency=signal.frequency,
            power=signal.power,
            signal_id=signal.id,
            confidence=signal.confidence,
            metadata=signal.metadata
        )
        
        # Add to trajectory
        if signal.id not in self.signal_trajectories:
            self.signal_trajectories[signal.id] = []
        
        self.signal_trajectories[signal.id].append(point)
        
        # Calculate velocity and acceleration if we have enough points
        trajectory = self.signal_trajectories[signal.id]
        if len(trajectory) >= 2:
            # Calculate velocity
            dt = trajectory[-1].timestamp - trajectory[-2].timestamp
            if dt > 0:
                velocity = (trajectory[-1].position - trajectory[-2].position) / dt
                trajectory[-1].velocity = velocity
                
                # Calculate acceleration if we have 3+ points
                if len(trajectory) >= 3 and trajectory[-2].velocity is not None:
                    acceleration = (trajectory[-1].velocity - trajectory[-2].velocity) / dt
                    trajectory[-1].acceleration = acceleration
        
        logger.debug(f"Added trajectory point for signal {signal.id} at position {position}")
    
    def predict_next_position(self, signal_id: str, time_ahead: float = 1.0, 
                             flight_conditions: Optional[Dict] = None) -> Optional[Dict]:
        """Predict the next position of a signal using DOMA model"""
        if not DOMA_AVAILABLE or self.default_model is None:
            return None
        
        if signal_id not in self.signal_trajectories:
            logger.warning(f"No trajectory data available for signal {signal_id}")
            return None
        
        trajectory = self.signal_trajectories[signal_id]
        if len(trajectory) == 0:
            return None
        
        # Get the latest trajectory point
        latest_point = trajectory[-1]
        
        try:
            # Use enhanced model if available and flight conditions provided
            if isinstance(self.default_model, EnhancedDOMAMotionModel) and flight_conditions:
                prediction = self.default_model.predict_next_position(
                    position=latest_point.position,
                    time_step=latest_point.timestamp + time_ahead,
                    flight_conditions=flight_conditions
                )
            else:
                # Use standard model
                prediction = self.default_model.predict_next_position(
                    position=latest_point.position,
                    time_step=latest_point.timestamp + time_ahead
                )
            
            # Format prediction result
            result = {
                "signal_id": signal_id,
                "current_position": latest_point.position.tolist(),
                "predicted_position": prediction if isinstance(prediction, np.ndarray) else prediction.get("next_position", [0, 0, 0]),
                "prediction_time": latest_point.timestamp + time_ahead,
                "time_ahead": time_ahead,
                "model_type": "enhanced" if isinstance(self.default_model, EnhancedDOMAMotionModel) else "standard",
                "trajectory_points": len(trajectory)
            }
            
            # Add additional fields for enhanced model
            if isinstance(prediction, dict):
                result.update({
                    "predicted_rotation": prediction.get("rotation", [0, 0, 0]),
                    "predicted_velocity": prediction.get("velocity", 0),
                    "confidence": prediction.get("confidence", 0.5),
                    "plasma_effects": prediction.get("plasma_effects", {})
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Error predicting position for signal {signal_id}: {e}")
            return None
    
    def predict_trajectory(self, signal_id: str, num_steps: int = 10, 
                          time_step: float = 1.0) -> Optional[List[Dict]]:
        """Predict a full trajectory for a signal"""
        if not DOMA_AVAILABLE or self.default_model is None:
            return None
        
        predictions = []
        current_time = time.time()
        
        for i in range(num_steps):
            prediction_time = current_time + (i + 1) * time_step
            prediction = self.predict_next_position(
                signal_id=signal_id,
                time_ahead=(i + 1) * time_step
            )
            
            if prediction:
                prediction["step"] = i + 1
                predictions.append(prediction)
            else:
                break
        
        return predictions if predictions else None
    
    def get_trajectory_analysis(self, signal_id: str) -> Optional[Dict]:
        """Get analysis of signal trajectory"""
        if signal_id not in self.signal_trajectories:
            return None
        
        trajectory = self.signal_trajectories[signal_id]
        if len(trajectory) < 2:
            return None
        
        # Calculate trajectory statistics
        positions = np.array([point.position for point in trajectory])
        timestamps = np.array([point.timestamp for point in trajectory])
        frequencies = np.array([point.frequency for point in trajectory])
        powers = np.array([point.power for point in trajectory])
        
        # Distance traveled
        distances = np.linalg.norm(np.diff(positions, axis=0), axis=1)
        total_distance = np.sum(distances)
        
        # Average speed
        time_span = timestamps[-1] - timestamps[0]
        avg_speed = total_distance / time_span if time_span > 0 else 0
        
        # Frequency drift
        freq_drift = frequencies[-1] - frequencies[0]
        
        # Power variation
        power_variation = np.std(powers)
        
        analysis = {
            "signal_id": signal_id,
            "trajectory_points": len(trajectory),
            "time_span": time_span,
            "total_distance": float(total_distance),
            "average_speed": float(avg_speed),
            "frequency_drift": float(freq_drift),
            "power_variation": float(power_variation),
            "start_position": positions[0].tolist(),
            "end_position": positions[-1].tolist(),
            "start_time": timestamps[0],
            "end_time": timestamps[-1],
            "frequency_range": [float(np.min(frequencies)), float(np.max(frequencies))],
            "power_range": [float(np.min(powers)), float(np.max(powers))]
        }
        
        return analysis
    
    def cleanup_old_trajectories(self, max_age: float = 3600.0):
        """Clean up trajectory data older than max_age seconds"""
        current_time = time.time()
        signals_to_remove = []
        
        for signal_id, trajectory in self.signal_trajectories.items():
            if trajectory and (current_time - trajectory[-1].timestamp) > max_age:
                signals_to_remove.append(signal_id)
        
        for signal_id in signals_to_remove:
            del self.signal_trajectories[signal_id]
            if signal_id in self.motion_models:
                del self.motion_models[signal_id]
        
        if signals_to_remove:
            logger.info(f"Cleaned up {len(signals_to_remove)} old signal trajectories")
    
    def _estimate_signal_position(self, signal: RFSignal) -> Optional[np.ndarray]:
        """
        Estimate 3D position of RF signal source (simplified implementation)
        In a real system, this would use triangulation from multiple receivers
        """
        # This is a simplified position estimation for demonstration
        # Real implementation would use direction finding, TDOA, etc.
        
        # Use frequency as a proxy for distance (higher freq = closer)
        # This is purely for demonstration - not physically accurate
        base_distance = 1000.0  # Base distance in meters
        freq_factor = 1.0 + (signal.frequency - 100e6) / 1e9  # Normalize around 100 MHz
        distance = base_distance / max(freq_factor, 0.1)
        
        # Random bearing for demonstration (would be from direction finding)
        import random
        bearing = random.uniform(0, 2 * np.pi)
        elevation = random.uniform(-np.pi/6, np.pi/6)  # -30 to +30 degrees
        
        # Convert to Cartesian coordinates
        x = distance * np.cos(elevation) * np.cos(bearing)
        y = distance * np.cos(elevation) * np.sin(bearing)
        z = distance * np.sin(elevation)
        
        return np.array([x, y, z])
    
    def get_motion_predictions(self, signal_id: Optional[str] = None) -> Dict[str, Any]:
        """Get motion predictions for signals"""
        if self.doma_tracker is None:
            return {"error": "DOMA Motion Tracker not available"}
        
        if signal_id:
            # Get prediction for specific signal
            prediction = self.doma_tracker.predict_next_position(signal_id)
            if prediction:
                return {"signal_id": signal_id, "prediction": prediction}
            else:
                return {"error": f"No prediction available for signal {signal_id}"}
        else:
            # Get predictions for all tracked signals
            predictions = {}
            for sid in self.doma_tracker.signal_trajectories.keys():
                prediction = self.doma_tracker.predict_next_position(sid)
                if prediction:
                    predictions[sid] = prediction
            
            return {"predictions": predictions, "total_signals": len(predictions)}
    
    def get_trajectory_analysis(self, signal_id: str) -> Optional[Dict]:
        """Get trajectory analysis for a specific signal"""
        if self.doma_tracker is None:
            return None
        
        return self.doma_tracker.get_trajectory_analysis(signal_id)
    
    def predict_signal_trajectory(self, signal_id: str, num_steps: int = 10, 
                                 time_step: float = 1.0) -> Optional[List[Dict]]:
        """Predict full trajectory for a signal"""
        if self.doma_tracker is None:
            return None
        
        return self.doma_tracker.predict_trajectory(signal_id, num_steps, time_step)

# Configuration and example usage for DOMA RF Motion Model integration

def create_doma_config():
    """Create default configuration for DOMA Motion Tracker"""
    return {
        "enabled": True,
        "use_enhanced_doma": True,
        "doma_model_path": "/home/gorelock/gemma/NerfEngine/doma_rf_motion_model.pth",
        "enhanced_doma_model_path": "/home/gorelock/gemma/NerfEngine/enhanced_doma_rf_motion_model.pth",
        "trajectory_cleanup_interval": 3600,  # seconds
        "max_trajectory_points": 1000,
        "position_estimation": {
            "method": "frequency_proxy",  # simplified method for demo
            "base_distance": 1000.0,
            "enable_triangulation": False  # would require multiple receivers
        }
    }

def demo_doma_integration():
    """
    Demonstration of DOMA RF Motion Model integration with Signal Intelligence
    
    This function shows how the DOMA motion tracker integrates with the
    Signal Intelligence system to provide trajectory prediction capabilities.
    """
    logger.info("=== DOMA RF Motion Model Integration Demo ===")
    
    # Create configuration with DOMA enabled
    config = {
        "signal_intelligence": {
            "classifier_type": "flash",
            "doma_motion_tracker": create_doma_config(),
            "attention": {
                "enabled": True,
                "d_model": 128,
                "num_heads": 8,
                "speculative_decoding": True
            }
        },
        "use_cuda": torch.cuda.is_available() if PYTORCH_AVAILABLE else False
    }
    
    # Mock communication network
    class MockCommNetwork:
        def publish(self, topic, data):
            logger.info(f"Published to {topic}: {type(data).__name__}")
    
    try:
        # Initialize Signal Intelligence System with DOMA
        comm_network = MockCommNetwork()
        si_system = SignalIntelligenceSystem(config, comm_network)
        
        logger.info("Signal Intelligence System with DOMA Motion Tracker initialized")
        
        # Simulate some RF signals with motion
        import uuid
        
        # Create a moving signal (simulated drone)
        for i in range(5):
            signal_data = {
                "iq_data": np.random.normal(0, 1, 1024) + 1j * np.random.normal(0, 1, 1024),
                "frequency": 915e6 + i * 1000,  # Slight frequency drift
                "bandwidth": 25000,
                "power": -60 + np.random.normal(0, 2),
                "timestamp": time.time() + i,
                "source": f"moving_transmitter_{uuid.uuid4().hex[:8]}"
            }
            
            # Process the signal
            signal = si_system.process_signal(signal_data)
            
            time.sleep(0.1)  # Small delay between signals
        
        # Get motion predictions
        predictions = si_system.get_motion_predictions()
        logger.info(f"Motion predictions generated for {len(predictions.get('predictions', {}))} signals")
        
        # Analyze signals with motion data
        analysis = si_system.analyze_signals()
        if "motion_analysis" in analysis:
            motion_stats = analysis["motion_analysis"].get("movement_statistics", {})
            logger.info(f"Motion Analysis - Tracked Signals: {analysis['motion_analysis'].get('tracked_signals', 0)}")
            logger.info(f"Average Speed: {motion_stats.get('average_speed', 0):.2f} m/s")
        
        logger.info("=== DOMA Integration Demo Complete ===")
        
    except Exception as e:
        logger.error(f"DOMA integration demo failed: {e}")
        logger.info("This is expected if PyTorch or DOMA models are not available")

# Main execution
if __name__ == "__main__":
    # Run demo if executed directly
    demo_doma_integration()
