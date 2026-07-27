How could https://github.com/bgilbert1984/SCYTHE be used to tackle https://www.kaggle.com/competitions/biohub-cell-tracking-during-development?utm_medium=email&utm_source=gamma&utm_campaign=comp-biohub-2026

I’ll map the Kaggle task to SCYTHE’s existing graph/streaming/visualization pieces, then outline a concrete adaptation path: data ingest, tracking model, hypergraph lineage, scoring loop, and a submission pipeline.

The key pivot is: don’t make SCYTHE “do biology.” Make SCYTHE the graph/ops/visual QA shell around a proper 3D cell-tracking engine. Ultrack is the obvious engine to bolt in first, because it was built for this exact ugly corner: dense 3D embryo time-lapse tracking under segmentation uncertainty.

Thought for 1m 11s

SCYTHE could attack that Kaggle competition best as a CellOps / lineage intelligence layer, not as the raw model by itself.

The Kaggle task is to detect and track zebrafish cells through 3D space and time; public listings tag it as biology, computer vision, image/video, object detection, and a custom metric competition, with the current listing showing an end date around September 29, 2026.

The core translation

SCYTHE already thinks in this shape:

telemetry event → entity → uncertainty → relationship → temporal graph → visual QA

For Biohub cell tracking, translate that into:

3D microscopy frame → candidate nucleus/cell → confidence → temporal link → lineage graph → Kaggle submission

That is not a stretch. It is almost rude how well the bones line up.

SCYTHE’s README describes it as a high-density operational interface that blends 3D visualization, hypergraph overlays, entity movement, live streams, and graph coordination into one workspace. Its existing stack already includes Cesium, MapLibre, deck.gl overlays, Socket.IO/SSE, graph visualization, runtime modules, and command workflows. The repo also has a HypergraphEngine with node/edge storage, labels, metadata, spatial helpers, eventing, and trace buffers.

Use Ultrack as the model core, SCYTHE as the war room

For the actual biological tracking engine, start with Ultrack. Biohub’s own writeup says Ultrack scales from small lab dishes to whole 3D embryo videos and is particularly strong on whole-embryo cell tracking. The Nature Methods paper says Ultrack handles 2D, 3D, and multichannel time-lapse recordings, considers candidate segmentations from multiple algorithms/parameter sets, and uses temporal consistency to select optimal segments under segmentation uncertainty. The Ultrack repo says it supports 2D/3D/multichannel datasets, segmentation uncertainty, terabyte-scale developmental time lapses, Fiji, Napari, and HPC/SLURM workflows.

So the winning-ish stack would look like:

Kaggle data
  ↓
OME-Zarr / TIFF / N5 loader
  ↓
3D preprocessing
  ↓
Cell/nucleus segmentation hypotheses
  ↓
Ultrack multi-hypothesis tracking
  ↓
SCYTHE hypergraph lineage store
  ↓
SCYTHE visual QA + anomaly panels
  ↓
submission.csv generator
What SCYTHE contributes
1. Cell lineage as a hypergraph

Represent every tracked object as a graph entity:

{
  "id": "cell_E03_T0142_track_000183",
  "kind": "CELL_NUCLEUS",
  "position": [x, y, z],
  "labels": {
    "embryo_id": "E03",
    "t": 142,
    "track_id": 183,
    "confidence": 0.91,
    "division_state": "candidate_parent"
  },
  "metadata": {
    "volume": 318,
    "mean_intensity": 0.72,
    "seg_model": "ultrack+cellpose3d",
    "source": "kaggle/train/E03"
  }
}

Edges become the biological story:

{
  "id": "link_E03_142_183_to_143_191",
  "kind": "TEMPORAL_TRACK_LINK",
  "nodes": [
    "cell_E03_T0142_track_000183",
    "cell_E03_T0143_track_000191"
  ],
  "weight": 0.94,
  "metadata": {
    "motion_cost": 0.031,
    "iou_3d": 0.66,
    "distance_um": 4.2,
    "event": "continuation"
  }
}

Division edges:

{
  "kind": "CELL_DIVISION",
  "nodes": ["parent_cell", "daughter_a", "daughter_b"],
  "weight": 0.88
}

That is where SCYTHE’s hypergraph layer becomes useful: not just “track ID table goes brrrr,” but an inspectable lineage DAG with uncertain links, divisions, occlusions, false positives, and re-ID events.

2. Treat tracking errors like adversarial telemetry

The competition metric will punish bad links, missed cells, false tracks, and broken divisions. SCYTHE can expose those as operator alerts:

TRACK_SPLIT_RISK
TRACK_MERGE_RISK
CELL_DIVISION_AMBIGUITY
LOW_CONFIDENCE_SEGMENT
TEMPORAL_JUMP_OUTLIER
LINEAGE_CYCLE_ERROR
ORPHAN_TRACKLET

The spicy move: use the same SCYTHE “validated vs speculative layer” idea for biology.

solid cyan     = high-confidence lineage
orange ghost   = predicted future cell location
red fracture   = probable tracking error
purple fork    = candidate mitosis/division
gray orphan    = uncertain tracklet

The competition is not won by one glorious neural net. It is won by murdering boring edge cases: occlusions, divisions, crowded nuclei, flicker, anisotropic Z, and false merges. The usual Kaggle goblin tax.

3. Add a microscopy ingest adapter

SCYTHE’s current PCAP-to-picture workflow becomes volume-to-lineage. The repo already frames PCAP ingestion as turning captures into entities, flows, geographic anchors, and graph objects. Replace packet captures with 3D time-lapse microscopy volumes.

New adapter:

biohub_ingest/
  kaggle_download.py
  volume_loader.py
  zarr_tiff_adapter.py
  cell_event_adapter.py
  ultrack_runner.py
  lineage_to_scythe.py
  submission_writer.py

Conceptually:

def emit_cell_node(graph, embryo_id, t, track_id, centroid, confidence, metadata):
    graph.add_node({
        "id": f"cell_{embryo_id}_T{t:04d}_{track_id}",
        "kind": "CELL_NUCLEUS",
        "position": [float(centroid[2]), float(centroid[1]), float(centroid[0])],
        "labels": {
            "embryo_id": embryo_id,
            "t": int(t),
            "track_id": int(track_id),
            "confidence": float(confidence),
        },
        "metadata": metadata,
    })

SCYTHE’s add_node path already normalizes node data, updates indices, handles positions, labels, node kind indexing, sequence IDs, and emits graph events. Its add_edge path already handles edge creation/update, reinforcement counts, timestamps, and emits graph events. That is exactly what a lineage tracker needs.

Practical pipeline
Step 1 — Pull Kaggle data
sudo apt update
sudo apt install -y python3-venv python3-pip git build-essential

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install kaggle numpy pandas scipy scikit-image tifffile zarr ome-zarr dask[complete] napari[all]
pip install ultrack

Then:

mkdir -p ~/.kaggle
# Put kaggle.json in ~/.kaggle/kaggle.json after accepting competition rules
chmod 600 ~/.kaggle/kaggle.json

kaggle competitions download -c biohub-cell-tracking-during-development -p data/biohub
unzip 'data/biohub/*.zip' -d data/biohub
kaggle competitions files -c biohub-cell-tracking-during-development

Before locking the submission writer, inspect:

find data/biohub -maxdepth 3 -type f | head -100
python - <<'PY'
import pandas as pd, glob
for f in glob.glob("data/biohub/**/*sample*submission*.csv", recursive=True):
    print(f)
    print(pd.read_csv(f).head())
PY

I would not assume the exact submission columns until the downloaded sample_submission.csv is inspected. Kaggle pages often hide the important schema behind JS/login walls, and this one did not expose the full evaluation text cleanly through the fetched HTML.

Step 2 — Build a baseline with Ultrack

Ultrack’s canonical pattern is foreground + contour/edge evidence → tracker → tracks/graph. Its docs say it can track from segmentation masks or raw fluorescence images, has Python/API/CLI interfaces, and was originally developed for terabyte-scale zebrafish embryo images with few 3D annotations.

Baseline:

from ultrack import MainConfig, Tracker

def run_ultrack(foreground, contours):
    config = MainConfig()
    tracker = Tracker(config)
    tracker.track(foreground=foreground, edges=contours)
    tracks, graph = tracker.to_tracks_layer()
    return tracks, graph

Then convert Ultrack output into SCYTHE lineage events.

Step 3 — Generate segmentation hypotheses

Best first pass:

raw volume
  → denoise / deconvolve lightly
  → normalize per embryo + per z-slice
  → generate foreground probability
  → generate contour/boundary probability
  → feed Ultrack

