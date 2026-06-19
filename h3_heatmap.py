"""
h3_heatmap.py — LandSAR-inspired probability heatmaps over H3 hexagonal cells.

Adapts LandSAR's ContainmentMap concept (gridded cell probabilities with Bayesian
update) to RF SCYTHE's geo-tagged hypergraph. Instead of forward-simulating
motion paths, we project RF evidence backward to probable source locations
through the semantic graph.

Architecture:
  ┌──────────────────────────────────────────────────────┐
  │ Evidence (geo nodes + edges)                           │
  │   geo_point, host w/ geoip, flow w/ src/dst geo        │
  ├──────────────────────────────────────────────────────┤
  │ Probability kernel per evidence source                  │
  │   Gaussian blob σ = f(confidence, source_type)          │
  ├──────────────────────────────────────────────────────┤
  │ H3 hexagonal grid accumulation                          │
  │   cell_weight[h3_index] += kernel_contribution          │
  ├──────────────────────────────────────────────────────┤
  │ Normalize → containment levels (50/90/99%)              │
  ├──────────────────────────────────────────────────────┤
  │ Output: { h3_index → weight, containment_level }        │
  │   → TAK polygons, Cesium tiles, API JSON                │
  └──────────────────────────────────────────────────────┘

H3 resolution guide:
  res 3 ≈ 12,400 km² per hex (continental)
  res 4 ≈  1,770 km² per hex (country/regional)
  res 5 ≈    252 km² per hex (metro area)
  res 6 ≈     36 km² per hex (city)
  res 7 ≈      5 km² per hex (neighborhood)
  res 8 ≈    0.74 km² per hex (block)
"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

Json = Dict[str, Any]

# ─── H3 abstraction layer ───────────────────────────────────────────────────
# Uses h3 library when available; falls back to a simple lat/lon grid.

try:
    import h3
    HAS_H3 = True
except ImportError:
    HAS_H3 = False
    logger.info("h3 library not installed. Using fallback lat/lon grid. pip install h3")


def _lat_lon_to_cell(lat: float, lon: float, resolution: int) -> str:
    """Convert lat/lon to a cell identifier."""
    if HAS_H3:
        return h3.latlng_to_cell(lat, lon, resolution)
    else:
        # Fallback: simple grid cells (not hexagonal, but functional)
        # Cell size roughly matches H3 at the given resolution
        cell_degrees = 360.0 / (7 ** (resolution - 1) * 10)  # approximate
        cell_degrees = max(cell_degrees, 0.001)
        grid_lat = round(lat / cell_degrees) * cell_degrees
        grid_lon = round(lon / cell_degrees) * cell_degrees
        return f"grid_{grid_lat:.4f}_{grid_lon:.4f}_r{resolution}"


def _cell_to_boundary(cell_id: str) -> List[Tuple[float, float]]:
    """Get polygon boundary of a cell as list of (lat, lon) tuples."""
    if HAS_H3:
        return list(h3.cell_to_boundary(cell_id))
    else:
        # Parse grid cell ID for fallback
        parts = cell_id.replace("grid_", "").split("_")
        if len(parts) >= 3:
            try:
                lat = float(parts[0])
                lon = float(parts[1])
                res = int(parts[2].replace("r", ""))
                cell_degrees = 360.0 / (7 ** (res - 1) * 10)
                cell_degrees = max(cell_degrees, 0.001)
                half = cell_degrees / 2.0
                return [
                    (lat - half, lon - half),
                    (lat - half, lon + half),
                    (lat + half, lon + half),
                    (lat + half, lon - half),
                    (lat - half, lon - half),
                ]
            except (ValueError, IndexError):
                pass
        return []


def _cell_to_latlng(cell_id: str) -> Tuple[float, float]:
    """Get center of a cell."""
    if HAS_H3:
        return h3.cell_to_latlng(cell_id)
    else:
        parts = cell_id.replace("grid_", "").split("_")
        if len(parts) >= 2:
            try:
                return (float(parts[0]), float(parts[1]))
            except (ValueError, IndexError):
                pass
        return (0.0, 0.0)


def _k_ring(cell_id: str, k: int) -> Set[str]:
    """Get k-ring neighbors of a cell."""
    if HAS_H3:
        return set(h3.grid_disk(cell_id, k))
    else:
        # For fallback, just return the cell itself (no neighbor expansion)
        return {cell_id}


# ─── Probability kernel ─────────────────────────────────────────────────────

# Source type → (base_sigma_km, decay_k_rings)
# Lower sigma = more confident location
SOURCE_SIGMA = {
    "geoip_city":    (25.0, 2),    # city-level GeoIP: ~25km uncertainty
    "geoip_country": (250.0, 3),   # country-level: ~250km
    "gps_precise":   (0.1, 0),     # precision GPS: 100m
    "asn_region":    (100.0, 3),   # ASN regional presence: ~100km
    "dns_anycast":   (500.0, 4),   # DNS anycast: very uncertain
    "rf_aoa":        (5.0, 1),     # RF angle-of-arrival bearing: ~5km
    "rf_tdoa":       (1.0, 1),     # time-difference-of-arrival: ~1km
    "default":       (50.0, 2),    # default uncertainty
}


def _classify_source(node: Json) -> str:
    """Classify a geo-bearing node's source type for sigma selection."""
    labels = node.get("labels") or {}
    meta = node.get("metadata") or {}

    # Check for precise source markers
    source = meta.get("provenance", {}).get("source", "")
    if source == "gps":
        return "gps_precise"
    if source == "rf_aoa" or meta.get("method") == "aoa":
        return "rf_aoa"
    if source == "rf_tdoa" or meta.get("method") == "tdoa":
        return "rf_tdoa"

    kind = node.get("kind", "")
    if kind == "geo_point":
        # Check how granular the geo data is
        if labels.get("city"):
            return "geoip_city"
        elif labels.get("country"):
            return "geoip_country"
        else:
            return "default"
    elif kind == "host":
        geo = labels.get("geo") or meta.get("geo") or {}
        if isinstance(geo, dict) and geo.get("city"):
            return "geoip_city"
        return "geoip_country"
    elif kind == "asn":
        return "asn_region"
    elif kind == "dns_name":
        return "dns_anycast"
    else:
        return "default"


