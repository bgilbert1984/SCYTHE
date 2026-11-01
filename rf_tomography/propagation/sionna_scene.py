"""Sionna RT scene builder and batched path tracer for the synthetic block.

Needs the separate CPU venv (see rf_tomography/README.md, "Sionna RT environment"); the
rest of rf_tomography does not import this module. Build the *same* Scene (building boxes)
that the Gaussian-model experiments use, trace paths from many emitters to many receivers,
and return a numpy PathTable (measurements/raytraced.py).

Materials: ITU-R P.2040 permittivity/conductivity formulas evaluated at the scene frequency
(eps = a f^b, sigma = c f^d, f in GHz). Concrete and ground are only specified for >= 1 GHz,
so at 433/915 MHz this extrapolates slightly; sionna's own ITU loader refuses those
frequencies, hence the explicit radio-material plugin. Names must not equal shape ids or
start with plain ITU names (sionna's XML processor checks), hence the "sc_" prefix.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict, Optional, Sequence

import numpy as np

from ..geometry.scene import Scene
from ..measurements.raytraced import DIFFRACTION, DIFFUSE, SPECULAR, PathTable

# name -> (a, b, c, d, thickness_m)
P2040 = {
    "concrete": (5.24, 0.0, 0.0462, 0.7822, 0.30),
    "glass": (6.27, 0.0, 0.0043, 0.8158, 0.024),
    "metal": (1.0, 0.0, 1.0e7, 0.0, 0.002),
    "medium_dry_ground": (15.0, -0.1, 0.035, 1.63, 1.0),
}


def material_params(name: str, frequency_hz: float):
    a, b, c, d, th = P2040[name]
    g = frequency_hz / 1e9
    return a * g ** b, c * g ** d, th


def scene_xml(scene: Scene, frequency_hz: float,
              facade: str | Dict[str, str] = "concrete",
              ground: str = "medium_dry_ground", ground_half_size_m: float = 400.0,
              scattering: float = 0.0) -> str:
    """`scattering` is the fraction of reflected power that is diffuse (0 = purely specular);
    it only has an effect when trace_paths(..., diffuse=True)."""
    facade_of = (lambda b: facade) if isinstance(facade, str) else (lambda b: facade.get(b, "concrete"))
    used = {facade_of(b.name) for b in scene.buildings} | {ground}
    bsdfs = []
    for m in sorted(used):
        eps, sig, th = material_params(m, frequency_hz)
        bsdfs.append(f'  <bsdf type="radio-material" id="mat-sc_{m}">'
                     f'<float name="relative_permittivity" value="{eps}"/>'
                     f'<float name="conductivity" value="{sig}"/>'
                     f'<float name="thickness" value="{th}"/>'
                     f'<float name="scattering_coefficient" value="{scattering}"/></bsdf>')
    shapes = []
    for b in scene.buildings:
        shapes.append(
            f'  <shape type="cube" id="building_{b.name}"><transform name="to_world">'
            f'<scale x="{b.width / 2}" y="{b.depth / 2}" z="{b.height / 2}"/>'
            f'<translate x="{b.cx}" y="{b.cy}" z="{b.height / 2}"/></transform>'
            f'<ref id="mat-sc_{facade_of(b.name)}" name="bsdf"/></shape>')
    shapes.append(
        f'  <shape type="rectangle" id="ground_plane"><transform name="to_world">'
        f'<scale x="{ground_half_size_m}" y="{ground_half_size_m}" z="1"/></transform>'
        f'<ref id="mat-sc_{ground}" name="bsdf"/></shape>')
    return ('<scene version="2.1.0">\n' + "\n".join(bsdfs) + "\n" + "\n".join(shapes) + "\n</scene>\n")


def write_obj(path: Path, verts: np.ndarray, faces: np.ndarray) -> None:
    with open(path, "w") as fh:
        fh.writelines(f"v {x:.5f} {y:.5f} {z:.5f}\n" for x, y, z in verts)
        fh.writelines(f"f {a + 1} {b + 1} {c + 1}\n" for a, b, c in faces)


def polygon_scene_xml(scene: Scene, frequency_hz: float, out_dir: Path, ground: str = "medium_dry_ground",
                      ground_half_size_m: float = 600.0, scattering: float = 0.0,
                      spandrel_material: str = "concrete") -> str:
    """Extruded real footprints with per-floor window bands (geometry/mesh.py), one OBJ mesh per
    material (merged over buildings), plus a ground plane."""
    from ..geometry.mesh import scene_meshes
    meshes = scene_meshes(scene)
    mat_of = {"concrete": spandrel_material, "glass": "glass"}
    used = {mat_of[m] for m in meshes} | {ground}
    bsdfs = []
    for m in sorted(used):
        eps, sig, th = material_params(m, frequency_hz)
        bsdfs.append(f'  <bsdf type="radio-material" id="mat-sc_{m}">'
                     f'<float name="relative_permittivity" value="{eps}"/>'
                     f'<float name="conductivity" value="{sig}"/>'
                     f'<float name="thickness" value="{th}"/>'
                     f'<float name="scattering_coefficient" value="{scattering}"/></bsdf>')
    shapes = []
    for m, (v, f) in meshes.items():
        write_obj(out_dir / f"{m}.obj", v, f)
        shapes.append(f'  <shape type="obj" id="mesh_{m}"><string name="filename" value="{m}.obj"/>'
                      f'<boolean name="face_normals" value="true"/><ref id="mat-sc_{mat_of[m]}" name="bsdf"/></shape>')
    shapes.append(f'  <shape type="rectangle" id="ground_plane"><transform name="to_world">'
                  f'<scale x="{ground_half_size_m}" y="{ground_half_size_m}" z="1"/></transform>'
                  f'<ref id="mat-sc_{ground}" name="bsdf"/></shape>')
    return '<scene version="2.1.0">\n' + "\n".join(bsdfs) + "\n" + "\n".join(shapes) + "\n</scene>\n"


def load_block(scene: Scene, frequency_hz: float, **kw):
    """Load the Scene into Sionna RT with isotropic, vertically polarised antennas.
    Box scenes use cubes; scenes containing PolygonBuildings use extruded meshes with windows."""
    from sionna.rt import PlanarArray, load_scene
    folder = Path(tempfile.mkdtemp(prefix="rf_tomography_"))
    path = folder / "block.xml"
    if any(not getattr(b, "is_box", True) for b in scene.buildings):
        path.write_text(polygon_scene_xml(scene, frequency_hz, folder, **kw))
    else:
        path.write_text(scene_xml(scene, frequency_hz, **kw))
    sc = load_scene(str(path), merge_shapes=False)
    sc.frequency = frequency_hz
    sc.tx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")
    sc.rx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")
    return sc


def _material_lut(sn_scene) -> np.ndarray:
    """object_id -> material code (1 concrete, 2 glass, 3 ground, 0 unknown); invalid id -> 0."""
    code = {"mesh_concrete": 1, "mesh_glass": 2, "ground_plane": 3}
    n = max(o.object_id for o in sn_scene.objects.values()) + 2
    lut = np.zeros(n, np.int8)
    for name, o in sn_scene.objects.items():
        # box scenes name their shapes building_X; those are concrete/glass walls by their material
        lut[int(o.object_id)] = code.get(name, 2 if "glass" in name else 1 if name.startswith("building_") else 0)
    return lut


def _composition(P, ok, ctype, inter, objs, lut):
    """Interaction types/materials of the strongest path and of the strongest direct-like path.
    P, ok, ctype are (T, R, K); inter, objs are (D, R, T, K); returns int8 (T, R, D) arrays."""
    T, R, K = P.shape
    Pm = np.where(ok, P, 0.0)
    dl = ok & ((ctype & (SPECULAR | DIFFUSE | DIFFRACTION)) == 0)
    Pd = np.where(dl, P, 0.0)
    ib, idd = np.argmax(Pm, -1), np.argmax(Pd, -1)
    I = np.asarray(inter).transpose(2, 1, 3, 0)                         # (T, R, K, D)
    O = np.asarray(objs).transpose(2, 1, 3, 0)
    codes = np.where(O == 0xFFFFFFFF, 0, lut[np.minimum(O, len(lut) - 1)]).astype(np.int8)

    def take(a, idx):
        return np.take_along_axis(a, idx[:, :, None, None], axis=2)[:, :, 0, :]

    has_d = Pd.max(-1) > 0
    dty = np.where(has_d[..., None], take(I, idd), 0).astype(np.int8)
    dma = np.where(has_d[..., None], take(codes, idd), 0).astype(np.int8)
    has_b = Pm.max(-1) > 0
    bty = np.where(has_b[..., None], take(I, ib), 0).astype(np.int8)
    bma = np.where(has_b[..., None], take(codes, ib), 0).astype(np.int8)
    return bty, bma, dty, dma, Pd.max(-1).astype(np.float32)


def trace_paths(sn_scene, tx: np.ndarray, rx: np.ndarray, *, tx_batch: int = 8,
                samples_per_src: int = 100_000, max_depth: int = 3, diffraction: bool = True,
                refraction: bool = True, diffuse: bool = False, keep_top: int | None = None,
                seed: int = 1, progress=None, record_composition: bool = True) -> PathTable:
    """All emitter x receiver links. Emitters are traced in batches to bound memory
    (the solver shoots `samples_per_src` rays per emitter)."""
    from sionna.rt import PathSolver, Receiver, Transmitter
    tx, rx = np.asarray(tx, float), np.asarray(rx, float)
    for j, p in enumerate(rx):
        sn_scene.add(Receiver(name=f"rx{j}", position=[float(v) for v in p]))
    solver = PathSolver()
    chunks, totals, comp = [], [], []
    lut = _material_lut(sn_scene) if record_composition else None
    for s in range(0, len(tx), tx_batch):
        batch = tx[s:s + tx_batch]
        for i, p in enumerate(batch):
            sn_scene.add(Transmitter(name=f"tx{i}", position=[float(v) for v in p]))
        paths = solver(sn_scene, max_depth=max_depth, samples_per_src=samples_per_src,
                       max_num_paths_per_src=samples_per_src, los=True, specular_reflection=True,
                       diffuse_reflection=diffuse, refraction=refraction, diffraction=diffraction,
                       seed=seed + s)
        a, tau = paths.cir(normalize_delays=False, out_type="numpy")
        a, tau = np.asarray(a), np.asarray(tau)
        P = (np.abs(a[:, 0, :, 0, :, 0]) ** 2).transpose(1, 0, 2)          # (T, R, K)
        tau = tau.transpose(1, 0, 2)
        theta = np.asarray(paths.theta_r).transpose(1, 0, 2)
        phi = np.asarray(paths.phi_r).transpose(1, 0, 2)
        inter = np.asarray(paths.interactions)                              # (D, R, T, K)
        ctype = np.bitwise_or.reduce(inter, axis=0).transpose(1, 0, 2).astype(np.int8)
        depth = (inter > 0).sum(0).transpose(1, 0, 2).astype(np.int8)
        ok = (P > 0) & (tau >= 0)
        tot = np.where(ok, P, 0.0).sum(-1)                                  # (T, R) all paths
        if record_composition:
            comp.append(_composition(P, ok, ctype, inter, np.asarray(paths.objects), lut))
        if keep_top is not None and P.shape[-1] > keep_top:                 # diffuse tracing: huge K
            sel = np.argpartition(-np.where(ok, P, 0.0), keep_top - 1, axis=-1)[..., :keep_top]
            g = lambda x: np.take_along_axis(x, sel, -1)
            P, tau, theta, phi, ctype, depth, ok = (g(P), g(tau), g(theta), g(phi), g(ctype), g(depth), g(ok))
        totals.append(tot)
        chunks.append((np.where(ok, P, 0.0), np.where(ok, tau, np.nan),
                       np.where(ok, theta, 0.0), np.where(ok, phi, 0.0),
                       np.where(ok, ctype, 0).astype(np.int8), np.where(ok, depth, 0).astype(np.int8)))
        for i in range(len(batch)):
            sn_scene.remove(f"tx{i}")
        if progress:
            progress(min(s + tx_batch, len(tx)), len(tx))
    K = max(c[0].shape[-1] for c in chunks)
    pad = lambda x, v: np.pad(x, ((0, 0), (0, 0), (0, K - x.shape[-1])), constant_values=v)
    cat = lambda i, v: np.concatenate([pad(c[i], v) for c in chunks], 0)
    for j in range(len(rx)):
        sn_scene.remove(f"rx{j}")
    return PathTable(sn_scene.frequency.item() if hasattr(sn_scene.frequency, "item") else float(sn_scene.frequency),
                     tx, rx, cat(0, 0.0), cat(1, np.nan), cat(2, 0.0), cat(3, 0.0),
                     cat(4, 0).astype(np.int8), cat(5, 0).astype(np.int8),
                     dict(max_depth=max_depth, samples_per_src=samples_per_src,
                          diffraction=diffraction, refraction=refraction, diffuse=diffuse,
                          keep_top=keep_top),
                     np.concatenate(totals, 0) if keep_top is not None else None,
                     *([np.concatenate([c[i] for c in comp], 0) for i in range(5)]
                       if record_composition else [None] * 5))