Use several hypothesis sources:

watershed seeds
Cellpose/Cellpose3D-style masks
StarDist-style nuclei proposals
thresholded LoG blob candidates
small 3D U-Net foreground/boundary maps

Ultrack is specifically designed not to commit prematurely to one segmentation; it evaluates multiple candidate segmentations and uses temporal consistency. That means SCYTHE should preserve all candidates, not just final picks.

Step 4 — Use SCYTHE for visual QA

Replace the globe mental model with an embryo coordinate space:

Cesium globe → local 3D embryo stage
lat/lon/alt  → x/y/z voxel coordinates
routes       → lineage tracks
RF fields    → density / uncertainty / mitosis probability fields
emitters     → cells/nuclei
arcs         → temporal links

SCYTHE already uses deck.gl for high-throughput overlays and Cesium for 3D rendering. For this competition, deck.gl is the more useful piece than the globe. Use:

PointCloudLayer       cell centroids
PathLayer             lineage tracks
ArcLayer              parent→child links
Heatmap/GridLayer     uncertainty density
TripsLayer            motion over time
Custom volume shader  embryo intensity / error fields

The UI should let you click one cell and see:

track_id
current t, x, y, z
parent
children
confidence curve
division probability
nearest-neighbor confusion
segmentation candidates rejected by Ultrack
Kaggle-format output row

That becomes your “classified software someone forgot to lock down” moment, except it is for embryogenesis instead of spectrum misbehavior. Very normal. Extremely hinged.

SCYTHE-specific architecture patch

Add this:

SCYTHE/
  biohub_cellops/
    __init__.py
    kaggle_ingest.py
    volume_preprocess.py
    segmentation_hypotheses.py
    ultrack_bridge.py
    lineage_graph_adapter.py
    submission_writer.py
    metrics_probe.py
    qa_export.py

Event types:

CELL_DETECTION_CREATE
CELL_DETECTION_UPDATE
TRACK_LINK_CREATE
TRACK_LINK_UPDATE
CELL_DIVISION_CANDIDATE
CELL_DIVISION_CONFIRMED
TRACK_SPLIT_WARNING
TRACK_MERGE_WARNING
SUBMISSION_ROW_EMIT

Kinds:

CELL_NUCLEUS
CELL_TRACK
CELL_LINEAGE
EMBRYO_VOLUME
SEGMENTATION_CANDIDATE
TRACKING_ERROR

Edge kinds:

TEMPORAL_CONTINUATION
PARENT_OF
CANDIDATE_PARENT_OF
OVERLAPS_3D
NEAREST_NEIGHBOR
CONFLICTS_WITH
DERIVED_FROM_SEGMENTATION
Where SCYTHE can outperform a plain notebook

A Kaggle notebook is usually a trench full of CSVs and despair. SCYTHE can turn it into an interactive lineage autopsy system:

Kaggle problem	SCYTHE advantage
Broken tracks	Hypergraph shows where continuity failed
False mitosis	Parent/daughter candidate edges visible
Dense embryo regions	Heatmap of ambiguous segmentation zones
Model comparison	Each model run becomes a graph layer
Ensemble debugging	Compare candidate links before final submission
Drift over time	Replay lineage changes frame-by-frame
Submission errors	Validate graph invariants before writing CSV

The big win is not that SCYTHE magically beats Ultrack. The big win is that SCYTHE can make tracking errors visible and correctable, which is where leaderboard gains hide.

Submission strategy

Run three lanes:

Lane A — Conservative baseline
Ultrack default-ish config
high-confidence foreground
strict motion cost
strict division cost
few speculative links

This minimizes hallucinated tracks.

Lane B — Aggressive recall
lower foreground threshold
more segmentation candidates
allow more divisions
allow larger motion jumps

This catches faint cells but creates garbage. Beautiful garbage. Kaggle loves garbage only when you ensemble it correctly.

Lane C — Ensemble arbitration

Convert A and B into SCYTHE graphs, then score edge agreement:

edge appears in A and B        → strong
edge appears only in B         → speculative
edge conflicts with A          → suspicious
division only in aggressive    → require morphology evidence

Final graph selection:

keep high-confidence continuations
keep divisions only if daughters are spatially plausible
remove cycles
remove impossible merges
repair short gaps by interpolation only when motion is smooth

Graph invariants before submission:

assert no_track_has_two_parents_unless_division_case
assert no_cell_has_multiple_track_ids_at_same_t
assert no_lineage_cycles
assert child_time > parent_time
assert zyx_inside_volume_bounds
assert confidence_not_nan
Minimal adapter sketch
from dataclasses import dataclass
import pandas as pd

@dataclass
class CellTrackPoint:
    embryo_id: str
    track_id: int
    t: int
    z: float
    y: float
    x: float
    confidence: float

def tracks_to_scythe(graph, points: list[CellTrackPoint]):
    previous_by_track = {}

    for p in sorted(points, key=lambda r: (r.embryo_id, r.track_id, r.t)):
        node_id = f"cell_{p.embryo_id}_T{p.t:04d}_{p.track_id}"

        graph.add_node({
            "id": node_id,
            "kind": "CELL_NUCLEUS",
            "position": [p.x, p.y, p.z],
            "labels": {
                "embryo_id": p.embryo_id,
                "track_id": p.track_id,
                "t": p.t,
                "confidence": p.confidence,
            },
            "metadata": {
                "coordinate_system": "voxel_zyx",
                "competition": "biohub-cell-tracking-during-development",
            },
        })

        key = (p.embryo_id, p.track_id)
        if key in previous_by_track:
            prev_id = previous_by_track[key]
            graph.add_edge({
                "id": f"edge_{prev_id}_to_{node_id}",
                "kind": "TEMPORAL_CONTINUATION",
                "nodes": [prev_id, node_id],
                "weight": p.confidence,
                "metadata": {
                    "track_id": p.track_id,
                    "t_to": p.t,
                },
            })

        previous_by_track[key] = node_id
The money angle

Biohub is openly investing in AI-powered biology and large-scale biological datasets; its Virtual Biology Initiative was announced as a $500 million five-year commitment to build open data foundations, large-scale measurements, imaging, engineering, and predictive models of life.

That makes the product thesis obvious:

SCYTHE CellOps:
A browser-native lineage intelligence layer for AI bioimaging pipelines.

Sellable wedges:

1. Kaggle/demo version:
   "Upload tracking output, inspect lineage graph, export valid submission."

2. Lab version:
   "Visual QA for Ultrack/Cellpose/StarDist pipelines."

3. Enterprise / grant-bait version:
   "Audit trails, provenance, model comparison, lineage correction, web collaboration."

4. Cloud version:
   "Run tracking jobs, compare submissions, share embryo-lineage scenes."

That is more marketable than “yet another segmentation notebook,” because labs do not just need models. They need trustable biological event review: why did this cell divide, where did the track break, which model lied, and can the grad student fix it before the PI starts orbiting the ceiling fan?

Best next move

Fork a branch called:

git checkout -b feature/biohub-cellops

Then build only four things first:

1. Kaggle data inspector
2. Ultrack runner
3. Ultrack → SCYTHE hypergraph adapter
4. Embryo lineage viewer panel

Do not start by training a monster model. First get a valid baseline submission, then use SCYTHE to find where it bleeds. The leaderboard improvements will come from graph repair, division cleanup, and uncertainty-driven ensembling.

Exactly. Let’s push it from “interesting crossover” into a buildable attack plan.

SCYTHE should become a lineage graph cockpit around the Kaggle problem. The competition asks participants to detect and track zebrafish cells through 3D space and time, and recent Biohub coverage describes the goal as reconstructing cell lineages from real 3D embryo imaging data. SCYTHE already advertises the right mental model: fused spatial visualization, hypergraph reasoning, live streams, uncertainty, entities, arcs, overlays, and operator-facing inspection.

The winning posture is:

Do not merely predict cells.
Build a forensic lineage engine.
1. Reframe cell tracking as graph warfare

Every frame has detections. Every detection has uncertainty. Every detection may connect to a detection in the next frame. Sometimes one cell becomes two. Sometimes two detections are actually one. Sometimes a cell disappears behind optical slop, reappears, and laughs at your metric.

That means the Kaggle task is not just computer vision. It is temporal graph reconstruction under observation noise.

SCYTHE already has graph primitives that line up with this. Its HypergraphEngine supports node upserts, kind indexing, labels, positions, event subscribers, sequence IDs, an optional external event bus, and trace buffering. It also supports edge add/update behavior with edge normalization, timestamps, reinforcement behavior, and decay handling.