def _gaussian_weight(distance_km: float, sigma_km: float) -> float:
    """Gaussian decay: w = exp(-d² / 2σ²)"""
    if sigma_km <= 0:
        return 1.0 if distance_km < 0.01 else 0.0
    return math.exp(-(distance_km ** 2) / (2.0 * sigma_km ** 2))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ─── Geo extraction (shared with cot_export) ────────────────────────────────

def _extract_geo(node: Json) -> Optional[Tuple[float, float]]:
    """Extract (lat, lon) from a node."""
    labels = node.get("labels") or {}
    meta = node.get("metadata") or {}

    lat = labels.get("lat") or meta.get("lat")
    lon = labels.get("lon") or meta.get("lon")
    if lat is not None and lon is not None:
        try:
            return (float(lat), float(lon))
        except (ValueError, TypeError):
            pass

    geo = labels.get("geo") or meta.get("geo") or meta.get("geoip")
    if isinstance(geo, dict):
        lat = geo.get("lat") or geo.get("latitude")
        lon = geo.get("lon") or geo.get("longitude") or geo.get("lng")
        if lat is not None and lon is not None:
            try:
                return (float(lat), float(lon))
            except (ValueError, TypeError):
                pass

    return None


# ─── HeatmapLayer ───────────────────────────────────────────────────────────