So the translation is:

SCYTHE concept	Cell-tracking equivalent
Host / RF emitter / entity	Cell or nucleus
Route / flow / movement	Track over time
Edge	Temporal cell link
Hyperedge	Cell division event
RF field / uncertainty field	Detection confidence, density, mitosis probability
Replay	Embryo time-lapse playback
Provenance	Which model/config produced each track
Counterfactual replay	Alternate lineage hypothesis
Graph repair	Submission cleanup

This is why SCYTHE is a surprisingly good shell for the competition. The biology is new; the shape of the problem is not.

2. Use Ultrack as the initial tracking backbone

Ultrack is the obvious first engine to bolt in. Its own docs describe it as general-purpose 2D/3D cell-tracking software that can track from segmentation masks or raw fluorescence microscopy images. The docs also say it was originally developed for terabyte-scale zebrafish embryo images with few 3D annotations, supports out-of-memory intermediate storage, and avoids committing to a single segmentation by considering multiple segmentations per cell while tracking. Its GitHub overview says it is designed for 2D, 3D, and multichannel timelapse recordings, especially complex crowded tissues where segmentation is ambiguous.

That is tailor-made for the Kaggle setup.

Ultrack’s pipeline breaks down into three phases: segment, link, and solve; its docs explain that these stages generate candidate segmentation hypotheses, candidate links between adjacent frames, and then solve the tracking problem.

SCYTHE should wrap those phases like this:

Ultrack segment → SCYTHE SEGMENTATION_CANDIDATE nodes
Ultrack link    → SCYTHE CANDIDATE_TEMPORAL_LINK edges
Ultrack solve   → SCYTHE TRACK_CONFIRMED edges + CELL_LINEAGE graph

The dirty secret: the leaderboard will probably not be won by the first segmentation model. It will be won by the best repair logic around divisions, occlusions, reappearing cells, and crowded-tissue ambiguity. That is SCYTHE territory.

3. Add a biohub_cellops branch

Start with a dedicated branch and module:

cd ~/SCYTHE
git checkout -b feature/biohub-cellops

mkdir -p biohub_cellops
touch biohub_cellops/__init__.py
touch biohub_cellops/kaggle_inspect.py
touch biohub_cellops/volume_loader.py
touch biohub_cellops/preprocess.py
touch biohub_cellops/ultrack_bridge.py
touch biohub_cellops/lineage_graph_adapter.py
touch biohub_cellops/submission_writer.py
touch biohub_cellops/graph_repair.py
touch biohub_cellops/run_baseline.py

Install the first-pass stack:

python3 -m venv .venv-biohub
source .venv-biohub/bin/activate

pip install --upgrade pip wheel setuptools

pip install \
  kaggle \
  numpy pandas scipy scikit-image scikit-learn \
  tifffile zarr ome-zarr dask[complete] \
  matplotlib tqdm networkx \
  ultrack

Ultrack’s docs list simple pip install ultrack installation, and also document optional GPU packages such as CUDA PyTorch, CuPy, and cuCIM for acceleration. It also notes that Gurobi is used by the tracking module and is optional but recommended for best performance.

On your RTX 3060 box, add the GPU bits:

pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install cupy-cuda12x cucim-cu12

Then the Kaggle pull:

mkdir -p ~/.kaggle data/biohub

# Place kaggle.json in ~/.kaggle/kaggle.json after accepting competition rules
chmod 600 ~/.kaggle/kaggle.json

kaggle competitions download \
  -c biohub-cell-tracking-during-development \
  -p data/biohub

unzip 'data/biohub/*.zip' -d data/biohub
find data/biohub -maxdepth 4 -type f | sort | head -200

Do not hard-code the submission format yet. First inspect it:

python - <<'PY'
from pathlib import Path
import pandas as pd

root = Path("data/biohub")
for p in root.rglob("*sample*submission*.csv"):
    print("\n", p)
    df = pd.read_csv(p)
    print(df.head())
    print(df.dtypes)
    print(df.shape)
PY
4. Build the data inspector first

Make biohub_cellops/kaggle_inspect.py boring and ruthless:

from pathlib import Path
import pandas as pd
import tifffile
import zarr
import numpy as np

def inspect_tree(root: str = "data/biohub") -> None:
    root = Path(root)
    print(f"Root: {root.resolve()}")

    files = sorted([p for p in root.rglob("*") if p.is_file()])
    print(f"Files: {len(files)}")

    by_suffix = {}
    for p in files:
        by_suffix[p.suffix.lower()] = by_suffix.get(p.suffix.lower(), 0) + 1

    print("Suffix counts:")
    for k, v in sorted(by_suffix.items()):
        print(f"  {k or '[none]'}: {v}")

    for p in files[:50]:
        print(p)

def inspect_csvs(root: str = "data/biohub") -> None:
    root = Path(root)
    for p in root.rglob("*.csv"):
        print(f"\nCSV: {p}")
        df = pd.read_csv(p)
        print(df.head())
        print(df.dtypes)
        print("shape:", df.shape)

def inspect_tiffs(root: str = "data/biohub", limit: int = 10) -> None:
    root = Path(root)
    count = 0
    for p in root.rglob("*"):
        if p.suffix.lower() not in {".tif", ".tiff"}:
            continue
        print(f"\nTIFF: {p}")
        arr = tifffile.imread(p)
        print("shape:", arr.shape, "dtype:", arr.dtype, "min/max:", np.nanmin(arr), np.nanmax(arr))
        count += 1
        if count >= limit:
            break

if __name__ == "__main__":
    inspect_tree()
    inspect_csvs()
    inspect_tiffs()

Run:

python -m biohub_cellops.kaggle_inspect

This tells you whether the competition gives you raw volumes, annotations, per-frame masks, centroid tables, lineage labels, or some cursed hybrid. Kaggle competitions love tiny schema landmines. The sample submission is the treaty; everything else is propaganda.

5. Define a SCYTHE cell event schema

This is the adapter contract:

from dataclasses import dataclass, asdict
from typing import Optional

@dataclass
class CellDetection:
    embryo_id: str
    t: int
    detection_id: str
    z: float
    y: float
    x: float
    confidence: float
    volume_voxels: Optional[float] = None
    mean_intensity: Optional[float] = None
    model: str = "unknown"
    run_id: str = "baseline"

@dataclass
class CellTrackLink:
    embryo_id: str
    parent_detection_id: str
    child_detection_id: str
    track_id: int
    t0: int
    t1: int
    confidence: float
    event_type: str = "continuation"
    run_id: str = "baseline"

@dataclass
class CellDivision:
    embryo_id: str
    parent_detection_id: str
    daughter_a_id: str
    daughter_b_id: str
    t_parent: int
    t_children: int
    confidence: float
    run_id: str = "baseline"

Then make the SCYTHE adapter:

def add_cell_detection(graph, d):
    node_id = f"cell:{d.embryo_id}:t{d.t:04d}:{d.detection_id}"

    graph.add_node({
        "id": node_id,
        "kind": "CELL_NUCLEUS",
        "position": [float(d.x), float(d.y), float(d.z)],
        "labels": {
            "embryo_id": d.embryo_id,
            "t": int(d.t),
            "model": d.model,
            "run_id": d.run_id,
            "confidence": float(d.confidence),
        },
        "metadata": {
            "detection_id": d.detection_id,
            "volume_voxels": d.volume_voxels,
            "mean_intensity": d.mean_intensity,
            "coordinate_system": "voxel_xyz",
            "source": "biohub_kaggle",
        },
    })

    return node_id


def add_track_link(graph, link):
    edge_id = (
        f"track:{link.embryo_id}:"
        f"{link.parent_detection_id}->{link.child_detection_id}:"
        f"{link.run_id}"
    )

    graph.add_edge({
        "id": edge_id,
        "kind": "CELL_TRACK_CONTINUATION",
        "nodes": [
            f"cell:{link.embryo_id}:t{link.t0:04d}:{link.parent_detection_id}",
            f"cell:{link.embryo_id}:t{link.t1:04d}:{link.child_detection_id}",
        ],
        "weight": float(link.confidence),
        "metadata": {
            "track_id": int(link.track_id),
            "event_type": link.event_type,
            "run_id": link.run_id,
        },
    })

    return edge_id