class HeatmapLayer:
    """
    A single probability heatmap layer over H3 cells.

    Inspired by LandSAR's ContainmentMap: maintains a weighted grid of cells
    with containment level computation (50%, 90%, 99%).

    Usage:
        layer = HeatmapLayer(resolution=6)
        layer.add_evidence(nodes, edges)
        result = layer.to_dict()
    """

    def __init__(
        self,
        resolution: int = 6,
        label: str = "rf_scythe",
        decay_rings: int = 2,
    ):
        self.resolution = resolution
        self.label = label
        self.decay_rings = decay_rings
        self.cells: Dict[str, float] = defaultdict(float)  # cell_id → raw weight
        self.cell_sources: Dict[str, List[str]] = defaultdict(list)  # cell_id → contributing node IDs
        self.total_weight = 0.0
        self.created_at = time.time()

    def add_point(
        self,
        lat: float,
        lon: float,
        *,
        weight: float = 1.0,
        sigma_km: float = 50.0,
        source_id: str = "",
        k_rings: int = 2,
    ) -> None:
        """Add a probability kernel at a geographic point.

        The kernel is a Gaussian blob centered at (lat, lon) with spread σ,
        accumulated into H3 cells at the configured resolution.
        """
        center_cell = _lat_lon_to_cell(lat, lon, self.resolution)
        k = min(k_rings, self.decay_rings, 5)  # cap expansion

        cells_to_update = _k_ring(center_cell, k)

        for cell in cells_to_update:
            cell_center = _cell_to_latlng(cell)
            dist = _haversine_km(lat, lon, cell_center[0], cell_center[1])
            w = _gaussian_weight(dist, sigma_km) * weight
            if w > 1e-10:
                self.cells[cell] += w
                self.total_weight += w
                if source_id and source_id not in self.cell_sources[cell]:
                    self.cell_sources[cell].append(source_id)

    def add_evidence(
        self,
        nodes: List[Json],
        edges: Optional[List[Json]] = None,
        *,
        obs_classes: Optional[Set[str]] = None,
        min_confidence: float = 0.0,
        kind_filter: Optional[Set[str]] = None,
    ) -> int:
        """Process hypergraph nodes as evidence, adding probability kernels.

        Implements the LandSAR concept: each piece of evidence contributes
        a weighted probability kernel based on:
          - confidence (from obs_class metadata)
          - source type (classification drives sigma)
          - geographic precision (city vs country vs GPS)

        Returns: number of evidence points processed.
        """
        count = 0

        for node in nodes:
            geo = _extract_geo(node)
            if geo is None:
                continue

            lat, lon = geo
            nid = node.get("id", "")
            kind = node.get("kind", "")
            meta = node.get("metadata") or {}
            obs_class = meta.get("obs_class", "observed")
            confidence = meta.get("confidence", 1.0)

            # Filter
            if obs_classes and obs_class not in obs_classes:
                continue
            if confidence < min_confidence:
                continue
            if kind_filter and kind not in kind_filter:
                continue

            # Classify source and determine kernel parameters
            source_type = _classify_source(node)
            sigma_km, k_rings = SOURCE_SIGMA.get(source_type, SOURCE_SIGMA["default"])

            # Weight = confidence (higher confidence = stronger signal)
            w = float(confidence)

            self.add_point(
                lat, lon,
                weight=w,
                sigma_km=sigma_km,
                source_id=nid,
                k_rings=k_rings,
            )
            count += 1

        # Also process edges for cross-evidence reinforcement
        if edges:
            node_lookup = {n.get("id", ""): n for n in nodes}
            for edge in edges:
                edge_nodes = edge.get("nodes", [])
                emeta = edge.get("metadata") or {}
                econf = emeta.get("confidence", 1.0)
                eobs = emeta.get("obs_class", "observed")

                if obs_classes and eobs not in obs_classes:
                    continue
                if econf < min_confidence:
                    continue

                # For edges connecting two geo-bearing nodes,
                # reinforce probability along the path
                for nid in edge_nodes:
                    n = node_lookup.get(nid)
                    if n and _extract_geo(n):
                        geo = _extract_geo(n)
                        if geo:
                            # Small reinforcement from edge evidence
                            self.add_point(
                                geo[0], geo[1],
                                weight=econf * 0.25,
                                sigma_km=SOURCE_SIGMA["default"][0],
                                source_id=edge.get("id", ""),
                                k_rings=1,
                            )

        logger.info(f"[Heatmap] Added {count} evidence points, {len(self.cells)} cells")
        return count

    def normalize(self) -> None:
        """Normalize cell weights to sum to 1.0 (probability distribution)."""
        if self.total_weight <= 0:
            return
        factor = 1.0 / self.total_weight
        for cell in self.cells:
            self.cells[cell] *= factor
        self.total_weight = 1.0

    def containment_levels(self) -> Dict[str, float]:
        """Compute containment levels (LandSAR-style).

        Returns dict mapping cell_id → containment_level where:
          0.50 = cell is in the 50% containment region (most likely area)
          0.90 = cell is in the 90% containment region
          0.99 = cell is in the 99% containment region
          1.00 = outside all regions

        Cells are sorted by probability descending; cumulative sum determines
        which containment region each cell belongs to.
        """
        if not self.cells:
            return {}

        self.normalize()

        # Sort cells by weight descending
        sorted_cells = sorted(self.cells.items(), key=lambda x: x[1], reverse=True)

        levels = {}
        cumulative = 0.0
        for cell_id, weight in sorted_cells:
            cumulative += weight
            if cumulative <= 0.50:
                levels[cell_id] = 0.50
            elif cumulative <= 0.90:
                levels[cell_id] = 0.90
            elif cumulative <= 0.99:
                levels[cell_id] = 0.99
            else:
                levels[cell_id] = 1.00

        return levels

    def to_dict(self, *, include_boundaries: bool = False, top_n: int = 0) -> Json:
        """Export heatmap as JSON-serializable dict.

        Args:
            include_boundaries: Include polygon boundaries for each cell.
            top_n: Only include top N cells by weight. 0 = all.

        Returns:
            {
              "resolution": int,
              "label": str,
              "total_cells": int,
              "total_weight": float,
              "containment_levels": {"50": int, "90": int, "99": int},
              "cells": [
                {
                  "h3": str,
                  "weight": float,
                  "level": float,
                  "center": [lat, lon],
                  "sources": [node_ids...],
                  "boundary"?: [[lat,lon], ...]
                }
              ]
            }
        """
        self.normalize()
        levels = self.containment_levels()

        sorted_cells = sorted(self.cells.items(), key=lambda x: x[1], reverse=True)
        if top_n > 0:
            sorted_cells = sorted_cells[:top_n]

        cells_list = []
        level_counts = {0.50: 0, 0.90: 0, 0.99: 0}

        for cell_id, weight in sorted_cells:
            level = levels.get(cell_id, 1.0)
            center = _cell_to_latlng(cell_id)
            entry: Json = {
                "h3": cell_id,
                "weight": round(weight, 8),
                "level": level,
                "center": [round(center[0], 6), round(center[1], 6)],
                "sources": self.cell_sources.get(cell_id, [])[:5],
            }
            if include_boundaries:
                boundary = _cell_to_boundary(cell_id)
                entry["boundary"] = [[round(p[0], 6), round(p[1], 6)] for p in boundary]

            cells_list.append(entry)

            if level <= 0.99:
                for lev in [0.50, 0.90, 0.99]:
                    if level <= lev:
                        level_counts[lev] += 1

        return {
            "resolution": self.resolution,
            "label": self.label,
            "total_cells": len(self.cells),
            "total_weight": round(self.total_weight, 6),
            "created_at": self.created_at,
            "h3_available": HAS_H3,
            "containment_levels": {
                "50": level_counts.get(0.50, 0),
                "90": level_counts.get(0.90, 0),
                "99": level_counts.get(0.99, 0),
            },
            "cells": cells_list,
        }

    def to_geojson(self, *, top_n: int = 500) -> Json:
        """Export as GeoJSON FeatureCollection for mapping libraries.

        Returns GeoJSON with polygon features colored by containment level.
        """
        self.normalize()
        levels = self.containment_levels()
        sorted_cells = sorted(self.cells.items(), key=lambda x: x[1], reverse=True)
        if top_n > 0:
            sorted_cells = sorted_cells[:top_n]

        level_colors = {
            0.50: "#ff0000",  # red — highest probability
            0.90: "#ff8c00",  # orange — high probability
            0.99: "#ffff00",  # yellow — moderate
            1.00: "#44ff44",  # green — low
        }

        features = []
        for cell_id, weight in sorted_cells:
            level = levels.get(cell_id, 1.0)
            boundary = _cell_to_boundary(cell_id)
            if not boundary:
                continue

            # GeoJSON uses [lon, lat] order
            coords = [[p[1], p[0]] for p in boundary]
            if coords and coords[0] != coords[-1]:
                coords.append(coords[0])  # close the ring

            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [coords],
                },
                "properties": {
                    "h3": cell_id,
                    "weight": round(weight, 8),
                    "level": level,
                    "fill": level_colors.get(level, "#888888"),
                    "fill-opacity": min(0.8, weight * 20 + 0.1),
                    "stroke": level_colors.get(level, "#888888"),
                    "stroke-width": 1,
                    "sources": self.cell_sources.get(cell_id, [])[:3],
                },
            }
            features.append(feature)

        return {
            "type": "FeatureCollection",
            "features": features,
        }

    def to_kml(self, *, top_n: int = 200) -> str:
        """Export as KML for TAK/Google Earth import.

        Returns KML string with colored polygons per cell.
        """
        self.normalize()
        levels = self.containment_levels()
        sorted_cells = sorted(self.cells.items(), key=lambda x: x[1], reverse=True)
        if top_n > 0:
            sorted_cells = sorted_cells[:top_n]

        # KML colors are AABBGGRR (alpha, blue, green, red)
        level_kml_colors = {
            0.50: "cc0000ff",  # red
            0.90: "cc008cff",  # orange
            0.99: "cc00ffff",  # yellow
            1.00: "6644ff44",  # green (semi-transparent)
        }

        placemarks = []
        for cell_id, weight in sorted_cells:
            level = levels.get(cell_id, 1.0)
            boundary = _cell_to_boundary(cell_id)
            if not boundary:
                continue

            # KML coordinates: lon,lat,alt separated by spaces
            coords_str = " ".join(f"{p[1]:.6f},{p[0]:.6f},0" for p in boundary)
            color = level_kml_colors.get(level, "66888888")

            placemarks.append(f"""    <Placemark>
      <name>{cell_id}</name>
      <description>Weight: {weight:.6f}, Level: {level}</description>
      <Style>
        <PolyStyle>
          <color>{color}</color>
          <outline>1</outline>
        </PolyStyle>
        <LineStyle>
          <color>{color}</color>
          <width>1</width>
        </LineStyle>
      </Style>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>{coords_str}</coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>""")

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>RF SCYTHE Heatmap - {self.label}</name>
  <description>Resolution: {self.resolution}, Cells: {len(self.cells)}</description>
{"".join(placemarks)}
</Document>
</kml>"""


# ─── Convenience API ─────────────────────────────────────────────────────────

def generate_heatmap(
    nodes: List[Json],
    edges: Optional[List[Json]] = None,
    *,
    resolution: int = 6,
    obs_classes: Optional[Set[str]] = None,
    min_confidence: float = 0.0,
    kind_filter: Optional[Set[str]] = None,
    label: str = "rf_scythe",
) -> HeatmapLayer:
    """One-shot heatmap generation from a hypergraph snapshot.

    This is the public API for endpoint integration.

    Returns:
        A HeatmapLayer ready for .to_dict(), .to_geojson(), or .to_kml().
    """
    layer = HeatmapLayer(resolution=resolution, label=label)
    layer.add_evidence(
        nodes,
        edges,
        obs_classes=obs_classes,
        min_confidence=min_confidence,
        kind_filter=kind_filter,
    )
    return layer


def bayesian_update(
    prior: HeatmapLayer,
    negative_scan_cells: Set[str],
    detection_probability: float = 0.8,
) -> HeatmapLayer:
    """Bayesian update: downweight cells that were scanned without detection.

    Implements the same logic as LandSAR's InternalModel.calcOverallSampleWeights():
      P(source in cell | not detected) ∝ P(cell) × (1 - P_d)

    Args:
        prior: The current heatmap layer.
        negative_scan_cells: Set of H3 cell IDs where a scan found nothing.
        detection_probability: P(detection | source present in cell).

    Returns:
        New HeatmapLayer with updated weights.
    """
    posterior = HeatmapLayer(
        resolution=prior.resolution,
        label=prior.label,
        decay_rings=prior.decay_rings,
    )

    non_detection_factor = 1.0 - detection_probability

    for cell_id, weight in prior.cells.items():
        if cell_id in negative_scan_cells:
            # Downweight by P(not detected)
            posterior.cells[cell_id] = weight * non_detection_factor
        else:
            posterior.cells[cell_id] = weight

        posterior.cell_sources[cell_id] = list(prior.cell_sources.get(cell_id, []))

    posterior.total_weight = sum(posterior.cells.values())
    posterior.normalize()

    logger.info(
        f"[Heatmap] Bayesian update: scanned {len(negative_scan_cells)} cells, "
        f"P_d={detection_probability:.2f}, remaining cells={len(posterior.cells)}"
    )
    return posterior