def add_cell_division(graph, div):
    edge_id = (
        f"division:{div.embryo_id}:"
        f"{div.parent_detection_id}->{div.daughter_a_id}+{div.daughter_b_id}:"
        f"{div.run_id}"
    )

    graph.add_edge({
        "id": edge_id,
        "kind": "CELL_DIVISION",
        "nodes": [
            f"cell:{div.embryo_id}:t{div.t_parent:04d}:{div.parent_detection_id}",
            f"cell:{div.embryo_id}:t{div.t_children:04d}:{div.daughter_a_id}",
            f"cell:{div.embryo_id}:t{div.t_children:04d}:{div.daughter_b_id}",
        ],
        "weight": float(div.confidence),
        "metadata": {
            "division_confidence": float(div.confidence),
            "run_id": div.run_id,
            "biological_event": "mitosis",
        },
    })

    return edge_id

This gives SCYTHE a biological graph without corrupting the original RF/network worldview.

6. Make the first baseline embarrassingly simple

Before model wizardry, make a classical baseline. Something like:

normalize volume
→ detect bright blobs
→ watershed / LoG candidate nuclei
→ nearest-neighbor link
→ simple division heuristic
→ write submission

This will be mediocre. That is fine. Mediocre plus visible is better than elegant and blind.

Example nucleus candidate extractor:

import numpy as np
from skimage import filters, morphology, measure, segmentation, feature
from scipy import ndimage as ndi

def normalize_frame(vol):
    vol = vol.astype(np.float32)
    lo, hi = np.percentile(vol, [1, 99.7])
    vol = np.clip((vol - lo) / max(hi - lo, 1e-6), 0, 1)
    return vol

def detect_candidates_3d(vol, min_size=20):
    vol = normalize_frame(vol)

    smooth = filters.gaussian(vol, sigma=1.0)
    thresh = filters.threshold_otsu(smooth)
    fg = smooth > thresh

    fg = morphology.remove_small_objects(fg, min_size=min_size)
    distance = ndi.distance_transform_edt(fg)

    coords = feature.peak_local_max(
        distance,
        min_distance=3,
        labels=fg,
        exclude_border=False,
    )

    markers = np.zeros_like(vol, dtype=np.int32)
    for i, (z, y, x) in enumerate(coords, start=1):
        markers[z, y, x] = i

    labels = segmentation.watershed(-distance, markers, mask=fg)
    props = measure.regionprops(labels, intensity_image=vol)

    detections = []
    for p in props:
        z, y, x = p.centroid
        detections.append({
            "z": float(z),
            "y": float(y),
            "x": float(x),
            "volume_voxels": float(p.area),
            "mean_intensity": float(p.mean_intensity),
            "confidence": float(min(1.0, p.mean_intensity)),
        })

    return labels, detections

Nearest-neighbor linker:

from scipy.spatial import cKDTree
import numpy as np

def link_frames(prev, curr, max_dist=12.0):
    if not prev or not curr:
        return []

    prev_xyz = np.array([[d["x"], d["y"], d["z"]] for d in prev])
    curr_xyz = np.array([[d["x"], d["y"], d["z"]] for d in curr])

    tree = cKDTree(curr_xyz)
    dists, idxs = tree.query(prev_xyz, k=1)

    links = []
    used_curr = set()

    for i, (dist, j) in enumerate(zip(dists, idxs)):
        if dist <= max_dist and int(j) not in used_curr:
            used_curr.add(int(j))
            conf = float(np.exp(-dist / max_dist))
            links.append((i, int(j), conf, float(dist)))

    return links

This baseline creates the control sample. Everything later must beat it.

7. Then add the serious segmentation ensemble

Use three segmentation candidate lanes:

Lane	Purpose
Classical LoG/watershed	No-training sanity baseline
Cellpose / Cellpose-SAM	Generalist cell/nucleus segmentation
nnU-Net	Strong supervised biomedical segmentation
StarDist	Nucleus-like object detection, especially if cell centers are compact

Cellpose’s repo describes Cellpose-SAM as cell and nucleus segmentation with strong generalization, 3D application, and robustness to shot noise, blur, undersampling, contrast inversions, channel order, and object sizes. nnU-Net’s repo describes it as supervised semantic segmentation for 2D/3D data with dataset-specific adaptation of preprocessing and network configuration. StarDist’s repo describes star-convex object detection for 2D and 3D images.

The ensemble is not “average masks.” The better trick is:

each segmentation model proposes candidates
Ultrack arbitrates temporal plausibility
SCYTHE stores all candidates and rejected alternatives
graph repair chooses final lineage

That preserves uncertainty instead of flushing it down the toilet.

8. Add graph repair passes

This is where SCYTHE gets nasty in the useful way.

Pass A — gap closing

A track disappears for 1–3 frames, then reappears nearby.

def plausible_gap(a, b, max_gap=3, max_speed=8.0):
    dt = b["t"] - a["t"]
    if dt <= 1 or dt > max_gap:
        return False

    dx = b["x"] - a["x"]
    dy = b["y"] - a["y"]
    dz = b["z"] - a["z"]
    dist = (dx*dx + dy*dy + dz*dz) ** 0.5

    return dist / dt <= max_speed

Create a CELL_TRACK_GAP_REPAIR edge, not a normal edge. That way the submission writer may use it, but the UI can scream, “this is inferred, don’t marry it.”

Pass B — impossible jump pruning
def jump_score(a, b):
    dt = max(1, b["t"] - a["t"])
    dist = ((a["x"] - b["x"])**2 + (a["y"] - b["y"])**2 + (a["z"] - b["z"])**2) ** 0.5
    return dist / dt

Mark links above a learned speed threshold as TRACK_JUMP_OUTLIER.

Pass C — division sanity

A parent should not create daughters on opposite sides of the embryo like it got hit by a hadron collider.

def division_plausibility(parent, daughter_a, daughter_b):
    import math

    def dist(u, v):
        return math.sqrt(
            (u["x"] - v["x"])**2 +
            (u["y"] - v["y"])**2 +
            (u["z"] - v["z"])**2
        )

    da = dist(parent, daughter_a)
    db = dist(parent, daughter_b)
    dd = dist(daughter_a, daughter_b)

    symmetry = abs(da - db) / max(da + db, 1e-6)
    compactness = dd / max(da + db, 1e-6)

    score = 1.0
    score *= max(0.0, 1.0 - symmetry)
    score *= max(0.0, 1.5 - compactness) / 1.5

    return score
Pass D — no cycles, no biological nonsense

Before submission:

def validate_lineage_graph(G):
    import networkx as nx

    if not nx.is_directed_acyclic_graph(G):
        raise ValueError("Lineage graph contains a cycle")

    for node in G.nodes:
        parents = list(G.predecessors(node))
        if len(parents) > 1:
            raise ValueError(f"Node {node} has multiple parents")

    return True

That alone can save a submission from humiliating itself.

9. Add a Biohub panel to command-ops-visualization.html

SCYTHE’s command interface already has a runtime surface for visual operations; the repo lists command-ops-visualization.html, Cesium hypergraph pieces, deck/globe integrations, hypergraph viewer modules, and API/runtime files in the main project tree.

Add a new panel:

<section class="panel biohub-cellops-panel">
  <header>
    <h2>Biohub CellOps</h2>
    <span id="biohub-run-status">idle</span>
  </header>

  <div class="controls">
    <button id="biohub-load-run">Load Run</button>
    <button id="biohub-play">Play</button>
    <button id="biohub-pause">Pause</button>
    <button id="biohub-next-error">Next Error</button>
    <button id="biohub-export-submission">Export Submission</button>
  </div>

  <div class="metrics-grid">
    <div>
      <label>Detections</label>
      <strong id="biohub-detection-count">0</strong>
    </div>
    <div>
      <label>Tracks</label>
      <strong id="biohub-track-count">0</strong>
    </div>
    <div>
      <label>Divisions</label>
      <strong id="biohub-division-count">0</strong>
    </div>
    <div>
      <label>Warnings</label>
      <strong id="biohub-warning-count">0</strong>
    </div>
  </div>

  <div id="biohub-lineage-detail"></div>
</section>

Add keyboard violence:

Space      play/pause embryo time
Right      next frame
Left       previous frame
E          next tracking error
D          toggle division candidates
G          toggle gap repairs
U          toggle uncertainty heatmap
S          export submission

Operator UX matters. Kaggle notebook UX is cave art with pandas.

10. API endpoints

Add a small service layer:

/api/biohub/runs
/api/biohub/runs/:run_id/summary
/api/biohub/runs/:run_id/frame/:t
/api/biohub/runs/:run_id/cells/:cell_id
/api/biohub/runs/:run_id/lineage/:track_id
/api/biohub/runs/:run_id/errors
/api/biohub/runs/:run_id/export_submission
/api/biohub/runs/:run_id/compare/:other_run_id

Payload for frame view:

{
  "run_id": "ultrack_baseline_001",
  "embryo_id": "E03",
  "t": 142,
  "cells": [
    {
      "id": "cell:E03:t0142:000183",
      "track_id": 183,
      "x": 121.4,
      "y": 88.2,
      "z": 14.7,
      "confidence": 0.91,
      "flags": []
    }
  ],
  "links": [
    {
      "source": "cell:E03:t0141:000177",
      "target": "cell:E03:t0142:000183",
      "kind": "CELL_TRACK_CONTINUATION",
      "confidence": 0.94
    }
  ],
  "warnings": [
    {
      "kind": "TRACK_JUMP_OUTLIER",
      "cell_id": "cell:E03:t0142:000183",
      "severity": "medium"
    }
  ]
}
11. Run matrix: the leaderboard grinder

Create runs like this:

run_001_classical_strict
run_002_classical_recall
run_003_ultrack_default
run_004_ultrack_flow
run_005_cellpose_ultrack
run_006_nnunet_ultrack
run_007_stardist_ultrack
run_008_ensemble_consensus
run_009_ensemble_recall_repaired

Each run gets stored as a separate graph namespace:

labels.run_id = run_005_cellpose_ultrack
labels.embryo_id = E03
labels.source_model = cellpose_sam
labels.tracker = ultrack

Then SCYTHE can compare them:

same detection, same track      → consensus
same detection, different track → identity conflict
division in one run only        → mitosis ambiguity
missing in strict, present recall → faint cell candidate
present in only one model       → hallucination candidate

Consensus edge score:

def consensus_score(edge_votes, model_weights):
    score = 0.0
    max_score = 0.0

    for model, voted in edge_votes.items():
        w = model_weights.get(model, 1.0)
        max_score += w
        if voted:
            score += w

    return score / max(max_score, 1e-9)

That creates the final submission from a graph, not from a single fragile model output.

12. The “Gravity Map” version

This is where your existing SCYTHE vibe gets genuinely useful.

Make a cell gravity field:

high local density
+ low segmentation confidence
+ high division probability
+ model disagreement
= tracking gravity well

Render those zones as field intensity.

def tracking_gravity(cell):
    density = cell.get("local_density_norm", 0.0)
    uncertainty = 1.0 - cell.get("confidence", 1.0)
    division = cell.get("division_probability", 0.0)
    disagreement = cell.get("ensemble_disagreement", 0.0)

    return (
        0.30 * density +
        0.30 * uncertainty +
        0.25 * disagreement +
        0.15 * division
    )

This gives you a “where the model is probably lying” heatmap. That is pure SCYTHE. Less microscope spreadsheet, more embryo crime scene.

13. Training strategy after baseline

Once baseline + visualization exist, then train.

First supervised model: nnU-Net

Use nnU-Net if the competition provides labels/masks or if you generate pseudo-labels and clean them. Its repo describes support for 2D/3D data and dataset-specific adaptation, which is exactly why it remains a strong biomedical segmentation hammer.

Use it to produce:

foreground probability
boundary probability
nucleus center probability

Feed those to Ultrack.

Second model: flow-assisted tracking

Ultrack docs note that its workflows can use flow-field estimation to improve tracking dynamic cells. That is probably important for embryo development because cells move coherently in tissue regions rather than as independent billiard balls.

SCYTHE can expose:

observed cell displacement
local tissue flow vector
residual motion
outlier tracks

Residual motion is huge. If every nearby cell flows northeast and one track “teleports” southwest, that is probably a link error.

Third model: division classifier

Train a small classifier over candidate parent/daughter triples:

Features:

parent volume before split
daughter volume ratio
daughter distance from parent
daughter-daughter distance
intensity conservation
local density
track smoothness before split
track smoothness after split
time since last division
model agreement

Model:

LightGBM / XGBoost first
small MLP second
graph neural net later if bored and caffeinated

I would not start with a GNN. GNNs are often where Kaggle sanity goes to wear a velvet cape and vanish. Start with feature tables. Then escalate.

14. “Clarktech” version: differentiable lineage repair

Later moonshot: treat the final lineage graph as a constrained optimization problem.

maximize:
  detection confidence
+ temporal smoothness
+ division plausibility
+ ensemble agreement

subject to:
  no cycles
  one parent max
  reasonable speed
  valid time ordering
  valid volume bounds

That resembles what Ultrack already does at the segmentation/tracking stage, but SCYTHE can do a second-pass submission-level optimization across multiple model outputs.

Pseudo-objective:

score(edge) =
    0.35 * detection_confidence
  + 0.25 * motion_plausibility
  + 0.20 * ensemble_consensus
  + 0.10 * local_flow_agreement
  + 0.10 * biological_plausibility
  - 0.50 * conflict_penalty

Then solve with:

networkx min-cost flow
OR-Tools CP-SAT
integer linear programming
Ultrack/Gurobi-style formulation

This is where you stop being “another competitor” and start becoming the person who brought an audit-grade lineage optimizer to a knife fight.

15. Marketable product wedge

The larger Biohub article frames this competition as part of a push to automate a major bottleneck in biological imaging and establish community-driven standards for evaluating cell-tracking methods. That makes SCYTHE CellOps more than a Kaggle side quest.

Product framing:

SCYTHE CellOps:
Lineage QA, provenance, and graph repair for 3D cell-tracking pipelines.

Landing page copy:

Turn raw 3D microscopy into inspectable cell lineages.
Compare models, audit divisions, repair broken tracks, and export reproducible results.

Buyers/users:

developmental biology labs
bioimaging cores
microscopy platform teams
AI-biology groups
biotech R&D teams
grant-funded academic consortia

This is a good niche because the pain is concrete: scientists already have images; the bottleneck is converting those massive 3D time-lapse datasets into trustworthy cell histories. The Kaggle/Biohub competition exists because this is still hard.

16. Immediate build order

Here is the order I would actually execute:

Day 1:
  Inspect Kaggle data
  Generate valid dummy submission
  Create CellDetection / CellTrackLink schema

Day 2:
  Classical detector + nearest-neighbor tracker
  Export baseline submission
  Import baseline into SCYTHE graph

Day 3:
  Ultrack baseline
  Ultrack → SCYTHE adapter
  Compare classical vs Ultrack

Day 4:
  Add CellOps panel
  Render detections, tracks, divisions, warnings

Day 5:
  Add graph repair passes
  Generate submission from repaired graph

Day 6+:
  Add Cellpose / nnU-Net / StarDist lanes
  Ensemble, compare, repair, submit
17. The core command script

Make biohub_cellops/run_baseline.py the one button:

from pathlib import Path
import json
import pandas as pd

from biohub_cellops.kaggle_inspect import inspect_tree
from biohub_cellops.volume_loader import load_embryo
from biohub_cellops.preprocess import make_foreground_and_contours
from biohub_cellops.ultrack_bridge import run_ultrack
from biohub_cellops.lineage_graph_adapter import tracks_to_scythe_graph
from biohub_cellops.graph_repair import repair_graph
from biohub_cellops.submission_writer import write_submission

def main():
    data_root = Path("data/biohub")
    out_root = Path("runs/biohub_ultrack_001")
    out_root.mkdir(parents=True, exist_ok=True)

    inspect_tree(str(data_root))

    run_manifest = {
        "run_id": "biohub_ultrack_001",
        "tracker": "ultrack",
        "segmentation": "classical_foreground_contours",
        "data_root": str(data_root),
    }

    with open(out_root / "manifest.json", "w") as f:
        json.dump(run_manifest, f, indent=2)

    all_submission_parts = []

    for embryo_id in discover_embryos(data_root):
        print(f"Processing embryo {embryo_id}")

        volume = load_embryo(data_root, embryo_id)
        foreground, contours = make_foreground_and_contours(volume)

        tracks, ultrack_graph = run_ultrack(
            foreground=foreground,
            contours=contours,
            run_id=run_manifest["run_id"],
            embryo_id=embryo_id,
        )

        scythe_graph = tracks_to_scythe_graph(
            tracks=tracks,
            ultrack_graph=ultrack_graph,
            embryo_id=embryo_id,
            run_id=run_manifest["run_id"],
        )

        repaired = repair_graph(scythe_graph)

        part = write_submission(
            repaired,
            sample_submission_path=data_root / "sample_submission.csv",
            output_path=out_root / f"{embryo_id}_submission_part.csv",
        )

        all_submission_parts.append(part)

    final = pd.concat(all_submission_parts, ignore_index=True)
    final.to_csv(out_root / "submission.csv", index=False)
    print(f"Wrote {out_root / 'submission.csv'}")

if __name__ == "__main__":
    main()

Some functions are placeholders because the exact Kaggle file layout and submission columns need to be confirmed after download. That is intentional. The right first program is one that tells the truth about the data instead of pretending the schema is obvious.

18. SCYTHE-specific killer feature: provenance replay

Every submission row should be explainable:

{
  "submission_row": 182773,
  "embryo_id": "E03",
  "track_id": 183,
  "t": 142,
  "cell_node": "cell:E03:t0142:000183",
  "derived_from": [
    "ultrack_solution_node_99172",
    "cellpose_candidate_33822",
    "graph_repair_gap_close_12"
  ],
  "confidence": 0.91,
  "warnings": []
}

Click row → see cell → see parent → see daughters → see rejected alternatives → see why it made the CSV.

That is the thing a normal Kaggle notebook will not give you. It is also the thing a lab could actually use after the contest.

19. Final tactical opinion

The first meaningful SCYTHE/Biohub milestone is not a high leaderboard rank.

It is this:

Load one embryo.
Run Ultrack.
Convert tracks into SCYTHE.
Click a cell.
See its lineage.
Export a valid submission.

Once that works, SCYTHE becomes the exoskeleton around every model attempt. Then the leaderboard work becomes graph repair, model disagreement mining, and division cleanup instead of blind notebook thrashing.

That is the move: Ultrack for muscle, SCYTHE for nervous system, graph repair for the knife.

I can monitor the Kaggle discussion/leaderboard changes and surface new baseline notebooks or metric discoveries as they appear.

This architecture is ruthlessly pragmatic. You are completely right: treating biological lineage as a hostile network graph is the fastest way to stop fighting the pixels and start fighting the metric.

While the rest of the leaderboard is drowning in Jupyter notebook OOM errors and trying to hyper-tune a monolithic 3D U-Net, you are building a forensic tracking engine. Ultrack provides the brute force; SCYTHE provides the command and control.

Here is how to weaponize this setup further, focusing strictly on where 3D tracking pipelines typically bleed points and how SCYTHE can directly patch those wounds.

Weaponizing the Metric

In cell tracking competitions (like those historically run using the Cell Tracking Challenge metrics), the evaluation is usually based on a lineage graph matching algorithm, often an Acyclic Oriented Graph Matching (AOGM) measure. It penalizes specific topological errors between your submitted graph and the ground truth.

You can encode this exact penalty math into SCYTHE's edge weights. The generalized tracking error cost usually looks like this:

$Cost = w_{fn} FN + w_{fp} FP + w_{ns} NS + w_{ed} ED + w_{ea} EA + w_{ec} EC$

*(Where $FN$ = False Negative nodes, $FP$ = False Positive nodes, $NS$ = Node Split errors, $ED$ = Edge Deletions, $EA$ = Edge Additions, and $EC$ = Edge Semantic Changes like wrong divisions).*

SCYTHE can use a proxy of this formula to color-code the "danger level" of a track. If a track requires a massive spatial jump or sudden volume change, SCYTHE flags it as a high-probability $ED$ or $EA$ penalty.

Mapping Anomalies to SCYTHE Visuals

Use SCYTHE's `deck.gl` layers to visually scream at the operator when the graph breaks biological invariants.

| Biological Anomaly | Root Cause | SCYTHE Visual Encoding | Operator Action |
| --- | --- | --- | --- |
| Z-Axis Teleportation | Anisotropic resolution causing track loss | Pulsing red `ArcLayer` | Interpolate track or cut edge |
| Massive Volume Shift | Segmentation merged two cells | Yellow `PointCloudLayer` node | Flag for watershed parameter tweak |
| Orphaned Tracklet | Faint fluorescence missed by model | Grey `PathLayer` ending abruptly | Lower threshold in local density well |
| Triple Mitosis | Impossible biological division (3 daughters) | Flashing purple `PathLayer` fork | Prune lowest-confidence edge |

---

Tactical Implementation Traps

As you build this out, there are three Kaggle-specific landmines in 3D time-lapse data that your SCYTHE adapter must handle gracefully.

1. The Z-Anisotropy Slop

Microscopy volumes are almost never isotropic. A voxel might be 0.5 microns in X and Y, but 2.0 microns in Z. If your nearest-neighbor linker or SCYTHE visualizer treats Z equally with X/Y, spheres become cigars, and vertical movement gets heavily penalized by your tracker.

The Fix: Force the `coordinate_system` metadata in SCYTHE to scale the Z-axis by the physical anisotropy factor before computing any distances or rendering the `PointCloudLayer`.

2. The Browser OOM Tax

A full 3D embryo time-lapse can easily sit at 50GB+ in memory. If you try to feed the raw OME-Zarr array directly into the SCYTHE frontend via JSON/SSE for the volume shader, the browser will crash immediately.

The Fix: SCYTHE's frontend must only ingest the graph (centroids, links, metadata). For the raw image overlay, build a lightweight FastAPI endpoint in `biohub_cellops` that serves 2D maximum intensity projections (MIPs) or highly downsampled 3D tiles around the currently selected `CELL_NUCLEUS`.

3. Handling Counterfactuals ("Ghost Tracks")

When running your lane ensemble (Classical vs. Ultrack vs. nnU-Net), do not overwrite the nodes. Namespace them by model run.

You can implement a "Ghost Track" toggle in the SCYTHE UI that allows the operator to overlay Run B (faint orange) on top of Run A (solid cyan).

If cyan and orange overlap perfectly, the tracker is confident.
If an orange track continues where a cyan track dies, the operator can hit a hotkey to merge that specific subgraph into the final submission state.

---

The Final Graph Serialization

Before writing to `submission.csv`, your `submission_writer.py` needs an absolute, unyielding gatekeeper function. The Kaggle evaluator will immediately throw a generic `Submission Scoring Error` if the CSV graph is malformed, giving you zero feedback on why.

Your `validate_lineage_graph(G)` needs to enforce these specific Kaggle-tier invariants:

1. Unique IDs per Timepoint: A track ID can only exist exactly once per `t`.
2. Strict Temporal Monotonicity: A child cell's `t` must be exactly `parent_t + 1`. No skipping frames in the final CSV (interpolate gaps before writing).
3. Acyclic Enforcement: Track 10 cannot become Track 12, which later merges back into Track 10.
4. Valid Mitosis Signatures: If Track A ends at $t=5$, and Track B and Track C claim Track A as a parent, both B and C must begin exactly at $t=6$.

Your plan is airtight. Lock down the baseline CSV parsing first. Once SCYTHE can successfully round-trip a dummy tracker into the UI and back out to a valid Kaggle submission, the infrastructure is won. The rest is just biological graph tuning.

You nailed the exact failure surface: the metric does not care that the pixels were difficult; it cares that the lineage graph is malformed. I’m going to tighten this into the missing implementation layer: metric-proxy scoring, graph validation, ghost-track merge semantics, and UI hotkeys that produce submission-safe edits.

Thought for 14s

Yes. This is the layer that turns SCYTHE from “viewer” into submission weaponry.

One important correction/caution: use AOGM as the internal proxy, but do not assume Kaggle’s metric is literally the classic Cell Tracking Challenge AOGM unless the competition metric page confirms it. The current Kaggle listing says submissions are evaluated with a combined tracking metric and that the CSV contains two row types: nodes/cell detections and edges/links. The Cell Tracking Challenge’s TRA/DET/LNK measures are AOGM-family graph comparisons, where the submitted/computed graph is compared to a reference graph by counting graph-edit operations. So: AOGM-style risk scoring is absolutely the right surrogate, even if Kaggle’s exact scoring formula differs.

The missing SCYTHE module: metric_risk_engine.py

Add this as the internal pre-submission “threat model.”

# biohub_cellops/metric_risk_engine.py

from dataclasses import dataclass
from math import sqrt, exp
from typing import Dict, Any, Optional


@dataclass
class MetricRiskWeights:
    false_negative: float = 10.0
    false_positive: float = 1.0
    node_split: float = 5.0
    edge_delete: float = 5.0
    edge_add: float = 1.0
    semantic_change: float = 10.0
    teleport: float = 8.0
    volume_shift: float = 4.0
    impossible_division: float = 12.0
    orphan_tracklet: float = 6.0


@dataclass
class VoxelScale:
    x_um: float = 1.0
    y_um: float = 1.0
    z_um: float = 1.0


def scaled_distance_um(a: Dict[str, Any], b: Dict[str, Any], scale: VoxelScale) -> float:
    dx = (float(a["x"]) - float(b["x"])) * scale.x_um
    dy = (float(a["y"]) - float(b["y"])) * scale.y_um
    dz = (float(a["z"]) - float(b["z"])) * scale.z_um
    return sqrt(dx * dx + dy * dy + dz * dz)


def motion_plausibility(a, b, scale: VoxelScale, max_speed_um_per_frame: float) -> float:
    dist = scaled_distance_um(a, b, scale)
    return exp(-dist / max(max_speed_um_per_frame, 1e-6))


def volume_shift_ratio(a, b) -> float:
    va = max(float(a.get("volume_voxels", 1.0)), 1.0)
    vb = max(float(b.get("volume_voxels", 1.0)), 1.0)
    return max(va, vb) / min(va, vb)


def edge_metric_risk(
    parent: Dict[str, Any],
    child: Dict[str, Any],
    scale: VoxelScale,
    weights: MetricRiskWeights,
    max_speed_um_per_frame: float = 8.0,
    max_volume_ratio: float = 2.5,
) -> Dict[str, Any]:
    dist = scaled_distance_um(parent, child, scale)
    speed = dist / max(1, int(child["t"]) - int(parent["t"]))
    vol_ratio = volume_shift_ratio(parent, child)

    risk = 0.0
    flags = []

    if int(child["t"]) <= int(parent["t"]):
        risk += weights.semantic_change
        flags.append("NON_MONOTONIC_TIME")

    if int(child["t"]) > int(parent["t"]) + 1:
        risk += weights.edge_delete
        flags.append("TEMPORAL_GAP_REQUIRES_INTERPOLATION")

    if speed > max_speed_um_per_frame:
        risk += weights.teleport * min(speed / max_speed_um_per_frame, 4.0)
        flags.append("Z_OR_XY_TELEPORT")

    if vol_ratio > max_volume_ratio:
        risk += weights.volume_shift * min(vol_ratio / max_volume_ratio, 4.0)
        flags.append("MASSIVE_VOLUME_SHIFT")

    confidence = float(child.get("confidence", 1.0))
    risk += (1.0 - confidence) * weights.false_positive

    return {
        "risk": float(risk),
        "flags": flags,
        "distance_um": float(dist),
        "speed_um_per_frame": float(speed),
        "volume_ratio": float(vol_ratio),
    }

This becomes a universal annotation pass. Every edge gets a metric_risk score before it is allowed anywhere near submission.csv.

The important architecture change

Do not store one edge weight. Store three:

{
  "weight": 0.91,
  "metadata": {
    "model_confidence": 0.91,
    "biological_plausibility": 0.84,
    "metric_risk": 2.7,
    "submission_allowed": true
  }
}

That distinction matters.

A model can be confident and still biologically stupid. Biology is rude like that.

Add a hard gatekeeper: submission_guard.py

This is the bouncer outside the Kaggle club. No malformed graph gets in.

# biohub_cellops/submission_guard.py

from collections import defaultdict, deque


class SubmissionValidationError(Exception):
    pass


def validate_unique_track_per_time(rows):
    seen = set()

    for row in rows:
        if row["row_type"] != "node":
            continue

        key = (row["video_id"], int(row["t"]), int(row["track_id"]))

        if key in seen:
            raise SubmissionValidationError(
                f"Duplicate track at same timepoint: video={key[0]} t={key[1]} track_id={key[2]}"
            )

        seen.add(key)


def validate_edge_references(rows):
    node_ids = set()
    edges = []

    for row in rows:
        if row["row_type"] == "node":
            node_ids.add(row["id"])
        elif row["row_type"] == "edge":
            edges.append(row)

    for edge in edges:
        if edge["source"] not in node_ids:
            raise SubmissionValidationError(f"Edge source missing node: {edge['source']}")
        if edge["target"] not in node_ids:
            raise SubmissionValidationError(f"Edge target missing node: {edge['target']}")


def validate_temporal_monotonicity(rows):
    nodes = {
        row["id"]: row
        for row in rows
        if row["row_type"] == "node"
    }

    for row in rows:
        if row["row_type"] != "edge":
            continue

        src = nodes[row["source"]]
        dst = nodes[row["target"]]

        src_t = int(src["t"])
        dst_t = int(dst["t"])

        if dst_t != src_t + 1:
            raise SubmissionValidationError(
                f"Invalid edge time jump: {row['source']} t={src_t} -> {row['target']} t={dst_t}. "
                "Final CSV edges must be adjacent; interpolate gaps before writing."
            )


def validate_acyclic(rows):
    graph = defaultdict(list)
    indegree = defaultdict(int)
    nodes = set()

    for row in rows:
        if row["row_type"] == "node":
            nodes.add(row["id"])

    for row in rows:
        if row["row_type"] == "edge":
            graph[row["source"]].append(row["target"])
            indegree[row["target"]] += 1
            nodes.add(row["source"])
            nodes.add(row["target"])

    q = deque([n for n in nodes if indegree[n] == 0])
    visited = 0

    while q:
        n = q.popleft()
        visited += 1

        for m in graph[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                q.append(m)

    if visited != len(nodes):
        raise SubmissionValidationError("Lineage graph contains a cycle")


def validate_mitosis_signatures(rows):
    nodes = {
        row["id"]: row
        for row in rows
        if row["row_type"] == "node"
    }

    children_by_parent = defaultdict(list)

    for row in rows:
        if row["row_type"] == "edge":
            children_by_parent[row["source"]].append(row["target"])

    for parent_id, child_ids in children_by_parent.items():
        if len(child_ids) <= 1:
            continue

        if len(child_ids) != 2:
            raise SubmissionValidationError(
                f"Invalid mitosis: {parent_id} has {len(child_ids)} children"
            )

        parent_t = int(nodes[parent_id]["t"])
        child_times = {int(nodes[c]["t"]) for c in child_ids}

        if child_times != {parent_t + 1}:
            raise SubmissionValidationError(
                f"Invalid mitosis timing: {parent_id} children do not both begin at t+1"
            )


def validate_submission_rows(rows):
    validate_unique_track_per_time(rows)
    validate_edge_references(rows)
    validate_temporal_monotonicity(rows)
    validate_acyclic(rows)
    validate_mitosis_signatures(rows)
    return True

This is intentionally mean. A polite validator is how you lose a Saturday to Submission Scoring Error.

Handle Z anisotropy in one place

Do not let every module invent distance math. Create one canonical coordinate transform.

# biohub_cellops/coordinates.py

from dataclasses import dataclass


@dataclass(frozen=True)
class CoordinateSystem:
    name: str
    x_um: float
    y_um: float
    z_um: float

    def voxel_to_physical(self, x, y, z):
        return {
            "x_um": float(x) * self.x_um,
            "y_um": float(y) * self.y_um,
            "z_um": float(z) * self.z_um,
        }

    def physical_distance_um(self, a, b):
        ax = float(a["x"]) * self.x_um
        ay = float(a["y"]) * self.y_um
        az = float(a["z"]) * self.z_um

        bx = float(b["x"]) * self.x_um
        by = float(b["y"]) * self.y_um
        bz = float(b["z"]) * self.z_um

        return ((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2) ** 0.5

Every SCYTHE node should carry this metadata:

{
  "coordinate_system": {
    "name": "biohub_voxel_scaled",
    "x_um": 0.5,
    "y_um": 0.5,
    "z_um": 2.0
  }
}

And the UI should render with:

const renderX = cell.x * voxelScale.x_um;
const renderY = cell.y * voxelScale.y_um;
const renderZ = cell.z * voxelScale.z_um;

Otherwise the viewer lies. A lying viewer is worse than no viewer.

Browser OOM solution: graph first, pixels on demand

This should be the rule:

SCYTHE frontend receives:
  centroids
  links
  confidence
  warnings
  local thumbnails
  MIPs
  low-res tiles

SCYTHE frontend does not receive:
  full 3D embryo volumes
  raw OME-Zarr chunks
  entire TIFF stacks serialized into JSON

FastAPI tile endpoint:

# biohub_cellops/tile_server.py

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pathlib import Path
import io
import numpy as np
import tifffile
from PIL import Image

app = FastAPI()

VOLUME_CACHE = {}


def normalize_uint8(img):
    img = img.astype(np.float32)
    lo, hi = np.percentile(img, [1, 99.5])
    img = np.clip((img - lo) / max(hi - lo, 1e-6), 0, 1)
    return (img * 255).astype(np.uint8)


@app.get("/api/biohub/mip/{embryo_id}/{t}")
def get_mip(embryo_id: str, t: int):
    # Replace this with your actual loader once Kaggle layout is inspected.
    path = Path(f"data/biohub/{embryo_id}/t{t:04d}.tif")

    if not path.exists():
        raise HTTPException(status_code=404, detail="Volume not found")

    vol = tifffile.imread(path)

    if vol.ndim != 3:
        raise HTTPException(status_code=400, detail=f"Expected 3D volume, got shape={vol.shape}")

    mip = vol.max(axis=0)
    img = Image.fromarray(normalize_uint8(mip))

    buf = io.BytesIO()
    img.save(buf, format="PNG")

    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/api/biohub/crop/{embryo_id}/{t}/{z}/{y}/{x}")
def get_crop(embryo_id: str, t: int, z: int, y: int, x: int, radius: int = 48):
    path = Path(f"data/biohub/{embryo_id}/t{t:04d}.tif")

    if not path.exists():
        raise HTTPException(status_code=404, detail="Volume not found")

    vol = tifffile.imread(path)

    z0 = max(0, z - 4)
    z1 = min(vol.shape[0], z + 5)
    y0 = max(0, y - radius)
    y1 = min(vol.shape[1], y + radius)
    x0 = max(0, x - radius)
    x1 = min(vol.shape[2], x + radius)

    crop = vol[z0:z1, y0:y1, x0:x1].max(axis=0)
    img = Image.fromarray(normalize_uint8(crop))

    buf = io.BytesIO()
    img.save(buf, format="PNG")

    return Response(content=buf.getvalue(), media_type="image/png")

Frontend behavior:

async function loadCellCrop(cell) {
  const url = `/api/biohub/crop/${cell.embryo_id}/${cell.t}/${Math.round(cell.z)}/${Math.round(cell.y)}/${Math.round(cell.x)}?radius=64`;
  document.querySelector("#biohub-cell-crop").src = url;
}

That keeps the browser alive. Barely. Browsers are just RAM bonfires with CSS opinions.

Ghost tracks: exact merge semantics

Namespace every run:

run.ultrack_strict
run.ultrack_recall
run.nnunet_ultrack
run.cellpose_ultrack
run.final_submission

A ghost edge is not an edge. It is a candidate patch.

{
  "kind": "GHOST_TRACK_PATCH",
  "source_run": "ultrack_recall",
  "target_run": "final_submission",
  "action": "MERGE_SUBGRAPH",
  "nodes": ["cell:E03:t0042:ghost_991", "cell:E03:t0043:ghost_998"],
  "edges": ["edge:ghost_991->ghost_998"],
  "risk_delta": -7.2,
  "operator_status": "pending"
}

Hotkey semantics:

G = toggle ghost tracks
M = merge selected ghost subgraph into final graph
X = reject selected ghost subgraph
R = recompute metric-risk for local neighborhood
V = validate final graph
S = export submission

The merge function should only copy a ghost subgraph if it improves local risk and passes invariants.

def merge_ghost_subgraph(final_graph, ghost_graph, selected_node_ids, guard_fn):
    candidate = final_graph.copy()

    for node_id in selected_node_ids:
        candidate.add_node(node_id, **ghost_graph.nodes[node_id])

    for u, v, data in ghost_graph.edges(data=True):
        if u in selected_node_ids and v in selected_node_ids:
            candidate.add_edge(u, v, **data)

    guard_fn(candidate)
    return candidate

Do not let the UI directly mutate final submission state. The UI proposes patches. The guard accepts or rejects them. This keeps the cockpit from becoming a beautiful footgun.

SCYTHE visual encoding table, upgraded
Risk type	Likely metric wound	Visual	Hotkey action
Missing cell	FN	Black/red hollow point	A add candidate
Hallucinated cell	FP	Pale yellow blinking point	Delete suppress
Node split / oversegmentation	NS	Two close nodes with red tether	J join
Missing link	ED	Broken cyan path	L link selected
Extra link	EA	Thin orange suspect edge	X cut
Wrong division	EC	Purple fork with warning halo	P prune daughter
Temporal gap	ED/FN	Dashed cyan interpolation	I interpolate
Z teleport	ED/EA	Pulsing red vertical arc	C cut or F flow-repair
Merge event	NS/EC	Fat yellow node	W local watershed retune
Pre-submission repair order

This order matters.

1. Remove impossible edges
2. Split illegal multi-parent nodes
3. Repair short gaps with interpolation
4. Recompute division candidates
5. Remove triple mitosis
6. Enforce adjacent-frame edges
7. Reassign compact track IDs
8. Validate CSV rows
9. Export
10. Re-read exported CSV and validate again

That last step is important. Validate the graph object and validate the actual CSV. Serialization bugs are sneaky little raccoons.

def export_and_revalidate(graph, output_csv, sample_columns):
    rows = graph_to_submission_rows(graph)

    validate_submission_rows(rows)

    df = rows_to_dataframe(rows, sample_columns=sample_columns)
    df.to_csv(output_csv, index=False)

    reread = dataframe_to_rows(pd.read_csv(output_csv))
    validate_submission_rows(reread)

    return output_csv
Metric-risk local neighborhood recomputation

When an operator edits a cell, don’t recompute the world. Recompute the blast radius.

def neighborhood_nodes(G, node_id, radius=2):
    frontier = {node_id}
    seen = {node_id}

    for _ in range(radius):
        nxt = set()
        for n in frontier:
            nxt.update(G.predecessors(n))
            nxt.update(G.successors(n))
        nxt -= seen
        seen |= nxt
        frontier = nxt

    return seen

For each edited node:

recompute incoming edge risk
recompute outgoing edge risk
recompute mitosis signature
recompute local density
recompute ghost-overlap agreement
refresh only those deck.gl objects

This preserves interactivity.

The leaderboard-specific exploit: sparse-subset evaluation

One Kaggle discussion result says submissions are evaluated on a random sparse subset of cells even though inference requires tracking all cells. That changes strategy.

It means you should prioritize:

globally coherent graph
stable IDs
fewer catastrophic topological errors
less hallucinated dense-region chaos
high precision around obvious cells

In other words: do not chase every faint speck if it causes graph shrapnel. A sparse subset metric often rewards lineage stability over maximal recall. That makes SCYTHE’s risk-pruning layer even more valuable.

Scoring proxy per cell track

Add a track-level “do not trust this thing” score:

def track_risk(track_nodes, track_edges):
    if not track_nodes:
        return 999.0

    edge_risk = sum(e["metadata"].get("metric_risk", 0.0) for e in track_edges)
    mean_conf = sum(n["confidence"] for n in track_nodes) / len(track_nodes)

    gaps = sum(1 for e in track_edges if "TEMPORAL_GAP_REQUIRES_INTERPOLATION" in e["metadata"].get("flags", []))
    teleports = sum(1 for e in track_edges if "Z_OR_XY_TELEPORT" in e["metadata"].get("flags", []))

    return (
        edge_risk
        + gaps * 6.0
        + teleports * 8.0
        + (1.0 - mean_conf) * 10.0
    )

Then in the UI:

Track risk < 3       solid cyan
3–8                  cyan/orange
8–15                 orange
15+                  red, suspicious
illegal              flashing red/purple
What to build next, specifically

The next actual coding milestone should be this:

biohub_cellops/
  coordinates.py
  metric_risk_engine.py
  submission_guard.py
  tile_server.py
  ghost_tracks.py
  final_graph_store.py

Then wire one endpoint:

POST /api/biohub/final/patch

Payload:

{
  "run_id": "final_submission",
  "patch_type": "MERGE_GHOST_SUBGRAPH",
  "source_run_id": "ultrack_recall",
  "selected_node_ids": [
    "cell:E03:t0042:ghost_991",
    "cell:E03:t0043:ghost_998"
  ]
}

Response:

{
  "accepted": true,
  "risk_before": 18.4,
  "risk_after": 9.7,
  "validation": "passed",
  "changed_nodes": 2,
  "changed_edges": 1
}

If rejected:

{
  "accepted": false,
  "reason": "Invalid mitosis: parent has 3 children",
  "validation": "failed",
  "changed_nodes": 0,
  "changed_edges": 0
}
The final doctrine

The Kaggle submission should never be “the model output.”

It should be:

model output
→ graph namespace
→ metric-risk annotation
→ biological invariant repair
→ ghost-track arbitration
→ final namespace
→ hard validation
→ CSV
→ CSV revalidation
→ submission

That is the ruthless version.

Ultrack is the bulldozer. SCYTHE is the targeting system. The metric-risk engine is the knife in the boot.